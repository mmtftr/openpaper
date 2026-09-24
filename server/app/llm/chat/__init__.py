"""Unified agentic chat runtime.

One pydantic-ai based runtime for every chat surface (single paper,
whole-library "everything", project). The wire protocol is the Vercel AI SDK
v6 UIMessage stream, produced by `pydantic_ai.ui.vercel_ai.VercelAIAdapter`;
ground truth per turn is the pydantic-ai ModelMessage dump persisted on the
assistant message row.
"""
