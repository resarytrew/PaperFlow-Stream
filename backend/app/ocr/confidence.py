"""Confidence bucketing and preliminary (non-grading) keyword analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.config import OcrConfig
from app.ocr.provider import RecognitionOutput


class ConfidenceLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


@dataclass
class ConfidenceVerdict:
    level: ConfidenceLevel
    overall: float
    needs_review: bool
    reasons: list[str] = field(default_factory=list)
    weakest_line: int | None = None
    weakest_token: str | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "overall": round(self.overall, 4),
            "needsReview": self.needs_review,
            "reasons": self.reasons,
            "weakestLine": self.weakest_line,
            "weakestToken": self.weakest_token,
        }


def classify_confidence(output: RecognitionOutput, config: OcrConfig) -> ConfidenceVerdict:
    """Route a recognition result into the green / yellow / red queue.

    Beyond the mean confidence, a single critically weak token is enough to send
    the work to manual review (section 7.5).
    """
    overall = float(output.overall_confidence)

    if overall >= config.high_confidence:
        level = ConfidenceLevel.high
    elif overall >= config.low_confidence:
        level = ConfidenceLevel.medium
    else:
        level = ConfidenceLevel.low

    reasons: list[str] = []
    needs_review = level is not ConfidenceLevel.high

    if level is ConfidenceLevel.medium:
        reasons.append(f"Средняя уверенность {overall:.0%}")
    elif level is ConfidenceLevel.low:
        reasons.append(f"Низкая уверенность {overall:.0%}")

    # Weakest line / token analysis.
    weakest_line: int | None = None
    weakest_token: str | None = None
    lowest_line_conf = 1.0
    lowest_token_conf = 1.0

    for line in output.lines:
        if line.confidence < lowest_line_conf:
            lowest_line_conf = line.confidence
            weakest_line = line.index
        for token in line.token_confidences:
            if token.confidence < lowest_token_conf:
                lowest_token_conf = token.confidence
                weakest_token = token.text

    if output.lines and lowest_token_conf < config.critical_token_confidence:
        needs_review = True
        reasons.append(
            f"Слово «{weakest_token}» распознано с уверенностью {lowest_token_conf:.0%}"
        )

    if output.lines and lowest_line_conf < config.critical_token_confidence:
        needs_review = True
        if f"Строка {weakest_line}" not in " ".join(reasons):
            reasons.append(f"Строка {(weakest_line or 0) + 1} ненадёжна ({lowest_line_conf:.0%})")

    if not output.lines and not output.is_blank:
        needs_review = True
        reasons.append("Текст не найден")

    if output.warnings:
        # Model warnings never auto-fail, but they are surfaced to the teacher.
        reasons.extend(w for w in output.warnings if "MOCK" not in w)

    return ConfidenceVerdict(
        level=level,
        overall=overall,
        needs_review=needs_review,
        reasons=reasons[:6],
        weakest_line=weakest_line,
        weakest_token=weakest_token,
    )


# ------------------------------------------------------- preliminary analysis

_DATE_RE = re.compile(r"\b(1[0-9]{3}|20[0-2][0-9])\b")
_CAPITALISED_RE = re.compile(r"\b[А-ЯЁ][а-яё]{2,}\b")


def analyse_text(text: str, expected_answer: str = "", enabled: bool = True) -> dict:
    """Experimental, clearly-labelled preliminary analysis (section 7.9).

    Highlights keywords, dates and capitalised words. It **never** produces a
    grade — the teacher always decides.
    """
    if not enabled or not text.strip():
        return {"enabled": False}

    lowered = text.lower()
    keywords = [
        word.strip(" .,;:!?()").lower()
        for word in re.split(r"[\s,;]+", expected_answer)
        if len(word.strip(" .,;:!?()")) >= 4
    ]
    unique_keywords = sorted({k for k in keywords if k})

    found = [k for k in unique_keywords if k in lowered]
    missing = [k for k in unique_keywords if k not in lowered]

    return {
        "enabled": True,
        "preliminary": True,
        "disclaimer": "Предварительный анализ. Оценку выставляет только учитель.",
        "keywordsFound": found,
        "keywordsMissing": missing[:12],
        "keywordCoverage": (round(len(found) / len(unique_keywords), 3) if unique_keywords else None),
        "dates": sorted(set(_DATE_RE.findall(text))),
        "properNouns": sorted(set(_CAPITALISED_RE.findall(text)))[:12],
        "wordCount": len([w for w in re.split(r"\s+", text.strip()) if w]),
        "charCount": len(text.strip()),
    }
