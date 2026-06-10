from __future__ import annotations

from docdex import extract as ex


def test_plain_and_office_extraction(corpus):
    assert "ZEPHYRTOKEN" in ex.extract(corpus / "Reports" / "Q1 report.md")
    assert "XANTHICWORD" in ex.extract(corpus / "Reports" / "board deck.docx")
    xlsx_text = ex.extract(corpus / "Reports" / "numbers.xlsx")
    assert "h1" in xlsx_text and "42" in xlsx_text
    assert "QUARKPDF" in ex.extract(corpus / "Notes" / "real.pdf")


def test_empty_pdf_extracts_empty(corpus):
    assert ex.extract(corpus / "Reports" / "scan.pdf").strip() == ""


def test_unsupported_marker(corpus):
    out = ex.extract(corpus / "unsupported.bin")
    assert out.startswith(ex.UNSUPPORTED_PREFIX)


def test_is_supported():
    assert ex.is_supported("a/b/report.PDF")
    assert ex.is_supported("x.md")
    assert not ex.is_supported("x.bin")
    assert not ex.is_supported("x")
