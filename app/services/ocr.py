from dataclasses import dataclass
from functools import cached_property
from typing import Literal

import cv2
import numpy as np
import pytesseract
from PIL import Image
from paddleocr import PaddleOCR

from app.config import Settings


@dataclass
class OCRText:
    text: str
    lines: list[str]
    confidence: float
    engine: str
    fallback_used: bool = False


def preprocess_image(image: Image.Image) -> Image.Image:
    rgb = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # Mild denoise + adaptive threshold helps phone photos and scans while
    # preserving text edges better than aggressive morphology.
    gray = cv2.bilateralFilter(gray, 5, 35, 35)
    thresh = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    return Image.fromarray(thresh)


class OCRService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @cached_property
    def paddle(self) -> PaddleOCR:
        return PaddleOCR(
            lang=self.settings.paddle_lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            engine="paddle",
        )

    def _run_paddle(self, image: Image.Image) -> OCRText:
        prepared = preprocess_image(image)
        arr = np.array(prepared.convert("RGB"))

        result = self.paddle.predict(arr)
        texts: list[str] = []
        scores: list[float] = []

        for item in result:
            payload = item.json
            res = payload.get("res", payload)
            rec_texts = res.get("rec_texts") or []
            rec_scores = res.get("rec_scores") or []
            texts.extend(str(t).strip() for t in rec_texts if str(t).strip())
            scores.extend(float(s) for s in rec_scores)

        confidence = sum(scores) / len(scores) if scores else 0.0
        return OCRText(
            text="\n".join(texts),
            lines=texts,
            confidence=max(0.0, min(1.0, confidence)),
            engine="paddleocr",
        )

    def _run_tesseract(self, image: Image.Image) -> OCRText:
        prepared = preprocess_image(image)

        data = pytesseract.image_to_data(
            prepared,
            lang=self.settings.tesseract_lang,
            config="--oem 1 --psm 6",
            output_type=pytesseract.Output.DICT,
        )

        words: list[str] = []
        confidences: list[float] = []
        line_map: dict[tuple[int, int, int], list[str]] = {}

        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            try:
                conf = float(data["conf"][i])
            except (ValueError, TypeError):
                conf = -1

            if not text:
                continue

            words.append(text)
            if conf >= 0:
                confidences.append(conf / 100.0)

            key = (
                int(data["block_num"][i]),
                int(data["par_num"][i]),
                int(data["line_num"][i]),
            )
            line_map.setdefault(key, []).append(text)

        lines = [" ".join(parts) for parts in line_map.values() if parts]
        confidence = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )

        return OCRText(
            text="\n".join(lines) if lines else " ".join(words),
            lines=lines if lines else [" ".join(words)],
            confidence=max(0.0, min(1.0, confidence)),
            engine="tesseract",
        )

    def read_image(
        self,
        image: Image.Image,
        engine: Literal["auto", "paddle", "tesseract"] = "auto",
    ) -> OCRText:
        if engine == "paddle":
            return self._run_paddle(image)

        if engine == "tesseract":
            return self._run_tesseract(image)

        paddle_result: OCRText | None = None
        try:
            paddle_result = self._run_paddle(image)
            if (
                paddle_result.lines
                and paddle_result.confidence >= self.settings.paddle_min_confidence
            ):
                return paddle_result
        except Exception:
            paddle_result = None

        tesseract_result = self._run_tesseract(image)
        tesseract_result.fallback_used = True

        if paddle_result and paddle_result.confidence > tesseract_result.confidence:
            paddle_result.fallback_used = True
            return paddle_result

        return tesseract_result
