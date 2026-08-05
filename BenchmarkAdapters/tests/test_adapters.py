from __future__ import annotations

from pathlib import Path

import pytest

from BenchmarkAdapters import UnsupportedAdapterError
from BenchmarkAdapters.MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest
from BenchmarkAdapters.TerminalBench.adapter import (
    TerminalAoAdapter,
    TerminalAoRequest,
    parse_pass_rate,
)


def _public_task(tmp_path: Path) -> Path:
    public = tmp_path / "demo" / "prepared" / "public"
    public.mkdir(parents=True)
    (public / "description.md").write_text("# Demo\n", encoding="utf-8")
    (public / "sample_submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")
    return public


def test_codex_mle_adapter_reuses_public_workspace(tmp_path: Path) -> None:
    public = _public_task(tmp_path)
    request = MleLiteRequest(
        agent="codex",
        competition_id="demo",
        data_root=tmp_path,
        output_dir=tmp_path / "run",
    )
    command = MleLiteAdapter("codex").build_command(request)
    assert command.cwd == (tmp_path / "run" / "workspace").resolve()
    assert command.argv[0] == "codex"
    assert command.env["OPENAI_BASE_URL"].endswith("/v1")
    assert command.env["HTTPS_PROXY"] == "http://127.0.0.1:17892"
    assert public.is_dir()
    assert command.cwd.joinpath("input").is_symlink()
    assert command.cwd.joinpath("sample_submission.csv").is_file()


def test_terminal_ao_reuses_evaluator_contract(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    evaluator = harness / "run_eval.py"
    evaluator.write_text("print('7/10')\n", encoding="utf-8")
    dev = tmp_path / "dev.json"
    test = tmp_path / "test.json"
    dev.write_text("{}\n", encoding="utf-8")
    test.write_text("{}\n", encoding="utf-8")
    request = TerminalAoRequest(
        agent="codex",
        harness_dir=harness,
        eval_script=evaluator,
        dev_data=dev,
        test_data=test,
        output_dir=tmp_path / "run",
    )
    command = TerminalAoAdapter("codex").build_eval_command(request, "dev")
    assert command.env["HARBOR_N_CONCURRENT"] == "8"
    assert command.argv[-1] == str(dev.resolve())
    assert parse_pass_rate("passed=7/10") == 0.7


def test_blocked_terminal_agent_fails_closed(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    evaluator = harness / "run_eval.py"
    evaluator.write_text("print('1/1')\n", encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text("{}\n", encoding="utf-8")
    request = TerminalAoRequest(
        agent="mlevolve",
        harness_dir=harness,
        eval_script=evaluator,
        dev_data=data,
        test_data=data,
        output_dir=tmp_path / "run",
    )
    with pytest.raises(UnsupportedAdapterError):
        TerminalAoAdapter("mlevolve").build_optimizer_command(request)


def test_parse_pass_rate_accepts_reward() -> None:
    assert parse_pass_rate("reward: 0.75") == 0.75
