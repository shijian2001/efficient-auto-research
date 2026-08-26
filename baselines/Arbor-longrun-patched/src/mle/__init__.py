"""MLE-Bench adapter for Arbor.

The adapter deliberately lives on the Arbor side. MLE-Bench remains an
unmodified data preparation, submission validation, and final grading layer.
"""

from .adapter import AdapterError, WorkspaceSpec, prepare_workspace

__all__ = ["AdapterError", "WorkspaceSpec", "prepare_workspace"]
