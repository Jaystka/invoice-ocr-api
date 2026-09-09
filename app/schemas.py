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


class TransferFields(BaseModel):
    transfer_status: FieldValue
    amount: FieldValue
    currency: FieldValue
    transaction_date: FieldValue
    transaction_time: FieldValue
    source_bank: FieldValue
    source_account: FieldValue
    source_name: FieldValue
    destination_bank: FieldValue
    destination_account: FieldValue
    destination_name: FieldValue
    reference_number: FieldValue
    channel: FieldValue


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
    warnings: list[str] = Field(default_factory=list)


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


class ValidationCheck(BaseModel):
    name: str
    status: Literal["pass", "warning", "fail", "not_checked"]
    expected: str | int | float | None = None
    actual: str | int | float | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None


class PaymentValidationResult(BaseModel):
    status: Literal["valid", "review", "invalid"]
    score: float = Field(ge=0.0, le=1.0)
    checks: list[ValidationCheck] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    # Important: OCR validates document content consistency, not bank authenticity.
    verification_scope: Literal["content_consistency_only"] = "content_consistency_only"


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


class PaymentAnalyzeResponse(BaseModel):
    document: DocumentInfo
    ocr: OCRMeta
    payment: TransferFields
    validation: PaymentValidationResult
    raw_text: str
    lines: list[str]


class ReconciliationResult(BaseModel):
    status: Literal["matched", "review", "mismatch", "insufficient_data"]
    score: float = Field(ge=0.0, le=1.0)
    invoice_total: int | None = None
    paid_amount: int | None = None
    difference: int | None = None
    invoice_number: str | None = None
    payment_reference: str | None = None
    checks: list[ValidationCheck] = Field(default_factory=list)


class ReconciliationResponse(BaseModel):
    invoice_document: DocumentInfo
    payment_document: DocumentInfo
    invoice_ocr: OCRMeta
    payment_ocr: OCRMeta
    invoice: InvoiceFields
    payment: TransferFields
    invoice_validation: ValidationResult
    payment_validation: PaymentValidationResult
    reconciliation: ReconciliationResult


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str


AnnotationStatus = Literal["draft", "reviewed", "approved", "rejected"]


class BoundingBox(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class InvoiceAnnotation(BaseModel):
    id: str
    invoice_id: str
    page: int = Field(ge=1)
    label: str
    bbox: BoundingBox
    text: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    engine: str | None = None
    source: Literal["manual", "ocr_crop", "auto_suggest"] = "manual"
    status: AnnotationStatus = "draft"
    created_at: str
    updated_at: str


class InvoiceAnnotationCreate(BaseModel):
    page: int = Field(ge=1)
    label: str
    bbox: BoundingBox
    text: str | None = None
    source: Literal["manual", "ocr_crop", "auto_suggest"] = "manual"
    status: AnnotationStatus = "draft"


class InvoiceAnnotationUpdate(BaseModel):
    page: int | None = Field(default=None, ge=1)
    label: str | None = None
    bbox: BoundingBox | None = None
    text: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    engine: str | None = None
    source: Literal["manual", "ocr_crop", "auto_suggest"] | None = None
    status: AnnotationStatus | None = None


class OCRCropRequest(BaseModel):
    page: int = Field(ge=1)
    bbox: BoundingBox
    engine: Literal["auto", "paddle", "tesseract"] = "auto"


class OCRCropResponse(BaseModel):
    text: str
    lines: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    engine: str
    fallback_used: bool = False


class AnnotationPage(BaseModel):
    page: int
    width: int
    height: int
    image_url: str


class AnnotationDocument(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    sha256: str
    page_count: int
    status: AnnotationStatus = "draft"
    created_at: str
    updated_at: str
    pages: list[AnnotationPage]
    annotations: list[InvoiceAnnotation] = Field(default_factory=list)


class AnnotationDocumentSummary(BaseModel):
    id: str
    filename: str
    content_type: str | None = None
    size_bytes: int
    sha256: str
    page_count: int
    status: AnnotationStatus
    annotation_count: int
    approved_annotation_count: int
    created_at: str
    updated_at: str


class DatasetExport(BaseModel):
    dataset_version: str
    generated_at: str
    invoice_count: int
    annotation_count: int
    documents: list[AnnotationDocument]
