import os.path
import threading
from typing import List, Optional

import requests
from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings
from pydantic import BaseModel

from common.core.config import settings

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# 当使用 API 嵌入（如 Qwen3-embedding-8B）时的向量维度，需与库表 VECTOR 维度一致
EMBEDDING_API_VECTOR_DIMENSION = 4096


class EmbeddingModelInfo(BaseModel):
    folder: str
    name: str
    device: str = 'cpu'


local_embedding_model = EmbeddingModelInfo(folder=settings.LOCAL_MODEL_PATH,
                                           name=os.path.join(settings.LOCAL_MODEL_PATH, 'embedding',
                                                             "shibing624_text2vec-base-chinese"))

_lock = threading.Lock()
locks = {}

_embedding_model: dict[str, Optional[Embeddings]] = {}


def _use_embedding_api() -> bool:
    """是否使用 API 嵌入（配置了 EMBEDDING_API_BASE_URL 时使用）。"""
    base = getattr(settings, 'EMBEDDING_API_BASE_URL', None) or ''
    return bool(base and str(base).strip())


def _api_embedding_cache_key() -> str:
    return f"api:{getattr(settings, 'EMBEDDING_API_BASE_URL', '')}:{getattr(settings, 'EMBEDDING_API_MODEL', '')}"


class _HttpEmbeddings(Embeddings):
    """仅通过 HTTP 调用 OpenAI 兼容的 embedding 接口，不依赖 OpenAI SDK，避免容器内访问外网（如 tiktoken）。"""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60.0):
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.api_key = (api_key or '').strip()
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post(self, payload: dict) -> dict:
        url = f"{self.base_url}/embeddings"
        r = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        payload = {"input": texts, "model": self.model}
        data = self._post(payload)
        out = []
        for item in sorted(data.get("data", []), key=lambda x: x.get("index", 0)):
            out.append(item["embedding"])
        return out

    def embed_query(self, text: str) -> List[float]:
        payload = {"input": text, "model": self.model}
        data = self._post(payload)
        for item in data.get("data", []):
            return item["embedding"]
        raise ValueError("embedding API returned no data")


class EmbeddingModelCache:

    @staticmethod
    def _new_instance_api() -> Embeddings:
        """使用 OpenAI 兼容的 Embedding API（如 Qwen3-embedding-8B）。纯 HTTP 调用，不依赖 OpenAI SDK，避免访问外网。"""
        base_url = (settings.EMBEDDING_API_BASE_URL or '').strip().rstrip('/')
        model = (settings.EMBEDDING_API_MODEL or 'qwen3-embedding-8b').strip()
        api_key = (settings.EMBEDDING_API_KEY or '').strip()
        return _HttpEmbeddings(base_url=base_url, model=model, api_key=api_key)

    @staticmethod
    def _new_instance(config: EmbeddingModelInfo = local_embedding_model) -> Embeddings:
        return HuggingFaceEmbeddings(model_name=config.name, cache_folder=config.folder,
                                     model_kwargs={'device': config.device},
                                     encode_kwargs={'normalize_embeddings': True}
                                     )

    @staticmethod
    def _get_lock(key: str) -> threading.Lock:
        lock = locks.get(key)
        if lock is None:
            with _lock:
                lock = locks.get(key)
                if lock is None:
                    lock = threading.Lock()
                    locks[key] = lock
        return lock

    @staticmethod
    def get_model(key: str | None = None,
                  config: EmbeddingModelInfo | None = None) -> Embeddings:
        if key is None:
            key = _api_embedding_cache_key() if _use_embedding_api() else settings.DEFAULT_EMBEDDING_MODEL
        if config is None:
            config = local_embedding_model

        model_instance = _embedding_model.get(key)
        if model_instance is None:
            lock = EmbeddingModelCache._get_lock(key)
            with lock:
                model_instance = _embedding_model.get(key)
                if model_instance is None:
                    if _use_embedding_api():
                        model_instance = EmbeddingModelCache._new_instance_api()
                    else:
                        model_instance = EmbeddingModelCache._new_instance(config)
                    _embedding_model[key] = model_instance

        return model_instance
