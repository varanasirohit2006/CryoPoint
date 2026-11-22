import { useEffect, useState } from 'react'
import { request } from './api'
import type { Automation, AutomationReport, Run } from './types'

const count = (value?: number | null) => value == null ? '—' : value.toLocaleString()
export function RunTokens({ run }: { run: Run }) {
  const usage = run.metrics
  const strategy = usage.simulated ? 'Simulation' : usage.execution_strategy === 'native'
    ? `${run.request.site_id === 'amazon_in' ? 'Native Amazon search' : 'Native browser action'}${usage.model_calls === 0 ? ' · no model calls' : ''}`
    : usage.execution_strategy === 'replay' || run.mode === 'HOT' ? 'Compiled replay · cached workflow' : 'Model exploration'
  const savingPercent = usage.estimated_tokens_saved != null && usage.baseline_tokens != null && usage.baseline_tokens > 0
    ? usage.estimated_tokens_saved / usage.baseline_tokens * 100 : null
  return <div className="run-token-strip">
    <span>Execution <strong>{strategy}</strong></span>
    <span>Input <strong>{count(usage.input_tokens)}</strong></span><span>Output <strong>{count(usage.output_tokens)}</strong></span>
    <span>Total tracked <strong>{count(usage.input_tokens + usage.output_tokens)}</strong></span>
    <span>Model calls <strong>{count(usage.model_calls)}</strong></span>
    <span>Estimated saved <strong>{count(usage.estimated_tokens_saved)}{savingPercent != null ? ` (${savingPercent.toFixed(1)}%)` : ''}</strong></span>
    <small>{usage.simulated ? 'Simulation; excluded from savings.' : !usage.token_usage_complete ? 'Partial usage: some provider attempts did not return token counts.' : usage.baseline_run_id ? `Learning reference ${usage.baseline_run_id.slice(0, 8)} · ${usage.baseline_model} · ${count(usage.baseline_tokens)} tokens` : 'No learning baseline. Token usage is tracked; savings are not estimated.'}</small>
    {run.mode === 'HOT' && run.workflow_id && <small>Cached workflow {run.workflow_id.slice(0, 8)} · version {run.workflow_version ?? 1}</small>}
  </div>
}
export default function AutomationPanel({ runs }: { runs: Run[] }) {
  const [sets, setSets] = useState<Automation[]>([])
  const [selected, setSelected] = useState('')
  const [report, setReport] = useState<AutomationReport | null>(null)
  const [error, setError] = useState('')
  const revision = runs.map(run => `${run.run_id}:${run.status}:${run.metrics.model_calls}`).join('|')
  useEffect(() => { let active = true; request<Automation[]>('/automations').then(items => { if (active) { setSets(items); setSelected(current => current || items[0]?.automation_id || '') } }).catch(err => { if (active) setError(String(err)) }); return () => { active = false } }, [revision])
  useEffect(() => { let active = true; setReport(null); if (selected) request<AutomationReport>(`/automations/${selected}`).then(item => { if (active) { setReport(item); setError('') } }).catch(err => { if (active) setError(String(err)) }); return () => { active = false } }, [selected, revision])
  const summary = report?.summary
  return <section className="panel automation-panel">
    <div className="panel-heading"><div className="panel-title"><h2>Tokens by automation set</h2></div><span className="panel-kicker">PERSISTENT LEDGER</span></div>
    <div className="automation-content"><label className="field-label" htmlFor="ledger-set">Automation set</label><select id="ledger-set" value={selected} onChange={event => setSelected(event.target.value)}>{sets.length ? sets.map(item => <option key={item.automation_id} value={item.automation_id}>{item.name} · {item.run_count} runs</option>) : <option value="">Run an automation to start tracking</option>}</select>
      {error && <p className="inline-error">{error}</p>}
      {summary && <><div className="ledger-totals"><div><span>Tracked tokens</span><strong>{count(summary.total_tokens)}</strong><small>{count(summary.input_tokens)} input · {count(summary.output_tokens)} output</small></div><div><span>Estimated tokens saved</span><strong>{count(summary.estimated_tokens_saved)}</strong><small>{summary.estimated_savings_percent == null ? 'Learning reference unavailable' : `${summary.estimated_savings_percent.toFixed(1)}% versus learning reference`}</small></div><div><span>Runs / model calls</span><strong>{summary.run_count} / {summary.model_calls}</strong><small>{summary.comparable_runs} comparable · {summary.incomplete_usage_runs} partial usage</small></div></div>
        <div className="ledger-downloads"><a href={`/api/automations/${selected}/export?format=csv`} download>Download CSV</a><a href={`/api/automations/${selected}/export?format=json`} download>Download JSON</a></div>
        <div className="ledger-table-wrap"><table className="ledger-table"><thead><tr><th>Run / task</th><th>Mode / status</th><th>Model calls</th><th>Input</th><th>Output</th><th>Estimated saved</th></tr></thead><tbody>{report.runs.slice().reverse().map(run => <tr key={run.run_id}><td><code>{run.run_id.slice(0, 8)}</code><small title={run.request.task}>{run.request.task}</small></td><td>{run.mode}<small>{run.status}</small></td><td>{run.metrics.model_calls}</td><td>{count(run.metrics.input_tokens)}</td><td>{count(run.metrics.output_tokens)}</td><td>{count(run.metrics.estimated_tokens_saved)}<small>{!run.metrics.token_usage_complete ? 'Partial usage' : run.metrics.baseline_run_id ? `Ref ${run.metrics.baseline_run_id.slice(0, 8)}` : 'No reference'}</small></td></tr>)}</tbody></table></div><p className="chart-note">{report.methodology}</p></>}
    </div>
  </section>
}
