import unittest
from types import SimpleNamespace

from app.llm.paper_pydantic_agent import (
    EvidenceStreamParser,
    PaperAgentDeps,
    _convert_message_history,
    _run_sync_tool,
    build_pydantic_paper_agent,
    pretty_tool_status,
)
from pydantic_ai.messages import ModelRequest, ModelResponse


async def _no_reconcile(citations, paper, llm_client):
    return None


class PaperPydanticAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_evidence_parser_emits_content_and_references(self):
        parser = EvidenceStreamParser(
            paper=SimpleNamespace(parser="mistral"),
            llm_client=SimpleNamespace(),
            citation_reconciler=_no_reconcile,
        )

        chunks = []
        chunks.extend(await parser.feed("Answer text."))
        chunks.extend(await parser.feed("\n---EVIDENCE---\n@cite[1|page=2]\nquoted text"))
        chunks.extend(await parser.feed("\n---END-EVIDENCE---"))
        flushed = parser.flush()
        if flushed:
            chunks.append(flushed)

        self.assertEqual(chunks[0]["type"], "content")
        self.assertEqual(chunks[1]["type"], "references")
        self.assertEqual(
            chunks[1]["content"]["citations"],
            [{"key": 1, "reference": "quoted text", "page": 2}],
        )

    async def test_evidence_parser_handles_delimiters_in_one_chunk(self):
        parser = EvidenceStreamParser(
            paper=SimpleNamespace(parser="mistral"),
            llm_client=SimpleNamespace(),
            citation_reconciler=_no_reconcile,
        )

        chunks = await parser.feed(
            "Answer\n---EVIDENCE---\n@cite[1]\nquote\n---END-EVIDENCE---"
        )

        self.assertEqual([chunk["type"] for chunk in chunks], ["content", "references"])
        self.assertEqual(chunks[1]["content"]["citations"][0]["reference"], "quote")

    async def test_run_sync_tool_injects_deps(self):
        calls = []

        def fake_tool(*, paper_id, current_user, db, query):
            calls.append(
                {
                    "paper_id": paper_id,
                    "current_user": current_user,
                    "db": db,
                    "query": query,
                }
            )
            return {"ok": True}

        deps = PaperAgentDeps(
            paper_id="paper-1",
            paper=SimpleNamespace(),
            current_user=SimpleNamespace(id="user-1"),
            db=object(),
            context_mode="adaptive",
            llm_client=SimpleNamespace(),
            citation_reconciler=_no_reconcile,
        )

        result = await _run_sync_tool(fake_tool, deps, query="entropy")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls[0]["paper_id"], "paper-1")
        self.assertEqual(calls[0]["current_user"].id, "user-1")
        self.assertEqual(calls[0]["query"], "entropy")

    def test_tool_surface_by_context_mode(self):
        adaptive_agent = build_pydantic_paper_agent(
            model=None,
            system_prompt="system",
            paper=SimpleNamespace(parser="mistral"),
            context_mode="adaptive",
        )
        full_agent = build_pydantic_paper_agent(
            model=None,
            system_prompt="system",
            paper=SimpleNamespace(parser="mistral"),
            context_mode="full",
        )
        raw_agent = build_pydantic_paper_agent(
            model=None,
            system_prompt="system",
            paper=SimpleNamespace(parser="pymupdf"),
            context_mode="raw",
        )

        adaptive_tools = set(adaptive_agent._function_toolset.tools)
        full_tools = set(full_agent._function_toolset.tools)
        raw_tools = set(raw_agent._function_toolset.tools)

        self.assertIn("read_section", adaptive_tools)
        self.assertIn("get_figure", adaptive_tools)
        self.assertEqual(full_tools, {"read_main_doc", "write_main_doc"})
        self.assertIn("read_section", raw_tools)
        self.assertNotIn("get_figure", raw_tools)

    def test_status_and_history_conversion(self):
        self.assertEqual(
            pretty_tool_status("search_paper", {"query": "ELBO"}),
            "Searching for 'ELBO'",
        )
        history = _convert_message_history(
            [
                SimpleNamespace(role="user", content="Question"),
                SimpleNamespace(role="assistant", content="Answer"),
            ]
        )
        self.assertIsInstance(history[0], ModelRequest)
        self.assertIsInstance(history[1], ModelResponse)


if __name__ == "__main__":
    unittest.main()
