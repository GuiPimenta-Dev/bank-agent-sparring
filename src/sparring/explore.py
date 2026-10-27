"""Exploration: run generated Scenarios against the agent under a hard dollar cap.

Composition root: the agent on Claude; the Simulated User, the story writer and the LLM
judges on the pinned OpenAI model (ADR-0006); one Langfuse trace per conversation; and
Promotion into the Golden Set Dataset in Langfuse, which Exploration therefore needs
(ADR-0005), with Quarantine as a Langfuse annotation queue.
It calls paid APIs, so only the user runs it, with a cap that fits the project's budget
(ADR-0020, ADR-0024).
"""

import argparse
import asyncio
import os
import random
import sys
import uuid
from decimal import Decimal

from mcp import Client
from openai import AsyncOpenAI

from sparring.agent.support_agent import POLICY, SupportAgent
from sparring.evaluation.conversation import ScenarioRunner
from sparring.evaluation.cost import Budget, PriceTable, UnpricedModel
from sparring.evaluation.exploration import Exploration, ExplorationReport, typical_conversation
from sparring.evaluation.golden_set import LangfuseGoldenSet
from sparring.evaluation.judges import OpenAIPolicyDriftJudge, OpenAIToneJudge
from sparring.evaluation.narrative import OpenAINarrativeModel
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.promotion import Promotion
from sparring.evaluation.quarantine import LangfuseQuarantine
from sparring.evaluation.recording import LangfuseRecorder
from sparring.evaluation.simulated_user import OpenAISimulatedUserModel
from sparring.system_under_test import kestrel
from sparring.tracing import agent_version, langfuse_client

REQUIRED = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "SPARRING_SIMULATOR_MODEL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)


async def explore(args: argparse.Namespace) -> None:
    agent_model = os.environ.get("SPARRING_AGENT_MODEL", "claude-haiku-5-5")
    agent_effort = os.environ.get("SPARRING_AGENT_EFFORT")
    simulator_model = os.environ["SPARRING_SIMULATOR_MODEL"]
    prices = PriceTable.load()
    prices.require(agent_model, simulator_model)
    estimate = prices.cost(typical_conversation(agent_model, simulator_model))
    print(
        f"Estimate: ${estimate:.4f} per conversation, ${estimate * args.scenarios:.2f} for "
        f"{args.scenarios}. A cap of ${args.max_cost:.2f} covers about "
        f"{min(args.scenarios, int(args.max_cost / estimate))} of them."
    )
    if not args.yes and input("Start? [y/N] ").strip().lower() != "y":
        return

    async def connect_agent(client: Client) -> SupportAgent:
        return await SupportAgent.connect(client, agent_model, agent_effort, tracing=True)

    run_id = f"explore-{uuid.uuid4().hex[:6]}"
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    print(f"Run {run_id}, seed {seed}.")
    metadata = {
        "agent_version": agent_version(),
        "exploration_run": run_id,
        "agent_model": agent_model,
        "agent_effort": agent_effort or "default",
        "simulator_model": simulator_model,
    }
    langfuse = langfuse_client()
    recorder = LangfuseRecorder(langfuse, metadata)
    openai = AsyncOpenAI()
    runner = ScenarioRunner(
        kestrel(connect_agent, agent_model),
        OpenAISimulatedUserModel(openai, simulator_model),
        Oracle(),
        recorder,
        tone_judge=OpenAIToneJudge(openai, simulator_model),
    )
    promotion = Promotion(
        LangfuseGoldenSet(langfuse),
        Oracle(),
        OpenAIPolicyDriftJudge(openai, simulator_model, POLICY),
        LangfuseQuarantine(langfuse),
        recorder,
    )
    exploration = Exploration(
        runner,
        OpenAINarrativeModel(openai, simulator_model),
        promotion,
        prices,
        args.parallelism,
    )
    report = await exploration.run(run_id, args.scenarios, Budget(args.max_cost, estimate), seed)
    langfuse.flush()
    print_report(report)


def print_report(report: ExplorationReport) -> None:
    for explored in report.explored:
        dimensions = " ".join(f"{name}={value}" for name, value in explored.dimensions.items())
        print(f"{explored.outcome.upper():<12} ${explored.cost:.4f}  {explored.scenario_id}")
        print(f"  {dimensions}")
        for finding in explored.findings:
            print(f"  - {finding}")
        if explored.promotion:
            print(f"  Promotion: {explored.promotion}")
    counts = ", ".join(
        f"{report.count(outcome)} {outcome}"
        for outcome in ("pass", "failure", "inconclusive", "discarded")
    )
    print(f"\n{len(report.explored)} explored ({counts}); {report.not_launched} not launched.")
    print(f"Spent ${report.spent:.4f} of ${report.cap:.2f}.")
    per_resolved = report.cost_per_resolved_conversation
    if per_resolved is not None:
        print(f"Cost per resolved conversation: ${per_resolved:.4f}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, required=True, help="How many to generate.")
    parser.add_argument(
        "--max-cost", type=Decimal, required=True, help="The run's hard cap, in USD."
    )
    parser.add_argument("--parallelism", type=int, default=4, help="Conversations at once.")
    parser.add_argument(
        "--seed", type=int, help="Seeds the Dimension sampling; random when left out."
    )
    parser.add_argument("--yes", action="store_true", help="Start without asking.")
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    try:
        asyncio.run(explore(args))
    except UnpricedModel as error:
        sys.exit(str(error))
