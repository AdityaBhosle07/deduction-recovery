"""
Deduction Recovery API
- Dispute workflow state machine
- Multi-company filtering
- Recovery metrics (disputed vs recovered)
- Null-safe retailer/reason enrichment + data_flags
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEDUCTIONS_FILE = DATA_DIR / "deductions.json"
COMPANIES_FILE = DATA_DIR / "companies.json"
RETAILERS_FILE = DATA_DIR / "retailers.json"
REASONS_FILE = DATA_DIR / "dispute_reasons.json"
STATIC_DIR = Path(__file__).parent.parent / "static"

app = FastAPI(
    title="Deduction Recovery API",
    description="Internal tool for tracking and disputing retailer deductions",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Enums & workflow -----

class DeductionStatus(str, Enum):
    open = "open"
    disputed = "disputed"
    resolved = "resolved"
    closed = "closed"


class DisputeStatus(str, Enum):
    draft = "draft"
    submitted = "submitted"
    in_review = "in_review"
    won = "won"
    partial = "partial"
    lost = "lost"
    not_disputed = "not_disputed"


DISPUTE_TRANSITIONS = {
    None: ["draft", "not_disputed"],
    "draft": ["submitted", "not_disputed"],
    "submitted": ["in_review", "won", "partial", "lost"],
    "in_review": ["won", "partial", "lost"],
    "won": [],
    "partial": [],
    "lost": [],
    "not_disputed": [],
}


class DeductionUpdate(BaseModel):
    status: Optional[DeductionStatus] = None
    dispute_status: Optional[DisputeStatus] = None
    disputed_amount: Optional[float] = None
    recovered_amount: Optional[float] = None
    notes: Optional[str] = None


# ----- Data helpers -----

def load_json(path: Path) -> List[Dict]:
    with open(path, "r") as f:
        return json.load(f)


def save_deductions(data: List[Dict]) -> None:
    with open(DEDUCTIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_lookup(path: Path, key: str = "id") -> Dict:
    items = load_json(path)
    return {item[key]: item for item in items}


def enrich_deduction(
    d: Dict,
    companies: Dict,
    retailers: Dict,
    reasons: Dict,
) -> Dict:
    out = copy.deepcopy(d)
    company = companies.get(d["company_id"], {})

    rid = d.get("retailer_id")
    retailer = retailers.get(rid, {}) if rid is not None else {}
    reason = reasons.get(d.get("reason_code"), {})

    out["company"] = {
        "id": company.get("id"),
        "name": company.get("name", "Unknown"),
        "slug": company.get("slug"),
        "is_active": company.get("is_active", False),
        "default_currency": company.get("default_currency"),
    }
    out["retailer"] = {
        "id": retailer.get("id"),
        "name": retailer.get("name") or d.get("retailer_raw") or "Unknown",
        "type": retailer.get("type"),
        "region": retailer.get("region"),
    }
    out["reason"] = {
        "code": reason.get("code", d.get("reason_code")),
        "label": reason.get("label") or d.get("reason_raw") or d.get("reason_code"),
        "category": reason.get("category", "Unknown"),
        "typically_disputable": reason.get("typically_disputable", False),
    }
    out["data_flags"] = d.get("data_flags") or []
    out["reason_raw"] = d.get("reason_raw") or ""
    out["retailer_raw"] = d.get("retailer_raw") or ""
    # Keep description consistent (prefer stored description / reason_raw)
    if not out.get("description"):
        out["description"] = d.get("reason_raw") or out["reason"]["label"] or "No reason provided"

    amount = d.get("amount") or 0
    disputed = d.get("disputed_amount")
    recovered = d.get("recovered_amount") or 0

    out["recovery_rate"] = None
    if disputed and disputed > 0:
        out["recovery_rate"] = round((recovered / disputed) * 100, 1)

    out["potential_recovery"] = 0.0
    if d.get("status") in ("open", "disputed") and reason.get("typically_disputable"):
        out["potential_recovery"] = amount if disputed is None else max(0.0, disputed - recovered)

    return out


# ----- Routes -----

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/companies")
def list_companies(active_only: bool = Query(False)):
    companies = load_json(COMPANIES_FILE)
    if active_only:
        companies = [c for c in companies if c.get("is_active")]
    return companies


@app.get("/api/retailers")
def list_retailers():
    return load_json(RETAILERS_FILE)


@app.get("/api/reasons")
def list_reasons():
    return load_json(REASONS_FILE)


@app.get("/api/deductions")
def list_deductions(
    company_id: Optional[int] = Query(None),
    retailer_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    dispute_status: Optional[str] = Query(None),
    reason_code: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    only_disputable: bool = Query(False),
):
    deductions = load_json(DEDUCTIONS_FILE)
    companies = get_lookup(COMPANIES_FILE)
    retailers = get_lookup(RETAILERS_FILE)
    reasons = get_lookup(REASONS_FILE, key="code")

    results = []
    for d in deductions:
        if company_id is not None and d["company_id"] != company_id:
            continue
        if retailer_id is not None and d.get("retailer_id") != retailer_id:
            continue
        if status and d.get("status") != status:
            continue
        if dispute_status and d.get("dispute_status") != dispute_status:
            continue
        if reason_code and d.get("reason_code") != reason_code:
            continue

        reason = reasons.get(d.get("reason_code"), {})
        if only_disputable and not reason.get("typically_disputable", False):
            continue

        if search:
            q = search.lower()
            searchable = " ".join(
                [
                    d.get("id", ""),
                    d.get("invoice_number", ""),
                    d.get("description", ""),
                    d.get("notes", ""),
                    d.get("retailer_raw", ""),
                    companies.get(d["company_id"], {}).get("name", ""),
                    (retailers.get(d.get("retailer_id")) or {}).get("name", ""),
                ]
            ).lower()
            if q not in searchable:
                continue

        results.append(enrich_deduction(d, companies, retailers, reasons))

    results.sort(key=lambda x: x.get("deduction_date", ""), reverse=True)
    return results


@app.get("/api/deductions/{deduction_id}")
def get_deduction(deduction_id: str):
    deductions = load_json(DEDUCTIONS_FILE)
    companies = get_lookup(COMPANIES_FILE)
    retailers = get_lookup(RETAILERS_FILE)
    reasons = get_lookup(REASONS_FILE, key="code")

    for d in deductions:
        if d["id"] == deduction_id:
            return enrich_deduction(d, companies, retailers, reasons)
    raise HTTPException(status_code=404, detail="Deduction not found")


@app.patch("/api/deductions/{deduction_id}")
def update_deduction(deduction_id: str, update: DeductionUpdate):
    deductions = load_json(DEDUCTIONS_FILE)
    companies = get_lookup(COMPANIES_FILE)
    retailers = get_lookup(RETAILERS_FILE)
    reasons = get_lookup(REASONS_FILE, key="code")

    idx = None
    for i, d in enumerate(deductions):
        if d["id"] == deduction_id:
            idx = i
            break
    if idx is None:
        raise HTTPException(status_code=404, detail="Deduction not found")

    d = deductions[idx]
    current_dispute = d.get("dispute_status")

    if update.dispute_status is not None:
        allowed = DISPUTE_TRANSITIONS.get(current_dispute, [])
        if (
            update.dispute_status.value not in allowed
            and update.dispute_status.value != current_dispute
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid transition from '{current_dispute}' "
                    f"to '{update.dispute_status.value}'. Allowed: {allowed}"
                ),
            )

        d["dispute_status"] = update.dispute_status.value

        if update.dispute_status.value in ("draft", "submitted", "in_review"):
            d["status"] = "disputed"
        elif update.dispute_status.value in ("won", "partial", "lost"):
            d["status"] = "resolved"
        elif update.dispute_status.value == "not_disputed":
            d["status"] = "closed"

        if (
            update.dispute_status.value in ("draft", "submitted")
            and d.get("disputed_amount") is None
        ):
            d["disputed_amount"] = d.get("amount")

        # Won with no explicit recovered → assume full
        if (
            update.dispute_status.value == "won"
            and update.recovered_amount is None
            and d.get("recovered_amount") is None
        ):
            d["recovered_amount"] = d.get("disputed_amount") or d.get("amount") or 0

        if update.dispute_status.value == "lost" and update.recovered_amount is None:
            d["recovered_amount"] = 0

    if update.status is not None:
        d["status"] = update.status.value

    if update.disputed_amount is not None:
        d["disputed_amount"] = update.disputed_amount

    if update.recovered_amount is not None:
        d["recovered_amount"] = update.recovered_amount
        disputed = d.get("disputed_amount") or 0
        if (
            disputed > 0
            and update.recovered_amount >= disputed
            and d.get("dispute_status") in ("submitted", "in_review", "partial")
        ):
            d["dispute_status"] = "won"
            d["status"] = "resolved"
        elif (
            disputed > 0
            and 0 < update.recovered_amount < disputed
            and d.get("dispute_status") in ("submitted", "in_review")
        ):
            d["dispute_status"] = "partial"
            d["status"] = "resolved"

    if update.notes is not None:
        d["notes"] = update.notes

    d["updated_at"] = datetime.utcnow().isoformat() + "Z"
    deductions[idx] = d
    save_deductions(deductions)
    return enrich_deduction(d, companies, retailers, reasons)


@app.get("/api/dashboard")
def dashboard(company_id: Optional[int] = Query(None)):
    deductions = load_json(DEDUCTIONS_FILE)
    companies = get_lookup(COMPANIES_FILE)
    retailers = get_lookup(RETAILERS_FILE)
    reasons = get_lookup(REASONS_FILE, key="code")

    if company_id is not None:
        deductions = [d for d in deductions if d["company_id"] == company_id]

    total_deductions = len(deductions)
    open_count = disputed_count = resolved_count = closed_count = 0
    total_amount = open_amount = disputed_amount_total = 0.0
    recovered_total = potential_recovery = 0.0

    by_reason: Dict[str, Dict] = {}
    by_retailer: Dict[Any, Dict] = {}
    by_company: Dict[int, Dict] = {}
    by_status: Dict[str, int] = {}

    for d in deductions:
        amount = d.get("amount") or 0
        recovered = d.get("recovered_amount") or 0
        disputed = d.get("disputed_amount")
        status = d.get("status", "open")
        reason_code = d.get("reason_code", "OTHER")
        reason = reasons.get(reason_code, {})
        retailer_id = d.get("retailer_id")
        cid = d["company_id"]

        total_amount += amount
        recovered_total += recovered
        by_status[status] = by_status.get(status, 0) + 1

        if status == "open":
            open_count += 1
            open_amount += amount
            if reason.get("typically_disputable"):
                potential_recovery += amount
        elif status == "disputed":
            disputed_count += 1
            if disputed:
                disputed_amount_total += disputed
                potential_recovery += max(0.0, disputed - recovered)
            else:
                potential_recovery += amount
        elif status == "resolved":
            resolved_count += 1
            if disputed:
                disputed_amount_total += disputed
        elif status == "closed":
            closed_count += 1

        if reason_code not in by_reason:
            by_reason[reason_code] = {
                "code": reason_code,
                "label": reason.get("label", reason_code),
                "count": 0,
                "amount": 0.0,
                "recovered": 0.0,
                "typically_disputable": reason.get("typically_disputable", False),
            }
        by_reason[reason_code]["count"] += 1
        by_reason[reason_code]["amount"] += amount
        by_reason[reason_code]["recovered"] += recovered

        rkey = retailer_id if retailer_id is not None else -1
        if rkey not in by_retailer:
            ret = retailers.get(retailer_id, {}) if retailer_id is not None else {}
            by_retailer[rkey] = {
                "id": retailer_id,
                "name": ret.get("name") or d.get("retailer_raw") or "Unknown",
                "count": 0,
                "amount": 0.0,
                "recovered": 0.0,
            }
        by_retailer[rkey]["count"] += 1
        by_retailer[rkey]["amount"] += amount
        by_retailer[rkey]["recovered"] += recovered

        if cid not in by_company:
            comp = companies.get(cid, {})
            by_company[cid] = {
                "id": cid,
                "name": comp.get("name", "Unknown"),
                "count": 0,
                "amount": 0.0,
                "recovered": 0.0,
                "potential": 0.0,
            }
        by_company[cid]["count"] += 1
        by_company[cid]["amount"] += amount
        by_company[cid]["recovered"] += recovered
        if status in ("open", "disputed") and reason.get("typically_disputable"):
            by_company[cid]["potential"] += (disputed or amount) - recovered

    recovery_rate = None
    if disputed_amount_total > 0:
        recovery_rate = round((recovered_total / disputed_amount_total) * 100, 1)

    return {
        "summary": {
            "total_deductions": total_deductions,
            "open_count": open_count,
            "disputed_count": disputed_count,
            "resolved_count": resolved_count,
            "closed_count": closed_count,
            "total_amount": round(total_amount, 2),
            "open_amount": round(open_amount, 2),
            "disputed_amount": round(disputed_amount_total, 2),
            "recovered_total": round(recovered_total, 2),
            "potential_recovery": round(potential_recovery, 2),
            "recovery_rate": recovery_rate,
        },
        "by_status": by_status,
        "by_reason": sorted(by_reason.values(), key=lambda x: -x["amount"]),
        "by_retailer": sorted(by_retailer.values(), key=lambda x: -x["amount"]),
        "by_company": sorted(by_company.values(), key=lambda x: -x["amount"]),
    }


@app.get("/api/workflow")
def get_workflow():
    return {
        "statuses": [
            {"value": "open", "label": "Open", "description": "Newly imported, not yet reviewed"},
            {"value": "disputed", "label": "Disputed", "description": "Actively being chased"},
            {"value": "resolved", "label": "Resolved", "description": "Dispute concluded (won/partial/lost)"},
            {"value": "closed", "label": "Closed", "description": "Accepted or not pursued"},
        ],
        "dispute_statuses": [
            {"value": "draft", "label": "Draft", "description": "Preparing documentation"},
            {"value": "submitted", "label": "Submitted", "description": "Sent to retailer"},
            {"value": "in_review", "label": "In Review", "description": "Retailer is reviewing"},
            {"value": "won", "label": "Won", "description": "Full recovery"},
            {"value": "partial", "label": "Partial", "description": "Partial recovery"},
            {"value": "lost", "label": "Lost", "description": "Retailer denied claim"},
            {"value": "not_disputed", "label": "Not Disputed", "description": "Accepted as valid"},
        ],
        "transitions": DISPUTE_TRANSITIONS,
    }


# Static UI
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "API running. Frontend not found at backend/static/index.html"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
