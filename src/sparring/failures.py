"""Failure Signatures by frequency, with how many Scenarios each has in the Golden Set.

Composition root: reads the raw `failure_signature` scores and the Golden Set from
Langfuse (ADR-0012). It calls no LLM.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

from sparring.evaluation.failure_signature import LangfuseFailureLog, signature_counts
from sparring.evaluation.golden_set import LangfuseGoldenSet
from sparring.evaluation.promotion import PER_SIGNATURE
from sparring.tracing import langfuse_client

REQUIRED = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


async def report(days: int) -> None:
    langfuse = langfuse_client()
    since = datetime.now(UTC) - timedelta(days=days)
    signatures = await LangfuseFailureLog(langfuse).signatures(since)
    golden_set = await LangfuseGoldenSet(langfuse).items()
    counts = signature_counts(signatures, golden_set)
    print(f"{len(signatures)} Failures in the last {days} days, {len(counts)} Signatures.\n")
    print(f"{'Failures':>8}  {'Golden':>6}  Signature")
    for count in counts:
        golden = f"{count.in_golden_set}/{PER_SIGNATURE}"
        print(f"{count.failures:>8}  {golden:>6}  {count.signature}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="How far back to count.")
    args = parser.parse_args()
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"Set {', '.join(missing)} (see .env.example).")
    asyncio.run(report(args.days))
