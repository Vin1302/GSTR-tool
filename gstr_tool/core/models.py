from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


ZERO = Decimal("0")


@dataclass(slots=True)
class GstCredential:
    label: str
    username: str
    password: str
    gstin: str = ""


@dataclass(slots=True)
class TaxAmounts:
    taxable: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO

    def add(self, other: "TaxAmounts", sign: int = 1) -> None:
        multiplier = Decimal(sign)
        self.taxable += other.taxable * multiplier
        self.igst += other.igst * multiplier
        self.cgst += other.cgst * multiplier
        self.sgst += other.sgst * multiplier
        self.cess += other.cess * multiplier


@dataclass(slots=True)
class MonthData:
    gstr3b: dict[str, TaxAmounts] = field(default_factory=dict)
    gstr1: dict[str, TaxAmounts] = field(default_factory=dict)
    einvoice: dict[str, TaxAmounts] = field(default_factory=dict)
    gstr2b: dict[str, TaxAmounts] = field(default_factory=dict)


@dataclass(slots=True)
class Invoice2B:
    party: str = ""
    gstin: str = ""
    document_type: str = "INVOICE"
    document_number: str = ""
    document_date: str = ""
    taxable: Decimal = ZERO
    igst: Decimal = ZERO
    cgst: Decimal = ZERO
    sgst: Decimal = ZERO
    cess: Decimal = ZERO
    document_value: Decimal = ZERO
    reverse_charge: str = "No"
    itc_availability: str = "Yes"
    period_2a: str = ""
    period_2b: str = ""
    remarks: str = ""
