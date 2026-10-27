"""Langfuse set-up shared by the composition roots.

The first `Langfuse` client created in a process fixes the export settings, so every
entrypoint creates it here, before anything traces.
"""

import os
import subprocess
from collections.abc import Mapping

from langfuse import Langfuse
from langfuse.span_filter import is_default_export_span
from opentelemetry.sdk.trace import ReadableSpan

from sparring.evaluation.recording import InMemoryRecorder, LangfuseRecorder, Recorder


def tracing_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))


def langfuse_client() -> Langfuse:
    return Langfuse(should_export_span=_not_from_mcp_sdk)


def recorder(metadata: Mapping[str, str]) -> Recorder:
    """Langfuse when it is configured, with the agent version added to `metadata`;
    otherwise conversations are only kept in memory."""
    if not tracing_enabled():
        return InMemoryRecorder()
    return LangfuseRecorder(langfuse_client(), {"agent_version": agent_version(), **metadata})


def agent_version() -> str:
    """The git commit the agent runs from."""
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() or "unknown"


def _not_from_mcp_sdk(span: ReadableSpan) -> bool:
    # The MCP SDK traces every tool call in a context of its own, which would leave a
    # stray trace per call; the calls are already visible as the agent's tool runs.
    scope = span.instrumentation_scope
    return is_default_export_span(span) and not (scope and scope.name == "mcp-python-sdk")
