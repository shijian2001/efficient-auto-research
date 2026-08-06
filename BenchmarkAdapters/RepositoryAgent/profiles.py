"""Thin Agent-specific strategy profiles over the shared backend."""

from __future__ import annotations

from .contracts import AgentProfile


COMMON = """
You are optimizing a code repository against a development evaluator. Work only
inside the provided repository workspace. Inspect before editing, make focused
changes, run relevant local checks, and call evaluate_dev when you need objective
feedback. Never attempt to locate held-out test data or alter an evaluator. Finish
with submit_candidate after leaving the best working tree state in place.
""".strip()


PROFILES = {
    "ear": AgentProfile(
        key="ear",
        display_name="Efficient Agent Research",
        system_prompt=COMMON
        + "\nUse sample-efficient search: form one high-information hypothesis at a time, "
        "prefer minimal experiments, and use evaluator feedback to update the next hypothesis.",
        candidate_prompts=(
            "Candidate {candidate_index}: establish a strong minimal baseline. Current best={best_score:.6f}.",
            "Candidate {candidate_index}: explore a meaningfully different high-information change from the current best={best_score:.6f}.",
            "Candidate {candidate_index}: exploit the strongest observed direction while avoiding redundant edits. Current best={best_score:.6f}.",
        ),
    ),
    "mlevolve": AgentProfile(
        key="mlevolve",
        display_name="MLEvolve",
        system_prompt=COMMON
        + "\nUse an evolutionary workflow: diagnose the parent, draft or improve a candidate, "
        "debug failures from concrete logs, and make a larger mutation only after local stagnation.",
        candidate_prompts=(
            "Draft candidate {candidate_index} from repository evidence. Current best={best_score:.6f}.",
            "Improve or debug the current best={best_score:.6f}; preserve working behavior and target its weakest point.",
            "Evolve candidate {candidate_index} with a distinct implementation idea that can beat best={best_score:.6f}.",
        ),
    ),
    "ml-master-2": AgentProfile(
        key="ml-master-2",
        display_name="ML-Master 2.0",
        system_prompt=COMMON
        + "\nUse a research-engineering workflow: inspect architecture, plan, implement, test, "
        "and review the resulting diff before submitting.",
        candidate_prompts=(
            "Research and implement candidate {candidate_index}; current best={best_score:.6f}.",
            "Review the current best={best_score:.6f}, identify the highest-impact engineering gap, and fix it.",
            "Try an alternative architecture for candidate {candidate_index} only where evidence supports it; best={best_score:.6f}.",
        ),
    ),
    "ai-scientist": AgentProfile(
        key="ai-scientist",
        display_name="AweAI AiScientist",
        system_prompt=COMMON
        + "\nUse a file-as-bus workflow: keep plans, observations, implementation state, and "
        "validation grounded in repository files and command results rather than unsupported claims.",
        candidate_prompts=(
            "Analyze, implement, and validate candidate {candidate_index}; current best={best_score:.6f}.",
            "Prioritize the most defensible improvement over current best={best_score:.6f}, then verify it with files and commands.",
            "Independently challenge the current solution for candidate {candidate_index}; retain only evidence-backed changes. Best={best_score:.6f}.",
        ),
    ),
}


def get_profile(agent: str) -> AgentProfile:
    try:
        return PROFILES[agent]
    except KeyError as exc:
        choices = ", ".join(PROFILES)
        raise ValueError(f"no shared repository profile for {agent!r}; choose one of: {choices}") from exc


__all__ = ["AgentProfile", "PROFILES", "get_profile"]
