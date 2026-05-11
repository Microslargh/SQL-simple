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
        # 确保token以Bearer前缀开头
        if not user_token.lower().startswith("bearer "):
            self.user_token = f"Bearer {user_token}"
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.verify_ssl = verify_ssl
        self.headers = {
            "Content-Type": "application/json",
            "X-SQLBOT-TOKEN": self.user_token
        }
    
    async def create_chat(self, datasource: int = None, origin: int = 0) -> Dict[str, Any]:
        """创建新对话
        
        Args:
            datasource: 数据源ID（必填）
            origin: 来源标识，0=页面，1=mcp，2=小助手
        """
        url = f"{self.api_base_url}/chat/start"
        payload = {
            "origin": origin
        }
        if datasource is not None:
            payload["datasource"] = datasource
        
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(url, json=payload, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Failed to create chat: {response.status} - {text}")
                
                result = await response.json()
                
                # 解析嵌套结构：{"code": 0, "data": {...}, "msg": null}
                if "data" in result:
                    result = result["data"]
                
                chat_id = result.get("id")
                
                # 检查chat_id是否有效
                if chat_id is None:
                    raise Exception(f"Failed to create chat: chat_id is null. Response: {result}")
                
                # 确保chat_id是整数
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
        current_step = None
        step_start_times = {}
        
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(url, json=payload, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Failed to send question: {response.status} - {text}")
                
                async for line in response.content:
                    line = line.decode("utf-8").strip()
                    if not line:
                        continue
                    
                    # 解析SSE格式：data: {"type": "...", ...}
                    if line.startswith("data:"):
                        try:
                            data_str = line[5:].strip()
                            data = json.loads(data_str)
                            
                            # 记录步骤开始时间
                            if data.get("type") == "step-start":
                                step_name = data.get("step")
                                current_step = step_name
                                step_start_times[step_name] = time.time()
                                logger.debug(f"Step started: {step_name}")
                            
                            # 记录步骤完成时间
                            elif data.get("type") == "step-complete":
                                step_name = data.get("step")
                                if step_name in step_start_times:
                                    duration_ms = int((time.time() - step_start_times[step_name]) * 1000)
                                    data["duration_ms"] = duration_ms
                                    logger.debug(f"Step completed: {step_name} ({duration_ms}ms)")
                            
                            results.append(data)
                            
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse JSON: {line[:200]} - {e}")
        
        # 添加总耗时
        total_duration_ms = int((time.time() - start_time) * 1000)
        results.append({
            "type": "total_duration",
            "duration_ms": total_duration_ms
        })
        
        return results
    
    async def get_chat_record(self, chat_id: int, record_id: int) -> Dict[str, Any]:
        """获取对话记录详情"""
        url = f"{self.api_base_url}/chat/get/{chat_id}"
        
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Failed to get chat record: {response.status} - {text}")
                
                result = await response.json()
                # 解析嵌套结构
                if "data" in result:
                    result = result["data"]
                return result
    
    async def get_execution_trace(self, record_id: int) -> List[Dict[str, Any]]:
        """获取执行轨迹"""
        url = f"{self.api_base_url}/chat/record/{record_id}/trace"
        
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Failed to get execution trace: {response.status} - {text}")
                
                result = await response.json()
                # 解析嵌套结构
                if "data" in result:
                    result = result["data"]
                return result
    
    async def get_datasource_list(self) -> List[Dict[str, Any]]:
        """获取数据源列表"""
        url = f"{self.api_base_url}/datasource/list"
        
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(url, headers=self.headers, ssl=False) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(f"Failed to get datasource list: {response.status} - {text}")
                
                result = await response.json()
                # 解析嵌套结构
                if "data" in result:
                    result = result["data"]
                return result