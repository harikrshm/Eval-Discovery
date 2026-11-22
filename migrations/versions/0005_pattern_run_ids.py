"""assist: remember which runs the stored pattern came from

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23 12:00:00.000000

Hand-written. One nullable JSON column on `agent`, no backfill: existing agents (and every general
agent) keep NULL, which means "no pattern runs recorded".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agent', sa.Column('pattern_run_ids', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('agent') as batch:
        batch.drop_column('pattern_run_ids')
