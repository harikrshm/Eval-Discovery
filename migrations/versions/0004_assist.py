"""assist: assistant agent kind, flagged-run provenance, signal tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-22 12:00:00.000000

Hand-written. The live database already holds rows, so every new NOT NULL column carries a server
default (`agent.kind` = 'general', `run.source` = 'manual'). The unique constraint on
`(run.agent_id, run.session_id)` is creatable over existing data because every existing run has a
NULL session id and NULLs are distinct on both SQLite and Postgres. Constraint changes go through
batch_alter_table so SQLite can apply them. The new tables hang off `workspace` with ON DELETE CASCADE.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'agent',
        sa.Column('kind', sa.String(length=16), server_default='general', nullable=False),
    )
    op.add_column(
        'run',
        sa.Column('source', sa.String(length=32), server_default='manual', nullable=False),
    )
    op.add_column('run', sa.Column('session_id', sa.String(length=64), nullable=True))
    op.add_column('run', sa.Column('brief_snapshot', sa.JSON(), nullable=True))
    op.add_column('test_case', sa.Column('brief_snapshot', sa.JSON(), nullable=True))
    with op.batch_alter_table('run') as batch:
        batch.create_unique_constraint('uq_run_agent_session', ['agent_id', 'session_id'])

    op.create_table(
        'assist_page_view',
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('patent_number', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('dwell_ms', sa.Integer(), nullable=False),
        sa.Column('sections', sa.JSON(), nullable=False),
        sa.Column('bounce', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('assist_version', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id'),
    )
    op.create_index(
        op.f('ix_assist_page_view_workspace_id'), 'assist_page_view', ['workspace_id'], unique=False
    )
    op.create_table(
        'assist_question',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('workspace_id', sa.String(length=36), nullable=False),
        sa.Column('patent_number', sa.String(length=64), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('intent', sa.String(length=32), nullable=False),
        sa.Column('section_focus', sa.String(length=64), nullable=True),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('rating', sa.String(length=16), nullable=True),
        sa.Column('rating_reason', sa.Text(), nullable=True),
        sa.Column('reasked', sa.Boolean(), nullable=False),
        sa.Column('citation_ok', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('assist_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assist_question_session_id'), 'assist_question', ['session_id'], unique=False)
    op.create_index(op.f('ix_assist_question_workspace_id'), 'assist_question', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_assist_question_workspace_id'), table_name='assist_question')
    op.drop_index(op.f('ix_assist_question_session_id'), table_name='assist_question')
    op.drop_table('assist_question')
    op.drop_index(op.f('ix_assist_page_view_workspace_id'), table_name='assist_page_view')
    op.drop_table('assist_page_view')
    with op.batch_alter_table('run') as batch:
        batch.drop_constraint('uq_run_agent_session', type_='unique')
    with op.batch_alter_table('test_case') as batch:
        batch.drop_column('brief_snapshot')
    with op.batch_alter_table('run') as batch:
        batch.drop_column('brief_snapshot')
        batch.drop_column('session_id')
        batch.drop_column('source')
    with op.batch_alter_table('agent') as batch:
        batch.drop_column('kind')
