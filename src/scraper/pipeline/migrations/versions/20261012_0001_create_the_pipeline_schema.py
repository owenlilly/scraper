"""create the pipeline schema

Revision ID: 0001
Revises:
Create Date: 2026-10-12 00:31:32.716500
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('source',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('schedule', sa.Text(), nullable=True),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source')),
    sa.UniqueConstraint('name', name=op.f('uq_source_name')),
    schema='pipeline'
    )
    op.create_table('record',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_id', sa.BigInteger(), nullable=False),
    sa.Column('natural_key', sa.Text(), nullable=False),
    sa.Column('current_version_id', sa.BigInteger(), nullable=True),
    sa.Column('first_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('gone_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['pipeline.source.id'], name=op.f('fk_record_source_id_source')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_record')),
    sa.UniqueConstraint('source_id', 'natural_key', name='uq_record_source_id_natural_key'),
    schema='pipeline'
    )
    op.create_table('run',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('source_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Text(), server_default='queued', nullable=False),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('worker_id', sa.Text(), nullable=True),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['source_id'], ['pipeline.source.id'], name=op.f('fk_run_source_id_source')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run')),
    schema='pipeline'
    )
    op.create_index('ix_run_source_id_id', 'run', ['source_id', 'id'], unique=False, schema='pipeline')
    op.create_table('fetch',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('final_url', sa.Text(), nullable=False),
    sa.Column('status', sa.Integer(), nullable=False),
    sa.Column('headers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('fetcher', sa.Text(), nullable=False),
    sa.Column('blob_sha256', sa.String(length=64), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline.run.id'], name=op.f('fk_fetch_run_id_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fetch')),
    schema='pipeline'
    )
    op.create_index('ix_fetch_blob_sha256', 'fetch', ['blob_sha256'], unique=False, schema='pipeline')
    op.create_index('ix_fetch_run_id_id', 'fetch', ['run_id', 'id'], unique=False, schema='pipeline')
    op.create_table('record_version',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('record_id', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('fetch_id', sa.BigInteger(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('parser_version', sa.Text(), nullable=False),
    sa.Column('seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['fetch_id'], ['pipeline.fetch.id'], name=op.f('fk_record_version_fetch_id_fetch')),
    sa.ForeignKeyConstraint(['record_id'], ['pipeline.record.id'], name=op.f('fk_record_version_record_id_record')),
    sa.ForeignKeyConstraint(['run_id'], ['pipeline.run.id'], name=op.f('fk_record_version_run_id_run')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_record_version')),
    schema='pipeline'
    )
    op.create_index('ix_record_version_record_id_id', 'record_version', ['record_id', 'id'], unique=False, schema='pipeline')


def downgrade() -> None:
    op.drop_index('ix_record_version_record_id_id', table_name='record_version', schema='pipeline')
    op.drop_table('record_version', schema='pipeline')
    op.drop_index('ix_fetch_run_id_id', table_name='fetch', schema='pipeline')
    op.drop_index('ix_fetch_blob_sha256', table_name='fetch', schema='pipeline')
    op.drop_table('fetch', schema='pipeline')
    op.drop_index('ix_run_source_id_id', table_name='run', schema='pipeline')
    op.drop_table('run', schema='pipeline')
    op.drop_table('record', schema='pipeline')
    op.drop_table('source', schema='pipeline')
