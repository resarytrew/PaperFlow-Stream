"""Background OCR queue (section 7.4).

A small asyncio worker pool. Scanning is never blocked: sheets are queued and
processed with bounded concurrency so a laptop stays responsive. Failures are
recorded per sheet and never stop the other jobs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from app.config import RuntimeConfig
from app.db import SessionLocal
from app.models import RecognitionResult, RecognitionStatus, ScannedSheet, Task
from app.ocr.answer_check import compare_answers
from app.ocr.confidence import analyse_text, classify_confidence
from app.ocr.provider import RecognitionOutput
from app.ocr.providers import get_provider
from app.services.events import OCR_TOPIC, hub, session_topic
from app.services.settings_service import load_config
from app.services.storage import get_storage

logger = logging.getLogger(__name__)


@dataclass
class QueueStats:
    queued: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    last_error: str = ""
    history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "queued": self.queued,
            "processing": self.processing,
            "completed": self.completed,
            "failed": self.failed,
            "lastError": self.last_error,
            "history": self.history[-20:],
        }


class OcrQueue:
    """Bounded-concurrency async worker pool."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[int] | None = None
        self._workers: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._running = False
        self.stats = QueueStats()
        self._in_flight: set[int] = set()

    # ---------------------------------------------------------- lifecycle

    async def start(self, concurrency: int = 2) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"ocr-worker-{i}") for i in range(max(1, concurrency))
        ]
        logger.info("OCR queue started with %s worker(s)", concurrency)

    async def stop(self) -> None:
        self._running = False
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._workers = []
        logger.info("OCR queue stopped")

    # ------------------------------------------------------------- enqueue

    def enqueue(self, sheet_id: int) -> bool:
        """Thread-safe enqueue (callable from worker threads)."""
        if not self._running or self._queue is None or self._loop is None:
            logger.warning("OCR queue not running – sheet %s left pending", sheet_id)
            self._mark_pending(sheet_id)
            return False
        if sheet_id in self._in_flight:
            return False

        self._mark_pending(sheet_id)
        self._in_flight.add(sheet_id)
        self.stats.queued += 1
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, sheet_id)
        except RuntimeError as exc:  # pragma: no cover - loop shutting down
            logger.warning("could not enqueue sheet %s: %s", sheet_id, exc)
            self._in_flight.discard(sheet_id)
            self.stats.queued -= 1
            return False
        return True

    @staticmethod
    def _mark_pending(sheet_id: int) -> None:
        """Create/reset the RecognitionResult row so the UI shows the job."""
        try:
            with SessionLocal() as db:
                result = db.execute(
                    select(RecognitionResult).where(RecognitionResult.scanned_sheet_id == sheet_id)
                ).scalar_one_or_none()
                if result is None:
                    result = RecognitionResult(
                        scanned_sheet_id=sheet_id, status=RecognitionStatus.pending.value
                    )
                    db.add(result)
                else:
                    result.status = RecognitionStatus.pending.value
                    result.error_message = None
                db.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning("could not mark sheet %s pending: %s", sheet_id, exc)

    # -------------------------------------------------------------- worker

    async def _worker(self, index: int) -> None:
        assert self._queue is not None
        while self._running:
            try:
                sheet_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            self.stats.queued = max(0, self.stats.queued - 1)
            try:
                assert self._semaphore is not None
                async with self._semaphore:
                    self.stats.processing += 1
                    await self._process(sheet_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # a single failure must not kill the worker
                logger.exception("OCR worker %s crashed on sheet %s", index, sheet_id)
                self.stats.failed += 1
                self.stats.last_error = str(exc)
            finally:
                self.stats.processing = max(0, self.stats.processing - 1)
                self._in_flight.discard(sheet_id)
                self._queue.task_done()

    async def _process(self, sheet_id: int) -> None:
        """Run the full OCR pipeline for one sheet."""
        started = time.perf_counter()

        with SessionLocal() as db:
            sheet = db.get(ScannedSheet, sheet_id)
            if sheet is None:
                logger.warning("sheet %s vanished before OCR", sheet_id)
                return
            config: RuntimeConfig = load_config(db)
            session_id = sheet.session_id
            crop_path = sheet.answer_crop_path or sheet.normalized_image_path
            expected_answer = ""
            if sheet.task_id:
                task = db.get(Task, sheet.task_id)
                if task:
                    expected_answer = task.expected_answer or ""
            result = db.execute(
                select(RecognitionResult).where(RecognitionResult.scanned_sheet_id == sheet_id)
            ).scalar_one_or_none()
            if result is None:
                result = RecognitionResult(scanned_sheet_id=sheet_id)
                db.add(result)
            result.status = RecognitionStatus.preprocessing.value
            result.attempts = (result.attempts or 0) + 1
            attempts = result.attempts
            db.commit()

        await self._publish(session_id, sheet_id, RecognitionStatus.preprocessing.value)

        if not crop_path:
            await self._finish_failed(sheet_id, session_id, "У листа нет изображения области ответа")
            return

        try:
            absolute = get_storage().absolute(crop_path)
        except Exception as exc:
            await self._finish_failed(sheet_id, session_id, f"Файл недоступен: {exc}")
            return

        provider_name = config.ocr.provider
        try:
            provider = get_provider(provider_name)
        except Exception as exc:
            await self._finish_failed(sheet_id, session_id, f"Провайдер «{provider_name}» недоступен: {exc}")
            return

        await self._set_status(sheet_id, RecognitionStatus.recognizing.value)
        await self._publish(session_id, sheet_id, RecognitionStatus.recognizing.value)

        try:
            output: RecognitionOutput = await provider.recognize(str(absolute), config.ocr.language)
        except Exception as exc:
            logger.warning("OCR failed for sheet %s: %s", sheet_id, exc)
            retry = attempts <= config.ocr.max_retries
            await self._finish_failed(
                sheet_id, session_id, f"{type(exc).__name__}: {exc}", retryable=retry
            )
            self.stats.failed += 1
            self.stats.last_error = str(exc)
            if retry:
                await asyncio.sleep(0.5)
                self.enqueue(sheet_id)
            return

        verdict = classify_confidence(output, config.ocr)
        analysis = analyse_text(output.text, expected_answer, config.ocr.keyword_analysis)
        analysis["confidence"] = verdict.to_dict()
        # Answer hint: recognized text vs the task's expected answer.
        # Blank sheets get an explicit mismatch only when an answer was expected.
        if expected_answer:
            analysis["answerCheck"] = compare_answers(
                "" if output.is_blank else output.text, expected_answer
            )

        if output.is_blank:
            status = RecognitionStatus.blank.value
        elif verdict.needs_review:
            status = RecognitionStatus.needs_review.value
        else:
            status = RecognitionStatus.recognized.value

        elapsed = int((time.perf_counter() - started) * 1000)

        with SessionLocal() as db:
            result = db.execute(
                select(RecognitionResult).where(RecognitionResult.scanned_sheet_id == sheet_id)
            ).scalar_one_or_none()
            if result is None:  # pragma: no cover
                result = RecognitionResult(scanned_sheet_id=sheet_id)
                db.add(result)
            result.recognized_text = output.text
            result.provider = output.provider
            result.model_name = output.model_name
            result.overall_confidence = output.overall_confidence
            result.line_results_json = [line.to_dict() for line in output.lines]
            result.warnings = output.warnings
            result.analysis_json = analysis
            result.preprocess_variant = output.preprocess_variant
            result.processing_time_ms = output.processing_time_ms or elapsed
            result.status = status
            result.error_message = None
            db.commit()

        self.stats.completed += 1
        self.stats.history.append(
            {
                "sheetId": sheet_id,
                "status": status,
                "confidence": round(output.overall_confidence, 3),
                "ms": output.processing_time_ms or elapsed,
            }
        )
        await self._publish(session_id, sheet_id, status, confidence=output.overall_confidence)

    # ------------------------------------------------------------- helpers

    @staticmethod
    async def _set_status(sheet_id: int, status: str) -> None:
        def _update() -> None:
            with SessionLocal() as db:
                result = db.execute(
                    select(RecognitionResult).where(RecognitionResult.scanned_sheet_id == sheet_id)
                ).scalar_one_or_none()
                if result is not None:
                    result.status = status
                    db.commit()

        await asyncio.to_thread(_update)

    async def _finish_failed(
        self, sheet_id: int, session_id: int | None, message: str, retryable: bool = False
    ) -> None:
        def _update() -> None:
            with SessionLocal() as db:
                result = db.execute(
                    select(RecognitionResult).where(RecognitionResult.scanned_sheet_id == sheet_id)
                ).scalar_one_or_none()
                if result is None:
                    result = RecognitionResult(scanned_sheet_id=sheet_id)
                    db.add(result)
                result.status = (
                    RecognitionStatus.pending.value if retryable else RecognitionStatus.failed.value
                )
                result.error_message = message[:2000]
                db.commit()

        await asyncio.to_thread(_update)
        logger.error("OCR sheet %s failed: %s", sheet_id, message)
        await self._publish(session_id, sheet_id, RecognitionStatus.failed.value, error=message)

    @staticmethod
    async def _publish(session_id: int | None, sheet_id: int, status: str, **extra) -> None:
        message = {"type": "ocr_status", "sheetId": sheet_id, "status": status, **extra}
        try:
            if session_id is not None:
                await hub.publish(session_topic(session_id), message)
            await hub.publish(OCR_TOPIC, message)
        except Exception:  # pragma: no cover
            logger.debug("could not publish OCR status")

    def snapshot(self) -> dict:
        return {
            "running": self._running,
            "workers": len(self._workers),
            "pending": self._queue.qsize() if self._queue else 0,
            **self.stats.to_dict(),
        }


ocr_queue = OcrQueue()
