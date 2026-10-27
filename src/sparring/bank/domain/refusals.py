"""Hard Rule refusals: expected business outcomes the agent must explain, not retry (ADR-0017).

The hierarchy is at most two levels deep: `HardRuleRefusal` and its leaves.
"""


class HardRuleRefusal(Exception):
    pass


class IdentityVerificationFailed(HardRuleRefusal):
    def __init__(self) -> None:
        super().__init__("The details given do not match our records.")


class IdentityNotVerified(HardRuleRefusal):
    def __init__(self) -> None:
        super().__init__("Identity Verification is required before this operation.")


class VerifiedSessionExpired(HardRuleRefusal):
    def __init__(self) -> None:
        super().__init__(
            "The Verified Session has expired; Identity Verification is required again."
        )


class CardNotFound(HardRuleRefusal):
    def __init__(self, card_id: str) -> None:
        super().__init__(f"There is no card {card_id} on this Customer's account.")


class CardAlreadyFrozen(HardRuleRefusal):
    def __init__(self, card_id: str) -> None:
        super().__init__(f"Card {card_id} is already frozen.")


class CardNotFrozen(HardRuleRefusal):
    def __init__(self, card_id: str) -> None:
        super().__init__(f"Card {card_id} is not frozen.")


class FraudFreezeLocked(HardRuleRefusal):
    def __init__(self, card_id: str) -> None:
        super().__init__(
            f"Card {card_id} was frozen by the fraud team; only the fraud team can lift it."
        )


class CardCancelled(HardRuleRefusal):
    def __init__(self, card_id: str) -> None:
        super().__init__(f"Card {card_id} has been cancelled and can no longer be used.")


class TransactionNotFound(HardRuleRefusal):
    def __init__(self, transaction_id: str) -> None:
        super().__init__(f"There is no transaction {transaction_id} on this Customer's account.")


class TransactionPending(HardRuleRefusal):
    def __init__(self, transaction_id: str) -> None:
        super().__init__(
            f"Transaction {transaction_id} is still pending; it can be disputed once it settles."
        )


class DisputeWindowExpired(HardRuleRefusal):
    def __init__(self, transaction_id: str, window_days: int) -> None:
        super().__init__(
            f"Transaction {transaction_id} settled more than {window_days} days ago "
            "and can no longer be disputed."
        )


class DisputeAlreadyOpen(HardRuleRefusal):
    def __init__(self, transaction_id: str) -> None:
        super().__init__(f"Transaction {transaction_id} already has an open Dispute.")


class PendingActionNotFound(HardRuleRefusal):
    def __init__(self, pending_action_id: str) -> None:
        super().__init__(f"There is no pending action {pending_action_id} for this Customer.")


class PendingActionExpired(HardRuleRefusal):
    def __init__(self, pending_action_id: str) -> None:
        super().__init__(
            f"Pending action {pending_action_id} expired before it was confirmed; request it again."
        )


class PendingActionAlreadyAnswered(HardRuleRefusal):
    def __init__(self, pending_action_id: str, status: str) -> None:
        super().__init__(f"Pending action {pending_action_id} was already {status}.")
