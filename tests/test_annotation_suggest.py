from types import SimpleNamespace

from app.services.annotation_suggest import suggest_annotations


def _line(text, left=10, top=20, width=200, height=24):
    return SimpleNamespace(
        text=text,
        confidence=0.9,
        left=left,
        top=top,
        width=width,
        height=height,
    )


def test_suggest_invoice_boxes_from_initial_ocr_lines():
    suggestions = suggest_annotations(
        document_type="invoice",
        page=1,
        line_boxes=[
            _line("Invoice No: INV-2026-001"),
            _line("Grand Total Rp 1.250.000", top=200),
        ],
    )

    by_label = {item.label: item for item in suggestions}

    assert by_label["invoice_number"].text == "INV-2026-001"
    assert by_label["total_amount"].text == "1.250.000"
    assert by_label["invoice_number"].source == "auto_suggest"


def test_suggest_payment_proof_boxes_from_initial_ocr_lines():
    suggestions = suggest_annotations(
        document_type="payment_proof",
        page=1,
        line_boxes=[
            _line("Transfer Berhasil"),
            _line("Nominal Rp 250.000", top=120),
            _line("No Ref: TRX123456", top=160),
        ],
    )

    by_label = {item.label: item for item in suggestions}

    assert by_label["transfer_status"].text == "Transfer Berhasil"
    assert by_label["amount"].text == "250.000"
    assert by_label["reference_number"].text == "TRX123456"
