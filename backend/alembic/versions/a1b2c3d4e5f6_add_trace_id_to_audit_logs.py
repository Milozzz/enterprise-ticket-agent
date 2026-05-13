"""add trace_id duration_ms success to audit_logs

Revision ID: a1b2c3d4e5f6
Revises: 0a9de19714cd
Create Date: 2026-04-11 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '0a9de19714cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('trace_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('duration_ms', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('success', sa.Boolean(), nullable=True))
        batch_op.create_index('ix_audit_logs_trace_id', ['trace_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('audit_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_logs_trace_id')
        batch_op.drop_column('success')
        batch_op.drop_column('duration_ms')
        batch_op.drop_column('trace_id')
