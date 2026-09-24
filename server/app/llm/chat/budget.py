"""Per-turn tool-call budgets, enforced in one place.

Paper chat and quick question both cap how many tools a single answer may
call. The cap is a SOFT limit: a call past it is not executed, and its tool
result is a "stop calling tools, answer now" message, so the model degrades
into an answer instead of the run dying. pydantic-ai's own `UsageLimits`
RAISE on an over-budget call (checked against the projected batch, before
any call in it runs), which would kill a stream that may already have text
in it — so each runtime keeps its `UsageLimits` well above the budget here
as a hard backstop only.

- `ToolBudget` is the per-run state: a shared cap over every tool plus
  optional per-tool caps (the repo sub-budget inside paper chat).
- `BudgetedToolset` is the enforcement point: a `WrapperToolset` around the
  agent's whole toolset whose `call_tool` answers "free" refusals first,
  then refuses or charges, runs the tool, and refunds a call the tool marks
  as `NotCharged`.
- `ToolBudgetCapability` installs that wrapper per run via
  `get_wrapper_toolset`, so tools stay registered with `@agent.tool` and the
  tool definitions the model sees are untouched.

Check-and-charge happens before the first `await`, so a response's parallel
tool calls (pydantic-ai runs them as concurrent tasks) are admitted in call
order with no race.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool

# Which cap refused a call: the tool's own, or the one shared by all tools.
Refusal = Literal["tool", "shared"]


class NotCharged(Exception):
    """Raised by a tool whose call must not count against the budget.

    For outcomes that are not the model's doing (e.g. every lookup slot is
    busy): the budget is refunded and `reply` becomes the tool result.
    """

    def __init__(self, reply: Any) -> None:
        super().__init__(str(reply))
        self.reply = reply


@dataclass
class ToolBudget:
    """Tool calls one run may make: `max_calls` in total, plus per-tool caps.

    One instance per run — it is the run's counter.
    """

    max_calls: int
    per_tool: Dict[str, int] = field(default_factory=dict)
    calls: int = 0
    calls_by_tool: Counter[str] = field(default_factory=Counter)

    def refusal(self, name: str) -> Optional[Refusal]:
        """Why a call to `name` may not run now, or None when it may.

        The tool's own cap is checked FIRST: a call it refuses must not also
        burn one of the shared slots the other tools need.
        """
        limit = self.per_tool.get(name)
        if limit is not None and self.calls_by_tool[name] >= limit:
            return "tool"
        if self.calls >= self.max_calls:
            return "shared"
        return None

    def charge(self, name: str) -> None:
        self.calls += 1
        self.calls_by_tool[name] += 1

    def refund(self, name: str) -> None:
        self.calls -= 1
        self.calls_by_tool[name] -= 1


# (ctx) -> the run's budget. A callable so a budget can live on the run's
# deps (paper chat) or be bound to the request (quick question).
BudgetFor = Callable[[RunContext[Any]], ToolBudget]
# (tool name, which cap, budget) -> the tool result a refused call gets.
RefusalReply = Callable[[str, Refusal, ToolBudget], Any]
# (tool name, validated args) -> a reply for a call that is answered
# without running or charging anything, or None to go ahead.
FreeReply = Callable[[str, Dict[str, Any]], Any]


@dataclass
class BudgetedToolset(WrapperToolset[AgentDepsT]):
    """Refuses, charges and refunds tool calls against a `ToolBudget`."""

    budget_for: BudgetFor = field(kw_only=True)
    refuse: RefusalReply = field(kw_only=True)
    free_reply: Optional[FreeReply] = field(default=None, kw_only=True)

    async def call_tool(
        self,
        name: str,
        tool_args: Dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        if self.free_reply is not None:
            reply = self.free_reply(name, tool_args)
            if reply is not None:
                return reply
        budget = self.budget_for(ctx)
        refused = budget.refusal(name)
        if refused is not None:
            return self.refuse(name, refused, budget)
        budget.charge(name)
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except NotCharged as exc:
            budget.refund(name)
            return exc.reply


@dataclass
class ToolBudgetCapability(AbstractCapability[AgentDepsT]):
    """Wraps the agent's toolset in a `BudgetedToolset` for every run."""

    budget_for: BudgetFor = field(kw_only=True)
    refuse: RefusalReply = field(kw_only=True)
    free_reply: Optional[FreeReply] = field(default=None, kw_only=True)

    @classmethod
    def get_serialization_name(cls) -> Optional[str]:
        # Built in code with callables; not constructible from an agent spec.
        return None

    def get_wrapper_toolset(
        self, toolset: AbstractToolset[AgentDepsT]
    ) -> AbstractToolset[AgentDepsT]:
        return BudgetedToolset(
            toolset,
            budget_for=self.budget_for,
            refuse=self.refuse,
            free_reply=self.free_reply,
        )
