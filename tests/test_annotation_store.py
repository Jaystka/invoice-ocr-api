from io import BytesIO

import pytest
from PIL import Image

from app.config import Settings
from app.schemas import BoundingBox, InvoiceAnnotationCreate, InvoiceAnnotationUpdate
from app.services.annotation_store import AnnotationStore


def _png_bytes(width: int = 300, height: int = 180) -> bytes:
    image = Image.new("RGB", (width, height), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_annotation_store_exports_approved_annotations(tmp_path):
    settings = Settings(annotation_storage_dir=str(tmp_path))
    store = AnnotationStore(settings)

    document = store.create_document(
        content=_png_bytes(),
        filename="invoice.png",
        content_type="image/png",
    )

    annotation = store.add_annotation(
        document.id,
        InvoiceAnnotationCreate(
            page=1,
            label="invoice_number",
            bbox=BoundingBox(x=10, y=20, width=120, height=32),
            text="INV-001",
        ),
    )
    store.update_annotation(
        document.id,
        annotation.id,
        InvoiceAnnotationUpdate(status="approved"),
    )

    dataset = store.export_dataset(approved_only=True)

    assert dataset.invoice_count == 1
    assert dataset.payment_proof_count == 0
    assert dataset.annotation_count == 1
    assert dataset.documents[0].annotations[0].label == "invoice_number"
    assert dataset.documents[0].annotations[0].text == "INV-001"


def test_annotation_store_exports_payment_proof_annotations(tmp_path):
    settings = Settings(annotation_storage_dir=str(tmp_path))
    store = AnnotationStore(settings)

    document = store.create_document(
        content=_png_bytes(),
        filename="payment.png",
        content_type="image/png",
        document_type="payment_proof",
    )

    annotation = store.add_annotation(
        document.id,
        InvoiceAnnotationCreate(
            page=1,
            label="reference_number",
            bbox=BoundingBox(x=10, y=20, width=120, height=32),
            text="TRX123",
        ),
    )
    store.update_annotation(
        document.id,
        annotation.id,
        InvoiceAnnotationUpdate(status="approved"),
    )

    dataset = store.export_dataset(approved_only=True)
    payment_docs = store.list_documents(document_type="payment_proof")

    assert payment_docs[0].document_type == "payment_proof"
    assert dataset.document_count == 1
    assert dataset.invoice_count == 0
    assert dataset.payment_proof_count == 1
    assert dataset.annotation_count == 1
    assert dataset.documents[0].document_type == "payment_proof"
    assert dataset.documents[0].annotations[0].label == "reference_number"


def test_annotation_store_rejects_box_outside_page(tmp_path):
    settings = Settings(annotation_storage_dir=str(tmp_path))
    store = AnnotationStore(settings)
    document = store.create_document(
        content=_png_bytes(width=100, height=100),
        filename="invoice.png",
        content_type="image/png",
    )

    with pytest.raises(ValueError, match="outside page bounds"):
        store.add_annotation(
            document.id,
            InvoiceAnnotationCreate(
                page=1,
                label="total_amount",
                bbox=BoundingBox(x=90, y=10, width=20, height=20),
                text="100000",
            ),
        )


def test_annotation_store_deletes_only_auto_suggestions(tmp_path):
    settings = Settings(annotation_storage_dir=str(tmp_path))
    store = AnnotationStore(settings)
    document = store.create_document(
        content=_png_bytes(width=200, height=120),
        filename="invoice.png",
        content_type="image/png",
    )

    manual = store.add_annotation(
        document.id,
        InvoiceAnnotationCreate(
            page=1,
            label="invoice_number",
            bbox=BoundingBox(x=10, y=10, width=80, height=20),
            text="INV-001",
            source="manual",
        ),
    )
    store.add_annotation(
        document.id,
        InvoiceAnnotationCreate(
            page=1,
            label="total_amount",
            bbox=BoundingBox(x=10, y=50, width=80, height=20),
            text="100000",
            source="auto_suggest",
        ),
    )

    store.delete_auto_suggestions(document.id, page=1)

    annotations = store.get_document(document.id).annotations
    assert [annotation.id for annotation in annotations] == [manual.id]
