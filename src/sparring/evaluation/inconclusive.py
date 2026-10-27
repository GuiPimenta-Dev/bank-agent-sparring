class InfrastructureFailure(Exception):
    """A provider the conversation depends on still failed after its retries.

    The conversation it interrupts is Inconclusive, never a Failure (ADR-0017).
    """
