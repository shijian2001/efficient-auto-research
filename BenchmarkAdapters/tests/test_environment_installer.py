from __future__ import annotations

import importlib.util
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_PATH = ROOT / "BenchmarkAdapters" / "environments" / "install.py"


def _installer_module():
    spec = importlib.util.spec_from_file_location("environment_installer", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_autoresearch_is_a_standalone_locked_profile(capsys) -> None:
    installer = _installer_module()
    manifest = installer.load_manifest()
    names = installer.profile_names(manifest)
    assert len(names) == 30
    assert "autoresearch" in names
    assert "optimizer-design" in names
    assert names[-1] == "optimizer-design.ai-scientist"

    installer.install_profile(manifest, "autoresearch", dry_run=True)
    output = capsys.readouterr().out
    assert "standalone benchmark environment" in output
    assert "autoresearch" in output
    assert "--locked" in output
    assert "--managed-python" in output


def test_all_autoresearch_agent_profiles_are_explicit_and_locked(capsys) -> None:
    installer = _installer_module()
    manifest = installer.load_manifest()
    for agent in (
        "ear",
        "mlevolve",
        "arbor",
        "codex",
        "claude-code",
        "ml-master-2",
        "ai-scientist",
    ):
        installer.install_profile(manifest, f"autoresearch.{agent}", dry_run=True)
    output = capsys.readouterr().out
    assert "environments/terminal/arbor" in output
    assert "environments/terminal/ai-scientist" in output
    assert "environments/agents/mlevolve-autoresearch" in output
    assert "environments/agents/ml-master-2-autoresearch" in output
    assert "codex --version" in output
    assert "claude --version" in output


def test_python_selector_accepts_version_specific_override(monkeypatch) -> None:
    installer = _installer_module()
    interpreter = "/opt/python/cpython-3.11/bin/python"
    monkeypatch.setenv("BENCHMARK_ADAPTERS_PYTHON_311", interpreter)
    assert installer.python_selector({"python": "3.11"}) == interpreter
    assert os.environ["BENCHMARK_ADAPTERS_PYTHON_311"] == interpreter


def test_optimizer_design_profiles_are_explicit_and_locked(capsys) -> None:
    installer = _installer_module()
    manifest = installer.load_manifest()
    installer.install_profile(manifest, "optimizer-design", dry_run=True)
    for agent in (
        "ear",
        "mlevolve",
        "arbor",
        "codex",
        "claude-code",
        "ml-master-2",
        "ai-scientist",
    ):
        installer.install_profile(manifest, f"optimizer-design.{agent}", dry_run=True)
    output = capsys.readouterr().out
    assert "optimizer-design --python 3.10 --locked --managed-python" in output
    assert "mlevolve-autoresearch" in output
    assert "ml-master-2-autoresearch" in output
    assert "codex --version" in output
    assert "claude --version" in output
