from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "BenchmarkAdapters" / "LLMRelay" / "server.py"


def _relay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("UPSTREAM_BASE_URL", "https://upstream.example/v1")
    monkeypatch.setenv("UPSTREAM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_FORCE_MODEL", "gpt-5.5")
    monkeypatch.setenv(
        "LLM_FORCE_PARAMETERS_JSON",
        '{"reasoning_effort":"high","temperature":1.0,"max_output_tokens":512}',
    )
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    monkeypatch.setenv("LLM_TOKEN_LOG_PATH", str(tmp_path / "telemetry.jsonl"))
    name = f"relay_model_track_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _tool() -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "record_value",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _handler(module):
    handler = object.__new__(module.ProxyHandler)
    responses: list[tuple[int, object, str]] = []
    handler._send_json = lambda status, payload, content_type="application/json": responses.append(
        (status, payload, content_type)
    )
    return handler, responses


def test_relay_strips_all_client_generation_parameters(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    rewritten = relay._rewrite_body(
        {
            "model": "agent-selected-model",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [_tool()],
            "tool_choice": "auto",
            "max_tokens": 1,
            "temperature": 0.1,
            "reasoning_effort": "low",
            "logprobs": True,
            "top_logprobs": 5,
            "top_p": 0.2,
            "seed": 7,
            "response_format": {"type": "json_object"},
            "parallel_tool_calls": True,
            "frequency_penalty": 1,
            "presence_penalty": 1,
            "store": True,
        },
        "/chat/completions",
    )
    assert rewritten["model"] == "gpt-5.5"
    assert rewritten["reasoning_effort"] == "high"
    assert rewritten["temperature"] == 1.0
    # The output-token limit is the Agent's own budget, not a sampling knob the
    # track owns: the campaign never injects one, and an Agent that sets its own
    # keeps it. Everything else below is still stripped so the frozen model track
    # remains the only source of generation parameters.
    assert rewritten["max_tokens"] == 1
    assert "max_tokens" not in relay._request_telemetry()
    assert rewritten["tools"] == [_tool()]
    assert rewritten["tool_choice"] == "auto"
    for name in (
        "logprobs",
        "top_logprobs",
        "top_p",
        "seed",
        "response_format",
        "parallel_tool_calls",
        "frequency_penalty",
        "presence_penalty",
        "store",
    ):
        assert name not in rewritten
        assert name in relay._request_telemetry()


def test_relay_forces_temperature_one_without_track_temperature(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("UPSTREAM_BASE_URL", "https://upstream.example/v1")
    monkeypatch.setenv("UPSTREAM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_FORCE_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_FORCE_PARAMETERS_JSON", '{"reasoning_effort":"high"}')
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    monkeypatch.setenv("LLM_TOKEN_LOG_PATH", str(tmp_path / "telemetry.jsonl"))
    name = f"relay_temperature_track_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    rewritten = module._rewrite_body(
        {
            "model": "agent-selected-model",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.7,
        },
        "/chat/completions",
    )
    assert rewritten["temperature"] == 1.0


def test_auto_tool_choice_preserves_upstream_text(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    response = {
        "choices": [{"message": {"role": "assistant", "content": "I will inspect first."}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    post = pytest.MonkeyPatch()
    try:
        post.setattr(relay, "_post_canonical_chat", lambda *_args: (response, 0.1, 0))
        logged: list[tuple[tuple, dict]] = []
        post.setattr(relay, "_append_token_log", lambda *args, **kwargs: logged.append((args, kwargs)))
        handler, sent = _handler(relay)
        handler._handle_chat(
            {"messages": [{"role": "user", "content": "go"}], "tools": [_tool()], "tool_choice": "auto"}
        )
    finally:
        post.undo()
    assert sent == [(200, response, "application/json")]
    assert len(logged) == 1
    assert logged[0][1]["response_kind"] == "text"


def test_required_tool_choice_rejects_text_without_synthesis(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    response = {
        "choices": [{"message": {"role": "assistant", "content": "I cannot call it."}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    post = pytest.MonkeyPatch()
    try:
        post.setattr(relay, "_post_canonical_chat", lambda *_args: (response, 0.1, 0))
        post.setattr(relay, "_append_token_log", lambda *_args, **_kwargs: None)
        handler, _sent = _handler(relay)
        with pytest.raises(relay._ToolCallProtocolError):
            handler._handle_chat(
                {"messages": [{"role": "user", "content": "go"}], "tools": [_tool()], "tool_choice": "required"}
            )
    finally:
        post.undo()


def test_telemetry_records_track_digest_strips_and_response_kind(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    relay._append_token_log(
        "gpt-5.5",
        "chat.completions",
        {"prompt_tokens": 3, "completion_tokens": 2},
        0.1,
        0,
        stripped_params=("logprobs", "top_p"),
        response_kind="tool_call",
    )
    record = json.loads((tmp_path / "telemetry.jsonl").read_text(encoding="utf-8"))
    assert record["model_track_digest"] == relay.MODEL_TRACK_DIGEST
    assert record["effective_request_param_digest"] == relay.MODEL_TRACK_DIGEST
    assert record["stripped_params"] == ["logprobs", "top_p"]
    assert record["response_kind"] == "tool_call"


def _truncated_responses_payload() -> dict[str, object]:
    """An upstream Responses object cut off by the upstream output-token limit."""

    return {
        "id": "resp_truncated",
        "object": "response",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "model": "gpt-5.5",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "status": "incomplete",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "partial answer that was cut"}],
            }
        ],
        "usage": {"input_tokens": 11, "output_tokens": 512, "total_tokens": 523},
    }


def test_truncated_responses_upstream_becomes_length_finish_reason(
    monkeypatch, tmp_path: Path
) -> None:
    relay = _relay(monkeypatch, tmp_path)
    chat = relay._responses_response_to_chat(_truncated_responses_payload())
    assert chat["choices"][0]["finish_reason"] == "length"
    assert chat["choices"][0]["message"]["content"] == "partial answer that was cut"


def test_truncated_message_item_without_envelope_status_is_still_length(
    monkeypatch, tmp_path: Path
) -> None:
    relay = _relay(monkeypatch, tmp_path)
    payload = _truncated_responses_payload()
    payload.pop("status")
    payload.pop("incomplete_details")
    chat = relay._responses_response_to_chat(payload)
    assert chat["choices"][0]["finish_reason"] == "length"


def test_complete_responses_upstream_keeps_stop_and_tool_calls(
    monkeypatch, tmp_path: Path
) -> None:
    relay = _relay(monkeypatch, tmp_path)
    complete = {
        "id": "resp_ok",
        "status": "completed",
        "incomplete_details": None,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [{"type": "output_text", "text": "done"}],
            }
        ],
        "usage": {},
    }
    assert relay._responses_response_to_chat(complete)["choices"][0]["finish_reason"] == "stop"
    with_tool = {
        "id": "resp_tool",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "record_value",
                "arguments": "{}",
            }
        ],
        "usage": {},
    }
    assert (
        relay._responses_response_to_chat(with_tool)["choices"][0]["finish_reason"]
        == "tool_calls"
    )


def test_length_finish_reason_becomes_messages_max_tokens(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    truncated_chat = {
        "id": "chatcmpl-truncated",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "partial"},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 512},
    }
    message = relay._chat_response_to_messages(truncated_chat)
    assert message["stop_reason"] == "max_tokens"


def test_messages_stop_reason_covers_the_remaining_terminal_states(
    monkeypatch, tmp_path: Path
) -> None:
    relay = _relay(monkeypatch, tmp_path)

    def _stop_reason(finish_reason: str, *, tool_calls: bool = False) -> str:
        message: dict[str, object] = {"role": "assistant", "content": "text"}
        if tool_calls:
            message["tool_calls"] = [
                {"id": "call_1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ]
        return relay._chat_response_to_messages(
            {"choices": [{"message": message, "finish_reason": finish_reason}], "usage": {}}
        )["stop_reason"]

    assert _stop_reason("stop") == "end_turn"
    assert _stop_reason("tool_calls", tool_calls=True) == "tool_use"
    assert _stop_reason("content_filter") == "refusal"
    # A missing finish_reason must still fall back on the message shape.
    assert (
        relay._chat_response_to_messages(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "t", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ],
                "usage": {},
            }
        )["stop_reason"]
        == "tool_use"
    )


def test_length_finish_reason_becomes_incomplete_responses_object(
    monkeypatch, tmp_path: Path
) -> None:
    relay = _relay(monkeypatch, tmp_path)
    truncated_chat = {
        "id": "chatcmpl-truncated",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "partial"},
                "finish_reason": "length",
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 512},
    }
    response = relay._chat_response_to_responses(truncated_chat)
    assert response["status"] == "incomplete"
    assert response["incomplete_details"] == {"reason": "max_output_tokens"}
    assert [item["status"] for item in response["output"]] == ["incomplete"]
    complete = relay._chat_response_to_responses(
        {
            "id": "chatcmpl-ok",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "all"}, "finish_reason": "stop"}
            ],
            "usage": {},
        }
    )
    assert complete["status"] == "completed"
    assert complete["incomplete_details"] is None
    assert [item["status"] for item in complete["output"]] == ["completed"]


def test_truncation_survives_a_full_responses_to_messages_round_trip(
    monkeypatch, tmp_path: Path
) -> None:
    """The cross-protocol path an Agent actually sees: responses -> chat -> messages."""

    relay = _relay(monkeypatch, tmp_path)
    chat = relay._responses_response_to_chat(_truncated_responses_payload())
    message = relay._chat_response_to_messages(chat)
    assert message["stop_reason"] == "max_tokens"
    responses_again = relay._chat_response_to_responses(chat)
    assert responses_again["status"] == "incomplete"
    assert responses_again["incomplete_details"] == {"reason": "max_output_tokens"}


def test_streaming_synthesis_carries_the_truncation_signal(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    chat = relay._responses_response_to_chat(_truncated_responses_payload())
    chat_stream = relay._synthesize_sse(chat).decode("utf-8")
    assert '"finish_reason": "length"' in chat_stream
    messages_stream = relay._synthesize_messages_sse(
        relay._chat_response_to_messages(chat)
    ).decode("utf-8")
    assert '"stop_reason": "max_tokens"' in messages_stream
    responses_stream = relay._synthesize_responses_sse(
        relay._chat_response_to_responses(chat)
    ).decode("utf-8")
    assert "response.incomplete" in responses_stream
    assert "response.completed" not in responses_stream


def test_token_log_captures_cache_from_both_upstream_usage_shapes(monkeypatch, tmp_path: Path) -> None:
    """Real gpt-5.6-terra usage objects: chat reports cache under
    prompt_tokens_details, /responses under input_tokens_details.  Both must be
    recorded as numbers, and total_tokens must stay input+output because the
    cached count is a subset of the prompt tokens, not an addition."""
    relay = _relay(monkeypatch, tmp_path)
    observed = [
        # /v1/chat/completions, second identical long request (cache hit).
        {
            "completion_tokens": 5,
            "prompt_tokens": 11207,
            "total_tokens": 11212,
            "prompt_tokens_details": {"cached_tokens": 11008},
        },
        # /v1/responses.
        {
            "input_tokens": 26778,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 7},
            "total_tokens": 26783,
        },
        # Anthropic-style usage.
        {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 20,
        },
    ]
    for usage in observed:
        relay._append_token_log("gpt-5.6-terra", "chat.completions", usage, 0.1, 0)
    records = [
        json.loads(line)
        for line in (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    chat, responses, anthropic = records
    assert chat["cache_tokens"] == 11008
    assert chat["cache_read_tokens"] == 11008
    assert chat["total_tokens"] == 11212

    assert responses["cache_tokens"] == 0
    assert responses["reasoning_tokens"] == 7
    assert responses["total_tokens"] == 26783

    assert anthropic["cache_tokens"] == 100
    assert anthropic["total_tokens"] == 110


def test_token_log_reports_unknown_cache_when_upstream_is_silent(monkeypatch, tmp_path: Path) -> None:
    relay = _relay(monkeypatch, tmp_path)
    relay._append_token_log(
        "gpt-5.6-terra",
        "chat.completions",
        {"prompt_tokens": 11207, "completion_tokens": 5, "total_tokens": 11212},
        0.1,
        0,
    )
    record = json.loads((tmp_path / "telemetry.jsonl").read_text(encoding="utf-8"))
    assert record["cache_tokens"] is None
    assert record["total_tokens"] == 11212
