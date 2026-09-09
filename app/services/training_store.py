from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.schemas import DatasetExport, ModelVersion, TrainingJob
from app.services.annotation_store import now_iso


class TrainingStore:
    def __init__(self, settings: Settings):
        self.root = Path(settings.annotation_storage_dir) / "training"
        self.training_command = settings.training_command
        self.datasets_dir = self.root / "datasets"
        self.jobs_dir = self.root / "jobs"
        self.models_dir = self.root / "models"
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        *,
        dataset: DatasetExport,
        notes: str | None = None,
    ) -> TrainingJob:
        if dataset.annotation_count <= 0:
            raise ValueError("No approved annotations available for training")

        job_id = uuid4().hex
        dataset_path = self.datasets_dir / f"{dataset.dataset_version}_{job_id}.json"
        dataset_path.write_text(
            json.dumps(dataset.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

        timestamp = now_iso()
        job = TrainingJob(
            id=job_id,
            status="queued",
            dataset_version=dataset.dataset_version,
            dataset_path=str(dataset_path),
            notes=notes,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._save_job(job)
        return job

    def start_background_job(self, job_id: str) -> None:
        thread = threading.Thread(target=self.run_job, args=(job_id,), daemon=True)
        thread.start()

    def run_job(self, job_id: str) -> TrainingJob:
        job = self.get_job(job_id)
        try:
            timestamp = now_iso()
            job.status = "running"
            job.started_at = timestamp
            job.updated_at = timestamp
            self._save_job(job)

            dataset = DatasetExport.model_validate_json(Path(job.dataset_path).read_text())
            model = self._build_model_artifact(job=job, dataset=dataset)

            completed_at = now_iso()
            job.status = "completed"
            job.model_id = model.id
            job.completed_at = completed_at
            job.updated_at = completed_at
            self._save_job(job)
            return job
        except Exception as exc:
            failed_at = now_iso()
            job.status = "failed"
            job.error = str(exc)
            job.completed_at = failed_at
            job.updated_at = failed_at
            self._save_job(job)
            return job

    def list_jobs(self) -> list[TrainingJob]:
        return [
            self._read_job(path)
            for path in sorted(self.jobs_dir.glob("*.json"), reverse=True)
        ]

    def get_job(self, job_id: str) -> TrainingJob:
        path = self._job_path(job_id)
        if not path.exists():
            raise FileNotFoundError(job_id)
        return self._read_job(path)

    def list_models(self) -> list[ModelVersion]:
        return [
            self._read_model(path)
            for path in sorted(self.models_dir.glob("*.json"), reverse=True)
            if ".artifact." not in path.name
        ]

    def get_model(self, model_id: str) -> ModelVersion:
        path = self._model_path(model_id)
        if not path.exists():
            raise FileNotFoundError(model_id)
        return self._read_model(path)

    def promote_model(self, model_id: str) -> ModelVersion:
        model = self.get_model(model_id)
        timestamp = now_iso()
        for current in self.list_models():
            if current.status == "production" and current.id != model_id:
                current.status = "archived"
                current.updated_at = timestamp
                self._save_model(current)

        model.status = "production"
        model.updated_at = timestamp
        self._save_model(model)
        return model

    def _build_model_artifact(
        self,
        *,
        job: TrainingJob,
        dataset: DatasetExport,
    ) -> ModelVersion:
        model_id = f"model_{job.id}"
        label_counts: dict[str, int] = {}
        for document in dataset.documents:
            for annotation in document.annotations:
                label_counts[annotation.label] = label_counts.get(annotation.label, 0) + 1

        metrics: dict[str, float | int | str] = {
            "document_count": dataset.document_count,
            "invoice_count": dataset.invoice_count,
            "payment_proof_count": dataset.payment_proof_count,
            "annotation_count": dataset.annotation_count,
            "label_count": len(label_counts),
            "training_mode": "dataset_snapshot",
        }

        artifact = {
            "model_id": model_id,
            "training_job_id": job.id,
            "dataset_version": dataset.dataset_version,
            "labels": label_counts,
            "metrics": metrics,
            "note": (
                "MVP training artifact. Set INVOICE_TRAINING_COMMAND to run "
                "a real detector/fine-tuning command."
            ),
        }
        artifact_path = self.models_dir / f"{model_id}.artifact.json"
        if self.training_command:
            command = self.training_command.format(
                dataset_path=job.dataset_path,
                artifact_path=str(artifact_path),
                model_id=model_id,
            )
            completed = subprocess.run(
                command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
            )
            artifact["training_command"] = command
            artifact["stdout"] = completed.stdout[-4000:]
            artifact["stderr"] = completed.stderr[-4000:]
            artifact["metrics"]["training_mode"] = "external_command"

        artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

        timestamp = now_iso()
        model = ModelVersion(
            id=model_id,
            status="staging",
            training_job_id=job.id,
            dataset_version=dataset.dataset_version,
            artifact_path=str(artifact_path),
            metrics=metrics,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._save_model(model)
        return model

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _model_path(self, model_id: str) -> Path:
        return self.models_dir / f"{model_id}.json"

    def _save_job(self, job: TrainingJob) -> None:
        self._job_path(job.id).write_text(
            json.dumps(job.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _read_job(path: Path) -> TrainingJob:
        return TrainingJob.model_validate_json(path.read_text())

    def _save_model(self, model: ModelVersion) -> None:
        self._model_path(model.id).write_text(
            json.dumps(model.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _read_model(path: Path) -> ModelVersion:
        return ModelVersion.model_validate_json(path.read_text())
