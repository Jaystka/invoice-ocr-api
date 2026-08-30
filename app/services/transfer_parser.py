import re

import dateparser

from app.schemas import FieldValue, TransferFields
from app.utils.money import amounts_in_text, detect_currency


SUCCESS_PATTERNS = [
    ("success", re.compile(r"(?i)\b(?:transfer|transaksi|pembayaran)\s+(?:berhasil|sukses)\b")),
    ("success", re.compile(r"(?i)\b(?:berhasil|sukses|successful|success|completed)\b")),
]
PENDING_PATTERNS = [
    ("pending", re.compile(r"(?i)\b(?:pending|diproses|processing|menunggu)\b")),
]
FAILED_PATTERNS = [
    ("failed", re.compile(r"(?i)\b(?:gagal|failed|failure|dibatalkan|cancelled|canceled)\b")),
]

AMOUNT_LABELS = [
    "jumlah transfer",
    "nominal transfer",
    "nominal transaksi",
    "jumlah transaksi",
    "amount transferred",
    "transfer amount",
    "amount",
    "nominal",
    "jumlah",
    "total transfer",
    "total",
]
AMOUNT_AVOID = [
    "biaya admin",
    "admin fee",
    "fee",
    "saldo",
    "balance",
]

DATE_LABELS = [
    "tanggal transaksi",
    "waktu transaksi",
    "transaction date",
    "transaction time",
    "tanggal",
    "date",
]
TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3])[:.][0-5]\d(?::[0-5]\d)?(?:\s*(?:WIB|WITA|WIT))?\b", re.I)
DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}[/-]\d{1,2}[/-]\d{1,2}"
    r"|\d{1,2}\s+(?:jan(?:uari)?|feb(?:ruari)?|mar(?:et)?|apr(?:il)?|mei|jun(?:i)?|jul(?:i)?|agu(?:stus)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|des(?:ember)?)\s+\d{4}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b",
    re.I,
)

REFERENCE_LABELS = [
    "nomor referensi",
    "no referensi",
    "no. referensi",
    "reference number",
    "reference no",
    "ref no",
    "ref.",
    "transaction id",
    "id transaksi",
    "nomor transaksi",
]

DEST_ACCOUNT_LABELS = [
    "rekening tujuan",
    "no rekening tujuan",
    "nomor rekening tujuan",
    "account tujuan",
    "destination account",
    "beneficiary account",
    "nomor rekening",
    "no. rekening",
    "no rekening",
]
SOURCE_ACCOUNT_LABELS = [
    "rekening sumber",
    "sumber dana",
    "source account",
    "from account",
    "rekening pengirim",
]
DEST_NAME_LABELS = [
    "nama penerima",
    "penerima",
    "nama tujuan",
    "beneficiary name",
    "beneficiary",
    "recipient name",
    "recipient",
]
SOURCE_NAME_LABELS = [
    "nama pengirim",
    "pengirim",
    "sender name",
    "sender",
    "from name",
]
DEST_BANK_LABELS = [
    "bank tujuan",
    "destination bank",
    "beneficiary bank",
    "bank penerima",
]
SOURCE_BANK_LABELS = [
    "bank pengirim",
    "source bank",
    "sender bank",
]

ACCOUNT_TOKEN_RE = re.compile(r"(?<!\w)(?:\d[\d\s.-]{5,18}\d|\*{2,}\d{2,8}|[xX]{2,}\d{2,8})(?!\w)")
VALUE_AFTER_LABEL_RE = re.compile(r"^\s*[:\-]?\s*(.+?)\s*$")

BANK_ALIASES = {
    "BCA": ["bca", "bank central asia"],
    "BRI": ["bri", "bank rakyat indonesia"],
    "BNI": ["bni", "bank negara indonesia"],
    "MANDIRI": ["bank mandiri", "mandiri"],
    "BSI": ["bank syariah indonesia", "bsi"],
    "CIMB NIAGA": ["cimb niaga", "cimb"],
    "PERMATA": ["permata bank", "bank permata", "permata"],
    "DANAMON": ["bank danamon", "danamon"],
    "OCBC": ["ocbc nisp", "ocbc"],
    "BTN": ["bank tabungan negara", "btn"],
    "BTPN": ["bank btpn", "btpn", "jenius"],
    "JAGO": ["bank jago", "jago"],
    "SEABANK": ["seabank"],
    "BLU BCA DIGITAL": ["blu by bca", "bca digital", "blu"],
    "BANK JATENG": ["bank jateng"],
    "BANK JATIM": ["bank jatim"],
    "BANK BJB": ["bank bjb", "bjb"],
    "BANK DKI": ["bank dki"],
}

CHANNEL_PATTERNS = {
    "BI-FAST": ["bi-fast", "bifast", "bi fast"],
    "RTGS": ["rtgs"],
    "SKN": ["skn", "kliring"],
    "ONLINE TRANSFER": ["online transfer", "transfer online"],
    "VIRTUAL ACCOUNT": ["virtual account", "va payment"],
    "QRIS": ["qris"],
}


def _inline_or_nearby_value(lines: list[str], idx: int, label: str, max_offset: int = 2) -> tuple[str | None, str | None]:
    line = lines[idx]
    lower = line.lower()
    pos = lower.find(label)
    if pos >= 0:
        remainder = line[pos + len(label):].strip()
        remainder = re.sub(r"^[\s:=-]+", "", remainder)
        if remainder:
            return remainder, line

    for offset in range(1, max_offset + 1):
        if idx + offset < len(lines):
            value = lines[idx + offset].strip()
            if value:
                return value, f"{line} | {value}"
    return None, None


def _find_label_value(lines: list[str], labels: list[str]) -> tuple[str | None, str | None, float]:
    for idx, line in enumerate(lines):
        lower = line.lower()
        for priority, label in enumerate(labels):
            if label in lower:
                value, source = _inline_or_nearby_value(lines, idx, label)
                if value:
                    confidence = max(0.62, 0.95 - priority * 0.025)
                    return value, source, confidence
    return None, None, 0.0


def _extract_status(lines: list[str]) -> FieldValue:
    text = "\n".join(lines)

    # Negative states override generic "success" words elsewhere in a screenshot.
    for value, pattern in FAILED_PATTERNS:
        match = pattern.search(text)
        if match:
            return FieldValue(value=value, confidence=0.98, source_text=match.group(0))

    for value, pattern in PENDING_PATTERNS:
        match = pattern.search(text)
        if match:
            return FieldValue(value=value, confidence=0.95, source_text=match.group(0))

    for value, pattern in SUCCESS_PATTERNS:
        match = pattern.search(text)
        if match:
            return FieldValue(value=value, confidence=0.96, source_text=match.group(0))

    return FieldValue(value="unknown", confidence=0.20)


def _extract_amount(lines: list[str]) -> FieldValue:
    candidates: list[tuple[float, int, str]] = []

    for idx, line in enumerate(lines):
        lower = line.lower()
        if any(avoid in lower for avoid in AMOUNT_AVOID):
            continue

        matched_label = None
        label_score = 0.0
        for priority, label in enumerate(AMOUNT_LABELS):
            if label in lower:
                matched_label = label
                label_score = max(0.62, 0.98 - priority * 0.035)
                break

        if not matched_label:
            continue

        found = amounts_in_text(line)
        if found:
            for amount, _ in found:
                candidates.append((label_score, amount, line))
        else:
            for offset in (1, 2):
                if idx + offset >= len(lines):
                    continue
                nearby = lines[idx + offset]
                if any(avoid in nearby.lower() for avoid in AMOUNT_AVOID):
                    continue
                for amount, _ in amounts_in_text(nearby):
                    candidates.append(
                        (label_score - 0.07 * offset, amount, f"{line} | {nearby}")
                    )

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        score, amount, source = candidates[0]
        return FieldValue(value=amount, confidence=score, source_text=source)

    # Conservative fallback: use the largest money-like value only when there
    # are few candidates. This is intentionally low confidence.
    global_amounts: list[tuple[int, str]] = []
    for line in lines:
        if any(avoid in line.lower() for avoid in AMOUNT_AVOID):
            continue
        global_amounts.extend((amount, line) for amount, _ in amounts_in_text(line))

    if 1 <= len(global_amounts) <= 3:
        amount, source = max(global_amounts, key=lambda x: x[0])
        return FieldValue(value=amount, confidence=0.42, source_text=source)

    return FieldValue()


def _extract_date(lines: list[str]) -> FieldValue:
    labeled: list[tuple[str, str]] = []
    unlabeled: list[tuple[str, str]] = []

    for idx, line in enumerate(lines):
        match = DATE_RE.search(line)
        is_labeled = any(label in line.lower() for label in DATE_LABELS)
        if match:
            (labeled if is_labeled else unlabeled).append((match.group(0), line))

        if is_labeled and not match:
            for offset in (1, 2):
                if idx + offset < len(lines):
                    m2 = DATE_RE.search(lines[idx + offset])
                    if m2:
                        labeled.append((m2.group(0), f"{line} | {lines[idx + offset]}"))

    for raw, source, confidence in [
        *[(r, s, 0.92) for r, s in labeled],
        *[(r, s, 0.62) for r, s in unlabeled],
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
                source_text=source,
            )

    return FieldValue()


def _extract_time(lines: list[str]) -> FieldValue:
    for line in lines:
        match = TIME_RE.search(line)
        if match:
            value = match.group(0).replace(".", ":")
            return FieldValue(value=value, confidence=0.80, source_text=line)
    return FieldValue()


def _extract_reference(lines: list[str]) -> FieldValue:
    for idx, line in enumerate(lines):
        lower = line.lower()
        for priority, label in enumerate(REFERENCE_LABELS):
            if label not in lower:
                continue
            value, source = _inline_or_nearby_value(lines, idx, label)
            if not value:
                continue
            # References are commonly alphanumeric, dashes or slashes.
            match = re.search(r"\b[A-Z0-9][A-Z0-9./_-]{4,}\b", value, re.I)
            if match:
                return FieldValue(
                    value=match.group(0),
                    confidence=max(0.65, 0.95 - priority * 0.025),
                    source_text=source,
                )
    return FieldValue()


def _extract_account(lines: list[str], labels: list[str]) -> FieldValue:
    value, source, confidence = _find_label_value(lines, labels)
    if value:
        match = ACCOUNT_TOKEN_RE.search(value)
        if match:
            normalized = re.sub(r"[\s.-]", "", match.group(0))
            return FieldValue(
                value=normalized,
                confidence=confidence,
                source_text=source,
            )

    # Sometimes label and account are in the same line but our generic value
    # captured a name first. Search directly around label lines.
    for line in lines:
        lower = line.lower()
        if any(label in lower for label in labels):
            match = ACCOUNT_TOKEN_RE.search(line)
            if match:
                normalized = re.sub(r"[\s.-]", "", match.group(0))
                return FieldValue(
                    value=normalized,
                    confidence=0.76,
                    source_text=line,
                )

    return FieldValue()


def _clean_name(value: str) -> str:
    value = re.sub(r"(?i)\b(?:nama|name|penerima|recipient|beneficiary|pengirim|sender)\b", " ", value)
    value = re.sub(r"^[\s:=-]+", "", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _extract_name(lines: list[str], labels: list[str]) -> FieldValue:
    value, source, confidence = _find_label_value(lines, labels)
    if not value:
        return FieldValue()

    # Avoid returning account/amount/reference as a "name".
    if ACCOUNT_TOKEN_RE.fullmatch(value.strip()) or amounts_in_text(value):
        return FieldValue()

    cleaned = _clean_name(value)
    if len(cleaned) < 3:
        return FieldValue()

    return FieldValue(
        value=cleaned[:120],
        confidence=confidence,
        source_text=source,
    )


def _canonical_bank(text: str) -> str | None:
    lower = text.lower()
    for canonical, aliases in BANK_ALIASES.items():
        if any(alias in lower for alias in aliases):
            return canonical
    return None


def _extract_bank(lines: list[str], labels: list[str]) -> FieldValue:
    value, source, confidence = _find_label_value(lines, labels)
    if value:
        bank = _canonical_bank(value)
        if bank:
            return FieldValue(value=bank, confidence=confidence, source_text=source)

    # Search line containing the label itself.
    for line in lines:
        if any(label in line.lower() for label in labels):
            bank = _canonical_bank(line)
            if bank:
                return FieldValue(value=bank, confidence=0.78, source_text=line)

    return FieldValue()


def _extract_all_banks(lines: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen = set()
    for line in lines:
        bank = _canonical_bank(line)
        if bank and bank not in seen:
            seen.add(bank)
            found.append((bank, line))
    return found


def _extract_channel(lines: list[str]) -> FieldValue:
    text = "\n".join(lines).lower()
    for channel, aliases in CHANNEL_PATTERNS.items():
        for alias in aliases:
            if alias in text:
                return FieldValue(value=channel, confidence=0.90, source_text=alias)
    return FieldValue()


def parse_transfer_proof(lines: list[str]) -> TransferFields:
    text = "\n".join(lines)
    currency, currency_conf, currency_source = detect_currency(text)

    destination_bank = _extract_bank(lines, DEST_BANK_LABELS)
    source_bank = _extract_bank(lines, SOURCE_BANK_LABELS)

    # If labels are absent, a receipt may show only one or two bank names.
    banks = _extract_all_banks(lines)
    if destination_bank.value is None and len(banks) == 1:
        destination_bank = FieldValue(
            value=banks[0][0],
            confidence=0.45,
            source_text=banks[0][1],
        )
    elif len(banks) >= 2:
        if source_bank.value is None:
            source_bank = FieldValue(
                value=banks[0][0],
                confidence=0.42,
                source_text=banks[0][1],
            )
        if destination_bank.value is None:
            destination_bank = FieldValue(
                value=banks[-1][0],
                confidence=0.42,
                source_text=banks[-1][1],
            )

    return TransferFields(
        transfer_status=_extract_status(lines),
        amount=_extract_amount(lines),
        currency=FieldValue(
            value=currency,
            confidence=currency_conf,
            source_text=currency_source,
        ),
        transaction_date=_extract_date(lines),
        transaction_time=_extract_time(lines),
        source_bank=source_bank,
        source_account=_extract_account(lines, SOURCE_ACCOUNT_LABELS),
        source_name=_extract_name(lines, SOURCE_NAME_LABELS),
        destination_bank=destination_bank,
        destination_account=_extract_account(lines, DEST_ACCOUNT_LABELS),
        destination_name=_extract_name(lines, DEST_NAME_LABELS),
        reference_number=_extract_reference(lines),
        channel=_extract_channel(lines),
    )
