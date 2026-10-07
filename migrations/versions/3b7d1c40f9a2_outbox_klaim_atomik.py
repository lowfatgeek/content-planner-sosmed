"""outbox klaim atomik (D18): claimed_at + claim_token + index status,id

Revision ID: 3b7d1c40f9a2
Revises: e7b764ce4a01
Create Date: 2026-10-07 11:05:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '3b7d1c40f9a2'
down_revision: Union[str, None] = 'e7b764ce4a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `status` sudah VARCHAR(20) sejak migrasi awal, jadi menambah nilai `proses`
    # tidak butuh ALTER tipe — cukup dua kolom klaim + index untuk query klaim.
    with op.batch_alter_table('notification_outbox', schema=None) as batch_op:
        batch_op.add_column(sa.Column('claim_token', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('claimed_at', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_outbox_status_id', ['status', 'id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('notification_outbox', schema=None) as batch_op:
        batch_op.drop_index('ix_outbox_status_id')
        batch_op.drop_column('claimed_at')
        batch_op.drop_column('claim_token')
