import datetime
import logging
from typing import List, Optional
from xml.dom.minidom import parseString

import dicttoxml
from sqlalchemy import and_, select, func, delete, update, or_
from sqlalchemy import text
from sqlalchemy.orm.session import Session
from common.utils.utils import SQLBotLogUtil
from apps.ai_model.embedding import EmbeddingModelCache
from apps.data_training.models.data_training_model import DataTrainingInfo, DataTraining
from apps.datasource.models.datasource import CoreDatasource
from apps.template.generate_chart.generator import get_base_data_training_template
from common.core.config import settings
from common.core.deps import SessionDep, Trans
from common.utils.embedding_threads import run_save_data_training_embeddings
from common.utils.embedding_utils import ensure_embedding_dimension

logger = logging.getLogger(__name__)


def page_data_training(session: SessionDep, current_page: int = 1, page_size: int = 10, name: Optional[str] = None,
                       oid: Optional[int] = 1):
    _list: List[DataTrainingInfo] = []

    current_page = max(1, current_page)
    page_size = max(10, page_size)

    total_count = 0
    total_pages = 0

    if name and name.strip() != "":
        keyword_pattern = f"%{name.strip()}%"
        # 全局搜索：问题、SQL模板、模板提示、描述、表名
        parent_ids_subquery = (
            select(DataTraining.id)
            .where(
                and_(
                    DataTraining.oid == oid,
                    or_(
                        DataTraining.question.ilike(keyword_pattern),
                        DataTraining.sql_template.ilike(keyword_pattern),
                        DataTraining.template_prompt.ilike(keyword_pattern),
                        DataTraining.description.ilike(keyword_pattern),
                        DataTraining.tables.ilike(keyword_pattern),
                    )
                )
            )
        )
    else:
        parent_ids_subquery = (
            select(DataTraining.id).where(and_(DataTraining.oid == oid))
        )

    count_stmt = select(func.count()).select_from(parent_ids_subquery.subquery())
    total_count = session.execute(count_stmt).scalar()
    total_pages = (total_count + page_size - 1) // page_size

    if current_page > total_pages:
        current_page = 1

    paginated_parent_ids = (
        parent_ids_subquery
        .order_by(DataTraining.create_time.desc())
        .offset((current_page - 1) * page_size)
        .limit(page_size)
        .subquery()
    )

    stmt = (
        select(
            DataTraining.id,
            DataTraining.oid,
            DataTraining.datasource,
            CoreDatasource.name,
            DataTraining.question,
            DataTraining.create_time,
            DataTraining.description,
            DataTraining.sql_template,
            DataTraining.template_k,
            DataTraining.tables,
            DataTraining.template_prompt,
        )
        .outerjoin(CoreDatasource, and_(DataTraining.datasource == CoreDatasource.id))
        .where(and_(DataTraining.id.in_(paginated_parent_ids)))
        .order_by(DataTraining.create_time.desc())
    )

    result = session.execute(stmt)

    for row in result:
        _list.append(DataTrainingInfo(
            id=row.id,
            oid=row.oid,
            datasource=row.datasource,
            datasource_name=row.name,
            question=row.question,
            create_time=row.create_time,
            description=row.description,
            sql_template=row.sql_template,
            template_k=row.template_k,
            tables=row.tables,
            template_prompt=row.template_prompt,
        ))

    return current_page, page_size, total_count, total_pages, _list


def create_training(session: SessionDep, info: DataTrainingInfo, oid: int, trans: Trans):
    create_time = datetime.datetime.now()
    if info.datasource is None:
        raise Exception(trans("i18n_data_training.datasource_cannot_be_none"))
    parent = DataTraining(
        question=info.question,
        create_time=create_time,
        description=info.description,
        oid=oid,
        datasource=info.datasource,
        sql_template=info.sql_template,
        template_k=info.template_k,
        tables=info.tables,
        template_prompt=info.template_prompt
    )

    exists = session.query(
        session.query(DataTraining).filter(
            and_(DataTraining.question == info.question, DataTraining.oid == oid,
                 DataTraining.datasource == info.datasource)).exists()).scalar()
    if exists:
        raise Exception(trans("i18n_data_training.exists_in_db"))

    result = DataTraining(**parent.model_dump())

    session.add(parent)
    session.flush()
    session.refresh(parent)

    result.id = parent.id
    session.commit()

    # embedding
    run_save_data_training_embeddings([result.id])

    return result.id


def update_training(session: SessionDep, info: DataTrainingInfo, oid: int, trans: Trans):
    if info.datasource is None:
        raise Exception(trans("i18n_data_training.datasource_cannot_be_none"))

    count = session.query(DataTraining).filter(
        DataTraining.id == info.id
    ).count()
    if count == 0:
        raise Exception(trans('i18n_data_training.data_training_not_exists'))

    exists = session.query(
        session.query(DataTraining).filter(
            and_(DataTraining.question == info.question, DataTraining.oid == oid,
                 DataTraining.datasource == info.datasource,
                 DataTraining.id != info.id)).exists()).scalar()
    if exists:
        raise Exception(trans("i18n_data_training.exists_in_db"))

    stmt = update(DataTraining).where(and_(DataTraining.id == info.id)).values(
        question=info.question,
        description=info.description,
        datasource=info.datasource,
        sql_template=info.sql_template,
        template_k=info.template_k,
        tables=info.tables,
        template_prompt=info.template_prompt,
    )
    session.execute(stmt)
    session.commit()

    # embedding
    run_save_data_training_embeddings([info.id])

    return info.id


def delete_training(session: SessionDep, ids: list[int]):
    stmt = delete(DataTraining).where(and_(DataTraining.id.in_(ids)))
    session.execute(stmt)
    session.commit()


# def run_save_embeddings(ids: List[int]):
#     executor.submit(save_embeddings, ids)
#
#
# def fill_empty_embeddings():
#     executor.submit(run_fill_empty_embeddings)


def run_fill_empty_embeddings(session: Session):
    if not settings.EMBEDDING_ENABLED:
        return

    stmt = select(DataTraining.id).where(and_(DataTraining.embedding.is_(None)))
    results = session.execute(stmt).scalars().all()

    save_embeddings(session, results)


def save_embeddings(session: Session, ids: List[int]):
    if not settings.EMBEDDING_ENABLED:
        return

    if not ids or len(ids) == 0:
        return
    try:

        _list = session.query(DataTraining).filter(and_(DataTraining.id.in_(ids))).all()

        _question_list = [item.question for item in _list]

        model = EmbeddingModelCache.get_model()

        results = model.embed_documents(_question_list)

        for index in range(len(results)):
            item = ensure_embedding_dimension(results[index])
            stmt = update(DataTraining).where(and_(DataTraining.id == _list[index].id)).values(embedding=item)
            session.execute(stmt)
            session.commit()

    except Exception:
        SQLBotLogUtil.exception("Failed to update data training embeddings")


embedding_sql = f"""
SELECT id, datasource, question, similarity
FROM
(SELECT id, datasource, question, oid,
( 1 - (embedding <=> :embedding_array) ) AS similarity
FROM data_training AS child
) TEMP
WHERE similarity > {settings.EMBEDDING_DATA_TRAINING_SIMILARITY} and oid = :oid and datasource = :datasource
ORDER BY similarity DESC
LIMIT {settings.EMBEDDING_DATA_TRAINING_TOP_COUNT}
"""


def select_training_by_question(session: SessionDep, question: str, oid: int, datasource: int):
    if question.strip() == "":
        return []

    _list: List[DataTraining] = []

    # 首先尝试精确匹配（完全相同的文本）
    exact_stmt = (
        select(
            DataTraining.id,
            DataTraining.question,
        )
        .where(
            and_(
                DataTraining.question == question,
                DataTraining.oid == oid,
                DataTraining.datasource == datasource
            )
        )
    )
    exact_results = session.execute(exact_stmt).fetchall()
    exact_match_count = len(exact_results)
    logger.info(f"[数据训练检索] 精确匹配结果数量: {exact_match_count}, 用户问题: {question[:100]}")
    
    for row in exact_results:
        _list.append(DataTraining(id=row.id, question=row.question))

    # 然后使用模糊匹配（如果精确匹配没有结果，或者需要更多结果）
    if exact_match_count == 0 or len(_list) < 5:
        # 使用参数化查询，避免SQL注入和参数绑定问题
        fuzzy_stmt = (
            select(
                DataTraining.id,
                DataTraining.question,
            )
            .where(
                and_(
                    or_(
                        DataTraining.question.ilike(f'%{question}%'),
                        text(f"question ILIKE '%' || :sentence || '%'")
                    ),
                    DataTraining.oid == oid,
                    DataTraining.datasource == datasource
                )
            )
        )
        fuzzy_results = session.execute(fuzzy_stmt, {'sentence': question}).fetchall()
        fuzzy_match_count = len(fuzzy_results)
        logger.info(f"[数据训练检索] 模糊匹配结果数量: {fuzzy_match_count}, 用户问题: {question[:100]}")
        
        for row in fuzzy_results:
            # 避免重复添加（精确匹配已经添加的）
            if not any(item.id == row.id for item in _list):
                _list.append(DataTraining(id=row.id, question=row.question))
    
    text_match_count = len(_list)
    fuzzy_match_added = fuzzy_match_count - exact_match_count if exact_match_count > 0 else (fuzzy_match_count if exact_match_count == 0 else 0)
    logger.info(f"[数据训练检索] 文本匹配总结果数量: {text_match_count} (精确匹配: {exact_match_count}, 模糊匹配新增: {fuzzy_match_added})")

    embedding_match_count = 0
    if settings.EMBEDDING_ENABLED:
        try:
            logger.info(f"[数据训练检索] Embedding检索已启用，开始使用embedding模型检索 - 用户问题: {question[:100]}")
            model = EmbeddingModelCache.get_model()
            logger.info(f"[数据训练检索] Embedding模型已加载: {type(model).__name__}")

            embedding = model.embed_query(question)
            logger.info(f"[数据训练检索] 问题embedding向量已生成，向量维度: {len(embedding)}")
            embedding = ensure_embedding_dimension(embedding)

            results = session.execute(text(embedding_sql),
                                      {'embedding_array': str(embedding), 'oid': oid, 'datasource': datasource})

            # 记录embedding检索的详细结果
            embedding_results_with_similarity = []
            for row in results:
                similarity = getattr(row, 'similarity', None)
                embedding_results_with_similarity.append({
                    'id': row.id,
                    'question': row.question if hasattr(row, 'question') else None,
                    'similarity': similarity
                })
                # 避免重复添加（文本匹配已经添加的）
                if not any(item.id == row.id for item in _list):
                    _list.append(DataTraining(id=row.id, question=row.question if hasattr(row, 'question') else ''))
                    embedding_match_count += 1
            
            # 记录相似度详情
            if embedding_results_with_similarity:
                top_similarities = [f"ID={r['id']}, 相似度={r['similarity']:.4f}" for r in embedding_results_with_similarity[:5] if r['similarity'] is not None]
                logger.info(f"[数据训练检索] Embedding检索完成，匹配到 {len(embedding_results_with_similarity)} 条结果（去重后新增: {embedding_match_count}），相似度阈值: {settings.EMBEDDING_DATA_TRAINING_SIMILARITY}, 最大返回数量: {settings.EMBEDDING_DATA_TRAINING_TOP_COUNT}")
                if top_similarities:
                    logger.info(f"[数据训练检索] Embedding检索前5个结果相似度: {', '.join(top_similarities)}")
            else:
                logger.info(f"[数据训练检索] Embedding检索完成，未匹配到任何结果（相似度阈值: {settings.EMBEDDING_DATA_TRAINING_SIMILARITY}，可能阈值过高）")

        except Exception as e:
            logger.error(f"[数据训练检索] Embedding检索失败: {str(e)}", exc_info=True)
    else:
        logger.info(f"[数据训练检索] Embedding检索未启用 (EMBEDDING_ENABLED={settings.EMBEDDING_ENABLED})，仅使用文本匹配")

    _map: dict = {}
    _ids: list[int] = []
    for row in _list:
        if row.id in _ids:
            continue
        else:
            _ids.append(row.id)

    if len(_ids) == 0:
        return []

    t_list = session.query(DataTraining.id, DataTraining.datasource, DataTraining.question,
                           DataTraining.description, DataTraining.sql_template, DataTraining.template_k, DataTraining.template_prompt, DataTraining.tables).filter(
        and_(DataTraining.id.in_(_ids))).all()

    for row in t_list:
        _map[row.id] = {
            'id': row.id,
            'question': row.question,
            'suggestion-answer': row.description,
            'sql-template': row.sql_template,
            'sql-info': row.template_k,
            'template-prompt': row.template_prompt,
            'tables': row.tables,
        }

    _results: list[dict] = []
    for key in _map.keys():
        _results.append(_map.get(key))

    logger.info(f"[数据训练检索] 最终返回 {len(_results)} 条SQL示例 (文本匹配: {text_match_count}, Embedding匹配: {embedding_match_count})")
    if _results:
        result_ids = [r.get('id') for r in _results[:5]]
        logger.info(f"[数据训练检索] 前5个示例ID: {result_ids}")

    return _results


def to_xml_string(_dict: list[dict] | dict, root: str = 'sql-examples', need_template=None) -> str:

    dicttoxml.LOG.setLevel(logging.ERROR)
    if not need_template:
        item_name_func = lambda x: 'sql-example' if x == 'sql-examples' else 'item'
        xml = dicttoxml.dicttoxml(_dict,
                                  cdata=['question', 'suggestion-answer'],
                                  custom_root=root,
                                  item_func=item_name_func,
                                  xml_declaration=False,
                                  encoding='utf-8',
                                  attr_type=False).decode('utf-8')
    else:
        item_name_func = lambda x: 'sql-info-template'
        xml = dicttoxml.dicttoxml(_dict,
                                  cdata=[
                                      'id',
                                      'question',
                                      'sql-template',
                                      'sql-info',
                                      'template-prompt',
                                      'tables'
                                  ],
                                  custom_root="sql-info-templates",
                                  item_func=item_name_func,
                                  xml_declaration=False,
                                  encoding='utf-8',
                                  attr_type=False).decode('utf-8')
    pretty_xml = parseString(xml).toprettyxml()

    if pretty_xml.startswith('<?xml'):
        end_index = pretty_xml.find('>') + 1
        pretty_xml = pretty_xml[end_index:].lstrip()

    # 替换所有 XML 转义字符
    escape_map = {
        '&lt;': '<',
        '&gt;': '>',
        '&amp;': '&',
        '&quot;': '"',
        '&apos;': "'"
    }
    for escaped, original in escape_map.items():
        pretty_xml = pretty_xml.replace(escaped, original)

    return pretty_xml


def get_training_template(session: SessionDep, question: str, datasource: int, oid: Optional[int] = 1) -> str:
    if not oid:
        oid = 1
    if not datasource:
        return ''
    _results = select_training_by_question(session, question, oid, datasource)
    if _results and len(_results) > 0:
        data_training = to_xml_string(_results)
        template = get_base_data_training_template().format(data_training=data_training)
        return template
    else:
        return ''


def get_training_template_with_data(session: SessionDep, question: str, datasource: int, oid: Optional[int] = 1) -> tuple[str, str, List[dict]]:
    """
    获取训练数据模板和原始数据
    Returns:
        tuple: (template_string, training_data_list)
    """
    if not oid:
        oid = 1
    if not datasource:
        return '', '', []
    _results = select_training_by_question(session, question, oid, datasource)
    if _results and len(_results) > 0:
        data_training = to_xml_string(_results)
        sql_info_templates = to_xml_string(_results, need_template=True)
        template = get_base_data_training_template().format(data_training=data_training)
        return template, sql_info_templates,_results
    else:
        return '', '', [],
