"""task.py — PROTECTED ground truth. Do not edit during a research run.

Define the three pieces a benchmark needs so that "fast/clever but wrong" cannot
score:

  * generate_problem(seed, ...) -> a deterministic problem instance from a seed.
  * reference_solver(problem)   -> the baseline / correctness oracle.
  * is_solution(problem, sol)   -> an INDEPENDENT verifier that recomputes the
                                   ground truth (so neither the reference nor a
                                   candidate can cheat).

Only solution.py may be edited to do better. Delete this file if your benchmark's
correctness is checked entirely inside eval.py instead.
"""

from __future__ import annotations


def generate_problem(seed: int):
    """TODO: build one deterministic problem instance from *seed*."""
    raise NotImplementedError


def reference_solver(problem):
    """TODO: the reference / baseline answer for *problem*."""
    raise NotImplementedError


def is_solution(problem, solution) -> bool:
    """TODO: independently verify *solution* against recomputed ground truth."""
    raise NotImplementedError
