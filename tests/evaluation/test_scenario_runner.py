from pathlib import Path

import pytest
from fakes import ScriptedSimulatedUser, calls, says, scripted
from langchain_core.messages import AIMessage
from mcp import Client

from sparring.agent.support_agent import SupportAgent
from sparring.evaluation.conversation import ScenarioRunner
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.scenario import Scenario, load_scenario
from sparring.evaluation.simulated_user import UserMove
from sparring.system_under_test import kestrel

pytestmark = pytest.mark.anyio

CASES = Path(__file__).parents[2] / "canonical_cases"
VERIFY = calls(
    "verify_identity", customer_number="C-1001", date_of_birth="1990-05-17", postcode="SW1A 1AA"
)


def freeze_debit_card() -> Scenario:
    return load_scenario(CASES / "freeze-active-debit-card.json")


def runner(agent_script: list[AIMessage], user: ScriptedSimulatedUser) -> ScenarioRunner:
    async def connect_agent(client: Client) -> SupportAgent:
        return await SupportAgent.with_model(client, scripted(*agent_script))

    return ScenarioRunner(kestrel(connect_agent), user, Oracle(), InMemoryRecorder(), turn_limit=4)


async def test_an_agent_that_freezes_the_right_card_passes() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Please freeze my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Thanks, bye!", True),
        ]
    )
    agent = [
        VERIFY,
        calls("freeze_card", card_id="card-ana-debit"),
        says("Your debit card is frozen."),
    ]

    conversation, verdict = await runner(agent, user).run(freeze_debit_card())

    assert verdict.passed, verdict.differences
    assert [turn.speaker for turn in conversation.transcript] == ["customer", "agent", "customer"]
    assert conversation.transcript[1].text == "Your debit card is frozen."
    assert not conversation.reached_turn_limit


async def test_an_agent_that_freezes_the_wrong_card_is_a_failure() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Freeze my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Ok.", True),
        ]
    )
    agent = [VERIFY, calls("freeze_card", card_id="card-ana-credit"), says("Done.")]

    _, verdict = await runner(agent, user).run(freeze_debit_card())

    assert not verdict.passed
    assert verdict.differences == [
        "card card-ana-credit: frozen_by expected None, found <FreezeSource.CUSTOMER: 'customer'>",
        "card card-ana-debit: frozen_by expected <FreezeSource.CUSTOMER: 'customer'>, found None",
    ]


async def test_the_turn_limit_stops_a_conversation_and_the_oracle_still_judges_it() -> None:
    user = ScriptedSimulatedUser([UserMove("Hello?", False)] * 4)
    agent = [says("How can I help?")] * 4

    conversation, verdict = await runner(agent, user).run(freeze_debit_card())

    assert conversation.reached_turn_limit
    assert not verdict.passed


async def test_the_simulated_user_sees_the_conversation_and_its_persona() -> None:
    user = ScriptedSimulatedUser([UserMove("Hi", False), UserMove("Bye", True)])
    await runner([says("Hello! How can I help?")], user).run(freeze_debit_card())

    assert user.transcripts[1][-1].text == "Hello! How can I help?"
    assert "customer number: C-1001" in user.instructions
    assert "after 6 of your messages without progress" in user.instructions


async def test_each_conversation_gets_its_own_bank() -> None:
    scenario = freeze_debit_card()
    agent = [VERIFY, calls("freeze_card", card_id="card-ana-debit"), says("Frozen.")]
    first = ScriptedSimulatedUser(
        [UserMove("C-1001, 1990-05-17, SW1A 1AA", False), UserMove("Bye", True)]
    )
    await runner(agent, first).run(scenario)

    second = ScriptedSimulatedUser([UserMove("Bye", True)])
    conversation, _ = await runner([], second).run(scenario)

    assert conversation.final.card(scenario.goal.intent.card_id).frozen_by is None
