import { useState, useEffect, useCallback } from 'react'
import './index.css'

const API = 'http://localhost:8000/api'

type Company = { id: number; name: string; slug: string; is_active: boolean; default_currency?: string }
type Retailer = { id: number; name: string; type: string; region: string }
type Reason = { code: string; label: string; category: string; typically_disputable: boolean }

type Deduction = {
  id: string
  company_id: number
  retailer_id: number
  invoice_number: string
  deduction_date: string
  amount: number
  currency: string
  reason_code: string
  description: string
  status: string
  dispute_status: string | null
  disputed_amount: number | null
  recovered_amount: number | null
  notes: string
  created_at: string
  updated_at: string
  company: { id: number; name: string; slug: string; is_active: boolean }
  retailer: { id: number; name: string; type: string; region: string }
  reason: { code: string; label: string; category: string; typically_disputable: boolean }
  recovery_rate: number | null
  potential_recovery: number
}

type Dashboard = {
  summary: {
    total_deductions: number
    open_count: number
    disputed_count: number
    resolved_count: number
    closed_count: number
    total_amount: number
    open_amount: number
    disputed_amount: number
    recovered_total: number
    potential_recovery: number
    recovery_rate: number | null
  }
  by_reason: Array<{ code: string; label: string; count: number; amount: number; recovered: number; typically_disputable: boolean }>
  by_retailer: Array<{ id: number; name: string; count: number; amount: number; recovered: number }>
  by_company: Array<{ id: number; name: string; count: number; amount: number; recovered: number; potential: number }>
}

const fmt = (n: number | null | undefined, currency = 'USD') => {
  if (n == null) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

const fmtDate = (d: string) => {
  try {
    return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return d
  }
}

function Badge({ status }: { status: string | null }) {
  if (!status) return null
  return <span className={`badge ${status}`}>{status.replace('_', ' ')}</span>
}

export default function App() {
  const [view, setView] = useState<'dashboard' | 'deductions'>('dashboard')
  const [companies, setCompanies] = useState<Company[]>([])
  const [retailers, setRetailers] = useState<Retailer[]>([])
  const [reasons, setReasons] = useState<Reason[]>([])
  const [companyId, setCompanyId] = useState<number | ''>('')
  const [deductions, setDeductions] = useState<Deduction[]>([])
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Deduction | null>(null)
  const [filters, setFilters] = useState({ status: '', reason_code: '', search: '', only_disputable: false })
  const [actionLoading, setActionLoading] = useState(false)
  const [notes, setNotes] = useState('')
  const [recoveredInput, setRecoveredInput] = useState('')

  // Load reference data
  useEffect(() => {
    Promise.all([
      fetch(`${API}/companies`).then(r => r.json()),
      fetch(`${API}/retailers`).then(r => r.json()),
      fetch(`${API}/reasons`).then(r => r.json()),
    ]).then(([c, r, rs]) => {
      setCompanies(c)
      setRetailers(r)
      setReasons(rs)
    }).catch(console.error)
  }, [])

  const loadDashboard = useCallback(() => {
    const q = companyId ? `?company_id=${companyId}` : ''
    fetch(`${API}/dashboard${q}`)
      .then(r => r.json())
      .then(setDashboard)
      .catch(console.error)
  }, [companyId])

  const loadDeductions = useCallback(() => {
    setLoading(true)
    const params = new URLSearchParams()
    if (companyId) params.set('company_id', String(companyId))
    if (filters.status) params.set('status', filters.status)
    if (filters.reason_code) params.set('reason_code', filters.reason_code)
    if (filters.search) params.set('search', filters.search)
    if (filters.only_disputable) params.set('only_disputable', 'true')
    fetch(`${API}/deductions?${params}`)
      .then(r => r.json())
      .then(data => {
        setDeductions(data)
        setLoading(false)
      })
      .catch(err => {
        console.error(err)
        setLoading(false)
      })
  }, [companyId, filters])

  useEffect(() => {
    if (view === 'dashboard') loadDashboard()
    else loadDeductions()
  }, [view, loadDashboard, loadDeductions])

  const openDetail = (d: Deduction) => {
    setSelected(d)
    setNotes(d.notes || '')
    setRecoveredInput(d.recovered_amount != null ? String(d.recovered_amount) : '')
  }

  const patchDeduction = async (id: string, body: Record<string, unknown>) => {
    setActionLoading(true)
    try {
      const res = await fetch(`${API}/deductions/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json()
        alert(err.detail || 'Update failed')
        return
      }
      const updated = await res.json()
      setSelected(updated)
      loadDeductions()
      loadDashboard()
    } catch (e) {
      console.error(e)
      alert('Network error')
    } finally {
      setActionLoading(false)
    }
  }

  const startDispute = () => {
    if (!selected) return
    patchDeduction(selected.id, { dispute_status: 'draft', notes })
  }

  const advanceDispute = (next: string) => {
    if (!selected) return
    const body: Record<string, unknown> = { dispute_status: next, notes }
    if (next === 'won' || next === 'partial') {
      const rec = parseFloat(recoveredInput)
      if (!isNaN(rec)) body.recovered_amount = rec
    }
    if (next === 'lost') body.recovered_amount = 0
    patchDeduction(selected.id, body)
  }

  const saveNotesAndRecovered = () => {
    if (!selected) return
    const body: Record<string, unknown> = { notes }
    const rec = parseFloat(recoveredInput)
    if (!isNaN(rec)) body.recovered_amount = rec
    patchDeduction(selected.id, body)
  }

  const acceptAsValid = () => {
    if (!selected) return
    patchDeduction(selected.id, { dispute_status: 'not_disputed', notes })
  }

  const maxAmount = dashboard?.summary
    ? Math.max(
        ...dashboard.by_reason.map(r => r.amount),
        ...dashboard.by_retailer.map(r => r.amount),
        1
      )
    : 1

  return (
    <div className="app">
      <header className="header">
        <h1>
          <span className="header-logo">DR</span>
          Deduction Recovery
        </h1>
        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <select
            className="company-select"
            value={companyId}
            onChange={e => setCompanyId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">All Companies</option>
            {companies.filter(c => c.is_active).map(c => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>
      </header>

      <div className="main">
        <aside className="sidebar">
          <nav>
            <button className={view === 'dashboard' ? 'active' : ''} onClick={() => setView('dashboard')}>
              📊 Dashboard
            </button>
            <button className={view === 'deductions' ? 'active' : ''} onClick={() => setView('deductions')}>
              📋 Deductions
            </button>
          </nav>
        </aside>

        <main className="content">
          {view === 'dashboard' && dashboard && (
            <>
              <div className="stats-grid">
                <div className="stat-card">
                  <div className="label">Total Deductions</div>
                  <div className="value">{dashboard.summary.total_deductions}</div>
                  <div className="sub">{fmt(dashboard.summary.total_amount)}</div>
                </div>
                <div className="stat-card">
                  <div className="label">Open</div>
                  <div className="value" style={{ color: '#60a5fa' }}>{dashboard.summary.open_count}</div>
                  <div className="sub">{fmt(dashboard.summary.open_amount)}</div>
                </div>
                <div className="stat-card">
                  <div className="label">In Dispute</div>
                  <div className="value" style={{ color: '#fbbf24' }}>{dashboard.summary.disputed_count}</div>
                  <div className="sub">{fmt(dashboard.summary.disputed_amount)} disputed</div>
                </div>
                <div className="stat-card highlight">
                  <div className="label">Potential Recovery</div>
                  <div className="value" style={{ color: '#34d399' }}>{fmt(dashboard.summary.potential_recovery)}</div>
                  <div className="sub">Disputable open + outstanding</div>
                </div>
                <div className="stat-card">
                  <div className="label">Recovered</div>
                  <div className="value" style={{ color: '#34d399' }}>{fmt(dashboard.summary.recovered_total)}</div>
                  <div className="sub">
                    {dashboard.summary.recovery_rate != null
                      ? `${dashboard.summary.recovery_rate}% of disputed`
                      : '—'}
                  </div>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
                <div className="table-wrap" style={{ padding: '1.25rem' }}>
                  <h3 style={{ marginBottom: '1rem', fontSize: '0.95rem' }}>By Reason</h3>
                  <div className="bar-list">
                    {dashboard.by_reason.map(r => (
                      <div className="bar-row" key={r.code}>
                        <span className="name" title={r.label}>{r.label}</span>
                        <div className="bar-track">
                          <div className="bar-fill" style={{ width: `${(r.amount / maxAmount) * 100}%` }} />
                        </div>
                        <span className="amt">{fmt(r.amount)}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="table-wrap" style={{ padding: '1.25rem' }}>
                  <h3 style={{ marginBottom: '1rem', fontSize: '0.95rem' }}>By Retailer</h3>
                  <div className="bar-list">
                    {dashboard.by_retailer.slice(0, 8).map(r => (
                      <div className="bar-row" key={r.id}>
                        <span className="name" title={r.name}>{r.name}</span>
                        <div className="bar-track">
                          <div className="bar-fill" style={{ width: `${(r.amount / maxAmount) * 100}%`, background: 'var(--info)' }} />
                        </div>
                        <span className="amt">{fmt(r.amount)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {!companyId && (
                <div className="table-wrap" style={{ padding: '1.25rem', marginTop: '1.5rem' }}>
                  <h3 style={{ marginBottom: '1rem', fontSize: '0.95rem' }}>By Company</h3>
                  <div className="bar-list">
                    {dashboard.by_company.map(c => (
                      <div className="bar-row" key={c.id}>
                        <span className="name">{c.name}</span>
                        <div className="bar-track">
                          <div className="bar-fill" style={{ width: `${(c.amount / maxAmount) * 100}%`, background: 'var(--warning)' }} />
                        </div>
                        <span className="amt">{fmt(c.amount)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {view === 'deductions' && (
            <>
              <div className="filters">
                <div className="field">
                  <label>Status</label>
                  <select value={filters.status} onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}>
                    <option value="">All</option>
                    <option value="open">Open</option>
                    <option value="disputed">Disputed</option>
                    <option value="resolved">Resolved</option>
                    <option value="closed">Closed</option>
                  </select>
                </div>
                <div className="field">
                  <label>Reason</label>
                  <select value={filters.reason_code} onChange={e => setFilters(f => ({ ...f, reason_code: e.target.value }))}>
                    <option value="">All</option>
                    {reasons.map(r => (
                      <option key={r.code} value={r.code}>{r.label}</option>
                    ))}
                  </select>
                </div>
                <div className="field" style={{ minWidth: 200 }}>
                  <label>Search</label>
                  <input
                    placeholder="Invoice, ID, notes..."
                    value={filters.search}
                    onChange={e => setFilters(f => ({ ...f, search: e.target.value }))}
                  />
                </div>
                <div className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.5rem', paddingTop: '1rem' }}>
                  <input
                    type="checkbox"
                    id="disputable"
                    checked={filters.only_disputable}
                    onChange={e => setFilters(f => ({ ...f, only_disputable: e.target.checked }))}
                    style={{ width: 'auto' }}
                  />
                  <label htmlFor="disputable" style={{ textTransform: 'none', fontSize: '0.85rem' }}>Only typically disputable</label>
                </div>
              </div>

              <div className="table-wrap">
                {loading ? (
                  <div className="loading">Loading deductions…</div>
                ) : deductions.length === 0 ? (
                  <div className="empty">No deductions match your filters.</div>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>Company</th>
                        <th>Retailer</th>
                        <th>Date</th>
                        <th>Amount</th>
                        <th>Reason</th>
                        <th>Status</th>
                        <th>Dispute</th>
                        <th>Recovered</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deductions.map(d => (
                        <tr key={d.id} className="clickable" onClick={() => openDetail(d)}>
                          <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{d.id}</td>
                          <td>{d.company.name}</td>
                          <td>{d.retailer.name}</td>
                          <td>{fmtDate(d.deduction_date)}</td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(d.amount, d.currency)}</td>
                          <td>
                            {d.reason.label}
                            {d.reason.typically_disputable && (
                              <span title="Typically disputable" style={{ marginLeft: 4, color: 'var(--success)' }}>●</span>
                            )}
                          </td>
                          <td><Badge status={d.status} /></td>
                          <td><Badge status={d.dispute_status} /></td>
                          <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                            {d.recovered_amount != null ? fmt(d.recovered_amount, d.currency) : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </>
          )}
        </main>
      </div>

      {selected && (
        <div className="overlay" onClick={e => e.target === e.currentTarget && setSelected(null)}>
          <div className="modal">
            <div className="modal-header">
              <h2>{selected.id} · {selected.invoice_number}</h2>
              <button className="ghost" onClick={() => setSelected(null)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="detail-grid">
                <div className="detail-item">
                  <label>Company</label>
                  <div className="val">{selected.company.name}</div>
                </div>
                <div className="detail-item">
                  <label>Retailer</label>
                  <div className="val">{selected.retailer.name} ({selected.retailer.type})</div>
                </div>
                <div className="detail-item">
                  <label>Amount</label>
                  <div className="val">{fmt(selected.amount, selected.currency)}</div>
                </div>
                <div className="detail-item">
                  <label>Date</label>
                  <div className="val">{fmtDate(selected.deduction_date)}</div>
                </div>
                <div className="detail-item">
                  <label>Reason</label>
                  <div className="val">
                    {selected.reason.label}
                    {selected.reason.typically_disputable
                      ? <span style={{ color: 'var(--success)', marginLeft: 6 }}>(typically disputable)</span>
                      : <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>(rarely worth disputing)</span>}
                  </div>
                </div>
                <div className="detail-item">
                  <label>Status</label>
                  <div className="val"><Badge status={selected.status} /> <Badge status={selected.dispute_status} /></div>
                </div>
              </div>

              <div className="detail-item" style={{ marginBottom: '1rem' }}>
                <label>Description</label>
                <div className="val">{selected.description}</div>
              </div>

              {(selected.disputed_amount != null || selected.recovered_amount != null) && (
                <div className="detail-grid">
                  <div className="detail-item">
                    <label>Disputed Amount</label>
                    <div className="val">{fmt(selected.disputed_amount, selected.currency)}</div>
                  </div>
                  <div className="detail-item">
                    <label>Recovered</label>
                    <div className="val" style={{ color: 'var(--success)' }}>
                      {fmt(selected.recovered_amount, selected.currency)}
                      {selected.recovery_rate != null && ` (${selected.recovery_rate}%)`}
                    </div>
                  </div>
                </div>
              )}

              <div className="section-title">Dispute Workflow</div>
              <div className="workflow">
                {['draft', 'submitted', 'in_review', 'won / partial / lost'].map((s, i) => {
                  const current = selected.dispute_status
                  const order = ['draft', 'submitted', 'in_review']
                  let cls = 'workflow-step'
                  if (current === 'won' || current === 'partial' || current === 'lost') {
                    if (i < 3) cls += ' done'
                    else cls += ' current'
                  } else if (current && order.indexOf(current) >= i) {
                    cls += i === order.indexOf(current) ? ' current' : ' done'
                  } else if (!current && i === 0) {
                    // nothing
                  }
                  return <span key={s} className={cls}>{s}</span>
                })}
              </div>

              <div className="section-title">Notes & Recovery</div>
              <textarea
                rows={3}
                value={notes}
                onChange={e => setNotes(e.target.value)}
                placeholder="Add notes about this dispute..."
                style={{ marginBottom: '0.75rem' }}
              />
              {(selected.status === 'disputed' || selected.dispute_status === 'partial' || selected.dispute_status === 'won') && (
                <div className="field" style={{ marginBottom: '0.75rem' }}>
                  <label style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Recovered Amount</label>
                  <input
                    type="number"
                    step="0.01"
                    value={recoveredInput}
                    onChange={e => setRecoveredInput(e.target.value)}
                    placeholder="0.00"
                    style={{ maxWidth: 160 }}
                  />
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="secondary" onClick={saveNotesAndRecovered} disabled={actionLoading}>
                Save Notes
              </button>

              {selected.status === 'open' && (
                <>
                  <button className="secondary" onClick={acceptAsValid} disabled={actionLoading}>
                    Accept (Not Dispute)
                  </button>
                  <button onClick={startDispute} disabled={actionLoading || !selected.reason.typically_disputable}>
                    Start Dispute
                  </button>
                </>
              )}

              {selected.dispute_status === 'draft' && (
                <button onClick={() => advanceDispute('submitted')} disabled={actionLoading}>
                  Mark Submitted
                </button>
              )}
              {selected.dispute_status === 'submitted' && (
                <button onClick={() => advanceDispute('in_review')} disabled={actionLoading}>
                  Mark In Review
                </button>
              )}
              {(selected.dispute_status === 'submitted' || selected.dispute_status === 'in_review') && (
                <>
                  <button className="success" onClick={() => advanceDispute('won')} disabled={actionLoading}>
                    Won (Full)
                  </button>
                  <button onClick={() => advanceDispute('partial')} disabled={actionLoading} style={{ background: 'var(--warning)' }}>
                    Partial Recovery
                  </button>
                  <button className="danger" onClick={() => advanceDispute('lost')} disabled={actionLoading}>
                    Lost
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
