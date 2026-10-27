"""Langfuse set-up shared by the composition roots.

The first `Langfuse` client created in a process fixes the export settings, so every
entrypoint creates it here, before anything traces.
"""

from langfuse import Langfuse
from langfuse.span_filter import is_default_export_span
from opentelemetry.sdk.trace import ReadableSpan


def langfuse_client() -> Langfuse:
    return Langfuse(should_export_span=_not_from_mcp_sdk)


def _not_from_mcp_sdk(span: ReadableSpan) -> bool:
    # The MCP SDK traces every tool call in a context of its own, which would leave a
    # stray trace per call; the calls are already visible as the agent's tool runs.
    scope = span.instrumentation_scope
    return is_default_export_span(span) and not (scope and scope.name == "mcp-python-sdk")
