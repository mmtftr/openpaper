"""
Simplified LLM client for metadata extraction.

Uses the standard OpenAI client by default and switches to AzureOpenAI when
AZURE_OPENAI=true (with AZURE_OPENAI_ENDPOINT). Strict JSON-schema mode is
patched for Azure to satisfy its constraints.
"""
import asyncio
import json
import logging
import os
import random
import re
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar, Union

import httpx
import openai
from pydantic import BaseModel, ConfigDict, Field, create_model

from src.prompts import (
    EXTRACT_COLS_INSTRUCTION,
    EXTRACT_METADATA_PROMPT_TEMPLATE,
)
from src.schemas import (
    DataTableCellValue,
    DataTableRow,
    Highlights,
    InstitutionsKeywords,
    PaperMetadataExtraction,
    SummaryAndCitations,
    TitleAuthorsAbstract,
)
from src.utils import retry_llm_operation, time_it

logger = logging.getLogger(__name__)

DEFAULT_CHAT_MODEL = "gpt-4.1"
FAST_CHAT_MODEL = "gpt-5.4-mini"

T = TypeVar("T", bound=BaseModel)

OpenAIClient = Union[openai.AsyncOpenAI, openai.AsyncAzureOpenAI]


def _is_azure_openai_enabled() -> bool:
    return os.getenv("AZURE_OPENAI", "").strip().lower() in ("1", "true", "yes")


_UNSUPPORTED_STRICT_KEYWORDS = {
    "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "multipleOf",
    "patternProperties", "unevaluatedProperties", "propertyNames",
    "minProperties", "maxProperties",
    "unevaluatedItems", "contains", "minContains", "maxContains",
    "minItems", "maxItems", "uniqueItems",
}


def _patch_schema_for_azure_strict(schema: Any) -> Any:
    """Patch a JSON schema for Azure OpenAI strict mode.

    - $ref nodes must have no sibling keywords
    - additionalProperties: false on every object
    - all properties listed in required
    - unsupported keywords stripped
    """
    if not isinstance(schema, dict):
        return schema

    if "$ref" in schema:
        ref = schema["$ref"]
        schema.clear()
        schema["$ref"] = ref
        return schema

    for key in _UNSUPPORTED_STRICT_KEYWORDS:
        schema.pop(key, None)

    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
        props = schema.get("properties", {})
        schema["required"] = list(props.keys())
        for prop in props.values():
            _patch_schema_for_azure_strict(prop)

    if "items" in schema:
        _patch_schema_for_azure_strict(schema["items"])

    for sub in (
        schema.get("anyOf", [])
        + schema.get("allOf", [])
        + schema.get("oneOf", [])
    ):
        _patch_schema_for_azure_strict(sub)

    for defn in schema.get("$defs", {}).values():
        _patch_schema_for_azure_strict(defn)

    return schema


class JSONParser:

    @staticmethod
    def validate_and_extract_json(json_data: str) -> dict:
        """Extract and validate JSON data from various formats"""
        if not json_data or not isinstance(json_data, str):
            raise ValueError("Invalid input: empty or non-string data")

        json_data = json_data.strip()

        try:
            return json.loads(json_data)
        except json.JSONDecodeError:
            pass

        if "```" in json_data:
            code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", json_data)

            for block in code_blocks:
                block = block.strip()
                block = re.sub(r"}\s+\w+\s+}", "}}", block)
                block = re.sub(r"}\s+\w+\s+,", "},", block)

                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    continue

        raise ValueError(
            "Could not extract valid JSON from the provided string. "
            "Please ensure the response contains proper JSON format."
        )


class AsyncLLMClient:
    """OpenAI-backed async LLM client used by jobs.

    Reads its configuration from the environment so the same instance works
    for OpenAI and AzureOpenAI deployments.
    """

    DEFAULT_TIMEOUT_SECONDS = 90.0

    def __init__(self, default_model: Optional[str] = None):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")

        self.is_azure = _is_azure_openai_enabled()
        if self.is_azure:
            self.azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            if not self.azure_endpoint:
                raise ValueError(
                    "AZURE_OPENAI=true requires AZURE_OPENAI_ENDPOINT to be set"
                )
            self.azure_api_version = os.getenv(
                "AZURE_OPENAI_API_VERSION", "2025-04-01-preview"
            )
            self.base_url = None
        else:
            self.azure_endpoint = None
            self.azure_api_version = None
            self.base_url = os.getenv("OPENAI_BASE_URL") or None

        self.default_model = default_model or DEFAULT_CHAT_MODEL

    def _create_client(
        self, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> OpenAIClient:
        """Create a fresh client instance for thread-safe concurrent calls."""
        if self.is_azure:
            # Azure's v1 OpenAI-compatible endpoint (ending /openai/v1) works
            # with the plain async OpenAI client; the deployment-routed Azure
            # URL needs AsyncAzureOpenAI.
            assert self.azure_endpoint is not None
            if self.azure_endpoint.rstrip("/").endswith("/openai/v1"):
                return openai.AsyncOpenAI(
                    api_key=self.api_key,
                    base_url=self.azure_endpoint,
                    timeout=timeout,
                )
            return openai.AsyncAzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.azure_endpoint,
                api_version=self.azure_api_version,
                timeout=timeout,
            )
        return openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )

    async def generate_content(
        self,
        prompt: str,
        client: OpenAIClient,
        model: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> str:
        """Generate content with automatic retry and exponential backoff."""
        if not model:
            model = self.default_model

        kwargs: Dict[str, Any] = {}
        if schema:
            schema_dict = schema.model_json_schema()
            if self.is_azure:
                schema_dict = _patch_schema_for_azure_strict(schema_dict)
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "strict": True,
                    "schema": schema_dict,
                },
            }

        last_exception: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    **kwargs,
                )

                if response.choices and response.choices[0].message.content:
                    return response.choices[0].message.content

                raise ValueError("No content generated from LLM response")
            except (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.APIStatusError,
                openai.RateLimitError,
                httpx.TimeoutException,
            ) as e:
                last_exception = e
                if attempt < max_retries:
                    backoff_time = (
                        base_delay * (2 ** attempt) * (0.5 + 0.5 * random.random())
                    )
                    logger.warning(
                        "LLM API error (attempt %s/%s): %s. Retrying in %.2fs",
                        attempt + 1,
                        max_retries + 1,
                        e,
                        backoff_time,
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    logger.error(
                        "All %s attempts failed for generate_content: %s",
                        max_retries + 1,
                        e,
                    )

        raise last_exception or ValueError(
            "Failed to generate content after all retries"
        )


class PaperOperations(AsyncLLMClient):
    """OpenAI-backed paper-metadata extraction operations."""

    async def _extract_single_metadata_field(
        self,
        model: Type[T],
        paper_content: str,
        schema: Type[BaseModel],
        status_callback: Callable[[str], None],
        client: OpenAIClient,
        llm_model: Optional[str] = None,
    ) -> T:
        prompt = EXTRACT_METADATA_PROMPT_TEMPLATE.format()
        if paper_content:
            prompt = f"Paper Content:\n\n{paper_content}\n\n{prompt}"

        response = await self.generate_content(
            prompt, schema=schema, client=client, model=llm_model
        )
        response_json = JSONParser.validate_and_extract_json(response)
        instance = model.model_validate(response_json)

        if model == SummaryAndCitations:
            n_citations = len(getattr(instance, "summary_citations", []))
            status_callback(f"Compiled with {n_citations} citations")
        elif model == InstitutionsKeywords:
            keywords = getattr(instance, "keywords", [])
            institutions = getattr(instance, "institutions", [])
            first_keyword = keywords[0] if keywords else ""
            if first_keyword:
                status_callback(f"Building on {first_keyword} context")
            elif institutions:
                first_institution = institutions[0] if institutions else ""
                status_callback(
                    f"Adding context from institution: {first_institution}"
                )
            else:
                status_callback("Processing without keyword data")
        elif model == Highlights:
            highlights = getattr(instance, "highlights", [])
            if highlights:
                status_callback(f"Formulated {len(highlights)} annotations")
            else:
                status_callback("No annotations extracted")
        elif model == TitleAuthorsAbstract:
            title = getattr(instance, "title", "")
            status_callback(f"Reading {title if title else 'untitled paper'}")
        else:
            status_callback(f"Successfully extracted {model.__name__}")

        return instance

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_title_authors_abstract(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: OpenAIClient,
        llm_model: Optional[str] = None,
    ) -> TitleAuthorsAbstract:
        return await self._extract_single_metadata_field(
            model=TitleAuthorsAbstract,
            schema=TitleAuthorsAbstract,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_institutions_keywords(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: OpenAIClient,
        llm_model: Optional[str] = None,
    ) -> InstitutionsKeywords:
        return await self._extract_single_metadata_field(
            model=InstitutionsKeywords,
            schema=InstitutionsKeywords,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_summary_and_citations(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: OpenAIClient,
        llm_model: Optional[str] = None,
    ) -> SummaryAndCitations:
        return await self._extract_single_metadata_field(
            model=SummaryAndCitations,
            schema=SummaryAndCitations,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_highlights(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: OpenAIClient,
        llm_model: Optional[str] = None,
    ) -> Highlights:
        return await self._extract_single_metadata_field(
            model=Highlights,
            schema=Highlights,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    async def extract_paper_metadata(
        self,
        paper_content: str,
        job_id: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> PaperMetadataExtraction:
        """Extract metadata from paper content using LLM."""
        async with time_it("Extracting paper metadata from LLM", job_id=job_id):
            extraction_model = os.getenv("EXTRACTION_MODEL")
            if extraction_model:
                logger.info(f"Using extraction model override: {extraction_model}")

            client = self._create_client()
            try:
                async with time_it(
                    "Running all metadata extraction tasks concurrently",
                    job_id=job_id,
                ):
                    tasks = [
                        asyncio.create_task(
                            time_it(
                                "Extracting title, authors, and abstract",
                                job_id=job_id,
                            )(self.extract_title_authors_abstract)(
                                paper_content=paper_content,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                        asyncio.create_task(
                            time_it(
                                "Extracting institutions and keywords",
                                job_id=job_id,
                            )(self.extract_institutions_keywords)(
                                paper_content=paper_content,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                        asyncio.create_task(
                            time_it(
                                "Extracting summary and citations",
                                job_id=job_id,
                            )(self.extract_summary_and_citations)(
                                paper_content=paper_content,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                        asyncio.create_task(
                            time_it("Extracting highlights", job_id=job_id)(
                                self.extract_highlights
                            )(
                                paper_content=paper_content,
                                status_callback=status_callback,
                                client=client,
                                llm_model=extraction_model,
                            )
                        ),
                    ]

                    shielded_tasks = [asyncio.shield(task) for task in tasks]
                    results = await asyncio.gather(
                        *shielded_tasks, return_exceptions=True
                    )

                (
                    title_authors_abstract,
                    institutions_keywords,
                    summary_and_citations,
                    highlights,
                ) = results

                return PaperMetadataExtraction(
                    title=getattr(title_authors_abstract, "title", ""),
                    authors=getattr(title_authors_abstract, "authors", []),
                    abstract=getattr(title_authors_abstract, "abstract", ""),
                    institutions=getattr(institutions_keywords, "institutions", []),
                    keywords=getattr(institutions_keywords, "keywords", []),
                    summary=getattr(summary_and_citations, "summary", ""),
                    summary_citations=getattr(
                        summary_and_citations, "summary_citations", []
                    ),
                    highlights=getattr(highlights, "highlights", []),
                    publish_date=getattr(
                        title_authors_abstract, "publish_date", None
                    ),
                )

            except Exception as e:
                logger.error(f"Error extracting metadata: {e}", exc_info=True)
                if status_callback:
                    status_callback(f"Error during metadata extraction: {e}")
                raise ValueError(f"Failed to extract metadata: {str(e)}")

    async def extract_data_table(
        self,
        columns: List[str],
        paper_content: str,
        paper_id: str,
    ) -> DataTableRow:
        """Extract structured data table values for the given columns."""
        client = self._create_client()
        try:
            cols_str = "\n".join(f"- {col}" for col in columns)
            prompt = EXTRACT_COLS_INSTRUCTION.format(
                cols_str=cols_str, n_cols=len(columns)
            )
            prompt = f"Paper Content:\n\n{paper_content}\n\n{prompt}"

            field_definitions: Dict[str, Any] = {
                col: (
                    DataTableCellValue,
                    Field(description=f"Value and citations for column '{col}'"),
                )
                for col in columns
            }

            ValuesModel = create_model(
                "ValuesModel",
                __config__=ConfigDict(),
                **field_definitions,
            )

            response = await self.generate_content(
                prompt,
                model=self.default_model,
                schema=ValuesModel,
                client=client,
            )

            response_json = JSONParser.validate_and_extract_json(response)
            values_instance = ValuesModel.model_validate(response_json)

            values_dict: Dict[str, DataTableCellValue] = {
                col: getattr(values_instance, col) for col in columns
            }

            return DataTableRow(paper_id=paper_id, values=values_dict)
        except Exception as e:
            logger.error(f"Error extracting data table: {str(e)}", exc_info=True)
            raise ValueError(
                f"Failed to extract DT for paper {paper_id}: {str(e)}"
            )


def _resolve_model(env_var: str, fallback: str) -> str:
    return os.getenv(env_var) or fallback


llm_client = PaperOperations(default_model=_resolve_model("OPENAI_MODEL", DEFAULT_CHAT_MODEL))
fast_llm_client = PaperOperations(
    default_model=_resolve_model("OPENAI_FAST_MODEL", FAST_CHAT_MODEL)
)
