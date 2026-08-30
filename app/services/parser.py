import re
from dataclasses import dataclass

import dateparser

from app.schemas import FieldValue, InvoiceFields, ValidationResult
from app.utils.money import amounts_in_text, detect_currency


INVOICE_NO_PATTERNS = [
    re.compile(
        r"(?i)\b(?:invoice|inv|faktur)\s*(?:no|number|nomor|#|:)?\.?\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})"
    ),
    re.compile(
        r"(?i)\b(?:no|nomor)\s*(?:invoice|faktur)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,})"
    ),
]

DATE_LABEL_RE = re.compile(
    r"(?i)\b(?:invoice\s*date|tanggal\s*invoice|tanggal\s*faktur|tgl\.?|date)\b"
)

DATE_CANDIDATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{1,2}\s+(?:jan(?:uari)?|feb(?:ruari)?|mar(?:et)?|apr(?:il)?|mei|jun(?:i)?|jul(?:i)?|agu(?:stus)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|des(?:ember)?)\s+\d{4}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)

KEYWORDS = {
    "grand_total": [
        "grand total",
        "total tagihan",
        "jumlah tagihan",
        "total invoice",
        "amount due",
        "total due",
        "total payment",
        "total bayar",
        "net total",
        "total",
    ],
    "subtotal": ["subtotal", "sub total", "dpp", "total before tax"],
    "tax": ["ppn", "vat", "tax", "pajak"],
    "discount": ["discount", "diskon", "potongan"],
}


def _score_keyword_line(line: str, keywords: list[str]) -> tuple[float, str | None]:
    lower = line.lower()
    for idx, keyword in enumerate(keywords):
        if keyword in lower:
            # Earlier keyword is intentionally more specific / important.
            return max(0.55, 0.98 - idx * 0.04), keyword
    return 0.0, None


def _extract_amount_field(
    lines: list[str],
    field_name: str,
    avoid_keywords: list[str] | None = None,
) -> FieldValue:
    candidates: list[tuple[float, int, str, str]] = []
    avoid_keywords = avoid_keywords or []

    for line_idx, line in enumerate(lines):
        lower = line.lower()
        score, keyword = _score_keyword_line(line, KEYWORDS[field_name])
        if score <= 0:
            continue
        if any(avoid in lower for avoid in avoid_keywords):
            score -= 0.25

        amounts = amounts_in_text(line)
        for amount, raw in amounts:
            candidates.append((score, amount, line, raw))

        # OCR sometimes separates label and amount into adjacent lines.
        if not amounts:
            for offset in (1, 2):
                if line_idx + offset >= len(lines):
                    continue
                nearby = lines[line_idx + offset]
                for amount, raw in amounts_in_text(nearby):
                    candidates.append(
                        (score - (offset * 0.08), amount, f"{line} | {nearby}", raw)
                    )

    if not candidates:
        return FieldValue()

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    score, amount, source, _ = candidates[0]
    return FieldValue(
        value=amount,
        confidence=max(0.0, min(1.0, score)),
        source_text=source,
    )


def _extract_invoice_number(lines: list[str]) -> FieldValue:
    for line in lines:
        for pattern in INVOICE_NO_PATTERNS:
            match = pattern.search(line)
            if match:
                value = match.group(1).strip(" .:")
                return FieldValue(
                    value=value,
                    confidence=0.94,
                    source_text=line,
                )
    return FieldValue()


def _extract_invoice_date(lines: list[str]) -> FieldValue:
    labeled: list[str] = []
    unlabeled: list[str] = []

    for idx, line in enumerate(lines):
        match = DATE_CANDIDATE_RE.search(line)
        if match:
            if DATE_LABEL_RE.search(line):
                labeled.append(match.group(0))
            else:
                unlabeled.append(match.group(0))

        if DATE_LABEL_RE.search(line):
            for offset in (1, 2):
                if idx + offset < len(lines):
                    match2 = DATE_CANDIDATE_RE.search(lines[idx + offset])
                    if match2:
                        labeled.append(match2.group(0))

    for raw, confidence in [(v, 0.92) for v in labeled] + [
        (v, 0.65) for v in unlabeled
    ]:
        parsed = dateparser.parse(
            raw,
            languages=["id", "en"],
            settings={"DATE_ORDER": "DMY"},
        )
        if parsed:
            return FieldValue(
                value=parsed.date().isoformat(),
                confidence=confidence,
                source_text=raw,
            )

    return FieldValue()


def _extract_vendor(lines: list[str]) -> FieldValue:
    # Heuristic for MVP:
    # prioritize PT/CV/UD lines in the top section, then a plausible early header.
    top = [line.strip() for line in lines[:15] if line.strip()]

    legal = re.compile(
        r"(?i)\b(?:PT\.?|CV\.?|UD\.?|YAYASAN|KOPERASI)\s+[A-Z0-9][A-Z0-9 .,&'()/-]{2,}"
    )

    for line in top:
        match = legal.search(line)
        if match:
            vendor = match.group(0).strip()
            return FieldValue(
                value=vendor,
                confidence=0.82,
                source_text=line,
            )

    excluded = (
        "invoice",
        "faktur",
        "tanggal",
        "date",
        "bill to",
        "ship to",
        "subtotal",
        "total",
        "tax",
        "ppn",
    )
    for line in top:
        lower = line.lower()
        if any(x in lower for x in excluded):
            continue
        if len(line) >= 4 and not amounts_in_text(line):
            return FieldValue(value=line, confidence=0.40, source_text=line)

    return FieldValue()


def parse_invoice(lines: list[str]) -> InvoiceFields:
    text = "\n".join(lines)
    currency, currency_conf, currency_source = detect_currency(text)

    subtotal = _extract_amount_field(lines, "subtotal")
    tax = _extract_amount_field(lines, "tax")
    discount = _extract_amount_field(lines, "discount")
    grand_total = _extract_amount_field(
        lines,
        "grand_total",
        avoid_keywords=["subtotal", "sub total", "ppn", "vat", "tax", "pajak"],
    )

    return InvoiceFields(
        invoice_number=_extract_invoice_number(lines),
        invoice_date=_extract_invoice_date(lines),
        vendor_name=_extract_vendor(lines),
        currency=FieldValue(
            value=currency,
            confidence=currency_conf,
            source_text=currency_source,
        ),
        subtotal=subtotal,
        tax=tax,
        discount=discount,
        grand_total=grand_total,
    )


def validate_invoice(fields: InvoiceFields) -> ValidationResult:
    warnings: list[str] = []

    subtotal = fields.subtotal.value
    tax = fields.tax.value or 0
    discount = fields.discount.value or 0
    total = fields.grand_total.value

    if isinstance(subtotal, int) and isinstance(total, int):
        calculated = subtotal + int(tax) - int(discount)
        difference = total - calculated

        # OCR and invoice calculations may include very small rounding differences.
        arithmetic_ok = abs(difference) <= 2

        if not arithmetic_ok:
            warnings.append(
                "Grand total tidak konsisten dengan subtotal + pajak - diskon."
            )

        return ValidationResult(
            status="valid" if arithmetic_ok else "warning",
            arithmetic_ok=arithmetic_ok,
            calculated_total=calculated,
            difference=difference,
            warnings=warnings,
        )

    if total is None:
        warnings.append("Grand total belum berhasil ditemukan.")
        return ValidationResult(
            status="warning",
            arithmetic_ok=None,
            warnings=warnings,
        )

    return ValidationResult(
        status="unknown",
        arithmetic_ok=None,
        warnings=["Data subtotal/pajak belum cukup untuk validasi aritmetika."],
    )
