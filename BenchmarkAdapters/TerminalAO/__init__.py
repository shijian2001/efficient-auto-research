"""Terminal-Bench Harness Engineering AO adapters."""

from .adapter import TerminalAOAdapter, TerminalAORequest
from .aggregate import TerminalAOSeedMetrics, aggregate_terminal_ao


def run_terminal_ao(*args, **kwargs):
    from .supervisor import run_terminal_ao as implementation

    return implementation(*args, **kwargs)

__all__ = [
    "TerminalAOAdapter",
    "TerminalAORequest",
    "TerminalAOSeedMetrics",
    "aggregate_terminal_ao",
    "run_terminal_ao",
]
