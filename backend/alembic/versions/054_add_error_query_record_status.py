"""054_add_error_query_record_status

为 error_query_record 增加 status 列（待解决/已解决）。

Revision ID: 20251203003
Revises: 20251203002
Create Date: 2025-12-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from common.utils.alembic_helpers import column_exists

revision = "20251203003"
down_revision = "20251203002"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if not column_exists(conn, "error_query_record", "status"):
        op.add_column("error_query_record", sa.Column("status", sa.Text(), nullable=True, server_default="pending"))


def downgrade():
    op.drop_column("error_query_record", "status")
