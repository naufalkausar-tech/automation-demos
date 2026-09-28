"""Deterministic SAMPLE rows for the demo workbook. Every client is named 'عميل تجريبي' (sample client) and every
plate starts with DEMO: none of it describes a real client, vehicle or organisation."""
from __future__ import annotations

import datetime as dt

from .core import ACTIVE, DUE, PAID, PENDING

# (kind of client, vehicle type, count, monthly rent, maintenance deduction, contract status)
CONTRACTS = [
    ("شركة مقاولات", "دفع رباعي", 2, 9500, 400, ACTIVE),
    ("مستشفى", "حافلة صغيرة", 1, 7200, 0, ACTIVE),
    ("جهة حكومية", "سيدان", 3, 11400, 650, ACTIVE),
    ("مصنع", "بيك أب", 2, 6800, 300, "منتهي"),
]
SCALE = (1.0, 0.8, 1.3)  # so the organisations differ in size
FEES = [("تجديد السجل التجاري", 2000, 1.0), ("رخصة البلدية", 1500, 0.0),
        ("تجديد استمارات المركبات", 3600, 0.5), ("تأمين المركبات السنوي", 24000, 0.5)]


def _entry(date, org, bank, category, amount, status, notes=None):
    return {"date": date, "org": org, "bank": bank, "category": category, "amount": amount, "status": status,
            "notes": notes}


def sample_data(orgs, banks, year):
    """Eight months of rent receipts and running costs per organisation (Jan-Aug of `year`): months 1-6 settled,
    month 7 has one cheque pending clearance, month 8 is still due; plus an opening balance and one rent receipt
    dated the previous December, which the dashboard counts in the balance brought forward, not in the period."""
    fleet, income, expenses, fees = {}, [], [], []
    for i, org in enumerate(orgs):
        scale, bank = SCALE[i % len(SCALE)], banks[i % len(banks)]
        contracts = [{"client": f"عميل تجريبي {i + 1}-{k + 1} ({kind})", "vehicle_type": vtype, "count": count,
                      "vehicle_no": f"V-{i + 1}{k + 1:02d}", "plate": f"DEMO {i + 1}{k + 1:03d}",
                      "handover": dt.date(year - 1, 3 + 2 * k, 1), "rent": round(rent * scale),
                      "deduction": round(deduction * scale), "status": status}
                     for k, (kind, vtype, count, rent, deduction, status) in enumerate(CONTRACTS)]
        fleet[org] = contracts
        income.append(_entry(dt.date(year - 1, 12, 1), org, bank, "رصيد افتتاحي", round(40000 * scale), PAID,
                             "رصيد افتتاحي تجريبي"))
        income.append(_entry(dt.date(year - 1, 12, 5), org, bank, "إيجار شهري", round(9000 * scale), PAID,
                             "قبل الفترة: يدخل في رصيد أول المدة لا في إيرادات الفترة"))
        for month in range(1, 9):
            for k, c in enumerate(contracts):
                if c["status"] != ACTIVE:
                    continue
                status, note = PAID, None
                if month == 7 and k == 0:
                    status, note = PENDING, "شيك قيد التحصيل"
                elif month == 8:
                    status, note = DUE, "لم يُستلم بعد"
                income.append(_entry(dt.date(year, month, 5), org, bank, "إيجار شهري",
                                     c["rent"] - c["deduction"], status, note))
            expenses.append(_entry(dt.date(year, month, 10), org, bank, "صيانة", round(2100 * scale),
                                   DUE if month == 8 else PAID, "فاتورة الورشة لم تُسدَّد" if month == 8 else None))
            expenses.append(_entry(dt.date(year, month, 25), org, bank, "رواتب سائقين", round(6000 * scale), PAID))
        expenses.append(_entry(dt.date(year, 1, 15), org, bank, "تأمين", round(12000 * scale), PAID,
                               "القسط الأول من التأمين"))
        expenses.append(_entry(dt.date(year, 3, 12), org, bank, "رسوم حكومية",
                               round(2000 * scale) + round(1800 * scale), PAID, "السجل التجاري + نصف رسوم الاستمارات"))
        expenses.append(_entry(dt.date(year, 8, 20), org, bank, "رسوم حكومية", round(1500 * scale), DUE,
                               "رخصة البلدية"))
        expenses.append(_entry(dt.date(year, 8, 28), org, bank, "وقود", round(3200 * scale), PENDING,
                               "فاتورة وقود قيد المراجعة"))
        fees += [{"fee": fee, "org": org, "total": round(total * scale), "paid": round(total * scale * share)}
                 for fee, total, share in FEES]
    income.sort(key=lambda r: r["date"])
    expenses.sort(key=lambda r: r["date"])
    return {"fleet": fleet, "income": income, "expenses": expenses, "fees": fees}
