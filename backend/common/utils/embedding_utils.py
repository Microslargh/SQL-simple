"""Embedding 向量维度对齐：本地 768 维模型写入 4096 维库表时自动填充，便于灵活切换本地/API 模型。"""

from typing import List


def ensure_embedding_dimension(vec: List[float], target_dim: int = None) -> List[float]:
    """
    将向量对齐到库表维度：不足则补 0，超出则截断。
    本地 768 维模型在 4096 维库表下会自动填充，无需改表。
    """
    if target_dim is None:
        from common.core.config import settings
        target_dim = getattr(settings, "EMBEDDING_VECTOR_DIMENSION", 4096)
    if not vec:
        return vec
    n = len(vec)
    if n == target_dim:
        return list(vec)
    if n < target_dim:
        return list(vec) + [0.0] * (target_dim - n)
    return list(vec)[:target_dim]
