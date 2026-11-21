"""tests and jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21 18:00:00.000000

Hand-written. `prompt_version.number` is NOT NULL with a server default of 1 because the live
database already holds Phase 1 rows (every existing prompt version is version 1).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('criterion', sa.Column('judge_check', sa.JSON(), nullable=True))
    op.add_column(
        'prompt_version',
        sa.Column('number', sa.Integer(), server_default='1', nullable=False),
    )
    op.create_table('test_case',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('criterion_id', sa.String(length=36), nullable=False),
    sa.Column('source_run_id', sa.String(length=36), nullable=True),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('scripted_user_messages', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['criterion_id'], ['criterion.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('criterion_id', 'role', name='uq_test_case_criterion_role')
    )
    op.create_index(op.f('ix_test_case_criterion_id'), 'test_case', ['criterion_id'], unique=False)
    op.create_table('test_result',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('test_id', sa.String(length=36), nullable=False),
    sa.Column('prompt_version_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('verdict', sa.String(length=16), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('transcript', sa.JSON(), nullable=True),
    sa.Column('stale', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['test_id'], ['test_case.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_test_result_test_id'), 'test_result', ['test_id'], unique=False)
    op.create_table('job',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspace.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_status'), 'job', ['status'], unique=False)
    op.create_index(op.f('ix_job_workspace_id'), 'job', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_job_workspace_id'), table_name='job')
    op.drop_index(op.f('ix_job_status'), table_name='job')
    op.drop_table('job')
    op.drop_index(op.f('ix_test_result_test_id'), table_name='test_result')
    op.drop_table('test_result')
    op.drop_index(op.f('ix_test_case_criterion_id'), table_name='test_case')
    op.drop_table('test_case')
    with op.batch_alter_table('prompt_version') as batch:
        batch.drop_column('number')
    with op.batch_alter_table('criterion') as batch:
        batch.drop_column('judge_check')
