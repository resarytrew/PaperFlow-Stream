"""A4 PDF generator for personalised answer forms with QR codes.

The generator deliberately keeps the printed geometry predictable: the QR code
stays in the upper-left header and the answer blocks occupy the remaining body.
The frontend visual constructor sends a list of semantic blocks (choice, short
answer, grid, free lines); this module renders them and embeds the chosen
variant number into every student's QR payload.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Any

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

logger = logging.getLogger(__name__)

PAGE_W, PAGE_H = A4
MARGIN = 10 * mm

_FONT_CANDIDATES = [
    ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("DejaVuSans", "/usr/share/fonts/TTF/DejaVuSans.ttf"),
    ("LiberationSans", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ("Arial", "C:\\Windows\\Fonts\\arial.ttf"),
]
_FONT_BOLD_CANDIDATES = [
    ("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("DejaVuSans-Bold", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("LiberationSans-Bold", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("Arial-Bold", "C:\\Windows\\Fonts\\arialbd.ttf"),
]

_registered: dict[str, str] = {}


def _register_fonts() -> tuple[str, str]:
    """Register a Cyrillic-capable font, falling back to Helvetica."""
    global _registered
    if _registered:
        return _registered["regular"], _registered["bold"]

    regular, bold = "Helvetica", "Helvetica-Bold"
    for name, path in _FONT_CANDIDATES:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            regular = name
            break
        except Exception:
            continue
    for name, path in _FONT_BOLD_CANDIDATES:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            bold = name
            break
        except Exception:
            continue
    if regular == "Helvetica":
        logger.warning("No Cyrillic TTF found – Russian text in PDF forms may not render correctly")
    _registered = {"regular": regular, "bold": bold}
    return regular, bold


@dataclass
class FormBlock:
    """A semantic answer area on a printed form."""

    type: str = "lines"  # lines | choice | short | grid
    title: str = "Ответ"
    rows: int = 4
    columns: int = 4

    @classmethod
    def from_dict(cls, data: dict[str, Any] | Any) -> "FormBlock":
        if isinstance(data, FormBlock):
            return data
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        data = dict(data or {})
        kind = str(data.get("type") or "lines")
        if kind not in {"lines", "choice", "short", "grid"}:
            kind = "lines"
        return cls(
            type=kind,
            title=str(data.get("title") or _default_block_title(kind))[:80],
            rows=max(1, min(int(data.get("rows") or 4), 80)),
            columns=max(1, min(int(data.get("columns") or 4), 12)),
        )


def _default_block_title(kind: str) -> str:
    return {
        "lines": "Развёрнутый ответ",
        "choice": "Выбор ответа",
        "short": "Краткий ответ",
        "grid": "Сетка / таблица",
    }.get(kind, "Ответ")


@dataclass
class FormSpec:
    """One physical form to print."""

    student_external_id: str
    student_name: str
    class_name: str
    task_external_id: str
    task_title: str
    sheet_uid: str
    sheet_index: int = 1
    sheet_total: int = 1
    variant_number: int = 1
    variant_total: int = 1

    def payload(self, fmt: str = "json") -> str:
        if fmt == "compact":
            # The sheet_uid already contains the variant/page information when
            # applicable, so compact payloads remain backward-compatible.
            return f"v1|{self.student_external_id}|{self.class_name}|{self.task_external_id}|{self.sheet_uid}"
        return json.dumps(
            {
                "version": 1,
                "studentId": self.student_external_id,
                "classId": self.class_name,
                "taskId": self.task_external_id,
                "sheetId": self.sheet_uid,
                "variantNo": self.variant_number,
                "variantTotal": self.variant_total,
                "sheetIndex": self.sheet_index,
                "sheetTotal": self.sheet_total,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _qr_reader(text: str, box: int = 8):
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _draw_header(
    c: pdf_canvas.Canvas,
    spec: FormSpec,
    x: float,
    y: float,
    width: float,
    height: float,
    header_h: float,
    payload_format: str,
    fonts: tuple[str, str],
) -> None:
    regular, bold = fonts
    qr_size = header_h - 5 * mm

    # header separator
    c.setLineWidth(0.8)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.line(x, y + height - header_h, x + width, y + height - header_h)

    # QR code
    try:
        image = _qr_reader(spec.payload(payload_format))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        from reportlab.lib.utils import ImageReader

        c.drawImage(
            ImageReader(buffer),
            x + 3 * mm,
            y + height - header_h + 2.5 * mm,
            width=qr_size,
            height=qr_size,
            preserveAspectRatio=True,
            mask="auto",
        )
    except Exception as exc:  # pragma: no cover
        logger.error("QR rendering failed for %s: %s", spec.sheet_uid, exc)
        c.setFont(regular, 7)
        c.drawString(x + 4 * mm, y + height - header_h / 2, "QR ERROR")

    text_x = x + qr_size + 7 * mm
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont(bold, 10.5)
    line1 = f"{spec.class_name} • {spec.student_name}"
    c.drawString(text_x, y + height - 9 * mm, line1[:58])

    c.setFont(regular, 8.5)
    c.setFillColorRGB(0.32, 0.32, 0.32)
    variant = f"   Вариант: {spec.variant_number}" if spec.variant_total > 1 else ""
    c.drawString(text_x, y + height - 14 * mm, f"ID: {spec.student_external_id}   Задание: {spec.task_external_id}{variant}")
    title = spec.task_title if len(spec.task_title) <= 62 else spec.task_title[:59] + "…"
    c.drawString(text_x, y + height - 18.5 * mm, title)
    if spec.sheet_total > 1:
        c.drawRightString(x + width - 3 * mm, y + height - 18.5 * mm, f"Лист {spec.sheet_index}/{spec.sheet_total}")


def _draw_block_frame(
    c: pdf_canvas.Canvas,
    block: FormBlock,
    x: float,
    y: float,
    width: float,
    height: float,
    fonts: tuple[str, str],
) -> tuple[float, float, float, float]:
    regular, bold = fonts
    c.setStrokeColorRGB(0.68, 0.68, 0.68)
    c.setLineWidth(0.6)
    c.roundRect(x, y, width, height, 3 * mm)
    c.setFillColorRGB(0.18, 0.18, 0.18)
    c.setFont(bold, 8.5)
    c.drawString(x + 3 * mm, y + height - 5 * mm, block.title[:55])
    return x + 3 * mm, y + 3 * mm, width - 6 * mm, max(1 * mm, height - 10 * mm)


def _draw_lines_block(c: pdf_canvas.Canvas, block: FormBlock, box: tuple[float, float, float, float], fonts: tuple[str, str]) -> None:
    bx, by, bw, bh = box
    c.setStrokeColorRGB(0.62, 0.62, 0.62)
    c.setLineWidth(0.55)
    spacing = bh / max(block.rows, 1)
    for i in range(block.rows):
        line_y = by + bh - (i + 1) * spacing
        if line_y < by:
            break
        c.line(bx, line_y, bx + bw, line_y)


def _draw_choice_block(c: pdf_canvas.Canvas, block: FormBlock, box: tuple[float, float, float, float], fonts: tuple[str, str]) -> None:
    bx, by, bw, bh = box
    regular, bold = fonts
    rows = max(1, block.rows)
    columns = max(2, block.columns)
    row_h = bh / rows
    answer_w = min(8 * mm, bw / (columns + 3))
    labels = [chr(ord("А") + i) for i in range(columns)]

    c.setFont(bold, 7)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    for col, label in enumerate(labels):
        c.drawCentredString(bx + 14 * mm + col * answer_w, by + bh - 3 * mm, label)

    c.setFont(regular, 7.5)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    for row in range(rows):
        cy = by + bh - (row + 0.75) * row_h
        c.setFillColorRGB(0.25, 0.25, 0.25)
        c.drawRightString(bx + 8 * mm, cy - 2.2, str(row + 1))
        for col in range(columns):
            cx = bx + 14 * mm + col * answer_w
            c.circle(cx, cy, 2.0 * mm, stroke=1, fill=0)


def _draw_short_block(c: pdf_canvas.Canvas, block: FormBlock, box: tuple[float, float, float, float], fonts: tuple[str, str]) -> None:
    bx, by, bw, bh = box
    regular, _ = fonts
    rows = max(1, block.rows)
    cells = max(4, block.columns)
    row_h = bh / rows
    cell_w = min(8 * mm, (bw - 13 * mm) / cells)

    c.setFont(regular, 7.5)
    c.setStrokeColorRGB(0.48, 0.48, 0.48)
    for row in range(rows):
        bottom = by + bh - (row + 1) * row_h + 1.3 * mm
        c.setFillColorRGB(0.25, 0.25, 0.25)
        c.drawRightString(bx + 8 * mm, bottom + 2 * mm, str(row + 1))
        for col in range(cells):
            c.rect(bx + 12 * mm + col * cell_w, bottom, cell_w - 0.5 * mm, min(6 * mm, row_h - 1.5 * mm))


def _draw_grid_block(c: pdf_canvas.Canvas, block: FormBlock, box: tuple[float, float, float, float], fonts: tuple[str, str]) -> None:
    bx, by, bw, bh = box
    rows = max(1, block.rows)
    columns = max(1, block.columns)
    c.setStrokeColorRGB(0.58, 0.58, 0.58)
    c.setLineWidth(0.5)
    for row in range(rows + 1):
        yy = by + row * bh / rows
        c.line(bx, yy, bx + bw, yy)
    for col in range(columns + 1):
        xx = bx + col * bw / columns
        c.line(xx, by, xx, by + bh)


def _normalise_blocks(blocks: list[FormBlock] | list[dict] | None, layout_kind: str = "lines") -> list[FormBlock]:
    if blocks:
        return [FormBlock.from_dict(b) for b in blocks][:8]
    if layout_kind == "choice":
        return [FormBlock("choice", "Выбор ответа", rows=12, columns=4)]
    if layout_kind == "short":
        return [FormBlock("short", "Краткие ответы", rows=10, columns=8)]
    if layout_kind == "grid":
        return [FormBlock("grid", "Сетка / таблица", rows=12, columns=8)]
    if layout_kind == "mixed":
        return [
            FormBlock("choice", "Часть A — выбор ответа", rows=8, columns=4),
            FormBlock("short", "Часть B — краткий ответ", rows=5, columns=8),
            FormBlock("lines", "Часть C — развёрнутый ответ", rows=5, columns=4),
        ]
    return [FormBlock("lines", "Ответ", rows=6, columns=4)]


def _draw_form(
    c: pdf_canvas.Canvas,
    spec: FormSpec,
    x: float,
    y: float,
    width: float,
    height: float,
    payload_format: str,
    fonts: tuple[str, str],
    blocks: list[FormBlock],
) -> None:
    """Draw a single form with its border, header, QR and answer areas."""
    c.setLineWidth(1.0)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.rect(x, y, width, height)

    header_h = min(24 * mm, height * 0.30)
    _draw_header(c, spec, x, y, width, height, header_h, payload_format, fonts)

    body_x = x + 4 * mm
    body_y = y + 5 * mm
    body_w = width - 8 * mm
    body_h = height - header_h - 10 * mm
    if body_h <= 8 * mm:
        return

    normalised = _normalise_blocks(blocks)
    gap = 3 * mm if len(normalised) > 1 else 0
    weights = [max(1, b.rows + (2 if b.type == "lines" else 0)) for b in normalised]
    unit = max(1.0, (body_h - gap * (len(normalised) - 1)) / sum(weights))
    top = body_y + body_h

    for block, weight in zip(normalised, weights):
        block_h = max(13 * mm, unit * weight)
        block_y = top - block_h
        content_box = _draw_block_frame(c, block, body_x, block_y, body_w, block_h, fonts)
        if block.type == "choice":
            _draw_choice_block(c, block, content_box, fonts)
        elif block.type == "short":
            _draw_short_block(c, block, content_box, fonts)
        elif block.type == "grid":
            _draw_grid_block(c, block, content_box, fonts)
        else:
            _draw_lines_block(c, block, content_box, fonts)
        top = block_y - gap

    c.setFillColorRGB(0.55, 0.55, 0.55)
    c.setFont(fonts[0], 6)
    c.drawRightString(x + width - 3 * mm, y + 2.5 * mm, spec.sheet_uid)


def generate_forms_pdf(
    specs: list[FormSpec],
    *,
    forms_per_page: int = 3,
    include_cut_lines: bool = True,
    payload_format: str = "json",
    document_title: str = "Чистовик — бланки",
    layout_kind: str = "lines",
    blocks: list[FormBlock] | list[dict] | None = None,
) -> bytes:
    """Render all ``specs`` onto A4 pages and return the PDF bytes."""
    if not specs:
        raise ValueError("no forms to generate")

    fonts = _register_fonts()
    buffer = io.BytesIO()
    c = pdf_canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(document_title)

    normalised_blocks = _normalise_blocks(blocks, layout_kind)
    forms_per_page = max(1, min(int(forms_per_page), 6))
    usable_h = PAGE_H - 2 * MARGIN
    usable_w = PAGE_W - 2 * MARGIN
    gap = 6 * mm
    form_h = (usable_h - gap * (forms_per_page - 1)) / forms_per_page

    for index, spec in enumerate(specs):
        slot = index % forms_per_page
        if slot == 0 and index > 0:
            c.showPage()

        y = PAGE_H - MARGIN - (slot + 1) * form_h - slot * gap
        _draw_form(c, spec, MARGIN, y, usable_w, form_h, payload_format, fonts, normalised_blocks)

        if include_cut_lines and slot < forms_per_page - 1:
            cut_y = y - gap / 2
            c.setStrokeColorRGB(0.72, 0.72, 0.72)
            c.setLineWidth(0.5)
            c.setDash(3, 3)
            c.line(MARGIN / 2, cut_y, PAGE_W - MARGIN / 2, cut_y)
            c.setDash()
            c.setFont(fonts[0], 6)
            c.setFillColorRGB(0.6, 0.6, 0.6)
            c.drawString(2 * mm, cut_y - 1.5 * mm, "✂")

    c.showPage()
    c.save()
    return buffer.getvalue()


def build_sheet_uid(
    student_external_id: str,
    task_external_id: str,
    index: int = 1,
    total: int = 1,
    *,
    variant_number: int = 1,
    variant_total: int = 1,
) -> str:
    """Deterministic, unique sheet identifier including variant/page when needed."""
    base = f"{student_external_id}-{task_external_id}"
    if variant_total > 1:
        base = f"{base}-v{variant_number:02d}"
    return base if total <= 1 else f"{base}-{index:02d}"
