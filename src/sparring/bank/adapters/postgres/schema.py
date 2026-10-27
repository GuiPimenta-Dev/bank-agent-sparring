"""The bank's tables, in SQLAlchemy Core (ADR-0015).

Hard Rules live in the domain; the constraints here are only defense in depth (ADR-0009).
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)

metadata = MetaData()

customers = Table(
    "customers",
    metadata,
    Column("id", String, primary_key=True),
    Column("full_name", Text, nullable=False),
    Column("date_of_birth", Date, nullable=False),
    Column("postcode", String, nullable=False),
    Column("tier", String, CheckConstraint("tier IN ('standard', 'premium')"), nullable=False),
)

cards = Table(
    "cards",
    metadata,
    Column("id", String, primary_key=True),
    Column("customer_id", String, ForeignKey("customers.id"), nullable=False, index=True),
    Column("last_four", String(4), nullable=False),
    Column("frozen_by", String, CheckConstraint("frozen_by IN ('customer', 'fraud_team')")),
    Column("cancelled_because", String, CheckConstraint("cancelled_because IN ('lost', 'stolen')")),
    Column("replaces", String, ForeignKey("cards.id")),
)

transactions = Table(
    "transactions",
    metadata,
    Column("id", String, primary_key=True),
    Column("customer_id", String, ForeignKey("customers.id"), nullable=False, index=True),
    Column("card_id", String, ForeignKey("cards.id"), nullable=False),
    Column("merchant", Text, nullable=False),
    Column("amount", Numeric(12, 2), CheckConstraint("amount > 0"), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("settled_on", Date),
)

disputes = Table(
    "disputes",
    metadata,
    Column("id", String, primary_key=True),
    Column("customer_id", String, ForeignKey("customers.id"), nullable=False, index=True),
    # DS-3: one open Dispute per transaction, and every Dispute here is open.
    Column("transaction_id", String, ForeignKey("transactions.id"), nullable=False, unique=True),
    Column("provisional_credit", Numeric(12, 2)),
    Column("currency", String(3), nullable=False),
)

fees = Table(
    "fees",
    metadata,
    Column("id", String, primary_key=True),
    Column("customer_id", String, ForeignKey("customers.id"), nullable=False, index=True),
    Column("amount", Numeric(12, 2), CheckConstraint("amount > 0"), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("reason", Text, nullable=False),
)

pending_actions = Table(
    "pending_actions",
    metadata,
    Column("id", String, primary_key=True),
    Column("customer_id", String, ForeignKey("customers.id"), nullable=False, index=True),
    Column("kind", String, nullable=False),
    Column("card_id", String, ForeignKey("cards.id")),
    Column("because", String),
    Column("transaction_id", String, ForeignKey("transactions.id")),
    Column("summary", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column(
        "status",
        String,
        CheckConstraint("status IN ('awaiting', 'confirmed', 'rejected')"),
        nullable=False,
    ),
    CheckConstraint(
        "(kind = 'report_lost_or_stolen' AND card_id IS NOT NULL AND because IS NOT NULL"
        " AND transaction_id IS NULL)"
        " OR (kind = 'dispute_transaction' AND transaction_id IS NOT NULL"
        " AND card_id IS NULL AND because IS NULL)",
        name="pending_action_operation",
    ),
)

escalations = Table(
    "escalations",
    metadata,
    Column("id", String, primary_key=True),
    Column("reason", Text, nullable=False),
)


def is_the_bank_s(name: str | None, kind: str, parent_names: object) -> bool:
    """For Alembic: the database may hold other tables, such as the agent's conversations,
    which the bank's migrations must leave alone."""
    return kind != "table" or name in metadata.tables
