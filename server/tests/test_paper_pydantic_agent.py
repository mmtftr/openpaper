import unittest
from types import SimpleNamespace

from app.llm.paper_pydantic_agent import (
    PaperAgentDeps,
    _convert_message_history,
    _reasoning_part_delta,
    _rehydrate_figure_bytes,
    _run_sync_tool,
    _strip_figure_bytes,
    build_pydantic_paper_agent,
    pretty_tool_status,
    split_evidence_block,
)
from pydantic_ai.messages import ModelRequest, ModelResponse


async def _no_reconcile(citations, paper, llm_client):
    return None


class PaperPydanticAgentTests(unittest.IsolatedAsyncioTestCase):
    def test_split_evidence_block_extracts_inner(self):
        text = (
            "Yes — the answer is X.\n\n"
            "---EVIDENCE---\n"
            "@cite[1|page=2]\n"
            "quoted text\n"
            "---END-EVIDENCE---"
        )
        clean, inner = split_evidence_block(text)
        self.assertEqual(clean, "Yes — the answer is X.")
        self.assertIn("@cite[1|page=2]", inner)
        self.assertIn("quoted text", inner)

    def test_split_evidence_block_no_block(self):
        clean, inner = split_evidence_block("Just a plain answer.")
        self.assertEqual(clean, "Just a plain answer.")
        self.assertEqual(inner, "")

    def test_split_evidence_block_unterminated(self):
        # Truncated mid-evidence: no `---END-EVIDENCE---`. Everything past
        # the start delimiter is treated as evidence so the citations still
        # parse instead of leaking into displayed content.
        text = "Answer.\n---EVIDENCE---\n@cite[1]\npartial"
        clean, inner = split_evidence_block(text)
        self.assertEqual(clean, "Answer.")
        self.assertIn("@cite[1]", inner)
        self.assertIn("partial", inner)

    def test_reasoning_part_delta_separates_adjacent_parts(self):
        self.assertEqual(
            _reasoning_part_delta("Next title\nNext summary", "Previous summary"),
            "\n\nNext title\nNext summary",
        )
        self.assertEqual(
            _reasoning_part_delta("Next title", "Previous summary\n"),
            "Next title",
        )
        self.assertEqual(_reasoning_part_delta("First title", ""), "First title")

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
        self.assertEqual(full_tools, {"list_docs", "read_doc", "write_doc"})
        self.assertIn("read_section", raw_tools)
        self.assertNotIn("get_figure", raw_tools)

    def test_strip_and_rehydrate_figure_bytes(self):
        from unittest.mock import patch

        dump = {
            "messages": [
                {
                    "parts": [
                        {
                            "kind": "binary",
                            "media_type": "image/png",
                            "identifier": "openpaper-figure:papers/p1/fig-2.png",
                            "data": "ZmFrZS1ieXRlcw==",
                        },
                        {
                            "kind": "text",
                            "content": "Some text — should be untouched",
                        },
                    ]
                }
            ]
        }
        stripped = _strip_figure_bytes(dump)
        self.assertEqual(stripped["messages"][0]["parts"][0]["data"], "")
        self.assertEqual(
            stripped["messages"][0]["parts"][1]["content"],
            "Some text — should be untouched",
        )

        with patch(
            "app.llm.paper_pydantic_agent.s3_service.get_object_bytes",
            return_value=b"rehydrated-bytes",
        ):
            rehydrated = _rehydrate_figure_bytes(stripped)
        self.assertNotEqual(rehydrated["messages"][0]["parts"][0]["data"], "")
        # Base64 of b"rehydrated-bytes" so validate_json round-trips back.
        import base64
        self.assertEqual(
            rehydrated["messages"][0]["parts"][0]["data"],
            base64.b64encode(b"rehydrated-bytes").decode("ascii"),
        )

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
