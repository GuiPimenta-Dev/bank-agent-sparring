from sparring.evaluation.narrative import uncited_details

FACTS = {
    "customer number": "C-4821",
    "date of birth": "1984-01-02",
    "postcode": "M1 1AE",
    "debit card": "ends in 1881",
    "the payments to dispute": "£1,200.00 to Emirates on 10 October 2026; £800.01 to Hilton, "
    "still pending",
}


def test_a_story_that_repeats_only_the_facts_is_kept() -> None:
    story = (
        "You cancelled a trip in time, yet Emirates took £1,200.00 from the card ending 1881 "
        "on 10 October 2026, and Hilton is taking £800.01 too. You are furious."
    )
    assert uncited_details(story, FACTS) == []


def test_numbers_compare_by_value_whatever_their_format() -> None:
    assert uncited_details("Emirates took 1200 pounds, then £800.01 more.", FACTS) == []


def test_a_story_with_no_details_at_all_is_kept() -> None:
    assert uncited_details("You are worried and want this sorted today.", FACTS) == []


def test_an_invented_amount_date_or_card_number_is_caught() -> None:
    story = "On 3 October Emirates took £1,250.00 from the card ending 4242."
    assert uncited_details(story, FACTS) == ["3", "1,250.00", "4242"]


def test_an_invented_id_is_caught_even_without_digits_to_check() -> None:
    assert uncited_details("The app shows tx-emirates and card-ben-debit.", FACTS) == [
        "tx-emirates",
        "card-ben-debit",
    ]
