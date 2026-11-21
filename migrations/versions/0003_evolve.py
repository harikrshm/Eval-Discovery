"""evolve: candidates, generated tests, criterion revision

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21 20:00:00.000000

Hand-written. The live database already holds rows, so every new NOT NULL column carries a server
default (`test_case.origin` = 'run', `criterion.revision` = 0). The unique constraint on
`(prompt_version.agent_id, prompt_version.number)` is creatable over existing data because each
agent's versions already have distinct numbers (Phase 2 assigns max + 1; Phase 1 rows are all 1 and
an agent has exactly one). Constraint changes go through batch_alter_table so SQLite can apply them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prompt_version', sa.Column('change_report', sa.JSON(), nullable=True))
    op.add_column('prompt_version', sa.Column('verification', sa.JSON(), nullable=True))
    op.add_column('prompt_version', sa.Column('source_test_id', sa.String(length=36), nullable=True))
    op.add_column(
        'test_case',
        sa.Column('origin', sa.String(length=16), server_default='run', nullable=False),
    )
    op.add_column(
        'criterion',
        sa.Column('revision', sa.Integer(), server_default='0', nullable=False),
    )
    with op.batch_alter_table('prompt_version') as batch:
        batch.create_unique_constraint('uq_prompt_version_agent_number', ['agent_id', 'number'])


def downgrade() -> None:
    with op.batch_alter_table('prompt_version') as batch:
        batch.drop_constraint('uq_prompt_version_agent_number', type_='unique')
    with op.batch_alter_table('criterion') as batch:
        batch.drop_column('revision')
    with op.batch_alter_table('test_case') as batch:
        batch.drop_column('origin')
    with op.batch_alter_table('prompt_version') as batch:
        batch.drop_column('source_test_id')
        batch.drop_column('verification')
        batch.drop_column('change_report')
