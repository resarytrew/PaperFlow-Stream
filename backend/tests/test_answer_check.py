"""Unit tests for the answer comparison hint (app.ocr.answer_check)."""

from __future__ import annotations

from fractions import Fraction

import pytest

from app.ocr.answer_check import compare_answers, extract_numbers, normalize_answer


# ------------------------------------------------------------- normalization


class TestNormalizeAnswer:
    def test_empty(self):
        assert normalize_answer("") == ""
        assert normalize_answer("   ") == ""

    def test_case_and_spaces(self):
        assert normalize_answer("X = 7") == normalize_answer("x=7")

    def test_cyrillic_lookalikes(self):
        # OCR often reads Latin "x" as Cyrillic "х" and vice versa
        assert normalize_answer("х = 7") == normalize_answer("x = 7")
        assert normalize_answer("АВС") == normalize_answer("abc")

    def test_decimal_comma(self):
        assert normalize_answer("3,14") == normalize_answer("3.14")

    def test_comma_between_words_kept(self):
        # перечисление не должно превращаться в десятичную точку
        assert "," in normalize_answer("Москва, Париж")

    def test_unicode_minus_and_dashes(self):
        assert normalize_answer("−5") == normalize_answer("-5")
        assert normalize_answer("–5") == normalize_answer("-5")

    def test_answer_prefix_stripped(self):
        assert normalize_answer("Ответ: 42") == normalize_answer("42")
        assert normalize_answer("otvet: 42") == normalize_answer("42")

    def test_trailing_punctuation(self):
        assert normalize_answer("42.") == normalize_answer("42")
        assert normalize_answer("Париж!") == normalize_answer("Париж")


# ------------------------------------------------------------------- numbers


class TestExtractNumbers:
    def test_integers_and_decimals(self):
        assert extract_numbers("x = 7, y = 3.5") == [Fraction(7), Fraction(7, 2)]

    def test_decimal_comma(self):
        assert extract_numbers("0,5") == [Fraction(1, 2)]

    def test_fraction(self):
        assert extract_numbers("1/2") == [Fraction(1, 2)]

    def test_negative(self):
        assert extract_numbers("-3") == [Fraction(-3)]

    def test_none(self):
        assert extract_numbers("Крымская война") == []

    def test_division_by_zero_ignored(self):
        assert extract_numbers("1/0") == []


# ------------------------------------------------------------------ verdicts


class TestCompareAnswers:
    def test_no_expected_answer(self):
        assert compare_answers("42", "")["verdict"] == "unknown"

    def test_no_recognized_text(self):
        assert compare_answers("", "42")["verdict"] == "unknown"

    def test_exact_match(self):
        result = compare_answers("x = 7", "x=7")
        assert result["verdict"] == "match"
        assert result["editDistance"] == 0

    def test_cyrillic_x_match(self):
        assert compare_answers("х=7", "x = 7")["verdict"] == "match"

    def test_numeric_equivalence(self):
        assert compare_answers("0,5", "1/2")["verdict"] == "match"
        assert compare_answers("Ответ: 0.5", "1/2")["verdict"] == "match"

    def test_multiple_roots_order_insensitive(self):
        result = compare_answers("x1 = 2, x2 = -3", "x1=-3, x2=2")
        # same significant numbers {2, -3, 1, 2} on both sides after indices...
        assert result["verdict"] in ("match", "likely")

    def test_single_ocr_slip_is_likely(self):
        # 'Крымская' recognized with one wrong letter
        result = compare_answers("Крымсная война", "Крымская война")
        assert result["verdict"] == "likely"
        assert result["editDistance"] == 1

    def test_clear_mismatch(self):
        assert compare_answers("x = 9", "x = 7")["verdict"] == "mismatch"
        assert compare_answers("Берлин", "Париж")["verdict"] == "mismatch"

    def test_short_answers_are_strict(self):
        # for a 1-2 char expected answer even distance 2 must not be "likely"
        assert compare_answers("19", "42")["verdict"] == "mismatch"

    def test_expected_numbers_subset_is_likely(self):
        result = compare_answers("получилось 7 см приблизительно", "7")
        assert result["verdict"] == "likely"

    def test_date_answer(self):
        assert compare_answers("1861 год", "1861")["verdict"] == "likely"
        assert compare_answers("1961", "1861")["verdict"] == "mismatch"

    def test_result_shape(self):
        result = compare_answers("x=7", "x=7")
        assert set(result) >= {
            "verdict",
            "expectedNormalized",
            "recognizedNormalized",
            "numericMatch",
            "editDistance",
            "disclaimer",
        }


# ---------------------------------------------------------------- edge cases


@pytest.mark.parametrize(
    ("recognized", "expected", "verdict"),
    [
        ("ЛИСТ ПРИНЯТ", "x=7", "mismatch"),  # noise text
        ("x = 7.0", "x = 7", "match"),  # 7.0 == 7
        ("7", "x = 7", "likely"),  # bare number vs equation
        ("III", "3", "mismatch"),  # roman numerals are not parsed - honest mismatch
    ],
)
def test_verdict_matrix(recognized: str, expected: str, verdict: str):
    assert compare_answers(recognized, expected)["verdict"] == verdict
