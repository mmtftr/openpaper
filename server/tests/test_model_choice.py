"""`app.llm.chat.model_choice`: the request -> model resolution shared by
paper chat and quick question."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.chat import model_choice
from app.llm.chat.model_choice import ModelChoiceError, choose_model
from app.llm.model_registry import LLMProvider

SPEC = SimpleNamespace(id="picked")
SLOT_SPEC = SimpleNamespace(id="slot-default")


class FakeRegistry:
    def __init__(self) -> None:
        self.resolved: list = []

    def resolve(self, provider=None, model_id=None, role=None):
        if model_id == "missing":
            raise ValueError("Unknown model 'missing'.")
        self.resolved.append((provider, model_id))
        return SPEC


@pytest.fixture()
def registry(monkeypatch) -> FakeRegistry:
    fake = FakeRegistry()
    monkeypatch.setattr(model_choice, "get_registry", lambda: fake)
    monkeypatch.setattr(
        model_choice,
        "resolve_slot",
        lambda slot, reg: SimpleNamespace(spec=SLOT_SPEC, reasoning_effort="high"),
    )
    return fake


def test_no_pick_gets_the_slot_and_its_effort(registry):
    choice = choose_model(
        slot="chat.default", provider=None, model=None, reasoning_effort=None
    )
    assert choice.spec is SLOT_SPEC
    assert choice.reasoning_effort == "high"


def test_a_requested_effort_beats_the_slots(registry):
    choice = choose_model(
        slot="chat.default", provider=None, model=None, reasoning_effort="low"
    )
    assert choice.reasoning_effort == "low"


def test_an_explicit_pick_is_resolved_by_the_registry(registry):
    choice = choose_model(
        slot="chat.default",
        provider="OpenAI",
        model="gpt-x",
        reasoning_effort=None,
    )
    assert choice.spec is SPEC
    assert registry.resolved == [(LLMProvider.OPENAI, "gpt-x")]
    assert choice.reasoning_effort is None


@pytest.mark.parametrize(
    "provider,model,message",
    [("nope", None, "Unknown provider 'nope'."), (None, "missing", "missing")],
)
def test_a_bad_pick_is_a_model_choice_error(registry, provider, model, message):
    with pytest.raises(ModelChoiceError) as excinfo:
        choose_model(
            slot="chat.default", provider=provider, model=model, reasoning_effort=None
        )
    assert message in str(excinfo.value)


def test_a_broken_registry_is_not_reported_as_a_bad_pick(monkeypatch):
    """A registry that fails to build is a server error (500), not a 4xx."""

    def broken():
        raise ValueError("OPENAI_MODELS is malformed")

    monkeypatch.setattr(model_choice, "get_registry", broken)
    with pytest.raises(ValueError) as excinfo:
        choose_model(
            slot="chat.default", provider=None, model=None, reasoning_effort=None
        )
    assert not isinstance(excinfo.value, ModelChoiceError)
