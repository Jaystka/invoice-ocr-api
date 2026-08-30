from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, UploadFile

from app.config import get_settings
from app.schemas import (
    AnalyzeResponse,
    HealthResponse,
    PaymentAnalyzeResponse,
    ReconciliationResponse,
)
from app.services.matcher import match_total
from app.services.ocr import OCRService
from app.services.parser import parse_invoice, validate_invoice
from app.services.payment_validator import (
    reconcile_invoice_payment,
    validate_payment,
)
from app.services.pipeline import extract_document
from app.services.transfer_parser import parse_transfer_proof


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Self-hosted API untuk membaca invoice dan bukti transfer dari "
        "gambar/PDF menggunakan PaddleOCR dengan fallback Tesseract. "
        "Service melakukan extraction, content validation, dan reconciliation."
    ),
)

ocr_service = OCRService(settings)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
    )


@app.post(
    f"{settings.api_prefix}/invoices/analyze",
    response_model=AnalyzeResponse,
)
async def analyze_invoice(
    file: Annotated[UploadFile, File(description="Invoice PDF/JPG/PNG/WEBP")],
    expected_total: Annotated[int | None, Form()] = None,
    engine: Annotated[Literal["auto", "paddle", "tesseract"], Form()] = "auto",
    tolerance_amount: Annotated[int | None, Form()] = None,
    tolerance_percent: Annotated[float | None, Form()] = None,
) -> AnalyzeResponse:
    content = await file.read()
    extracted = extract_document(
        content=content,
        filename=file.filename or "invoice",
        content_type=file.content_type,
        engine=engine,
        settings=settings,
        ocr_service=ocr_service,
    )

    fields = parse_invoice(extracted.lines)
    validation = validate_invoice(fields)
    invoice_total = (
        fields.grand_total.value
        if isinstance(fields.grand_total.value, int)
        else None
    )

    matching = match_total(
        invoice_total=invoice_total,
        expected_total=expected_total,
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

    return AnalyzeResponse(
        document=extracted.document,
        ocr=extracted.ocr,
        invoice=fields,
        validation=validation,
        match=matching,
        raw_text=extracted.raw_text,
        lines=extracted.lines,
    )


@app.post(
    f"{settings.api_prefix}/payments/analyze",
    response_model=PaymentAnalyzeResponse,
)
async def analyze_payment_proof(
    file: Annotated[
        UploadFile,
        File(description="Bukti transfer / payment receipt PDF/JPG/PNG/WEBP"),
    ],
    expected_amount: Annotated[int | None, Form()] = None,
    expected_recipient_name: Annotated[str | None, Form()] = None,
    expected_recipient_account: Annotated[str | None, Form()] = None,
    expected_recipient_bank: Annotated[str | None, Form()] = None,
    expected_reference: Annotated[str | None, Form()] = None,
    engine: Annotated[Literal["auto", "paddle", "tesseract"], Form()] = "auto",
    tolerance_amount: Annotated[int | None, Form()] = None,
    tolerance_percent: Annotated[float | None, Form()] = None,
) -> PaymentAnalyzeResponse:
    content = await file.read()
    extracted = extract_document(
        content=content,
        filename=file.filename or "payment-proof",
        content_type=file.content_type,
        engine=engine,
        settings=settings,
        ocr_service=ocr_service,
    )

    payment = parse_transfer_proof(extracted.lines)
    validation = validate_payment(
        payment,
        settings=settings,
        expected_amount=expected_amount,
        expected_recipient_name=expected_recipient_name,
        expected_recipient_account=expected_recipient_account,
        expected_recipient_bank=expected_recipient_bank,
        expected_reference=expected_reference,
        tolerance_amount=tolerance_amount,
        tolerance_percent=tolerance_percent,
    )

    return PaymentAnalyzeResponse(
        document=extracted.document,
        ocr=extracted.ocr,
        payment=payment,
        validation=validation,
        raw_text=extracted.raw_text,
        lines=extracted.lines,
    )


@app.post(
    f"{settings.api_prefix}/reconciliation/validate",
    response_model=ReconciliationResponse,
)
async def reconcile(
    invoice_file: Annotated[
        UploadFile,
        File(description="Invoice PDF/JPG/PNG/WEBP"),
    ],
    payment_file: Annotated[
        UploadFile,
        File(description="Bukti transfer PDF/JPG/PNG/WEBP"),
    ],
    expected_recipient_name: Annotated[str | None, Form()] = None,
    expected_recipient_account: Annotated[str | None, Form()] = None,
    expected_recipient_bank: Annotated[str | None, Form()] = None,
    engine: Annotated[Literal["auto", "paddle", "tesseract"], Form()] = "auto",
    tolerance_amount: Annotated[int | None, Form()] = None,
    tolerance_percent: Annotated[float | None, Form()] = None,
) -> ReconciliationResponse:
    invoice_content = await invoice_file.read()
    payment_content = await payment_file.read()

    invoice_doc = extract_document(
        content=invoice_content,
        filename=invoice_file.filename or "invoice",
        content_type=invoice_file.content_type,
        engine=engine,
        settings=settings,
        ocr_service=ocr_service,
    )
    payment_doc = extract_document(
        content=payment_content,
        filename=payment_file.filename or "payment-proof",
        content_type=payment_file.content_type,
        engine=engine,
        settings=settings,
        ocr_service=ocr_service,
    )

    invoice = parse_invoice(invoice_doc.lines)
    invoice_validation = validate_invoice(invoice)
    invoice_total = (
        invoice.grand_total.value
        if isinstance(invoice.grand_total.value, int)
        else None
    )

    payment = parse_transfer_proof(payment_doc.lines)
    payment_validation = validate_payment(
        payment,
        settings=settings,
        expected_amount=invoice_total,
        expected_recipient_name=expected_recipient_name,
        expected_recipient_account=expected_recipient_account,
        expected_recipient_bank=expected_recipient_bank,
        tolerance_amount=tolerance_amount,
        tolerance_percent=tolerance_percent,
    )

    ta = (
        tolerance_amount
        if tolerance_amount is not None
        else settings.default_tolerance_amount
    )
    tp = (
        tolerance_percent
        if tolerance_percent is not None
        else settings.default_tolerance_percent
    )

    reconciliation = reconcile_invoice_payment(
        invoice_total=invoice_total,
        invoice_number=(
            str(invoice.invoice_number.value)
            if invoice.invoice_number.value
            else None
        ),
        payment=payment,
        payment_validation=payment_validation,
        tolerance_amount=ta,
        tolerance_percent=tp,
    )

    return ReconciliationResponse(
        invoice_document=invoice_doc.document,
        payment_document=payment_doc.document,
        invoice_ocr=invoice_doc.ocr,
        payment_ocr=payment_doc.ocr,
        invoice=invoice,
        payment=payment,
        invoice_validation=invoice_validation,
        payment_validation=payment_validation,
        reconciliation=reconciliation,
    )
