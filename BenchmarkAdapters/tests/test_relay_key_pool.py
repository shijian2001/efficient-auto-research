from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import threading
import uuid

import pytest

from BenchmarkAdapters.LLMRelay.client import relay_agent_environment

SERVER = Path(__file__).resolve().parents[1] / "LLMRelay/server.py"


def load_relay(monkeypatch, tmp_path, *, extra=None, slot="1"):
    monkeypatch.setenv("UPSTREAM_API_KEY", "first-secret-key")
    monkeypatch.setenv("UPSTREAM_BASE_URL", "https://upstream.example/v1")
    monkeypatch.setenv("LLM_FORCE_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("LLM_FORCE_PARAMETERS_JSON", '{"reasoning_effort":"high","temperature":1.0}')
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("LLM_TOKEN_LOG_PATH", str(tmp_path / "tokens.jsonl"))
    if slot is None:
        monkeypatch.delenv("UPSTREAM_KEY_SLOT", raising=False)
    else:
        monkeypatch.setenv("UPSTREAM_KEY_SLOT", slot)
    if extra is None:
        monkeypatch.delenv("UPSTREAM_EXTRA_KEYS_FILE", raising=False)
    else:
        monkeypatch.setenv("UPSTREAM_EXTRA_KEYS_FILE", str(extra))
    spec = importlib.util.spec_from_file_location("relay_keys_" + uuid.uuid4().hex, SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def key_file(tmp_path, keys):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(keys))
    path.chmod(0o600)
    return path


class Response:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


def test_instances_keep_separate_keys_across_concurrent_requests(monkeypatch, tmp_path):
    keys = key_file(tmp_path, ["second-secret-key"])
    relays = [load_relay(monkeypatch, tmp_path, extra=keys, slot=str(slot)) for slot in (1, 2)]
    # If the HTTP operation is serialized under the key-selection lock, this
    # barrier will fail. Every instance must use its fixed key for all calls.
    barrier = threading.Barrier(2, timeout=5)
    seen = []
    lock = threading.Lock()

    class Client:
        def post(self, url, *, json, headers):
            with lock:
                seen.append((json["instance"], headers["Authorization"]))
            barrier.wait()
            return Response()

    for relay in relays:
        monkeypatch.setattr(relay, "_client", Client)
    def request(instance):
        relay = relays[instance]
        response, duration, retries = relay._post_upstream("/chat/completions", {"instance": instance}, "test")
        assert retries == 0
        relay._append_token_log("gpt-5.6-terra", "test", {}, duration, retries)
        return response

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(list(pool.map(request, [0, 1] * 10))) == 20
    assert Counter(seen) == {(0, "Bearer first-secret-key"): 10, (1, "Bearer second-secret-key"): 10}
    assert relays[0]._upstream_key_calls == [10, 0]
    assert relays[1]._upstream_key_calls == [0, 10]
    telemetry = (tmp_path / "tokens.jsonl").read_text()
    assert "secret-key" not in telemetry
    assert Counter(json.loads(line)["upstream_key_slot"] for line in telemetry.splitlines()) == {1: 10, 2: 10}


def test_retry_preserves_instance_key_and_redacts_errors(monkeypatch, tmp_path, caplog):
    relay = load_relay(monkeypatch, tmp_path, extra=key_file(tmp_path, ["second-secret-key"]))
    seen = []

    class Client:
        def post(self, url, *, json, headers):
            seen.append(headers["Authorization"])
            if len(seen) == 1:
                result = Response()
                result.status_code = 429
                result.text = "rate limited first-secret-key"
                return result
            return Response()

    monkeypatch.setattr(relay, "_client", Client)
    monkeypatch.setattr(relay.time, "sleep", lambda _: None)
    _, _, retries = relay._post_upstream("/responses", {}, "test")
    assert retries == 1
    assert seen == ["Bearer first-secret-key", "Bearer first-secret-key"]
    assert "rate limited" in caplog.text
    assert "first-secret-key" not in caplog.text


def test_invalid_auth_is_not_retried_or_leaked(monkeypatch, tmp_path):
    relay = load_relay(monkeypatch, tmp_path, extra=key_file(tmp_path, ["second-secret-key"]))
    class Client:
        def post(self, *args, **kwargs):
            result = Response()
            result.status_code = 401
            result.text = "invalid first-secret-key second-secret-key"
            return result
    monkeypatch.setattr(relay, "_client", Client)
    with pytest.raises(relay._UpstreamHTTPError) as exc:
        relay._post_upstream("/chat/completions", {}, "test")
    assert relay._upstream_calls == 1
    assert "secret-key" not in str(exc.value)
    assert "secret-key" not in exc.value.text


def test_single_key_compatibility_and_duplicate_deduplication(monkeypatch, tmp_path):
    relay = load_relay(monkeypatch, tmp_path)
    assert relay.UPSTREAM_API_KEYS == ("first-secret-key",)
    relay = load_relay(monkeypatch, tmp_path, extra=key_file(tmp_path, ["first-secret-key", "second-secret-key", "second-secret-key"]))
    assert relay.UPSTREAM_API_KEYS == ("first-secret-key", "second-secret-key")


@pytest.mark.parametrize("slot", [None, "0", "3", "bad"])
def test_multi_key_relay_requires_valid_fixed_slot(monkeypatch, tmp_path, slot):
    with pytest.raises(RuntimeError, match="UPSTREAM_KEY_SLOT"):
        load_relay(monkeypatch, tmp_path, extra=key_file(tmp_path, ["second-secret-key"]), slot=slot)


@pytest.mark.parametrize("keys", [[], {}, [""], [None], ["secret with spaces"]])
def test_bad_key_file_fails_without_exposing_contents(monkeypatch, tmp_path, keys):
    with pytest.raises(RuntimeError, match="UPSTREAM_EXTRA_KEYS_FILE") as exc:
        load_relay(monkeypatch, tmp_path, extra=key_file(tmp_path, keys))
    assert "secret with spaces" not in str(exc.value)


def test_key_file_requires_private_permissions_and_rejects_symlink(monkeypatch, tmp_path):
    path = key_file(tmp_path, ["second-secret-key"])
    path.chmod(0o644)
    with pytest.raises(RuntimeError):
        load_relay(monkeypatch, tmp_path, extra=path)
    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(RuntimeError):
        load_relay(monkeypatch, tmp_path, extra=link)


def test_provider_key_file_is_not_passed_to_agent_environment():
    environment = relay_agent_environment(base_url="http://127.0.0.1:6202/v1", model="gpt-5.6-terra",
        environment={"UPSTREAM_EXTRA_KEYS_FILE": "/private/keys.json", "UPSTREAM_API_KEY": "secret"})
    assert "UPSTREAM_EXTRA_KEYS_FILE" not in environment
    assert environment["UPSTREAM_API_KEY"] == "proxy"
