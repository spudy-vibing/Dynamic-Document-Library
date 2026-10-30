"""DocCode API spike (WP-002). Throwaway: never merged, never released.

Write documents like code: sections and prompts in Python, run as a DAG, rendered to Markdown/HTML.
"""

from . import checks, parse
from ._core import Document, Prompt, Section, Value, configure, ctx
from .content import DocumentContent
from .errors import *  # noqa: F403
from .providers import CompletionRequest, Completion, MockProvider

__all__ = ["Document", "Prompt", "Section", "Value", "ctx", "configure", "checks", "parse",
           "MockProvider", "CompletionRequest", "Completion", "DocumentContent"]
__version__ = "0.0.0.spike"
