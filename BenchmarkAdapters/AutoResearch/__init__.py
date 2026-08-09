"""Autoresearch Architecture Design formal benchmark support."""

from .broker import CandidateDevBroker
from .evaluator import CandidateEvaluation, CandidateEvaluator, EvaluatorManifest
from .protocol import AutoResearchProtocol
from .revisions import TrainRevision, TrainRevisionStore
from .search import SearchContext, SearchOutcome
from .seed_injection import SeedPolicy, inject_seed
from .supervisor import run_autoresearch

__all__ = [
    "AutoResearchProtocol",
    "CandidateDevBroker",
    "CandidateEvaluation",
    "CandidateEvaluator",
    "EvaluatorManifest",
    "SeedPolicy",
    "SearchContext",
    "SearchOutcome",
    "TrainRevision",
    "TrainRevisionStore",
    "inject_seed",
    "run_autoresearch",
]
