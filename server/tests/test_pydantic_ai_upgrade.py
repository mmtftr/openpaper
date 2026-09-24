"""Exercise the dependency boundaries that mocks of the chat runtime miss."""

import asyncio
import json

import httpx2
import pytest
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

from app.llm._pai_compat import (
    AzureStrictJsonSchemaTransformer,
    close_model_transport,
    make_openai_chat_model,
)
from app.llm.chat.stream import OpenPaperAdapter
from app.llm.model_registry import LLMProvider, ModelRegistry
from app.llm.retrying_model import RetryingModel, is_retryable, retry_after_seconds


@pytest.mark.parametrize(
    "api,reasoning", [("chat", None), ("chat", 17), ("responses", 17)]
)
def test_usage_extraction_including_reasoning(api, reasoning):
    if api == "chat":
        usage = {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}
        if reasoning is not None:
            usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    else:
        usage = {
            "input_tokens": 123,
            "output_tokens": 45,
            "total_tokens": 168,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": reasoning},
        }
    extracted = RequestUsage.extract(
        {"model": "gpt-5.4-mini", "usage": usage},
        provider="openai",
        provider_url="https://example.openai.azure.com/openai/v1/",
        provider_fallback="openai",
        api_flavor=api,
    )
    assert (extracted.input_tokens, extracted.output_tokens) == (123, 45)
    assert getattr(extracted, "output_reasoning_tokens", 0) == (reasoning or 0)
    # This is the actual persisted response shape, including the new usage field.
    messages = [ModelResponse(parts=[TextPart("ok")], usage=extracted)]
    loaded = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(messages)
    )
    assert getattr(loaded[0].usage, "output_reasoning_tokens", 0) == (reasoning or 0)


@pytest.mark.parametrize(
    "endpoint,expected_url,azure_client",
    [
        (
            "https://resource.openai.azure.com/openai/v1/",
            "https://resource.openai.azure.com/openai/v1/",
            False,
        ),
        (
            "https://deployment.models.ai.azure.com",
            "https://deployment.models.ai.azure.com/v1/",
            False,
        ),
        (
            "https://resource.openai.azure.com",
            "https://resource.openai.azure.com/openai/",
            True,
        ),
    ],
)
def test_azure_transport_and_profile(monkeypatch, endpoint, expected_url, azure_client):
    from openai import AsyncAzureOpenAI

    monkeypatch.setenv("AZURE_OPENAI", "true")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)
    model = make_openai_chat_model("gpt-5.4-mini", api_key="test-key")
    try:
        assert isinstance(model.client, AsyncAzureOpenAI) is azure_client
        assert str(model.client.base_url) == expected_url
        assert model.client.max_retries == 0
        assert isinstance(model.client.timeout, httpx2.Timeout)
        assert model.client.timeout.as_dict() == {
            "connect": 5.0,
            "read": 180.0,
            "write": 60.0,
            "pool": 30.0,
        }
        assert RetryingModel(model).base_url == model.base_url
        assert (
            model.profile["json_schema_transformer"] is AzureStrictJsonSchemaTransformer
        )

        class Output(BaseModel):
            label: str = Field(min_length=1, max_length=30)
            values: list[int] = Field(min_length=1)

        schema = model.profile["json_schema_transformer"](
            Output.model_json_schema(), strict=True
        ).walk()
        assert "minLength" not in schema["properties"]["label"]
        assert "maxLength" not in schema["properties"]["label"]
        assert "minItems" not in schema["properties"]["values"]
        assert schema["additionalProperties"] is False
    finally:
        asyncio.run(model.client.close())


@pytest.mark.parametrize(
    "provider",
    [
        LLMProvider.OPENAI,
        LLMProvider.CODEX_PROXY,
        LLMProvider.ANTHROPIC,
        LLMProvider.GEMINI,
    ],
)
def test_registry_provider_construction_and_cleanup(monkeypatch, provider):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("CODEX_PROXY_BASE_URL", "http://localhost:8788/v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    registry = ModelRegistry.from_env()
    model = registry.build_model(registry.resolve(provider=provider))
    # __getattr__ still forwards the application's per-request closer in v2.
    asyncio.run(close_model_transport(RetryingModel(model)))


def test_httpx2_transport_errors_keep_retry_policy():
    assert is_retryable(httpx2.ReadTimeout("dead stream"))
    assert is_retryable(httpx2.ConnectError("connection reset"))
    error = httpx2.HTTPStatusError(
        "overloaded",
        request=httpx2.Request("POST", "https://example.test"),
        response=httpx2.Response(429, headers={"retry-after": "4"}),
    )
    assert is_retryable(error)
    assert retry_after_seconds(error) == 4


def _adapter(agent):
    return OpenPaperAdapter(
        agent=agent,
        run_input=OpenPaperAdapter.build_run_input(
            json.dumps(
                {
                    "id": "conversation",
                    "trigger": "submit-message",
                    "messages": [
                        {
                            "id": "user",
                            "role": "user",
                            "parts": [{"type": "text", "text": "Read the paper"}],
                        }
                    ],
                }
            ).encode()
        ),
        sdk_version=6,
        server_message_id="assistant",
    )


@pytest.mark.asyncio
async def test_real_agent_tool_stream_and_persisted_replay():
    async def respond(messages, info):
        if not any(p.part_kind == "tool-return" for m in messages for p in m.parts):
            yield {0: DeltaThinkingPart(content="Look up the page")}
            yield {
                1: DeltaToolCall(
                    name="read_pages", json_args='{"start":1}', tool_call_id="call-1"
                )
            }
        else:
            yield "The answer.\n---EVI"
            yield "DENCE---hidden citation---END-EVIDENCE---"

    agent = Agent(FunctionModel(stream_function=respond))

    @agent.tool_plain
    def read_pages(start: int) -> dict:
        return {"pages_returned": [start], "content": "Paper content"}

    adapter = _adapter(agent)
    results = []

    async def complete(result):
        results.append(result)

    stream = adapter.transform_stream(adapter.run_stream_native(), on_complete=complete)
    wire = [chunk async for chunk in adapter.encode_stream(stream)]
    events = [
        json.loads(chunk[6:]) for chunk in wire if chunk.strip() != "data: [DONE]"
    ]
    assert not any(e["type"] == "error" for e in events)
    tool_result = next(e for e in events if e["type"] == "tool-output-available")
    assert tool_result["toolCallId"] == "call-1"
    assert tool_result["output"]["pages_returned"] == [1]
    assert any(e["type"] == "reasoning-end" for e in events)
    assert "hidden citation" not in "".join(
        e.get("delta", "") for e in events if e["type"] == "text-delta"
    )
    assert len(results) == 1
    saved = ModelMessagesTypeAdapter.dump_json(results[0].new_messages())
    history = ModelMessagesTypeAdapter.validate_json(saved)
    assert any(p.part_kind == "tool-return" for m in history for p in m.parts)

    async def replay(messages, info):
        assert any(p.part_kind == "tool-return" for m in messages for p in m.parts)
        return ModelResponse(parts=[TextPart("Replayed")])

    result = await Agent(FunctionModel(replay)).run("Continue", message_history=history)
    assert result.output == "Replayed"


@pytest.mark.asyncio
async def test_upstream_closes_text_before_stream_error():
    async def fail(messages, info):
        yield "Partial answer"
        raise RuntimeError("lost provider connection")

    adapter = _adapter(Agent(FunctionModel(stream_function=fail)))
    chunks = [chunk async for chunk in adapter.encode_stream(adapter.run_stream())]
    events = [json.loads(c[6:]) for c in chunks if c.strip() != "data: [DONE]"]
    kinds = [e["type"] for e in events]
    assert kinds.count("text-start") == kinds.count("text-end") == 1
    assert kinds.index("text-end") < kinds.index("error")
    assert adapter.last_event_stream.error_text == "lost provider connection"
    assert adapter.last_event_stream.accumulated_text == "Partial answer"
