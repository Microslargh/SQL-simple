"""056_add_chat_execution_trace

新增问数执行轨迹表，用于记录问题从输入到输出的关键节点输入/输出/错误信息。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from common.utils.alembic_helpers import table_exists, index_exists

revision = "20260316001"
down_revision = "20251203004"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if not table_exists(conn, "chat_execution_trace"):
        op.create_table(
            "chat_execution_trace",
            sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
            sa.Column("record_id", sa.BigInteger(), nullable=False),
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            sa.Column("create_by", sa.BigInteger(), nullable=True),
            sa.Column("trace_group", sa.String(length=64), nullable=False),
            sa.Column("node_key", sa.String(length=64), nullable=False),
            sa.Column("node_name", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("extra_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("start_time", sa.DateTime(), nullable=True),
            sa.Column("finish_time", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not index_exists(conn, "chat_execution_trace", "ix_chat_execution_trace_record_id"):
        op.create_index("ix_chat_execution_trace_record_id", "chat_execution_trace", ["record_id"])

    if not index_exists(conn, "chat_execution_trace", "ix_chat_execution_trace_trace_group"):
        op.create_index("ix_chat_execution_trace_trace_group", "chat_execution_trace", ["trace_group"])

    if not index_exists(conn, "chat_execution_trace", "ix_chat_execution_trace_chat_id"):
        op.create_index("ix_chat_execution_trace_chat_id", "chat_execution_trace", ["chat_id"])


def downgrade():
    conn = op.get_bind()
    if index_exists(conn, "chat_execution_trace", "ix_chat_execution_trace_chat_id"):
        op.drop_index("ix_chat_execution_trace_chat_id", table_name="chat_execution_trace")
    if index_exists(conn, "chat_execution_trace", "ix_chat_execution_trace_trace_group"):
        op.drop_index("ix_chat_execution_trace_trace_group", table_name="chat_execution_trace")
    if index_exists(conn, "chat_execution_trace", "ix_chat_execution_trace_record_id"):
        op.drop_index("ix_chat_execution_trace_record_id", table_name="chat_execution_trace")
    if table_exists(conn, "chat_execution_trace"):
        op.drop_table("chat_execution_trace")
