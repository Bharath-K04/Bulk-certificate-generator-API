from datetime import date
from io import BytesIO

from pypdf import PdfReader

from app.generator import CertificateContent, _fit_font_size, render_certificate_pdf
from tests.conftest import make_payload


def _content(**overrides) -> CertificateContent:
    values = {
        "certificate_id": "1234-abcd",
        "recipient_name": "Asha Rao",
        "course_name": "Advanced Python Workshop",
        "issued_by": "AEREO Academy",
        "issue_date": date(2026, 10, 1),
    }
    values.update(overrides)
    return CertificateContent(**values)


def _pdf_text(data: bytes) -> str:
    return PdfReader(BytesIO(data)).pages[0].extract_text()


def test_render_certificate_pdf_contains_recipient_specific_data(tmp_path):
    output = tmp_path / "nested" / "cert.pdf"

    render_certificate_pdf(_content(), output)

    data = output.read_bytes()
    assert data.startswith(b"%PDF")
    text = _pdf_text(data)
    assert "Asha Rao" in text
    assert "Advanced Python Workshop" in text
    assert "AEREO Academy" in text
    assert "01 October 2026" in text
    assert "1234-abcd" in text


def test_render_leaves_no_temp_file(tmp_path):
    output = tmp_path / "cert.pdf"
    render_certificate_pdf(_content(), output)
    assert [p.name for p in tmp_path.iterdir()] == ["cert.pdf"]


def test_long_names_shrink_to_fit():
    short = _fit_font_size("Asha Rao", "Times-BoldItalic", 36, 16, 660)
    long = _fit_font_size("A" * 60, "Times-BoldItalic", 36, 16, 660)
    assert short == 36
    assert 16 <= long < 36


def test_generated_certificate_is_stored_for_each_recipient(client, settings):
    body = client.post("/api/v1/jobs", json=make_payload()).json()
    items = client.get(body["links"]["certificates"]).json()["items"]

    assert all(item["status"] == "generated" for item in items)
    for item in items:
        pdf_path = settings.storage_dir / body["id"] / f"{item['id']}.pdf"
        assert pdf_path.is_file()
        text = _pdf_text(pdf_path.read_bytes())
        assert item["recipient_name"] in text
        assert item["id"] in text  # certificate id printed for verification
