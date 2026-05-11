#!/usr/bin/env python
"""并发速率控制模块"""

import asyncio
import time
from typing import Callable, Any

class RateLimiter:
    """基于时间窗口的速率限制器"""
    
    def __init__(self, requests_per_minute: int, max_concurrent: int = 5):
        """
        初始化速率限制器
        
        :param requests_per_minute: 每分钟最大请求数
        :param max_concurrent: 最大并发数
        """
        self.requests_per_minute = requests_per_minute
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.request_times = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        """获取速率限制许可"""
        # 限制并发数
        await self.semaphore.acquire()
        
        # 限制每分钟请求数
        async with self.lock:
            now = time.time()
            # 移除1分钟前的记录
            self.request_times = [t for t in self.request_times if now - t < 60]
            
            # 如果达到限制，等待
            while len(self.request_times) >= self.requests_per_minute:
                wait_time = 60 - (now - self.request_times[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                now = time.time()
                self.request_times = [t for t in self.request_times if now - t < 60]
            
            # 记录当前请求时间
            self.request_times.append(time.time())
        
        return True
    
    def release(self):
        """释放并发许可"""
        self.semaphore.release()
    
    async def run_with_limit(self, func: Callable, *args, **kwargs) -> Any:
        """在速率限制下运行函数"""
        try:
            await self.acquire()
            return await func(*args, **kwargs)
        finally:
            self.release()