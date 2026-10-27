from collections.abc import Iterator, Sequence
from pathlib import Path

import anthropic
import httpx2
import pytest
from fakes import ScriptedModel, ScriptedSimulatedUser, calls, says, scripted, unreachable
from langchain_core.messages import AIMessage
from mcp import Client

from sparring.agent.support_agent import CAP_REACHED_REPLY, TOOL_CALL_CAP, SupportAgent
from sparring.bank.domain.card import CardId
from sparring.evaluation.conversation import ScenarioRunner
from sparring.evaluation.cost import Usage
from sparring.evaluation.inconclusive import InfrastructureFailure
from sparring.evaluation.judges import ToneJudge, ToneJudgement
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import InMemoryRecorder
from sparring.evaluation.scenario import Scenario, load_scenario
from sparring.evaluation.simulated_user import Turn, UserMove
from sparring.system_under_test import kestrel

pytestmark = pytest.mark.anyio

CASES = Path(__file__).parents[2] / "canonical_cases"
VERIFY = calls(
    "verify_identity", customer_number="C-1001", date_of_birth="1990-05-17", postcode="SW1A 1AA"
)


def freeze_debit_card() -> Scenario:
    return load_scenario(CASES / "freeze-active-debit-card.json")


def runner(
    agent: list[AIMessage] | ScriptedModel,
    user: ScriptedSimulatedUser,
    recorder: InMemoryRecorder | None = None,
    tone_judge: ToneJudge | None = None,
) -> ScenarioRunner:
    model = scripted(*agent) if isinstance(agent, list) else agent

    async def connect_agent(client: Client) -> SupportAgent:
        return await SupportAgent.with_model(client, model)

    return ScenarioRunner(
        kestrel(connect_agent, "scripted"),
        user,
        Oracle(),
        recorder or InMemoryRecorder(),
        tone_judge=tone_judge,
        turn_limit=4,
    )


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
    assert verdict.signature is None
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
    assert verdict.signature == (
        "freeze_card: card.frozen_by customer->none, card.frozen_by none->customer"
    )


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

    assert conversation.final.card(CardId("card-ana-debit")).frozen_by is None


def report_lost_debit_card() -> Scenario:
    return load_scenario(CASES / "report-lost-debit-card-standard.json")


def wants_a_freeze_not_a_report() -> Scenario:
    return load_scenario(CASES / "misplaced-card-wants-freeze-not-report.json")


REPORT_LOST = calls("report_lost_or_stolen", card_id="card-ana-debit", reason="lost")


async def test_a_customer_who_confirms_the_right_report_in_the_app_passes() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("I lost my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Confirmed in the app, thanks.", True),
        ],
        answer_requests=True,
    )
    agent = [VERIFY, REPORT_LOST, says("Please confirm the request in your Kestrel Bank app.")]

    conversation, verdict = await runner(agent, user).run(report_lost_debit_card())

    assert verdict.passed, verdict.differences
    channel = [turn.text for turn in conversation.transcript if turn.speaker == "customer_channel"]
    assert channel[0].startswith("Request pa-") and "for £10.00" in channel[0]
    assert channel[1].startswith("You confirmed request pa-")


async def test_a_careless_customer_who_confirms_the_wrong_action_is_a_failure() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Please block my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Done.", True),
        ],
        answer_requests=True,
    )
    agent = [VERIFY, REPORT_LOST, says("Confirm the request in your app.")]

    _, verdict = await runner(agent, user).run(wants_a_freeze_not_a_report())

    assert not verdict.passed
    assert (
        "card card-ana-debit: cancelled_because expected None, found <LossReason.LOST: 'lost'>"
        in (verdict.differences)
    )


async def test_a_careful_customer_rejects_the_wrong_action_and_the_agent_recovers() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Please block my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("I rejected it. I don't want it cancelled, just frozen.", False),
            UserMove("Great, thanks.", True),
        ],
        answer_requests=False,
    )
    agent = [
        VERIFY,
        REPORT_LOST,
        says("Confirm the request in your app."),
        calls("freeze_card", card_id="card-ana-debit"),
        says("Understood. Your debit card is frozen instead."),
    ]

    conversation, verdict = await runner(agent, user).run(wants_a_freeze_not_a_report())

    assert verdict.passed, verdict.differences
    assert any(turn.text.startswith("You rejected") for turn in conversation.transcript)


async def test_a_confirmed_dispute_with_provisional_credit_passes() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Pret charged me for a cancelled order. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Confirmed, thanks.", True),
        ],
        answer_requests=True,
    )
    agent = [
        VERIFY,
        calls("list_transactions"),
        calls("dispute_transaction", transaction_id="tx-ana-pret"),
        says("You'll get £42.50 back while we investigate. Please confirm in your app."),
    ]
    scenario = load_scenario(CASES / "dispute-wrong-charge-applies-provisional-credit.json")

    conversation, verdict = await runner(agent, user).run(scenario)

    assert verdict.passed, verdict.differences
    assert conversation.hard_rule_refusals == []


async def test_the_conversation_keeps_the_hard_rules_that_refused_the_agent() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Dispute my Ticketline payment. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Ok, bye.", True),
        ]
    )
    agent = [
        VERIFY,
        calls("dispute_transaction", transaction_id="tx-ana-ticketline"),
        says("That payment is still pending; please come back once it has settled."),
    ]
    scenario = load_scenario(CASES / "dispute-pending-transaction-changes-nothing.json")

    conversation, verdict = await runner(agent, user).run(scenario)

    assert verdict.passed, verdict.differences
    assert conversation.hard_rule_refusals == ["TransactionPending"]


ESCALATE = calls("escalate", reason="The Customer wants to complain to a human.")


async def test_an_escalation_ends_the_conversation_without_identity_verification() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("I want to complain to a human, not a bot.", False),
            UserMove("This move is never played.", False),
        ]
    )
    agent = [ESCALATE, says("A colleague will take over this conversation now.")]
    scenario = load_scenario(
        CASES / "explicit-request-for-a-human-escalates-without-verification.json"
    )

    conversation, verdict = await runner(agent, user).run(scenario)

    assert verdict.passed, verdict.differences
    assert [turn.speaker for turn in conversation.transcript] == ["customer", "agent"]
    assert conversation.final.escalated and not conversation.reached_turn_limit


async def test_an_escalation_instead_of_a_request_the_agent_can_handle_is_a_failure() -> None:
    user = ScriptedSimulatedUser([UserMove("Freeze my debit card, please.", False)])
    agent = [ESCALATE, says("A colleague will take over this conversation now.")]

    _, verdict = await runner(agent, user).run(freeze_debit_card())

    assert verdict.differences == [
        "card card-ana-debit: frozen_by expected <FreezeSource.CUSTOMER: 'customer'>, found None",
        "escalated: expected False, found True",
    ]


async def test_a_missing_escalation_is_a_failure() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Unfreeze my credit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Fine, bye.", True),
        ]
    )
    agent = [
        VERIFY,
        calls("unfreeze_card", card_id="card-ana-credit"),
        says("Sorry, our fraud team froze that card, so I cannot unfreeze it."),
    ]
    scenario = load_scenario(CASES / "unfreeze-fraud-freeze-stays-frozen.json")

    _, verdict = await runner(agent, user).run(scenario)

    assert verdict.differences == ["escalated: expected True, found False"]


async def test_an_invariant_violation_is_a_failure_even_when_the_bank_ends_up_right() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Unfreeze my credit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Try again, please.", False),
            UserMove("Then I want a human.", False),
        ]
    )
    unfreeze = calls("unfreeze_card", card_id="card-ana-credit")
    agent = [
        VERIFY,
        unfreeze,
        says("Our fraud team froze that card, so I cannot unfreeze it."),
        calls("unfreeze_card", card_id="card-ana-credit"),
        says("I'm afraid it is still refused."),
        ESCALATE,
        says("A colleague will take over now."),
    ]
    scenario = load_scenario(CASES / "unfreeze-fraud-freeze-stays-frozen.json")

    _, verdict = await runner(agent, user).run(scenario)

    assert verdict.differences == []
    assert [v.invariant for v in verdict.violations] == ["refused_call_repeated"]
    assert verdict.failed and not verdict.passed
    assert verdict.signature == "invariant: refused_call_repeated"


async def test_a_turn_past_the_tool_call_cap_is_a_failure_and_the_conversation_goes_on() -> None:
    user = ScriptedSimulatedUser(
        [
            UserMove("Freeze my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Freeze my debit card, please.", False),
            UserMove("Thanks.", True),
        ]
    )
    agent = [
        VERIFY,
        *[calls("list_cards") for _ in range(TOOL_CALL_CAP)],
        calls("freeze_card", card_id="card-ana-debit"),
        says("Your debit card is frozen."),
    ]

    conversation, verdict = await runner(agent, user).run(freeze_debit_card())

    assert conversation.transcript[1].text == CAP_REACHED_REPLY
    assert verdict.differences == []
    assert [v.invariant for v in verdict.violations] == ["tool_call_cap"]


async def test_an_agent_provider_outage_makes_the_conversation_inconclusive() -> None:
    user = ScriptedSimulatedUser([UserMove("Freeze my debit card, please.", False)])
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    outage = unreachable(
        anthropic.InternalServerError(
            "Overloaded", response=httpx2.Response(529, request=request), body=None
        )
    )
    recorder = InMemoryRecorder()

    conversation, verdict = await runner(outage, user, recorder).run(freeze_debit_card())

    assert verdict.inconclusive and not verdict.failed and not verdict.passed
    assert conversation.interrupted_by == "Overloaded"
    assert [turn.speaker for turn in conversation.transcript] == ["customer"]
    assert recorder.records == [(conversation, verdict)]


async def test_a_simulated_user_provider_outage_makes_the_conversation_inconclusive() -> None:
    def moves() -> Iterator[UserMove]:
        yield UserMove("Freeze my debit card.", False)
        raise InfrastructureFailure("OpenAI is unreachable")

    user = ScriptedSimulatedUser(moves())

    conversation, verdict = await runner([says("Sure, please verify first.")], user).run(
        freeze_debit_card()
    )

    assert verdict.inconclusive
    assert conversation.interrupted_by == "OpenAI is unreachable"
    assert [turn.speaker for turn in conversation.transcript] == ["customer", "agent"]


TONE_USAGE = Usage("simulator", input_tokens=900, output_tokens=60)


class Tone:
    """A tone judge that likes everything, or is unreachable."""

    def __init__(self, reachable: bool = True) -> None:
        self._reachable = reachable
        self.judged: list[Sequence[Turn]] = []

    async def judge(self, transcript: Sequence[Turn]) -> ToneJudgement:
        self.judged.append(transcript)
        if not self._reachable:
            raise InfrastructureFailure("OpenAI is unreachable")
        return ToneJudgement(5, "Warm and brief.", TONE_USAGE)


def freezing_user() -> ScriptedSimulatedUser:
    return ScriptedSimulatedUser(
        [
            UserMove("Freeze my debit card. C-1001, 1990-05-17, SW1A 1AA.", False),
            UserMove("Bye", True),
        ]
    )


FREEZING_AGENT = [VERIFY, calls("freeze_card", card_id="card-ana-debit"), says("Frozen.")]


async def test_every_conversation_gets_a_tone_that_never_decides_the_verdict() -> None:
    tone = Tone()
    recorder = InMemoryRecorder()

    conversation, verdict = await runner(FREEZING_AGENT, freezing_user(), recorder, tone).run(
        freeze_debit_card()
    )

    assert verdict.passed and verdict.tone == ToneJudgement(5, "Warm and brief.", TONE_USAGE)
    assert tone.judged == [conversation.transcript]
    assert conversation.usage[-1] == TONE_USAGE
    assert recorder.records == [(conversation, verdict)]


async def test_a_tone_judge_outage_leaves_only_the_tone_out() -> None:
    conversation, verdict = await runner(
        FREEZING_AGENT, freezing_user(), tone_judge=Tone(False)
    ).run(freeze_debit_card())

    assert verdict.passed and verdict.tone is None
    assert conversation.judge_usage == ()


async def test_an_inconclusive_conversation_is_not_judged_for_tone() -> None:
    tone = Tone()
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    outage = unreachable(anthropic.APIConnectionError(request=request))
    user = ScriptedSimulatedUser([UserMove("Freeze my debit card, please.", False)])

    _, verdict = await runner(outage, user, tone_judge=tone).run(freeze_debit_card())

    assert verdict.inconclusive and tone.judged == []
