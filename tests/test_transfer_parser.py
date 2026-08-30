from app.services.transfer_parser import parse_transfer_proof


def test_parse_transfer_proof():
    lines = [
        "TRANSFER BERHASIL",
        "Tanggal Transaksi: 30 Agustus 2026 14:32 WIB",
        "Bank Tujuan: BCA",
        "Nama Penerima: PT CONTOH INDONESIA",
        "Rekening Tujuan: ****1234",
        "Nominal Transfer",
        "Rp 1.387.500",
        "No. Referensi: TRX20260830123456",
        "BI-FAST",
    ]

    payment = parse_transfer_proof(lines)

    assert payment.transfer_status.value == "success"
    assert payment.transaction_date.value == "2026-08-30"
    assert payment.destination_bank.value == "BCA"
    assert payment.destination_account.value == "****1234"
    assert payment.amount.value == 1_387_500
    assert payment.reference_number.value == "TRX20260830123456"
    assert payment.channel.value == "BI-FAST"
