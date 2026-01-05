"""049_add_user_orgcode

添加用户部门编码字段

Revision ID: 20251202002
Revises: 20251202001
Create Date: 2025-12-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
# 使用工具函数（推荐）
from common.utils.alembic_helpers import column_exists

# revision identifiers, used by Alembic.
revision = '20251202002'
down_revision = '20251202001'  # 048_add_user_orgname 的 revision
branch_labels = None
depends_on = None


def upgrade():
    """
    升级：添加字段（如果不存在）
    Alembic 本身会保证这个函数只执行一次（通过版本号跟踪）
    但为了更安全，我们可以在函数内部再次检查字段是否存在
    """
    connection = op.get_bind()

    table_name = 'sys_user'
    column_name = 'userorg'  # 替换为你要添加的字段名

    # 检查字段是否已存在
    if column_exists(connection, table_name, column_name):
        print(f"字段 {table_name}.{column_name} 已存在，跳过添加")
        return

    # 字段不存在，执行添加
    print(f"正在添加字段 {table_name}.{column_name}...")
    op.add_column(
        table_name,
        sa.Column(
            column_name,
            sa.String(255),  # 字段类型
            nullable=True,   # 是否允许为空
            server_default=sa.text("''"),  # 默认值
            comment='部门编码'  # 字段注释（PostgreSQL 支持）
        )
    )
    print(f"✓ 成功添加字段 {table_name}.{column_name}")


def downgrade():
    """
    降级：删除字段（如果存在）
    """
    connection = op.get_bind()
    table_name = 'sys_user'
    column_name = 'userorg'
    
    # 检查字段是否存在
    if not column_exists(connection, table_name, column_name):
        print(f"字段 {table_name}.{column_name} 不存在，跳过删除")
        return
    
    # 字段存在，执行删除
    print(f"正在删除字段 {table_name}.{column_name}...")
    op.drop_column(table_name, column_name)
    print(f"✓ 成功删除字段 {table_name}.{column_name}")

