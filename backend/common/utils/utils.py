import base64
import hashlib
import inspect
import json
import logging
import queue
import threading
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from common.core.config import settings
from typing import Optional

import jwt
import orjson
from jwt.exceptions import InvalidTokenError

from common.core import security


def generate_password_reset_token(email: str) -> str:
    delta = timedelta(hours=settings.EMAIL_RESET_TOKEN_EXPIRE_HOURS)
    now = datetime.now(timezone.utc)
    expires = now + delta
    exp = expires.timestamp()
    encoded_jwt = jwt.encode(
        {"exp": exp, "nbf": now, "sub": email},
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    return encoded_jwt


def verify_password_reset_token(token: str) -> str | None:
    try:
        decoded_token = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        return str(decoded_token["sub"])
    except InvalidTokenError:
        return None


def deepcopy_ignore_extra(src, dest):
    import copy
    for attr in vars(src):
        if hasattr(dest, attr):
            src_value = getattr(src, attr)
            dest_value = copy.deepcopy(src_value)  # deep copy
            setattr(dest, attr, dest_value)
    return dest


def extract_nested_json(text):
    stack = []
    start_index = -1
    results = []
    dict_results = []

    for i, char in enumerate(text):
        if char in '{[':
            if not stack:  # 记录起始位置
                start_index = i
            stack.append(char)
        elif char in '}]':
            if stack and ((char == '}' and stack[-1] == '{') or (char == ']' and stack[-1] == '[')):
                stack.pop()
                if not stack:  # 栈空时截取完整JSON
                    json_str = text[start_index:i + 1]
                    try:
                        parsed = orjson.loads(json_str)  # 验证有效性
                        results.append(json_str)
                        if isinstance(parsed, dict):
                            dict_results.append(json_str)
                    except:
                        pass
            else:
                stack = []  # 括号不匹配则重置
    # 优先返回 JSON 对象，避免误选到 [] 导致后续 data.get 报错
    if len(dict_results) > 0 and dict_results[0]:
        return dict_results[0]
    if len(results) > 0 and results[0]:
        return results[0]
    return None

def string_to_numeric_hash(text: str, bits: Optional[int] = 64) -> int:
    hash_bytes = hashlib.sha256(text.encode()).digest()
    hash_num = int.from_bytes(hash_bytes, byteorder='big')
    max_bigint = 2**63 - 1
    return hash_num % max_bigint

def get_access_token(request: Request):
    token_key = settings.TOKEN_KEY
    token = request.headers.get(token_key)
    if token.startswith("Bearer"):
        token = token.replace("Bearer", "")
    return token;

def setup_logging():
    # 确保日志目录存在
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 日志格式
    formatter = logging.Formatter(
        f'{settings.LOG_FORMAT}'
    )
    
    # 控制台日志
    console_handler = logging.StreamHandler()
    console_handler.setLevel(settings.LOG_LEVEL)
    console_handler.setFormatter(formatter)
    
    # 文件日志处理器
    file_handlers = {
        'debug': logging.DEBUG,
        'info': logging.INFO,
        'warn': logging.WARNING,
        'error': logging.ERROR
    }
    
    # 主日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 设置最低级别
    
    # 添加控制台处理器
    root_logger.addHandler(console_handler)
    
    # 为每个级别创建文件处理器
    for level_name, level in file_handlers.items():
        file_path = log_dir / f"{level_name}.log"
        handler = RotatingFileHandler(
            file_path,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        handler.setLevel(level)
        handler.setFormatter(formatter)
        
        # 添加过滤器只处理特定级别日志
        if level_name == 'debug':
            handler.addFilter(lambda record: record.levelno == logging.DEBUG)
        elif level_name == 'info':
            handler.addFilter(lambda record: record.levelno == logging.INFO)
        elif level_name == 'warn':
            handler.addFilter(lambda record: record.levelno == logging.WARNING)
        elif level_name == 'error':
            handler.addFilter(lambda record: record.levelno >= logging.ERROR)
        
        root_logger.addHandler(handler)
    
    # SQL日志特殊处理
    if settings.LOG_LEVEL == "DEBUG" and settings.SQL_DEBUG:
        sql_logger = logging.getLogger('sqlalchemy.engine')
        sql_logger.setLevel(logging.DEBUG)
        
        sql_handler = RotatingFileHandler(
            log_dir / "sql.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=2,
            encoding='utf-8'
        )
        sql_handler.setFormatter(formatter)
        sql_logger.addHandler(sql_handler)
        
setup_logging()


class CallerLogger(logging.Logger):
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        super().__init__(logger.name, logger.level)

    def _log(self, level, msg, args, exc_info=None, extra=None, stacklevel=3):
        if self.logger.isEnabledFor(level):
            self.logger._log(level, msg, args, exc_info=exc_info, extra=extra, stacklevel=stacklevel)

class SQLBotLogUtil:
    
    @staticmethod
    def _get_logger() -> logging.Logger:
        frame = inspect.currentframe()
        try:
            caller_frame = frame.f_back.f_back
            module_name = caller_frame.f_globals.get('__name__', '__main__')
            return CallerLogger(logging.getLogger(module_name))
        finally:
            del frame
        

    @staticmethod
    def debug(msg: str, *args, **kwargs):
        logger = SQLBotLogUtil._get_logger()
        if logger.isEnabledFor(logging.DEBUG):
            logger._log(logging.DEBUG, msg, args, **kwargs)

    @staticmethod
    def info(msg: str, *args, **kwargs):
        logger = SQLBotLogUtil._get_logger()
        if logger.isEnabledFor(logging.INFO):
            logger._log(logging.INFO, msg, args, **kwargs)

    @staticmethod
    def warning(msg: str, *args, **kwargs):
        logger = SQLBotLogUtil._get_logger()
        if logger.isEnabledFor(logging.WARNING):
            logger._log(logging.WARNING, msg, args, **kwargs)

    @staticmethod
    def error(msg: str, *args, exc_info: Optional[bool] = None, **kwargs):
        logger = SQLBotLogUtil._get_logger()
        if logger.isEnabledFor(logging.ERROR):
            logger._log(
                logging.ERROR, 
                msg, 
                args, 
                exc_info=exc_info if exc_info is not None else True,
                **kwargs
            )

    @staticmethod
    def exception(msg: str, *args, **kwargs):
        logger = SQLBotLogUtil._get_logger()
        if logger.isEnabledFor(logging.ERROR):
            logger._log(logging.ERROR, msg, args, exc_info=True, **kwargs)

    @staticmethod
    def critical(msg: str, *args, **kwargs):
        logger = SQLBotLogUtil._get_logger()
        if logger.isEnabledFor(logging.CRITICAL):
            logger._log(logging.CRITICAL, msg, args, **kwargs)


class AsyncBufferedLogUtil:
    """
    异步缓冲日志工具类
    使用队列和后台线程实现非阻塞日志记录，缓冲区大小为10
    """
    _instance = None
    _lock = threading.Lock()
    _buffer_size = 10
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(AsyncBufferedLogUtil, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._log_queue = queue.Queue(maxsize=self._buffer_size)
        self._worker_thread = None
        self._stop_event = threading.Event()
        self._initialized = True
        self._start_worker()
    
    def _start_worker(self):
        """启动后台日志处理线程"""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_event.clear()
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()
    
    def _worker_loop(self):
        """后台线程循环处理日志"""
        buffer = []
        while not self._stop_event.is_set():
            try:
                # 非阻塞获取日志项，超时时间0.1秒
                log_item = self._log_queue.get(timeout=0.1)
                buffer.append(log_item)
                
                # 当缓冲区满或队列为空时批量写入
                if len(buffer) >= self._buffer_size:
                    self._flush_buffer(buffer)
                    buffer = []
            except queue.Empty:
                # 超时后检查缓冲区，如果有内容则刷新
                if buffer:
                    self._flush_buffer(buffer)
                    buffer = []
            except Exception as e:
                # 错误处理：如果异步日志失败，降级到同步日志
                try:
                    logger = SQLBotLogUtil._get_logger()
                    logger.error(f"Async log worker error: {e}", exc_info=True)
                except:
                    pass
        
        # 线程退出前刷新剩余缓冲区
        if buffer:
            self._flush_buffer(buffer)
    
    def _flush_buffer(self, buffer: list):
        """批量刷新缓冲区中的日志"""
        for log_item in buffer:
            try:
                level, msg, args, kwargs = log_item
                logger = SQLBotLogUtil._get_logger()
                if logger.isEnabledFor(level):
                    logger._log(level, msg, args, **kwargs)
            except Exception as e:
                # 如果批量写入失败，尝试同步写入
                try:
                    logger = SQLBotLogUtil._get_logger()
                    logger.error(f"Failed to flush log buffer: {e}", exc_info=True)
                except:
                    pass
    
    def _log(self, level: int, msg: str, *args, **kwargs):
        """非阻塞添加日志到队列"""
        try:
            # 如果队列已满，尝试非阻塞put，失败则直接同步写入（避免丢失关键日志）
            self._log_queue.put_nowait((level, msg, args, kwargs))
        except queue.Full:
            # 队列满时，降级到同步日志（确保不丢失日志）
            try:
                logger = SQLBotLogUtil._get_logger()
                if logger.isEnabledFor(level):
                    logger._log(level, msg, args, **kwargs)
            except:
                pass
    
    def debug(self, msg: str, *args, **kwargs):
        """异步记录DEBUG级别日志"""
        self._log(logging.DEBUG, msg, *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs):
        """异步记录INFO级别日志"""
        self._log(logging.INFO, msg, *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs):
        """异步记录WARNING级别日志"""
        self._log(logging.WARNING, msg, *args, **kwargs)
    
    def error(self, msg: str, *args, exc_info: Optional[bool] = None, **kwargs):
        """异步记录ERROR级别日志"""
        if exc_info is not None:
            kwargs['exc_info'] = exc_info
        self._log(logging.ERROR, msg, *args, **kwargs)
    
    def exception(self, msg: str, *args, **kwargs):
        """异步记录异常日志"""
        kwargs['exc_info'] = True
        self._log(logging.ERROR, msg, *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs):
        """异步记录CRITICAL级别日志"""
        self._log(logging.CRITICAL, msg, *args, **kwargs)
    
    def flush(self):
        """刷新所有待处理的日志（同步等待）"""
        # 等待队列清空
        while not self._log_queue.empty():
            threading.Event().wait(0.01)
        # 给worker线程一点时间处理
        threading.Event().wait(0.1)
    
    def shutdown(self):
        """关闭日志处理器"""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)


# 创建全局异步日志实例
_async_log_util = AsyncBufferedLogUtil()


def prepare_for_orjson(data):
    if not data:
        return data
    if isinstance(data, bytes):
        return base64.b64encode(data).decode('utf-8')
    elif isinstance(data, dict):
        return {k: prepare_for_orjson(v) for k, v in data.items()}
    elif isinstance(data, (list, tuple)):
        return [prepare_for_orjson(item) for item in data]
    else:
        return data
        
    
def prepare_model_arg(origin_arg: str):
    if not isinstance(origin_arg, str):
        return origin_arg
    if not origin_arg.strip()[0] in {'{', '['}:
        return origin_arg
    try:
        return json.loads(origin_arg)
    except:
        return origin_arg
    
def get_origin_from_referer(request: Request):
    referer = request.headers.get("referer")
    if not referer:
        return None
    
    try:
        parsed = urlparse(referer)
        if not parsed.scheme or not parsed.hostname:
            return None
        port = parsed.port
        if port:
            if (parsed.scheme == "http" and port != 80) or \
               (parsed.scheme == "https" and port != 443):
                return f"{parsed.scheme}://{parsed.hostname}:{port}"
        
        return f"{parsed.scheme}://{parsed.hostname}"
    except Exception as e:
        SQLBotLogUtil.error(f"解析 Referer 出错: {e}")
        return referer

