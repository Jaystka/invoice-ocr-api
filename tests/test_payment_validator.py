from app.config import Settings
from app.schemas import FieldValue, TransferFields
from app.services.payment_validator import validate_payment


def fv(value):
    return FieldValue(value=value, confidence=0.95)


def test_validate_payment_match():
    payment = TransferFields(
        transfer_status=fv("success"),
        amount=fv(1_387_500),
        currency=fv("IDR"),
        transaction_date=fv("2026-08-30"),
        transaction_time=fv("14:32 WIB"),
        source_bank=fv("MANDIRI"),
        source_account=fv("****7777"),
        source_name=fv("BUDI"),
        destination_bank=fv("BCA"),
        destination_account=fv("****1234"),
        destination_name=fv("PT CONTOH INDONESIA"),
        reference_number=fv("TRX20260830123456"),
        channel=fv("BI-FAST"),
    )

    result = validate_payment(
        payment,
        settings=Settings(),
        expected_amount=1_387_500,
        expected_recipient_name="PT Contoh Indonesia",
        expected_recipient_account="12345678901234",
        expected_recipient_bank="Bank Central Asia",
    )

    assert result.status == "valid"
    assert result.score >= 0.85


def test_validate_payment_wrong_amount():
    payment = TransferFields(
        transfer_status=fv("success"),
        amount=fv(1_000_000),
        currency=fv("IDR"),
        transaction_date=fv("2026-08-30"),
        transaction_time=fv("14:32 WIB"),
        source_bank=fv("MANDIRI"),
        source_account=fv("****7777"),
        source_name=fv("BUDI"),
        destination_bank=fv("BCA"),
        destination_account=fv("****1234"),
        destination_name=fv("PT CONTOH INDONESIA"),
        reference_number=fv("TRX20260830123456"),
        channel=fv("BI-FAST"),
    )

    result = validate_payment(
        payment,
        settings=Settings(),
        expected_amount=1_387_500,
        expected_recipient_account="12345678901234",
        expected_recipient_bank="BCA",
    )

    assert result.status == "invalid"
    assert "amount_mismatch" in result.risk_flags
