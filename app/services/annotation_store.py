from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import fitz
from PIL import Image

from app.config import Settings
from app.schemas import (
    AnnotationDocument,
    AnnotationDocumentSummary,
    AnnotationPage,
    BoundingBox,
    DatasetExport,
    InvoiceAnnotation,
    InvoiceAnnotationCreate,
    InvoiceAnnotationUpdate,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnnotationStore:
    def __init__(self, settings: Settings):
        self.root = Path(settings.annotation_storage_dir)
        self.pdf_dpi = settings.pdf_dpi
        self.root.mkdir(parents=True, exist_ok=True)

    def create_document(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str | None,
    ) -> AnnotationDocument:
        invoice_id = uuid4().hex
        doc_dir = self._doc_dir(invoice_id)
        pages_dir = doc_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(filename).suffix or ".bin"
        original_path = doc_dir / f"original{suffix}"
        original_path.write_bytes(content)

        pages = self._render_pages(
            content=content,
            filename=filename,
            content_type=content_type,
            pages_dir=pages_dir,
        )

        created_at = now_iso()
        document = AnnotationDocument(
            id=invoice_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            page_count=len(pages),
            status="draft",
            created_at=created_at,
            updated_at=created_at,
            pages=[
                AnnotationPage(
                    page=page.page,
                    width=page.width,
                    height=page.height,
                    image_url=f"/api/v1/training/invoices/{invoice_id}/pages/{page.page}.png",
                )
                for page in pages
            ],
            annotations=[],
        )
        self._save_document(document)
        return document

    def list_documents(self) -> list[AnnotationDocumentSummary]:
        summaries: list[AnnotationDocumentSummary] = []
        for metadata_path in sorted(self.root.glob("*/metadata.json")):
            document = self._read_document(metadata_path.parent.name)
            approved = sum(1 for ann in document.annotations if ann.status == "approved")
            summaries.append(
                AnnotationDocumentSummary(
                    id=document.id,
                    filename=document.filename,
                    content_type=document.content_type,
                    size_bytes=document.size_bytes,
                    sha256=document.sha256,
                    page_count=document.page_count,
                    status=document.status,
                    annotation_count=len(document.annotations),
                    approved_annotation_count=approved,
                    created_at=document.created_at,
                    updated_at=document.updated_at,
                )
            )
        return summaries

    def get_document(self, invoice_id: str) -> AnnotationDocument:
        return self._read_document(invoice_id)

    def update_document_status(
        self,
        invoice_id: str,
        status: str,
    ) -> AnnotationDocument:
        document = self._read_document(invoice_id)
        document.status = status  # type: ignore[assignment]
        document.updated_at = now_iso()
        self._save_document(document)
        return document

    def delete_document(self, invoice_id: str) -> None:
        doc_dir = self._doc_dir(invoice_id)
        if doc_dir.exists():
            shutil.rmtree(doc_dir)

    def page_image_path(self, invoice_id: str, page: int) -> Path:
        path = self._doc_dir(invoice_id) / "pages" / f"{page}.png"
        if not path.exists():
            raise FileNotFoundError(f"Page {page} not found")
        return path

    def add_annotation(
        self,
        invoice_id: str,
        payload: InvoiceAnnotationCreate,
    ) -> InvoiceAnnotation:
        document = self._read_document(invoice_id)
        self._validate_page_and_box(document, payload.page, payload.bbox)
        timestamp = now_iso()
        annotation = InvoiceAnnotation(
            id=uuid4().hex,
            invoice_id=invoice_id,
            page=payload.page,
            label=payload.label,
            bbox=payload.bbox,
            text=payload.text,
            source=payload.source,
            status=payload.status,
            created_at=timestamp,
            updated_at=timestamp,
        )
        document.annotations.append(annotation)
        document.updated_at = timestamp
        self._save_document(document)
        return annotation

    def update_annotation(
        self,
        invoice_id: str,
        annotation_id: str,
        payload: InvoiceAnnotationUpdate,
    ) -> InvoiceAnnotation:
        document = self._read_document(invoice_id)
        for index, annotation in enumerate(document.annotations):
            if annotation.id != annotation_id:
                continue

            update = payload.model_dump(exclude_unset=True)
            page = update.get("page", annotation.page)
            bbox = update.get("bbox", annotation.bbox)
            self._validate_page_and_box(document, page, bbox)

            next_annotation = annotation.model_copy(update=update)
            next_annotation.updated_at = now_iso()
            document.annotations[index] = next_annotation
            document.updated_at = next_annotation.updated_at
            self._save_document(document)
            return next_annotation

        raise KeyError(annotation_id)

    def delete_annotation(self, invoice_id: str, annotation_id: str) -> None:
        document = self._read_document(invoice_id)
        remaining = [
            annotation
            for annotation in document.annotations
            if annotation.id != annotation_id
        ]
        if len(remaining) == len(document.annotations):
            raise KeyError(annotation_id)

        document.annotations = remaining
        document.updated_at = now_iso()
        self._save_document(document)

    def export_dataset(self, approved_only: bool = True) -> DatasetExport:
        documents: list[AnnotationDocument] = []
        annotation_count = 0
        for summary in self.list_documents():
            document = self._read_document(summary.id)
            if approved_only:
                document.annotations = [
                    annotation
                    for annotation in document.annotations
                    if annotation.status == "approved"
                ]
            if not document.annotations:
                continue
            annotation_count += len(document.annotations)
            documents.append(document)

        return DatasetExport(
            dataset_version=f"dataset_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            generated_at=now_iso(),
            invoice_count=len(documents),
            annotation_count=annotation_count,
            documents=documents,
        )

    def _render_pages(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str | None,
        pages_dir: Path,
    ) -> list[AnnotationPage]:
        lower_name = filename.lower()
        is_pdf = lower_name.endswith(".pdf") or content_type == "application/pdf"

        if not is_pdf:
            image = Image.open(BytesIO(content)).convert("RGB")
            path = pages_dir / "1.png"
            image.save(path)
            return [AnnotationPage(page=1, width=image.width, height=image.height, image_url="")]

        doc = fitz.open(stream=content, filetype="pdf")
        pages: list[AnnotationPage] = []
        scale = self.pdf_dpi / 72
        matrix = fitz.Matrix(scale, scale)
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
            path = pages_dir / f"{index}.png"
            image.save(path)
            pages.append(
                AnnotationPage(
                    page=index,
                    width=image.width,
                    height=image.height,
                    image_url="",
                )
            )
        return pages

    def _doc_dir(self, invoice_id: str) -> Path:
        return self.root / invoice_id

    def _metadata_path(self, invoice_id: str) -> Path:
        return self._doc_dir(invoice_id) / "metadata.json"

    def _read_document(self, invoice_id: str) -> AnnotationDocument:
        path = self._metadata_path(invoice_id)
        if not path.exists():
            raise FileNotFoundError(invoice_id)
        return AnnotationDocument.model_validate_json(path.read_text())

    def _save_document(self, document: AnnotationDocument) -> None:
        path = self._metadata_path(document.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _validate_page_and_box(
        document: AnnotationDocument,
        page_number: int,
        bbox: BoundingBox,
    ) -> None:
        page = next((page for page in document.pages if page.page == page_number), None)
        if page is None:
            raise ValueError(f"Page {page_number} not found")
        if bbox.x + bbox.width > page.width or bbox.y + bbox.height > page.height:
            raise ValueError("Bounding box is outside page bounds")
