"""A4 PDF generator for standard answer forms with QR codes."""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass

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

    def payload(self, fmt: str = "json") -> str:
        if fmt == "compact":
            return f"v1|{self.student_external_id}|{self.class_name}|{self.task_external_id}|{self.sheet_uid}"
        return json.dumps(
            {
                "version": 1,
                "studentId": self.student_external_id,
                "classId": self.class_name,
                "taskId": self.task_external_id,
                "sheetId": self.sheet_uid,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _qr_reader(text: str, box: int = 8):
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _draw_form(
    c: pdf_canvas.Canvas,
    spec: FormSpec,
    x: float,
    y: float,
    width: float,
    height: float,
    payload_format: str,
    fonts: tuple[str, str],
    answer_lines: int,
) -> None:
    """Draw a single form with its border, header, QR and answer lines."""
    regular, bold = fonts
    c.setLineWidth(1.0)
    c.setStrokeColorRGB(0.35, 0.35, 0.35)
    c.rect(x, y, width, height)

    header_h = min(24 * mm, height * 0.30)
    qr_size = header_h - 5 * mm

    # header separator
    c.setLineWidth(0.8)
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
    c.drawString(text_x, y + height - 14 * mm, f"ID: {spec.student_external_id}   Задание: {spec.task_external_id}")
    title = spec.task_title if len(spec.task_title) <= 62 else spec.task_title[:59] + "…"
    c.drawString(text_x, y + height - 18.5 * mm, title)
    if spec.sheet_total > 1:
        c.drawRightString(x + width - 3 * mm, y + height - 18.5 * mm, f"Лист {spec.sheet_index}/{spec.sheet_total}")

    # answer area
    c.setFillColorRGB(0.25, 0.25, 0.25)
    c.setFont(regular, 9)
    answer_top = y + height - header_h - 7 * mm
    c.drawString(x + 5 * mm, answer_top, "Ответ:")

    c.setStrokeColorRGB(0.62, 0.62, 0.62)
    c.setLineWidth(0.6)
    usable = answer_top - y - 8 * mm
    spacing = usable / max(answer_lines, 1)
    for i in range(answer_lines):
        line_y = answer_top - 6 * mm - i * spacing
        if line_y < y + 5 * mm:
            break
        c.line(x + 5 * mm, line_y, x + width - 5 * mm, line_y)

    c.setFillColorRGB(0.55, 0.55, 0.55)
    c.setFont(regular, 6)
    c.drawRightString(x + width - 3 * mm, y + 2.5 * mm, spec.sheet_uid)


def generate_forms_pdf(
    specs: list[FormSpec],
    *,
    forms_per_page: int = 3,
    include_cut_lines: bool = True,
    payload_format: str = "json",
    document_title: str = "PaperFlow Stream — бланки",
) -> bytes:
    """Render all ``specs`` onto A4 pages and return the PDF bytes."""
    if not specs:
        raise ValueError("no forms to generate")

    fonts = _register_fonts()
    buffer = io.BytesIO()
    c = pdf_canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(document_title)

    forms_per_page = max(1, min(int(forms_per_page), 6))
    usable_h = PAGE_H - 2 * MARGIN
    usable_w = PAGE_W - 2 * MARGIN
    gap = 6 * mm
    form_h = (usable_h - gap * (forms_per_page - 1)) / forms_per_page
    answer_lines = 3 if form_h > 70 * mm else (2 if form_h > 48 * mm else 1)

    for index, spec in enumerate(specs):
        slot = index % forms_per_page
        if slot == 0 and index > 0:
            c.showPage()

        y = PAGE_H - MARGIN - (slot + 1) * form_h - slot * gap
        _draw_form(c, spec, MARGIN, y, usable_w, form_h, payload_format, fonts, answer_lines)

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


def build_sheet_uid(student_external_id: str, task_external_id: str, index: int = 1, total: int = 1) -> str:
    """Deterministic, unique sheet identifier."""
    base = f"{student_external_id}-{task_external_id}"
    return base if total <= 1 else f"{base}-{index:02d}"
