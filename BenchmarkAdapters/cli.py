"""CLI for the shared benchmark adapter packages."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from .agents import get_agent_adapter
from .contracts import AdapterError, CommandSpec
from .MLEBenchLite.campaign import (
    aggregate_campaign,
    build_mle_protocol,
    campaign_cells,
    run_campaign_cell,
)
from .MLEBenchLite.adapter import MleLiteRequest
from .preflight import collect_preflight
from .process import run_command
from .protocol import FormalProtocol
from .registry import AGENTS, ROOT
from .security import is_sensitive_name, redact_url
from .status import collect_status
from .TerminalBench.adapter import HarborTerminalRequest
from .TerminalAO.adapter import TerminalAORequest
from .TerminalAO.aggregate import aggregate_terminal_ao
from .TerminalAO.protocol import TerminalAOProtocol

def _redacted_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for value in argv:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        name, separator, _ = value.partition("=")
        if separator and is_sensitive_name(name):
            redacted.append(f"{name}=<redacted>")
        else:
            redacted.append(value)
            redact_next = value.startswith("-") and is_sensitive_name(value.lstrip("-"))
    return tuple(redacted)


def _command_payload(command: CommandSpec) -> dict[str, object]:
    return {
        "command": shlex.join(_redacted_argv(command.argv)),
        "cwd": str(command.cwd),
        "environment": {
            key: redact_url(value)
            for key, value in sorted(command.env.items())
            if not is_sensitive_name(key)
        },
        "timeout_seconds": command.timeout_seconds,
        "label": command.label,
        "artifact_path": str(command.artifact_path) if command.artifact_path else None,
    }


def _add_common_endpoint_args(
    parser: argparse.ArgumentParser,
    *,
    include_timeout: bool = True,
) -> None:
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--upstream-base-url",
        default="https://relay.shuai-ederson-clow.xyz/v1",
    )
    parser.add_argument("--proxy", default="http://127.0.0.1:17892")
    if include_timeout:
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
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--instruction")
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser)


def _terminal_parser(
    subparsers: argparse._SubParsersAction,
    name: str,
    *,
    help_text: str,
) -> None:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("--agent", required=True)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=HarborTerminalRequest.__dataclass_fields__["dataset_dir"].default,
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=HarborTerminalRequest.__dataclass_fields__["jobs_dir"].default,
    )
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--agent-concurrency", type=int)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--exclude-task", action="append", default=[])
    parser.add_argument("--agent-kwarg", action="append", default=[])
    parser.add_argument("--job-name")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--timeout-multiplier", type=float, default=1.0)
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser, include_timeout=False)


def _terminal_ao_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "terminal-ao",
        help="run the formal Terminal-Bench 36/53 harness-optimization protocol",
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=172800)
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser, include_timeout=False)


def _formal_tools_parsers(subparsers: argparse._SubParsersAction) -> None:
    status = subparsers.add_parser("status", help="show stable protocol/readiness status")
    status.add_argument("--output", type=Path)

    preflight = subparsers.add_parser("preflight", help="validate formal protocols and readiness")
    preflight.add_argument("--mle-data-root", type=Path, default=ROOT / "mle-bench-data")
    preflight.add_argument(
        "--ao-protocol",
        type=Path,
        default=ROOT / "terminal-bench-2/ao_protocol/protocol.json",
    )

    protocol = subparsers.add_parser("mle-protocol", help="write the frozen MLE protocol")
    protocol.add_argument("--output", type=Path, required=True)
    protocol.add_argument("--model", default="gpt-5.5")
    protocol.add_argument("--seed", action="append", type=int, default=[])
    protocol.add_argument("--timeout", type=int, default=86400)

    cell = subparsers.add_parser("mle-cell", help="run one manifest-bound formal MLE cell")
    cell.add_argument("--protocol", type=Path, required=True)
    cell.add_argument("--agent", required=True)
    cell.add_argument("--competition-id", required=True)
    cell.add_argument("--seed", type=int, required=True)
    cell.add_argument("--data-root", type=Path, required=True)
    cell.add_argument("--campaign-dir", type=Path, required=True)
    cell.add_argument("--gpu-id", type=int, default=0)

    mle_aggregate = subparsers.add_parser("mle-aggregate", help="aggregate official MLE reports")
    mle_aggregate.add_argument("--protocol", type=Path, required=True)
    mle_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    mle_aggregate.add_argument("--agent", required=True)
    mle_aggregate.add_argument("--output", type=Path)

    ao_aggregate = subparsers.add_parser(
        "terminal-ao-aggregate", help="aggregate held-out 53-task AO results"
    )
    ao_aggregate.add_argument("--protocol", type=Path, required=True)
    ao_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    ao_aggregate.add_argument("--agent", required=True)
    ao_aggregate.add_argument("--output", type=Path)

    scorecard = subparsers.add_parser(
        "scorecard", help="produce separate MLE and Terminal AO scorecards"
    )
    scorecard.add_argument("--mle-protocol", type=Path, required=True)
    scorecard.add_argument("--mle-campaign-dir", type=Path, required=True)
    scorecard.add_argument("--ao-protocol", type=Path, required=True)
    scorecard.add_argument("--ao-campaign-dir", type=Path, required=True)
    scorecard.add_argument("--output", type=Path)


def _print_or_write(payload: object, output: Path | None = None) -> None:
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if output is None:
        print(serialized, end="")
        return
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    except FileExistsError as exc:
        raise AdapterError(f"refusing to overwrite aggregate/status output: {output}") from exc


def _handle_formal_tool(args: argparse.Namespace) -> int | None:
    if args.command == "status":
        _print_or_write(collect_status(), args.output)
        return 0
    if args.command == "preflight":
        _print_or_write(
            collect_preflight(mle_data_root=args.mle_data_root, ao_protocol_path=args.ao_protocol)
        )
        return 0
    if args.command == "mle-protocol":
        protocol = build_mle_protocol(
            model=args.model,
            seeds=tuple(args.seed or (0, 1, 2)),
            wall_clock_seconds=args.timeout,
        )
        protocol.write(args.output)
        _print_or_write({"protocol_path": str(args.output.resolve()), "protocol_digest": protocol.digest})
        return 0
    if args.command == "mle-cell":
        protocol = FormalProtocol.load(args.protocol)
        matching = [
            item
            for item in campaign_cells(protocol, args.campaign_dir, agents=(args.agent,))
            if item.task_id == args.competition_id and item.seed == args.seed
        ]
        if len(matching) != 1:
            raise AdapterError("requested MLE cell is outside the frozen protocol grid")
        outcome = run_campaign_cell(
            cell=matching[0],
            protocol=protocol,
            data_root=args.data_root,
            gpu_id=args.gpu_id,
        )
        result = outcome.result if hasattr(outcome, "result") else outcome
        _print_or_write(result.to_dict())
        return 0 if result.score_valid else 1
    if args.command == "mle-aggregate":
        payload = aggregate_campaign(FormalProtocol.load(args.protocol), args.campaign_dir, args.agent)
        _print_or_write(payload, args.output)
        return 0
    if args.command == "terminal-ao-aggregate":
        payload = aggregate_terminal_ao(
            protocol=TerminalAOProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
            agent=args.agent,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "scorecard":
        mle_protocol = FormalProtocol.load(args.mle_protocol)
        ao_protocol = TerminalAOProtocol.load(args.ao_protocol)
        payload = {
            "schema_version": 1,
            "comparison_policy": {
                "separate_benchmark_scorecards": True,
                "composite_score": None,
                "terminal_direct_89_excluded": True,
            },
            "readiness": collect_status(),
            "mle": {
                agent: aggregate_campaign(mle_protocol, args.mle_campaign_dir, agent)
                for agent in AGENTS
            },
            "terminal_ao": {
                agent: aggregate_terminal_ao(
                    protocol=ao_protocol,
                    campaign_dir=args.ao_campaign_dir,
                    agent=agent,
                )
                for agent in AGENTS
            },
        }
        _print_or_write(payload, args.output)
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shared baseline benchmark adapters")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _mle_parser(subparsers)
    _terminal_ao_parser(subparsers)
    _terminal_parser(
        subparsers,
        "terminal-direct-smoke",
        help_text="run a non-comparable direct Terminal-Bench infrastructure smoke",
    )
    _terminal_parser(
        subparsers,
        "terminal",
        help_text="deprecated alias for terminal-direct-smoke",
    )
    _formal_tools_parsers(subparsers)
    args = parser.parse_args(argv)
    handled = _handle_formal_tool(args)
    if handled is not None:
        return handled

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
            dry_run=args.dry_run,
            seed=args.seed,
        )
        adapter = get_agent_adapter(args.agent).mle_lite
        if args.dry_run:
            command = adapter.build_command(request)
            print(json.dumps(_command_payload(command), indent=2, ensure_ascii=False))
            return 0
        submission = adapter.run(request)
        print(submission)
        return 0
    elif args.command == "terminal-ao":
        request = TerminalAORequest(
            agent=args.agent,
            protocol_path=args.protocol,
            output_dir=args.output_dir,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            seed=args.seed,
            timeout_seconds=args.timeout,
            dry_run=args.dry_run,
        )
        command = get_agent_adapter(args.agent).build_terminal_ao_command(request)
    else:
        request = HarborTerminalRequest(
            agent=args.agent,
            dataset_dir=args.dataset_dir,
            jobs_dir=args.jobs_dir,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            attempts=args.attempts,
            concurrency=args.concurrency,
            agent_concurrency=args.agent_concurrency,
            task_names=tuple(args.task),
            exclude_task_names=tuple(args.exclude_task),
            agent_kwargs=tuple(args.agent_kwarg),
            job_name=args.job_name,
            max_retries=args.max_retries,
            timeout_multiplier=args.timeout_multiplier,
            force_build=args.force_build,
            dry_run=args.dry_run,
        )
        command = get_agent_adapter(args.agent).build_terminal_command(request)

    if args.dry_run:
        payload = _command_payload(command)
        if args.command in {"terminal", "terminal-direct-smoke"}:
            payload.update(
                {
                    "mode": "terminal-direct-smoke",
                    "non_comparable_to_terminal_ao": True,
                    "deprecated_alias": args.command == "terminal",
                }
            )
        elif args.command == "terminal-ao":
            payload["mode"] = "terminal-ao"
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = run_command(command)
    print(result.stdout, end="")
    return result.return_code


def cli_entrypoint(argv: list[str] | None = None) -> int:
    try:
        return main(argv)
    except AdapterError as exc:
        _print_or_write(
            {
                "status": "failed",
                "score_valid": False,
                "error_type": type(exc).__name__,
                "failure_reason": str(exc),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_entrypoint())
