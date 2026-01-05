"""
Alembic 迁移辅助函数
提供在迁移中检查字段、表、索引等是否存在的工具函数
"""
from sqlalchemy import inspect
from typing import Optional


def column_exists(connection, table_name: str, column_name: str) -> bool:
    """
    检查表中是否存在指定字段
    
    Args:
        connection: 数据库连接（通过 op.get_bind() 获取）
        table_name: 表名
        column_name: 字段名
    
    Returns:
        bool: 字段是否存在
    
    Example:
        from alembic import op
        from common.utils.alembic_helpers import column_exists
        
        def upgrade():
            connection = op.get_bind()
            if not column_exists(connection, 'sys_user', 'phone'):
                op.add_column('sys_user', sa.Column('phone', sa.String(20)))
    """
    try:
        inspector = inspect(connection)
        
        # 检查表是否存在
        if table_name not in inspector.get_table_names():
            return False
        
        # 获取表的所有列
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        
        return column_name in columns
    except Exception as e:
        # 如果检查失败，返回 False（保守策略）
        print(f"检查字段时出错: {str(e)}")
        return False


def table_exists(connection, table_name: str) -> bool:
    """
    检查表是否存在
    
    Args:
        connection: 数据库连接
        table_name: 表名
    
    Returns:
        bool: 表是否存在
    
    Example:
        def upgrade():
            connection = op.get_bind()
            if not table_exists(connection, 'new_table'):
                op.create_table('new_table', ...)
    """
    try:
        inspector = inspect(connection)
        return table_name in inspector.get_table_names()
    except Exception as e:
        print(f"检查表时出错: {str(e)}")
        return False


def index_exists(connection, table_name: str, index_name: str) -> bool:
    """
    检查索引是否存在
    
    Args:
        connection: 数据库连接
        table_name: 表名
        index_name: 索引名
    
    Returns:
        bool: 索引是否存在
    
    Example:
        def upgrade():
            connection = op.get_bind()
            if not index_exists(connection, 'sys_user', 'ix_sys_user_phone'):
                op.create_index('ix_sys_user_phone', 'sys_user', ['phone'])
    """
    try:
        inspector = inspect(connection)
        
        if table_name not in inspector.get_table_names():
            return False
        
        indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
        return index_name in indexes
    except Exception as e:
        print(f"检查索引时出错: {str(e)}")
        return False


def constraint_exists(connection, table_name: str, constraint_name: str) -> bool:
    """
    检查约束是否存在（如外键、唯一约束等）
    
    Args:
        connection: 数据库连接
        table_name: 表名
        constraint_name: 约束名
    
    Returns:
        bool: 约束是否存在
    """
    try:
        inspector = inspect(connection)
        
        if table_name not in inspector.get_table_names():
            return False
        
        # 检查外键约束
        foreign_keys = [fk['name'] for fk in inspector.get_foreign_keys(table_name)]
        if constraint_name in foreign_keys:
            return True
        
        # 检查唯一约束
        unique_constraints = [uc['name'] for uc in inspector.get_unique_constraints(table_name)]
        if constraint_name in unique_constraints:
            return True
        
        # 检查检查约束（PostgreSQL）
        try:
            check_constraints = [cc['name'] for cc in inspector.get_check_constraints(table_name)]
            if constraint_name in check_constraints:
                return True
        except AttributeError:
            # 某些数据库可能不支持 get_check_constraints
            pass
        
        return False
    except Exception as e:
        print(f"检查约束时出错: {str(e)}")
        return False


def get_table_columns(connection, table_name: str) -> list[str]:
    """
    获取表的所有字段名
    
    Args:
        connection: 数据库连接
        table_name: 表名
    
    Returns:
        list[str]: 字段名列表
    """
    try:
        inspector = inspect(connection)
        
        if table_name not in inspector.get_table_names():
            return []
        
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        return columns
    except Exception as e:
        print(f"获取字段列表时出错: {str(e)}")
        return []

