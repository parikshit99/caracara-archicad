"""The property name search: what a user calls a property finds it, and unrelated names do not."""

import pytest

from caracara_archicad_mcp.property_search import MIN_SCORE, normalize, score


def test_names_are_compared_as_lowercase_words():
    assert normalize("Fire-Rating (EI)") == "fire rating ei"
    assert normalize("  ") == ""


@pytest.mark.parametrize(
    ("query", "name", "group", "expected"),
    [
        ("Fire Rating", "fire rating", "", 1.0),
        ("rating", "Fire Rating", "", 0.9),
        ("fire safety rating", "Rating", "Fire Safety", 0.85),
    ],
)
def test_whole_and_partial_names_score_highest(query, name, group, expected):
    assert score(query, name, group) == expected


@pytest.mark.parametrize(
    ("query", "name", "group"),
    [
        ("fire raitng", "Fire Rating", "Fire safety"),  # a typo
        ("rating fire", "Fire Rating", ""),  # another word order
        ("fire resistance", "Fire Resistance Rating", "Fire safety"),
        ("u value", "U-value", "Thermal"),
    ],
)
def test_close_names_match(query, name, group):
    assert score(query, name, group) >= MIN_SCORE


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("fire rating", "Net Volume"),
        ("cost code", "Element ID"),
        ("area", "Zone Category Code"),
    ],
)
def test_unrelated_names_do_not_match(query, name):
    assert score(query, name, "General Ratings") < MIN_SCORE


def test_an_empty_query_or_name_matches_nothing():
    assert score("", "Fire Rating") == 0.0
    assert score("fire", "") == 0.0
