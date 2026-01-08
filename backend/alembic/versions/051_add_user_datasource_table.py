"""051_add_user_datasource_table

创建用户-数据源关联表

Revision ID: 20251202004
Revises: 20251202003
Create Date: 2025-12-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BIGINT
from common.utils.alembic_helpers import table_exists, column_exists

# revision identifiers, used by Alembic.
revision = '20251202004'
down_revision = '20251202003'  # 050_add_user_ws_datasource_access 的 revision
branch_labels = None
depends_on = None


def upgrade():
    """
    升级：创建用户-数据源关联表
    """
    connection = op.get_bind()
    table_name = 'sys_user_datasource'
    
    # 检查表是否已存在
    if table_exists(connection, table_name):
        print(f"表 {table_name} 已存在，跳过创建")
        return
    
    # 创建表
    print(f"正在创建表 {table_name}...")
    op.create_table(
        table_name,
        sa.Column('id', BIGINT, primary_key=True, autoincrement=True),
        sa.Column('uid', BIGINT, nullable=False, comment='用户ID'),
        sa.Column('oid', BIGINT, nullable=False, comment='工作空间ID'),
        sa.Column('ds_id', BIGINT, nullable=False, comment='数据源ID'),
        sa.Column('create_time', BIGINT, nullable=True, server_default='0', comment='创建时间'),
        sa.UniqueConstraint('uid', 'oid', 'ds_id', name='uq_user_workspace_datasource'),
        sa.Index('idx_user_workspace', 'uid', 'oid'),
        sa.Index('idx_datasource', 'ds_id'),
    )
    print(f"✓ 成功创建表 {table_name}")


def downgrade():
    """
    降级：删除表（如果存在）
    """
    connection = op.get_bind()
    table_name = 'sys_user_datasource'
    
    # 检查表是否存在
    if not table_exists(connection, table_name):
        print(f"表 {table_name} 不存在，跳过删除")
        return
    
    # 表存在，执行删除
    print(f"正在删除表 {table_name}...")
    op.drop_table(table_name)
    print(f"✓ 成功删除表 {table_name}")


