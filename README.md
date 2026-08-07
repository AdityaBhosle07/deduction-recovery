# Deduction Recovery

Internal tool for tracking retailer/distributor deductions and managing disputes so analysts can recover short-paid amounts.

Retailers (Kroger, Target, Costco, KeHE, UNFI, …) often pay less than the invoice and attach a reason. Some deductions are fair; many are not. This app replaces the out-of-control spreadsheet with a workflow, recovery tracking, and multi-company visibility.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Architecture](#architecture)
3. [Dispute workflow](#dispute-workflow)
4. [What “recovered” means](#what-recovered-means)
5. [Multi-company model](#multi-company-model)
6. [Data pipeline (messy export → clean records)](#data-pipeline)
7. [API overview](#api-overview)
8. [Project layout](#project-layout)
9. [Assumptions](#assumptions)
10. [Tradeoffs](#tradeoffs)
11. [What we’d improve with more time](#what-wed-improve-with-more-time)
12. [Verification checklist](#verification-checklist)

---

## Quick start

### Requirements

- Python 3.10+
- (Optional) Node 18+ if you want the React frontend

### Run the working app (one command)

```bash
cd backend
pip install -r requirements.txt   # fastapi, uvicorn, pydantic
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000**

API docs: **http://localhost:8000/docs**  
Health: **http://localhost:8000/api/health**

### Optional: React frontend

```bash
cd frontend
npm install
npm run dev
```

Point the browser at the Vite URL (usually http://localhost:5173). The API must still be on port 8000.

### Re-clean the raw export

If you drop a new export in place:

```bash
# Prefer data/pasted-text.txt, or the path the script discovers
python3 scripts/clean_deductions.py
```

Then refresh the browser (data is read from disk on each request).

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Analyst browser                           │
│              http://localhost:8000  (static SPA)                 │
│         optional: React/Vite on :5173 → same JSON API            │
└────────────────────────────┬────────────────────────────────────┘
                             │  JSON
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI  (backend/app/main.py)               │
│  /api/deductions  /api/dashboard  /api/companies  /api/workflow  │
│  PATCH transitions enforced by dispute state machine             │
└────────────────────────────┬────────────────────────────────────┘
                             │  read / write
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  data/                                                           │
│    deductions.json   ← cleaned working set (741 rows)            │
│    companies.json    retailers.json    dispute_reasons.json      │
└─────────────────────────────────────────────────────────────────┘
                             ▲
                             │  scripts/clean_deductions.py
┌─────────────────────────────────────────────────────────────────┐
│  Raw export (messy JSON)                                         │
│  free-text statuses, $ amounts, bad dates, KeHE name variants    │
└─────────────────────────────────────────────────────────────────┘
```

**Stack**

| Layer | Choice |
|-------|--------|
| API | Python 3 + FastAPI |
| Working UI | Single-page app served by FastAPI (`backend/static/index.html`) |
| Preferred UI source | React + TypeScript (`frontend/`) |
| Persistence | JSON files (single-analyst MVP) |
| Cleaning | `scripts/clean_deductions.py` |

---

## Dispute workflow

### How stages are modeled

Two levels on every deduction:

| Field | Role |
|-------|------|
| `status` | Coarse: `open` → `disputed` → `resolved` \| `closed` |
| `dispute_status` | Fine-grained chase state |

```text
                    ┌──────────────┐
                    │    open      │
                    │ dispute=null │
                    └──────┬───────┘
           Start Dispute   │   Accept (not dispute)
               ┌───────────┴───────────┐
               ▼                       ▼
        ┌────────────┐          ┌──────────────┐
        │   draft    │          │ not_disputed │
        │  disputed  │          │    closed    │
        └─────┬──────┘          └──────────────┘
              │ Mark Submitted
              ▼
        ┌────────────┐
        │ submitted  │
        └─────┬──────┘
              │ Mark In Review
              ▼
        ┌────────────┐
        │ in_review  │
        └─────┬──────┘
       ┌──────┼──────────────┐
       ▼      ▼              ▼
   ┌─────┐ ┌─────────┐  ┌──────┐
   │ won │ │ partial │  │ lost │
   └──┬──┘ └────┬────┘  └──┬───┘
      │         │          │
      └─────────┴──────────┘
                ▼
         status = resolved
```

**Rules**

- Transitions are enforced by the API (`DISPUTE_TRANSITIONS`). Invalid moves return HTTP 400.
- Starting a dispute defaults `disputed_amount` to the full deduction amount.
- **Won** with no explicit recovered amount → `recovered_amount` set to full disputed amount.
- **Lost** → `recovered_amount = 0`.
- **Partial** → analyst enters the actual recovered dollars.

### How the analyst moves a stage

1. Open a deduction from the list.
2. Use explicit action buttons (not free-text status editing):
   - **Start Dispute** → `draft`
   - **Mark Submitted** → `submitted`
   - **Mark In Review** → `in_review`
   - **Won (Full)** / **Partial Recovery** / **Lost**
   - **Accept (Not Dispute)** → closed
3. Save notes and recovered amount at any time.

---

## What “recovered” means

Retailers often credit only part of a claim. The model keeps three numbers:

```text
  amount              original short-pay on the remittance
       │
       ▼
  disputed_amount     what we claim (defaults to amount when dispute starts)
       │
       ▼
  recovered_amount    what they actually paid back (may be < disputed)
```

**Dashboard KPIs**

| KPI | Definition |
|-----|------------|
| **Recovered ($)** | Sum of `recovered_amount` |
| **Recovery rate** | `recovered_total ÷ disputed_amount_total` for rows that entered dispute |
| **Potential recovery** | Outstanding disputable $ = open (if typically disputable) + (disputed − recovered) |

So “recovered” means **cash/credit received**, not merely “we won the argument.”

Currency is always shown with **two decimal places** so table, badge, and input never disagree (e.g. `$141.72`, not `$142`).

---

## Multi-company model

The export spans multiple operating companies.

```text
┌──────────────────────────────────────────┐
│  Header: [ All Companies ▾ ]             │
│           Cascade Snacks Co.             │
│           Northfield Beverage            │
│           Pacific Pantry Co.             │
│           Harbor & Vine Foods            │
└──────────────────┬───────────────────────┘
                   │ company_id filter
                   ▼
         List + Dashboard metrics
         only for selected context
```

- **All Companies** — portfolio view for a lead analyst.
- **Single company** — focused book of business.
- Inactive companies (e.g. Sunbelt Organics) remain in history but are de-emphasized in the picker.
- MVP uses a **soft filter** (no auth). Production should enforce hard tenant isolation per user.

`company_id: 3` appears in the full export but not the original sample reference file; it is modeled as **Pacific Pantry Co.**

---

## Data pipeline

Raw remittance exports are messy. Cleaning is a first-class step, not a one-off spreadsheet fix.

```text
 Raw JSON export
 (1,007 rows)
       │
       ▼
 ┌─────────────────────────────────────────┐
 │  scripts/clean_deductions.py            │
 │  • drop soft-deleted / dup ids          │
 │  • normalize amounts ($, commas, TBD)   │
 │  • parse many date formats              │
 │  • map retailer name variants → ids     │
 │  • map reason phrases → codes           │
 │  • map status phrases → workflow        │
 │  • Complete → won + full recovery       │
 │  • empty reason → OTHER                 │
 │  • flag future_date, unknown_*, etc.    │
 └─────────────────────────────────────────┘
       │
       ▼
 data/deductions.json  (741 active rows)
       │
       ▼
 API enrich (join companies, retailers, reasons)
       │
       ▼
 UI list / dashboard / detail modal
```

### Example data flags

| Flag | Meaning |
|------|---------|
| `unknown_retailer` | Name did not map to canonical retailers.json |
| `unknown_reason` | Empty or unmapped reason → code OTHER |
| `missing_date` / `invalid_date` | Could not parse deduction date |
| `future_date` | Date after 2026-12-31 (export contains 2028 values) |
| `zero_amount` / `large_amount` / `credit_sign` | Amount anomalies |
| `amount_outlier` | Extreme values zeroed for totals |

Unmapped retailers keep `retailer_raw` and still appear in the UI — rows are not dropped on the floor.

---

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Liveness |
| GET | `/api/companies` | Companies (`?active_only=true`) |
| GET | `/api/retailers` | Canonical retailers/distributors |
| GET | `/api/reasons` | Dispute reason codes |
| GET | `/api/deductions` | List + filters (`company_id`, `status`, `reason_code`, `search`, `only_disputable`, …) |
| GET | `/api/deductions/{id}` | Single enriched deduction |
| PATCH | `/api/deductions/{id}` | Update status, dispute_status, amounts, notes |
| GET | `/api/dashboard` | Aggregates + breakdowns by reason/retailer/company |
| GET | `/api/workflow` | Status machine metadata for clients |
| GET | `/` | Working SPA |

Interactive docs: **http://localhost:8000/docs**

---

## Project layout

```text
deduction-recovery/
├── README.md
├── backend/
│   ├── app/main.py              # FastAPI app + workflow + dashboard
│   ├── requirements.txt
│   └── static/index.html        # Working single-page UI
├── frontend/                    # React + TypeScript source (optional)
│   └── src/App.tsx
├── data/
│   ├── deductions.json          # Cleaned working set
│   ├── companies.json
│   ├── retailers.json
│   └── dispute_reasons.json
└── scripts/
    └── clean_deductions.py      # Export → clean JSON pipeline
```

---

## Assumptions

1. **Single analyst MVP** — no authentication or roles. Company selector is a soft filter, not hard tenancy.
2. **Complete ⇒ full recovery** — the raw export has no separate recovered field. Status `Complete` is mapped to `resolved` / `won` with `recovered_amount = amount`. Live disputes set recovered explicitly in the UI.
3. **company_id 3** — present in the full export, absent from the original sample companies file → added as **Pacific Pantry Co.**
4. **Empty / unknown reasons** → code `OTHER` (not typically disputable); description is the raw text or `"No reason provided"`.
5. **Far-future dates** (e.g. 2028) are kept but flagged `future_date` so analysts can see data smells.
6. **Currency** — amounts treated as USD for display unless a company default says otherwise; formatting always shows cents.
7. **“Typically disputable”** comes from `dispute_reasons.json` and drives potential-recovery math and the list filter.
8. **JSON file persistence** is enough for one concurrent user; not safe for multi-writer production use.

---

## Tradeoffs

| Decision | Why | Cost |
|----------|-----|------|
| JSON files instead of Postgres | Ship a usable demo with zero ops | No concurrent writes, weak queryability, no real audit table |
| Vanilla SPA as the default UI | One command to run; no npm install friction for reviewers | Preferred React+TS app is secondary unless you `npm run dev` |
| Custom CSS vs MUI/shadcn | Avoided heavy UI install issues in constrained envs | Less “design system” polish out of the box |
| Soft company filter | Matches multi-company export quickly | Not true isolation; must not ship to multi-tenant prod as-is |
| Assume full recovery on Complete | Only signal available in export | May overstate recovered $ until real AR cash-app is integrated |
| Free-form recovered amount | Analyst knows the credit memo | No automatic match to bank/AR applications |
| Notes as light audit trail | Simple | No immutable history of who changed status when |

---

## What we’d improve with more time

1. **Postgres + migrations** and an append-only audit log of status transitions.
2. **Auth & RBAC** — analyst vs finance manager; hard company-level data isolation.
3. **Bulk actions** — start dispute / export claim packets for many rows.
4. **Attachments** — BOLs, photos, portal screenshots on each dispute.
5. **Aging / SLAs** — days open, days since last action, stuck-in-submitted alerts.
6. **Import pipeline** — watch folder or upload for new remittance files; flag unknown reason codes for ops review.
7. **AR integration** — map `recovered_amount` to actual cash applications.
8. **Tests** — API contract tests + a small Playwright smoke path through the workflow.

---

## Verification checklist

After replace + restart:

```bash
# Dashboard should show non-zero recovered
curl -s http://localhost:8000/api/dashboard | python3 -m json.tool | head -25

# WON row should have recovered_amount set; OTHER reason when unknown
curl -s http://localhost:8000/api/deductions/DED-1548 | python3 -m json.tool

# Pacific Pantry present
curl -s http://localhost:8000/api/companies | grep -i pacific

# Currency: open DED-1715 in UI — table, badge, and input should all show $141.72
```

| Check | Pass if |
|-------|---------|
| Recovered KPI | ≠ $0 |
| DED-1548 recovered | numeric, not null |
| DED-1548 reason | Other / Unknown when no raw reason |
| Companies | includes Pacific Pantry Co. (id 3) |
| Future dates | `future_date` in `data_flags` where applicable |
| Cents display | `$141.72` not `$142` |

---
