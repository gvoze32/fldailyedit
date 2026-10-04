"""Shared text folding for comparing names across transfer sources."""

from __future__ import annotations

import unicodedata
from functools import lru_cache


_UNDECOMPOSED_LETTERS = str.maketrans(
    {
        "ı": "i",
        "Ł": "L",
        "ł": "l",
        "Đ": "D",
        "đ": "d",
        "Ð": "D",
        "ð": "d",
        "Þ": "Th",
        "þ": "th",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "Ø": "O",
        "ø": "o",
        "Ħ": "H",
        "ħ": "h",
        "Ŧ": "T",
        "ŧ": "t",
        "Ŋ": "N",
        "ŋ": "n",
        "ĸ": "k",
    }
)


@lru_cache(maxsize=1 << 18)
def fold_text(value: str | None) -> str:
    """
    Fold a human-readable name into its comparison form.

    Strips diacritics, transliterates letters NFKD does not decompose
    (ł → l, ø → o, ı → i), casefolds, and collapses whitespace, so every
    source compares 'Łukasz Fabiański' and 'Lukasz Fabianski' as equal.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.translate(_UNDECOMPOSED_LETTERS).casefold().split())
