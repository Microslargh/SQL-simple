"""055_add_error_query_record_analysis_text

反馈为「分析过程有误」时记录数据分析文本，新增 analysis_text 列。

Revision ID: 20251203004
Revises: 20251203003
Create Date: 2025-12-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from common.utils.alembic_helpers import column_exists

revision = "20251203004"
down_revision = "20251203003"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if not column_exists(conn, "error_query_record", "analysis_text"):
        op.add_column("error_query_record", sa.Column("analysis_text", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("error_query_record", "analysis_text")
