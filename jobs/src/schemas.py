"""
Pydantic schemas for PDF processing.
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class HighlightType(str, Enum):
    TOPIC = "topic"
    MOTIVATION = "motivation"
    METHOD = "method"
    EVIDENCE = "evidence"
    RESULT = "result"
    IMPACT = "impact"

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

class TitleAuthorsAbstract(BaseModel):
    """Schema for title, authors, and abstract extraction."""
    title: str = Field(description="Title of the paper **in normal case**")
    authors: List[str] = Field(default=[], description="List of authors")
    abstract: str = Field(default="", description="Abstract of the paper")
    publish_date: Optional[str] = Field(
        default="", description="Publishing date of the paper in YYYY-MM-DD format"
    )


class InstitutionsKeywords(BaseModel):
    """Schema for institutions and keywords extraction."""
    institutions: List[str] = Field(
        default=[], description="List of institutions involved in the publication."
    )
    keywords: List[str] = Field(default=[], description="List of keywords")


class Highlights(BaseModel):
    """Schema for highlights extraction."""
    highlights: List[AIHighlight] = Field(
        default=[],
        description="""
Extract 3-5 standout highlights that capture the most compelling and unique aspects of this research paper. Focus on what makes this paper distinctive rather than summarizing standard content.

Requirements for Highlights:
- Each highlight should be a direct, exact quote from the paper
- Each highlight must be accompanied by a brief annotation (1-2 sentences) explaining its significance or relevance to the paper's contributions

Selection Criteria:
Prioritize highlights that are:
- Novel or surprising: Unexpected findings, counterintuitive results, or breakthrough discoveries
- Methodologically innovative: New techniques, creative experimental designs, or unique approaches
- High-impact insights: Findings that could change how the field thinks about a problem
- Quantitatively significant: Impressive performance gains, large effect sizes, or notable statistical findings
- Practically valuable: Real-world applications, actionable implications, or scalable solutions

Content Sources:
- Key results from tables/figures: Extract specific metrics, comparisons, or visual insights
- Critical methodology details: Novel algorithms, experimental setups, or analytical approaches
- Standout conclusions: Bold claims, important limitations, or paradigm-shifting implications
- Notable observations: Interesting patterns, unexpected behaviors, or important caveats

Quality Guidelines:
- Selectivity: Choose only the most essential "must-read" elements—what would experts in the field find most noteworthy?
- Specificity: Prefer concrete findings over general statements
- Diversity: Ensure highlights span different aspects (methods, results, implications) and types, without referencing the abstract
- Context: Each annotation should explain *why* this highlight matters to the broader research landscape

What to Avoid:
- Generic background information or literature review content
- Standard methodology descriptions unless truly innovative
- Routine experimental procedures or common practices
- Abstract-level summaries that don't reveal paper specifics
- Redundant highlights that convey similar information
- Snippets that are pulled directly from the abstract or summary

Think: "If I could only share 3-5 insights from this paper with a colleague, what would make them most excited to read the full work?"
""",
    )


class PaperMetadataExtraction(BaseModel):
    """Extracted metadata from a paper"""
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


class PDFProcessingResult(BaseModel):
    """Result of PDF processing"""
    success: bool
    job_id: str
    raw_content: Optional[str] = None
    page_offset_map: Optional[dict[int, list[int]]] = None
    metadata: Optional[PaperMetadataExtraction] = None
    ai_highlight_anchors: Optional[List[Optional[Dict[str, Any]]]] = None
    s3_object_key: Optional[str] = None
    file_url: Optional[str] = None
    preview_url: Optional[str] = None
    preview_object_key: Optional[str] = None
    error: Optional[str] = None
    duration: Optional[float] = None  # Duration in seconds

    # Mistral OCR fields. parser is "mistral" | "pymupdf" — the chat layer
    # branches on this. ocr is the per-page jsonb (stripped of base64
    # bitmaps); figure_count and page_count are denormalized for cheap
    # reads without scanning ocr.pages.
    parser: Optional[str] = None
    ocr: Optional[Dict[str, Any]] = None
    figure_count: Optional[int] = None
    page_count: Optional[int] = None
