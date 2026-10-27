"""Model sweep: play the Golden Set over a grid of agent models and effort levels, under a
hard dollar cap, and report each cell's resolved rate and cost per resolved conversation.

Composition root. It calls paid APIs, and a sweep over the larger models can take a large
share of the project's budget, so only the user runs it, with a cap that fits what is left
(ADR-0020, ADR-0024).
"""

import argparse
import asyncio
import os
import sys
from decimal import Decimal

from mcp import Client
from openai import AsyncOpenAI

from sparring.agent.support_agent import SupportAgent
from sparring.evaluation.conversation import ScenarioRunner
from sparring.evaluation.cost import Budget, PriceTable, UnpricedModel
from sparring.evaluation.exploration import typical_conversation
from sparring.evaluation.golden_set import LangfuseGoldenSet
from sparring.evaluation.judges import OpenAIToneJudge
from sparring.evaluation.model_sweep import Cell, CellResult, LangfuseExperiments, ModelSweep
from sparring.evaluation.oracle import Oracle
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
MODELS = "claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5"


async def sweep(args: argparse.Namespace) -> None:
    simulator_model = os.environ["SPARRING_SIMULATOR_MODEL"]
    cells = [
        Cell(model, None if effort == "default" else effort)
        for model in args.models.split(",")
        for effort in args.efforts.split(",")
    ]
    prices = PriceTable.load()
    prices.require(simulator_model, *(cell.model for cell in cells))
    langfuse = langfuse_client()
    scenarios = len(await LangfuseGoldenSet(langfuse).items())
    estimates = {
        cell: prices.cost(typical_conversation(cell.model, simulator_model)) for cell in cells
    }
    for cell, estimate in estimates.items():
        print(f"{cell.label:<40} about ${estimate * scenarios:.2f} for {scenarios} Scenarios")
    total = sum(estimates.values(), Decimal(0)) * scenarios
    print(f"About ${total:.2f} in all. Cap: ${args.max_cost:.2f}.")
    if not args.yes and input("Start? [y/N] ").strip().lower() != "y":
        return

    version = agent_version()
    metadata = {"agent_version": version, "simulator_model": simulator_model}

    def runner_for(cell: Cell) -> ScenarioRunner:
        # Called on the experiment's own event loop, so the clients belong to it.
        openai = AsyncOpenAI()

        async def connect_agent(client: Client) -> SupportAgent:
            return await SupportAgent.connect(client, cell.model, cell.effort, tracing=True)

        cell_metadata = {
            **metadata,
            "model_sweep": version,
            "agent_model": cell.model,
            "agent_effort": cell.effort or "default",
        }
        return ScenarioRunner(
            kestrel(connect_agent, cell.model),
            OpenAISimulatedUserModel(openai, simulator_model),
            Oracle(),
            LangfuseRecorder(langfuse, cell_metadata),
            tone_judge=OpenAIToneJudge(openai, simulator_model),
        )

    model_sweep = ModelSweep(runner_for, LangfuseExperiments(langfuse, args.parallelism), prices)
    budget = Budget(args.max_cost, max(estimates.values()))
    print_results(await model_sweep.run(f"sweep {version}", cells, budget, metadata))


def print_results(results: list[CellResult]) -> None:
    print(
        f"\n{'Model':<22}{'Effort':<9}{'Resolved':>10}{'Rate':>7}{'Cost':>10}{'Per resolved':>14}"
    )
    for result in results:
        rate = f"{result.resolved_rate:.0%}" if result.resolved_rate is not None else "-"
        per = f"${result.cost_per_resolved:.4f}" if result.cost_per_resolved else "-"
        resolved = f"{result.resolved}/{result.conclusive}"
        print(
            f"{result.cell.model:<22}{result.cell.effort or 'default':<9}{resolved:>10}"
            f"{rate:>7}{f'${result.cost:.4f}':>10}{per:>14}"
            + (f"  ({result.not_run} not run)" if result.not_run else "")
        )
    for result in results:
        if result.signatures:
            print(f"\nFailure Signatures for {result.cell.label}:")
            for signature, count in result.signatures.most_common():
                print(f"  {count:>3}  {signature}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-cost", type=Decimal, required=True, help="The sweep's hard cap, in USD."
    )
    parser.add_argument("--models", default=MODELS, help="Comma-separated agent models.")
    parser.add_argument(
        "--efforts", default="default", help="Comma-separated effort levels, or 'default'."
    )
    parser.add_argument("--parallelism", type=int, default=4, help="Scenarios at once.")
    parser.add_argument("--yes", action="store_true", help="Start without asking.")
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    try:
        asyncio.run(sweep(args))
    except UnpricedModel as error:
        sys.exit(str(error))
