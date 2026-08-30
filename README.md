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
