"""052_embedding_vector_4096

将 terminology、data_training 的 embedding 列改为 4096 维（适配 Qwen3-embedding-8B 等 API 模型）。
执行后原有向量会清空，需在系统中重新触发「术语/训练数据」的 embedding 填充或手动编辑保存以重新生成。

Revision ID: 20251203001
Revises: 20251202004
Create Date: 2025-12-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy.vector

from common.utils.alembic_helpers import column_exists

# revision identifiers, used by Alembic.
revision = '20251203001'
down_revision = '20251202004'
branch_labels = None
depends_on = None

VECTOR_4096 = pgvector.sqlalchemy.vector.VECTOR(4096)


def upgrade():
    conn = op.get_bind()
    # terminology: 先删列再加列，使维度从不定/768 改为 4096（数据会清空，需后续重算）
    op.drop_column('terminology', 'embedding')
    op.add_column('terminology', sa.Column('embedding', VECTOR_4096, nullable=True))

    op.drop_column('data_training', 'embedding')
    op.add_column('data_training', sa.Column('embedding', VECTOR_4096, nullable=True))

    # 若 core_table 存在 embedding 列（表结构 embedding 功能），一并改为 4096 维
    if column_exists(conn, 'core_table', 'embedding'):
        op.drop_column('core_table', 'embedding')
        op.add_column('core_table', sa.Column('embedding', VECTOR_4096, nullable=True))


def downgrade():
    conn = op.get_bind()
    op.drop_column('terminology', 'embedding')
    op.add_column('terminology', sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), nullable=True))
    op.drop_column('data_training', 'embedding')
    op.add_column('data_training', sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), nullable=True))

    if column_exists(conn, 'core_table', 'embedding'):
        op.drop_column('core_table', 'embedding')
        op.add_column('core_table', sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), nullable=True))
