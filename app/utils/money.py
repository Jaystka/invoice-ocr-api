import re
from decimal import Decimal, InvalidOperation


CURRENCY_PATTERNS = (
    ("IDR", re.compile(r"\b(?:IDR|Rp\.?)\b", re.IGNORECASE)),
    ("USD", re.compile(r"\b(?:USD|US\$|\$)\b", re.IGNORECASE)),
    ("EUR", re.compile(r"\b(?:EUR|€)\b", re.IGNORECASE)),
)


def detect_currency(text: str) -> tuple[str | None, float, str | None]:
    for code, pattern in CURRENCY_PATTERNS:
        match = pattern.search(text)
        if match:
            return code, 0.95, match.group(0)
    return None, 0.0, None


def normalize_amount(value: str) -> int | None:
    """
    Normalize common Indonesian/international money formats to integer minor-less units.

    Examples:
      Rp 1.250.000      -> 1250000
      1,250,000         -> 1250000
      1.250.000,00      -> 1250000
      1,250,000.00      -> 1250000
      1250000           -> 1250000
    """
    if not value:
        return None

    s = value.strip()
    s = re.sub(r"(?i)\b(IDR|USD|EUR|RP)\.?\b", "", s)
    s = s.replace(" ", "").replace(",-", "").replace("–", "-")
    s = re.sub(r"[^\d,.\-]", "", s)

    if not re.search(r"\d", s):
        return None

    # Keep only leading minus if any.
    negative = s.startswith("-")
    s = s.replace("-", "")

    # Decide decimal separator when both separators are present.
    if "." in s and "," in s:
        last_dot = s.rfind(".")
        last_comma = s.rfind(",")
        decimal_sep = "." if last_dot > last_comma else ","
        thousands_sep = "," if decimal_sep == "." else "."
        decimals = s.split(decimal_sep)[-1]

        s = s.replace(thousands_sep, "")
        # Treat last separator as decimals only for 1-2 decimal digits.
        if len(decimals) in (1, 2):
            s = s.rsplit(decimal_sep, 1)[0]
        else:
            s = s.replace(decimal_sep, "")
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            s = "".join(parts)
        elif len(parts) == 2 and len(parts[-1]) in (1, 2):
            s = parts[0]
        else:
            s = "".join(parts)
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            s = "".join(parts)
        elif len(parts) == 2 and len(parts[-1]) in (1, 2):
            s = parts[0]
        else:
            s = "".join(parts)

    if not s:
        return None

    try:
        amount = int(Decimal(s))
    except (InvalidOperation, ValueError):
        return None

    return -amount if negative else amount


AMOUNT_RE = re.compile(
    r"(?:(?:IDR|Rp\.?|USD|EUR|US\$|\$)\s*)?"
    r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d{4,})(?:\s*,-)?",
    re.IGNORECASE,
)


def amounts_in_text(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for match in AMOUNT_RE.finditer(text):
        raw = match.group(0)
        amount = normalize_amount(raw)
        if amount is not None:
            out.append((amount, raw.strip()))
    return out
