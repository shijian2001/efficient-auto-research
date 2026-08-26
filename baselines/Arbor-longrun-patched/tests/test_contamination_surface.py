from __future__ import annotations

from pathlib import Path

from arbor.cli.preflight import PreflightChecker


def test_preflight_contamination_warns_on_public(tmp_path: Path):
    checker = PreflightChecker(tmp_path, provider="anthropic")
    checker.eval_contract = {"contamination": {"is_public": True}}
    result = checker._check_contamination()
    assert result.status == "warn"
    assert "public" in result.message.lower()


def test_preflight_contamination_pass_when_no_block(tmp_path: Path):
    checker = PreflightChecker(tmp_path, provider="anthropic")
    checker.eval_contract = {}
    result = checker._check_contamination()
    assert result.status == "pass"
