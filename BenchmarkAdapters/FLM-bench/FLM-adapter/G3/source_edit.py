#!/usr/bin/env python3
"""Use efficient-auto-research's LLM wrapper as a strict source editor."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_prompt(args: argparse.Namespace) -> str:
    if args.prompt_path:
        return Path(args.prompt_path).read_text(encoding="utf-8")
    return sys.stdin.read()


def _coerce_response(value) -> str:
    if isinstance(value, tuple):
        return str(value[0] if value else "")
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eair-root",
        default=os.environ.get("EAIR_ROOT", ""),
        help="Path to the efficient-auto-research source checkout.",
    )
    parser.add_argument("--model", default=os.environ.get("FML_MODEL_ID") or os.environ.get("MODEL"))
    parser.add_argument("--prompt-path", default="")
    parser.add_argument(
        "--output-path",
        default=os.environ.get("FML_RESPONSE_PATH")
        or os.environ.get("EAIR_OUTPUT_PATH")
        or "",
    )
    parser.add_argument("--temperature", type=float)
    args = parser.parse_args()

    if not args.model:
        raise ValueError("legacy FML source editor requires an explicit model")
    try:
        model_parameters = json.loads(os.environ.get("FML_MODEL_PARAMETERS", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("FML_MODEL_PARAMETERS is not valid JSON") from exc
    temperature = args.temperature
    if temperature is None:
        configured_temperature = model_parameters.get("temperature")
        if configured_temperature is None:
            raise ValueError("legacy FML source editor requires an explicit temperature")
        temperature = float(configured_temperature)

    root = Path(args.eair_root).expanduser().resolve()
    if not (root / "agent" / "llm" / "__init__.py").is_file():
        raise FileNotFoundError(f"invalid efficient-auto-research root: {root}")
    sys.path.insert(0, str(root))

    from agent.llm import query

    prompt = _load_prompt(args)
    response = _coerce_response(
        query(
            system_message=(
                "You are efficient-auto-research operating in strict FML-bench "
                "source-edit mode. Return only the requested HYPOTHESIS/PATCH "
                "contract. Do not run code, train models, create submissions, "
                "or start the native search loop."
            ),
            user_message=prompt,
            model=args.model,
            temperature=temperature,
        )
    )
    if args.output_path:
        path = Path(args.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response, encoding="utf-8")
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
