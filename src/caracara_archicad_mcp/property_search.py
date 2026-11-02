"""Find property definitions by a name the user remembers, without listing all of them.

Archicad has about 1,600 built-in properties besides the project's own. The search compares
the query with each property's name, and with its group and name together ("Fire Rating"
finds a custom "Fire rating" in the group "Fire safety"), and tolerates small typos and other
word orders. Plain Python (difflib); nothing is kept between searches.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Scores at or above this count as a match; below it the names share too little.
MIN_SCORE = 0.6


def normalize(text: str) -> str:
    """Lowercase words without punctuation: 'Fire-Rating (EI)' -> 'fire rating ei'."""
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def score(query: str, name: str, group: str = "") -> float:
    """How well a property named `name` in `group` matches the query, from 0 to 1.

    1 for the same name, 0.9 when the name contains the query, 0.85 when group and name do;
    otherwise up to 0.8 for close spellings or the query's words found in any order.
    """
    wanted, named, full = normalize(query), normalize(name), normalize(f"{group} {name}")
    if not wanted or not named:
        return 0.0
    if wanted == named:
        return 1.0
    if wanted in named:
        return 0.9
    if wanted in full:
        return 0.85
    words = full.split()
    by_word = sum(max(_ratio(word, other) for other in words) for word in wanted.split()) / len(wanted.split())
    return 0.8 * max(by_word, _ratio(wanted, named))
