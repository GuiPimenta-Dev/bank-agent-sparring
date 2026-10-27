import json
from pathlib import Path

import pytest

from sparring.evaluation.scenario import load_scenario
from sparring.evaluation.simulated_user import Turn, as_input

CASE = Path(__file__).parents[2] / "canonical_cases" / "freeze-active-debit-card.json"


def write(tmp_path: Path, change: dict[str, object]) -> Path:
    document = json.loads(CASE.read_text())
    document.update(change)
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(document))
    return path


def test_a_goal_naming_an_unknown_card_is_rejected(tmp_path: Path) -> None:
    goal = {"intent": {"operation": "freeze_card", "card_id": "card-nobody"}, "narrative": "x"}
    with pytest.raises(ValueError, match="unknown card card-nobody"):
        load_scenario(write(tmp_path, {"goal": goal}))


def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="frozen_bye"):
        load_scenario(write(tmp_path, {"frozen_bye": "customer"}))


def test_a_scenario_without_a_timezone_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        load_scenario(write(tmp_path, {"now": "2026-10-26T12:00:00"}))


def test_the_simulated_user_reads_the_agent_as_the_other_party() -> None:
    transcript = [Turn("customer", "Hi"), Turn("agent", "Hello")]
    roles = [message["role"] for message in as_input(transcript)]
    assert roles == ["user", "assistant", "user"]


@pytest.mark.parametrize(
    ("intent", "error"),
    [
        ({"operation": "dispute_transaction", "transaction_ids": ["tx-x"]}, "unknown transaction"),
        ({"operation": "dispute_transaction", "card_id": "card-ana-debit"}, "a Dispute names"),
        ({"operation": "escalate", "card_id": "card-ana-debit"}, "an Escalation nothing"),
        ({"operation": "freeze_card", "card_id": "card-ana-debit", "unrecognised": True}, "only"),
    ],
)
def test_a_goal_names_what_its_operation_acts_on(
    tmp_path: Path, intent: dict[str, object], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        load_scenario(write(tmp_path, {"goal": {"intent": intent, "narrative": "x"}}))


def test_a_scenario_starts_before_any_escalation(tmp_path: Path) -> None:
    bank = {**json.loads(CASE.read_text())["bank"], "escalated": True}
    with pytest.raises(ValueError, match="before any Escalation"):
        load_scenario(write(tmp_path, {"bank": bank}))
