import datetime
import json
import logging
import re
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import and_, text
from sqlbot_xpack.permissions.models.ds_rules import DsRules
from sqlmodel import select

from apps.datasource.crud.permission import get_column_permission_fields, get_row_permission_filters, is_normal_user
from apps.datasource.embedding.table_embedding import get_table_embedding
from apps.datasource.utils.utils import aes_decrypt
from apps.db.constant import DB
from apps.db.db import get_tables, get_fields, exec_sql, check_connection
from apps.db.engine import get_engine_config, get_engine_conn
from common.core.config import settings
from common.core.deps import SessionDep, CurrentUser, Trans
from common.utils.utils import deepcopy_ignore_extra
from .table import get_tables_by_ds_id
from ..crud.field import delete_field_by_ds_id, update_field
from ..crud.table import delete_table_by_ds_id, update_table
from ..models.datasource import CoreDatasource, CreateDatasource, CoreTable, CoreField, ColumnSchema, TableObj, \
    DatasourceConf, TableAndFields


def get_datasource_list(session: SessionDep, user: CurrentUser, oid: Optional[int] = None) -> List[CoreDatasource]:
    current_oid = user.oid if user.oid is not None else 1
    if user.isAdmin and oid:
        current_oid = oid
    
    # 管理员默认有所有数据源访问权限
    if user.isAdmin:
        return session.exec(
            select(CoreDatasource).where(CoreDatasource.oid == current_oid).order_by(CoreDatasource.name)).all()
    
    # 检查用户数据源访问权限
    from apps.system.models.system_model import UserWsModel, UserDatasourceModel
    user_ws = session.exec(
        select(UserWsModel).where(
            UserWsModel.uid == user.id,
            UserWsModel.oid == current_oid
        )
    ).first()
    
    if not user_ws:
        # 用户不在该工作空间，返回空列表
        return []
    
    # 如果用户是工作空间管理员（weight > 0），默认有所有数据源访问权限
    if user_ws.weight > 0:
        return session.exec(
            select(CoreDatasource).where(CoreDatasource.oid == current_oid).order_by(CoreDatasource.name)).all()
    
    # 普通成员：只返回有权限的数据源
    allowed_ds_ids = session.exec(
        select(UserDatasourceModel.ds_id).where(
            UserDatasourceModel.uid == user.id,
            UserDatasourceModel.oid == current_oid
        )
    ).all()
    
    if not allowed_ds_ids:
        return []
    
    return session.exec(
        select(CoreDatasource).where(
            CoreDatasource.oid == current_oid,
            CoreDatasource.id.in_(allowed_ds_ids)
        ).order_by(CoreDatasource.name)
    ).all()


def get_ds(session: SessionDep, id: int, current_user: Optional[CurrentUser] = None):
    statement = select(CoreDatasource).where(CoreDatasource.id == id)
    ds = session.exec(statement).first()
    
    # 如果提供了 current_user，检查数据源访问权限
    if ds and current_user and not current_user.isAdmin:
        from apps.system.models.system_model import UserWsModel, UserDatasourceModel
        user_ws = session.exec(
            select(UserWsModel).where(
                UserWsModel.uid == current_user.id,
                UserWsModel.oid == ds.oid
            )
        ).first()
        
        if not user_ws:
            # 用户不在该工作空间，返回 None
            return None
        
        # 如果用户是工作空间管理员（weight > 0），默认有权限
        if user_ws.weight > 0:
            return ds
        
        # 普通成员：检查是否有该数据源的访问权限
        user_ds = session.exec(
            select(UserDatasourceModel).where(
                UserDatasourceModel.uid == current_user.id,
                UserDatasourceModel.oid == ds.oid,
                UserDatasourceModel.ds_id == ds.id
            )
        ).first()
        
        if not user_ds:
            # 没有该数据源的访问权限，返回 None
            return None
    
    return ds


def check_status_by_id(session: SessionDep, trans: Trans, ds_id: int, is_raise: bool = False):
    ds = session.get(CoreDatasource, ds_id)
    if ds is None:
        if is_raise:
            raise HTTPException(status_code=500, detail=trans('i18n_ds_invalid'))
        return False
    return check_status(session, trans, ds, is_raise)


def check_status(session: SessionDep, trans: Trans, ds: CoreDatasource, is_raise: bool = False):
    return check_connection(trans, ds, is_raise)


def check_name(session: SessionDep, trans: Trans, user: CurrentUser, ds: CoreDatasource):
    if ds.id is not None:
        ds_list = session.query(CoreDatasource).filter(
            and_(CoreDatasource.name == ds.name, CoreDatasource.id != ds.id, CoreDatasource.oid == user.oid)).all()
        if ds_list is not None and len(ds_list) > 0:
            raise HTTPException(status_code=500, detail=trans('i18n_ds_name_exist'))
    else:
        ds_list = session.query(CoreDatasource).filter(
            and_(CoreDatasource.name == ds.name, CoreDatasource.oid == user.oid)).all()
        if ds_list is not None and len(ds_list) > 0:
            raise HTTPException(status_code=500, detail=trans('i18n_ds_name_exist'))


def create_ds(session: SessionDep, trans: Trans, user: CurrentUser, create_ds: CreateDatasource):
    ds = CoreDatasource()
    deepcopy_ignore_extra(create_ds, ds)
    check_name(session, trans, user, ds)
    ds.create_time = datetime.datetime.now()
    # status = check_status(session, ds)
    ds.create_by = user.id
    ds.oid = user.oid if user.oid is not None else 1
    ds.status = "Success"
    ds.type_name = DB.get_db(ds.type).db_name
    record = CoreDatasource(**ds.model_dump())
    session.add(record)
    session.flush()
    session.refresh(record)
    ds.id = record.id
    session.commit()

    # save tables and fields
    sync_table(session, ds, create_ds.tables)
    updateNum(session, ds)
    return ds


def chooseTables(session: SessionDep, trans: Trans, id: int, tables: List[CoreTable]):
    ds = session.query(CoreDatasource).filter(CoreDatasource.id == id).first()
    check_status(session, trans, ds, True)
    sync_table(session, ds, tables)
    updateNum(session, ds)


def update_ds(session: SessionDep, trans: Trans, user: CurrentUser, ds: CoreDatasource):
    ds.id = int(ds.id)
    check_name(session, trans, user, ds)
    # status = check_status(session, trans, ds)
    ds.status = "Success"
    record = session.exec(select(CoreDatasource).where(CoreDatasource.id == ds.id)).first()
    update_data = ds.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(record, field, value)
    session.add(record)
    session.commit()
    return ds


def delete_ds(session: SessionDep, id: int):
    term = session.exec(select(CoreDatasource).where(CoreDatasource.id == id)).first()
    if term.type == "excel":
        # drop all tables for current datasource
        engine = get_engine_conn()
        conf = DatasourceConf(**json.loads(aes_decrypt(term.configuration)))
        with engine.connect() as conn:
            for sheet in conf.sheets:
                conn.execute(text(f'DROP TABLE IF EXISTS "{sheet["tableName"]}"'))
            conn.commit()

    session.delete(term)
    session.commit()
    delete_table_by_ds_id(session, id)
    delete_field_by_ds_id(session, id)
    return {
        "message": f"Datasource with ID {id} deleted successfully."
    }


def getTables(session: SessionDep, id: int):
    ds = session.exec(select(CoreDatasource).where(CoreDatasource.id == id)).first()
    tables = get_tables(ds)
    return tables


def getTablesByDs(session: SessionDep, ds: CoreDatasource):
    # check_status(session, ds, True)
    tables = get_tables(ds)
    return tables


def getFields(session: SessionDep, id: int, table_name: str):
    ds = session.exec(select(CoreDatasource).where(CoreDatasource.id == id)).first()
    fields = get_fields(ds, table_name)
    return fields


def getFieldsByDs(session: SessionDep, ds: CoreDatasource, table_name: str):
    fields = get_fields(ds, table_name)
    return fields


def execSql(session: SessionDep, id: int, sql: str):
    ds = session.exec(select(CoreDatasource).where(CoreDatasource.id == id)).first()
    return exec_sql(ds, sql, True)


def sync_table(session: SessionDep, ds: CoreDatasource, tables: List[CoreTable]):
    id_list = []
    for item in tables:
        statement = select(CoreTable).where(and_(CoreTable.ds_id == ds.id, CoreTable.table_name == item.table_name))
        record = session.exec(statement).first()
        # update exist table, only update table_comment
        if record is not None:
            item.id = record.id
            id_list.append(record.id)

            record.table_comment = item.table_comment
            session.add(record)
            session.commit()
        else:
            # save new table
            table = CoreTable(ds_id=ds.id, checked=True, table_name=item.table_name, table_comment=item.table_comment,
                              custom_comment=item.table_comment)
            session.add(table)
            session.flush()
            session.refresh(table)
            item.id = table.id
            id_list.append(table.id)
            session.commit()

        # sync field
        fields = getFieldsByDs(session, ds, item.table_name)
        sync_fields(session, ds, item, fields)

    if len(id_list) > 0:
        session.query(CoreTable).filter(and_(CoreTable.ds_id == ds.id, CoreTable.id.not_in(id_list))).delete(
            synchronize_session=False)
        session.query(CoreField).filter(and_(CoreField.ds_id == ds.id, CoreField.table_id.not_in(id_list))).delete(
            synchronize_session=False)
        session.commit()
    else:  # delete all tables and fields in this ds
        session.query(CoreTable).filter(CoreTable.ds_id == ds.id).delete(synchronize_session=False)
        session.query(CoreField).filter(CoreField.ds_id == ds.id).delete(synchronize_session=False)
        session.commit()


def sync_fields(session: SessionDep, ds: CoreDatasource, table: CoreTable, fields: List[ColumnSchema]):
    id_list = []
    for index, item in enumerate(fields):
        statement = select(CoreField).where(
            and_(CoreField.table_id == table.id, CoreField.field_name == item.fieldName))
        record = session.exec(statement).first()
        if record is not None:
            item.id = record.id
            id_list.append(record.id)

            record.field_comment = item.fieldComment
            record.field_index = index
            record.field_type = item.fieldType
            session.add(record)
            session.commit()
        else:
            field = CoreField(ds_id=ds.id, table_id=table.id, checked=True, field_name=item.fieldName,
                              field_type=item.fieldType, field_comment=item.fieldComment,
                              custom_comment=item.fieldComment, field_index=index)
            session.add(field)
            session.flush()
            session.refresh(field)
            item.id = field.id
            id_list.append(field.id)
            session.commit()

    if len(id_list) > 0:
        session.query(CoreField).filter(and_(CoreField.table_id == table.id, CoreField.id.not_in(id_list))).delete(
            synchronize_session=False)
        session.commit()


def update_table_and_fields(session: SessionDep, data: TableObj):
    update_table(session, data.table)
    for field in data.fields:
        update_field(session, field)


def updateTable(session: SessionDep, table: CoreTable):
    update_table(session, table)


def updateField(session: SessionDep, field: CoreField):
    update_field(session, field)


def preview(session: SessionDep, current_user: CurrentUser, id: int, data: TableObj):
    ds = session.query(CoreDatasource).filter(CoreDatasource.id == id).first()
    # check_status(session, ds, True)

    if data.fields is None or len(data.fields) == 0:
        return {"fields": [], "data": [], "sql": ''}

    where = ''
    f_list = [f for f in data.fields if f.checked]
    if is_normal_user(current_user):
        # column is checked, and, column permission for data.fields
        contain_rules = session.query(DsRules).all()
        f_list = get_column_permission_fields(session=session, current_user=current_user, table=data.table,
                                              fields=f_list, contain_rules=contain_rules)

        # row permission tree
        where_str = ''
        filter_mapping = get_row_permission_filters(session=session, current_user=current_user, ds=ds, tables=None,
                                                    single_table=data.table)
        if filter_mapping:
            mapping_dict = filter_mapping[0]
            where_str = mapping_dict.get('filter')
        where = (' where ' + where_str) if where_str is not None and where_str != '' else ''

    fields = [f.field_name for f in f_list]
    if fields is None or len(fields) == 0:
        return {"fields": [], "data": [], "sql": ''}

    conf = DatasourceConf(**json.loads(aes_decrypt(ds.configuration))) if ds.type != "excel" else get_engine_config()
    sql: str = ""
    if ds.type == "mysql" or ds.type == "doris":
        sql = f"""SELECT `{"`, `".join(fields)}` FROM `{data.table.table_name}` 
            {where} 
            LIMIT 100"""
    elif ds.type == "sqlServer":
        sql = f"""SELECT TOP 100 [{"], [".join(fields)}] FROM [{conf.dbSchema}].[{data.table.table_name}]
            {where} 
            """
    elif ds.type == "pg" or ds.type == "excel" or ds.type == "redshift" or ds.type == "kingbase":
        sql = f"""SELECT "{'", "'.join(fields)}" FROM "{conf.dbSchema}"."{data.table.table_name}" 
            {where} 
            LIMIT 100"""
    elif ds.type == "oracle":
        sql = f"""SELECT "{'", "'.join(fields)}" FROM "{conf.dbSchema}"."{data.table.table_name}"
            {where} 
            ORDER BY "{fields[0]}"
            OFFSET 0 ROWS FETCH NEXT 100 ROWS ONLY"""
    elif ds.type == "ck":
        sql = f"""SELECT "{'", "'.join(fields)}" FROM "{data.table.table_name}" 
            {where} 
            LIMIT 100"""
    elif ds.type == "dm":
        sql = f"""SELECT "{'", "'.join(fields)}" FROM "{conf.dbSchema}"."{data.table.table_name}" 
            {where} 
            LIMIT 100"""
    elif ds.type == "es":
        sql = f"""SELECT "{'", "'.join(fields)}" FROM "{data.table.table_name}" 
            {where} 
            LIMIT 100"""
    return exec_sql(ds, sql, True)


def fieldEnum(session: SessionDep, id: int):
    field = session.query(CoreField).filter(CoreField.id == id).first()
    if field is None:
        return []
    table = session.query(CoreTable).filter(CoreTable.id == field.table_id).first()
    if table is None:
        return []
    ds = session.query(CoreDatasource).filter(CoreDatasource.id == table.ds_id).first()
    if ds is None:
        return []

    db = DB.get_db(ds.type)
    sql = f"""SELECT DISTINCT {db.prefix}{field.field_name}{db.suffix} FROM {db.prefix}{table.table_name}{db.suffix}"""
    res = exec_sql(ds, sql, True)
    return [item.get(res.get('fields')[0]) for item in res.get('data')]


def updateNum(session: SessionDep, ds: CoreDatasource):
    all_tables = get_tables(ds) if ds.type != 'excel' else json.loads(aes_decrypt(ds.configuration)).get('sheets')
    selected_tables = get_tables_by_ds_id(session, ds.id)
    num = f'{len(selected_tables)}/{len(all_tables)}'

    record = session.exec(select(CoreDatasource).where(CoreDatasource.id == ds.id)).first()
    update_data = ds.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(record, field, value)
    record.num = num
    session.add(record)
    session.commit()


def get_table_obj_by_ds(session: SessionDep, current_user: CurrentUser, ds: CoreDatasource) -> List[TableAndFields]:
    _list: List = []
    tables = session.query(CoreTable).filter(CoreTable.ds_id == ds.id).all()
    conf = DatasourceConf(**json.loads(aes_decrypt(ds.configuration))) if ds.type != "excel" else get_engine_config()
    schema = conf.dbSchema if conf.dbSchema is not None and conf.dbSchema != "" else conf.database

    # get all field
    table_ids = [table.id for table in tables]
    all_fields = session.query(CoreField).filter(
        and_(CoreField.table_id.in_(table_ids), CoreField.checked == True)).all()
    # build dict
    fields_dict = {}
    for field in all_fields:
        if fields_dict.get(field.table_id):
            fields_dict.get(field.table_id).append(field)
        else:
            fields_dict[field.table_id] = [field]

    contain_rules = session.query(DsRules).all()
    for table in tables:
        # fields = session.query(CoreField).filter(and_(CoreField.table_id == table.id, CoreField.checked == True)).all()
        fields = fields_dict.get(table.id)

        # do column permissions, filter fields
        fields = get_column_permission_fields(session=session, current_user=current_user, table=table, fields=fields,
                                              contain_rules=contain_rules)
        _list.append(TableAndFields(schema=schema, table=table, fields=fields))
    return _list


def _build_schema_table_str(
        obj: TableAndFields,
        ds: CoreDatasource,
        db_name: str,
        value_hints: Optional[dict[str, List[str]]] = None
) -> str:
    """根据 TableAndFields 构建单表 schema 字符串（含表备注、字段备注）。"""
    schema_table = ''
    schema_table += f"# Table: {db_name}.{obj.table.table_name}" if ds.type != "mysql" and ds.type != "es" else f"# Table: {obj.table.table_name}"
    table_comment = ''
    if obj.table.custom_comment:
        table_comment = obj.table.custom_comment.strip()
    if table_comment == '':
        schema_table += '\n[\n'
    else:
        schema_table += f", {table_comment}\n[\n"
    if obj.fields:
        field_list = []
        for field in obj.fields:
            field_comment = ''
            if field.custom_comment:
                field_comment = field.custom_comment.strip()
            hint_values = (value_hints or {}).get(field.field_name, [])
            hint_text = f" 值域示例: {' | '.join(hint_values)}" if hint_values else ""
            if field_comment == '':
                field_list.append(f"({field.field_name}:{field.field_type}{(', ' + hint_text) if hint_text else ''})")
            else:
                field_list.append(
                    f"({field.field_name}:{field.field_type}, {field_comment}{(',' + hint_text) if hint_text else ''})"
                )
        schema_table += ",\n".join(field_list)
    schema_table += '\n]\n'
    return schema_table


def _extract_question_keywords(question: str) -> List[str]:
    """提取问题中的中文短语与英文标识符，用于关键词召回相关表。"""
    if not question:
        return []
    tokens = re.findall(r'[\u4e00-\u9fa5]{2,}|[A-Za-z_][A-Za-z0-9_]{1,}|[0-9]{2,}', question)
    dedup = []
    seen = set()
    for token in tokens:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(token)
    return dedup


def _keyword_score_table(obj: TableAndFields, keywords: List[str]) -> int:
    table_name = (obj.table.table_name or "").lower()
    table_comment = (obj.table.custom_comment or obj.table.table_comment or "").lower()
    score = 0
    for kw in keywords:
        kw_l = kw.lower()
        if kw_l in table_name:
            score += 5
        if kw_l in table_comment:
            score += 4
        for field in obj.fields or []:
            field_name = (field.field_name or "").lower()
            field_comment = (field.custom_comment or field.field_comment or "").lower()
            if kw_l in field_name:
                score += 3
            if kw_l in field_comment:
                score += 2
    return score


def _is_guess_deep_field(field: CoreField) -> bool:
    """判断字段是否适合注入值域示例（深表常见分类字段）。"""
    ft = (field.field_type or "").lower()
    name = (field.field_name or "").lower()
    comment = (field.custom_comment or field.field_comment or "").lower()
    is_text_like = any(t in ft for t in ["char", "text", "string", "varchar"])
    if not is_text_like:
        return False
    hint_keys = [
        "type", "category", "status", "kind", "class", "level", "flag", "code",
        "类型", "类别", "状态", "科目", "业务", "标识", "方向", "指标名称","板块"
    ]
    return any(k in name or k in comment for k in hint_keys)


def _get_distinct_values_preview(ds: CoreDatasource, table_name: str, field_name: str, limit_count: int = 5) -> List[str]:
    """读取字段高频/枚举值样例（轻量 distinct 预览）。"""
    try:
        db = DB.get_db(ds.type)
        conf = DatasourceConf(**json.loads(aes_decrypt(ds.configuration))) if ds.type != "excel" else get_engine_config()
        schema = conf.dbSchema if conf.dbSchema else conf.database

        if ds.type in ["mysql", "doris"]:
            sql = f"""SELECT DISTINCT {db.prefix}{field_name}{db.suffix} AS v
                FROM {db.prefix}{table_name}{db.suffix}
                WHERE {db.prefix}{field_name}{db.suffix} IS NOT NULL
                LIMIT {limit_count}"""
        elif ds.type == "sqlServer":
            sql = f"""SELECT TOP {limit_count} [{field_name}] AS v
                FROM [{schema}].[{table_name}]
                WHERE [{field_name}] IS NOT NULL
                GROUP BY [{field_name}]"""
        elif ds.type in ["pg", "excel", "redshift", "kingbase", "dm"]:
            sql = f"""SELECT DISTINCT "{field_name}" AS v
                FROM "{schema}"."{table_name}"
                WHERE "{field_name}" IS NOT NULL
                LIMIT {limit_count}"""
        elif ds.type == "oracle":
            sql = f"""SELECT * FROM (
                SELECT DISTINCT "{field_name}" AS v
                FROM "{schema}"."{table_name}"
                WHERE "{field_name}" IS NOT NULL
            ) WHERE ROWNUM <= {limit_count}"""
        elif ds.type in ["ck", "es"]:
            sql = f"""SELECT DISTINCT "{field_name}" AS v
                FROM "{table_name}"
                WHERE "{field_name}" IS NOT NULL
                LIMIT {limit_count}"""
        else:
            return []

        res = exec_sql(ds, sql, True)
        data = res.get("data") if isinstance(res, dict) else None
        if not data:
            return []

        values = []
        for row in data:
            if not isinstance(row, dict):
                continue
            value = row.get("v")
            if value is None and row:
                # 兜底：某些数据库返回键名不是 v
                value = next(iter(row.values()))
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str and value_str not in values:
                values.append(value_str)
            if len(values) >= limit_count:
                break
        return values
    except Exception:
        return []


def _build_guess_value_hints(ds: CoreDatasource, obj: TableAndFields) -> dict[str, List[str]]:
    value_hints: dict[str, List[str]] = {}
    top_k = max(1, int(settings.GUESS_SCHEMA_VALUE_HINT_TOPK))
    checked_fields = obj.fields or []
    sampled_fields = 0
    for field in checked_fields:
        if sampled_fields >= 8:
            break
        if not _is_guess_deep_field(field):
            continue
        values = _get_distinct_values_preview(ds, obj.table.table_name, field.field_name, limit_count=top_k)
        if len(values) >= 2:
            value_hints[field.field_name] = values
            sampled_fields += 1
    return value_hints


def get_table_schema_for_guess(session: SessionDep, current_user: CurrentUser, ds: CoreDatasource, question: str) -> str:
    """
    猜你想问专用 schema 构建：
    1) 先做 schema pruning（embedding 或关键词）筛到 3-5 张相关表；
    2) 对深表字段注入值域示例，提升推荐问题可查性。
    """
    table_objs = get_table_obj_by_ds(session=session, current_user=current_user, ds=ds)
    if len(table_objs) == 0:
        return ""

    db_name = table_objs[0].schema
    if len(table_objs) >= 3:
        configured_keep = int(settings.GUESS_SCHEMA_TABLE_COUNT or 5)
        configured_keep = max(3, min(5, configured_keep))
        keep_count = min(configured_keep, len(table_objs))
    else:
        keep_count = len(table_objs)

    table_candidates = []
    table_obj_map = {}
    for obj in table_objs:
        schema_table = _build_schema_table_str(obj, ds, db_name)
        table_candidates.append({"id": obj.table.id, "schema_table": schema_table})
        table_obj_map[obj.table.id] = obj

    selected_ids: List[int] = []
    table_logger = logging.getLogger(__name__)
    if settings.TABLE_EMBEDDING_ENABLED and question and question.strip():
        try:
            embedded = get_table_embedding(session, current_user, table_candidates, question)
            selected_ids = [int(t.get("id")) for t in (embedded or []) if t.get("id") is not None][:keep_count]
            table_logger.info(
                f"[猜你想问-schema pruning] embedding 命中表数量={len(selected_ids)}, keep_count={keep_count}"
            )
        except Exception:
            table_logger.warning("[猜你想问-schema pruning] embedding 检索失败，回退关键词检索", exc_info=True)

    if not selected_ids:
        keywords = _extract_question_keywords(question or "")
        scored = []
        for obj in table_objs:
            score = _keyword_score_table(obj, keywords)
            scored.append((obj.table.id, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        selected_ids = [int(tid) for tid, _ in scored[:keep_count]]
        table_logger.info(
            f"[猜你想问-schema pruning] 关键词命中表数量={len(selected_ids)}, keep_count={keep_count}, keywords={keywords}"
        )

    selected_objs = [table_obj_map[tid] for tid in selected_ids if tid in table_obj_map]
    if not selected_objs:
        selected_objs = table_objs[:keep_count]

    schema_str = f"【DB_ID】 {db_name}\n【Schema】\n"
    include_hints = bool(settings.GUESS_SCHEMA_INCLUDE_VALUE_HINTS)
    for obj in selected_objs:
        value_hints = _build_guess_value_hints(ds, obj) if include_hints else {}
        schema_str += _build_schema_table_str(obj, ds, db_name, value_hints=value_hints)

    # 仅保留已筛选表之间的外键关系，避免把无关表带回 prompt
    if selected_objs and ds.table_relation:
        selected_ids_set = {obj.table.id for obj in selected_objs}
        relations = list(filter(lambda x: x.get('shape') == 'edge', ds.table_relation))
        rel_selected = list(filter(
            lambda x: x.get('source', {}).get('cell') in selected_ids_set and x.get('target', {}).get('cell') in selected_ids_set,
            relations
        ))
        if rel_selected:
            table_records = session.query(CoreTable).filter(CoreTable.id.in_(list(selected_ids_set))).all()
            table_dict = {ele.id: ele.table_name for ele in table_records}
            relation_field_ids = []
            for relation in rel_selected:
                relation_field_ids.append(relation.get('source', {}).get('port'))
                relation_field_ids.append(relation.get('target', {}).get('port'))
            relation_field_ids = [int(fid) for fid in relation_field_ids if fid is not None]
            if relation_field_ids:
                field_records = session.query(CoreField).filter(CoreField.id.in_(relation_field_ids)).all()
                field_dict = {ele.id: ele.field_name for ele in field_records}
                schema_str += '【Foreign keys】\n'
                for ele in rel_selected:
                    s_cell = int(ele.get('source', {}).get('cell'))
                    t_cell = int(ele.get('target', {}).get('cell'))
                    s_port = int(ele.get('source', {}).get('port'))
                    t_port = int(ele.get('target', {}).get('port'))
                    schema_str += (
                        f"{table_dict.get(s_cell)}.{field_dict.get(s_port)}="
                        f"{table_dict.get(t_cell)}.{field_dict.get(t_port)}\n"
                    )

    table_logger.info(
        f"[猜你想问-schema pruning] 最终注入表数量={len(selected_objs)}, include_value_hints={include_hints}"
    )
    return schema_str


def get_table_schema_for_tables(session: SessionDep, current_user: CurrentUser, ds: CoreDatasource,
                                 table_names: List[str]) -> str:
    """
    仅返回指定表名的表结构（含表备注、字段备注），用于追问时补全历史SQL涉及表的字段定义。
    table_names: 表名列表，支持带 schema 前缀（如 "default.dws_xxx"），会按最后一段匹配。
    """
    if not table_names:
        return ""
    table_objs = get_table_obj_by_ds(session=session, current_user=current_user, ds=ds)
    if not table_objs:
        return ""
    db_name = table_objs[0].schema
    # 标准化：只保留最后一段表名用于匹配
    normalized_want = set()
    for t in table_names:
        t = (t or "").strip().strip('"').strip("'")
        if not t:
            continue
        if "." in t:
            t = t.split(".")[-1]
        normalized_want.add(t.lower())
    schema_str = ""
    for obj in table_objs:
        name = (obj.table.table_name or "").lower()
        if name in normalized_want:
            schema_str += _build_schema_table_str(obj, ds, db_name)
    return schema_str


def get_table_schema(session: SessionDep, current_user: CurrentUser, ds: CoreDatasource, question: str,
                     embedding: bool = True) -> str:
    schema_str = ""
    table_objs = get_table_obj_by_ds(session=session, current_user=current_user, ds=ds)
    if len(table_objs) == 0:
        return schema_str
    db_name = table_objs[0].schema
    schema_str += f"【DB_ID】 {db_name}\n【Schema】\n"
    tables = []
    all_tables = []  # temp save all tables
    for obj in table_objs:
        schema_table = ''
        schema_table += f"# Table: {db_name}.{obj.table.table_name}" if ds.type != "mysql" and ds.type != "es" else f"# Table: {obj.table.table_name}"
        table_comment = ''
        if obj.table.custom_comment:
            table_comment = obj.table.custom_comment.strip()
        if table_comment == '':
            schema_table += '\n[\n'
        else:
            schema_table += f", {table_comment}\n[\n"

        if obj.fields:
            field_list = []
            for field in obj.fields:
                field_comment = ''
                if field.custom_comment:
                    field_comment = field.custom_comment.strip()
                if field_comment == '':
                    field_list.append(f"({field.field_name}:{field.field_type})")
                else:
                    field_list.append(f"({field.field_name}:{field.field_type}, {field_comment})")
            schema_table += ",\n".join(field_list)
        schema_table += '\n]\n'

        t_obj = {"id": obj.table.id, "schema_table": schema_table}
        tables.append(t_obj)
        all_tables.append(t_obj)

    # do table embedding
    original_table_count = len(tables) if tables else 0
    if embedding and tables and settings.TABLE_EMBEDDING_ENABLED:
        import logging
        table_logger = logging.getLogger(__name__)
        table_logger.info(f"[表结构检索] 表Embedding检索已启用，开始筛选相关表 - 用户问题: {question[:100]}, 原始表数量: {original_table_count}")
        tables = get_table_embedding(session, current_user, tables, question)
        filtered_table_count = len(tables) if tables else 0
        table_logger.info(f"[表结构检索] 表Embedding筛选完成，筛选后表数量: {filtered_table_count}, 最大返回数量: {settings.TABLE_EMBEDDING_COUNT}")
    else:
        import logging
        table_logger = logging.getLogger(__name__)
        if not embedding:
            table_logger.info(f"[表结构检索] 表Embedding检索未启用 (embedding参数=False)")
        elif not settings.TABLE_EMBEDDING_ENABLED:
            table_logger.info(f"[表结构检索] 表Embedding检索未启用 (TABLE_EMBEDDING_ENABLED={settings.TABLE_EMBEDDING_ENABLED})，返回所有表结构，表数量: {original_table_count}")
        else:
            table_logger.info(f"[表结构检索] 表列表为空，无法进行Embedding筛选")
    # splice schema
    if tables:
        for s in tables:
            schema_str += s.get('schema_table')

    # field relation
    if tables and ds.table_relation:
        relations = list(filter(lambda x: x.get('shape') == 'edge', ds.table_relation))
        if relations:
            # Complete the missing table
            # get tables in relation, remove irrelevant relation
            embedding_table_ids = [s.get('id') for s in tables]
            all_relations = list(
                filter(lambda x: x.get('source').get('cell') in embedding_table_ids or x.get('target').get(
                    'cell') in embedding_table_ids, relations))

            # get relation table ids, sub embedding table ids
            relation_table_ids = []
            for r in all_relations:
                relation_table_ids.append(r.get('source').get('cell'))
                relation_table_ids.append(r.get('target').get('cell'))
            relation_table_ids = list(set(relation_table_ids))
            # get table dict
            table_records = session.query(CoreTable).filter(CoreTable.id.in_(list(map(int, relation_table_ids)))).all()
            table_dict = {}
            for ele in table_records:
                table_dict[ele.id] = ele.table_name

            # get lost table ids
            lost_table_ids = list(set(relation_table_ids) - set(embedding_table_ids))
            # get lost table schema and splice it
            lost_tables = list(filter(lambda x: x.get('id') in lost_table_ids, all_tables))
            if lost_tables:
                for s in lost_tables:
                    schema_str += s.get('schema_table')

            # get field dict
            relation_field_ids = []
            for relation in all_relations:
                relation_field_ids.append(relation.get('source').get('port'))
                relation_field_ids.append(relation.get('target').get('port'))
            relation_field_ids = list(set(relation_field_ids))
            field_records = session.query(CoreField).filter(CoreField.id.in_(list(map(int, relation_field_ids)))).all()
            field_dict = {}
            for ele in field_records:
                field_dict[ele.id] = ele.field_name

            if all_relations:
                schema_str += '【Foreign keys】\n'
                for ele in all_relations:
                    schema_str += f"{table_dict.get(int(ele.get('source').get('cell')))}.{field_dict.get(int(ele.get('source').get('port')))}={table_dict.get(int(ele.get('target').get('cell')))}.{field_dict.get(int(ele.get('target').get('port')))}\n"

    return schema_str
