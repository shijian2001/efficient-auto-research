from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "BenchmarkAdapters/adapter_smoke.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(SOURCE_PATH))


def test_smoke_matrix_has_exactly_fourteen_cells() -> None:
    namespace: dict[str, object] = {}
    matrix_node = next(
        node
        for node in TREE.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SMOKE_VARIANTS" for target in node.targets)
    )
    matrix = ast.literal_eval(matrix_node.value)
    assert set(matrix) == {"mle-bench-lite", "terminal-bench-ao"}
    assert all(len(cells) == 7 for cells in matrix.values())
    assert matrix["mle-bench-lite"]["arbor"] == "arbor-benchmark-patched"
    assert matrix["terminal-bench-ao"]["ai-scientist"] == "ai-scientist-terminal-variant"
    assert matrix["terminal-bench-ao"]["ml-master-2"] == "ml-master-autoresearch-variant"


def test_smoke_is_non_comparable_and_never_scores() -> None:
    assert "score_valid: bool = False" in SOURCE
    assert "non_comparable: bool = True" in SOURCE
    for forbidden in ("grade_csv", "grade-sample", "evaluate_harness(", "SealedTestGate("):
        assert forbidden not in SOURCE


def test_each_relay_is_one_call_and_skips_readiness() -> None:
    calls = [
        node
        for node in ast.walk(TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RelayProcess"
    ]
    assert len(calls) == 2
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert ast.literal_eval(keywords["check_upstream_ready"]) is False
        assert ast.literal_eval(keywords["max_upstream_calls"]) == 1
    assert '"LLM_MAX_UPSTREAM_CALLS": "1"' in SOURCE
    assert '"LLM_SKIP_UPSTREAM_READY": "1"' in SOURCE


def test_smoke_uses_real_adapter_entry_builders() -> None:
    for required in (
        "MleLiteAdapter(agent).build_command",
        "build_native_ao_command(request)",
        "sandbox_native_ao_command(",
        "TerminalAOProtocol.load(protocol_path)",
        "materialize_baseline(",
        'competition_id="spooky-author-identification"',
    ):
        assert required in SOURCE


def test_supervisor_terminates_only_after_telemetry() -> None:
    function = next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_until_first_request"
    )
    segment = ast.get_source_segment(SOURCE, function) or ""
    assert "records = _read_telemetry(token_log_path)" in segment
    assert "if records:" in segment
    assert "_terminate_process_group(process)" in segment


def test_reviewed_source_keeps_installed_runtime_separate() -> None:
    function = next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "_reviewed_source"
    )
    segment = ast.get_source_segment(SOURCE, function) or ""
    assert "install_path=worktree" in segment
    assert "runtime_path=original_spec.execution_path" in segment
    assert '(worktree / ".venv").mkdir()' in segment
    assert "symlink_to" not in segment


def test_terminal_sandbox_aliases_reviewed_source_to_editable_install_path() -> None:
    sandbox_path = ROOT / "BenchmarkAdapters/TerminalAO/launchers/sandbox.py"
    sandbox_source = sandbox_path.read_text(encoding="utf-8")
    sandbox_tree = ast.parse(sandbox_source, filename=str(sandbox_path))
    function = next(
        node
        for node in sandbox_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "sandbox_native_ao_command"
    )
    segment = ast.get_source_segment(sandbox_source, function) or ""
    assert "execution_path = AGENTS[agent].execution_path.absolute()" in segment
    assert "_ro_bind(argv, source_path, execution_path, created)" in segment


def test_arbor_smoke_uses_local_git_capable_image() -> None:
    assert 'smoke_environment["CONTAINER_IMAGE"] = "alexgshaw/fix-git:20251031"' in SOURCE


def test_ai_scientist_smoke_uses_official_local_image_options() -> None:
    run_function = next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_mle_smoke"
    )
    segment = ast.get_source_segment(SOURCE, run_function) or ""
    assert '"alexgshaw/fix-git:20251031" if agent == "ai-scientist" else None' in segment
    assert 'image_pull_policy="never" if agent == "ai-scientist" else None' in segment
    assert 'official_llm_profile="gpt-5.4" if agent == "ai-scientist" else None' in segment


def test_ml_master_terminal_smoke_allows_one_attempt_but_relay_stays_capped() -> None:
    run_function = next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_terminal_smoke"
    )
    segment = ast.get_source_segment(SOURCE, run_function) or ""
    assert '"max_retries": 1 if agent == "ml-master-2" else 0' in segment
    assert "max_upstream_calls=1" in segment
