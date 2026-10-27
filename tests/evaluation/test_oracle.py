from pathlib import Path

import pytest

from sparring.evaluation.oracle import Oracle
from sparring.evaluation.scenario import load_canonical_case

CANONICAL_CASES = sorted((Path(__file__).parents[2] / "canonical_cases").glob("*.json"))


def test_there_are_canonical_cases() -> None:
    assert CANONICAL_CASES


@pytest.mark.parametrize("path", CANONICAL_CASES, ids=lambda path: path.stem)
def test_the_oracle_agrees_with_every_canonical_case(path: Path) -> None:
    scenario, worked_out_by_a_human = load_canonical_case(path)
    computed = Oracle().expected_outcome(scenario)
    assert worked_out_by_a_human.differences(computed) == []


def test_the_expected_outcome_never_mutates_the_scenario() -> None:
    scenario, _ = load_canonical_case(CANONICAL_CASES[0])
    before = repr(scenario.bank)
    Oracle().expected_outcome(scenario)
    assert repr(scenario.bank) == before
