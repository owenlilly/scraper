"""add the run queue columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 21:40:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The one-active-run rule is new. Anything left queued or running belongs to a process that no longer exists.
    op.execute("UPDATE pipeline.run SET status = 'failed', finished_at = now(), error = 'abandoned before the run queue existed' WHERE status IN ('queued', 'running')")
    op.add_column('run', sa.Column('kind', sa.Text(), server_default='live', nullable=False), schema='pipeline')
    op.add_column('run', sa.Column('retry_of', sa.BigInteger(), nullable=True), schema='pipeline')
    op.add_column('run', sa.Column('requested_by', sa.Text(), nullable=True), schema='pipeline')
    op.add_column('run', sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True), schema='pipeline')
    op.add_column('run', sa.Column('cancel_requested', sa.Boolean(), server_default=sa.text('false'), nullable=False), schema='pipeline')
    op.add_column('run', sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False), schema='pipeline')
    op.create_foreign_key(op.f('fk_run_retry_of_run'), 'run', 'run', ['retry_of'], ['id'], source_schema='pipeline', referent_schema='pipeline')
    op.create_index('uq_run_source_id_active', 'run', ['source_id'], unique=True, schema='pipeline', postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index('uq_run_source_id_active', table_name='run', schema='pipeline')
    op.drop_constraint(op.f('fk_run_retry_of_run'), 'run', schema='pipeline', type_='foreignkey')
    for column in ('options', 'cancel_requested', 'heartbeat_at', 'requested_by', 'retry_of', 'kind'):
        op.drop_column('run', column, schema='pipeline')
