"""`app.llm.chat.budget`: the one place tool-call budgets are enforced."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.llm.chat.budget import (
    NotCharged,
    Refusal,
    ToolBudget,
    ToolBudgetCapability,
)

Call = Tuple[str, Dict[str, Any]]


@dataclass
class Deps:
    budget: ToolBudget
    ran: List[str] = field(default_factory=list)


def _refuse(name: str, refusal: Refusal, budget: ToolBudget) -> str:
    return f"refused:{name}:{refusal}"


def _agent(
    free_reply: Optional[Any] = None, steps: Sequence[Sequence[Call]] = ()
) -> Agent[Deps, str]:
    def respond(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = sum(1 for m in messages if isinstance(m, ModelResponse))
        if step < len(steps):
            return ModelResponse(
                parts=[
                    ToolCallPart(name, args, tool_call_id=f"{step}-{index}")
                    for index, (name, args) in enumerate(steps[step])
                ]
            )
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[Deps, str] = Agent(
        FunctionModel(respond),
        output_type=str,
        deps_type=Deps,
        capabilities=[
            ToolBudgetCapability(
                budget_for=lambda ctx: ctx.deps.budget,
                refuse=_refuse,
                free_reply=free_reply,
            )
        ],
    )

    @agent.tool
    async def paper(ctx: RunContext[Deps], n: int = 0) -> str:
        ctx.deps.ran.append(f"paper:{n}")
        return f"paper:{n}"

    @agent.tool
    async def repo(ctx: RunContext[Deps], n: int = 0) -> str:
        ctx.deps.ran.append(f"repo:{n}")
        return f"repo:{n}"

    @agent.tool
    async def flaky(ctx: RunContext[Deps], busy: bool = True) -> str:
        if busy:
            raise NotCharged("busy, try later")
        ctx.deps.ran.append("flaky")
        return "flaky"

    return agent


def _run(
    steps: Sequence[Sequence[Call]], budget: ToolBudget, free_reply: Any = None
) -> Tuple[Dict[str, Any], Deps]:
    deps = Deps(budget=budget)
    result = _agent(free_reply, steps).run_sync("go", deps=deps)
    returns = {
        part.tool_call_id: part.content
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    }
    return returns, deps


# -- ToolBudget --------------------------------------------------------------


def test_the_tool_cap_is_checked_before_the_shared_one():
    budget = ToolBudget(max_calls=1, per_tool={"repo": 0})
    # Both caps are spent-or-zero for `repo`; its own cap answers, so the
    # refusal names it and nothing is charged.
    assert budget.refusal("repo") == "tool"
    assert budget.calls == 0


def test_charge_and_refund_track_both_counters():
    budget = ToolBudget(max_calls=2)
    budget.charge("paper")
    budget.charge("repo")
    assert budget.refusal("paper") == "shared"
    budget.refund("repo")
    assert budget.calls == 1
    assert budget.calls_by_tool == {"paper": 1, "repo": 0}
    assert budget.refusal("paper") is None


# -- BudgetedToolset, through a real agent run ------------------------------


def test_calls_past_the_shared_cap_are_refused_without_running():
    steps = [[("paper", {"n": 1})], [("paper", {"n": 2})], [("repo", {"n": 3})]]
    returns, deps = _run(steps, ToolBudget(max_calls=2))
    assert returns == {"0-0": "paper:1", "1-0": "paper:2", "2-0": "refused:repo:shared"}
    assert deps.ran == ["paper:1", "paper:2"]
    assert deps.budget.calls == 2


def test_a_per_tool_cap_does_not_burn_shared_slots():
    steps = [[("repo", {"n": 1}), ("repo", {"n": 2}), ("paper", {"n": 3})]]
    returns, deps = _run(steps, ToolBudget(max_calls=2, per_tool={"repo": 1}))
    assert returns == {
        "0-0": "repo:1",
        "0-1": "refused:repo:tool",
        "0-2": "paper:3",
    }
    assert deps.budget.calls == 2


def test_a_parallel_batch_is_admitted_in_call_order():
    batch = [("paper", {"n": n}) for n in range(6)]
    returns, deps = _run([batch], ToolBudget(max_calls=4))
    assert [returns[f"0-{n}"] for n in range(6)] == [
        "paper:0",
        "paper:1",
        "paper:2",
        "paper:3",
        "refused:paper:shared",
        "refused:paper:shared",
    ]
    assert sorted(deps.ran) == ["paper:0", "paper:1", "paper:2", "paper:3"]


def test_not_charged_refunds_the_call_and_becomes_the_result():
    steps = [[("flaky", {})], [("flaky", {"busy": False})]]
    returns, deps = _run(steps, ToolBudget(max_calls=1))
    assert returns == {"0-0": "busy, try later", "1-0": "flaky"}
    assert deps.budget.calls == 1
    assert deps.budget.calls_by_tool["flaky"] == 1


def test_a_free_reply_skips_the_budget_and_the_tool():
    def free(name: str, args: Dict[str, Any]) -> Optional[str]:
        return "need n" if name == "paper" and args.get("n") == 0 else None

    steps = [[("paper", {"n": 1})], [("paper", {"n": 0}), ("paper", {"n": 2})]]
    returns, deps = _run(steps, ToolBudget(max_calls=1), free_reply=free)
    # The free reply wins even with the budget spent.
    assert returns == {"0-0": "paper:1", "1-0": "need n", "1-1": "refused:paper:shared"}
    assert deps.ran == ["paper:1"]


def test_each_run_counts_against_its_own_budget():
    agent = _agent(steps=[[("paper", {"n": 1})]])
    first = Deps(budget=ToolBudget(max_calls=1))
    second = Deps(budget=ToolBudget(max_calls=1))
    agent.run_sync("go", deps=first)
    agent.run_sync("go", deps=second)
    assert first.ran == ["paper:1"] and second.ran == ["paper:1"]
    assert first.budget.calls == second.budget.calls == 1


def test_the_capability_leaves_the_tool_definitions_alone():
    """The wrapper must not change what the model is offered."""
    seen: Dict[str, List[Any]] = {}

    def capture(key: str):
        def respond(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen[key] = list(info.function_tools)
            return ModelResponse(parts=[TextPart("done")])

        return respond

    def build(capabilities: List[Any], key: str) -> Agent[Deps, str]:
        agent: Agent[Deps, str] = Agent(
            FunctionModel(capture(key)),
            output_type=str,
            deps_type=Deps,
            capabilities=capabilities,
        )

        @agent.tool
        async def paper(ctx: RunContext[Deps], n: int = 0) -> str:
            """Read a paper."""
            return str(n)

        return agent

    deps = Deps(budget=ToolBudget(max_calls=1))
    build([], "plain").run_sync("go", deps=deps)
    build(
        [ToolBudgetCapability(budget_for=lambda ctx: ctx.deps.budget, refuse=_refuse)],
        "budgeted",
    ).run_sync("go", deps=deps)
    assert seen["plain"] == seen["budgeted"]


@pytest.mark.parametrize("refusal", ["tool", "shared"])
def test_paper_refusals_keep_each_tool_result_shape(refusal):
    from app.llm.chat.paper import (
        REPO_BUDGET_EXHAUSTED,
        TOOL_BUDGET_EXHAUSTED,
        _refuse_paper_tool,
        paper_tool_budget,
    )

    budget = paper_tool_budget()
    reply = _refuse_paper_tool("run_python", refusal, budget)
    if refusal == "tool":
        assert reply == REPO_BUDGET_EXHAUSTED
    else:
        assert reply["files"] == [] and reply["output"].startswith("[budget] ")
    assert _refuse_paper_tool("read_pages", "shared", budget) == TOOL_BUDGET_EXHAUSTED
