from typing import Literal

from pydantic import BaseModel, Field


class FieldValue(BaseModel):
    value: str | int | float | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_text: str | None = None


class InvoiceFields(BaseModel):
    invoice_number: FieldValue
    invoice_date: FieldValue
    vendor_name: FieldValue
    currency: FieldValue
    subtotal: FieldValue
    tax: FieldValue
    discount: FieldValue
    grand_total: FieldValue


class OCRMeta(BaseModel):
    engine: str
    confidence: float = Field(ge=0.0, le=1.0)
    page_count: int
    source_type: Literal["image", "pdf_digital", "pdf_scanned", "pdf_mixed"]
    fallback_used: bool = False


class ValidationResult(BaseModel):
    status: Literal["valid", "warning", "unknown"]
    arithmetic_ok: bool | None = None
    calculated_total: int | None = None
    difference: int | None = None
    warnings: list[str] = []


class MatchResult(BaseModel):
    status: Literal[
        "not_requested",
        "exact_match",
        "tolerance_match",
        "mismatch",
        "insufficient_data",
    ]
    expected_total: int | None = None
    invoice_total: int | None = None
    difference: int | None = None
    tolerance_used: int | None = None
    score: float | None = None


class DocumentInfo(BaseModel):
    filename: str
    content_type: str | None = None
    size_bytes: int
    sha256: str


class AnalyzeResponse(BaseModel):
    document: DocumentInfo
    ocr: OCRMeta
    invoice: InvoiceFields
    validation: ValidationResult
    match: MatchResult
    raw_text: str
    lines: list[str]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
