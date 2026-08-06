"""CLI for the shared benchmark adapter packages."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from .agents import get_agent_adapter
from .contracts import CommandSpec
from .MLEBenchLite.adapter import MleLiteRequest
from .process import run_command
from .TerminalBench.adapter import TerminalAoRequest


def _command_payload(command: CommandSpec) -> dict[str, object]:
    return {
        "command": shlex.join(command.argv),
        "cwd": str(command.cwd),
        "environment": {
            key: value
            for key, value in sorted(command.env.items())
            if "KEY" not in key and "TOKEN" not in key and "SECRET" not in key
        },
        "timeout_seconds": command.timeout_seconds,
        "label": command.label,
    }


def _add_common_endpoint_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--upstream-base-url",
        default="https://relay.shuai-ederson-clow.xyz/v1",
    )
    parser.add_argument("--proxy", default="http://127.0.0.1:17892")
    parser.add_argument("--timeout", type=int, default=900)


def _mle_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("mle", help="build or run an MLE-Bench Lite adapter")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--run-tag")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--instruction")
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser)


def _terminal_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("terminal", help="build or run a Terminal-Bench AO adapter")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--harness-dir", type=Path, required=True)
    parser.add_argument("--eval-script", type=Path, required=True)
    parser.add_argument("--dev-data", type=Path, required=True)
    parser.add_argument("--test-data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument("--optimize", action="store_true")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--python-executable", default="auto")
    parser.add_argument("--instruction")
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shared baseline benchmark adapters")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _mle_parser(subparsers)
    _terminal_parser(subparsers)
    args = parser.parse_args(argv)

    if args.command == "mle":
        request = MleLiteRequest(
            agent=args.agent,
            competition_id=args.competition_id,
            data_root=args.data_root,
            output_dir=args.output_dir,
            gpu_id=args.gpu_id,
            steps=args.steps,
            timeout_seconds=args.timeout,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            run_tag=args.run_tag,
            instruction=args.instruction,
            max_turns=args.max_turns,
            config_path=args.config_path,
            force=args.force,
        )
        command = get_agent_adapter(args.agent).build_mle_command(request)
    else:
        request = TerminalAoRequest(
            agent=args.agent,
            harness_dir=args.harness_dir,
            eval_script=args.eval_script,
            dev_data=args.dev_data,
            test_data=args.test_data,
            output_dir=args.output_dir,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            python_executable=args.python_executable,
            instruction=args.instruction,
            candidates=args.candidates,
            max_turns=args.max_turns,
            command_timeout_seconds=args.command_timeout,
        )
        adapter = get_agent_adapter(args.agent).terminal_ao
        if args.optimize:
            command = adapter.build_optimizer_command(request)
        elif args.split:
            command = adapter.build_eval_command(request, args.split)
        else:
            parser.error("terminal requires --optimize or --split")

    if args.dry_run:
        print(json.dumps(_command_payload(command), indent=2, ensure_ascii=False))
        return 0

    result = run_command(command)
    print(result.stdout, end="")
    return result.return_code


if __name__ == "__main__":
    raise SystemExit(main())
