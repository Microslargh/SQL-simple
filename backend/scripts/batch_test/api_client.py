#!/usr/bin/env python
"""SQLBot API客户端模块"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, Any, Optional, List

import aiohttp

logger = logging.getLogger(__name__)

class SQLBotAPIClient:
    """SQLBot API客户端"""

    def __init__(self, api_base_url: str, user_token: str, timeout_seconds: int = 300, verify_ssl: bool = False):
        self.api_base_url = api_base_url.rstrip("/")
        self.user_token = user_token
        if not user_token.lower().startswith("bearer "):
            self.user_token = f"Bearer {user_token}"
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.verify_ssl = verify_ssl
        self.headers = {
            "Content-Type": "application/json",
            "X-SQLBOT-TOKEN": self.user_token
        }
        # Token刷新相关
        self.token_refresh_url = f"{self.api_base_url}/login/access-token"
        self.last_token_refresh = time.time()
        self.token_refresh_interval = 3600
        # 重试配置
        self.max_retries = 5  # 增加重试次数
        self.retry_delay = 30  # 基础重试间隔
        self.retry_status_codes = [500, 502, 503, 504]
        # 服务健康检查相关
        self.consecutive_errors = 0  # 连续错误计数
        self.max_consecutive_errors = 3  # 最大连续错误数
        self.cool_down_duration = 300  # 冷却时间（5分钟）
        self.last_cool_down_time = 0  # 上次冷却时间

    async def _check_service_health(self):
        """检查服务健康状态"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                # 尝试访问一个简单的接口
                url = f"{self.api_base_url}/datasource/list"
                async with session.get(url, headers=self.headers, ssl=False) as response:
                    return response.status == 200
        except Exception:
            return False

    async def _wait_for_service_recovery(self):
        """等待服务恢复（冷却期）"""
        if time.time() - self.last_cool_down_time < self.cool_down_duration:
            # 已经在冷却期，等待剩余时间
            wait_time = self.cool_down_duration - (time.time() - self.last_cool_down_time)
            logger.info(f"服务冷却中，等待 {wait_time:.1f} 秒...")
            await asyncio.sleep(wait_time)
            return
        
        # 进入冷却期
        self.last_cool_down_time = time.time()
        self.consecutive_errors = 0  # 重置错误计数
        logger.warning(f"服务连续出错，进入 {self.cool_down_duration} 秒冷却期...")
        
        # 等待服务恢复
        health_check_interval = 30  # 每30秒检查一次
        total_wait_time = 0
        
        while total_wait_time < self.cool_down_duration:
            if await self._check_service_health():
                logger.info("服务已恢复！")
                return
            logger.info(f"服务尚未恢复，等待 {health_check_interval} 秒...")
            await asyncio.sleep(health_check_interval)
            total_wait_time += health_check_interval
        
        logger.info("冷却期结束，继续测试...")

    async def _refresh_token(self):
        """尝试刷新token"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                login_url = f"{self.api_base_url}/login/autoLogin"
                async with session.get(login_url, ssl=False) as response:
                    if response.status == 200:
                        result = await response.json()
                        if "data" in result:
                            result = result["data"]
                        new_token = result.get("access_token") or result.get("token")
                        if new_token:
                            self.user_token = f"Bearer {new_token}"
                            self.headers["X-SQLBOT-TOKEN"] = self.user_token
                            self.last_token_refresh = time.time()
                            logger.info("Token刷新成功")
                            return True
            logger.warning("Token刷新失败，继续使用旧token")
            return False
        except Exception as e:
            logger.error(f"Token刷新异常: {e}")
            return False

    async def _make_request_with_retry(self, method, url, **kwargs):
        """带智能重试机制的请求封装"""
        # 检查是否需要刷新token
        current_time = time.time()
        if current_time - self.last_token_refresh > self.token_refresh_interval:
            await self._refresh_token()

        for attempt in range(self.max_retries):
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.request(method, url, headers=self.headers, ssl=False, **kwargs) as response:
                    # Token过期处理
                    if response.status in [401, 403]:
                        logger.warning(f"Token可能过期，尝试刷新...")
                        if await self._refresh_token():
                            continue
                        else:
                            text = await response.text()
                            raise Exception(f"Token过期且刷新失败: {response.status} - {text}")
                    
                    # 5xx错误处理
                    if response.status in self.retry_status_codes:
                        # 增加连续错误计数
                        self.consecutive_errors += 1
                        
                        # 检查是否需要进入冷却期
                        if self.consecutive_errors >= self.max_consecutive_errors:
                            await self._wait_for_service_recovery()
                            # 冷却后重试
                            continue
                        
                        if attempt < self.max_retries - 1:
                            delay = self.retry_delay * (attempt + 1)  # 递增延迟
                            logger.warning(f"请求失败({response.status})，连续错误#{self.consecutive_errors}，{delay}秒后重试...")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            text = await response.text()
                            raise Exception(f"请求失败，已重试{self.max_retries}次: {response.status} - {text}")
                    
                    # 重置连续错误计数
                    self.consecutive_errors = 0
                    
                    if response.status != 200:
                        text = await response.text()
                        raise Exception(f"请求失败: {response.status} - {text}")
                    
                    return await response.json()
        
        raise Exception(f"请求失败，已重试{self.max_retries}次")

    async def create_chat(self, datasource: int = None, origin: int = 0) -> Dict[str, Any]:
        """创建新对话"""
        url = f"{self.api_base_url}/chat/start"
        payload = {"origin": origin}
        if datasource is not None:
            payload["datasource"] = datasource
        
        result = await self._make_request_with_retry("POST", url, json=payload)
        
        if "data" in result:
            result = result["data"]
        
        chat_id = result.get("id")
        if chat_id is None:
            raise Exception(f"Failed to create chat: chat_id is null. Response: {result}")
        
        if isinstance(chat_id, str):
            try:
                chat_id = int(chat_id)
                result["id"] = chat_id
            except ValueError:
                raise Exception(f"Invalid chat_id format: {chat_id}")
        
        logger.debug(f"Created chat successfully: id={chat_id}")
        return result

    async def send_question(self, chat_id: int, question: str) -> List[Dict[str, Any]]:
        """发送问题并解析流式响应"""
        url = f"{self.api_base_url}/chat/question"
        payload = {
            "chat_id": chat_id,
            "question": question,
            "lang": "简体中文"
        }
        
        results = []
        start_time = time.time()
        step_start_times = {}
        
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(url, json=payload, headers=self.headers, ssl=False) as response:
                        if response.status in [401, 403]:
                            if await self._refresh_token():
                                continue
                            else:
                                text = await response.text()
                                raise Exception(f"Token过期且刷新失败: {response.status} - {text}")
                        
                        if response.status in self.retry_status_codes:
                            self.consecutive_errors += 1
                            
                            if self.consecutive_errors >= self.max_consecutive_errors:
                                await self._wait_for_service_recovery()
                                continue
                            
                            if attempt < self.max_retries - 1:
                                delay = self.retry_delay * (attempt + 1)
                                logger.warning(f"发送问题失败({response.status})，连续错误#{self.consecutive_errors}，{delay}秒后重试...")
                                await asyncio.sleep(delay)
                                continue
                            else:
                                text = await response.text()
                                raise Exception(f"发送问题失败，已重试{self.max_retries}次: {response.status} - {text}")
                        
                        self.consecutive_errors = 0
                        
                        if response.status != 200:
                            text = await response.text()
                            raise Exception(f"发送问题失败: {response.status} - {text}")
                        
                        async for line in response.content:
                            line = line.decode("utf-8").strip()
                            if not line or not line.startswith("data:"):
                                continue
                            
                            try:
                                data_str = line[5:].strip()
                                data = json.loads(data_str)
                                
                                if data.get("type") == "step-start":
                                    step_name = data.get("step")
                                    step_start_times[step_name] = time.time()
                                    logger.debug(f"Step started: {step_name}")
                                
                                elif data.get("type") == "step-complete":
                                    step_name = data.get("step")
                                    if step_name in step_start_times:
                                        duration_ms = int((time.time() - step_start_times[step_name]) * 1000)
                                        data["duration_ms"] = duration_ms
                                        logger.debug(f"Step completed: {step_name} ({duration_ms}ms)")
                                
                                results.append(data)
                            
                            except json.JSONDecodeError as e:
                                logger.warning(f"Failed to parse JSON: {line[:200]} - {e}")
                    
                    total_duration_ms = int((time.time() - start_time) * 1000)
                    results.append({
                        "type": "total_duration",
                        "duration_ms": total_duration_ms
                    })
                    
                    return results
            
            except Exception as e:
                self.consecutive_errors += 1
                
                if self.consecutive_errors >= self.max_consecutive_errors:
                    await self._wait_for_service_recovery()
                    continue
                
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (attempt + 1)
                    logger.warning(f"发送问题异常，连续错误#{self.consecutive_errors}，{delay}秒后重试: {e}")
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise
        
        raise Exception(f"发送问题失败，已重试{self.max_retries}次")

    async def get_chat_record(self, chat_id: int, record_id: int) -> Dict[str, Any]:
        """获取对话记录详情"""
        url = f"{self.api_base_url}/chat/get/{chat_id}"
        result = await self._make_request_with_retry("GET", url)
        if "data" in result:
            result = result["data"]
        return result

    async def get_execution_trace(self, record_id: int) -> List[Dict[str, Any]]:
        """获取执行轨迹"""
        url = f"{self.api_base_url}/chat/record/{record_id}/trace"
        result = await self._make_request_with_retry("GET", url)
        if "data" in result:
            result = result["data"]
        return result

    async def get_datasource_list(self) -> List[Dict[str, Any]]:
        """获取数据源列表"""
        url = f"{self.api_base_url}/datasource/list"
        result = await self._make_request_with_retry("GET", url)
        if "data" in result:
            result = result["data"]
        return result