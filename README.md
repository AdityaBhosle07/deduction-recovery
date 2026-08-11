# Deduction Recovery

Internal tool for tracking retailer/distributor deductions and managing disputes so analysts can recover short-paid amounts.

**TL;DR**

- **Run:** `cd backend && pip install -r requirements.txt && python3 -m uvicorn app.main:app --port 8000` → [http://localhost:8000](http://localhost:8000) (zero-dependency SPA). For the **intended React + TypeScript UI** (brief preferred stack): also run `cd frontend && npm install && npm run dev` against the same API.
- **Dispute stages** are a state machine with API-enforced transitions (`draft → submitted → in_review → won | partial | lost`).
- **Recovery is a dollar amount**, not a boolean — dashboard tracks recovered $, recovery rate, and potential recovery still on the table.
- **Multi-company export** is scoped with a company filter (All vs one company). **1,007 raw records → 741** after dropping soft-deleted rows and duplicate IDs (full export, not the earlier sample).

Retailers (Kroger, Target, Costco, KeHE, UNFI, …) often pay less than the invoice and attach a reason. Some deductions are fair; many are not. This app replaces the out-of-control spreadsheet with a workflow, recovery tracking, and multi-company visibility.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Architecture](#architecture)
3. [Dispute workflow](#dispute-workflow)
4. [What “recovered” means](#what-recovered-means)
5. [Multi-company model](#multi-company-model)
6. [Data pipeline](#data-pipeline)
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
- Node 18+ **if** you want the React UI 

### 1. API (required)

```bash
cd backend
pip install -r requirements.txt   # fastapi, uvicorn, pydantic
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API + **fallback SPA**: [http://localhost:8000](http://localhost:8000)  
- Swagger: [http://localhost:8000/docs](http://localhost:8000/docs)  
- Health: [http://localhost:8000/api/health](http://localhost:8000/api/health)

The SPA at `:8000` is a **zero-dependency fallback** so reviewers can exercise the full product with one command. Same API powers both UIs.

### 2. React + TypeScript UI (intended / preferred stack)

The brief preferred React + TypeScript (MUI or shadcn). Source is under `frontend/`:

```bash
cd frontend
npm install
npm run dev
```


### Re-clean the raw export

```bash
python3 scripts/clean_deductions.py
```

Then refresh the browser (data is read from disk on each request).

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│  Analyst browser                                                 │
│  Preferred: React+TS (Vite :5173)                                │
│  Fallback:  SPA served by FastAPI at :8000                       │
└────────────────────────────┬────────────────────────────────────┘
                             │  JSON
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI  (backend/app/main.py)                                  │
│  /api/deductions  /api/dashboard  /api/companies  /api/workflow  │
│  PATCH transitions enforced by dispute state machine             │
└────────────────────────────┬────────────────────────────────────┘
                             │  read / write
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  data/                                                           │
│    deductions.json   ← cleaned working set                       │
│    companies.json    retailers.json    dispute_reasons.json      │
└─────────────────────────────────────────────────────────────────┘
                             ▲
                             │  scripts/clean_deductions.py
┌─────────────────────────────────────────────────────────────────┐
│  Raw export — 1,007 rows (messy free-text, $ amounts, bad dates) │
└─────────────────────────────────────────────────────────────────┘
```

| Layer | Choice |
|-------|--------|
| API | Python 3 + FastAPI |
| Intended UI | React + TypeScript (`frontend/`) |
| Fallback UI | SPA at `backend/static/index.html` |
| Persistence | JSON files (single-analyst MVP) |
| Cleaning | `scripts/clean_deductions.py` |

---

## Dispute workflow

### How stages are modeled

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
2. Use explicit action buttons: **Start Dispute**, **Mark Submitted**, **Mark In Review**, **Won / Partial / Lost**, or **Accept (Not Dispute)**.
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
| **Recovery rate** | `recovered_total ÷ disputed_amount_total` for rows that entered dispute. **The denominator includes disputes still open (in flight), not only settled ones** — so this rate will rise as more resolve. It is “cash back so far vs pipeline that entered dispute,” not a final win rate on closed cases. |
| **Potential recovery** | Outstanding disputable $ = open (if typically disputable) + (disputed − recovered) |

Currency always shows **two decimal places** so table, badge, and input never disagree (e.g. `$141.72`, not `$142`).

### Assumed recovery (import)

The export has no separate recovered field. Status **Complete** is mapped to `resolved` / `won` with `recovered_amount = amount`. Those rows are flagged:

- `data_flags` includes `assumed_recovery`
- `recovery_assumed: true`
- UI shows an **assumed** badge (and `*` in the table) so inferred cash is never confused with analyst-entered recovery

Live disputes set recovered explicitly in the UI and are not marked assumed.

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
```

- **All Companies** — portfolio view.
- **Single company** — focused book of business.
- Inactive companies stay in history but are de-emphasized in the picker.
- MVP uses a **soft filter** (no auth). Production should enforce hard tenant isolation.

`company_id: 3` appears in the full export but not the original sample reference file; it is modeled as **Pacific Pantry Co.**

---

## Data pipeline

**1,007 raw records → 741 active** after dropping soft-deleted rows and duplicate IDs. This is the **full export**, not the earlier small sample.

```text
 Raw JSON export (1,007 rows)
       │
       ▼
 scripts/clean_deductions.py
   • drop soft-deleted / dup ids          → 741 kept
   • normalize amounts ($, commas, TBD)
   • parse many date formats
   • map retailer name variants → ids
   • map reason phrases → codes
   • map status phrases → workflow
   • Complete → won + full recovery + assumed_recovery flag
   • empty reason → OTHER
   • flag future_date, unknown_*, etc.
       │
       ▼
 data/deductions.json  →  API enrich  →  UI
```

| Flag | Meaning |
|------|---------|
| `unknown_retailer` | Name did not map to canonical retailers.json |
| `unknown_reason` | Empty or unmapped reason → code OTHER |
| `missing_date` / `invalid_date` | Could not parse deduction date |
| `future_date` | Date after 2026-12-31 (export contains 2028 values) |
| `assumed_recovery` | Recovered $ inferred from export status Complete |
| `zero_amount` / `large_amount` / `credit_sign` | Amount anomalies |

Unmapped retailers keep `retailer_raw` and still appear — rows are not dropped on the floor.

---

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Liveness |
| GET | `/api/companies` | Companies (`?active_only=true`) |
| GET | `/api/retailers` | Canonical retailers/distributors |
| GET | `/api/reasons` | Dispute reason codes |
| GET | `/api/deductions` | List + filters |
| GET | `/api/deductions/{id}` | Single enriched deduction |
| PATCH | `/api/deductions/{id}` | Update status, dispute_status, amounts, notes |
| GET | `/api/dashboard` | Aggregates + breakdowns |
| GET | `/api/workflow` | Status machine metadata |
| GET | `/` | Fallback SPA |

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Project layout

```text
deduction-recovery/
├── README.md
├── docs/
│   ├── dashboard.png
│   └── dispute-modal.png
├── backend/
│   ├── app/main.py
│   ├── requirements.txt
│   └── static/index.html          # fallback SPA
├── frontend/                      # intended React + TypeScript UI
│   └── src/App.tsx
├── data/
│   ├── deductions.json            # 741 cleaned rows
│   ├── companies.json
│   ├── retailers.json
│   └── dispute_reasons.json
└── scripts/
    └── clean_deductions.py
```

---

## Assumptions

1. **Single analyst MVP** — no authentication or roles. Company selector is a soft filter, not hard tenancy.
2. **Complete ⇒ full recovery** — export has no separate recovered field. Status `Complete` → `resolved` / `won` with `recovered_amount = amount`, flagged **`assumed_recovery`** in data and UI. Live disputes set recovered explicitly.
3. **company_id 3** — present in the full export, absent from the original sample companies file → **Pacific Pantry Co.**
4. **Empty / unknown reasons** → code `OTHER` (not typically disputable).
5. **Far-future dates** (e.g. 2028) kept but flagged `future_date`.
6. **Recovery rate denominator** includes disputes still in flight (not only settled). Rate rises as more resolve.
7. **JSON file persistence** is enough for one concurrent user.

---

## Tradeoffs

| Decision | Why | Cost |
|----------|-----|------|
| JSON files instead of Postgres | Ship a usable demo with zero ops | No concurrent writes, weak queryability, no real audit table |
| SPA as one-command fallback; React as intended UI | Brief asked for React+TS; reviewers can still run without Node | Someone who only runs `uvicorn` never sees React unless they read this |
| Soft company filter | Matches multi-company export quickly | Not true isolation |
| Assume full recovery on Complete | Only signal available in export | May overstate recovered $ — mitigated with **assumed** badge |
| Free-form recovered amount | Analyst knows the credit memo | No automatic match to AR cash apps |
| Notes as light audit trail | Simple | No immutable history of who changed status when |

---

## What we’d improve with more time

1. **Postgres + migrations** and an append-only audit log of status transitions.
2. **Auth & RBAC** — analyst vs finance manager; hard company-level isolation.
3. **Bulk actions** — start dispute / export claim packets for many rows.
4. **Attachments** — BOLs, photos, portal screenshots on each dispute.
5. **Aging / SLAs** — days open, days since last action, stuck-in-submitted alerts.
6. **Import pipeline** — new remittance files; flag unknown reason codes.
7. **AR integration** — map `recovered_amount` to actual cash applications.
8. **Tests** — API contract tests + Playwright path through the workflow.
9. **shadcn/MUI polish** on the React app to fully match the brief’s UI library preference.

---

## Verification checklist

```bash
curl -s http://localhost:8000/api/dashboard | python3 -m json.tool | head -25
curl -s http://localhost:8000/api/deductions/DED-1548 | python3 -m json.tool
curl -s http://localhost:8000/api/companies | grep -i pacific
```

| Check | Pass if |
|-------|---------|
| Recovered KPI | ≠ $0 (~$517k on full clean set) |
| Recovery rate | Interpreted as cash-so-far vs in-flight disputed pool |
| DED-1548 recovered | numeric; **assumed** badge if import Complete |
| DED-1548 reason | Other / Unknown when no raw reason |
| Companies | includes Pacific Pantry Co. (id 3) |
| Future dates | `future_date` in `data_flags` where applicable |
| Cents display | `$141.72` not `$142` |
| Row counts | 1,007 raw → 741 active |

---
