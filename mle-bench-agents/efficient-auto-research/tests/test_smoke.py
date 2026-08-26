"""Smoke test: verify all components import and basic graph logic works."""

import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from agent.engine.graph import SearchGraph, Attempt
from agent.engine.thompson import select_parent


@contextmanager
def _isolated_numpy_seed(seed: int = 0):
    """Thompson sampling draws from the global numpy RNG; pin and restore it.

    Suite order otherwise leaks into select_parent and can collapse exploration
    (the self-observation test then fails as {'best': 200}).
    """
    state = np.random.get_state()
    np.random.seed(seed)
    try:
        yield
    finally:
        np.random.set_state(state)


def test_graph_and_thompson():
    with _isolated_numpy_seed(0):
        graph = SearchGraph()

        a1 = Attempt(id="a1", plan="xgboost", code="...", metric=0.79,
                     embedding=np.random.randn(100))
        a2 = Attempt(id="a2", plan="neural net", code="...", error="OOM",
                     parent_id="a1", embedding=np.random.randn(100))
        a3 = Attempt(id="a3", plan="xgboost tuned", code="...", metric=0.82,
                     parent_id="a1", embedding=np.random.randn(100))

        graph.add_attempt(a1)
        graph.add_attempt(a2)
        graph.add_attempt(a3)

        assert len(graph.attempts) == 3
        assert len(graph.get_children("a1")) == 2
        assert a3.metric > a1.metric  # a3 improved over a1
        assert a2.metric is None       # a2 has no metric (error)

        # Thompson sampling should work
        selections = {}
        for _ in range(100):
            parent = select_parent(graph)
            selections[parent] = selections.get(parent, 0) + 1

        # All candidates should be explored (early graph is uninformative → exploratory)
        assert len(selections) >= 3, f"Expected diverse selection, got {selections}"
        print(f"Selections: {selections}")


def test_self_observation_exploits_fresh_best():
    """A childless high-metric leaf must attract selection (GP self-observation).

    Before the fix, a fresh best node had zero observations and TS ignored it;
    with self-observations its posterior mean reflects its own metric.
    """
    rng = np.random.default_rng(0)
    graph = SearchGraph()

    # Root with a few mediocre children, plus one fresh high-metric leaf.
    graph.add_attempt(Attempt(id="root", plan="baseline", code="...", metric=0.50,
                              embedding=rng.normal(size=100)))
    for i, m in enumerate([0.52, 0.51, 0.49]):
        graph.add_attempt(Attempt(id=f"c{i}", plan=f"tweak {i}", code="...", metric=m,
                                  parent_id="root", embedding=rng.normal(size=100)))
    graph.add_attempt(Attempt(id="best", plan="great idea", code="...", metric=0.90,
                              parent_id="root", embedding=rng.normal(size=100)))

    # select_parent draws from the global numpy RNG; pin it so suite order cannot
    # collapse exploration into {'best': 200}.
    with _isolated_numpy_seed(1):
        counts = {}
        for _ in range(200):
            chosen = select_parent(graph)
            counts[chosen] = counts.get(chosen, 0) + 1

    best_share = counts.get("best", 0) / 200
    print(f"Self-observation selections: {counts} (best share={best_share:.2f})")
    # The fresh best leaf should be the clear favorite, yet not the only choice
    # (posterior variance keeps exploration alive).
    assert best_share > 0.4, f"fresh best under-exploited: {counts}"
    assert len(counts) >= 2, f"exploration collapsed: {counts}"


def test_ensemble_top_k():
    """Numeric submissions fuse by metric-weighted average; text tasks fall back."""
    from agent.engine.search import GraphSearchEngine, SearchConfig

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "ws"
        work.mkdir()
        data = Path(td) / "data"
        data.mkdir()
        (data / "train.csv").write_text("id,text\n1,a\n")

        eng = GraphSearchEngine(task_desc="t", data_dir=data, work_dir=work,
                                config=SearchConfig(max_steps=1, time_limit=1))

        subs = work / "submissions"
        subs.mkdir()
        specs = [("s1", 0.80, [0.10, 0.20]), ("s2", 0.90, [0.30, 0.40]),
                 ("s3", 0.70, [0.50, 0.60])]
        for name, metric, vals in specs:
            p = subs / f"{name}.csv"
            p.write_text("id,pred\n" + "\n".join(f"r{i},{v}" for i, v in enumerate(vals)))
            att = Attempt(id=name, plan="p", code="c", metric=metric,
                          embedding=np.zeros(4))
            eng.graph.add_attempt(att)
            eng._submission_paths[name] = str(p)

        out = eng._ensemble_top_k(k=3)
        assert out is not None and out.exists(), "ensemble not produced"
        lines = out.read_text().strip().splitlines()
        assert lines[0] == "id,pred"
        # weighted avg with weights ∝ (0.9, 0.8, 0.7): row0 = (.3*.9+.1*.8+.5*.7)/2.4
        expect = (0.30 * 0.9 + 0.10 * 0.8 + 0.50 * 0.7) / 2.4
        got = float(lines[1].split(",")[1])
        assert abs(got - expect) < 1e-9, f"weighted avg wrong: {got} vs {expect}"
        print(f"Ensemble fused OK: row0={got:.4f}")

        # Text predictions → no ensemble
        for name, _, _ in specs:
            Path(eng._submission_paths[name]).write_text("id,pred\nr0,hello\nr1,world\n")
        out.unlink()
        assert eng._ensemble_top_k(k=3) is None, "text task must skip ensemble"
        print("Text-task fallback OK")


def test_error_aggregation():
    """Same-type errors merge into one line with count and lesson."""
    from agent.engine.search import GraphSearchEngine, SearchConfig

    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "ws"; work.mkdir()
        data = Path(td) / "d"; data.mkdir()
        eng = GraphSearchEngine(task_desc="t", data_dir=data, work_dir=work,
                                config=SearchConfig(max_steps=1, time_limit=1))
        for i, err in enumerate([
            "TypeError: only integer scalar arrays can be converted",
            "TypeError: cannot unpack non-sequence",
            "FileNotFoundError: input/src_wavs/PC1_001",
        ]):
            eng.graph.add_attempt(Attempt(id=f"e{i}", plan="p", code="c",
                                          error=err, embedding=np.zeros(4)))
        errors = eng._collect_known_errors()
        assert len(errors) == 2, errors
        type_line = next(e for e in errors if e.startswith("TypeError"))
        assert "(2x)" in type_line and "lesson:" in type_line, type_line
        fnf_line = next(e for e in errors if e.startswith("FileNotFoundError"))
        assert "lesson:" in fnf_line, fnf_line
        print(f"Error aggregation OK: {errors}")


if __name__ == "__main__":
    test_graph_and_thompson()
    test_self_observation_exploits_fresh_best()
    test_ensemble_top_k()
    test_error_aggregation()
    print("All smoke tests passed!")
