from __future__ import annotations

import re


DSK_DIGITS = 40


def extract_dsk(raw_value: str) -> str:
    """Extract the first 40 digits from the QR payload as DSK candidate.

    This intentionally supports noisy payloads where delimiters/non-digit
    characters are present.
    """

    digits = "".join(re.findall(r"\d", raw_value.strip()))
    if len(digits) < DSK_DIGITS:
        raise ValueError("raw_value does not contain enough digits to derive a DSK")
    return digits[:DSK_DIGITS]
