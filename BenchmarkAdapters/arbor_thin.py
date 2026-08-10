"""Official Arbor CLI configuration helpers for thin adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from .contracts import AdapterError


def write_arbor_config(
    path: Path,
    *,
    model: str,
    base_url: str,
    model_parameters: Mapping[str, object] | None = None,
    eval_command: str | None = None,
    metric_direction: str | None = None,
    artifact_name: str | None = None,
    protected_paths: Sequence[str] = (),
    required_outputs: Sequence[str] = (),
) -> Path:
    if not model.strip() or not base_url.strip():
        raise AdapterError("Arbor official config requires an explicit model and base URL")
    parameters = dict(model_parameters or {})
    use_completion_api = parameters.get("use_completion_api") is True
    api_mode = str(parameters.get("api_mode", "")).strip().lower()
    provider = "openai-chat" if use_completion_api or api_mode == "completions" else "openai-responses"
    llm: dict[str, object] = {
        "provider": provider,
        "model": model,
        "base_url": base_url.rstrip("/"),
    }
    if parameters.get("reasoning_effort") is not None:
        llm["reasoning_effort"] = str(parameters["reasoning_effort"])
    output_tokens = parameters.get("max_output_tokens", parameters.get("max_tokens"))
    if output_tokens is not None:
        llm["max_tokens"] = int(output_tokens)
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"llm": llm}
    if eval_command is not None:
        if metric_direction not in {"minimize", "maximize"}:
            raise AdapterError("Arbor evaluator plugin requires a metric direction")
        plugin_name = "benchmark_dev"
        plugin_path = path.parent / "plugins" / f"{plugin_name}.yaml"
        plugin_path.parent.mkdir(parents=True, exist_ok=True)
        eval_contract: dict[str, object] = {
            "metric_direction": metric_direction,
            "eval_cmd": eval_command,
        }
        if artifact_name:
            eval_contract["submission_path"] = artifact_name
        plugin = {
            "schema_version": 1,
            "name": plugin_name,
            "description": "Host-owned development evaluator transport",
            "eval_contract": eval_contract,
            "protected_paths": list(protected_paths),
            "required_outputs": list(required_outputs),
            "meta_preamble_inject": (
                "Use only the injected B_dev evaluator during search. "
                "Held-out evaluation is unavailable."
            ),
        }
        try:
            with plugin_path.open("x", encoding="utf-8") as handle:
                json.dump(plugin, handle, sort_keys=True, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise AdapterError(f"refusing to overwrite Arbor plugin: {plugin_path}") from exc
        payload["plugin"] = plugin_name
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite Arbor config: {path}") from exc
    return path


__all__ = ["write_arbor_config"]
