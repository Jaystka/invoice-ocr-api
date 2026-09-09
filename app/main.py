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
    AutoSuggestRequest,
    AutoSuggestResponse,
    BoundingBox,
    AnalyzeResponse,
    DatasetExport,
    DocumentType,
    HealthResponse,
    InvoiceAnnotation,
    InvoiceAnnotationCreate,
    InvoiceAnnotationUpdate,
    ModelVersion,
    OCRCropRequest,
    OCRCropResponse,
    PaymentAnalyzeResponse,
    ReconciliationResponse,
    TrainingJob,
    TrainingJobCreate,
)
from app.services.annotation_suggest import suggest_annotations
from app.services.annotation_store import AnnotationStore
from app.services.matcher import match_total
from app.services.ocr import OCRService
from app.services.parser import parse_invoice, validate_invoice
from app.services.payment_validator import (
    reconcile_invoice_payment,
    validate_payment,
)
from app.services.pipeline import extract_document
from app.services.training_store import TrainingStore
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
training_store = TrainingStore(settings)


async def create_training_document(
    *,
    file: UploadFile,
    document_type: DocumentType,
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
            filename=file.filename or document_type,
            content_type=file.content_type,
            document_type=document_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    return await create_training_document(file=file, document_type="invoice")


@app.post(
    f"{settings.api_prefix}/training/payments",
    response_model=AnnotationDocument,
)
async def create_training_payment_proof(
    file: Annotated[
        UploadFile,
        File(description="Bukti transfer / payment receipt PDF/JPG/PNG/WEBP"),
    ],
) -> AnnotationDocument:
    return await create_training_document(file=file, document_type="payment_proof")


@app.post(
    f"{settings.api_prefix}/training/documents/{{document_type}}",
    response_model=AnnotationDocument,
)
async def create_training_document_by_type(
    document_type: DocumentType,
    file: Annotated[UploadFile, File(description="Invoice/payment PDF/JPG/PNG/WEBP")],
) -> AnnotationDocument:
    return await create_training_document(file=file, document_type=document_type)


@app.get(
    f"{settings.api_prefix}/training/invoices",
    response_model=list[AnnotationDocumentSummary],
)
def list_training_invoices() -> list[AnnotationDocumentSummary]:
    return annotation_store.list_documents(document_type="invoice")


@app.get(
    f"{settings.api_prefix}/training/payments",
    response_model=list[AnnotationDocumentSummary],
)
def list_training_payment_proofs() -> list[AnnotationDocumentSummary]:
    return annotation_store.list_documents(document_type="payment_proof")


@app.get(
    f"{settings.api_prefix}/training/documents",
    response_model=list[AnnotationDocumentSummary],
)
def list_training_documents(
    document_type: DocumentType | None = None,
) -> list[AnnotationDocumentSummary]:
    return annotation_store.list_documents(document_type=document_type)


@app.get(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}",
    response_model=AnnotationDocument,
)
def get_training_invoice(invoice_id: str) -> AnnotationDocument:
    try:
        return annotation_store.get_document(invoice_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Invoice not found") from exc


@app.get(
    f"{settings.api_prefix}/training/payments/{{payment_id}}",
    response_model=AnnotationDocument,
)
def get_training_payment_proof(payment_id: str) -> AnnotationDocument:
    try:
        return annotation_store.get_document(payment_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Payment proof not found") from exc


@app.get(
    f"{settings.api_prefix}/training/documents/{{document_id}}",
    response_model=AnnotationDocument,
)
def get_training_document(document_id: str) -> AnnotationDocument:
    try:
        return annotation_store.get_document(document_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Training document not found") from exc


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


@app.patch(
    f"{settings.api_prefix}/training/payments/{{payment_id}}/status/{{status}}",
    response_model=AnnotationDocument,
)
def update_training_payment_status(
    payment_id: str,
    status: AnnotationStatus,
) -> AnnotationDocument:
    try:
        return annotation_store.update_document_status(payment_id, status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Payment proof not found") from exc


@app.patch(
    f"{settings.api_prefix}/training/documents/{{document_id}}/status/{{status}}",
    response_model=AnnotationDocument,
)
def update_training_document_status(
    document_id: str,
    status: AnnotationStatus,
) -> AnnotationDocument:
    try:
        return annotation_store.update_document_status(document_id, status)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Training document not found") from exc


@app.delete(f"{settings.api_prefix}/training/invoices/{{invoice_id}}")
def delete_training_invoice(invoice_id: str) -> dict[str, str]:
    annotation_store.delete_document(invoice_id)
    return {"status": "deleted"}


@app.delete(f"{settings.api_prefix}/training/payments/{{payment_id}}")
def delete_training_payment_proof(payment_id: str) -> dict[str, str]:
    annotation_store.delete_document(payment_id)
    return {"status": "deleted"}


@app.delete(f"{settings.api_prefix}/training/documents/{{document_id}}")
def delete_training_document(document_id: str) -> dict[str, str]:
    annotation_store.delete_document(document_id)
    return {"status": "deleted"}


@app.get(f"{settings.api_prefix}/training/invoices/{{invoice_id}}/pages/{{page}}.png")
def get_training_invoice_page(invoice_id: str, page: int) -> FileResponse:
    try:
        path = annotation_store.page_image_path(invoice_id, page)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Page not found") from exc
    return FileResponse(path, media_type="image/png")


@app.get(f"{settings.api_prefix}/training/payments/{{payment_id}}/pages/{{page}}.png")
def get_training_payment_page(payment_id: str, page: int) -> FileResponse:
    try:
        path = annotation_store.page_image_path(payment_id, page)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Page not found") from exc
    return FileResponse(path, media_type="image/png")


@app.get(f"{settings.api_prefix}/training/documents/{{document_id}}/pages/{{page}}.png")
def get_training_document_page(document_id: str, page: int) -> FileResponse:
    try:
        path = annotation_store.page_image_path(document_id, page)
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
    f"{settings.api_prefix}/training/payments/{{payment_id}}/ocr-crop",
    response_model=OCRCropResponse,
)
def ocr_training_payment_crop(
    payment_id: str,
    payload: OCRCropRequest,
) -> OCRCropResponse:
    return ocr_training_crop(payment_id, payload)


@app.post(
    f"{settings.api_prefix}/training/documents/{{document_id}}/ocr-crop",
    response_model=OCRCropResponse,
)
def ocr_training_document_crop(
    document_id: str,
    payload: OCRCropRequest,
) -> OCRCropResponse:
    return ocr_training_crop(document_id, payload)


def create_auto_suggestions(
    document_id: str,
    payload: AutoSuggestRequest,
) -> AutoSuggestResponse:
    try:
        document = annotation_store.get_document(document_id)
        path = annotation_store.page_image_path(document_id, payload.page)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Training document not found") from exc

    if payload.replace_existing_auto:
        annotation_store.delete_auto_suggestions(document_id, payload.page)

    image = Image.open(path).convert("RGB")
    try:
        line_boxes = ocr_service.read_line_boxes(image)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Initial OCR failed: {exc}") from exc

    existing = annotation_store.get_document(document_id).annotations
    existing_labels = {
        annotation.label
        for annotation in existing
        if annotation.page == payload.page and annotation.source != "auto_suggest"
    }
    suggested_payloads = [
        _clamp_annotation_to_page(suggestion, document)
        for suggestion in suggest_annotations(
            document_type=document.document_type,
            page=payload.page,
            line_boxes=line_boxes,
        )
        if suggestion.label not in existing_labels
    ]

    created: list[InvoiceAnnotation] = []
    for suggestion in suggested_payloads:
        created.append(annotation_store.add_annotation(document_id, suggestion))

    return AutoSuggestResponse(created_count=len(created), annotations=created)


def _clamp_annotation_to_page(
    suggestion: InvoiceAnnotationCreate,
    document: AnnotationDocument,
) -> InvoiceAnnotationCreate:
    page = next(page for page in document.pages if page.page == suggestion.page)
    x = min(max(0, suggestion.bbox.x), page.width - 1)
    y = min(max(0, suggestion.bbox.y), page.height - 1)
    width = min(suggestion.bbox.width, page.width - x)
    height = min(suggestion.bbox.height, page.height - y)
    return suggestion.model_copy(
        update={
            "bbox": BoundingBox(
                x=x,
                y=y,
                width=max(1, width),
                height=max(1, height),
            )
        }
    )


@app.post(
    f"{settings.api_prefix}/training/invoices/{{invoice_id}}/auto-suggest",
    response_model=AutoSuggestResponse,
)
def auto_suggest_training_invoice(
    invoice_id: str,
    payload: AutoSuggestRequest,
) -> AutoSuggestResponse:
    return create_auto_suggestions(invoice_id, payload)


@app.post(
    f"{settings.api_prefix}/training/payments/{{payment_id}}/auto-suggest",
    response_model=AutoSuggestResponse,
)
def auto_suggest_training_payment(
    payment_id: str,
    payload: AutoSuggestRequest,
) -> AutoSuggestResponse:
    return create_auto_suggestions(payment_id, payload)


@app.post(
    f"{settings.api_prefix}/training/documents/{{document_id}}/auto-suggest",
    response_model=AutoSuggestResponse,
)
def auto_suggest_training_document(
    document_id: str,
    payload: AutoSuggestRequest,
) -> AutoSuggestResponse:
    return create_auto_suggestions(document_id, payload)


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


@app.post(
    f"{settings.api_prefix}/training/payments/{{payment_id}}/annotations",
    response_model=InvoiceAnnotation,
)
def create_training_payment_annotation(
    payment_id: str,
    payload: InvoiceAnnotationCreate,
) -> InvoiceAnnotation:
    return create_training_annotation(payment_id, payload)


@app.post(
    f"{settings.api_prefix}/training/documents/{{document_id}}/annotations",
    response_model=InvoiceAnnotation,
)
def create_training_document_annotation(
    document_id: str,
    payload: InvoiceAnnotationCreate,
) -> InvoiceAnnotation:
    return create_training_annotation(document_id, payload)


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


@app.patch(
    f"{settings.api_prefix}/training/payments/{{payment_id}}/annotations/{{annotation_id}}",
    response_model=InvoiceAnnotation,
)
def update_training_payment_annotation(
    payment_id: str,
    annotation_id: str,
    payload: InvoiceAnnotationUpdate,
) -> InvoiceAnnotation:
    return update_training_annotation(payment_id, annotation_id, payload)


@app.patch(
    f"{settings.api_prefix}/training/documents/{{document_id}}/annotations/{{annotation_id}}",
    response_model=InvoiceAnnotation,
)
def update_training_document_annotation(
    document_id: str,
    annotation_id: str,
    payload: InvoiceAnnotationUpdate,
) -> InvoiceAnnotation:
    return update_training_annotation(document_id, annotation_id, payload)


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


@app.delete(
    f"{settings.api_prefix}/training/payments/{{payment_id}}/annotations/{{annotation_id}}"
)
def delete_training_payment_annotation(
    payment_id: str,
    annotation_id: str,
) -> dict[str, str]:
    return delete_training_annotation(payment_id, annotation_id)


@app.delete(
    f"{settings.api_prefix}/training/documents/{{document_id}}/annotations/{{annotation_id}}"
)
def delete_training_document_annotation(
    document_id: str,
    annotation_id: str,
) -> dict[str, str]:
    return delete_training_annotation(document_id, annotation_id)


@app.get(
    f"{settings.api_prefix}/training/dataset/export",
    response_model=DatasetExport,
)
def export_training_dataset(
    approved_only: bool = True,
) -> DatasetExport:
    return annotation_store.export_dataset(approved_only=approved_only)


@app.post(
    f"{settings.api_prefix}/training/jobs",
    response_model=TrainingJob,
)
def create_training_job(
    payload: TrainingJobCreate | None = None,
) -> TrainingJob:
    payload = payload or TrainingJobCreate()
    dataset = annotation_store.export_dataset(approved_only=payload.approved_only)
    try:
        job = training_store.create_job(dataset=dataset, notes=payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    training_store.start_background_job(job.id)
    return job


@app.get(
    f"{settings.api_prefix}/training/jobs",
    response_model=list[TrainingJob],
)
def list_training_jobs() -> list[TrainingJob]:
    return training_store.list_jobs()


@app.get(
    f"{settings.api_prefix}/training/jobs/{{job_id}}",
    response_model=TrainingJob,
)
def get_training_job(job_id: str) -> TrainingJob:
    try:
        return training_store.get_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Training job not found") from exc


@app.get(
    f"{settings.api_prefix}/training/models",
    response_model=list[ModelVersion],
)
def list_training_models() -> list[ModelVersion]:
    return training_store.list_models()


@app.get(
    f"{settings.api_prefix}/training/models/{{model_id}}",
    response_model=ModelVersion,
)
def get_training_model(model_id: str) -> ModelVersion:
    try:
        return training_store.get_model(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Model version not found") from exc


@app.post(
    f"{settings.api_prefix}/training/models/{{model_id}}/promote",
    response_model=ModelVersion,
)
def promote_training_model(model_id: str) -> ModelVersion:
    try:
        return training_store.promote_model(model_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Model version not found") from exc


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
