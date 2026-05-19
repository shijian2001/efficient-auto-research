"""Smoke test: verify all components import and basic graph logic works."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from agent.engine.graph import SearchGraph, Attempt
from agent.engine.thompson import select_parent, compute_posterior, improved


def test_graph_and_thompson():
    graph = SearchGraph(k=3)

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
    assert improved(a3, a1)
    assert not improved(a2, a1)

    # Thompson sampling should work
    selections = {}
    for _ in range(100):
        parent = select_parent(graph)
        selections[parent] = selections.get(parent, 0) + 1

    # All candidates should be explored (early graph is uninformative → exploratory)
    assert len(selections) >= 3, f"Expected diverse selection, got {selections}"
    print(f"Selections: {selections}")
    print("All smoke tests passed!")


if __name__ == "__main__":
    test_graph_and_thompson()
