"""Native Terminal AO launcher contracts for the five participating Agents.

MLEvolve and ML-Master 2.0 are excluded: both decide candidate success from a
produced ``submission.csv`` deep inside their engines, which the Harness
Engineering AO task shape never yields. Their modules here are fail-closed stubs.
See ``registry.TERMINAL_AO_UNSUPPORTED_REASONS``.
"""

from .common import NativeAOLaunchRequest, build_native_ao_command
from .sandbox import sandbox_native_ao_command

__all__ = ["NativeAOLaunchRequest", "build_native_ao_command", "sandbox_native_ao_command"]
