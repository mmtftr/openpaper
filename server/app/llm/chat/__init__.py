"""Agentic chat: paper chat and quick question on pydantic-ai.

The wire protocol is the Vercel AI SDK v6 UIMessage stream, produced by
`pydantic_ai.ui.vercel_ai.VercelAIAdapter`; ground truth per turn is the
pydantic-ai ModelMessage dump persisted on the assistant message row.

Paper chat, one turn (`runtime.run_paper_chat`):

- `plan`         validate the request, pick the model, load paper +
                 conversation, rebuild prompt and model history
- `store`        persist the question, then the answer (completed, failed
                 or interrupted) exactly once
- `pump`         run the agent and hand its encoded stream parts to the
                 response; retry-status injection; teardown
- `runtime`      the turn top to bottom, and `on_complete`

Shared by paper chat and quick question:

- `model_choice` a request's model: explicit pick or a slot default
- `pump`         (above)
- `budget`       per-turn tool-call budgets, one `WrapperToolset`
- `stream`       the AI-SDK event stream: evidence holdback, tool-output caps
- `paper`        paper context / system prompt; the paper agent and tools

Also: `history/` (model replay + UI serialization of stored rows),
`evidence` (citation blocks), `title`, `tool_preview`, `quick_question`
(+ `quick_question_tools`).
"""
