# Author: Junjun
# Date: 2025/9/23
import json
import time
import logging

from apps.ai_model.embedding import EmbeddingModelCache
from apps.datasource.embedding.utils import cosine_similarity
from common.core.config import settings
from common.core.deps import SessionDep, CurrentUser
from common.utils.utils import SQLBotLogUtil


def get_table_embedding(session: SessionDep, current_user: CurrentUser, tables: list[dict], question: str):
    _list = []
    original_count = len(tables)
    SQLBotLogUtil.info(f"[表Embedding检索] 开始表Embedding检索，原始表数量: {original_count}, 用户问题: {question[:100]}")
    
    for table in tables:
        _list.append({"id": table.get('id'), "schema_table": table.get('schema_table'), "cosine_similarity": 0.0})

    if _list:
        try:
            text = [s.get('schema_table') for s in _list]
            SQLBotLogUtil.info(f"[表Embedding检索] 加载Embedding模型...")

            model = EmbeddingModelCache.get_model()
            SQLBotLogUtil.info(f"[表Embedding检索] Embedding模型已加载: {type(model).__name__}")
            
            start_time = time.time()
            results = model.embed_documents(text)
            end_time = time.time()
            SQLBotLogUtil.info(f"[表Embedding检索] 表结构embedding生成完成，耗时: {end_time - start_time:.2f}秒，表数量: {len(results)}")

            q_embedding = model.embed_query(question)
            SQLBotLogUtil.info(f"[表Embedding检索] 问题embedding向量已生成，向量维度: {len(q_embedding)}")
            
            for index in range(len(results)):
                item = results[index]
                similarity = cosine_similarity(q_embedding, item)
                _list[index]['cosine_similarity'] = similarity

            _list.sort(key=lambda x: x['cosine_similarity'], reverse=True)
            highest_sim = _list[0]['cosine_similarity'] if _list else 0.0
            lowest_sim = _list[-1]['cosine_similarity'] if _list else 0.0
            SQLBotLogUtil.info(f"[表Embedding检索] 相似度排序完成，最高相似度: {highest_sim:.4f}, 最低相似度: {lowest_sim:.4f}")
            
            _list = _list[:settings.TABLE_EMBEDDING_COUNT]
            SQLBotLogUtil.info(f"[表Embedding检索] 筛选完成，返回前 {len(_list)} 个表 (TABLE_EMBEDDING_COUNT={settings.TABLE_EMBEDDING_COUNT})")
            
            # 记录前3个表的相似度
            if _list:
                top_similarities = [f"表ID={t['id']}, 相似度={t['cosine_similarity']:.4f}" for t in _list[:3]]
                SQLBotLogUtil.info(f"[表Embedding检索] 前3个表的相似度: {', '.join(top_similarities)}")
            
            return _list
        except Exception as e:
            logging.getLogger(__name__).error(f"[表Embedding检索] 表Embedding检索失败: {str(e)}", exc_info=True)
            SQLBotLogUtil.error(f"[表Embedding检索] 表Embedding检索失败，返回原始表列表")
    else:
        SQLBotLogUtil.info(f"[表Embedding检索] 表列表为空，跳过Embedding检索")
    return _list
