"""上下文状态管理器 - 系统层精准管理上下文状态"""

import re
from typing import List, Optional, Dict, Any
import orjson
from sqlalchemy.orm import Session

from apps.chat.models.chat_model import ChatLog, ChatRecord
from apps.chat.context.context_types import ContextNeeds, StructuredContext
from apps.chat.context.context_cleaner import get_slots_to_discard_hint
from apps.chat.context.extractors import EntityReferenceExtractor, SQLPatternExtractor, IntentContinuityAnalyzer
from apps.chat.context.prompt_builder import ContextPromptBuilder
from apps.chat.context.question_enhancer import QuestionEnhancer, is_any_follow_up
from apps.chat.context.question_enhance_llm import rewrite_question_with_llm
from common.core.config import settings
from common.utils.utils import _async_log_util


class ContextStateManager:
    """上下文状态管理器 - 系统层精准管理上下文状态"""
    
    def __init__(self, session: Session, current_user):
        self.session = session
        self.current_user = current_user
        self.entity_extractor = EntityReferenceExtractor(session)
        self.sql_pattern_extractor = SQLPatternExtractor()
        self.intent_analyzer = IntentContinuityAnalyzer()
        self.prompt_builder = ContextPromptBuilder()
        self.question_enhancer = QuestionEnhancer()
    
    def enhance_question_with_history(self, current_question: str, history_logs: List[ChatLog]) -> str:
        """增强当前问题，自动补充历史问题中的关键信息
        
        Args:
            current_question: 当前用户问题
            history_logs: 历史SQL日志列表
            
        Returns:
            增强后的问题
        """
        if not history_logs or len(history_logs) == 0:
            return current_question

        # 语义层门控：仅"追问/指代"才允许进入问题增强链路。
        # 避免把新的独立问句误判为追问并继承上一轮限制条件。
        q = (current_question or "").strip()
        reference_words = ['这些', '它们', '上述', '上面', '刚才', '之前', '上一轮', '刚才的', '那些']
        has_reference = any(word in q for word in reference_words)
        if not (has_reference or is_any_follow_up(q)):
            _async_log_util.info(f"[问题增强] 当前问句非追问/非指代，跳过历史增强: {q[:60]}")
            return current_question
        
        # 优先从 ChatRecord 取当轮用户问题（log.messages 里第一个 human 可能是 <context> 上下文块，非真实问句）
        def _get_user_question_for_log(log: ChatLog) -> Optional[str]:
            if log.pid:
                record = self.session.get(ChatRecord, log.pid)
                if record and getattr(record, 'question', None):
                    return (record.question or '').strip()
            if not log.messages:
                return None
            for msg in log.messages:
                if msg.get('type') != 'human':
                    continue
                content = (msg.get('content') or '').strip()
                if not content or content.startswith('<context>') or content.startswith('<time-range') or content.startswith('<history'):
                    continue
                return content
            return None

        latest_log = history_logs[-1]
        history_question = _get_user_question_for_log(latest_log)
        if history_question and history_question == (current_question or '').strip() and len(history_logs) >= 2:
            latest_log = history_logs[-2]
            history_question = _get_user_question_for_log(latest_log)
            _async_log_util.info(f"[问题增强] 最新日志与当前问题相同，使用上一轮历史: {history_question[:50] if history_question else 'None'}...")
        
        if not history_question:
            return current_question

        # 构建多轮历史（供 LLM 使用）
        max_turns = getattr(settings, "QUESTION_ENHANCE_MAX_TURNS", 7)
        history_turns: List[dict] = []
        for log in history_logs[-max_turns:]:
            user_text = _get_user_question_for_log(log)
            if not user_text:
                continue
            assistant_text: Optional[str] = None
            if log.pid:
                record = self.session.get(ChatRecord, log.pid)
                if record and getattr(record, "analysis", None):
                    assistant_text = (record.analysis or "").strip()[:500]
            history_turns.append({"user": user_text, "assistant": assistant_text})

        # 已含明确实体（省/市+指标/明细）时不再调 LLM，避免"过度增强"注入用户未提及的过滤条件（如存续、集团、压减）
        if len(q) >= 8 and ("省" in q or "市" in q or "区" in q) and ("法人" in q or "户数" in q or "明细" in q or "详情" in q):
            _async_log_util.info(f"[问题增强] 当前问句已含明确地区与指标，跳过 LLM 与规则，直接返回: {q[:60]}")
            return current_question

        # 当前问句不含时间信息时，尝试从历史 SQL 提取时间并注入
        time_keywords = ['年', '月', '日', '时间', '日期', '期', '本月', '本年', '今年', '去年', '前年', '明年']
        has_time_in_current = any(kw in q for kw in time_keywords)
        if not has_time_in_current:
            latest_log = history_logs[-1]
            if latest_log and getattr(latest_log, "pid", None):
                record = self.session.get(ChatRecord, latest_log.pid)
                if record and getattr(record, "sql", None) and record.sql:
                    try:
                        time_range = self.entity_extractor.extract_time_range(record.sql)
                        if time_range and time_range.get("display"):
                            time_display = time_range["display"]
                            # Inject time at the beginning of the question
                            enhanced = f"{time_display}，{current_question}"
                            _async_log_util.info(
                                f"[问题增强-时间注入] 当前问句无时间信息，从历史注入: "
                                f"原始: {q[:60]}, 增强后: {enhanced[:80]}"
                            )
                            return enhanced
                    except Exception as e:
                        _async_log_util.debug(f"[问题增强-时间注入] 从历史SQL提取时间失败: {e}")

        # 仅"X月份"无年份时，优先用上一轮 SQL 时间补全年份，避免 LLM 误补成 2024 等
        if re.search(r"(?:十一|十二|[一二三四五六七八九十])月份", q) and not re.search(r"\d{4}年", q):
            latest_log = history_logs[-1]
            if latest_log and getattr(latest_log, "pid", None):
                record = self.session.get(ChatRecord, latest_log.pid)
                if record and getattr(record, "sql", None) and record.sql:
                    try:
                        time_range = self.entity_extractor.extract_time_range(record.sql)
                        if time_range:
                            ref_ym = time_range.get("time") or time_range.get("end") or time_range.get("start")
                            if ref_ym and len(str(ref_ym)) >= 4:
                                ref_year = str(ref_ym)[:4]
                                enhanced = re.sub(
                                    r"((?:十一|十二|[一二三四五六七八九十])月份)",
                                    ref_year + r"年\1",
                                    q,
                                    count=1,
                                )
                                if enhanced != q:
                                    _async_log_util.info(f"[问题增强-月份] 从上一轮SQL取年份 {ref_year}，补全: {q[:50]} -> {enhanced[:60]}")
                                    return enhanced
                    except Exception as e:
                        _async_log_util.debug(f"[问题增强-月份] 从历史SQL取年份失败: {e}")

        # 【治本】LLM 优先：若已配置且有多轮历史，先调 LLM；LLM 一旦返回有效结果则立即 return，严禁再执行任何规则，杜绝"LLM 结果被规则二次拼接"
        api_url = (getattr(settings, "QUESTION_ENHANCE_API_URL", "") or "").strip().rstrip("/")
        if api_url and history_turns:
            reference_time_str = None
            latest_log = history_logs[-1]
            if latest_log and getattr(latest_log, "pid", None):
                record = self.session.get(ChatRecord, latest_log.pid)
                if record and getattr(record, "sql", None) and record.sql:
                    try:
                        time_range = self.entity_extractor.extract_time_range(record.sql)
                        if time_range:
                            ref_ym = time_range.get("time") or time_range.get("end") or time_range.get("start")
                            if ref_ym and len(str(ref_ym)) >= 6:
                                y, m = str(ref_ym)[:4], str(ref_ym)[4:6].lstrip("0") or "0"
                                reference_time_str = f"{y}年{m}月" if m != "0" else f"{y}年"
                    except Exception:
                        pass
            llm_rewritten = rewrite_question_with_llm(
                history_turns, current_question or "", reference_time_str=reference_time_str
            )
            if llm_rewritten and llm_rewritten.strip():
                _async_log_util.info(f"[问题增强-LLM] 采用 LLM 补全，直接返回（不再执行规则）: {llm_rewritten[:80]}")
                return llm_rewritten.strip()

        # 仅当未配置 LLM 或 LLM 未返回时，才走规则增强
        _async_log_util.info(f"[问题增强] 参与规则增强的历史问题(前80字): {history_question[:80]}")
        enhanced_question = self.question_enhancer.enhance_question(current_question, history_question)
        return enhanced_question
    
    def analyze_context_needs(self, current_question: str, history_logs: List[ChatLog]) -> ContextNeeds:
        """分析当前问题需要哪些上下文信息
        
        Args:
            current_question: 当前用户问题
            history_logs: 历史SQL日志列表
            
        Returns:
            上下文需求分析结果
        """
        needs = ContextNeeds()
        
        if not history_logs or len(history_logs) == 0:
            return needs
        
        # 1. 识别指代词 或 追问（时间/地区/指标/公司主体）
        reference_words = ['这些', '它们', '上述', '上面', '刚才', '之前', '上一轮', '刚才的', '那些']
        has_reference = any(word in current_question for word in reference_words)
        is_followup = is_any_follow_up(current_question)
        
        if has_reference or is_followup:
            needs.needs_entity_ref = True
            needs.needs_intent_context = True
            needs.needs_terminology_enhancement = True  # 需要增强术语检索
            
            # 获取历史问题，用于增强术语检索
            latest_log = history_logs[-1]
            if latest_log.messages:
                for msg in latest_log.messages:
                    if msg.get('type') == 'human':
                        needs.history_question = msg.get('content', '')
                        break
            
            # 识别需要的实体类型
            entity_keywords = {
                'company': ['公司', '企业'],
                'city': ['城市', '市'],
                'province': ['省', '省份'],
                'time': ['时间', '日期', '月份', '年份'],
            }
            
            for entity_type, keywords in entity_keywords.items():
                if any(keyword in current_question for keyword in keywords):
                    needs.entity_types.append(entity_type)
            
            # 如果没有明确指定实体类型，默认检查公司
            if not needs.entity_types:
                needs.entity_types.append('company')
        
        # 2. 识别SQL模式需求
        # 如果问题中包含"类似"、"参考"、"按照"等词，可能需要SQL模式
        pattern_keywords = ['类似', '参考', '按照', '同样的', '相同的结构']
        if any(keyword in current_question for keyword in pattern_keywords):
            needs.needs_sql_pattern = True
        
        # 3. 识别意图连续性
        # 如果问题中包含"继续"、"接着"、"然后"等词，需要意图上下文
        intent_keywords = ['继续', '接着', '然后', '再', '还']
        if any(keyword in current_question for keyword in intent_keywords):
            needs.needs_intent_context = True
        
        # 4. 如果历史日志存在，默认需要意图上下文（用于理解对话连续性）
        if len(history_logs) > 0:
            needs.needs_intent_context = True
            # 检查是否需要时间上下文（如果当前问题没有明确指定时间，但历史查询有时间范围）
            time_keywords = ['时间', '日期', '月份', '年份', '年', '月', '日']
            has_time_in_current = any(keyword in current_question for keyword in time_keywords)
            if not has_time_in_current:
                # 当前问题没有明确时间，需要检查历史是否有时间范围
                needs.needs_terminology_enhancement = True  # 复用这个标志，表示需要提取时间上下文
        
        _async_log_util.info(f"[上下文管理] 上下文需求分析: entity_ref={needs.needs_entity_ref}, "
                           f"entity_types={needs.entity_types}, sql_pattern={needs.needs_sql_pattern}, "
                           f"intent={needs.needs_intent_context}")
        
        return needs
    
    def extract_context(self, needs: ContextNeeds, history_logs: List[ChatLog], current_question: Optional[str] = None) -> StructuredContext:
        """从历史记录中提取结构化上下文
        
        Args:
            needs: 上下文需求分析结果
            history_logs: 历史SQL日志列表
            current_question: 当前用户问题（用于意图分析）
            
        Returns:
            结构化上下文信息
        """
        context = StructuredContext()
        
        if not history_logs or len(history_logs) == 0:
            return context
        
        # 获取最近的一条历史日志
        latest_log = history_logs[-1]
        
        # 1. 提取实体引用上下文
        if needs.needs_entity_ref and latest_log.pid:
            try:
                record = self.session.get(ChatRecord, latest_log.pid)
                if record and record.data and record.data.strip():
                    try:
                        exec_data = orjson.loads(record.data)
                        if exec_data and isinstance(exec_data, dict):
                            # 提取各种类型的实体
                            for entity_type in needs.entity_types:
                                entities = self.entity_extractor.extract_entities_from_data(
                                    exec_data, entity_type, max_count=50
                                )
                                if entities:
                                    context.entity_references[entity_type] = entities
                            
                            # 如果没有指定类型，尝试提取公司
                            if not context.entity_references and 'company' in needs.entity_types:
                                companies = self.entity_extractor.extract_companies(exec_data, max_count=50)
                                if companies:
                                    context.entity_references['company'] = companies
                    except Exception as e:
                        _async_log_util.debug(f"[上下文管理] 解析历史数据失败: {e}")
            except Exception as e:
                _async_log_util.debug(f"[上下文管理] 加载历史记录失败: {e}")
        
        # 2. 提取SQL模式上下文
        if needs.needs_sql_pattern and latest_log.pid:
            try:
                record = self.session.get(ChatRecord, latest_log.pid)
                if record and record.sql and record.sql.strip():
                    sql_pattern = self.sql_pattern_extractor.extract_pattern(record.sql)
                    if sql_pattern:
                        context.sql_pattern = sql_pattern
            except Exception as e:
                _async_log_util.debug(f"[上下文管理] 提取SQL模式失败: {e}")
        
        # 2.5. 提取时间范围上下文（如果当前问题没有明确指定时间）
        if latest_log.pid:
            try:
                record = self.session.get(ChatRecord, latest_log.pid)
                if record and record.sql and record.sql.strip():
                    time_range = self.entity_extractor.extract_time_range(record.sql)
                    if time_range:
                        context.time_range = time_range
                        _async_log_util.info(f"[上下文管理] 提取到时间范围: {time_range}")
            except Exception as e:
                _async_log_util.debug(f"[上下文管理] 提取时间范围失败: {e}")
        
        # 2.6. 提取历史问题（用于 SQL 生成阶段，帮助 LLM 理解上下文意图）
        # 当检测到追问（时间/地区/指标/公司主体）时，提供历史问题作为参考
        if (needs.needs_terminology_enhancement or needs.needs_intent_context) and latest_log.pid:
            try:
                record = self.session.get(ChatRecord, latest_log.pid)
                if record:
                    # 提取历史用户问题
                    history_question = None
                    if latest_log.messages:
                        for msg in latest_log.messages:
                            if msg.get('type') == 'human':
                                content = (msg.get('content') or '').strip()
                                if content and not content.startswith('<context>') and not content.startswith('<time-range') and not content.startswith('<history'):
                                    history_question = content
                                    break
                    if history_question:
                        context.history_question = history_question[:500]  # 截断，避免过长
                        _async_log_util.info(f"[上下文管理] 提取到历史问题，长度: {len(context.history_question)} 字符")

                    # 2.7. 语义仲裁：检测「子集过滤」与「全量分布」冲突，获取应丢弃的槽位
                    history_question = None
                    if latest_log.messages:
                        for msg in latest_log.messages:
                            if msg.get('type') == 'human':
                                history_question = msg.get('content', '')
                                break
                    if history_question and current_question:
                        discarded = get_slots_to_discard_hint(
                            current_question=current_question,
                            history_question=history_question,
                            history_sql=record.sql.strip(),
                        )
                        if discarded:
                            context.slots_to_discard = discarded
                            _async_log_util.info(f"[上下文管理] 语义仲裁：应丢弃槽位 {discarded}")
            except Exception as e:
                _async_log_util.debug(f"[上下文管理] 提取历史SQL失败: {e}")
        
        # 3. 提取意图上下文
        if needs.needs_intent_context and latest_log.messages:
            try:
                # 从历史日志中提取历史问题
                history_question = None
                history_sql = None
                
                for msg in latest_log.messages:
                    if msg.get('type') == 'human':
                        history_question = msg.get('content', '')
                        break
                
                # 获取历史SQL
                if latest_log.pid:
                    record = self.session.get(ChatRecord, latest_log.pid)
                    if record and record.sql:
                        history_sql = record.sql
                
                # 分析意图连续性（需要当前问题，这里暂时使用历史问题作为占位）
                # 实际使用时，当前问题会在调用时传入
                if history_question:
                    # 这里暂时不分析，因为需要当前问题
                    # 实际分析会在 build_context_prompt 时进行
                    pass
            except Exception as e:
                _async_log_util.debug(f"[上下文管理] 提取意图上下文失败: {e}")
        
        return context
    
    def build_context_prompt(self, context: StructuredContext, current_question: Optional[str] = None, 
                           history_logs: Optional[List[ChatLog]] = None) -> Optional[str]:
        """构建精简、结构化的上下文提示
        
        Args:
            context: 结构化上下文信息
            current_question: 当前用户问题（用于意图分析）
            history_logs: 历史日志列表（用于意图分析）
            
        Returns:
            格式化的上下文提示字符串
        """
        # 如果提供了当前问题和历史日志，进行意图分析
        if current_question and history_logs and len(history_logs) > 0:
            latest_log = history_logs[-1]
            if latest_log.messages:
                history_question = None
                history_sql = None
                
                for msg in latest_log.messages:
                    if msg.get('type') == 'human':
                        history_question = msg.get('content', '')
                        break
                
                if latest_log.pid:
                    try:
                        record = self.session.get(ChatRecord, latest_log.pid)
                        if record and record.sql:
                            history_sql = record.sql
                    except Exception:
                        pass
                
                if history_question:
                    intent_summary = self.intent_analyzer.analyze_intent(
                        current_question, history_question, history_sql
                    )
                    if intent_summary:
                        context.intent_summary = intent_summary
        
        # 构建提示
        return self.prompt_builder.build(context, current_question)
