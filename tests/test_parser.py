from app.services.parser import parse_invoice, validate_invoice


def test_invoice_parse():
    lines = [
        "PT CONTOH INDONESIA",
        "INVOICE NO: INV-2026-00129",
        "Tanggal Invoice: 30 Agustus 2026",
        "Subtotal Rp 1.250.000",
        "PPN Rp 137.500",
        "Grand Total Rp 1.387.500",
    ]

    fields = parse_invoice(lines)

    assert fields.invoice_number.value == "INV-2026-00129"
    assert fields.invoice_date.value == "2026-08-30"
    assert fields.subtotal.value == 1_250_000
    assert fields.tax.value == 137_500
    assert fields.grand_total.value == 1_387_500

    validation = validate_invoice(fields)
    assert validation.arithmetic_ok is True
