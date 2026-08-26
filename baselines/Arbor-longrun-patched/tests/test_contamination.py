from __future__ import annotations

import asyncio

from arbor.coordinator.contamination import (
    ContaminationProbe,
    declarative_assess,
    scan_canaries,
)


def test_scan_canaries_finds_planted_string():
    found = scan_canaries(["...BENCHMARK-CANARY-XYZ appears here..."], ["BENCHMARK-CANARY-XYZ"])
    assert found == ["BENCHMARK-CANARY-XYZ"]


def test_scan_canaries_clean():
    assert scan_canaries(["nothing to see"], ["CANARY"]) == []


def test_declarative_public_dataset_warns():
    report = declarative_assess({"is_public": True}, model="claude-sonnet-4-20250514")
    assert report.status in {"warn", "contaminated"}
    assert report.reasons


def test_declarative_no_signal_is_unknown():
    report = declarative_assess({}, model=None)
    assert report.status == "unknown"


def test_probe_canary_hit_is_contaminated():
    probe = ContaminationProbe()
    report = asyncio.run(probe.assess(
        dataset_info="titanic",
        eval_contract={"contamination": {"canaries": ["LEAK-1"]}},
        outputs=["my output contains LEAK-1"],
        timeout=5.0,
    ))
    assert report.status == "contaminated"


def test_probe_never_raises_on_timeout(monkeypatch):
    probe = ContaminationProbe()

    async def _slow(*a, **k):
        await asyncio.sleep(10)

    monkeypatch.setattr(probe, "_llm_membership_probe", _slow)
    report = asyncio.run(probe.assess(
        dataset_info="x",
        eval_contract={"contamination": {"is_public": True}},
        model="claude-sonnet-4-20250514",
        provider=object(),  # truthy → would trigger the (stubbed/slow) probe
        timeout=0.2,
    ))
    # falls back to declarative; never raises
    assert report.status in {"warn", "contaminated", "unknown"}
