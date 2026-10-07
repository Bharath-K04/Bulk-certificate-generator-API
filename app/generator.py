"""Renders the single predefined certificate template to PDF using ReportLab."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


@dataclass(frozen=True)
class CertificateContent:
    certificate_id: str
    recipient_name: str
    course_name: str
    issued_by: str
    issue_date: date


# Anything that can turn content into a file at the given path. Injected into the
# JobProcessor so tests (or a future HTML/WeasyPrint template) can swap it out.
Renderer = Callable[[CertificateContent, Path], None]

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
NAVY = HexColor("#1F2A44")
GOLD = HexColor("#B8860B")
INK = HexColor("#333333")
PAPER = HexColor("#FFFDF6")


def render_certificate_pdf(content: CertificateContent, output_path: Path) -> None:
    """Write the certificate atomically: render to a temp file, then rename into place.

    A crash mid-render therefore never leaves a half-written PDF at the final path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        _draw(content, temp_path)
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _draw(content: CertificateContent, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    c.setTitle(f"Certificate - {content.recipient_name}")
    c.setAuthor(content.issued_by)
    c.setSubject(content.course_name)
    centre_x = PAGE_WIDTH / 2

    # Background and double border
    c.setFillColor(PAPER)
    c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(20, 20, PAGE_WIDTH - 40, PAGE_HEIGHT - 40)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(32, 32, PAGE_WIDTH - 64, PAGE_HEIGHT - 64)

    # Heading
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 40)
    c.drawCentredString(centre_x, 480, "CERTIFICATE")
    c.setFillColor(GOLD)
    c.setFont("Helvetica", 16)
    c.drawCentredString(centre_x, 452, "O F   C O M P L E T I O N")

    # Recipient
    c.setFillColor(INK)
    c.setFont("Helvetica-Oblique", 14)
    c.drawCentredString(centre_x, 395, "This is to certify that")

    max_text_width = PAGE_WIDTH - 180
    name_font = "Times-BoldItalic"
    name_size = _fit_font_size(content.recipient_name, name_font, 36, 16, max_text_width)
    c.setFillColor(NAVY)
    c.setFont(name_font, name_size)
    c.drawCentredString(centre_x, 345, content.recipient_name)
    name_width = stringWidth(content.recipient_name, name_font, name_size)
    underline_half = min(max(name_width / 2 + 20, 160), max_text_width / 2)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(centre_x - underline_half, 333, centre_x + underline_half, 333)

    # Course
    c.setFillColor(INK)
    c.setFont("Helvetica-Oblique", 14)
    c.drawCentredString(centre_x, 298, "has successfully completed")
    course_font = "Helvetica-Bold"
    course_size = _fit_font_size(content.course_name, course_font, 22, 11, max_text_width)
    c.setFillColor(NAVY)
    c.setFont(course_font, course_size)
    c.drawCentredString(centre_x, 265, content.course_name)

    # Footer: issuer (left), date (right), certificate id (bottom)
    c.setStrokeColor(INK)
    c.setLineWidth(0.8)
    for x in (230, PAGE_WIDTH - 230):
        c.line(x - 110, 130, x + 110, 130)
    c.setFillColor(INK)
    issuer_size = _fit_font_size(content.issued_by, "Helvetica-Bold", 12, 7, 220)
    c.setFont("Helvetica-Bold", issuer_size)
    c.drawCentredString(230, 138, content.issued_by)
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(PAGE_WIDTH - 230, 138, content.issue_date.strftime("%d %B %Y"))
    c.setFont("Helvetica", 10)
    c.drawCentredString(230, 115, "Issued by")
    c.drawCentredString(PAGE_WIDTH - 230, 115, "Date of issue")

    c.setFillColor(HexColor("#777777"))
    c.setFont("Helvetica", 8)
    c.drawCentredString(centre_x, 50, f"Certificate ID: {content.certificate_id}")

    c.showPage()
    c.save()


def _fit_font_size(text: str, font: str, start: float, minimum: float, max_width: float) -> float:
    """Shrink the font until the text fits on one line (long names, long course titles)."""
    size = start
    while size > minimum and stringWidth(text, font, size) > max_width:
        size -= 1
    return size
