"""assist: model-call telemetry table

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-24 12:00:00.000000

Hand-written. One new table, no backfill: `assist_call` holds one row per provider call (chat, judge,
insights) with latency, outcome and token counts, never any question or answer text.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assist_call',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='1', nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=True),
        sa.Column('prompt_version', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=16), server_default='live', nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assist_call_workspace_id'), 'assist_call', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_assist_call_created_at'), 'assist_call', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_assist_call_created_at'), table_name='assist_call')
    op.drop_index(op.f('ix_assist_call_workspace_id'), table_name='assist_call')
    op.drop_table('assist_call')
