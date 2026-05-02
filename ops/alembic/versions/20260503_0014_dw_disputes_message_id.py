"""Add ``discord_message_id`` column to dw.disputes (Story 9.2).

Revision ID: 0014_dw_disputes_message_id
Revises: 0013_dw_dispute_reject_fn
Create Date: 2026-05-03

Story 9.2 says: ``Each dispute open / status change posts a new embed
in #disputes with status updates editing prior message. Message IDs
persisted on the dw.disputes row.``

We persist the Discord message id on the disputes row so the
resolve/reject paths can edit the same embed instead of posting a
fresh one — cleaner audit-channel transcript for admins.

The column is nullable because:
- Disputes opened before this migration won't have a message id.
- The Discord post is best-effort: if it fails, the row keeps NULL
  and the resolver simply skips the edit step (the audit-log channel
  still receives the resolve/reject event via the audit poster).
"""

from alembic import op

revision = "0014_dw_disputes_message_id"
down_revision = "0013_dw_dispute_reject_fn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE dw.disputes
            ADD COLUMN IF NOT EXISTS discord_message_id BIGINT;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE dw.disputes
            DROP COLUMN IF EXISTS discord_message_id;
    """)
