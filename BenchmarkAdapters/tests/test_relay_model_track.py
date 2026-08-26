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
    assert "max_tokens" not in rewritten
    assert "max_tokens" in relay._request_telemetry()
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
