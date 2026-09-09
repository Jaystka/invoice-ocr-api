# Invoice & Payment Proof Validation API

Self-hosted backend service untuk:

1. membaca invoice dari PDF/gambar,
2. membaca bukti transfer dari PDF/gambar,
3. mencocokkan nominal dengan data aplikasi,
4. memvalidasi rekening/nama/bank tujuan,
5. merekonsiliasi invoice dengan bukti pembayaran.

OCR utama menggunakan **PaddleOCR**, dengan **Tesseract** sebagai fallback.

> Penting: service ini melakukan **validasi konsistensi isi dokumen**. Screenshot/PDF yang terlihat valid melalui OCR belum membuktikan bahwa transaksi benar-benar terjadi. Untuk validasi finansial final, integrasikan hasil dengan mutasi bank, payment gateway, virtual account callback, atau bank API.

## Architecture

```text
Invoice / Payment Proof
        |
        v
File + PDF Inspector
        |
   +----+------+
   |           |
PDF text     Image/scan
   |           |
   |       PaddleOCR
   |           |
   |     low confidence/error
   |           |
   |       Tesseract
   +-----+-----+
         |
         v
Normalization / Parsing
   |                |
 Invoice Parser   Transfer Parser
   |                |
   v                v
Invoice Validation Payment Validation
         \          /
          \        /
           v      v
          Reconciliation
```

## Extracted payment fields

- transfer status
- amount
- currency
- transaction date
- transaction time
- source bank
- source account
- source name
- destination bank
- destination account
- destination name
- reference number
- transfer channel (BI-FAST / RTGS / SKN / QRIS / etc.)

## Payment validation checks

The API can validate:

- transfer status is successful,
- amount matches expected bill,
- recipient name similarity,
- destination account exact or masked-suffix match,
- destination bank,
- optional transaction reference,
- presence of date/reference evidence.

The response includes:

```text
valid
review
invalid
```

plus per-check explanations and risk flags.

## Run

```bash
cp .env.example .env
docker compose up -d --build
```

Swagger:

```text
http://localhost:8000/docs
```

Bounding box training interface untuk invoice dan payment proof:

```text
http://localhost:8000/training
```

## 1. Analyze invoice

```bash
curl -X POST http://localhost:8000/api/v1/invoices/analyze \
  -F "file=@invoice.pdf" \
  -F "expected_total=1387500" \
  -F "engine=auto"
```

## 2. Analyze payment proof

```bash
curl -X POST http://localhost:8000/api/v1/payments/analyze \
  -F "file=@bukti-transfer.jpg" \
  -F "expected_amount=1387500" \
  -F "expected_recipient_name=PT Contoh Indonesia" \
  -F "expected_recipient_account=12345678901234" \
  -F "expected_recipient_bank=BCA" \
  -F "engine=auto"
```

Example result:

```json
{
  "payment": {
    "transfer_status": {
      "value": "success",
      "confidence": 0.96
    },
    "amount": {
      "value": 1387500,
      "confidence": 0.98
    },
    "destination_bank": {
      "value": "BCA",
      "confidence": 0.95
    },
    "destination_account": {
      "value": "****1234",
      "confidence": 0.95
    },
    "destination_name": {
      "value": "PT CONTOH INDONESIA",
      "confidence": 0.95
    },
    "reference_number": {
      "value": "TRX20260830123456",
      "confidence": 0.95
    }
  },
  "validation": {
    "status": "valid",
    "score": 0.98,
    "risk_flags": [],
    "verification_scope": "content_consistency_only"
  }
}
```

## 3. Invoice + payment reconciliation

```bash
curl -X POST http://localhost:8000/api/v1/reconciliation/validate \
  -F "invoice_file=@invoice.pdf" \
  -F "payment_file=@bukti-transfer.jpg" \
  -F "expected_recipient_name=PT Contoh Indonesia" \
  -F "expected_recipient_account=12345678901234" \
  -F "expected_recipient_bank=BCA"
```

Possible status:

```text
matched
review
mismatch
insufficient_data
```

## 4. Bounding box training workflow

Interface `/training` menyediakan MVP human-in-the-loop untuk membuat dataset
training dari invoice dan bukti transfer:

1. upload invoice PDF/gambar,
2. gambar bounding box pada field yang penting,
3. pilih label field,
4. jalankan OCR khusus area crop,
5. koreksi teks jika perlu,
6. simpan annotation,
7. approve annotation/document,
8. export dataset approved.

Tombol `Auto Suggest` menjalankan OCR awal pada halaman aktif, lalu membuat
bounding box draft untuk field yang terdeteksi. Hasil auto-suggest tetap harus
direview karena ia berbasis heuristic dari teks OCR awal.

Label invoice default yang tersedia di UI:

- `invoice_number`
- `invoice_date`
- `vendor_name`
- `tax_number`
- `subtotal`
- `tax_amount`
- `discount`
- `total_amount`
- `due_date`
- `currency`
- `line_items`

Label payment proof default yang tersedia di UI:

- `transfer_status`
- `amount`
- `currency`
- `transaction_date`
- `transaction_time`
- `source_bank`
- `source_account`
- `source_name`
- `destination_bank`
- `destination_account`
- `destination_name`
- `reference_number`
- `channel`

Storage default menggunakan file lokal di `data/annotations`. Pada Docker
Compose, folder ini dipersist ke host melalui volume `./data:/app/data`.

Upload invoice training:

```bash
curl -X POST http://localhost:8000/api/v1/training/invoices \
  -F "file=@invoice.pdf"
```

Upload payment proof training:

```bash
curl -X POST http://localhost:8000/api/v1/training/payments \
  -F "file=@bukti-transfer.jpg"
```

Upload training document dengan route generik:

```bash
curl -X POST http://localhost:8000/api/v1/training/documents/payment_proof \
  -F "file=@bukti-transfer.jpg"
```

Simpan annotation:

```bash
curl -X POST http://localhost:8000/api/v1/training/documents/{document_id}/annotations \
  -H "Content-Type: application/json" \
  -d '{
    "page": 1,
    "label": "invoice_number",
    "bbox": {"x": 812, "y": 143, "width": 210, "height": 42},
    "text": "INV-2026-0091",
    "status": "approved"
  }'
```

OCR area crop:

```bash
curl -X POST http://localhost:8000/api/v1/training/documents/{document_id}/ocr-crop \
  -H "Content-Type: application/json" \
  -d '{
    "page": 1,
    "bbox": {"x": 812, "y": 143, "width": 210, "height": 42},
    "engine": "auto"
  }'
```

Auto-suggest bounding box dari OCR awal:

```bash
curl -X POST http://localhost:8000/api/v1/training/documents/{document_id}/auto-suggest \
  -H "Content-Type: application/json" \
  -d '{
    "page": 1,
    "replace_existing_auto": true
  }'
```

Export dataset:

```bash
curl "http://localhost:8000/api/v1/training/dataset/export?approved_only=true"
```

Train model dari annotation approved:

```bash
curl -X POST http://localhost:8000/api/v1/training/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "approved_only": true,
    "notes": "manual training run"
  }'
```

Cek status training job:

```bash
curl http://localhost:8000/api/v1/training/jobs/{job_id}
```

List model version:

```bash
curl http://localhost:8000/api/v1/training/models
```

Promote model ke production:

```bash
curl -X POST http://localhost:8000/api/v1/training/models/{model_id}/promote
```

MVP training saat ini membuat dataset snapshot dan model artifact metadata di
`data/annotations/training`. Ini menyiapkan job lifecycle, dataset versioning,
dan model registry.

Untuk menjalankan training model sebenarnya, isi `INVOICE_TRAINING_COMMAND`.
Command ini bisa memakai placeholder:

- `{dataset_path}`
- `{artifact_path}`
- `{model_id}`

Contoh:

```env
INVOICE_TRAINING_COMMAND=python scripts/train_detector.py --dataset {dataset_path} --out {artifact_path} --model-id {model_id}
```

Format dataset berisi dokumen, halaman render, dan annotation:

```json
{
  "dataset_version": "dataset_20260909030000",
  "document_count": 2,
  "invoice_count": 1,
  "payment_proof_count": 1,
  "annotation_count": 2,
  "documents": [
    {
      "id": "invoice_id",
      "document_type": "invoice",
      "pages": [
        {
          "page": 1,
          "width": 1654,
          "height": 2339,
          "image_url": "/api/v1/training/invoices/invoice_id/pages/1.png"
        }
      ],
      "annotations": [
        {
          "label": "invoice_number",
          "bbox": {"x": 812, "y": 143, "width": 210, "height": 42},
          "text": "INV-2026-0091",
          "status": "approved"
        }
      ]
    },
    {
      "id": "payment_id",
      "document_type": "payment_proof",
      "annotations": [
        {
          "label": "reference_number",
          "bbox": {"x": 120, "y": 420, "width": 260, "height": 38},
          "text": "TRX20260830123456",
          "status": "approved"
        }
      ]
    }
  ]
}
```

## Recommended integration flow

```text
Aplikasi utama
    |
    | transaction_id + expected amount/account
    v
Invoice & Payment Validation API
    |
    +--> parse invoice
    +--> parse proof
    +--> validate payment content
    +--> reconcile amount
    |
    v
matched / review / mismatch
```

Do **not** auto-settle a high-value transaction solely from OCR. Recommended production states:

```text
OCR_VALID
BANK_PENDING
BANK_CONFIRMED
REVIEW_REQUIRED
REJECTED
```

For stronger production validation, the next layer should add:

- PostgreSQL audit history,
- duplicate proof detection using SHA-256 + reference number,
- API key per caller app,
- bank/account master data,
- webhook callback,
- asynchronous worker for batch processing,
- mutation/payment-gateway verification,
- manual review dashboard,
- cropped evidence regions for each extracted field.
