"""Answer comparison: recognized text vs the task's expected answer.

Design constraints (section 7.9 of the spec):

* This is a *hint* for the teacher, never a grade. The verdict is shown
  with an explicit disclaimer and the teacher always decides.
* OCR of Russian handwriting is weak, so the comparison must be robust to
  the typical OCR confusions: Cyrillic/Latin lookalike letters, decimal
  comma vs dot, stray spaces, case, ``х = 7`` vs ``x=7``.
* Works best for short factual answers (math results, dates, single
  words) — exactly the kind of tasks PaperFlow forms are designed for.

Verdicts:

``match``      normalized texts are equal, or they are numerically equal
``likely``     small edit distance or the significant numbers coincide
``mismatch``   an expected answer exists and clearly differs
``unknown``    no expected answer configured, or nothing was recognized
"""

from __future__ import annotations

import re
from fractions import Fraction

# Cyrillic letters that OCR routinely swaps with visually identical Latin
# ones (both directions). Mapping everything to Latin makes "х=7" == "x=7".
_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m",
        "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
        "х": "x", "А": "a", "В": "b", "Е": "e", "Ё": "e", "К": "k",
        "М": "m", "Н": "h", "О": "o", "Р": "p", "С": "c", "Т": "t",
        "У": "y", "Х": "x",
    }
)

# Unicode punctuation OCR likes to produce.
_PUNCT_NORMALISE = str.maketrans(
    {
        "−": "-", "–": "-", "—": "-", "±": "+-",
        "×": "*", "·": "*", "÷": "/", "∕": "/", "⁄": "/",
        "，": ",", "。": ".", "«": "", "»": "", '"': "", "'": "",
        "`": "", "´": "", "’": "", "‘": "",
    }
)

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?(?:/\d+)?")


def normalize_answer(text: str, *, map_lookalikes: bool = True) -> str:
    """Bring an answer to a canonical comparable form."""
    value = (text or "").strip().lower()
    if not value:
        return ""
    value = value.translate(_PUNCT_NORMALISE)
    if map_lookalikes:
        value = value.translate(_CYRILLIC_TO_LATIN)
    # decimal comma → dot, but only between digits (не трогаем перечисления)
    value = re.sub(r"(?<=\d),(?=\d)", ".", value)
    # collapse whitespace entirely: "x = 7" -> "x=7"
    value = re.sub(r"\s+", "", value)
    # drop trailing sentence punctuation
    value = value.rstrip(".;:!")
    # unify "ответ:" prefixes the model may pick up from the form itself
    # (after the lookalike mapping "ответ" becomes "otbet")
    value = re.sub(r"^(otvet|otbet|ответ)[:=-]*", "", value)
    return value


def _residue(text: str) -> str:
    """Text with all numbers stripped — what surrounds the numeric answer."""
    return _NUMBER_RE.sub("", text).strip(",;")


def extract_numbers(text: str) -> list[Fraction]:
    """All numeric values in the text as exact fractions (12, 3.5, 1/2)."""
    numbers: list[Fraction] = []
    for raw in _NUMBER_RE.findall((text or "").replace(",", ".")):
        try:
            if "/" in raw:
                num, den = raw.split("/", 1)
                if float(den) == 0:
                    continue
                numbers.append(Fraction(num) / Fraction(den))
            else:
                numbers.append(Fraction(raw))
        except (ValueError, ZeroDivisionError):
            continue
    return numbers


def _levenshtein(a: str, b: str, limit: int = 3) -> int:
    """Edit distance with an early-exit cap (answers are short strings)."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        best = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return previous[-1]


def compare_answers(recognized: str, expected: str) -> dict:
    """Compare recognized text with the expected answer.

    Returns a JSON-ready dict with the verdict and the evidence used, so the
    review screen can explain *why* the hint was produced.
    """
    expected_norm = normalize_answer(expected)
    recognized_norm = normalize_answer(recognized)

    result = {
        "verdict": "unknown",
        "expectedNormalized": expected_norm,
        "recognizedNormalized": recognized_norm,
        "numericMatch": None,
        "editDistance": None,
        "disclaimer": "Подсказка для учителя, не оценка.",
    }

    if not expected_norm or not recognized_norm:
        return result

    if recognized_norm == expected_norm:
        result["verdict"] = "match"
        result["editDistance"] = 0
        return result

    # Numeric comparison dominates for numeric answers: "x=9" vs "x=7" is a
    # different answer, not an OCR slip, even though the edit distance is 1.
    expected_numbers = extract_numbers(expected)
    recognized_numbers = extract_numbers(recognized)
    if expected_numbers and recognized_numbers:
        expected_set = set(expected_numbers)
        recognized_set = set(recognized_numbers)
        if expected_set == recognized_set and len(expected_numbers) == len(recognized_numbers):
            result["numericMatch"] = True
            # Same numbers; "match" only when the surrounding text agrees too
            # ("x=7.0" vs "x=7"), otherwise a cautious "likely" ("1861 год").
            same_context = _residue(recognized_norm) == _residue(expected_norm)
            result["verdict"] = "match" if same_context else "likely"
            return result
        if expected_set <= recognized_set:
            # expected numbers present among extra noise — possibly correct
            result["numericMatch"] = True
            result["verdict"] = "likely"
            return result
        result["numericMatch"] = False
        result["verdict"] = "mismatch"
        return result

    # Tolerate a single OCR slip in short text answers.
    distance = _levenshtein(recognized_norm, expected_norm, limit=2)
    result["editDistance"] = distance if distance <= 2 else None
    threshold = 1 if len(expected_norm) <= 6 else 2
    if distance <= threshold:
        result["verdict"] = "likely"
        return result

    result["verdict"] = "mismatch"
    return result
