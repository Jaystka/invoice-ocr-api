import hashlib
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.config import get_settings
from app.schemas import (
    AnalyzeResponse,
    DocumentInfo,
    HealthResponse,
    OCRMeta,
)
from app.services.document import load_document
from app.services.matcher import match_total
from app.services.ocr import OCRService
from app.services.parser import parse_invoice, validate_invoice


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "API untuk membaca invoice dari gambar/PDF menggunakan PaddleOCR "
        "dengan fallback Tesseract, lalu mengekstrak dan mencocokkan total."
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
    max_bytes = settings.max_file_mb * 1024 * 1024

    if not content:
        raise HTTPException(status_code=400, detail="File kosong.")

    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file maksimum {settings.max_file_mb} MB.",
        )

    allowed = {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/tiff",
    }
    if file.content_type and file.content_type not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type tidak didukung: {file.content_type}",
        )

    filename = file.filename or "invoice"
    sha256 = hashlib.sha256(content).hexdigest()

    try:
        document = load_document(
            content=content,
            filename=filename,
            content_type=file.content_type,
            settings=settings,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Dokumen tidak dapat dibaca: {exc}",
        ) from exc

    all_lines: list[str] = []
    confidences: list[float] = []
    engines: list[str] = []
    fallback_used = False

    for page in document.pages:
        if page.digital_text is not None:
            page_lines = [
                line.strip()
                for line in page.digital_text.splitlines()
                if line.strip()
            ]
            all_lines.extend(page_lines)
            confidences.append(1.0)
            engines.append("pdf_text")
            continue

        if page.image is None:
            continue

        try:
            result = ocr_service.read_image(page.image, engine=engine)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"OCR gagal pada halaman {page.page_number}: {exc}",
            ) from exc

        all_lines.extend(result.lines)
        confidences.append(result.confidence)
        engines.append(result.engine)
        fallback_used = fallback_used or result.fallback_used

    if not all_lines:
        raise HTTPException(
            status_code=422,
            detail="Tidak ada teks yang berhasil dibaca dari dokumen.",
        )

    fields = parse_invoice(all_lines)
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

    overall_confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )

    unique_engines = sorted(set(engines))
    engine_name = (
        unique_engines[0]
        if len(unique_engines) == 1
        else "+".join(unique_engines)
    )

    return AnalyzeResponse(
        document=DocumentInfo(
            filename=filename,
            content_type=file.content_type,
            size_bytes=len(content),
            sha256=sha256,
        ),
        ocr=OCRMeta(
            engine=engine_name,
            confidence=round(overall_confidence, 4),
            page_count=len(document.pages),
            source_type=document.source_type,
            fallback_used=fallback_used,
        ),
        invoice=fields,
        validation=validation,
        match=matching,
        raw_text="\n".join(all_lines),
        lines=all_lines,
    )
