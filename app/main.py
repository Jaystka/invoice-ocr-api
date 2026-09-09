from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image

from app.config import get_settings
from app.schemas import (
    AnnotationDocument,
    AnnotationDocumentSummary,
    AnnotationStatus,
    AnalyzeResponse,
    DatasetExport,
    HealthResponse,
    InvoiceAnnotation,
    InvoiceAnnotationCreate,
    InvoiceAnnotationUpdate,
    OCRCropRequest,
    OCRCropResponse,
    PaymentAnalyzeResponse,
    ReconciliationResponse,
)
from app.services.annotation_store import AnnotationStore
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
annotation_store = AnnotationStore(settings)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        version=settings.app_version,
    )


@app.get("/training", response_class=HTMLResponse)
def annotation_interface() -> HTMLResponse:
    html_path = Path(__file__).parent / "static" / "training.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Training interface not found")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post(
    f"{settings.api_prefix}/training/invoices",
    response_model=AnnotationDocument,
)
async def create_training_invoice(
    file: Annotated[UploadFile, File(description="Invoice PDF/JPG/PNG/WEBP")],
) -> AnnotationDocument:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    max_bytes = settings.max_file_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File is larger than {settings.max_file_mb} MB",
        )

    try:
        return annotation_store.create_document(
            content=content,
            filename=file.filename or "invoice",
            content_type=file.content_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    f"{settings.api_prefix}/training/invoices",
    response_model=list[AnnotationDocumentSummary],
)
def list_training_invoices() -> list[AnnotationDocumentSummary]:
    return annotation_store.list_documents()


@app.get(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}",
    response_model=AnnotationDocument,
)
def get_training_invoice(invoice_id: str) -> AnnotationDocument:
    try:
        return annotation_store.get_document(invoice_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found") from exc


@app.patch(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}/status/{{status}}",
    response_model=AnnotationDocument,
)
def update_training_invoice_status(
    invoice_id: str,
    status: AnnotationStatus,
) -> AnnotationDocument:
    try:
        return annotation_store.update_document_status(invoice_id, status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found") from exc


@app.delete(f"{settings.api_prefix}/training/invoices/{{invoice_id}}")
def delete_training_invoice(invoice_id: str) -> dict[str, str]:
    annotation_store.delete_document(invoice_id)
    return {"status": "deleted"}


@app.get(f"{settings.api_prefix}/training/invoices/{{invoice_id}}/pages/{{page}}.png")
def get_training_invoice_page(invoice_id: str, page: int) -> FileResponse:
    try:
        path = annotation_store.page_image_path(invoice_id, page)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Page not found") from exc
    return FileResponse(path, media_type="image/png")


@app.post(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}/ocr-crop",
    response_model=OCRCropResponse,
)
def ocr_training_crop(
    invoice_id: str,
    payload: OCRCropRequest,
) -> OCRCropResponse:
    try:
        path = annotation_store.page_image_path(invoice_id, payload.page)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Page not found") from exc

    image = Image.open(path).convert("RGB")
    left = int(max(0, payload.bbox.x))
    top = int(max(0, payload.bbox.y))
    right = int(min(image.width, payload.bbox.x + payload.bbox.width))
    bottom = int(min(image.height, payload.bbox.y + payload.bbox.height))
    if right <= left or bottom <= top:
        raise HTTPException(status_code=400, detail="Invalid crop bounds")

    result = ocr_service.read_image(image.crop((left, top, right, bottom)), payload.engine)
    return OCRCropResponse(
        text=result.text,
        lines=result.lines,
        confidence=result.confidence,
        engine=result.engine,
        fallback_used=result.fallback_used,
    )


@app.post(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}/annotations",
    response_model=InvoiceAnnotation,
)
def create_training_annotation(
    invoice_id: str,
    payload: InvoiceAnnotationCreate,
) -> InvoiceAnnotation:
    try:
        return annotation_store.add_annotation(invoice_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}/annotations/{{annotation_id}}",
    response_model=InvoiceAnnotation,
)
def update_training_annotation(
    invoice_id: str,
    annotation_id: str,
    payload: InvoiceAnnotationUpdate,
) -> InvoiceAnnotation:
    try:
        return annotation_store.update_annotation(invoice_id, annotation_id, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Annotation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}/annotations/{{annotation_id}}"
)
def delete_training_annotation(
    invoice_id: str,
    annotation_id: str,
) -> dict[str, str]:
    try:
        annotation_store.delete_annotation(invoice_id, annotation_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Annotation not found") from exc
    return {"status": "deleted"}


@app.get(
    f"{settings.api_prefix}/training/dataset/export",
    response_model=DatasetExport,
)
def export_training_dataset(
    approved_only: bool = True,
) -> DatasetExport:
    return annotation_store.export_dataset(approved_only=approved_only)


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
