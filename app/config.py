from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Invoice OCR API"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"

    max_file_mb: int = 15
    pdf_dpi: int = 200
    digital_pdf_min_chars: int = 40

    default_engine: str = "auto"
    paddle_lang: str = "id"
    tesseract_lang: str = "ind+eng"
    paddle_min_confidence: float = 0.60

    default_tolerance_amount: int = 100
    default_tolerance_percent: float = 0.01

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="INVOICE_",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
