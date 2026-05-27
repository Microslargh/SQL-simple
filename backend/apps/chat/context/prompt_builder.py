"""上下文提示构建器"""

from typing import Optional
from .context_types import StructuredContext
from common.utils.utils import _async_log_util


class ContextPromptBuilder:
    """构建精简、结构化的上下文提示"""
    
    def build(self, context: StructuredContext, current_question: Optional[str] = None) -> Optional[str]:
        """构建上下文提示字符串，只输出历史用户问题供追问场景参考。

        Args:
            context: 结构化上下文信息
            current_question: 当前用户问题（保留参数兼容性，当前未使用）

        Returns:
            格式化的上下文提示字符串，如果无历史问题则返回None
        """
        if not context.history_question:
            return None

        question_text = context.history_question
        if len(question_text) > 300:
            question_text = question_text[:300] + "..."

        context_prompt = f"<user-history-question>{question_text}</user-history-question>"
        _async_log_util.info(f"[上下文构建] 构建上下文提示，长度: {len(context_prompt)} 字符")
        return context_prompt
