"""Explicit model-track adapters for native Autoresearch launchers."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from ..autonomous_optimization import task_contract


def _settings() -> tuple[str, str, str, dict[str, Any], int | None, int | None]:
    model = os.environ.get("AUTORESEARCH_MODEL", "").strip()
    if not model:
        raise RuntimeError("AUTORESEARCH_MODEL must be configured explicitly")
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("UPSTREAM_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY or UPSTREAM_API_KEY is required")
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("OPENAI_BASE_URL is required")
    raw_parameters = os.environ.get("AUTORESEARCH_MODEL_PARAMETERS", "")
    if not raw_parameters:
        raise RuntimeError("AUTORESEARCH_MODEL_PARAMETERS must be configured explicitly")
    try:
        parameters = json.loads(raw_parameters)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTORESEARCH_MODEL_PARAMETERS is invalid JSON") from exc
    if not isinstance(parameters, dict) or not parameters:
        raise RuntimeError("AUTORESEARCH_MODEL_PARAMETERS must be a non-empty object")
    timeout_raw = os.environ.get("AUTORESEARCH_REQUEST_TIMEOUT_SECONDS", "").strip()
    timeout = int(timeout_raw) if timeout_raw else None
    if timeout is not None and timeout < 1:
        raise RuntimeError("AUTORESEARCH_REQUEST_TIMEOUT_SECONDS must be positive")
    retry_raw = os.environ.get("AUTORESEARCH_RETRY_POLICY", "")
    try:
        retry_policy = json.loads(retry_raw) if retry_raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTORESEARCH_RETRY_POLICY is invalid JSON") from exc
    if not isinstance(retry_policy, dict):
        raise RuntimeError("AUTORESEARCH_RETRY_POLICY must be an object")
    retries = retry_policy.get("max_retries")
    if retries is None and retry_policy.get("max_attempts") is not None:
        retries = int(retry_policy["max_attempts"]) - 1
    retries = None if retries is None else int(retries)
    if retries is not None and retries < 0:
        raise RuntimeError("Autoresearch max retries must be non-negative")
    return model, api_key, base_url, parameters, timeout, retries


def model_identity(model: str, base_url: str) -> str:
    if not model.strip() or not base_url.strip():
        raise ValueError("model identity requires explicit model and base URL")
    endpoint_digest = hashlib.sha256(base_url.rstrip("/").encode("utf-8")).hexdigest()[:16]
    return f"openai-compatible:{model}:endpoint-{endpoint_digest}"


def _usage_values(usage: object) -> dict[str, int]:
    if usage is None:
        return {}
    if isinstance(usage, Mapping):
        source = usage
    else:
        source = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "reasoning_tokens": getattr(usage, "reasoning_tokens", None),
        }
    aliases = {
        "input": ("input_tokens", "prompt_tokens"),
        "output": ("output_tokens", "completion_tokens"),
        "reasoning": ("reasoning_tokens",),
    }
    values: dict[str, int] = {}
    for name, candidates in aliases.items():
        value = next(
            (source[candidate] for candidate in candidates if source.get(candidate) is not None),
            None,
        )
        if isinstance(value, (int, float)) and value >= 0:
            values[name] = int(value)
    return values


def _record_usage(adapter: str, model: str, usage: object) -> None:
    path_value = os.environ.get("AUTORESEARCH_MODEL_USAGE_PATH")
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"adapter": adapter, "model": model, "usage": _usage_values(usage)}
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


def _embedding(value: str, dimensions: int = 64) -> list[float]:
    output: list[float] = []
    counter = 0
    while len(output) < dimensions:
        digest = hashlib.sha256(f"{counter}:{value}".encode("utf-8")).digest()
        output.extend((byte - 127.5) / 127.5 for byte in digest)
        counter += 1
    return output[:dimensions]


def propose() -> int:
    from openai import OpenAI

    request = json.load(sys.stdin)
    contract = task_contract()
    model, api_key, base_url, parameters, timeout, retries = _settings()
    client_options: dict[str, Any] = {}
    if timeout is not None:
        client_options["timeout"] = float(timeout)
    if retries is not None:
        client_options["max_retries"] = retries
    response = OpenAI(
        api_key=api_key,
        base_url=base_url,
        **client_options,
    ).chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Return JSON with exactly plan and train_source. train_source must be the complete "
                    f"replacement {contract.artifact_name}, remain valid Python, follow the frozen "
                    "benchmark policy described in the request, and must not alter any other file."
                ),
            },
            {"role": "user", "content": json.dumps(request, sort_keys=True)},
        ],
        response_format={"type": "json_object"},
        **parameters,
    )
    _record_usage("proposal", model, response.usage)
    payload = json.loads(response.choices[0].message.content or "")
    plan = str(payload["plan"])
    train_source = str(payload["train_source"])
    json.dump(
        {
            "plan": plan,
            "train_source": train_source,
            "embedding": _embedding(plan + "\n" + train_source),
        },
        sys.stdout,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


def arbor_provider(**_kwargs: object) -> object:
    from arbor.core.llm.openai_compat import OpenAICompatProvider

    model, api_key, base_url, parameters, _timeout, _retries = _settings()

    class AccountingProvider(OpenAICompatProvider):
        async def _acompletion(self, **params: Any) -> Any:
            for name, value in parameters.items():
                params.setdefault(name, value)
            return await super()._acompletion(**params)

        def _parse_response(self, response: Any) -> Any:
            parsed = super()._parse_response(response)
            _record_usage("arbor", model, getattr(response, "usage", None))
            return parsed

    return AccountingProvider(model=model, api_key=api_key, base_url=base_url)


def evomaster_llm(**_kwargs: object) -> object:
    from evomaster.utils.llm import LLMConfig, create_llm

    model, api_key, base_url, parameters, timeout, retries = _settings()
    config_options: dict[str, Any] = {}
    if parameters.get("temperature") is not None:
        config_options["temperature"] = parameters["temperature"]
    if parameters.get("reasoning_effort") is not None:
        config_options["reasoning_effort"] = parameters["reasoning_effort"]
    output_tokens = parameters.get("max_output_tokens", parameters.get("max_tokens"))
    if output_tokens is not None:
        config_options["max_tokens"] = int(output_tokens)
    if timeout is not None:
        config_options["timeout"] = timeout
    if retries is not None:
        config_options["max_retries"] = retries
    delegate = create_llm(
        LLMConfig(
            provider="openai",
            model=model,
            api_key=api_key,
            base_url=base_url,
            **config_options,
        ),
        output_config={"show_in_console": True, "log_to_file": True},
    )

    class AccountingLLM:
        def __getattr__(self, name: str) -> object:
            return getattr(delegate, name)

        def query(self, *args: object, **kwargs: object) -> object:
            message = delegate.query(*args, **kwargs)
            meta = getattr(message, "meta", {}) or {}
            _record_usage("ml-master-2", model, meta.get("usage", {}))
            return message

    return AccountingLLM()


def ai_scientist_llm(**_kwargs: object) -> object:
    from aisci_agent_runtime.llm_client import LLMConfig, create_llm_client

    model, api_key, base_url, parameters, timeout, _retries = _settings()
    config_options: dict[str, Any] = {}
    output_tokens = parameters.get("max_output_tokens", parameters.get("max_tokens"))
    if output_tokens is not None:
        config_options["max_tokens"] = int(output_tokens)
    if parameters.get("reasoning_effort") is not None:
        config_options["reasoning_effort"] = parameters["reasoning_effort"]
    if parameters.get("temperature") is not None:
        config_options["temperature"] = parameters["temperature"]
    if parameters.get("context_window") is not None:
        config_options["context_window"] = int(parameters["context_window"])
    if timeout is not None:
        config_options["request_timeout"] = float(timeout)
    return create_llm_client(
        LLMConfig(
            provider="openai",
            model=model,
            api_key=api_key,
            base_url=base_url,
            api_mode="completions",
            **config_options,
        )
    )


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "propose":
        return propose()
    raise SystemExit("usage: python -m BenchmarkAdapters.AutoResearch.model_adapters propose")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ai_scientist_llm",
    "arbor_provider",
    "evomaster_llm",
    "model_identity",
    "propose",
]
