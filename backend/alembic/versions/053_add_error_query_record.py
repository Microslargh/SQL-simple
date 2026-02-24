"""053_add_error_query_record

用户点踩反馈写入错误查询记录表，供系统管理-错误查询记录页展示。

Revision ID: 20251203002
Revises: 20251203001
Create Date: 2025-12-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import BIGINT
from common.utils.alembic_helpers import table_exists

revision = "20251203002"
down_revision = "20251203001"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if table_exists(conn, "error_query_record"):
        return
    op.create_table(
        "error_query_record",
        sa.Column("id", BIGINT, primary_key=True, autoincrement=True),
        sa.Column("record_id", BIGINT, nullable=False),
        sa.Column("chat_id", BIGINT, nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("sql", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("feedback_reason", sa.Text(), nullable=True),
        sa.Column("create_by", BIGINT, nullable=True),
        sa.Column("create_time", sa.DateTime(timezone=False), nullable=True),
    )
    op.create_index("idx_error_query_record_create_time", "error_query_record", ["create_time"], unique=False)


def downgrade():
    op.drop_table("error_query_record")
