"""上下文类型定义"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ContextNeeds:
    """上下文需求分析结果"""
    needs_entity_ref: bool = False  # 是否需要实体引用
    entity_types: List[str] = field(default_factory=list)  # 需要的实体类型（如["company", "time"]）
    needs_sql_pattern: bool = False  # 是否需要SQL模式
    needs_intent_context: bool = False  # 是否需要意图上下文
    needs_terminology_enhancement: bool = False  # 是否需要增强术语检索（结合历史问题）
    history_question: Optional[str] = None  # 历史问题（用于增强术语检索）
    
    def needs_any_context(self) -> bool:
        """检查是否需要任何上下文"""
        return (self.needs_entity_ref or 
                self.needs_sql_pattern or 
                self.needs_intent_context or 
                len(self.entity_types) > 0 or
                self.needs_terminology_enhancement)


@dataclass
class StructuredContext:
    """结构化上下文信息"""
    entity_references: Dict[str, List[str]] = field(default_factory=dict)  # 实体引用，如 {"company": ["公司A", "公司B"]}
    sql_pattern: Optional[str] = None  # SQL结构模式（占位符形式）
    intent_summary: Optional[str] = None  # 意图摘要
    relevant_data_summary: Optional[str] = None  # 相关数据摘要（精简版）
    time_range: Optional[Dict[str, str]] = None  # 时间范围，如 {"start": "202501", "end": "202512"} 或 {"time": "202501"}
    history_sql: Optional[str] = None  # 完整的历史SQL（用于追问场景，提供表名和字段名参考）
    slots_to_discard: List[str] = field(default_factory=list)  # 应丢弃的过滤条件（语义仲裁结果，如 is_consolidated）
    
    def has_content(self) -> bool:
        """检查是否有任何上下文内容"""
        return (len(self.entity_references) > 0 or 
                self.sql_pattern is not None or 
                self.intent_summary is not None or 
                self.relevant_data_summary is not None or
                self.time_range is not None or
                self.history_sql is not None or
                len(self.slots_to_discard) > 0)
