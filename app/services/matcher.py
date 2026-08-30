from app.schemas import MatchResult


def match_total(
    invoice_total: int | None,
    expected_total: int | None,
    tolerance_amount: int,
    tolerance_percent: float,
) -> MatchResult:
    if expected_total is None:
        return MatchResult(status="not_requested")

    if invoice_total is None:
        return MatchResult(
            status="insufficient_data",
            expected_total=expected_total,
            invoice_total=None,
        )

    difference = invoice_total - expected_total
    abs_difference = abs(difference)

    if difference == 0:
        return MatchResult(
            status="exact_match",
            expected_total=expected_total,
            invoice_total=invoice_total,
            difference=0,
            tolerance_used=0,
            score=1.0,
        )

    percent_tolerance_value = int(
        round(abs(expected_total) * (tolerance_percent / 100.0))
    )
    tolerance = max(tolerance_amount, percent_tolerance_value)

    if abs_difference <= tolerance:
        score = 1.0
        if tolerance > 0:
            score = max(0.90, 1 - (abs_difference / tolerance) * 0.10)

        return MatchResult(
            status="tolerance_match",
            expected_total=expected_total,
            invoice_total=invoice_total,
            difference=difference,
            tolerance_used=tolerance,
            score=round(score, 4),
        )

    # Simple interpretable score for MVP.
    denominator = max(abs(expected_total), 1)
    score = max(0.0, 1.0 - (abs_difference / denominator))

    return MatchResult(
        status="mismatch",
        expected_total=expected_total,
        invoice_total=invoice_total,
        difference=difference,
        tolerance_used=tolerance,
        score=round(score, 4),
    )
