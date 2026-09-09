from app.config import Settings
from app.schemas import AnnotationDocument, DatasetExport
from app.services.training_store import TrainingStore


def _dataset() -> DatasetExport:
    return DatasetExport(
        dataset_version="dataset_test",
        generated_at="2026-09-09T00:00:00+00:00",
        document_count=1,
        invoice_count=1,
        payment_proof_count=0,
        annotation_count=1,
        documents=[
            AnnotationDocument(
                id="doc_1",
                document_type="invoice",
                filename="invoice.png",
                size_bytes=100,
                sha256="hash",
                page_count=1,
                created_at="2026-09-09T00:00:00+00:00",
                updated_at="2026-09-09T00:00:00+00:00",
                pages=[],
                annotations=[],
            )
        ],
    )


def test_training_store_runs_job_and_creates_staging_model(tmp_path):
    settings = Settings(annotation_storage_dir=str(tmp_path))
    store = TrainingStore(settings)

    job = store.create_job(dataset=_dataset(), notes="test")
    finished = store.run_job(job.id)
    model = store.get_model(finished.model_id)

    assert finished.status == "completed"
    assert model.status == "staging"
    assert model.metrics["annotation_count"] == 1
    assert model.metrics["training_mode"] == "dataset_snapshot"


def test_training_store_promotes_model_and_archives_previous(tmp_path):
    settings = Settings(annotation_storage_dir=str(tmp_path))
    store = TrainingStore(settings)

    first = store.run_job(store.create_job(dataset=_dataset()).id)
    second = store.run_job(store.create_job(dataset=_dataset()).id)

    store.promote_model(first.model_id)
    promoted = store.promote_model(second.model_id)
    archived = store.get_model(first.model_id)

    assert promoted.status == "production"
    assert archived.status == "archived"


def test_training_store_runs_external_training_command(tmp_path):
    marker = tmp_path / "marker.txt"
    settings = Settings(
        annotation_storage_dir=str(tmp_path),
        training_command=f"printf trained > {marker}",
    )
    store = TrainingStore(settings)

    finished = store.run_job(store.create_job(dataset=_dataset()).id)
    model = store.get_model(finished.model_id)

    assert finished.status == "completed"
    assert marker.read_text() == "trained"
    assert model.metrics["training_mode"] == "external_command"
