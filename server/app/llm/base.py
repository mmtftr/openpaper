import logging
import os
import time
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional

from app.database.models import Message
from app.database.telemetry import track_event
from app.llm.provider import (
    AnthropicProvider,
    BaseLLMProvider,
    FileContent,
    GeminiProvider,
    LLMProvider,
    LLMResponse,
    MessageParam,
    ModelOption,
    OpenAIProvider,
    StreamChunk,
    ToolCallResult,
)
from app.llm.utils import retry_llm_operation
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ModelType(Enum):
    DEFAULT = "default"
    FAST = "fast"


class BaseLLMClient:
    """Unified LLM client that supports multiple providers"""

    def __init__(self, default_provider: Optional[LLMProvider] = None):
        self._providers: Dict[LLMProvider, BaseLLMProvider] = {}

        if default_provider is None:
            env_value = os.getenv("DEFAULT_LLM_PROVIDER", LLMProvider.OPENAI.value)
            try:
                default_provider = LLMProvider(env_value.lower())
            except ValueError:
                logger.warning(
                    "Invalid DEFAULT_LLM_PROVIDER=%s, using %s",
                    env_value,
                    LLMProvider.OPENAI.value,
                )
                default_provider = LLMProvider.OPENAI

        # Eagerly initialize every provider whose credentials are present.
        # Missing credentials are silent — the provider is just unavailable.
        for provider in LLMProvider:
            if self._is_provider_configured(provider):
                try:
                    self._initialize_provider(provider)
                except Exception as exc:
                    logger.warning(
                        "Skipping unavailable provider %s: %s", provider.value, exc
                    )

        # If the requested default isn't actually configured (common in
        # self-hosted setups that only configure one provider), fall back to
        # whatever IS available. This keeps callers like
        # `BaseLLMClient(default_provider=GEMINI)` working when Gemini is
        # absent but OpenAI is set up.
        if default_provider not in self._providers and self._providers:
            available = next(iter(self._providers.keys()))
            logger.info(
                "Default provider %s not configured; using %s instead",
                default_provider.value,
                available.value,
            )
            default_provider = available
        self.default_provider = default_provider

    def _is_provider_configured(self, provider: LLMProvider) -> bool:
        if provider == LLMProvider.GEMINI:
            return bool(os.getenv("GEMINI_API_KEY"))
        if provider == LLMProvider.OPENAI:
            return bool(os.getenv("OPENAI_API_KEY"))
        if provider == LLMProvider.CODEX_PROXY:
            return bool(os.getenv("CODEX_PROXY_BASE_URL"))
        if provider == LLMProvider.GROQ:
            return bool(os.getenv("GROQ_API_KEY") and os.getenv("GROQ_BASE_URL"))
        if provider == LLMProvider.CEREBRAS:
            return bool(
                os.getenv("CEREBRAS_API_KEY") and os.getenv("CEREBRAS_BASE_URL")
            )
        if provider == LLMProvider.ANTHROPIC:
            return bool(os.getenv("ANTHROPIC_API_KEY"))
        return False

    def get_chat_models(
        self, exclude: Optional[List[LLMProvider]] = None
    ) -> List[Dict[str, str]]:
        """Return all user-selectable chat models, attributed by provider.

        Each entry: {id, name, provider}. Order is by provider iteration
        order, then by each provider's own ordering.
        """
        excluded = set(exclude or [])
        out: List[Dict[str, str]] = []
        for provider, instance in self._providers.items():
            if provider in excluded:
                continue
            for option in instance.get_supported_models():
                out.append(
                    {"id": option.id, "name": option.name, "provider": provider.value}
                )
        return out

    def resolve_model(self, model_id: str) -> tuple[LLMProvider, str]:
        """Find which provider supplies this model id.

        Returns (provider, model_id). Ambiguous when two providers expose the
        same id (e.g. Azure's OPENAI and a same-model-family CODEX_PROXY) -
        returns the first match by provider iteration order. Callers that
        know the provider should use `resolve_model_for_provider` instead.
        Raises ValueError if no provider exposes a model with this id.
        """
        for provider, instance in self._providers.items():
            for option in instance.get_supported_models():
                if option.id == model_id:
                    return provider, option.id
        raise ValueError(f"Model id '{model_id}' not found in any configured provider")

    def resolve_model_for_provider(self, provider: LLMProvider, model_id: str) -> str:
        """Validate model_id is offered by the given provider.

        Use when the caller already knows the provider (e.g. it came from the
        model picker, which returns {id, provider} pairs) - this disambiguates
        the same id existing under multiple providers, unlike `resolve_model`.
        Raises ValueError if the provider isn't configured or doesn't expose
        this model id.
        """
        instance = self._providers.get(provider)
        if instance is None:
            raise ValueError(f"Provider '{provider.value}' is not configured")
        for option in instance.get_supported_models():
            if option.id == model_id:
                return option.id
        raise ValueError(
            f"Model id '{model_id}' not found under provider '{provider.value}'"
        )

    def _initialize_provider(self, provider: LLMProvider) -> None:
        """Initialize a provider if not already done"""
        if provider not in self._providers:
            if provider == LLMProvider.GEMINI:
                self._providers[provider] = GeminiProvider()
            elif provider == LLMProvider.OPENAI:
                self._providers[provider] = OpenAIProvider()
            elif provider == LLMProvider.CODEX_PROXY:
                # Local codex-raycast-proxy: an OpenAI Chat-Completions-compatible
                # endpoint on the host, authenticated via the ChatGPT/Codex
                # subscription (~/.codex/auth.json) rather than an API key.
                # OPENAI_API_KEY-style auth is required by the openai client
                # but ignored by the proxy, hence the placeholder default.
                self._providers[provider] = OpenAIProvider(
                    api_key=os.getenv("CODEX_PROXY_API_KEY", "codex-proxy-local"),
                    base_url=os.getenv("CODEX_PROXY_BASE_URL"),
                    default_model=os.getenv("CODEX_PROXY_MODEL", "gpt-5.5"),
                    fast_model=os.getenv("CODEX_PROXY_FAST_MODEL", "gpt-5.4-mini"),
                    supports_pdf_input=True,
                    models_env_var="CODEX_PROXY_MODELS",
                )
            elif provider == LLMProvider.GROQ:
                # Custom OpenAI-compatible provider using a separate base URL and API key.
                # These can be configured via environment variables or another config layer.
                custom_api_key = os.getenv("GROQ_API_KEY")
                custom_base_url = os.getenv("GROQ_BASE_URL")

                self._providers[provider] = OpenAIProvider(
                    api_key=custom_api_key,
                    base_url=custom_base_url,
                    default_model="openai/gpt-oss-120b",
                    fast_model="moonshotai/kimi-k2-instruct-0905",
                    supports_pdf_input=False,
                    models_env_var=None,
                )
            elif provider == LLMProvider.CEREBRAS:
                self._providers[provider] = OpenAIProvider(
                    api_key=os.getenv("CEREBRAS_API_KEY"),
                    base_url=os.getenv("CEREBRAS_BASE_URL"),
                    default_model="gpt-oss-120b",
                    fast_model="zai-glm-4.7",
                    supports_pdf_input=False,
                    models_env_var=None,
                )
            elif provider == LLMProvider.ANTHROPIC:
                self._providers[provider] = AnthropicProvider()
            else:
                raise ValueError(f"Unsupported LLM provider: {provider}")

    def _get_provider(self, provider: Optional[LLMProvider] = None) -> BaseLLMProvider:
        """Get the appropriate provider.

        If the requested provider isn't configured, fall back to the default
        provider so call sites that hardcode a non-default (e.g. CEREBRAS for
        evidence gathering) keep working in self-hosted setups that only
        configure one provider.
        """
        target_provider = provider or self.default_provider

        if target_provider not in self._providers:
            if target_provider != self.default_provider:
                logger.debug(
                    "Provider %s not configured; falling back to %s",
                    target_provider.value,
                    self.default_provider.value,
                )
                target_provider = self.default_provider

            if target_provider not in self._providers:
                raise ValueError(
                    f"LLM provider '{target_provider.value}' is not configured"
                )

        return self._providers[target_provider]

    def _get_model_for_type(
        self, model_type: ModelType, provider: Optional[LLMProvider] = None
    ) -> str:
        """Get the appropriate model string for the given type and provider"""
        provider_instance = self._get_provider(provider)

        if model_type == ModelType.DEFAULT:
            return provider_instance.get_default_model()
        elif model_type == ModelType.FAST:
            return provider_instance.get_fast_model()
        else:
            raise ValueError(f"Unsupported model type: {model_type}")

    @retry_llm_operation(max_retries=3, delay=1.0)
    def generate_content(
        self,
        contents: Any,
        system_prompt: Optional[str] = None,
        history: Optional[List[Message]] = None,
        function_declarations: Optional[List[Dict]] = None,
        tool_call_results: Optional[List[ToolCallResult]] = None,
        model_type: ModelType = ModelType.DEFAULT,
        provider: Optional[LLMProvider] = None,
        enable_thinking: bool = True,
        output_type: Optional[type[BaseModel]] = None,
        **kwargs,
    ) -> LLMResponse:
        """Generate content using the specified provider. Automatically retries on transient errors.

        Args:
            output_type: Pydantic model class for structured output. When provided,
                the response is constrained to its JSON schema via the provider's
                native structured output support; the returned `LLMResponse.text`
                is the serialized JSON (callers parse with
                `output_type.model_validate_json`).
        """
        start_time = time.time()
        model = self._get_model_for_type(model_type, provider)
        target_provider = provider or self.default_provider

        try:
            response = self._get_provider(provider).generate_content(
                model,
                contents,
                system_prompt=system_prompt,
                function_declarations=function_declarations,
                tool_call_results=tool_call_results,
                history=history,
                enable_thinking=enable_thinking,
                output_type=output_type,
                **kwargs,
            )

            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            # Track the event with model and timing information
            track_event(
                "llm_generate_content",
                {
                    "model": model,
                    "provider": target_provider.value,
                    "model_type": model_type.value,
                    "duration_ms": duration_ms,
                    "has_function_declarations": function_declarations is not None,
                    "enable_thinking": enable_thinking,
                },
            )

            logger.info(
                f"Generated content using {target_provider.value}/{model} in {duration_ms:.2f}ms"
            )

            return response
        except Exception as e:
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            # Track failures too
            track_event(
                "llm_generate_content_error",
                {
                    "model": model,
                    "provider": target_provider.value,
                    "model_type": model_type.value,
                    "duration_ms": duration_ms,
                    "error": str(e),
                },
            )

            logger.error(
                f"Error generating content with {target_provider.value}/{model}: {e}"
            )
            raise

    def send_message_stream(
        self,
        message: MessageParam,
        history: List[Message],
        system_prompt: str,
        file: FileContent | None = None,
        model_type: ModelType = ModelType.DEFAULT,
        provider: Optional[LLMProvider] = None,
        model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        **kwargs,
    ) -> Iterator[StreamChunk]:
        """Send a message and stream the response.

        If `model` is provided, resolves to whichever provider owns that id
        and routes the call there; `model_type` and `provider` are ignored.

        `reasoning_effort` (low/medium/high/xhigh) is forwarded so each
        provider can translate it to its native reasoning knob.
        """
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if model:
            resolved_provider, resolved_model = self.resolve_model(model)
            return self._get_provider(resolved_provider).send_message_stream(
                resolved_model, message, history, system_prompt, file, **kwargs
            )
        chosen_model = self._get_model_for_type(model_type, provider)
        return self._get_provider(provider).send_message_stream(
            chosen_model, message, history, system_prompt, file, **kwargs
        )

    # Convenience properties for backward compatibility
    @property
    def default_model(self) -> str:
        return self._get_model_for_type(ModelType.DEFAULT)

    @property
    def fast_model(self) -> str:
        return self._get_model_for_type(ModelType.FAST)
