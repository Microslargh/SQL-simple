#!/usr/bin/env python
"""大模型评分模块 - 支持OpenAI兼容API"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, Any, Optional, Tuple

from openai import AsyncOpenAI
from openai._exceptions import OpenAIError, RateLimitError, APIConnectionError, APIStatusError

logger = logging.getLogger(__name__)

class LLMEvaluator:
    """大模型评估器 - 支持OpenAI兼容API"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = self._create_client()
        self.rate_limit = config.get("rate_limit", {})
        self.requests_per_minute = self.rate_limit.get("requests_per_minute", 30)
        self.min_interval = 60.0 / self.requests_per_minute
        self.last_request_time = 0
        self.max_retries = 3
        self.retry_delay = 5

    def _create_client(self):
        """创建大模型客户端 - 支持OpenAI兼容API"""
        llm_config = self.config.get("llm", {})
        api_key = llm_config.get("api_key", "sk-no-key-required")  # 兼容不需要key的API
        api_base = llm_config.get("api_base")
        api_version = llm_config.get("api_version")
        
        # 创建客户端配置
        client_kwargs = {
            "api_key": api_key,
        }
        
        # 支持自定义API地址（兼容API）
        if api_base:
            client_kwargs["base_url"] = api_base
        
        # 支持API版本（Azure等）
        if api_version:
            client_kwargs["api_version"] = api_version
        
        logger.info(f"创建LLM客户端: base_url={api_base}, model={llm_config.get('model')}")
        return AsyncOpenAI(**client_kwargs)

    async def _rate_limit_wait(self):
        """速率限制等待"""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    async def _call_llm(self, messages: list) -> str:
        """调用大模型API - 统一处理各种兼容API"""
        llm_config = self.config.get("llm", {})
        
        for attempt in range(self.max_retries):
            try:
                await self._rate_limit_wait()
                
                response = await self.client.chat.completions.create(
                    model=llm_config["model"],
                    messages=messages,
                    temperature=llm_config.get("temperature", 0.2),
                    max_tokens=llm_config.get("max_tokens", 1024),
                    timeout=llm_config.get("timeout", 60)
                )

                # 处理标准OpenAI响应格式
                if hasattr(response, 'choices') and len(response.choices) > 0:
                    message = response.choices[0].message
                    if hasattr(message, 'content'):
                        return message.content.strip()
                
                # 尝试兼容其他响应格式
                response_dict = response.model_dump() if hasattr(response, 'model_dump') else {}
                if 'choices' in response_dict and len(response_dict['choices']) > 0:
                    content = response_dict['choices'][0].get('message', {}).get('content', '')
                    if content:
                        return content.strip()
                
                logger.error(f"无法解析API响应: {response}")
                return ""

            except RateLimitError as e:
                delay = self.retry_delay * (attempt + 1)
                logger.warning(f"API限流 (第{attempt+1}/{self.max_retries}次): {e}，{delay}秒后重试...")
                await asyncio.sleep(delay)
            except APIConnectionError as e:
                delay = self.retry_delay * (attempt + 1)
                logger.warning(f"API连接失败 (第{attempt+1}/{self.max_retries}次): {e}，{delay}秒后重试...")
                await asyncio.sleep(delay)
            except APIStatusError as e:
                delay = self.retry_delay * (attempt + 1)
                logger.warning(f"API状态错误 {e.status_code} (第{attempt+1}/{self.max_retries}次): {e}，{delay}秒后重试...")
                await asyncio.sleep(delay)
            except OpenAIError as e:
                delay = self.retry_delay * (attempt + 1)
                logger.warning(f"API调用失败 (第{attempt+1}/{self.max_retries}次): {e}，{delay}秒后重试...")
                await asyncio.sleep(delay)
            except Exception as e:
                delay = self.retry_delay * (attempt + 1)
                logger.warning(f"未知错误 (第{attempt+1}/{self.max_retries}次): {e}，{delay}秒后重试...")
                await asyncio.sleep(delay)

        logger.error(f"API调用失败，已重试{self.max_retries}次")
        return ""

    def _parse_score(self, result: str) -> Tuple[int, str]:
        """解析评分结果"""
        if not result:
            return 0, "无响应"

        try:
            # 尝试JSON格式解析
            parsed = json.loads(result)
            score = int(parsed.get("score", 0))
            reason = parsed.get("reason", "")
            return max(0, min(100, score)), reason
        except json.JSONDecodeError:
            # 尝试正则提取分数
            score_match = re.search(r'["\']?score["\']?\s*[：:]\s*(\d+)', result)
            if score_match:
                return int(score_match.group(1)), result
            
            # 尝试提取中文格式分数
            score_match_cn = re.search(r'分数\s*[：:]\s*(\d+)', result)
            if score_match_cn:
                return int(score_match_cn.group(1)), result
            
            return 0, f"解析失败: {result[:200]}"

    async def score_question_rewrite(self, original_question: str, rewritten_question: str) -> Tuple[int, str]:
        """
        评估问题改写质量
        
        评分维度：
        1. 完整性：改写后的问题是否保留了原始问题的所有关键信息
        2. 准确性：改写是否准确表达了原始意图
        3. 丰富性：是否添加了必要的上下文信息
        4. 清晰性：改写后的问题是否更清晰易懂
        """
        if not original_question or not rewritten_question:
            return 0, "输入为空"

        prompt = f"""
请评估以下问题改写的质量：

原始问题：{original_question}

改写后问题：{rewritten_question}

请从以下维度进行评估（每项25分，总分100分）：
1. 完整性（25分）：改写后的问题是否保留了原始问题的所有关键信息？
2. 准确性（25分）：改写是否准确表达了原始意图？有无语义偏差？
3. 丰富性（25分）：是否添加了必要的上下文信息或约束条件？
4. 清晰性（25分）：改写后的问题是否更清晰易懂？

请输出JSON格式的评估结果：
{{
    "score": 分数（0-100）,
    "reason": "评估理由，分点说明各维度的评价"
}}
        """

        messages = [
            {"role": "system", "content": "你是一个专业的问题改写质量评估专家。请根据提供的评分标准进行客观评估。"},
            {"role": "user", "content": prompt.strip()}
        ]

        result = await self._call_llm(messages)
        return self._parse_score(result)

    async def score_sql_generation(self, question: str, sql: str, context: str = "") -> Tuple[int, str]:
        """
        评估SQL生成质量
        
        评分维度：
        1. 语法正确性：SQL语句是否符合语法规范
        2. 逻辑正确性：SQL是否正确回答了问题
        3. 性能优化：SQL是否高效，有无优化空间
        4. 可读性：SQL是否易于理解和维护
        """
        if not question or not sql:
            return 0, "输入为空"

        prompt = f"""
请评估以下SQL语句的质量：

用户问题：{question}

生成的SQL：{sql}

上下文信息（可选）：{context if context else '无'}

请从以下维度进行评估（每项25分，总分100分）：
1. 语法正确性（25分）：SQL语句是否符合语法规范？能否正确执行？
2. 逻辑正确性（25分）：SQL是否正确回答了用户问题？结果是否准确？
3. 性能优化（25分）：SQL是否高效？有无优化空间（如索引使用、JOIN优化等）？
4. 可读性（25分）：SQL是否结构清晰、易于理解和维护？

请输出JSON格式的评估结果：
{{
    "score": 分数（0-100）,
    "reason": "评估理由，分点说明各维度的评价"
}}
        """

        messages = [
            {"role": "system", "content": "你是一个专业的SQL质量评估专家。请根据提供的评分标准进行客观评估。"},
            {"role": "user", "content": prompt.strip()}
        ]

        result = await self._call_llm(messages)
        return self._parse_score(result)

    async def evaluate_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """评估单条记录"""
        result = {
            "question_rewrite_score": None,
            "question_rewrite_reason": "",
            "sql_generation_score": None,
            "sql_generation_reason": "",
            "overall_score": None,
            "error": ""
        }

        try:
            # 评估问题改写
            if self.config["scoring"].get("enable_question_rewrite", True):
                original = str(record.get("question_rewrite_input", ""))
                rewritten = str(record.get("question_rewrite_output", ""))
                score, reason = await self.score_question_rewrite(original, rewritten)
                result["question_rewrite_score"] = score
                result["question_rewrite_reason"] = reason

            # 评估SQL生成
            if self.config["scoring"].get("enable_sql_generation", True):
                question = str(record.get("question", "")) or str(record.get("question_rewrite_input", ""))
                sql = str(record.get("sql_generated", ""))
                context = str(record.get("sql_gen_prompt", ""))[:500]
                score, reason = await self.score_sql_generation(question, sql, context)
                result["sql_generation_score"] = score
                result["sql_generation_reason"] = reason

            # 计算综合评分
            weights = self.config["scoring"]
            if result["question_rewrite_score"] is not None and result["sql_generation_score"] is not None:
                result["overall_score"] = int(
                    result["question_rewrite_score"] * weights.get("question_rewrite_weight", 0.4) +
                    result["sql_generation_score"] * weights.get("sql_generation_weight", 0.6)
                )
            elif result["question_rewrite_score"] is not None:
                result["overall_score"] = result["question_rewrite_score"]
            elif result["sql_generation_score"] is not None:
                result["overall_score"] = result["sql_generation_score"]

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"评估记录失败: {e}")

        return result