import hashlib
from dataclasses import dataclass

from fastapi import HTTPException

from app.config import Settings
from app.schemas import DocumentInfo, OCRMeta
from app.services.document import load_document
from app.services.ocr import OCRService


ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
}


@dataclass
class ExtractedDocument:
    document: DocumentInfo
    ocr: OCRMeta
    lines: list[str]

    @property
    def raw_text(self) -> str:
        return "\n".join(self.lines)


def validate_upload_bytes(
    content: bytes,
    filename: str,
    content_type: str | None,
    settings: Settings,
) -> None:
    if not content:
        raise HTTPException(status_code=400, detail=f"File {filename} kosong.")

    max_bytes = settings.max_file_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Ukuran file maksimum {settings.max_file_mb} MB.",
        )

    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type tidak didukung: {content_type}",
        )


def extract_document(
    *,
    content: bytes,
    filename: str,
    content_type: str | None,
    engine: str,
    settings: Settings,
    ocr_service: OCRService,
) -> ExtractedDocument:
    validate_upload_bytes(content, filename, content_type, settings)

    try:
        loaded = load_document(
            content=content,
            filename=filename,
            content_type=content_type,
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

    for page in loaded.pages:
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

    overall_confidence = (
        sum(confidences) / len(confidences) if confidences else 0.0
    )
    unique_engines = sorted(set(engines))
    engine_name = (
        unique_engines[0]
        if len(unique_engines) == 1
        else "+".join(unique_engines)
    )

    return ExtractedDocument(
        document=DocumentInfo(
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        ),
        ocr=OCRMeta(
            engine=engine_name,
            confidence=round(overall_confidence, 4),
            page_count=len(loaded.pages),
            source_type=loaded.source_type,
            fallback_used=fallback_used,
        ),
        lines=all_lines,
    )
