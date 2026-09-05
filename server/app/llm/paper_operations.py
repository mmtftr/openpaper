import logging
from typing import Literal, Optional

from app.database.crud.paper_crud import paper_crud
from app.llm.base import BaseLLMClient
from app.llm.json_parser import JSONParser
from app.llm.prompts import GENERATE_NARRATIVE_SUMMARY
from app.llm.provider import FileContent, TextContent
from app.llm.utils import retry_llm_operation
from app.schemas.responses import AudioOverviewForLLM
from app.schemas.user import CurrentUser
from fastapi import Depends
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.database.database import get_db
from app.helpers.s3 import s3_service


class PaperOperations(BaseLLMClient):
    """Operations related to paper analysis and chat functionality"""

    @retry_llm_operation(max_retries=3, delay=1.0)
    def create_narrative_summary(
        self,
        paper_id: str,
        user: CurrentUser,
        length: Optional[Literal["short", "medium", "long"]] = "medium",
        additional_instructions: Optional[str] = None,
        db: Session = Depends(get_db),
    ) -> AudioOverviewForLLM:
        """
        Create a narrative summary of the paper using the specified model
        """
        paper = paper_crud.get(db, id=paper_id, user=user)

        if not paper:
            raise ValueError(f"Paper with ID {paper_id} not found.")

        audio_overview_schema = AudioOverviewForLLM.model_json_schema()

        # Word count targets for audio durations at ~150 words/min
        # short: ~3 min, medium: ~7 min, long: ~14 min
        word_count_map = {
            "short": 450,
            "medium": 1000,
            "long": 2000,
        }

        formatted_prompt = GENERATE_NARRATIVE_SUMMARY.format(
            additional_instructions=additional_instructions,
            length=word_count_map.get(str(length), word_count_map["medium"]),
            schema=audio_overview_schema,
        )

        pdf_bytes = s3_service.get_object_bytes(str(paper.s3_object_key))

        message_content = [
            FileContent(
                data=pdf_bytes,
                mime_type="application/pdf",
                filename=f"{paper.title or 'paper'}.pdf",
                text_fallback=str(paper.raw_content) if paper.raw_content else None,
            ),
            TextContent(text=formatted_prompt),
        ]

        # Generate narrative summary using the LLM
        response = self.generate_content(
            contents=message_content,
        )

        try:
            if response and response.text:
                # Parse the response text as JSON
                response_json = JSONParser.validate_and_extract_json(response.text)
                # Validate against the AudioOverview schema
                audio_overview = AudioOverviewForLLM.model_validate(response_json)
                return audio_overview
            else:
                raise ValueError("Empty response from LLM.")
        except ValueError as e:
            logger.error(f"Error parsing LLM response: {e}", exc_info=True)
            raise ValueError(f"Invalid response from LLM: {str(e)}")

