"""assist: conversation judgements and the auto-judge flag

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-24 14:00:00.000000

Hand-written. One new table, `assist_judgement` (one verdict per workspace, session and prompt version:
labels, flags and ids only, never question or answer text), and one new column,
`assist_question.judge_flagged`, which defaults to false for every existing row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'assist_judgement',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('prompt_version', sa.Integer(), nullable=False),
        sa.Column('rubric_version', sa.String(length=16), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('answered', sa.String(length=16), nullable=False),
        sa.Column('concise', sa.Boolean(), nullable=False),
        sa.Column('citations_sensible', sa.Boolean(), nullable=False),
        sa.Column('legal', sa.String(length=16), nullable=False),
        sa.Column('grade', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('worst_question_id', sa.String(length=36), nullable=True),
        sa.Column('flagged', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(length=16), server_default='live', nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'session_id', 'prompt_version', name='uq_assist_judgement_session_version'),
    )
    op.create_index(op.f('ix_assist_judgement_workspace_id'), 'assist_judgement', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_assist_judgement_session_id'), 'assist_judgement', ['session_id'], unique=False)
    op.add_column(
        'assist_question',
        sa.Column('judge_flagged', sa.Boolean(), server_default='0', nullable=False),
    )


def downgrade() -> None:
    with op.batch_alter_table('assist_question') as batch:
        batch.drop_column('judge_flagged')
    op.drop_index(op.f('ix_assist_judgement_session_id'), table_name='assist_judgement')
    op.drop_index(op.f('ix_assist_judgement_workspace_id'), table_name='assist_judgement')
    op.drop_table('assist_judgement')
