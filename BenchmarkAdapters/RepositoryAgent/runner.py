"""CLI for the shared repository-agent backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .backend import RepositoryAgentBackend
from .contracts import RepositoryAgentRequest
from .profiles import PROFILES


def main(argv: list[str] | None = None, *, default_agent: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    if default_agent is None:
        parser.add_argument("--agent", choices=PROFILES, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--dev-data", type=Path, required=True)
    parser.add_argument("--protected-path", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--base-url", default="https://relay.shuai-ederson-clow.xyz/v1")
    parser.add_argument("--proxy", default="http://127.0.0.1:17892")
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--evaluator-concurrency", type=int, default=8)
    parser.add_argument("--python-executable", default="python3")
    parser.add_argument("--no-apply", action="store_true")
    args = parser.parse_args(argv)
    agent = default_agent or args.agent
    result = RepositoryAgentBackend(
        RepositoryAgentRequest(
            agent=agent,
            repository=args.repository,
            evaluator=args.evaluator,
            dev_data=args.dev_data,
            protected_paths=tuple(args.protected_path),
            output_dir=args.output_dir,
            instruction=args.instruction,
            model=args.model,
            base_url=args.base_url,
            proxy=args.proxy,
            candidates=args.candidates,
            max_turns=args.max_turns,
            timeout_seconds=args.timeout,
            command_timeout_seconds=args.command_timeout,
            evaluator_concurrency=args.evaluator_concurrency,
            python_executable=args.python_executable,
            apply_best=not args.no_apply,
        )
    ).run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
