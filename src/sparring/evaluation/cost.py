"""What runs cost: token usage priced from a table versioned in the repo (ADR-0020)."""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

PRICES = Path(__file__).parent / "prices.toml"
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Usage:
    """The tokens one model consumed, split the way providers price them. `input_tokens`
    counts only the input that was neither read from nor written to a cache."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write: Decimal


class UnpricedModel(Exception):
    def __init__(self, model: str) -> None:
        super().__init__(
            f"{model} has no price in {PRICES.name}; add it from the provider's pricing page."
        )


class PriceTable:
    def __init__(self, prices: Mapping[str, Price]) -> None:
        self._prices = prices

    @classmethod
    def load(cls, path: Path = PRICES) -> "PriceTable":
        table = tomllib.loads(path.read_text(), parse_float=Decimal)
        return cls({model: Price(**price) for model, price in table["models"].items()})

    def require(self, *models: str) -> None:
        """Refuses, before anything runs, a model whose spend could not be capped."""
        for model in models:
            if model not in self._prices:
                raise UnpricedModel(model)

    def cost(self, usages: Iterable[Usage]) -> Decimal:
        return sum((self._cost(usage) for usage in usages), Decimal(0))

    def _cost(self, usage: Usage) -> Decimal:
        if usage.model not in self._prices:
            raise UnpricedModel(usage.model)
        price = self._prices[usage.model]
        return (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_read_tokens * price.cache_read
            + usage.cache_write_tokens * price.cache_write
        ) / MILLION


class Budget:
    """A run's hard dollar cap (ADR-0020). A conversation is launched only if what was
    already spent, an estimate for each conversation still running, and its own estimate
    all fit under the cap; conversations already running always finish."""

    def __init__(self, cap: Decimal, estimate: Decimal) -> None:
        self.cap = cap
        self.spent = Decimal(0)
        self._estimate = estimate
        self._running = 0

    def reserve(self) -> bool:
        if self.spent + (self._running + 1) * self._estimate > self.cap:
            return False
        self._running += 1
        return True

    def settle(self, cost: Decimal) -> None:
        """A conversation launched with `reserve` has finished and cost `cost`."""
        self._running -= 1
        self.spent += cost
