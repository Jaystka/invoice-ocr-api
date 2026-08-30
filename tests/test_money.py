from app.utils.money import normalize_amount


def test_indonesian_amount():
    assert normalize_amount("Rp 1.250.000") == 1_250_000


def test_indonesian_decimal_amount():
    assert normalize_amount("1.250.000,00") == 1_250_000


def test_international_amount():
    assert normalize_amount("1,250,000.00") == 1_250_000
