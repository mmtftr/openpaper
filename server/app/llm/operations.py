from app.llm.citation_handler import CitationHandler
from app.llm.conversation_operations import ConversationOperations, DataTableOperations
from app.llm.json_parser import JSONParser
from app.llm.paper_operations import PaperOperations


class Operations(
    PaperOperations,
    ConversationOperations,
    DataTableOperations,
):
    """Unified non-chat LLM operations (summaries, titles, data tables).

    Chat itself lives in app/llm/chat/ on the pydantic-ai runtime and does
    not go through this class — except citation reconciliation, which uses
    this instance's `generate_content` FAST-model plumbing.
    """

    pass


__all__ = [
    "Operations",
    "PaperOperations",
    "ConversationOperations",
    "CitationHandler",
    "JSONParser",
    "DataTableOperations",
]

operations = Operations()
