"""Dimensions: the axes generated Scenarios vary along.

Code samples their values and builds everything the Oracle reads from them: the Bank
Snapshot, the Persona and the structured Goal. The Simulated User's model writes only the
story around them (narrative.py); what the Customer wants and the choices the Oracle
depends on are fixed here, in plain words (ADR-0025).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from random import Random

from sparring.bank.domain.card import Card, CardId, FreezeSource, LossReason
from sparring.bank.domain.customer import Customer, CustomerId, Tier
from sparring.bank.domain.dispute import DISPUTE_WINDOW, PROVISIONAL_CREDIT_LIMIT
from sparring.bank.domain.money import Money
from sparring.bank.domain.transaction import Transaction, TransactionId
from sparring.evaluation.scenario import BankSnapshot, Goal, Intent, Operation, Persona, Scenario

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
ESCALATION_THRESHOLD = Decimal(2000)

OPERATIONS: tuple[Operation, ...] = (
    "freeze_card",
    "unfreeze_card",
    "report_lost_or_stolen",
    "dispute_transaction",
    "escalate",
)
PERSONAS = {
    "calm": "calm and careful: you read what the app asks before confirming",
    "worried": "worried but cooperative: you read what the app asks before confirming",
    "impatient": "impatient and terse, but you read what the app asks before confirming",
    "hurried": "in a hurry: you confirm whatever your app asks without reading it closely",
}
SHARED: Mapping[str, Sequence[str]] = {
    "tier": ("standard", "premium"),
    "persona": tuple(PERSONAS),
    "patience": ("3", "5", "8"),
}
PER_OPERATION: Mapping[Operation, Mapping[str, Sequence[str]]] = {
    "freeze_card": {
        "card": ("debit", "credit"),
        "card_state": ("active", "frozen_by_customer", "frozen_by_fraud_team", "cancelled"),
    },
    "unfreeze_card": {
        "card": ("debit", "credit"),
        "card_state": ("frozen_by_customer", "frozen_by_fraud_team", "active"),
    },
    "report_lost_or_stolen": {
        "card": ("debit", "credit"),
        "card_state": ("active", "frozen_by_customer", "cancelled"),
        "reason": ("lost", "stolen"),
    },
    "dispute_transaction": {
        "transaction_count": ("1", "2", "3"),
        "transaction_state": ("settled", "pending", "settled_and_pending"),
        "amount_band": (
            "within_provisional_credit_limit",
            "over_provisional_credit_limit",
            "over_escalation_threshold",
        ),
        "transaction_age": ("recent", "last_day_of_window", "past_window"),
        "recognised": ("yes", "no"),
    },
    "escalate": {},
}

CUSTOMERS = (
    ("Ana Souza", date(1990, 5, 17), "SW1A 1AA"),
    ("Ben Okafor", date(1984, 1, 2), "M1 1AE"),
    ("Chloe Martin", date(1977, 11, 30), "EH1 2NG"),
    ("Dev Patel", date(1995, 8, 9), "B1 1BB"),
    ("Eilidh Ross", date(1962, 3, 21), "G2 3AX"),
)
MERCHANTS = ("Currys", "John Lewis", "British Airways", "Pret A Manger", "Deliveroo", "IKEA")
UNRECOGNISED_MERCHANTS = ("STEAMGAMES.COM", "ROBLOX.COM", "APPLE.COM/BILL", "SHEINGLOBAL", "TEMU")
EVERYDAY = (("Tesco", "18.20"), ("Trainline", "42.60"))


def sample_dimensions(random: Random) -> dict[str, str]:
    """Uniform over operations, then over each Dimension the operation has."""
    operation = random.choice(OPERATIONS)
    values = {"operation": operation}
    for name, choices in {**SHARED, **PER_OPERATION[operation]}.items():
        values[name] = random.choice(choices)
    if (
        values.get("transaction_count") == "1"
        and values["transaction_state"] == "settled_and_pending"
    ):
        values["transaction_state"] = random.choice(("settled", "pending"))
    if values.get("transaction_state") == "pending":
        del values["transaction_age"]
    return values


@dataclass(frozen=True)
class ScenarioDraft:
    """A generated Scenario before its story is written."""

    id: str
    bank: BankSnapshot
    persona: Persona
    intent: Intent
    dimensions: Mapping[str, str]
    wants: str
    """What the Customer wants, in plain words."""
    choices: str
    """The Customer's answers the Oracle depends on (ADR-0025)."""

    def scenario(self, story: str) -> Scenario:
        narrative = " ".join(part for part in (story, self.wants, self.choices) if part)
        return Scenario(
            self.id, NOW, self.bank, self.persona, Goal(self.intent, narrative), self.dimensions
        )


def draft(scenario_id: str, dimensions: Mapping[str, str], random: Random) -> ScenarioDraft:
    name, date_of_birth, postcode = random.choice(CUSTOMERS)
    first_name = name.split()[0].lower()
    customer = Customer(
        CustomerId(f"C-{random.randint(1000, 9999)}"),
        name,
        date_of_birth,
        postcode,
        Tier(dimensions["tier"]),
    )
    debit, credit = (
        Card(CardId(f"card-{first_name}-{kind}"), customer.id, f"{last_four:04d}")
        for kind, last_four in zip(
            ("debit", "credit"), random.sample(range(10_000), 2), strict=True
        )
    )
    bank = BankSnapshot([customer], [debit, credit])
    facts = {
        "customer number": customer.id,
        "date of birth": f"{date_of_birth:%Y-%m-%d}",
        "postcode": postcode,
    }
    persona = Persona(
        name,
        customer.id,
        PERSONAS[dimensions["persona"]],
        int(dimensions["patience"]),
        facts,
    )
    builder = _Builder(scenario_id, dimensions, random, bank, persona, debit, credit)
    match dimensions["operation"]:
        case "escalate":
            return builder.escalation()
        case "dispute_transaction":
            return builder.dispute()
        case _:
            return builder.card_operation()


class _Builder:
    def __init__(
        self,
        scenario_id: str,
        dimensions: Mapping[str, str],
        random: Random,
        bank: BankSnapshot,
        persona: Persona,
        debit: Card,
        credit: Card,
    ) -> None:
        self._id = scenario_id
        self._dimensions = dimensions
        self._random = random
        self._bank = bank
        self._persona = persona
        self._debit = debit
        self._credit = credit

    def escalation(self) -> ScenarioDraft:
        persona = self._with_facts({"full name": self._persona.name})
        wants = (
            "You want to complain about how a Kestrel Bank phone adviser treated you, to a "
            "human being rather than a chatbot. You do not want to give a chatbot your details."
        )
        return self._draft(persona, Intent("escalate"), wants, "Ask to speak to a human.")

    def card_operation(self) -> ScenarioDraft:
        kind = self._dimensions["card"]
        card = self._debit if kind == "debit" else self._credit
        self._apply_state(card)
        facts = {**self._persona.facts, f"{kind} card": f"ends in {card.last_four}"}
        operation = self._dimensions["operation"]
        match operation:
            case "freeze_card":
                wants = (
                    f"You cannot find your {kind} card, though it may turn up at home. You "
                    "want it frozen for now, not cancelled."
                )
                intent = Intent("freeze_card", card.id)
            case "unfreeze_card":
                wants = (
                    f"You froze your {kind} card yourself when you misplaced it, and you have "
                    "found it again. You want it unfrozen."
                    if self._dimensions["card_state"] != "frozen_by_fraud_team"
                    else f"Your {kind} card stopped working and you want it working again."
                )
                intent = Intent("unfreeze_card", card.id)
            case _:
                reason = LossReason(self._dimensions["reason"])
                happened = "was stolen" if reason is LossReason.STOLEN else "is lost for good"
                wants = f"Your {kind} card {happened}. You want it cancelled and replaced."
                intent = Intent("report_lost_or_stolen", card.id, reason)
        return self._draft(self._with_facts(facts), intent, wants, "")

    def dispute(self) -> ScenarioDraft:
        count = int(self._dimensions["transaction_count"])
        recognised = self._dimensions["recognised"] == "yes"
        merchants = self._random.sample(MERCHANTS if recognised else UNRECOGNISED_MERCHANTS, count)
        disputed = [
            Transaction(
                TransactionId(f"tx-{index}"),
                self._persona.customer_id,
                self._debit.id,
                merchant,
                Money(amount),
                settled_on,
            )
            for index, (merchant, amount, settled_on) in enumerate(
                zip(merchants, self._amounts(count), self._settlement_dates(count), strict=True),
                start=1,
            )
        ]
        everyday = [
            Transaction(
                TransactionId(f"tx-everyday-{index}"),
                self._persona.customer_id,
                self._debit.id,
                merchant,
                Money(Decimal(amount)),
                NOW.date() - timedelta(days=index + 1),
            )
            for index, (merchant, amount) in enumerate(EVERYDAY)
        ]
        self._bank.transactions.extend([*disputed, *everyday])
        listed = "; ".join(_described(t) for t in disputed)
        them = "it" if count == 1 else "them"
        if recognised:
            known = "the payments to dispute"
            wants = (
                f"You want {'this payment' if count == 1 else 'these payments'} disputed: {listed}."
            )
            choices = (
                "Mention every payment when you first explain the problem." if count > 1 else ""
            )
        else:
            known = "the payments you do not recognise"
            wants = (
                f"You do not recognise {'this payment' if count == 1 else 'these payments'} on "
                f"your debit card, which is still in your wallet: {listed}. You want {them} "
                "disputed."
            )
            choices = "If the agent offers to freeze your card, accept."
            if count > 1:
                choices = f"Mention every payment when you first explain the problem. {choices}"
        facts = {
            **self._persona.facts,
            "debit card": f"ends in {self._debit.last_four}",
            known: listed,
        }
        intent = Intent(
            "dispute_transaction",
            transaction_ids=tuple(t.id for t in disputed),
            unrecognised=not recognised,
        )
        return self._draft(self._with_facts(facts), intent, wants, choices)

    def _apply_state(self, card: Card) -> None:
        match self._dimensions["card_state"]:
            case "frozen_by_customer":
                card.frozen_by = FreezeSource.CUSTOMER
            case "frozen_by_fraud_team":
                card.frozen_by = FreezeSource.FRAUD_TEAM
            case "cancelled":
                self._bank.cards.append(card.cancel(LossReason.LOST))
            case _:
                pass

    def _amounts(self, count: int) -> list[Decimal]:
        """Within the Tier's Provisional Credit limit and the Escalation threshold, over the
        limit but not the threshold, or over the threshold in total (DS-5, ES-3)."""
        limit = PROVISIONAL_CREDIT_LIMIT[Tier(self._dimensions["tier"])].amount
        share = ESCALATION_THRESHOLD / count
        match self._dimensions["amount_band"]:
            case "within_provisional_credit_limit":
                return [self._pounds(Decimal(5), min(limit, share)) for _ in range(count)]
            case "over_provisional_credit_limit":
                small = [self._pounds(Decimal(5), Decimal(50)) for _ in range(count - 1)]
                ceiling = ESCALATION_THRESHOLD - 50 * (count - 1)
                return [self._pounds(limit + Decimal("0.01"), ceiling), *small]
            case _:
                return [self._pounds(share + Decimal("0.01"), share + 400) for _ in range(count)]

    def _settlement_dates(self, count: int) -> list[date | None]:
        """The last of several is pending when only some have settled (DS-1, DS-2)."""
        today = NOW.date()
        state = self._dimensions["transaction_state"]
        if state == "pending":
            return [None] * count
        match self._dimensions["transaction_age"]:
            case "recent":
                settled: list[date | None] = [
                    today - timedelta(days=self._random.randint(2, 30)) for _ in range(count)
                ]
            case "last_day_of_window":
                settled = [today - DISPUTE_WINDOW] * count
            case _:
                settled = [
                    today - DISPUTE_WINDOW - timedelta(days=self._random.randint(1, 60))
                    for _ in range(count)
                ]
        return [*settled[:-1], None] if state == "settled_and_pending" else settled

    def _pounds(self, low: Decimal, high: Decimal) -> Decimal:
        pennies = self._random.randint(int(low * 100), int(high * 100))
        return Decimal(pennies) / 100

    def _with_facts(self, facts: Mapping[str, str]) -> Persona:
        persona = self._persona
        return Persona(
            persona.name, persona.customer_id, persona.temperament, persona.patience, facts
        )

    def _draft(self, persona: Persona, intent: Intent, wants: str, choices: str) -> ScenarioDraft:
        return ScenarioDraft(
            self._id, self._bank, persona, intent, self._dimensions, wants, choices
        )


def _described(transaction: Transaction) -> str:
    amount = f"£{transaction.amount.amount:,.2f} to {transaction.merchant}"
    if transaction.settled_on is None:
        return f"{amount}, still pending"
    settled = transaction.settled_on
    return f"{amount} on {settled.day} {settled:%B %Y}"
