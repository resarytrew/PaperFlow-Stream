"""Explicit scanning state machine (section 6.4).

The machine is deliberately pure: it receives per-frame observations and
returns a decision. All I/O (saving files, DB writes) lives in the service
layer, which makes the machine trivially unit-testable.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

from app.config import DetectionConfig, StabilityConfig


class ScanState(str, enum.Enum):
    WAITING_EMPTY = "WAITING_EMPTY"
    PAPER_ENTERING = "PAPER_ENTERING"
    PAPER_DETECTED = "PAPER_DETECTED"
    WAITING_STABILITY = "WAITING_STABILITY"
    SELECTING_BEST_FRAME = "SELECTING_BEST_FRAME"
    PROCESSING_FRAME = "PROCESSING_FRAME"
    SCAN_SUCCESS = "SCAN_SUCCESS"
    SCAN_WARNING = "SCAN_WARNING"
    WAITING_REMOVAL = "WAITING_REMOVAL"


#: Large on-screen prompts (section 6.11)
STATE_PROMPTS: dict[ScanState, str] = {
    ScanState.WAITING_EMPTY: "ПОЛОЖИТЕ ЛИСТ",
    ScanState.PAPER_ENTERING: "ПОЛОЖИТЕ ЛИСТ",
    ScanState.PAPER_DETECTED: "НЕ ДВИГАЙТЕ",
    ScanState.WAITING_STABILITY: "НЕ ДВИГАЙТЕ",
    ScanState.SELECTING_BEST_FRAME: "СКАНИРОВАНИЕ",
    ScanState.PROCESSING_FRAME: "СКАНИРОВАНИЕ",
    ScanState.SCAN_SUCCESS: "ЛИСТ ПРИНЯТ",
    ScanState.SCAN_WARNING: "ПОВТОРИТЕ ПОДАЧУ",
    ScanState.WAITING_REMOVAL: "УБЕРИТЕ ЛИСТ",
}

#: Overlay frame colour per state
STATE_COLORS: dict[ScanState, str] = {
    ScanState.WAITING_EMPTY: "neutral",
    ScanState.PAPER_ENTERING: "blue",
    ScanState.PAPER_DETECTED: "blue",
    ScanState.WAITING_STABILITY: "blue",
    ScanState.SELECTING_BEST_FRAME: "green",
    ScanState.PROCESSING_FRAME: "blue",
    ScanState.SCAN_SUCCESS: "green",
    ScanState.SCAN_WARNING: "red",
    ScanState.WAITING_REMOVAL: "amber",
}


@dataclass
class FrameObservation:
    """Everything the machine needs to know about one analysed frame."""

    timestamp_ms: float
    paper_found: bool
    area_ratio: float = 0.0
    diff_ratio: float = 0.0
    motion_score: float = 1.0
    sharpness: float = 0.0
    glare: float = 0.0
    occlusion_answer: float = 0.0
    occlusion_overall: float = 0.0
    corners_visible: bool = False
    touches_border: bool = False
    perspective: float = 0.0
    quality: float = 0.0
    warnings: list[str] = field(default_factory=list)


class DecisionAction(str, enum.Enum):
    NONE = "none"
    COLLECT_CANDIDATE = "collect_candidate"
    PROCESS_BEST = "process_best"
    RESET_CANDIDATES = "reset_candidates"


@dataclass
class Decision:
    state: ScanState
    action: DecisionAction = DecisionAction.NONE
    prompt: str = ""
    color: str = "neutral"
    hints: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    progress: float = 0.0
    changed: bool = False

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "action": self.action.value,
            "prompt": self.prompt,
            "color": self.color,
            "hints": self.hints,
            "blockingReasons": self.blocking_reasons,
            "progress": round(self.progress, 3),
            "changed": self.changed,
        }


@dataclass
class StateTransition:
    at_ms: float
    from_state: str
    to_state: str
    reason: str = ""

    def to_dict(self) -> dict:
        return {"atMs": round(self.at_ms, 1), "from": self.from_state, "to": self.to_state, "reason": self.reason}


class ScanStateMachine:
    """Drives one scanning session."""

    def __init__(self, detection: DetectionConfig, stability: StabilityConfig) -> None:
        self.detection = detection
        self.stability = stability
        self.state: ScanState = ScanState.WAITING_EMPTY
        self.transitions: list[StateTransition] = []
        self._state_entered_ms: float = 0.0
        self._stable_frames: int = 0
        self._empty_frames: int = 0
        self._candidate_count: int = 0
        self._last_warning: str = ""
        self._paused: bool = False

    # ------------------------------------------------------------------ helpers

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False
        self._reset_to(ScanState.WAITING_EMPTY, time.monotonic() * 1000.0, "resumed")

    def reset(self, now_ms: float | None = None) -> None:
        self._reset_to(ScanState.WAITING_EMPTY, now_ms if now_ms is not None else time.monotonic() * 1000.0, "reset")

    def _reset_to(self, state: ScanState, now_ms: float, reason: str) -> None:
        self._transition(state, now_ms, reason)
        self._stable_frames = 0
        self._empty_frames = 0
        self._candidate_count = 0

    def _transition(self, new_state: ScanState, now_ms: float, reason: str = "") -> bool:
        if new_state == self.state:
            return False
        self.transitions.append(
            StateTransition(at_ms=now_ms, from_state=self.state.value, to_state=new_state.value, reason=reason)
        )
        if len(self.transitions) > 400:
            del self.transitions[:200]
        self.state = new_state
        self._state_entered_ms = now_ms
        return True

    def _elapsed(self, now_ms: float) -> float:
        return max(0.0, now_ms - self._state_entered_ms)

    def _stability_blockers(self, obs: FrameObservation) -> list[str]:
        reasons: list[str] = []
        if obs.motion_score > self.stability.motion_threshold:
            reasons.append("motion")
        if obs.occlusion_answer > self.stability.max_hand_overlap:
            reasons.append("hand")
        if not obs.corners_visible:
            reasons.append("corners")
        if obs.sharpness < self.stability.min_sharpness:
            reasons.append("sharpness")
        if obs.glare > self.stability.max_glare:
            reasons.append("glare")
        if obs.touches_border:
            reasons.append("out_of_bounds")
        return reasons

    @staticmethod
    def _hints(reasons: list[str]) -> list[str]:
        mapping = {
            "motion": "Лист движется — придержите и уберите руку",
            "hand": "Рука перекрывает область ответа",
            "corners": "Видны не все четыре угла листа",
            "sharpness": "Изображение размыто — подождите фокусировки",
            "glare": "Сильный блик на листе — измените освещение",
            "out_of_bounds": "Лист выходит за границы кадра",
        }
        return [mapping[r] for r in reasons if r in mapping]

    # -------------------------------------------------------------------- main

    def update(self, obs: FrameObservation) -> Decision:
        """Advance the machine by one observed frame."""
        now = obs.timestamp_ms
        if not self.transitions and self._state_entered_ms == 0.0:
            self._state_entered_ms = now

        if self._paused:
            return self._decide(ScanState.WAITING_EMPTY, DecisionAction.NONE, changed=False, prompt="ПАУЗА")

        changed = False
        action = DecisionAction.NONE
        blockers: list[str] = []
        progress = 0.0

        area_ok = obs.paper_found and obs.area_ratio >= self.detection.min_area_ratio
        area_present = obs.paper_found or obs.diff_ratio >= self.detection.entering_diff_ratio
        area_empty = (not obs.paper_found) and obs.diff_ratio < self.detection.empty_diff_ratio

        if self.state == ScanState.WAITING_EMPTY:
            if area_ok:
                changed = self._transition(ScanState.PAPER_DETECTED, now, "paper_detected")
            elif area_present:
                changed = self._transition(ScanState.PAPER_ENTERING, now, "change_detected")

        elif self.state == ScanState.PAPER_ENTERING:
            if area_ok:
                changed = self._transition(ScanState.PAPER_DETECTED, now, "paper_detected")
            elif area_empty:
                changed = self._transition(ScanState.WAITING_EMPTY, now, "area_cleared")

        elif self.state == ScanState.PAPER_DETECTED:
            if area_ok:
                changed = self._transition(ScanState.WAITING_STABILITY, now, "await_stability")
                self._stable_frames = 0
            elif area_empty:
                changed = self._transition(ScanState.WAITING_EMPTY, now, "paper_lost")

        elif self.state == ScanState.WAITING_STABILITY:
            if area_empty:
                self._stable_frames = 0
                changed = self._transition(ScanState.WAITING_EMPTY, now, "paper_removed")
            elif not area_ok:
                self._stable_frames = 0
                blockers = ["corners"]
            else:
                blockers = self._stability_blockers(obs)
                if blockers:
                    self._stable_frames = 0
                else:
                    self._stable_frames += 1
                progress = min(1.0, self._stable_frames / max(self.stability.stable_frames_required, 1))
                enough_frames = self._stable_frames >= self.stability.stable_frames_required
                enough_time = self._elapsed(now) >= self.stability.stability_duration_ms
                if enough_frames and enough_time:
                    self._candidate_count = 0
                    changed = self._transition(ScanState.SELECTING_BEST_FRAME, now, "stable")
                    action = DecisionAction.RESET_CANDIDATES

        elif self.state == ScanState.SELECTING_BEST_FRAME:
            elapsed = self._elapsed(now)
            progress = min(1.0, elapsed / max(self.stability.candidate_window_ms, 1))
            if area_empty:
                changed = self._transition(ScanState.WAITING_EMPTY, now, "paper_removed_early")
                self._candidate_count = 0
            elif not area_ok:
                blockers = ["corners"]
                if elapsed > self.stability.candidate_window_ms * 2:
                    changed = self._transition(ScanState.WAITING_STABILITY, now, "lost_during_selection")
            else:
                blockers = self._stability_blockers(obs)
                if not blockers:
                    action = DecisionAction.COLLECT_CANDIDATE
                    self._candidate_count += 1
                window_done = elapsed >= self.stability.candidate_window_ms
                enough = self._candidate_count >= self.stability.max_candidates
                if (window_done and self._candidate_count > 0) or enough:
                    action = DecisionAction.PROCESS_BEST
                    changed = self._transition(ScanState.PROCESSING_FRAME, now, "window_complete")
                elif window_done and self._candidate_count == 0:
                    changed = self._transition(ScanState.WAITING_STABILITY, now, "no_candidates")

        elif self.state == ScanState.PROCESSING_FRAME:
            # left by explicit notify_* calls from the service layer
            progress = 0.5

        elif self.state in (ScanState.SCAN_SUCCESS, ScanState.SCAN_WARNING):
            hold = (
                self.stability.success_hold_ms
                if self.state == ScanState.SCAN_SUCCESS
                else self.stability.warning_hold_ms
            )
            if self._elapsed(now) >= hold:
                if area_empty:
                    self._empty_frames += 1
                    if self._empty_frames >= self.stability.removal_frames_required:
                        changed = self._transition(ScanState.WAITING_EMPTY, now, "ready_for_next")
                        self._empty_frames = 0
                else:
                    self._empty_frames = 0
                    changed = self._transition(ScanState.WAITING_REMOVAL, now, "await_removal")

        elif self.state == ScanState.WAITING_REMOVAL:
            # Hard guard against re-scanning the same physical sheet.
            if area_empty:
                self._empty_frames += 1
                progress = min(1.0, self._empty_frames / max(self.stability.removal_frames_required, 1))
                if self._empty_frames >= self.stability.removal_frames_required:
                    changed = self._transition(ScanState.WAITING_EMPTY, now, "sheet_removed")
                    self._empty_frames = 0
            else:
                self._empty_frames = 0
                blockers = ["sheet_still_present"]

        return self._decide(self.state, action, changed, blockers=blockers, progress=progress)

    def _decide(
        self,
        state: ScanState,
        action: DecisionAction,
        changed: bool,
        *,
        blockers: list[str] | None = None,
        progress: float = 0.0,
        prompt: str | None = None,
    ) -> Decision:
        blockers = blockers or []
        hints = self._hints(blockers)
        resolved_prompt = prompt if prompt is not None else STATE_PROMPTS[state]
        if state == ScanState.WAITING_STABILITY and "hand" in blockers:
            resolved_prompt = "УБЕРИТЕ РУКУ"
        return Decision(
            state=state,
            action=action,
            prompt=resolved_prompt,
            color=STATE_COLORS[state],
            hints=hints,
            blocking_reasons=blockers,
            progress=progress,
            changed=changed,
        )

    # ------------------------------------------------------- external outcomes

    def notify_success(self, now_ms: float | None = None) -> Decision:
        now = now_ms if now_ms is not None else time.monotonic() * 1000.0
        self._transition(ScanState.SCAN_SUCCESS, now, "scan_saved")
        self._empty_frames = 0
        self._candidate_count = 0
        return self._decide(self.state, DecisionAction.NONE, True)

    def notify_warning(self, reason: str, now_ms: float | None = None) -> Decision:
        now = now_ms if now_ms is not None else time.monotonic() * 1000.0
        self._last_warning = reason
        self._transition(ScanState.SCAN_WARNING, now, reason)
        self._empty_frames = 0
        self._candidate_count = 0
        decision = self._decide(self.state, DecisionAction.NONE, True)
        decision.blocking_reasons = [reason]
        return decision

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "paused": self._paused,
            "stableFrames": self._stable_frames,
            "candidateCount": self._candidate_count,
            "lastWarning": self._last_warning,
            "transitions": [t.to_dict() for t in self.transitions[-40:]],
        }
