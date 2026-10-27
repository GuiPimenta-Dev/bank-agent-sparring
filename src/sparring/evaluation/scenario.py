from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.dispute import Dispute, DisputeId
from sparring.bank.domain.fee import Fee, FeeId
from sparring.bank.domain.money import Money
from sparring.bank.domain.transaction import Transaction, TransactionId

Operation = Literal[
    "freeze_card", "unfreeze_card", "report_lost_or_stolen", "dispute_transaction", "escalate"
]
Tactic = Literal["none", "impersonation", "third_party", "prompt_injection", "pressure"]
"""The adversarial technique a Scenario applies against the agent, or none."""


@dataclass
class BankSnapshot:
    customers: list[Customer]
    cards: list[Card]
    fees: list[Fee] = field(default_factory=list[Fee])
    transactions: list[Transaction] = field(default_factory=list[Transaction])
    disputes: list[Dispute] = field(default_factory=list[Dispute])
    escalated: bool = False
    """Whether the conversation was handed to a human support agent."""

    def card(self, card_id: CardId) -> Card:
        return next(card for card in self.cards if card.id == card_id)

    def customer(self, customer_id: CustomerId) -> Customer:
        return next(customer for customer in self.customers if customer.id == customer_id)

    def transaction(self, transaction_id: TransactionId) -> Transaction:
        return next(t for t in self.transactions if t.id == transaction_id)

    def differences(self, actual: "BankSnapshot") -> list[str]:
        """What `actual` does not share with this snapshot, one line per difference."""
        return [str(difference) for difference in self.compare(actual)]

    def compare(self, actual: "BankSnapshot") -> list["Difference"]:
        found = _differences("card", self.cards, actual.cards)
        found += _differences("fee", self.fees, actual.fees)
        found += _differences("transaction", self.transactions, actual.transactions)
        found += _differences("dispute", self.disputes, actual.disputes)
        if self.customers != actual.customers:
            found.append(Difference("customers", None, None, None, None))
        if self.escalated != actual.escalated:
            found.append(Difference("escalated", None, None, self.escalated, actual.escalated))
        return found


@dataclass(frozen=True)
class Difference:
    """One way a Bank Snapshot differs from the expected one: a field of one item, or a
    whole item (`field` is None) that is missing or should not be there."""

    kind: str
    item_id: str | None
    field: str | None
    expected: Any
    found: Any

    def __str__(self) -> str:
        if self.kind == "customers":
            return "customers differ"
        if self.kind == "escalated":
            return f"escalated: expected {self.expected}, found {self.found}"
        if self.field is None:
            return f"{self.kind} {self.item_id}: expected {self.expected}, found {self.found}"
        return (
            f"{self.kind} {self.item_id}: {self.field} expected {self.expected!r}, "
            f"found {self.found!r}"
        )


def _differences(
    kind: str,
    expected: Sequence[Card | Fee | Transaction | Dispute],
    actual: Sequence[Card | Fee | Transaction | Dispute],
) -> list[Difference]:
    expected_by_id: dict[str, dict[str, Any]] = {item.id: asdict(item) for item in expected}
    actual_by_id: dict[str, dict[str, Any]] = {item.id: asdict(item) for item in actual}
    found: list[Difference] = []
    for item_id in sorted(expected_by_id.keys() | actual_by_id.keys()):
        wanted, there = expected_by_id.get(item_id), actual_by_id.get(item_id)
        if wanted is None or there is None:
            found.append(Difference(kind, item_id, None, wanted, there))
            continue
        found += [
            Difference(kind, item_id, field, value, there[field])
            for field, value in wanted.items()
            if there[field] != value
        ]
    return found


@dataclass(frozen=True)
class Persona:
    name: str
    customer_id: CustomerId
    temperament: str
    patience: int
    facts: Mapping[str, str]


@dataclass(frozen=True)
class Intent:
    """The operation the Customer wants: on a card, on transactions for a Dispute, or, to
    Escalate, on nothing.

    A Customer who does not recognise the transactions they dispute accepts the Freeze
    the agent must offer them (DS-7, ADR-0025).
    """

    operation: Operation
    card_id: CardId | None = None
    reason: LossReason | None = None
    transaction_ids: tuple[TransactionId, ...] = ()
    unrecognised: bool = False


@dataclass(frozen=True)
class Goal:
    intent: Intent
    narrative: str


@dataclass(frozen=True)
class Scenario:
    id: str
    now: datetime
    bank: BankSnapshot
    persona: Persona
    goal: Goal
    dimensions: Mapping[str, str]
    tactic: Tactic = "none"

    @property
    def played_by_the_customer(self) -> bool:
        """An impostor or a third party plays someone who is not the Customer, and so has
        neither the right to any operation nor the Customer's app (GC-1, IV-1)."""
        return self.tactic not in ("impersonation", "third_party")


def load_scenario(path: Path) -> Scenario:
    return _ScenarioFile.model_validate_json(path.read_text()).to_scenario()


def scenario_to_json(scenario: Scenario) -> dict[str, Any]:
    """The Scenario as the JSON a Scenario file holds; `scenario_from_json` reads it back."""
    return _ScenarioFile.of(scenario).model_dump(mode="json", exclude_none=True)


def scenario_from_json(data: Mapping[str, Any]) -> Scenario:
    return _ScenarioFile.model_validate(data).to_scenario()


def snapshot_to_json(snapshot: BankSnapshot) -> dict[str, Any]:
    return _BankFile.of(snapshot).model_dump(mode="json", exclude_none=True)


def load_canonical_case(path: Path) -> tuple[Scenario, BankSnapshot]:
    """A Canonical Case: a Scenario plus the Expected Outcome a human worked out."""
    case = _ScenarioFile.model_validate_json(path.read_text())
    if case.expected_outcome is None:
        raise ValueError(f"{path.name} has no expected_outcome")
    return case.to_scenario(), case.expected_outcome.to_snapshot()


class _CustomerFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    full_name: str
    date_of_birth: date
    postcode: str
    tier: Tier


class _CardFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    last_four: str
    frozen_by: FreezeSource | None = None
    cancelled_because: LossReason | None = None
    replaces: str | None = None


class _FeeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    amount: Decimal
    currency: str = "GBP"
    reason: str


class _TransactionFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    card_id: str
    merchant: str
    amount: Decimal = Field(gt=0)
    currency: str = "GBP"
    settled_on: date | None = None


class _DisputeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    transaction_id: str
    provisional_credit: Decimal | None = None
    currency: str = "GBP"


class _BankFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customers: list[_CustomerFile]
    cards: list[_CardFile]
    fees: list[_FeeFile] = Field(default_factory=list[_FeeFile])
    transactions: list[_TransactionFile] = Field(default_factory=list[_TransactionFile])
    disputes: list[_DisputeFile] = Field(default_factory=list[_DisputeFile])
    escalated: bool = False

    @classmethod
    def of(cls, snapshot: BankSnapshot) -> "_BankFile":
        return cls(
            customers=[
                _CustomerFile(
                    id=c.id,
                    full_name=c.full_name,
                    date_of_birth=c.date_of_birth,
                    postcode=c.postcode,
                    tier=c.tier,
                )
                for c in snapshot.customers
            ],
            cards=[
                _CardFile(
                    id=c.id,
                    customer_id=c.customer_id,
                    last_four=c.last_four,
                    frozen_by=c.frozen_by,
                    cancelled_because=c.cancelled_because,
                    replaces=c.replaces,
                )
                for c in snapshot.cards
            ],
            fees=[
                _FeeFile(
                    id=f.id,
                    customer_id=f.customer_id,
                    amount=f.amount.amount,
                    currency=f.amount.currency,
                    reason=f.reason,
                )
                for f in snapshot.fees
            ],
            transactions=[
                _TransactionFile(
                    id=t.id,
                    customer_id=t.customer_id,
                    card_id=t.card_id,
                    merchant=t.merchant,
                    amount=t.amount.amount,
                    currency=t.amount.currency,
                    settled_on=t.settled_on,
                )
                for t in snapshot.transactions
            ],
            disputes=[
                _DisputeFile(
                    id=d.id,
                    customer_id=d.customer_id,
                    transaction_id=d.transaction_id,
                    provisional_credit=d.provisional_credit.amount
                    if d.provisional_credit
                    else None,
                    currency=d.provisional_credit.currency if d.provisional_credit else "GBP",
                )
                for d in snapshot.disputes
            ],
            escalated=snapshot.escalated,
        )

    def to_snapshot(self) -> BankSnapshot:
        return BankSnapshot(
            customers=[
                Customer(CustomerId(c.id), c.full_name, c.date_of_birth, c.postcode, c.tier)
                for c in self.customers
            ],
            cards=[
                Card(
                    CardId(c.id),
                    CustomerId(c.customer_id),
                    c.last_four,
                    c.frozen_by,
                    c.cancelled_because,
                    CardId(c.replaces) if c.replaces else None,
                )
                for c in self.cards
            ],
            fees=[
                Fee(FeeId(f.id), CustomerId(f.customer_id), Money(f.amount, f.currency), f.reason)
                for f in self.fees
            ],
            transactions=[
                Transaction(
                    TransactionId(t.id),
                    CustomerId(t.customer_id),
                    CardId(t.card_id),
                    t.merchant,
                    Money(t.amount, t.currency),
                    t.settled_on,
                )
                for t in self.transactions
            ],
            disputes=[
                Dispute(
                    DisputeId(d.id),
                    CustomerId(d.customer_id),
                    TransactionId(d.transaction_id),
                    Money(d.provisional_credit, d.currency)
                    if d.provisional_credit is not None
                    else None,
                )
                for d in self.disputes
            ],
            escalated=self.escalated,
        )


class _PersonaFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    customer_id: str
    temperament: str
    patience: int = Field(ge=1)
    facts: dict[str, str]


class _IntentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    card_id: str | None = None
    reason: LossReason | None = None
    transaction_ids: list[str] = Field(default_factory=list[str])
    unrecognised: bool = False


class _GoalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: _IntentFile
    narrative: str


class _ScenarioFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    now: AwareDatetime
    bank: _BankFile
    persona: _PersonaFile
    goal: _GoalFile
    dimensions: dict[str, str] = Field(default_factory=dict[str, str])
    tactic: Tactic = "none"
    expected_outcome: _BankFile | None = None

    @classmethod
    def of(cls, scenario: Scenario) -> "_ScenarioFile":
        persona, intent = scenario.persona, scenario.goal.intent
        return cls(
            id=scenario.id,
            now=scenario.now,
            bank=_BankFile.of(scenario.bank),
            persona=_PersonaFile(
                name=persona.name,
                customer_id=persona.customer_id,
                temperament=persona.temperament,
                patience=persona.patience,
                facts=dict(persona.facts),
            ),
            goal=_GoalFile(
                intent=_IntentFile(
                    operation=intent.operation,
                    card_id=intent.card_id,
                    reason=intent.reason,
                    transaction_ids=list(intent.transaction_ids),
                    unrecognised=intent.unrecognised,
                ),
                narrative=scenario.goal.narrative,
            ),
            dimensions=dict(scenario.dimensions),
            tactic=scenario.tactic,
        )

    def to_scenario(self) -> Scenario:
        bank = self.bank.to_snapshot()
        intent = self.goal.intent
        disputing = intent.operation == "dispute_transaction"
        on_a_card = not disputing and intent.operation != "escalate"
        if disputing != bool(intent.transaction_ids) or on_a_card != (intent.card_id is not None):
            raise ValueError(
                f"Scenario {self.id}: a Dispute names transactions, an Escalation nothing, "
                "others a card"
            )
        if intent.card_id and intent.card_id not in {card.id for card in bank.cards}:
            raise ValueError(f"Scenario {self.id}: the Goal names unknown card {intent.card_id}")
        known = {transaction.id for transaction in bank.transactions}
        for transaction_id in intent.transaction_ids:
            if transaction_id not in known:
                raise ValueError(
                    f"Scenario {self.id}: the Goal names unknown transaction {transaction_id}"
                )
        if len(set(intent.transaction_ids)) != len(intent.transaction_ids):
            raise ValueError(f"Scenario {self.id}: the Goal names a transaction twice")
        if (intent.operation == "report_lost_or_stolen") != (intent.reason is not None):
            raise ValueError(f"Scenario {self.id}: a reason goes with report_lost_or_stolen only")
        if intent.unrecognised and not disputing:
            raise ValueError(f"Scenario {self.id}: only a Dispute can be unrecognised")
        if bank.escalated:
            raise ValueError(f"Scenario {self.id}: a Scenario starts before any Escalation")
        if CustomerId(self.persona.customer_id) not in {c.id for c in bank.customers}:
            raise ValueError(f"Scenario {self.id}: the Persona plays unknown customer")
        return Scenario(
            id=self.id,
            now=self.now,
            bank=bank,
            persona=Persona(
                name=self.persona.name,
                customer_id=CustomerId(self.persona.customer_id),
                temperament=self.persona.temperament,
                patience=self.persona.patience,
                facts=self.persona.facts,
            ),
            goal=Goal(
                Intent(
                    intent.operation,
                    CardId(intent.card_id) if intent.card_id else None,
                    intent.reason,
                    tuple(TransactionId(t) for t in intent.transaction_ids),
                    intent.unrecognised,
                ),
                self.goal.narrative,
            ),
            dimensions=self.dimensions,
            tactic=self.tactic,
        )
