"""Repository-mode tests: candidate = diff, score = injected evaluator.

Everything here runs against a throwaway git repo and a fake evaluator, so no
benchmark harness, network, or LLM endpoint is involved. The LLM is stubbed by
monkeypatching `agent.engine.repo_domain.llm_query`, which is the single point
where the domain talks to a model.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from agent.engine.domain import SearchSummary, TaskDomain, is_better, run_kts_search
from agent.engine.graph import Attempt, SearchGraph
from agent.engine import repo_domain as repo_mod
from agent.engine.repo_domain import (
    EvaluationResult,
    RepoDomain,
    RepoTaskConfig,
    extract_unified_diff,
    validate_diff_paths,
)


EDITABLE = ("harness.py", "templates/")


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def workspace(tmp_path):
    """A git repo with one editable file, one editable dir, one frozen file."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "harness.py").write_text("VALUE = 1\n")
    (root / "templates").mkdir()
    (root / "templates" / "prompt.txt").write_text("hello\n")
    (root / "frozen_tests.py").write_text("assert True\n")
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "ear@test", cwd=root)
    _git("config", "user.name", "EAR Test", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "baseline", cwd=root)
    return root


def _diff(old_line: str, new_line: str, path: str = "harness.py") -> str:
    return (
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old_line}\n"
        f"+{new_line}\n"
    )


def _fence(diff_text: str) -> str:
    return f"Here is the change:\n```diff\n{diff_text}```\n"


# --- diff parsing and the allowlist boundary ---


def test_extract_diff_from_fenced_and_bare_responses():
    diff = _diff("VALUE = 1", "VALUE = 2")
    assert extract_unified_diff(_fence(diff)).startswith("--- a/harness.py")
    assert extract_unified_diff("prose first\n" + diff).startswith("--- a/harness.py")
    with pytest.raises(RuntimeError):
        extract_unified_diff("no diff here at all")


def test_allowlist_accepts_editable_file_and_directory_members():
    assert validate_diff_paths(_diff("a", "b"), EDITABLE) == ("harness.py",)
    nested = _diff("hello", "world", path="templates/prompt.txt")
    assert validate_diff_paths(nested, EDITABLE) == ("templates/prompt.txt",)


@pytest.mark.parametrize(
    "path",
    ["frozen_tests.py", "/etc/passwd", "../escape.py", "templates_evil/x.py"],
)
def test_allowlist_rejects_everything_outside_the_declared_paths(path):
    """A candidate touching a frozen path is invalid regardless of its score."""
    with pytest.raises(RuntimeError):
        validate_diff_paths(_diff("a", "b", path=path), EDITABLE)


# --- the domain drives real git commits and real scoring ---


def _domain(workspace, responses, scores, **overrides):
    """Build a RepoDomain whose model returns `responses` and whose evaluator
    returns `scores`, both consumed in order."""
    calls = {"llm": 0, "eval": 0}

    def fake_llm(system, user, model=None, **kwargs):
        index = min(calls["llm"], len(responses) - 1)
        calls["llm"] += 1
        return responses[index], 10, 5

    def fake_eval(root: Path) -> EvaluationResult:
        index = min(calls["eval"], len(scores) - 1)
        calls["eval"] += 1
        value = scores[index]
        if isinstance(value, Exception):
            raise value
        return EvaluationResult(score=value, feedback={"pass_rate": value})

    repo_mod.llm_query = fake_llm
    config = RepoTaskConfig(
        workspace=workspace,
        task_description="Improve the harness.",
        editable_paths=EDITABLE,
        evaluator=fake_eval,
        **overrides,
    )
    return RepoDomain(config), calls


@pytest.fixture(autouse=True)
def _restore_llm():
    original = repo_mod.llm_query
    yield
    repo_mod.llm_query = original


@pytest.fixture(autouse=True)
def _hash_embeddings(monkeypatch):
    """Avoid downloading the sentence encoder in unit tests; the hash embedding
    is the documented fallback and keeps the kernel PSD."""
    monkeypatch.setattr(
        repo_mod, "embed_diff", lambda diff, plan="": repo_mod._hash_embedding(plan + diff)
    )


def test_successful_step_commits_the_candidate_and_records_the_score(workspace):
    domain, calls = _domain(workspace, [_fence(_diff("VALUE = 1", "VALUE = 2"))], [0.5])
    attempt = domain.step(None, 0)

    assert attempt is not None and attempt.error is None
    assert attempt.metric == 0.5
    assert (workspace / "harness.py").read_text() == "VALUE = 2\n"
    # The candidate lives on its own commit, distinct from the baseline.
    head = _git("rev-parse", "HEAD", cwd=workspace).stdout.strip()
    assert head == domain.commits[attempt.id] != domain.baseline_commit
    assert calls["eval"] == 1
    assert domain.token_usage() == (10, 5)


def test_child_candidate_builds_on_its_parent_commit(workspace):
    """The attempt tree and the commit tree stay in lock-step."""
    domain, _ = _domain(
        workspace,
        [_fence(_diff("VALUE = 1", "VALUE = 2")), _fence(_diff("VALUE = 2", "VALUE = 3"))],
        [0.5, 0.7],
    )
    parent = domain.step(None, 0)
    child = domain.step(parent, 1)

    assert child is not None and child.error is None
    assert child.parent_id == parent.id
    assert (workspace / "harness.py").read_text() == "VALUE = 3\n"
    parent_of_head = _git("rev-parse", "HEAD~1", cwd=workspace).stdout.strip()
    assert parent_of_head == domain.commits[parent.id]


def test_out_of_bounds_diff_is_rejected_and_never_touches_the_workspace(workspace):
    domain, calls = _domain(
        workspace, [_fence(_diff("assert True", "assert False", path="frozen_tests.py"))], [1.0]
    )
    attempt = domain.step(None, 0)

    assert attempt is not None
    assert attempt.error is not None and "CandidateFormatError" in attempt.error
    assert attempt.metric is None
    # No evaluation was spent and the frozen file is untouched.
    assert calls["eval"] == 0
    assert (workspace / "frozen_tests.py").read_text() == "assert True\n"
    assert _git("status", "--porcelain", cwd=workspace).stdout.strip() == ""


def test_failed_evaluation_rolls_the_workspace_back_to_the_parent(workspace):
    domain, _ = _domain(
        workspace,
        [_fence(_diff("VALUE = 1", "VALUE = 2"))],
        [RuntimeError("evaluator exploded")],
    )
    attempt = domain.step(None, 0)

    assert attempt is not None and attempt.metric is None
    assert "evaluator exploded" in attempt.error
    # Rolled back to the baseline: the tree is clean and the file is original.
    assert (workspace / "harness.py").read_text() == "VALUE = 1\n"
    assert _git("status", "--porcelain", cwd=workspace).stdout.strip() == ""


def test_unapplicable_diff_is_recorded_as_a_failed_attempt(workspace):
    """A syntactically valid, in-bounds diff whose context does not match."""
    domain, _ = _domain(workspace, [_fence(_diff("NOT_PRESENT = 9", "OTHER = 9"))], [1.0])
    attempt = domain.step(None, 0)

    assert attempt is not None and attempt.metric is None and attempt.error
    assert (workspace / "harness.py").read_text() == "VALUE = 1\n"


def test_failed_attempts_are_recorded_in_the_history(workspace):
    """Failures must stay diagnosable in the run report, not vanish."""
    domain, _ = _domain(
        workspace,
        [_fence(_diff("VALUE = 1", "VALUE = 2"))],
        [RuntimeError("evaluator exploded")],
    )
    attempt = domain.step(None, 0)
    assert len(domain.history) == 1
    assert domain.history[0]["id"] == attempt.id
    assert "evaluator exploded" in domain.history[0]["error"]

    # A rejected (out-of-bounds / unparseable) candidate is recorded too.
    domain2, _ = _domain(workspace, ["no diff at all"], [0.0])
    domain2.step(None, 0)
    assert len(domain2.history) == 1
    assert "CandidateFormatError" in domain2.history[0]["error"]


def test_restore_best_leaves_the_workspace_on_the_best_candidate(workspace):
    domain, _ = _domain(
        workspace,
        [_fence(_diff("VALUE = 1", "VALUE = 2")), _fence(_diff("VALUE = 1", "VALUE = 9"))],
        [0.9, 0.2],
    )
    good = domain.step(None, 0)
    domain.on_new_best(good)
    domain.step(None, 1)  # a worse sibling, also committed

    domain.restore_best()
    assert (workspace / "harness.py").read_text() == "VALUE = 2\n"


def test_restore_best_falls_back_to_the_baseline_when_nothing_scored(workspace):
    domain, _ = _domain(workspace, ["no diff at all"], [0.0])
    domain.step(None, 0)
    assert domain.restore_best() == domain.baseline_commit
    assert (workspace / "harness.py").read_text() == "VALUE = 1\n"


# --- prompt content: no methodology, no asymmetric hints ---


def test_prompt_states_the_task_and_constraints_without_suggesting_directions(workspace):
    domain, _ = _domain(workspace, ["x"], [0.0])
    system = domain._system_prompt()
    user = domain._user_prompt(None)

    # It must carry the task, the allowlist, and the real source.
    assert "Improve the harness." in user
    assert "harness.py" in system and "harness.py" in user
    assert "VALUE = 1" in user, "the model must see the current source to write a diff"

    # It must NOT hand the agent a domain analysis that a general-purpose
    # coding agent solving the same task would not receive.
    combined = (system + user).lower()
    for hint in (
        "parsing",
        "shell/tool execution",
        "prompt discipline",
        "context management",
        "recovery",
    ):
        assert hint not in combined, f"prompt injects domain direction: {hint!r}"


def test_prompt_reflects_parent_score_and_diff_when_refining(workspace):
    domain, _ = _domain(workspace, [_fence(_diff("VALUE = 1", "VALUE = 2"))], [0.42])
    parent = domain.step(None, 0)
    user = domain._user_prompt(parent)

    assert "0.4200" in user
    assert "VALUE = 2" in user, "the source view must show the applied candidate"
    assert "pass_rate" in user, "opaque evaluator feedback is echoed back"


# --- the KTS driver ---


class _ScriptedDomain(TaskDomain):
    """Domain returning a fixed score sequence; used to test the driver alone."""

    def __init__(self, scores, metric_sign=1):
        self.scores = scores
        self.metric_sign = metric_sign
        self.parents = []

    def step(self, parent, step_index):
        self.parents.append(parent.id if parent else None)
        if step_index >= len(self.scores):
            return None
        score = self.scores[step_index]
        return Attempt(
            id=f"n{step_index}",
            plan="p",
            code="c",
            metric=score,
            parent_id=parent.id if parent else None,
            embedding=repo_mod._hash_embedding(f"n{step_index}"),
        )


def test_driver_tracks_the_best_and_stops_at_max_steps():
    domain = _ScriptedDomain([0.1, 0.5, 0.3])
    summary = run_kts_search(domain=domain, graph=SearchGraph(), max_steps=3, time_limit=1e6)

    assert isinstance(summary, SearchSummary)
    assert summary.best_metric == 0.5
    assert summary.best_attempt.id == "n1"
    assert summary.steps_taken == 3
    assert summary.stopped_reason == "max_steps"
    assert len(summary.step_log) == 3
    # The first step has no parent to select from; later ones do.
    assert domain.parents[0] is None


def test_driver_respects_metric_direction():
    """A lower-is-better domain must select the lowest score as best."""
    domain = _ScriptedDomain([0.9, 0.2, 0.5], metric_sign=-1)
    summary = run_kts_search(domain=domain, graph=SearchGraph(), max_steps=3, time_limit=1e6)
    assert summary.best_metric == 0.2 and summary.best_attempt.id == "n1"


def test_driver_stops_on_the_time_budget():
    import time

    domain = _ScriptedDomain([0.1] * 50)
    summary = run_kts_search(
        domain=domain,
        graph=SearchGraph(),
        max_steps=50,
        time_limit=0.0,
        start_time=time.time() - 10,
    )
    assert summary.steps_taken == 0 and summary.stopped_reason == "time_limit"


def test_driver_keeps_failed_attempts_in_the_graph():
    """Failures are evidence: they must reach the kernel, not be dropped."""

    class _FailingDomain(TaskDomain):
        metric_sign = 1

        def step(self, parent, step_index):
            return Attempt(
                id=f"f{step_index}",
                plan="p",
                code="",
                error="boom",
                parent_id=parent.id if parent else None,
                embedding=repo_mod._hash_embedding(str(step_index)),
            )

    graph = SearchGraph()
    summary = run_kts_search(domain=_FailingDomain(), graph=graph, max_steps=3, time_limit=1e6)
    assert len(graph.attempts) == 3
    assert summary.best_metric is None and summary.best_attempt is None


def test_is_better_honours_direction_and_missing_values():
    assert is_better(0.5, 0.4, 1) and not is_better(0.4, 0.5, 1)
    assert is_better(0.4, 0.5, -1) and not is_better(0.5, 0.4, -1)
    assert is_better(0.1, None, 1)
    assert not is_better(None, 0.1, 1)
    assert not is_better(None, None, 1)
