"""First-class FML-Bench formal adapter infrastructure."""

from .adapter import FMLBenchmarkAdapter, FMLRunRequest
from .aggregate import aggregate_fml, fml_scorecard
from .protocol import FMLProtocol
from .records import FMLTaskRecord
from .runner import build_fml_manifest, run_fml_task

__all__ = [
    "FMLBenchmarkAdapter",
    "FMLProtocol",
    "FMLRunRequest",
    "FMLTaskRecord",
    "aggregate_fml",
    "build_fml_manifest",
    "fml_scorecard",
    "run_fml_task",
]
