from typing import List, Optional

from app.database.models import HighlightType
from pydantic import BaseModel, Field


class AIHighlight(BaseModel):
    """
    Schema for a highlight in the paper.
    This is used to represent a single highlight with its text and context.
    """

    text: str = Field(
        description="The raw text of the highlight as it appears in the paper. Ensure that this is a direct quote or paraphrase from the paper."
    )
    annotation: str = Field(
        description="The context or annotation for the highlight, explaining its significance or relevance to the paper's content. Less than 350 characters."
    )
    type: HighlightType = Field(
        description="The type of highlight. This can be one of the following: topic, motivation, method, evidence, result, impact. This helps categorize the highlight based on its content and significance."
    )

class ResponseCitation(BaseModel):
    """
    Schema for a citation in the paper.
    This is used to represent a single citation with its text and context.
    """

    text: str = Field(
        description="The raw text of the citation as it appears in the paper. Ensure that this is a direct quote or paraphrase from the paper."
    )
    index: int = Field(
        description="The index of the citation in the paper's reference list. This is used to identify the citation in discussions or findings."
    )
    paper_id: Optional[str] = Field(
        default=None,
        description="The unique identifier of the paper from which this citation is drawn. This helps to contextualize the citation within the broader multi-paper analysis. This is required when there are multiple papers being analyzed.",
    )


class PaperMetadataExtraction(BaseModel):
    title: str = Field(description="Title of the paper in normal case")
    authors: List[str] = Field(default=[], description="List of authors")
    abstract: str = Field(default="", description="Abstract of the paper")
    institutions: List[str] = Field(
        default=[], description="List of institutions involved in the publication."
    )
    keywords: List[str] = Field(default=[], description="List of keywords")
    publish_date: Optional[str] = Field(
        default=None, description="Publishing date of the paper in YYYY-MM-DD format"
    )
    highlights: List[AIHighlight] = Field(
        default=[],
        description="List of key highlights from the paper. These should be significant quotes that are must-reads of the paper's findings and contributions. Each highlight should include the text of the highlight and an annotation explaining its significance or relevance to the paper's content. Particularly drill into interesting, novel findings, methodologies, or implications that are worth noting. Pay special attention to tables, figures, and diagrams that may contain important information.",
    )
