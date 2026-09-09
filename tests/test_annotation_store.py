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
    assert dataset.annotation_count == 1
    assert dataset.documents[0].annotations[0].label == "invoice_number"
    assert dataset.documents[0].annotations[0].text == "INV-001"


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
