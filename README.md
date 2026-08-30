# Invoice OCR API

MVP backend service untuk membaca invoice dari JPG/PNG/WEBP/PDF.

## Pipeline

1. Upload file.
2. PDF digital: ambil text layer langsung.
3. PDF scan/image: OCR dengan PaddleOCR.
4. Jika PaddleOCR gagal / confidence rendah: fallback Tesseract.
5. Normalisasi invoice number, tanggal, currency, subtotal, pajak, diskon, total.
6. Validasi `subtotal + tax - discount == grand_total`.
7. Jika `expected_total` dikirim, lakukan exact/tolerance matching.

## Jalankan dengan Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Swagger:

```text
http://localhost:8000/docs
```

Health:

```bash
curl http://localhost:8000/health
```

> Pada pemanggilan PaddleOCR pertama, model OCR dapat diunduh otomatis.
> Volume `paddle-models` menjaga cache model tetap ada.

## Analyze invoice

```bash
curl -X POST http://localhost:8000/api/v1/invoices/analyze \
  -F "file=@invoice.pdf" \
  -F "expected_total=1387500" \
  -F "engine=auto"
```

Engine tersedia:

- `auto` - PaddleOCR utama, Tesseract fallback
- `paddle`
- `tesseract`

Matching tolerance opsional:

```bash
curl -X POST http://localhost:8000/api/v1/invoices/analyze \
  -F "file=@invoice.jpg" \
  -F "expected_total=10000000" \
  -F "tolerance_amount=100" \
  -F "tolerance_percent=0.01"
```

## Contoh response

```json
{
  "document": {
    "filename": "invoice.pdf",
    "content_type": "application/pdf",
    "size_bytes": 201442,
    "sha256": "..."
  },
  "ocr": {
    "engine": "paddleocr",
    "confidence": 0.9421,
    "page_count": 1,
    "source_type": "pdf_scanned",
    "fallback_used": false
  },
  "invoice": {
    "invoice_number": {
      "value": "INV-2026-00129",
      "confidence": 0.94,
      "source_text": "INVOICE NO: INV-2026-00129"
    },
    "invoice_date": {
      "value": "2026-08-30",
      "confidence": 0.92,
      "source_text": "30 Agustus 2026"
    },
    "vendor_name": {
      "value": "PT CONTOH INDONESIA",
      "confidence": 0.82,
      "source_text": "PT CONTOH INDONESIA"
    },
    "currency": {
      "value": "IDR",
      "confidence": 0.95,
      "source_text": "Rp"
    },
    "subtotal": {
      "value": 1250000,
      "confidence": 0.98,
      "source_text": "Subtotal Rp 1.250.000"
    },
    "tax": {
      "value": 137500,
      "confidence": 0.98,
      "source_text": "PPN Rp 137.500"
    },
    "discount": {
      "value": null,
      "confidence": 0,
      "source_text": null
    },
    "grand_total": {
      "value": 1387500,
      "confidence": 0.98,
      "source_text": "Grand Total Rp 1.387.500"
    }
  },
  "validation": {
    "status": "valid",
    "arithmetic_ok": true,
    "calculated_total": 1387500,
    "difference": 0,
    "warnings": []
  },
  "match": {
    "status": "exact_match",
    "expected_total": 1387500,
    "invoice_total": 1387500,
    "difference": 0,
    "tolerance_used": 0,
    "score": 1
  },
  "raw_text": "...",
  "lines": []
}
```

## Catatan MVP

Parser saat ini rule-based. Ini sengaja agar:

- self-hosted,
- tidak memerlukan LLM,
- hasil dapat diaudit,
- source text setiap field terlihat,
- mudah menambah template/rule vendor tertentu.

Tahap berikutnya yang ideal:

1. persistence PostgreSQL untuk dokumen/extraction/match,
2. API key per aplikasi pemanggil,
3. duplicate detection berdasarkan SHA-256 + invoice number,
4. queue worker untuk batch PDF,
5. callback/webhook,
6. vendor-specific parsing rules,
7. line item/table extraction,
8. review dashboard untuk confidence rendah.
