"""The bank's schema: customers, cards, transactions, Disputes, fees, Pending Actions and
Escalations.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("postcode", sa.String(), nullable=False),
        sa.Column(
            "tier",
            sa.String(),
            sa.CheckConstraint("tier IN ('standard', 'premium')"),
            nullable=False,
        ),
    )
    op.create_table(
        "cards",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("last_four", sa.String(4), nullable=False),
        sa.Column(
            "frozen_by", sa.String(), sa.CheckConstraint("frozen_by IN ('customer', 'fraud_team')")
        ),
        sa.Column(
            "cancelled_because",
            sa.String(),
            sa.CheckConstraint("cancelled_because IN ('lost', 'stolen')"),
        ),
        sa.Column("replaces", sa.String(), sa.ForeignKey("cards.id")),
    )
    op.create_index("ix_cards_customer_id", "cards", ["customer_id"])
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("card_id", sa.String(), sa.ForeignKey("cards.id"), nullable=False),
        sa.Column("merchant", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), sa.CheckConstraint("amount > 0"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("settled_on", sa.Date()),
    )
    op.create_index("ix_transactions_customer_id", "transactions", ["customer_id"])
    op.create_table(
        "disputes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column(
            "transaction_id",
            sa.String(),
            sa.ForeignKey("transactions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("provisional_credit", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(3), nullable=False),
    )
    op.create_index("ix_disputes_customer_id", "disputes", ["customer_id"])
    op.create_table(
        "fees",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), sa.CheckConstraint("amount > 0"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    op.create_index("ix_fees_customer_id", "fees", ["customer_id"])
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("customer_id", sa.String(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("card_id", sa.String(), sa.ForeignKey("cards.id")),
        sa.Column("because", sa.String()),
        sa.Column("transaction_id", sa.String(), sa.ForeignKey("transactions.id")),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            sa.CheckConstraint("status IN ('awaiting', 'confirmed', 'rejected')"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'report_lost_or_stolen' AND card_id IS NOT NULL AND because IS NOT NULL"
            " AND transaction_id IS NULL)"
            " OR (kind = 'dispute_transaction' AND transaction_id IS NOT NULL"
            " AND card_id IS NULL AND because IS NULL)",
            name="pending_action_operation",
        ),
    )
    op.create_index("ix_pending_actions_customer_id", "pending_actions", ["customer_id"])
    op.create_table(
        "escalations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("reason", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "escalations",
        "pending_actions",
        "fees",
        "disputes",
        "transactions",
        "cards",
        "customers",
    ):
        op.drop_table(table)
