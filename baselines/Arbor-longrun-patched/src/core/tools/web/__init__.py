"""Generic web search and visit tools.

Provider-agnostic surface — currently backed by the BrowseComp HTTP API but
designed so any search/browse backend (Tavily, Serper, local index, …) can be
plugged in via constructor configuration.
"""

from .alphaxiv import AlphaXivSearchTool, AlphaXivVisitTool
from .search import WebSearchTool
from .visit import WebVisitTool

__all__ = [
    "WebSearchTool",
    "WebVisitTool",
    "AlphaXivSearchTool",
    "AlphaXivVisitTool",
]
