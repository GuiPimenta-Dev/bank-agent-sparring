from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.fee import Fee, FeeId
from sparring.bank.domain.money import Money

Operation = Literal["freeze_card", "unfreeze_card", "report_lost_or_stolen"]


@dataclass
class BankSnapshot:
    customers: list[Customer]
    cards: list[Card]
    fees: list[Fee]

    def card(self, card_id: CardId) -> Card:
        return next(card for card in self.cards if card.id == card_id)

    def customer(self, customer_id: CustomerId) -> Customer:
        return next(customer for customer in self.customers if customer.id == customer_id)

    def differences(self, actual: "BankSnapshot") -> list[str]:
        """What `actual` does not share with this snapshot, one line per difference."""
        lines = _differences("card", self.cards, actual.cards)
        lines += _differences("fee", self.fees, actual.fees)
        if self.customers != actual.customers:
            lines.append("customers differ")
        return lines


def _differences(
    kind: str, expected: list[Card] | list[Fee], actual: list[Card] | list[Fee]
) -> list[str]:
    expected_by_id: dict[str, dict[str, Any]] = {item.id: asdict(item) for item in expected}
    actual_by_id: dict[str, dict[str, Any]] = {item.id: asdict(item) for item in actual}
    lines: list[str] = []
    for item_id in sorted(expected_by_id.keys() | actual_by_id.keys()):
        wanted, found = expected_by_id.get(item_id), actual_by_id.get(item_id)
        if wanted is None or found is None:
            lines.append(f"{kind} {item_id}: expected {wanted}, found {found}")
            continue
        lines += [
            f"{kind} {item_id}: {field} expected {value!r}, found {found[field]!r}"
            for field, value in wanted.items()
            if found[field] != value
        ]
    return lines


@dataclass(frozen=True)
class Persona:
    name: str
    customer_id: CustomerId
    temperament: str
    patience: int
    facts: Mapping[str, str]


@dataclass(frozen=True)
class Intent:
    operation: Operation
    card_id: CardId
    reason: LossReason | None = None


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


def load_scenario(path: Path) -> Scenario:
    return _ScenarioFile.model_validate_json(path.read_text()).to_scenario()


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


class _BankFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customers: list[_CustomerFile]
    cards: list[_CardFile]
    fees: list[_FeeFile] = Field(default_factory=list[_FeeFile])

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
    card_id: str
    reason: LossReason | None = None


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
    expected_outcome: _BankFile | None = None

    def to_scenario(self) -> Scenario:
        bank = self.bank.to_snapshot()
        intent = self.goal.intent
        if CardId(intent.card_id) not in {card.id for card in bank.cards}:
            raise ValueError(f"Scenario {self.id}: the Goal names unknown card {intent.card_id}")
        if (intent.operation == "report_lost_or_stolen") != (intent.reason is not None):
            raise ValueError(f"Scenario {self.id}: a reason goes with report_lost_or_stolen only")
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
                Intent(intent.operation, CardId(intent.card_id), intent.reason),
                self.goal.narrative,
            ),
            dimensions=self.dimensions,
        )
