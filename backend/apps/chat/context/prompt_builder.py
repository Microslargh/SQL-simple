"""上下文提示构建器"""

from typing import Optional
from .context_types import StructuredContext
from common.utils.utils import _async_log_util


class ContextPromptBuilder:
    """构建精简、结构化的上下文提示"""
    
    def build(self, context: StructuredContext) -> Optional[str]:
        """构建上下文提示字符串
        
        Args:
            context: 结构化上下文信息
            
        Returns:
            格式化的上下文提示字符串，如果无内容则返回None
        """
        if not context.has_content():
            return None
        
        parts = []
        
        # 实体引用
        if context.entity_references:
            for entity_type, entities in context.entity_references.items():
                if entities:
                    # 限制实体数量，避免过长
                    display_entities = entities[:20]  # 最多显示20个
                    entity_str = ", ".join(display_entities)
                    if len(entities) > 20:
                        entity_str += f" (共{len(entities)}个)"
                    
                    type_names = {
                        'company': '公司',
                        'city': '城市',
                        'province': '省份',
                        'country': '国家',
                        'time': '时间',
                    }
                    type_name = type_names.get(entity_type, entity_type)
                    parts.append(f'<entity-ref type="{entity_type}">{entity_str}</entity-ref>')
        
        # SQL模式
        if context.sql_pattern:
            # 限制SQL模式长度
            sql_pattern = context.sql_pattern[:200] + "..." if len(context.sql_pattern) > 200 else context.sql_pattern
            parts.append(f'<sql-pattern>{sql_pattern}</sql-pattern>')
        
        # 意图摘要
        if context.intent_summary:
            parts.append(f'<intent>{context.intent_summary}</intent>')
        
        # 相关数据摘要（精简版）
        if context.relevant_data_summary:
            # 限制数据摘要长度
            data_summary = context.relevant_data_summary[:300] + "..." if len(context.relevant_data_summary) > 300 else context.relevant_data_summary
            parts.append(f'<data-summary>{data_summary}</data-summary>')
        
        # 时间范围
        if context.time_range:
            time_format = context.time_range.get('format', 'YYYYMM')
            if 'start' in context.time_range and 'end' in context.time_range:
                start = context.time_range['start']
                end = context.time_range['end']
                time_str = f"{start} 至 {end}"
                parts.append(f'<time-range start="{start}" end="{end}" format="{time_format}">{time_str}</time-range>')
            elif 'time' in context.time_range:
                time_value = context.time_range['time']
                parts.append(f'<time-range time="{time_value}" format="{time_format}">{time_value}</time-range>')
        
        # 历史SQL（用于追问场景，提供表名和字段名参考；不作为当前问题硬约束）
        if context.history_sql:
            parts.append(
                "<history-sql-note>以下 history-sql 仅用于参考表名、字段名与 SQL 结构；"
                "不得直接继承其中的地域过滤、口径修正（如 +1）等具体条件。"
                "当前 SQL 以当前问题和术语规则为准。</history-sql-note>"
            )
            parts.append(f'<history-sql>{context.history_sql}</history-sql>')

        # 应丢弃的过滤条件（语义仲裁：子集过滤与全量分布冲突时）
        if context.slots_to_discard:
            slot_to_field = {
                "is_consolidated": "sfbb（并表口径）",
                "register_status": "register_status（注册状态）",
            }
            discard_hints = []
            for slot in context.slots_to_discard:
                hint = slot_to_field.get(slot, slot)
                discard_hints.append(hint)
            parts.append(
                f'<filter-disposal>【重要】生成 SQL 时不要使用以下过滤条件（因与当前问题意图冲突，会导致分布统计失真）：{", ".join(discard_hints)}</filter-disposal>'
            )
        
        if not parts:
            return None
        
        # 组合成结构化格式
        context_prompt = "\n".join(parts)
        _async_log_util.info(f"[上下文构建] 构建上下文提示，长度: {len(context_prompt)} 字符")
        return context_prompt
