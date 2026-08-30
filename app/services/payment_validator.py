import re
import unicodedata
from difflib import SequenceMatcher

from app.config import Settings
from app.schemas import (
    PaymentValidationResult,
    ReconciliationResult,
    TransferFields,
    ValidationCheck,
)
from app.services.matcher import match_total


BANK_NORMALIZATION = {
    "BANK CENTRAL ASIA": "BCA",
    "BCA": "BCA",
    "BANK RAKYAT INDONESIA": "BRI",
    "BRI": "BRI",
    "BANK NEGARA INDONESIA": "BNI",
    "BNI": "BNI",
    "BANK MANDIRI": "MANDIRI",
    "MANDIRI": "MANDIRI",
    "BANK SYARIAH INDONESIA": "BSI",
    "BSI": "BSI",
    "CIMB NIAGA": "CIMB NIAGA",
    "BANK CIMB NIAGA": "CIMB NIAGA",
    "PERMATA BANK": "PERMATA",
    "BANK PERMATA": "PERMATA",
    "PERMATA": "PERMATA",
    "DANAMON": "DANAMON",
    "BANK DANAMON": "DANAMON",
    "OCBC NISP": "OCBC",
    "OCBC": "OCBC",
    "BTN": "BTN",
    "BANK TABUNGAN NEGARA": "BTN",
    "BTPN": "BTPN",
    "JENIUS": "BTPN",
    "BANK JAGO": "JAGO",
    "JAGO": "JAGO",
    "SEABANK": "SEABANK",
}


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.upper()
    value = re.sub(r"\b(?:PT|CV|UD|TBK|PERSERO|LTD|INC|CO)\b\.?", " ", value)
    value = re.sub(r"[^A-Z0-9 ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def name_similarity(actual: str | None, expected: str | None) -> float:
    a = normalize_name(actual)
    b = normalize_name(expected)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.94
    return SequenceMatcher(None, a, b).ratio()


def normalize_bank(value: str | None) -> str:
    if not value:
        return ""
    normalized = normalize_name(value)
    return BANK_NORMALIZATION.get(normalized, normalized)


def normalize_account(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9*Xx]", "", value).upper().replace("X", "*")


def account_similarity(actual: str | None, expected: str | None) -> tuple[float, str]:
    a = normalize_account(actual)
    e = normalize_account(expected)

    if not a or not e:
        return 0.0, "Nomor rekening tidak cukup untuk dibandingkan."

    if a == e:
        return 1.0, "Nomor rekening cocok tepat."

    visible_digits = "".join(ch for ch in a if ch.isdigit())
    expected_digits = "".join(ch for ch in e if ch.isdigit())

    # Masked bank receipt: ****1234, xxxx1234, etc.
    if "*" in a and len(visible_digits) >= 4:
        if expected_digits.endswith(visible_digits):
            return 0.95, "Akhiran rekening pada bukti transfer cocok."
        return 0.0, "Akhiran rekening pada bukti transfer tidak cocok."

    # OCR may omit separators or a bank may display only a suffix even without mask.
    if 4 <= len(a) < len(e) and expected_digits.endswith(visible_digits):
        return 0.88, "Rekening parsial pada bukti transfer cocok."

    return 0.0, "Nomor rekening tujuan tidak cocok."


def _weighted_score(checks: list[ValidationCheck], weights: dict[str, float]) -> float:
    numerator = 0.0
    denominator = 0.0
    for check in checks:
        weight = weights.get(check.name, 0.0)
        if weight <= 0 or check.status == "not_checked":
            continue
        denominator += weight
        numerator += weight * (check.score or 0.0)
    return round(numerator / denominator, 4) if denominator else 0.0


def validate_payment(
    payment: TransferFields,
    *,
    settings: Settings,
    expected_amount: int | None = None,
    expected_recipient_name: str | None = None,
    expected_recipient_account: str | None = None,
    expected_recipient_bank: str | None = None,
    expected_reference: str | None = None,
    tolerance_amount: int | None = None,
    tolerance_percent: float | None = None,
) -> PaymentValidationResult:
    checks: list[ValidationCheck] = []
    risk_flags: list[str] = []

    status_value = str(payment.transfer_status.value or "unknown").lower()
    if status_value == "success":
        checks.append(
            ValidationCheck(
                name="transfer_status",
                status="pass",
                expected="success",
                actual="success",
                score=1.0,
                message="Bukti menunjukkan transaksi berhasil.",
            )
        )
    elif status_value in {"failed", "pending"}:
        checks.append(
            ValidationCheck(
                name="transfer_status",
                status="fail" if status_value == "failed" else "warning",
                expected="success",
                actual=status_value,
                score=0.0 if status_value == "failed" else 0.35,
                message="Status transaksi belum menunjukkan pembayaran selesai.",
            )
        )
        risk_flags.append(f"transfer_status_{status_value}")
    else:
        checks.append(
            ValidationCheck(
                name="transfer_status",
                status="warning",
                expected="success",
                actual="unknown",
                score=0.45,
                message="Status berhasil tidak dapat dipastikan dari OCR.",
            )
        )
        risk_flags.append("transfer_status_unknown")

    actual_amount = payment.amount.value if isinstance(payment.amount.value, int) else None
    amount_match = match_total(
        invoice_total=actual_amount,
        expected_total=expected_amount,
        tolerance_amount=(
            tolerance_amount
            if tolerance_amount is not None
            else settings.default_tolerance_amount
        ),
        tolerance_percent=(
            tolerance_percent
            if tolerance_percent is not None
            else settings.default_tolerance_percent
        ),
    )

    if expected_amount is None:
        checks.append(
            ValidationCheck(
                name="amount",
                status="not_checked",
                actual=actual_amount,
                message="Expected amount tidak dikirim.",
            )
        )
    elif amount_match.status in {"exact_match", "tolerance_match"}:
        checks.append(
            ValidationCheck(
                name="amount",
                status="pass",
                expected=expected_amount,
                actual=actual_amount,
                score=amount_match.score or 1.0,
                message="Nominal transfer cocok dengan tagihan.",
            )
        )
    elif amount_match.status == "mismatch":
        checks.append(
            ValidationCheck(
                name="amount",
                status="fail",
                expected=expected_amount,
                actual=actual_amount,
                score=amount_match.score or 0.0,
                message=f"Nominal transfer berbeda sebesar {amount_match.difference}.",
            )
        )
        risk_flags.append("amount_mismatch")
    else:
        checks.append(
            ValidationCheck(
                name="amount",
                status="warning",
                expected=expected_amount,
                actual=actual_amount,
                score=0.0,
                message="Nominal transfer tidak berhasil dibaca.",
            )
        )
        risk_flags.append("amount_unreadable")

    if expected_recipient_name:
        sim = name_similarity(
            str(payment.destination_name.value or ""),
            expected_recipient_name,
        )
        passed = sim >= settings.minimum_name_similarity
        checks.append(
            ValidationCheck(
                name="recipient_name",
                status="pass" if passed else "fail",
                expected=expected_recipient_name,
                actual=payment.destination_name.value,
                score=round(sim, 4),
                message=(
                    "Nama penerima cocok."
                    if passed
                    else "Nama penerima tidak cukup mirip dengan data tujuan."
                ),
            )
        )
        if not passed:
            risk_flags.append("recipient_name_mismatch")
    else:
        checks.append(
            ValidationCheck(
                name="recipient_name",
                status="not_checked",
                actual=payment.destination_name.value,
            )
        )

    if expected_recipient_account:
        sim, message = account_similarity(
            str(payment.destination_account.value or ""),
            expected_recipient_account,
        )
        passed = sim >= 0.85
        checks.append(
            ValidationCheck(
                name="recipient_account",
                status="pass" if passed else "fail",
                expected=expected_recipient_account,
                actual=payment.destination_account.value,
                score=round(sim, 4),
                message=message,
            )
        )
        if not passed:
            risk_flags.append("recipient_account_mismatch")
    else:
        checks.append(
            ValidationCheck(
                name="recipient_account",
                status="not_checked",
                actual=payment.destination_account.value,
            )
        )

    if expected_recipient_bank:
        actual_bank = normalize_bank(str(payment.destination_bank.value or ""))
        expected_bank = normalize_bank(expected_recipient_bank)
        if not actual_bank:
            bank_score = 0.0
            bank_status = "warning"
            message = "Bank tujuan tidak berhasil dibaca."
            risk_flags.append("recipient_bank_unreadable")
        else:
            bank_score = 1.0 if actual_bank == expected_bank else 0.0
            bank_status = "pass" if bank_score == 1.0 else "fail"
            message = (
                "Bank tujuan cocok."
                if bank_score == 1.0
                else "Bank tujuan tidak cocok."
            )
            if bank_status == "fail":
                risk_flags.append("recipient_bank_mismatch")

        checks.append(
            ValidationCheck(
                name="recipient_bank",
                status=bank_status,
                expected=expected_bank,
                actual=actual_bank or payment.destination_bank.value,
                score=bank_score,
                message=message,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                name="recipient_bank",
                status="not_checked",
                actual=payment.destination_bank.value,
            )
        )

    if expected_reference:
        actual_ref = re.sub(
            r"[^A-Z0-9]",
            "",
            str(payment.reference_number.value or "").upper(),
        )
        expected_ref = re.sub(r"[^A-Z0-9]", "", expected_reference.upper())
        ref_score = 1.0 if actual_ref and actual_ref == expected_ref else 0.0
        checks.append(
            ValidationCheck(
                name="reference",
                status="pass" if ref_score else "fail",
                expected=expected_reference,
                actual=payment.reference_number.value,
                score=ref_score,
                message=(
                    "Nomor referensi cocok."
                    if ref_score
                    else "Nomor referensi tidak cocok."
                ),
            )
        )
        if not ref_score:
            risk_flags.append("reference_mismatch")
    else:
        checks.append(
            ValidationCheck(
                name="reference",
                status="not_checked",
                actual=payment.reference_number.value,
            )
        )

    # Presence signal: a real receipt normally carries at least a date or reference.
    has_date = bool(payment.transaction_date.value)
    has_ref = bool(payment.reference_number.value)
    evidence_score = 1.0 if has_date and has_ref else 0.7 if (has_date or has_ref) else 0.25
    checks.append(
        ValidationCheck(
            name="receipt_evidence",
            status="pass" if evidence_score >= 0.7 else "warning",
            actual=f"date={has_date}, reference={has_ref}",
            score=evidence_score,
            message="Cek keberadaan tanggal dan nomor referensi transaksi.",
        )
    )
    if evidence_score < 0.7:
        risk_flags.append("weak_receipt_evidence")

    weights = {
        "transfer_status": 0.20,
        "amount": 0.35,
        "recipient_name": 0.12,
        "recipient_account": 0.18,
        "recipient_bank": 0.08,
        "reference": 0.04,
        "receipt_evidence": 0.03,
    }
    score = _weighted_score(checks, weights)

    hard_fail_names = {"amount", "recipient_account", "recipient_bank"}
    hard_fail = any(
        c.status == "fail" and c.name in hard_fail_names
        for c in checks
    )
    transfer_failed = any(
        c.name == "transfer_status" and c.actual == "failed"
        for c in checks
    )

    if hard_fail or transfer_failed:
        status = "invalid"
    elif score >= settings.payment_valid_score:
        status = "valid"
    elif score >= settings.payment_review_score:
        status = "review"
    else:
        status = "invalid"

    return PaymentValidationResult(
        status=status,
        score=score,
        checks=checks,
        risk_flags=sorted(set(risk_flags)),
    )


def reconcile_invoice_payment(
    *,
    invoice_total: int | None,
    invoice_number: str | None,
    payment: TransferFields,
    payment_validation: PaymentValidationResult,
    tolerance_amount: int,
    tolerance_percent: float,
) -> ReconciliationResult:
    paid_amount = payment.amount.value if isinstance(payment.amount.value, int) else None
    amount_match = match_total(
        invoice_total=paid_amount,
        expected_total=invoice_total,
        tolerance_amount=tolerance_amount,
        tolerance_percent=tolerance_percent,
    )

    checks: list[ValidationCheck] = []

    if invoice_total is None or paid_amount is None:
        checks.append(
            ValidationCheck(
                name="invoice_vs_payment_amount",
                status="warning",
                expected=invoice_total,
                actual=paid_amount,
                score=0.0,
                message="Total invoice atau nominal pembayaran tidak terbaca.",
            )
        )
        return ReconciliationResult(
            status="insufficient_data",
            score=0.0,
            invoice_total=invoice_total,
            paid_amount=paid_amount,
            invoice_number=invoice_number,
            payment_reference=(
                str(payment.reference_number.value)
                if payment.reference_number.value
                else None
            ),
            checks=checks,
        )

    amount_ok = amount_match.status in {"exact_match", "tolerance_match"}
    checks.append(
        ValidationCheck(
            name="invoice_vs_payment_amount",
            status="pass" if amount_ok else "fail",
            expected=invoice_total,
            actual=paid_amount,
            score=amount_match.score or 0.0,
            message=(
                "Nominal pembayaran sesuai total invoice."
                if amount_ok
                else f"Selisih pembayaran: {amount_match.difference}."
            ),
        )
    )

    checks.append(
        ValidationCheck(
            name="payment_proof_validation",
            status=(
                "pass"
                if payment_validation.status == "valid"
                else "warning"
                if payment_validation.status == "review"
                else "fail"
            ),
            expected="valid",
            actual=payment_validation.status,
            score=payment_validation.score,
            message="Ringkasan validasi konten bukti transfer.",
        )
    )

    combined = round(
        (amount_match.score or 0.0) * 0.70
        + payment_validation.score * 0.30,
        4,
    )

    if not amount_ok or payment_validation.status == "invalid":
        status = "mismatch"
    elif payment_validation.status == "review":
        status = "review"
    else:
        status = "matched"

    return ReconciliationResult(
        status=status,
        score=combined,
        invoice_total=invoice_total,
        paid_amount=paid_amount,
        difference=paid_amount - invoice_total,
        invoice_number=invoice_number,
        payment_reference=(
            str(payment.reference_number.value)
            if payment.reference_number.value
            else None
        ),
        checks=checks,
    )
