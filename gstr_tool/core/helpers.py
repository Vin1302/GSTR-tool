from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


MONTHS = [calendar.month_name[i] for i in range(4, 13)] + [calendar.month_name[i] for i in range(1, 4)]


def decimal(value: object) -> Decimal:
    if value in (None, "", "-"):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


def month_name(value: object, fallback_period: str = "") -> str:
    if isinstance(value, (date, datetime)):
        return calendar.month_name[value.month]
    text = str(value or fallback_period).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%b-%y", "%B-%y", "%m%Y", "%Y%m"):
        try:
            return calendar.month_name[datetime.strptime(text, fmt).month]
        except ValueError:
            pass
    month_tokens = list(calendar.month_abbr[1:]) + list(calendar.month_name[1:])
    match = re.search(r"(?i)\b(" + "|".join(month_tokens) + r")\b", text)
    if match:
        return calendar.month_name[datetime.strptime(match.group(1)[:3], "%b").month]
    return ""


def yes(value: object) -> bool:
    return str(value or "").strip().lower() in {"yes", "y", "true", "1", "available", "eligible"}


def normalized_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()
