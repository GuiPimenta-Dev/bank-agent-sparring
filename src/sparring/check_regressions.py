"""Regression Check: play the Golden Set against this commit's agent, compare each
Scenario's Status with main's baseline, and publish a `regression-check` commit status.

Composition root, run locally because Langfuse is (ADR-0019). It calls paid APIs, so only
the user runs it, with a cap that fits the project's budget (ADR-0020, ADR-0024).
"""

import argparse
import asyncio
import os
import subprocess
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
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import LangfuseRecorder
from sparring.evaluation.regression_check import (
    RERUNS,
    Baseline,
    CheckedScenario,
    LangfuseRegressionLog,
    RegressionCheck,
    compare,
    unreliable,
)
from sparring.evaluation.simulated_user import OpenAISimulatedUserModel
from sparring.system_under_test import kestrel
from sparring.tracing import langfuse_client

REQUIRED = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "SPARRING_SIMULATOR_MODEL",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)
GITHUB_STATE = {"pass": "success", "fail": "failure", "invalid": "error"}


async def check(args: argparse.Namespace) -> int:
    """Exits 0 on pass, 1 on a Regression and 2 when the check is invalid."""
    agent_model = os.environ.get("SPARRING_AGENT_MODEL", "claude-haiku-5-5")
    agent_effort = os.environ.get("SPARRING_AGENT_EFFORT")
    simulator_model = os.environ["SPARRING_SIMULATOR_MODEL"]
    commit = _git("rev-parse", "HEAD")
    if _git("status", "--porcelain"):
        sys.exit("Commit or stash your changes first: the check must describe one commit.")
    if args.baseline and _git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("A baseline describes main: check out main first.")
    prices = PriceTable.load()
    prices.require(agent_model, simulator_model)
    langfuse = langfuse_client()
    scenarios = [item.scenario for item in await LangfuseGoldenSet(langfuse).items()]
    estimate = prices.cost(typical_conversation(agent_model, simulator_model))
    print(
        f"{len(scenarios)} Golden Set Scenarios at about ${estimate:.4f} a conversation: "
        f"${estimate * len(scenarios):.2f} if all pass, up to "
        f"${estimate * len(scenarios) * (1 + RERUNS):.2f}. Cap: ${args.max_cost:.2f}."
    )
    if not args.yes and input("Start? [y/N] ").strip().lower() != "y":
        return 2

    async def connect_agent(client: Client) -> SupportAgent:
        return await SupportAgent.connect(client, agent_model, agent_effort, tracing=True)

    metadata = {
        "agent_version": commit[:7],
        "regression_check": "baseline" if args.baseline else "check",
        "agent_model": agent_model,
        "simulator_model": simulator_model,
    }
    openai = AsyncOpenAI()
    runner = ScenarioRunner(
        kestrel(connect_agent, agent_model),
        OpenAISimulatedUserModel(openai, simulator_model),
        Oracle(),
        LangfuseRecorder(langfuse, metadata),
        tone_judge=OpenAIToneJudge(openai, simulator_model),
    )
    checked = await RegressionCheck(runner, prices, args.parallelism).run(
        scenarios, Budget(args.max_cost, estimate)
    )
    langfuse.flush()
    log = LangfuseRegressionLog(langfuse)
    statuses = Baseline(commit[:7], simulator_model, {c.scenario_id: c.status for c in checked})
    if args.baseline:
        return await record_baseline(log, statuses, checked)
    baseline = await log.latest_baseline(simulator_model)
    comparison = compare(baseline, checked)
    await log.record(comparison.outcome, statuses)
    print_checked(checked, baseline)
    print(f"\n{comparison.outcome.upper()}: {comparison.summary}")
    for scenario_id in comparison.warnings:
        print(f"  warning: {scenario_id} went from Passing to Flaky")
    if comparison.new:
        print(f"  {len(comparison.new)} Scenarios are new since the baseline.")
    _publish(commit, GITHUB_STATE[comparison.outcome], comparison.summary)
    return {"pass": 0, "fail": 1, "invalid": 2}[comparison.outcome]


async def record_baseline(
    log: LangfuseRegressionLog, statuses: Baseline, checked: list[CheckedScenario]
) -> int:
    print_checked(checked, None)
    reasons = unreliable(checked)
    if reasons:
        print(f"\nNo baseline recorded: {'; '.join(reasons)}.")
        return 2
    await log.record("baseline", statuses)
    print(f"\nBaseline recorded for main at {statuses.agent_version}.")
    return 0


def print_checked(checked: list[CheckedScenario], baseline: Baseline | None) -> None:
    for c in checked:
        was = baseline.statuses.get(c.scenario_id, "new") if baseline else "-"
        print(f"{c.status.upper():<13} (main: {was:<12}) ${c.cost:.4f}  {c.scenario_id}")
    spent = sum((c.cost for c in checked), Decimal(0))
    resolved = sum(c.passed for c in checked)
    per_resolved = f"; ${spent / resolved:.4f} per resolved conversation" if resolved else ""
    print(f"\nSpent ${spent:.4f}{per_resolved}.")


def _publish(commit: str, state: str, description: str) -> None:
    """The commit status branch protection can require (ADR-0019)."""
    subprocess.run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{{owner}}/{{repo}}/statuses/{commit}",
            "-f",
            f"state={state}",
            "-f",
            "context=regression-check",
            "-f",
            f"description={description[:140]}",
        ],
        check=True,
        capture_output=True,
    )
    print(f"Published regression-check: {state} on {commit[:7]}.")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cost", type=Decimal, required=True, help="The hard cap, in USD.")
    parser.add_argument("--parallelism", type=int, default=4, help="Scenarios at once.")
    parser.add_argument(
        "--baseline", action="store_true", help="Record main's Statuses instead of comparing."
    )
    parser.add_argument("--yes", action="store_true", help="Start without asking.")
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    try:
        sys.exit(asyncio.run(check(args)))
    except UnpricedModel as error:
        sys.exit(str(error))
