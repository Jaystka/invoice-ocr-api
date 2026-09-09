from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas import BoundingBox, DocumentType, InvoiceAnnotationCreate


@dataclass(frozen=True)
class FieldPattern:
    label: str
    patterns: tuple[re.Pattern[str], ...]
    value_pattern: re.Pattern[str] | None = None


INVOICE_PATTERNS = (
    FieldPattern(
        label="invoice_number",
        patterns=(
            re.compile(r"\b(invoice|inv)\s*(no|number|#|nomor|num)?\b", re.I),
            re.compile(r"\bno\.?\s*(invoice|faktur)\b", re.I),
        ),
    ),
    FieldPattern(
        label="invoice_date",
        patterns=(
            re.compile(r"\b(invoice\s*)?(date|tanggal|tgl)\b", re.I),
            re.compile(r"\btanggal\s*(invoice|faktur)\b", re.I),
        ),
    ),
    FieldPattern(
        label="vendor_name",
        patterns=(
            re.compile(r"\b(vendor|supplier|seller|merchant|from|penjual)\b", re.I),
            re.compile(r"\b(pt|cv)\.?\s+[a-z0-9]", re.I),
        ),
    ),
    FieldPattern(
        label="tax_number",
        patterns=(re.compile(r"\b(npwp|tax\s*id|vat\s*id)\b", re.I),),
    ),
    FieldPattern(
        label="subtotal",
        patterns=(re.compile(r"\b(sub\s*total|subtotal|dpp)\b", re.I),),
        value_pattern=re.compile(r"(\d[\d.,]+)"),
    ),
    FieldPattern(
        label="tax_amount",
        patterns=(re.compile(r"\b(ppn|pajak|tax|vat)\b", re.I),),
        value_pattern=re.compile(r"(\d[\d.,]+)"),
    ),
    FieldPattern(
        label="discount",
        patterns=(re.compile(r"\b(discount|diskon|potongan)\b", re.I),),
        value_pattern=re.compile(r"(\d[\d.,]+)"),
    ),
    FieldPattern(
        label="total_amount",
        patterns=(
            re.compile(r"\b(grand\s*total|total\s*(amount|due)?|jumlah)\b", re.I),
        ),
        value_pattern=re.compile(r"(\d[\d.,]+)"),
    ),
    FieldPattern(
        label="due_date",
        patterns=(re.compile(r"\b(due\s*date|jatuh\s*tempo)\b", re.I),),
    ),
    FieldPattern(
        label="currency",
        patterns=(re.compile(r"\b(rp|idr|usd|eur|sgd|aud|jpy)\b", re.I),),
    ),
)


PAYMENT_PATTERNS = (
    FieldPattern(
        label="transfer_status",
        patterns=(
            re.compile(r"\b(success|successful|berhasil|sukses|completed|selesai)\b", re.I),
        ),
    ),
    FieldPattern(
        label="amount",
        patterns=(re.compile(r"\b(amount|nominal|jumlah|total)\b", re.I),),
        value_pattern=re.compile(r"(\d[\d.,]+)"),
    ),
    FieldPattern(
        label="currency",
        patterns=(re.compile(r"\b(rp|idr|usd|eur|sgd|aud|jpy)\b", re.I),),
    ),
    FieldPattern(
        label="transaction_date",
        patterns=(re.compile(r"\b(date|tanggal|tgl|transaction\s*date)\b", re.I),),
    ),
    FieldPattern(
        label="transaction_time",
        patterns=(re.compile(r"\b(time|jam|waktu)\b", re.I),),
    ),
    FieldPattern(
        label="source_bank",
        patterns=(re.compile(r"\b(from\s*bank|bank\s*pengirim|source\s*bank|dari\s*bank)\b", re.I),),
    ),
    FieldPattern(
        label="source_account",
        patterns=(re.compile(r"\b(source\s*account|rekening\s*pengirim|dari\s*rekening)\b", re.I),),
    ),
    FieldPattern(
        label="source_name",
        patterns=(re.compile(r"\b(sender|pengirim|from|dari)\b", re.I),),
    ),
    FieldPattern(
        label="destination_bank",
        patterns=(re.compile(r"\b(to\s*bank|bank\s*tujuan|destination\s*bank|ke\s*bank)\b", re.I),),
    ),
    FieldPattern(
        label="destination_account",
        patterns=(re.compile(r"\b(destination\s*account|rekening\s*tujuan|ke\s*rekening)\b", re.I),),
    ),
    FieldPattern(
        label="destination_name",
        patterns=(re.compile(r"\b(recipient|penerima|beneficiary|tujuan|kepada)\b", re.I),),
    ),
    FieldPattern(
        label="reference_number",
        patterns=(
            re.compile(r"\b(reference|ref|no\.?\s*ref|transaction\s*id|trx|id\s*transaksi)\b", re.I),
        ),
    ),
    FieldPattern(
        label="channel",
        patterns=(re.compile(r"\b(bi-fast|rtgs|skn|qris|transfer|channel|metode)\b", re.I),),
    ),
)


def suggest_annotations(
    *,
    document_type: DocumentType,
    page: int,
    line_boxes: list[Any],
) -> list[InvoiceAnnotationCreate]:
    patterns = PAYMENT_PATTERNS if document_type == "payment_proof" else INVOICE_PATTERNS
    suggestions: list[InvoiceAnnotationCreate] = []
    seen_labels: set[str] = set()

    for pattern in patterns:
        match = _find_best_match(pattern, line_boxes)
        if match is None or pattern.label in seen_labels:
            continue

        line, text = match
        suggestions.append(
            InvoiceAnnotationCreate(
                page=page,
                label=pattern.label,
                bbox=BoundingBox(
                    x=max(0, line.left - 6),
                    y=max(0, line.top - 4),
                    width=line.width + 12,
                    height=line.height + 8,
                ),
                text=text,
                confidence=line.confidence,
                engine="tesseract",
                source="auto_suggest",
                status="draft",
            )
        )
        seen_labels.add(pattern.label)

    return suggestions


def _find_best_match(
    pattern: FieldPattern,
    line_boxes: list[Any],
) -> tuple[Any, str] | None:
    for line in line_boxes:
        if not any(regex.search(line.text) for regex in pattern.patterns):
            continue
        text = _extract_value(line.text, pattern.value_pattern)
        return line, text
    return None


def _extract_value(text: str, value_pattern: re.Pattern[str] | None) -> str:
    if value_pattern is not None:
        match = value_pattern.search(text)
        if match:
            return match.group(1).strip()

    parts = re.split(r"\s*[:#]\s*", text, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()

    return text.strip()
