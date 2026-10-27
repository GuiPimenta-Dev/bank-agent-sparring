"""Run one Scenario file end-to-end and print the Oracle's Verdict.

Composition root: the agent on Claude, the Simulated User on the pinned OpenAI model,
and one Langfuse trace per conversation when Langfuse is configured.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from mcp import Client
from openai import AsyncOpenAI

from sparring.agent.support_agent import SupportAgent
from sparring.evaluation.conversation import ScenarioRunner
from sparring.evaluation.oracle import Oracle
from sparring.evaluation.recording import InMemoryRecorder, LangfuseRecorder, Recorder
from sparring.evaluation.scenario import load_scenario
from sparring.evaluation.simulated_user import OpenAISimulatedUserModel
from sparring.system_under_test import kestrel
from sparring.tracing import langfuse_client

REQUIRED = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SPARRING_SIMULATOR_MODEL")


async def run(path: Path) -> int:
    """Exits 0 on a pass, 1 on a Failure and 2 when the conversation is Inconclusive."""
    agent_model = os.environ.get("SPARRING_AGENT_MODEL", "claude-haiku-5-5")
    agent_effort = os.environ.get("SPARRING_AGENT_EFFORT")
    simulator_model = os.environ["SPARRING_SIMULATOR_MODEL"]
    tracing = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))

    async def connect_agent(client: Client) -> SupportAgent:
        return await SupportAgent.connect(client, agent_model, agent_effort, tracing=tracing)

    recorder: Recorder = InMemoryRecorder()
    if tracing:
        metadata = {
            "agent_version": _git_sha(),
            "agent_model": agent_model,
            "agent_effort": agent_effort or "default",
            "simulator_model": simulator_model,
        }
        recorder = LangfuseRecorder(langfuse_client(), metadata)

    runner = ScenarioRunner(
        kestrel(connect_agent),
        OpenAISimulatedUserModel(AsyncOpenAI(), simulator_model),
        Oracle(),
        recorder,
    )
    conversation, verdict = await runner.run(load_scenario(path))
    for turn in conversation.transcript:
        print(f"{turn.speaker}> {turn.text}\n")
    if conversation.final.escalated:
        print("Escalated to a human support agent.")
    if conversation.hard_rule_refusals:
        print("Hard Rule refusals:", ", ".join(conversation.hard_rule_refusals))
    if verdict.inconclusive:
        print("INCONCLUSIVE", conversation.interrupted_by, sep="\n  ")
        return 2
    violations = [f"{v.invariant}: {v.detail}" for v in verdict.violations]
    print("PASS" if verdict.passed else "FAILURE", *verdict.differences, *violations, sep="\n  ")
    return 0 if verdict.passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="A Scenario or Canonical Case JSON file.")
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    sys.exit(asyncio.run(run(args.scenario)))


def _git_sha() -> str:
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() or "unknown"
