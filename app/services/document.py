from dataclasses import dataclass
from io import BytesIO

import fitz
from PIL import Image

from app.config import Settings


@dataclass
class PageInput:
    page_number: int
    digital_text: str | None = None
    image: Image.Image | None = None


@dataclass
class DocumentPages:
    source_type: str
    pages: list[PageInput]


def _render_page(page: fitz.Page, dpi: int) -> Image.Image:
    scale = dpi / 72
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")


def load_document(
    content: bytes,
    filename: str,
    content_type: str | None,
    settings: Settings,
) -> DocumentPages:
    lower_name = filename.lower()
    is_pdf = lower_name.endswith(".pdf") or content_type == "application/pdf"

    if not is_pdf:
        image = Image.open(BytesIO(content)).convert("RGB")
        return DocumentPages(
            source_type="image",
            pages=[PageInput(page_number=1, image=image)],
        )

    doc = fitz.open(stream=content, filetype="pdf")
    pages: list[PageInput] = []
    digital_count = 0
    scanned_count = 0

    for i, page in enumerate(doc):
        text = page.get_text("text").strip()
        if len(text) >= settings.digital_pdf_min_chars:
            digital_count += 1
            pages.append(PageInput(page_number=i + 1, digital_text=text))
        else:
            scanned_count += 1
            pages.append(
                PageInput(
                    page_number=i + 1,
                    image=_render_page(page, settings.pdf_dpi),
                )
            )

    if digital_count and scanned_count:
        source_type = "pdf_mixed"
    elif digital_count:
        source_type = "pdf_digital"
    else:
        source_type = "pdf_scanned"

    return DocumentPages(source_type=source_type, pages=pages)
