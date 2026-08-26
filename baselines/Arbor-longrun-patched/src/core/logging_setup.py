"""Centralized logging configuration.

Suppresses noisy HTTP client libraries (httpx, httpcore, openai, anthropic)
so that agent-level thinking and tool execution are clearly visible.
"""

from __future__ import annotations

import logging
import sys

_NOISY_LOGGERS = [
    "httpx",
    "httpcore",
    "anthropic",
    "openai",
    "urllib3",
    "asyncio",
    "hpack",
    "h2",
]


def setup_logging(*, verbose: bool = False) -> None:
    """Configure logging for the research agent.

    Agent-level logs (arbor.*) use INFO (or DEBUG if verbose).
    HTTP/SDK client libraries are silenced to WARNING to keep output readable.
    """
    log_level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
