"""First-class FML-Bench formal adapter infrastructure."""

from .adapter import FMLBenchmarkAdapter, FMLRunRequest
from .aggregate import aggregate_fml, fml_scorecard
from .agents import FML_AGENT_ADAPTERS, FMLAgentAdapter, get_fml_agent_adapter
from .audit import audit_report, build_review_candidate, rendered_instruction_audit
from .protocol import FMLProtocol
from .fake_relay import CaptureRelay, CapturedRequest, capture_relay
from .readiness import collect_fml_readiness
from .records import FMLTaskRecord
from .runner import build_fml_manifest, run_fml_task
from .task import FMLTaskSpec, load_fml_task

__all__ = [
    "FMLBenchmarkAdapter",
    "FMLAgentAdapter",
    "FMLTaskSpec",
    "FML_AGENT_ADAPTERS",
    "FMLProtocol",
    "CaptureRelay",
    "CapturedRequest",
    "FMLRunRequest",
    "FMLTaskRecord",
    "aggregate_fml",
    "audit_report",
    "build_review_candidate",
    "build_fml_manifest",
    "collect_fml_readiness",
    "capture_relay",
    "fml_scorecard",
    "get_fml_agent_adapter",
    "load_fml_task",
    "rendered_instruction_audit",
    "run_fml_task",
]
