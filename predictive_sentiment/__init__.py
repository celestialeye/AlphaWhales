"""Point-in-time research for Alpha Whale Sentiment predictiveness."""

from .config import ResearchConfig
from .pipeline import RunSummary, run_research

__all__ = ["ResearchConfig", "RunSummary", "run_research"]

__version__ = "0.1.0"
