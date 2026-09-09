from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Invoice & Payment Proof Validation API"
    app_version: str = "0.2.0"
    api_prefix: str = "/api/v1"

    max_file_mb: int = 15
    pdf_dpi: int = 200
    digital_pdf_min_chars: int = 40
    annotation_storage_dir: str = "data/annotations"

    default_engine: str = "auto"
    paddle_lang: str = "id"
    tesseract_lang: str = "ind+eng"
    paddle_min_confidence: float = 0.60

    default_tolerance_amount: int = 100
    default_tolerance_percent: float = 0.01

    # Minimum scores for content-level payment validation.
    payment_valid_score: float = 0.85
    payment_review_score: float = 0.65
    minimum_name_similarity: float = 0.72

    model_config = SettingsConfigDict(
        env_file=".env",
        # Kept for backwards compatibility with the v0.1 project.
        env_prefix="INVOICE_",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
