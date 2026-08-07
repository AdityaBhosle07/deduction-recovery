#!/usr/bin/env python3
"""
Clean the messy retailer deduction export into the app's normalized schema.

Fixes included:
- Complete → resolved/won with recovered_amount = amount (full recovery assumed)
- Empty/unmapped reasons → OTHER (not SHORT)
- Description prefers reason_raw; "No reason provided" only when empty
- Dates after 2026-12-31 flagged future_date
- company_id 3 ensured as Pacific Pantry Co.
- Null-safe retailer mapping with unknown_retailer flag
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = Path("/home/workdir/attachments/pasted-text.txt")
# Fallback: same folder as script's data, or local paste path
if not RAW.exists():
    candidates = [
        ROOT / "data" / "pasted-text.txt",
        ROOT.parent / "attachments" / "pasted-text.txt",
        Path.home() / "Downloads" / "pasted-text.txt",
    ]
    for c in candidates:
        if c.exists():
            RAW = c
            break

OUT = ROOT / "data" / "deductions.json"
COMPANIES_PATH = ROOT / "data" / "companies.json"
REASONS_PATH = ROOT / "data" / "dispute_reasons.json"

RETAILER_ALIASES = {
    "kehe": 10, "kehe\t": 10, "kehe ": 10, "  kehe": 10, "ke he": 10,
    "k e h e": 10, "kehe distributors": 10, "kehe distributors llc": 10,
    "kehe food distributors": 10, "kehe food distributors llc": 10,
    "unfi": 11, "un fi": 11, "u.n.f.i.": 11, "united natural foods": 11,
    "united natural foods inc": 11,
    "gfc": 12, "gfs": 12, "gordon food service": 12, "gordon food svc": 12,
    "gordon food service (gfc)": 12,
    "dot foods": 13, "dot foods inc": 13, "dotfoods": 13,
    "kroger": 14, "kroger co": 14, "kroger co.": 14, "the kroger co.": 14,
    "kroger  ": 14,
    "target": 15, "target ": 15, "tgt": 15, "target corp": 15,
    "target corporation": 15,
    "walmart": 16, "wal mart": 16, "wal-mart": 16, "walmart inc.": 16,
    "amazon": 17, "amazon.com": 17, "amzn": 17, "amazon vendor": 17,
    "amazon vendor central": 17,
    "winco": 18, "winco foods": 18, "winco ": 18,
    "loblaw": 19, "loblaws": 19, "loblaw companies": 19,
    "ahold": 20, "ahold delhaize": 20,
    "bjs": 21, "bj's": 21, "bj's wholesale club": 21, "bjs wholesale": 21,
    "h-e-b": 22, "h e b": 22, "heb": 22, "h-e-b ": 22,
    "cvs": 23, "cvs pharmacy": 23,
}

REASON_MAP = {
    "shortage": "SHORT", "short": "SHORT", "shortage in transit": "SHORT",
    "shortage - product": "SHORT", "shortage-product": "SHORT",
    "price": "PRICE", "pricing": "PRICE", "price discrepancy": "PRICE",
    "freight": "FREIGHT", "frieght": "FREIGHT", "freight allowance": "FREIGHT",
    "os&d": "OSD", "osd": "OSD",
    "compliance": "COMP", "compliance fine": "COMP", "fine": "COMP",
    "spoilage": "SPOIL", "spoiled": "SPOIL",
    "unsaleables": "UNSAL", "unsaleable": "UNSAL", "unsalables": "UNSAL",
    "mcb": "MCB", "bill back": "MCB", "billback": "MCB", "bill back / mcb": "MCB",
    "promo": "PROMO", "promotional allowance": "PROMO",
    "other": None, "misc": None, "n/a": None, "???": None, "": None,
}

REASON_LABELS = {
    "SHORT": "Shortage",
    "PRICE": "Pricing",
    "FREIGHT": "Freight",
    "OSD": "OS&D",
    "COMP": "Compliance Fine",
    "SPOIL": "Spoilage",
    "UNSAL": "Unsaleables",
    "MCB": "Bill Back / MCB",
    "PROMO": "Promotional Allowance",
    "OTHER": "Other / Unknown",
}


def ensure_reference_data() -> None:
    """Ensure OTHER reason and company_id 3 exist."""
    reasons = json.loads(REASONS_PATH.read_text())
    if not any(r.get("code") == "OTHER" for r in reasons):
        reasons.append({
            "code": "OTHER",
            "label": "Other / Unknown",
            "category": "Other",
            "typically_disputable": False,
        })
        REASONS_PATH.write_text(json.dumps(reasons, indent=2))
        print("Added OTHER to dispute_reasons.json")

    comps = json.loads(COMPANIES_PATH.read_text())
    if 3 not in {c["id"] for c in comps}:
        comps.append({
            "id": 3,
            "name": "Pacific Pantry Co.",
            "slug": "pacific-pantry",
            "erp_system": "NetSuite",
            "default_currency": "USD",
            "is_active": True,
        })
        COMPANIES_PATH.write_text(json.dumps(comps, indent=2))
        print("Added company_id=3 Pacific Pantry Co.")


def map_status(raw):
    if raw is None:
        return "open", None
    s = str(raw).strip().lower()
    if not s:
        return "open", None
    if s in ("complete", "completed"):
        return "resolved", "won"
    if "filing in progress" in s:
        return "disputed", "draft"
    if "filed" in s:
        return "disputed", "submitted"
    if "in review" in s:
        return "disputed", "in_review"
    if s in ("dispute", "disputed"):
        return "disputed", "draft"
    if s == "open":
        return "open", None
    if "send to ops" in s:
        return "open", None
    return "open", None


def clean_amount(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if abs(val) > 1e8:
            return None
        return round(float(val), 2)
    s = str(val).strip()
    if not s or s.upper() in ("TBD", "N/A", "NULL", "-", ""):
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace("USD", "").replace(",", "").strip()
    try:
        n = float(s)
        if neg:
            n = -n
        if abs(n) > 1e8:
            return None
        return round(n, 2)
    except ValueError:
        return None


def clean_date(val):
    """Return (yyyy-mm-dd | None, flags list)."""
    flags = []
    if val is None:
        return None, ["missing_date"]
    if isinstance(val, (int, float)):
        try:
            ts = int(val)
            if ts > 1e9:
                parsed = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if parsed > "2026-12-31":
                    flags.append("future_date")
                if parsed < "2020-01-01":
                    flags.append("stale_date")
                return parsed, flags
        except Exception:
            return None, ["missing_date"]

    s = str(val).strip()
    if not s or s.upper() in ("NULL", "N/A", "-", "0000-00-00"):
        return None, ["missing_date"]
    if re.match(r"20\d{2}-13-", s):
        return None, ["invalid_date"]

    formats = [
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
        "%m/%d/%Y", "%m/%d/%y", "%d-%m-%Y", "%d/%m/%Y",
        "%B %d, %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S.000Z",
        "%Y-%m-%dT%H:%M:%SZ", "%m-%d-%Y", "%d-%m-%y",
    ]
    parsed = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(s[:26].strip(), fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue
    if not parsed:
        m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                parsed = f"{y:04d}-{mo:02d}-{d:02d}"

    if not parsed:
        return None, ["missing_date"]
    if parsed > "2026-12-31":
        flags.append("future_date")
    if parsed < "2020-01-01":
        flags.append("stale_date")
    return parsed, flags


def clean_invoice(val):
    if val is None:
        return ""
    s = str(val).strip()
    if s.upper() in ("NULL", "N/A", "-", ""):
        return ""
    return s


def normalize_retailer_name(name):
    if name is None:
        return ""
    s = str(name).strip().lower()
    s = re.sub(r"\s+", " ", s).replace("\t", "").strip()
    return s


def resolve_retailer(row):
    name = row.get("retailer_name") or row.get("retailer") or ""
    key = normalize_retailer_name(name)
    if not key or key in ("null", "n/a", "-", ""):
        return None, name
    if key in RETAILER_ALIASES:
        return RETAILER_ALIASES[key], name
    for alias, rid in RETAILER_ALIASES.items():
        if alias in key or key in alias:
            return rid, name
    return None, name


def resolve_reason(row):
    raw = row.get("reason") or row.get("deduction_reason") or ""
    if raw is None:
        return None, ""
    key = str(raw).strip().lower()
    code = REASON_MAP.get(key)
    if code:
        return code, str(raw).strip()
    for k, c in REASON_MAP.items():
        if k and k in key and c:
            return c, str(raw).strip()
    return None, str(raw).strip() if raw else ""


def is_deleted(val):
    if val is True or val == 1 or str(val).lower() in ("true", "1"):
        return True
    return False


def main():
    if not RAW.exists():
        raise SystemExit(
            f"Raw export not found at {RAW}.\n"
            "Place pasted-text.txt in data/ or pass the full export path."
        )

    ensure_reference_data()

    with open(RAW) as f:
        raw = json.load(f)

    cleaned = []
    stats: Counter = Counter()
    seen_ids = set()

    for row in raw:
        stats["total"] += 1
        if is_deleted(row.get("is_deleted")):
            stats["skipped_deleted"] += 1
            continue

        rid = row.get("id")
        if rid is None:
            stats["skipped_no_id"] += 1
            continue
        sid = str(rid)
        if sid in seen_ids:
            stats["skipped_dup_id"] += 1
            continue
        seen_ids.add(sid)

        cid = row.get("company_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is None or cid == 0:
            stats["bad_company"] += 1
            cid = 1
        if cid not in (1, 2, 3, 4, 5):
            cid = 1

        amt = clean_amount(row.get("amount"))
        if amt is None:
            amt = clean_amount(row.get("total_amount"))
        if amt is None:
            stats["no_amount"] += 1
            amt = 0.0
        amount = abs(amt) if amt != 0 else 0.0
        if amount > 1_000_000:
            stats["outlier_zeroed"] += 1
            amount = 0.0

        retailer_id, retailer_raw = resolve_retailer(row)
        if retailer_id is None:
            stats["unmapped_retailer"] += 1

        reason_code, reason_raw = resolve_reason(row)
        if reason_code is None:
            stats["unmapped_reason"] += 1
            reason_code = "OTHER"

        status, dispute_status = map_status(row.get("status"))
        deducted, date_flags = clean_date(row.get("deducted_at"))
        invoice = clean_invoice(row.get("invoice_number"))
        notes = "" if row.get("notes") is None else str(row.get("notes")).strip()

        flags = list(date_flags)
        if retailer_id is None:
            flags.append("unknown_retailer")
        if reason_code == "OTHER":
            flags.append("unknown_reason")
        if not deducted:
            flags.append("missing_date")
        if amount == 0:
            flags.append("zero_amount")
        if abs(amt or 0) > 50000:
            flags.append("large_amount")
        if (amt or 0) < 0:
            flags.append("credit_sign")

        # Description must agree with reason fields
        if reason_raw:
            description = reason_raw
        elif reason_code != "OTHER":
            description = REASON_LABELS.get(reason_code, reason_code)
        else:
            description = "No reason provided"

        # Complete → won assumes full recovery (export has no recovered field)
        disputed_amount = None
        recovered_amount = None
        if status == "disputed":
            disputed_amount = amount
        elif status == "resolved" and dispute_status == "won":
            disputed_amount = amount
            recovered_amount = amount
            stats["assumed_full_recovery"] += 1

        rec = {
            "id": f"DED-{sid}",
            "company_id": cid,
            "retailer_id": retailer_id,
            "retailer_raw": (retailer_raw or "").strip(),
            "invoice_number": invoice or f"(none-{sid})",
            "deduction_date": deducted or "1970-01-01",
            "amount": amount,
            "currency": "USD",
            "reason_code": reason_code,
            "reason_raw": reason_raw,
            "description": description,
            "status": status,
            "dispute_status": dispute_status,
            "disputed_amount": disputed_amount,
            "recovered_amount": recovered_amount,
            "notes": notes,
            "data_flags": flags,
            "is_deleted": False,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        cleaned.append(rec)
        stats["kept"] += 1

    OUT.write_text(json.dumps(cleaned, indent=2))
    print("=== Cleaning stats ===")
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    print(f"Wrote {len(cleaned)} records → {OUT}")


if __name__ == "__main__":
    main()
