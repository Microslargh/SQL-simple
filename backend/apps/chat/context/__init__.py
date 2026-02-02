"""上下文管理模块 - 系统层精准管理多轮对话上下文状态"""

from .context_types import ContextNeeds, StructuredContext
from .context_manager import ContextStateManager
from .extractors import EntityReferenceExtractor, SQLPatternExtractor, IntentContinuityAnalyzer
from .prompt_builder import ContextPromptBuilder

__all__ = [
    'ContextNeeds', 
    'StructuredContext', 
    'ContextStateManager',
    'EntityReferenceExtractor',
    'SQLPatternExtractor',
    'IntentContinuityAnalyzer',
    'ContextPromptBuilder'
]
