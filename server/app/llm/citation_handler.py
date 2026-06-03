import re
from typing import List, Optional, Sequence, Union

from app.schemas.message import CitationIndex, OriginalSnippet
from app.schemas.responses import ResponseCitation


class CitationHandler:
    """Handles citation formatting and reference management"""

    @staticmethod
    def format_citations(citations: list[dict]) -> str:
        """Format citations into a structured string"""
        citation_format = "---EVIDENCE---\n"
        formatted_citations = []

        for citation in citations:
            if "paper_id" in citation:
                # Multi-paper format: @cite[key|paper_id]
                cite_marker = f"@cite[{citation['key']}|{citation['paper_id']}]"
            else:
                # Single paper format: @cite[key]
                cite_marker = f"@cite[{citation['key']}]"

            formatted_citations.append(f"{cite_marker}\n{citation['reference']}")

        citation_format += "\n".join(formatted_citations)
        citation_format += "\n---END-EVIDENCE---"
        return citation_format

    @staticmethod
    def convert_references_to_dict(references: Sequence[str]) -> dict:
        """Convert user references to structured citations. Currently only used for user citations."""
        citations = []
        for idx, ref in enumerate(references):
            citation = {
                "key": idx + 1,
                "reference": ref,
            }
            citations.append(citation)
        return {"citations": citations}

    @staticmethod
    def convert_references_to_citations(references: Optional[Sequence[str]]) -> str:
        """Convert user references to structured citations. Currently only used for user citations."""
        if not references:
            return ""
        return CitationHandler.format_citations(
            CitationHandler.convert_references_to_dict(references)["citations"]
        )

    @staticmethod
    def parse_evidence_block(evidence_text: str) -> list[dict]:
        """
        Parse evidence block into structured citations
        Handles multi-line citations between @cite markers

        Accepts these forms — `page=N` and `paper_id=ID` are optional and
        used by the agentic chat to pin a citation to a single PDF page (so
        the reconciliation step can match the quote against that page's
        pymupdf text) and to tag the originating paper (parent or one of its
        supplementaries):

            @cite[1]
            "First piece of evidence"

            @cite[2|page=4]
            "Second piece of evidence"

            @cite[3|page=2|paper_id=abc-123]
            "Evidence from a supplementary paper"
        """
        citations = []
        lines = evidence_text.strip().split("\n")
        current_citation: dict[str, Union[int, str]] | None = None
        current_text_lines: list[str] = []

        for line in lines:
            line = line.strip()
            if line.startswith("@cite["):
                # If we have a previous citation pending, save it
                if current_citation is not None:
                    current_citation["reference"] = " ".join(current_text_lines).strip()
                    citations.append(current_citation)

                # Start new citation. Match `@cite[N]`, `@cite[N|page=P]`, or
                # any combination of `page=P` and `paper_id=ID` separated by
                # `|`. `key` is case-insensitive on the leading digit only.
                match = re.search(
                    r"@cite\[(\d+)((?:\|[^\]]+)*)\]", line, re.IGNORECASE
                )
                if match:
                    number = int(match.group(1))
                    current_citation = {"key": number, "reference": ""}
                    extras = match.group(2) or ""
                    for part in extras.split("|"):
                        part = part.strip()
                        if not part:
                            continue
                        if "=" not in part:
                            continue
                        k, _, v = part.partition("=")
                        k = k.strip().lower()
                        v = v.strip()
                        if k == "page":
                            try:
                                current_citation["page"] = int(v)
                            except ValueError:
                                pass
                        elif k == "paper_id" and v:
                            current_citation["paper_id"] = v
                    current_text_lines = []
            elif current_citation is not None and line:
                # Accumulate lines for the current citation
                current_text_lines.append(line)

        # Don't forget to save the last citation
        if current_citation is not None and current_text_lines:
            current_citation["reference"] = " ".join(current_text_lines).strip()
            citations.append(current_citation)

        return citations

    @staticmethod
    def convert_response_citation_to_paper_citation(
        response_citations: List[ResponseCitation],
    ):
        """Convert ResponseCitation objects to structured citation dicts.

        `paper_id` is included when the response citation carries one
        (multi-paper / supplementary-paper flows). When absent, the citation
        is treated as originating from the parent paper of the chat.
        """
        citations = []
        for resp_citation in response_citations:
            citation: dict = {
                "key": resp_citation.index,
                "reference": resp_citation.text,
            }
            paper_id = getattr(resp_citation, "paper_id", None)
            if paper_id:
                citation["paper_id"] = paper_id
            citations.append(citation)
        return {"citations": citations}

    @staticmethod
    def parse_multi_paper_evidence_block(evidence_text: str) -> list[dict]:
        """
        Parse evidence block into structured citations from multiple papers.
        Handles multi-line citations between @cite markers

        Incoming format of evidence_text:
        @cite[1|paper_id]
        "First piece of evidence"
        @cite[2|paper_id]
        "Second piece of evidence"
        """
        citations = []
        lines = evidence_text.strip().split("\n")
        current_citation: dict[str, Union[int, str]] | None = None
        current_text_lines: list[str] = []

        for line in lines:
            line = line.strip()
            if line.startswith("@cite["):
                # If we have a previous citation pending, save it
                if current_citation is not None:
                    current_citation["reference"] = " ".join(current_text_lines).strip()
                    citations.append(current_citation)

                # Start new citation
                match = re.search(r"@cite\[(\d+)\|([^]]+)\]", line)
                if match:
                    number = int(match.group(1))
                    paper_id = match.group(2)
                    current_citation = {
                        "key": number,
                        "reference": "",
                        "paper_id": paper_id,
                    }
                    current_text_lines = []
            elif current_citation is not None and line:
                # Accumulate lines for the current citation
                current_text_lines.append(line)

        # Don't forget to save the last citation
        if current_citation is not None and current_text_lines:
            current_citation["reference"] = " ".join(current_text_lines).strip()
            citations.append(current_citation)

        return citations

    @staticmethod
    def resolve_compacted_citations(
        citations: list[dict],
        citation_index: CitationIndex,
    ) -> list[dict]:
        """
        Resolve LLM-generated citations from compacted summaries back to
        original snippets using the citation index.

        Strategy: For each citation, check if its reference text contains
        [@n] markers. If so, look up the original snippets and combine them.
        If no markers found, use fuzzy matching or keep the summary text.
        """
        resolved = []

        for citation in citations:
            paper_id = citation.get("paper_id")
            reference = citation.get("reference", "")

            # Find all [@n] markers in the LLM's citation text
            marker_matches = re.findall(r"\[@(\d+)\]", reference)

            if marker_matches and paper_id:
                # Look up all referenced original snippets
                original_texts = []
                for snippet_idx in marker_matches:
                    sidecar_key = f"{paper_id}:{snippet_idx}"
                    original = citation_index.index.get(sidecar_key)
                    if original and original.text not in original_texts:
                        original_texts.append(original.text)

                if original_texts:
                    # Combine all referenced snippets
                    resolved.append(
                        {
                            "key": citation["key"],
                            "reference": " [...] ".join(original_texts),
                            "paper_id": paper_id,
                        }
                    )
                    continue

            # Fallback: try to find best matching original snippet
            best_match = CitationHandler._find_best_match(
                reference, paper_id, citation_index
            )

            if best_match:
                resolved.append(
                    {
                        "key": citation["key"],
                        "reference": best_match.text,
                        "paper_id": paper_id,
                    }
                )
            else:
                # Last resort: keep the summary-derived citation
                resolved.append(citation)

        return resolved

    @staticmethod
    def _find_best_match(
        reference: str,
        paper_id: Optional[str],
        citation_index: "CitationIndex",
    ) -> Optional["OriginalSnippet"]:
        """
        Find the original snippet that best matches a summary-derived citation.
        Uses simple word overlap for speed (no semantic search).
        """
        if not paper_id:
            return None

        candidates = [
            snippet
            for key, snippet in citation_index.index.items()
            if snippet.paper_id == paper_id
        ]

        if not candidates:
            return None

        # Simple heuristic: find snippet with most word overlap
        ref_words = set(reference.lower().split())
        best_score = 0
        best_snippet = None

        for snippet in candidates:
            snippet_words = set(snippet.text.lower().split())
            overlap = len(ref_words & snippet_words)
            if overlap > best_score:
                best_score = overlap
                best_snippet = snippet

        # Only return if we have a reasonable match (at least 3 words overlap)
        return best_snippet if best_score >= 3 else None
