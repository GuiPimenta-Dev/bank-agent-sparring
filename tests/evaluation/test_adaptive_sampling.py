from collections import Counter
from random import Random

import pytest

from sparring.evaluation.adaptive_sampling import FLOOR, Observation, weights
from sparring.evaluation.dimensions import OPERATIONS, every_value, sample_dimensions

CHOICES = {"tier": ("standard", "premium"), "persona": ("calm", "worried", "hurried")}


def observed(failed: int, passed: int, **dimensions: str) -> list[Observation]:
    return [Observation(dimensions, True)] * failed + [Observation(dimensions, False)] * passed


def test_without_observations_every_value_is_equally_likely() -> None:
    assert weights([], CHOICES) == {
        "tier": {"standard": 0.5, "premium": 0.5},
        "persona": pytest.approx({"calm": 1 / 3, "worried": 1 / 3, "hurried": 1 / 3}),
    }


def test_values_that_fail_more_are_drawn_more() -> None:
    history = observed(8, 2, tier="premium") + observed(1, 9, tier="standard")

    tier = weights(history, CHOICES)["tier"]

    assert tier["premium"] > tier["standard"]
    assert sum(tier.values()) == pytest.approx(1)


@pytest.mark.parametrize("floor", [FLOOR, 0.2, 0.9])
def test_the_floor_keeps_every_value_however_well_it_does(floor: float) -> None:
    history = observed(1000, 0, persona="hurried") + observed(0, 1000, persona="calm")

    persona = weights(history, CHOICES, floor)["persona"]

    assert min(persona.values()) >= floor / 3
    assert sum(persona.values()) == pytest.approx(1)


def test_a_value_seen_once_is_not_judged_on_one_conversation() -> None:
    tier = weights(observed(1, 0, tier="premium"), CHOICES)["tier"]

    assert tier["premium"] < 0.65


def test_sampling_follows_the_weights_and_still_reaches_every_value() -> None:
    choices = every_value()
    history = observed(50, 0, operation="escalate") + [
        Observation({"operation": operation}, False) for operation in OPERATIONS * 10
    ]
    random = Random(3)

    drawn = Counter(
        sample_dimensions(random, weights(history, choices))["operation"] for _ in range(3000)
    )

    assert drawn.most_common(1)[0][0] == "escalate"
    assert set(drawn) == set(OPERATIONS)
    assert min(drawn.values()) / 3000 >= FLOOR / len(OPERATIONS) * 0.8


def test_every_value_lists_each_dimension_s_values_across_operations() -> None:
    choices = every_value()

    assert choices["operation"] == OPERATIONS
    assert set(choices["card_state"]) == {
        "active",
        "frozen_by_customer",
        "frozen_by_fraud_team",
        "cancelled",
    }
