"""057_add_chat_record_feedback

点赞/点踩统一写入 chat_record_feedback，便于评测与统计。

Revision ID: 20260401001
Revises: 20260316001
"""

from alembic import op
import sqlalchemy as sa
from common.utils.alembic_helpers import table_exists

revision = "20260401001"
down_revision = "20260316001"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if table_exists(conn, "chat_record_feedback"):
        return
    op.create_table(
        "chat_record_feedback",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("create_by", sa.BigInteger(), nullable=True),
        sa.Column("is_like", sa.Boolean(), nullable=False),
        sa.Column("feedback_reason", sa.Text(), nullable=True),
        sa.Column("create_time", sa.DateTime(timezone=False), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_chat_record_feedback_record_id",
        "chat_record_feedback",
        ["record_id"],
        unique=False,
    )
    op.create_index(
        "idx_chat_record_feedback_create_time",
        "chat_record_feedback",
        ["create_time"],
        unique=False,
    )


def downgrade():
    op.drop_table("chat_record_feedback")
