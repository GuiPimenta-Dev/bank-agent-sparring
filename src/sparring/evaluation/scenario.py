from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from sparring.bank.domain.card import Card, CardId, FreezeSource
from sparring.bank.domain.customer import Customer, CustomerId

Operation = Literal["freeze_card", "unfreeze_card"]


@dataclass
class BankSnapshot:
    customers: list[Customer]
    cards: list[Card]

    def card(self, card_id: CardId) -> Card:
        return next(card for card in self.cards if card.id == card_id)

    def differences(self, actual: "BankSnapshot") -> list[str]:
        """What `actual` does not share with this snapshot, one line per difference."""
        expected_cards = {card.id: asdict(card) for card in self.cards}
        actual_cards = {card.id: asdict(card) for card in actual.cards}
        lines: list[str] = []
        for card_id in sorted(expected_cards.keys() | actual_cards.keys()):
            expected, found = expected_cards.get(card_id), actual_cards.get(card_id)
            if expected is None or found is None:
                lines.append(f"card {card_id}: expected {expected}, found {found}")
                continue
            lines += [
                f"card {card_id}: {field} expected {value!r}, found {found[field]!r}"
                for field, value in expected.items()
                if found[field] != value
            ]
        if self.customers != actual.customers:
            lines.append("customers differ")
        return lines


@dataclass(frozen=True)
class Persona:
    name: str
    temperament: str
    patience: int
    facts: Mapping[str, str]


@dataclass(frozen=True)
class Intent:
    operation: Operation
    card_id: CardId


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


class _CardFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer_id: str
    last_four: str
    frozen_by: FreezeSource | None = None


class _BankFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customers: list[_CustomerFile]
    cards: list[_CardFile]

    def to_snapshot(self) -> BankSnapshot:
        return BankSnapshot(
            [
                Customer(CustomerId(c.id), c.full_name, c.date_of_birth, c.postcode)
                for c in self.customers
            ],
            [
                Card(CardId(c.id), CustomerId(c.customer_id), c.last_four, c.frozen_by)
                for c in self.cards
            ],
        )


class _PersonaFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    temperament: str
    patience: int = Field(ge=1)
    facts: dict[str, str]


class _IntentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    card_id: str


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
        card_id = CardId(self.goal.intent.card_id)
        if card_id not in {card.id for card in bank.cards}:
            raise ValueError(f"Scenario {self.id}: the Goal names unknown card {card_id}")
        return Scenario(
            id=self.id,
            now=self.now,
            bank=bank,
            persona=Persona(
                self.persona.name,
                self.persona.temperament,
                self.persona.patience,
                self.persona.facts,
            ),
            goal=Goal(Intent(self.goal.intent.operation, card_id), self.goal.narrative),
            dimensions=self.dimensions,
        )
