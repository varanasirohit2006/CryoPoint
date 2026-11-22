import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, ArrowDownToLine, ArrowRight, ArrowUpRight, BarChart3, BookOpen, Boxes, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Clock3, Code2, Cpu, Download, ExternalLink, FileJson, FlaskConical, FolderClock, GitBranch, Layers3, LoaderCircle, Monitor, Play, Plus, Radio, RefreshCw, Search, ShieldCheck, Sparkles, Square, Terminal, TriangleAlert, WandSparkles, X } from 'lucide-react'
import { artifactUrl, exportUrl, findScreenshot, formatDuration, humanize, request } from './api'
import type { Health, Run, RunEvent, Site, Workflow, WorkflowVersion } from './types'

import TaskForm from './TaskForm'
import AutomationPanel, { RunTokens } from './AutomationPanel'

const activeStatuses = ['QUEUED', 'RUNNING', 'CANCELLING', 'AWAITING_CONFIRMATION']
const fallbackSites: Site[] = [
  { site_id: 'web', label: 'Any website', start_url: '', goals: [{ goal_id: 'browser_task', label: 'Browser task', parameters: {} }] },
  { site_id: 'shop', label: 'Local shopping store', start_url: 'http://127.0.0.1:3000/shop', goals: [{ goal_id: 'search_products', label: 'Search, filter, and sort products', parameters: { product_name: { type: 'string', required: true }, max_price: { type: 'number', required: true, default: 5000, minimum: 0 }, sort_by: { type: 'string', required: true, default: 'rating_desc', enum: ['rating_desc', 'price_asc'] } } }] },
  { site_id: 'admin', label: 'Local order portal', start_url: 'http://127.0.0.1:3000/admin', goals: [{ goal_id: 'ship_order', label: 'Find and ship an order', parameters: { order_id: { type: 'string', required: true } } }] },
  { site_id: 'amazon_in', label: 'Amazon India', start_url: 'https://www.amazon.in/', goals: [{ goal_id: 'search_products', label: 'Search products', parameters: { query: { type: 'string', required: true } } }] },
]
function App() {
  const [tab, setTab] = useState<'workbench' | 'library' | 'evaluation'>('workbench')
  const [health, setHealth] = useState<Health | null>(null)
  const [sites, setSites] = useState<Site[]>(fallbackSites)
  const [runs, setRuns] = useState<Run[]>([])
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [benchmarks, setBenchmarks] = useState<Record<string, unknown>[]>([])
  const [connectionError, setConnectionError] = useState('')
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const refreshPending = useRef<Promise<void> | null>(null)
  const fetchedAt = useRef<Record<string, number>>({})
  const tabRef = useRef(tab)
  const activeRef = useRef(false)
  tabRef.current = tab
  const refresh = useCallback((force = false): Promise<void> => {
    if (refreshPending.current) return refreshPending.current
    const stable = <T,>(previous: T, next: T) => JSON.stringify(previous) === JSON.stringify(next) ? previous : next
    const now = Date.now()
    const tasks: Promise<unknown>[] = []
    const due = (key: string, interval: number) => force || !fetchedAt.current[key] || now - fetchedAt.current[key] >= interval
    if (due('health', 10000)) {
      tasks.push(request<Health>(force ? '/health?refresh=true' : '/health').then(value => {
        fetchedAt.current.health = Date.now(); setHealth(previous => stable(previous, value)); setConnectionError('')
      }).catch(() => { setHealth(null); setConnectionError('The runtime is not reachable. Start the local services, then reconnect.') }))
    }
    if (due('sites', 60000)) tasks.push(request<Site[]>('/sites').then(value => {
      if (Array.isArray(value)) { fetchedAt.current.sites = Date.now(); setSites(previous => stable(previous, value)) }
    }))
    tasks.push(request<Run[]>('/runs').then(value => {
      if (Array.isArray(value)) {
        activeRef.current = value.some(run => activeStatuses.includes(run.status))
        setRuns(previous => stable(previous, value.sort((a, b) => b.created_at.localeCompare(a.created_at))))
      }
    }))
    if (due('workflows', 30000)) tasks.push(request<Workflow[]>('/workflows').then(value => {
      if (Array.isArray(value)) { fetchedAt.current.workflows = Date.now(); setWorkflows(previous => stable(previous, value)) }
    }))
    if (tabRef.current === 'evaluation' && due('benchmarks', 30000)) tasks.push(request<Record<string, unknown>[]>('/benchmarks').then(value => {
      if (Array.isArray(value)) { fetchedAt.current.benchmarks = Date.now(); setBenchmarks(previous => stable(previous, value)) }
    }))
    const pending = Promise.allSettled(tasks).then(() => {}).finally(() => { refreshPending.current = null })
    refreshPending.current = pending
    return pending
  }, [])
  useEffect(() => {
    let disposed = false, timer = 0
    const tick = async () => {
      if (!document.hidden) await refresh()
      if (!disposed) timer = window.setTimeout(tick, document.hidden ? 15000 : activeRef.current ? 2500 : 10000)
    }
    const wake = () => { if (!document.hidden) void refresh() }
    void tick(); document.addEventListener('visibilitychange', wake)
    return () => { disposed = true; window.clearTimeout(timer); document.removeEventListener('visibilitychange', wake) }
  }, [refresh])
  useEffect(() => { if (tab === 'evaluation' || tab === 'library') void refresh() }, [tab, refresh])
  const reconnect = async () => { setRefreshing(true); try { await refresh(true) } finally { setRefreshing(false) } }
  const onSubmitted = useCallback((run: Run) => {
    setSelectedRun(run.run_id); setRuns(previous => [run, ...previous.filter(item => item.run_id !== run.run_id)]); activeRef.current = true; void refresh(true)
  }, [refresh])
  const onRunChanged = useCallback(() => { void refresh(true) }, [refresh])
  const current = runs.find(run => run.run_id === selectedRun) ?? (!selectedRun ? runs[0] : null) ?? null
  const activeCount = runs.filter(run => activeStatuses.includes(run.status)).length
  return <div className="lab-app"><aside className="sidebar"><a className="brand" href="/" aria-label="JIT UI home"><span className="brand-icon"><Layers3 size={22}/></span><span>jit<span className="brand-divider">/</span>ui<small>THE AGENT LAB</small></span></a><div className="workspace-label"><span className="workspace-avatar">J</span><div>Personal workspace<small>Local environment</small></div><ChevronDown size={13}/></div><p className="nav-section">WORKSPACE</p><nav><button className={tab === 'workbench' ? 'selected' : ''} onClick={() => setTab('workbench')}><Terminal size={17}/><span>Workbench</span></button><button className={tab === 'library' ? 'selected' : ''} onClick={() => setTab('library')}><Boxes size={17}/><span>Workflow library</span><span className="nav-count">{workflows.length}</span></button><button className={tab === 'evaluation' ? 'selected' : ''} onClick={() => setTab('evaluation')}><BarChart3 size={17}/><span>Evaluation</span></button></nav><div className="sidebar-explainer"><span className="small-icon"><GitBranch size={17}/></span><h3>Learn. Compile. Reuse.</h3><p>Visual intelligence when needed.<br/>Deterministic execution after.</p><span className="version-chip">JIT RUNTIME · V0.1</span></div><div className="sidebar-footer"><span className={`status-dot ${health ? 'online' : ''}`}/><span>{health ? 'Runtime connected' : 'Runtime disconnected'}<small>127.0.0.1:8000</small></span><button title="Refresh connection" aria-label="Refresh connection" onClick={() => void reconnect()}><RefreshCw size={14} className={refreshing ? 'spinning' : ''}/></button></div></aside><div className="main-shell"><header className="topbar"><div className="breadcrumbs">Workspace <ChevronRight size={12}/><span>{tab === 'workbench' ? 'Workbench' : tab === 'library' ? 'Workflow library' : 'Evaluation'}</span></div><div className="topbar-right"><span className="local-tag"><Monitor size={12}/> LOCAL</span><span className="worker-status"><span className={`status-dot ${health?.worker_running ? 'online' : ''}`}/>{activeCount ? `${activeCount} active ${activeCount === 1 ? 'run' : 'runs'}` : 'Worker idle'}</span></div></header><main className="page-content">{connectionError && <div className="connection-banner" role="status"><TriangleAlert size={16}/>{connectionError}<button onClick={() => void reconnect()}>Reconnect <RefreshCw size={12}/></button></div>}{tab === 'workbench' ? <><div className="page-heading"><div><p className="eyebrow">YOUR AUTOMATION WORKSPACE</p><h1>From intent to execution<span>.</span></h1><p>Learn a workflow. Reuse it. Repair it when the interface changes.</p></div><span className="heading-label"><FlaskConical size={13}/> AGENT LAB</span></div><div className="workspace-grid"><TaskForm sites={sites} health={health} onSubmitted={onSubmitted}/><RunViewer run={current} onChanged={onRunChanged} onSubmitted={onSubmitted}/></div><div className="lower-grid"><RunHistory runs={runs} selected={current?.run_id} onSelect={setSelectedRun}/><ChaosControls/></div></> : tab === 'library' ? <WorkflowLibrary workflows={workflows} onChanged={onRunChanged}/> : <Evaluation runs={runs} reports={benchmarks}/>}<footer className="page-footer"><span><Circle size={5} fill="currentColor"/> Built for observable automation</span><span>Cold exploration → compilation → hot execution → scoped repair</span></footer></main></div></div>
}

function RunViewer({ run, onChanged, onSubmitted }: { run: Run | null; onChanged: () => void; onSubmitted: (run: Run) => void }) {
  const [events, setEvents] = useState<RunEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState('')
  const [retrying, setRetrying] = useState(false)
  const [expandedResult, setExpandedResult] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const runStatus = useRef(run?.status)
  runStatus.current = run?.status
  useEffect(() => {
    setNow(Date.now())
    if (!run || !activeStatuses.includes(run.status)) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [run?.run_id, run?.status])
  const elapsed = run && run.started_at && activeStatuses.includes(run.status)
    ? Math.max(run.metrics.duration_ms, now - Date.parse(run.started_at))
    : run?.metrics.duration_ms
  useEffect(() => {
    setEvents([]); setConnected(false); setCancelError(''); setExpandedResult(false)
    if (!run) return
    let disposed = false, lastSequence = 0, reconnectTimer = 0
    let socket: WebSocket | null = null
    const connect = () => {
      if (disposed) return
      socket = new WebSocket(`${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/runs/${run.run_id}/events?after_seq=${lastSequence}`)
      socket.onopen = () => { if (!disposed) setConnected(true) }
      socket.onmessage = event => { try { const item = JSON.parse(event.data) as RunEvent; if (item.sequence > lastSequence) { lastSequence = item.sequence; setEvents(current => [...current.slice(-159), item]); if (['completed', 'status', 'failed', 'cancelled', 'run_finished'].includes(item.kind)) onChanged() } } catch { /* Ignore frames that are not run events. */ } }
      socket.onclose = () => { if (!disposed) { setConnected(false); if (lastSequence === 0 || activeStatuses.includes(runStatus.current ?? '')) reconnectTimer = window.setTimeout(connect, 1800) } }
      socket.onerror = () => socket?.close()
    }
    connect()
    return () => { disposed = true; window.clearTimeout(reconnectTimer); socket?.close() }
  }, [run?.run_id])
  const eventScreenshot = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index--) {
      const path = findScreenshot(events[index].data)
      if (path) return path
    }
    return null
  }, [events])
  const screenshot = run?.pending_confirmation?.screenshot_path || run?.pending_confirmation?.screenshot || eventScreenshot
  const timeline = useMemo(() => events.filter(event => !['observation', 'screenshot'].includes(event.kind)).slice(-12), [events])
  const cancel = async () => { if (!run) return; setCancelling(true); setCancelError(''); try { await request(`/runs/${run.run_id}/cancel`, {}); onChanged() } catch (err) { setCancelError(err instanceof Error ? err.message : String(err)) } finally { setCancelling(false) } }
  const retryWithBrowser = async () => {
    if (!run || retrying) return
    setRetrying(true); setCancelError('')
    try {
      const next = await request<Run>('/runs', { ...run.request, interactive: true, mode: run.request.compile_workflow === false ? 'cold' : 'auto' })
      onSubmitted(next)
    } catch (err) { setCancelError(err instanceof Error ? err.message : String(err)) }
    finally { setRetrying(false) }
  }
  return <section className="panel run-panel"><div className="panel-heading"><div className="panel-title"><Radio size={15}/><h2>Live execution</h2></div><span className={`live-label ${run && connected ? 'connected' : ''}`}><span/>{run && connected ? 'CONNECTED' : 'OBSERVATIONS'}</span></div>{run && <RunTokens run={run}/>} {run?.status === 'AWAITING_CONFIRMATION' && <ConfirmationReview run={run} onChanged={onChanged}/>}<div className="run-header"><div><span className="run-id">{run ? `RUN / ${run.run_id.slice(0, 8)}` : 'READY WHEN YOU ARE'}</span><h3>{run ? runTitle(run) : 'Your next workflow starts here.'}</h3></div>{run ? <span className={`status-chip ${run.status.toLowerCase()}`} data-testid="run-status">{activeStatuses.includes(run.status) && <span className="status-dot online"/>}{humanize(run.status)}</span> : <span className="status-chip ready">Idle</span>}</div><div className="browser-window"><div className="browser-chrome"><div className="browser-dots"><i/><i/><i/></div><span>{run ? sitesLabel(run.request.site_id, run.request.target_url) : 'Browser observation'}</span>{screenshot && <a href={artifactUrl(screenshot)} target="_blank" rel="noreferrer" title="Open full screenshot"><ExternalLink size={12}/></a>}</div>{screenshot ? <a href={artifactUrl(screenshot)} target="_blank" rel="noreferrer" className="screenshot-link"><img src={artifactUrl(screenshot)} alt="Latest browser observation from this run"/></a> : <div className="browser-empty"><div className="browser-empty-icon"><Monitor size={28} strokeWidth={1.25}/><span><Sparkles size={11}/></span></div><h4>{run ? 'Waiting for the browser…' : 'See what the agent sees.'}</h4><p>{run ? 'Recorded observations will appear as the run progresses.' : 'Screenshots and execution events appear here when you start a task.'}</p><div className="empty-pipeline"><span>COLD</span><ArrowRight size={10}/><span>COMPILE</span><ArrowRight size={10}/><span>HOT</span></div></div>}</div><div className="run-metrics"><Metric label="Elapsed" value={formatDuration(elapsed)} icon={<Clock3 size={13}/>}/><Metric label="Model calls" value={run ? String(run.metrics.model_calls) : '—'} icon={<Cpu size={13}/>}/><Metric label="Actions" value={run ? String(run.metrics.actions) : '—'} icon={<Activity size={13}/>}/><Metric label="Repairs" value={run ? String(run.metrics.healed_steps) : '—'} icon={<WandSparkles size={13}/>}/></div><div className="timeline-heading"><span>EXECUTION TRACE</span>{run && <span className={`mode-chip ${run.mode.toLowerCase()}`}>{run.mode}</span>}</div><div className="timeline">{timeline.length ? timeline.slice(-12).map(event => <div className={`timeline-event ${event.kind.includes('fail') || event.kind === 'error' ? 'error' : ''}`} key={event.sequence}><span className="timeline-symbol">{event.kind.includes('heal') ? <WandSparkles size={12}/> : event.kind.includes('fail') || event.kind === 'error' ? <X size={12}/> : event.kind.includes('complete') || event.kind.includes('succeed') ? <Check size={12}/> : <Circle size={6} fill="currentColor"/>}</span><div><strong>{eventTitle(event)}</strong><p>{eventDescription(event)}</p></div><time>{new Date(event.timestamp).toLocaleTimeString('en-IN', { hour12: false })}</time></div>) : <div className="timeline-empty"><span/><span/><span/><p>{run ? 'Waiting for execution events.' : 'The trace will show how your workflow is learned and reused.'}</p></div>}</div>{run?.error && <div className="run-error"><TriangleAlert size={14}/><div><strong>{run.error_code ? humanize(run.error_code) : 'Run stopped'}</strong><p>{run.error}</p></div></div>}{run?.status === 'BLOCKED' && run.error_code === 'SITE_BLOCKED' && !run.request.interactive && <div className="run-actions"><button className="primary-button" type="button" disabled={retrying} onClick={() => void retryWithBrowser()}>{retrying ? <LoaderCircle size={13} className="spinning"/> : <Monitor size={13}/>} Retry with browser</button><p>Complete verification in the automation browser, then select Resume automation.</p></div>}{cancelError && <div className="inline-error">{cancelError}</div>}{run?.metrics.simulated && <div className="simulation-tag"><FlaskConical size={12}/> Offline simulation · not an AI benchmark</div>}{run?.result && <div className="result-summary"><button onClick={() => setExpandedResult(!expandedResult)}><CheckCircle2 size={14}/><span>Verified result</span><ChevronDown size={13}/></button>{expandedResult && <pre>{JSON.stringify(run.result, null, 2)}</pre>}{run.workflow_id && <a href={exportUrl(run.workflow_id, run.workflow_version ?? 1, 'python')}><Download size={12}/> Compiled script</a>}</div>}{run && activeStatuses.includes(run.status) && run.status !== 'AWAITING_CONFIRMATION' && <div className="run-actions"><button className="secondary-button" disabled={cancelling || run.status === 'CANCELLING'} onClick={() => void cancel()}><Square size={11}/>{cancelling || run.status === 'CANCELLING' ? 'Cancelling…' : 'Cancel run'}</button></div>}</section>
}

function Metric({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) { return <div className="metric"><span>{icon}{label}</span><strong>{value}</strong></div> }
function sitesLabel(site: string, targetUrl?: string | null) {
  if (targetUrl) { try { return new URL(targetUrl).hostname } catch { return targetUrl } }
  return site === 'shop' ? '127.0.0.1:3000/shop' : site === 'admin' ? '127.0.0.1:3000/admin' : site === 'amazon_in' ? 'amazon.in' : 'Any website'
}
function runTitle(run: Run) {
  if (run.request.site_id === 'admin') return `Order #${run.request.parameters.order_id}`
  if (run.request.parameters.language) return `Change language to ${String(run.request.parameters.language)}`
  return String(run.request.parameters.product_name ?? run.request.parameters.query ?? run.request.task ?? 'Browser workflow')
}

function ConfirmationReview({ run, onChanged }: { run: Run; onChanged: () => void }) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const pending = run.pending_confirmation
  const confirm = async (approve: boolean) => {
    setSubmitting(true); setError('')
    try { await request(`/runs/${run.run_id}/confirmation`, { approve }); onChanged() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setSubmitting(false) }
  }
  if (!pending) return <div className="confirmation-review"><strong>Preparing final action review…</strong></div>
  return <section className="confirmation-review" aria-label={pending.manual ? 'Manual browser step' : 'Final action review'}>
    <div className="confirmation-heading"><ShieldCheck size={17}/><div><strong>{pending.label || (pending.manual ? 'Complete the browser step' : 'Review final action')}</strong><p>{pending.manual ? 'Complete the required step in the browser window, then resume.' : 'The browser is paused before this action.'}</p></div></div>
    {pending.url && <p className="confirmation-url">{pending.url}</p>}
    {pending.details && (typeof pending.details === 'string' ? <p>{pending.details}</p> : <dl>{Object.entries(pending.details).map(([key, value]) => <div key={key}><dt>{humanize(key)}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>)}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="confirmation-actions"><button className="primary-button" type="button" disabled={submitting} onClick={() => void confirm(true)}>{submitting ? <LoaderCircle size={13} className="spinning"/> : <Check size={13}/>} {pending.manual ? 'Resume automation' : 'Approve final action'}</button><button className="secondary-button" type="button" disabled={submitting} onClick={() => void confirm(false)}>Cancel</button></div>
  </section>
}
function eventTitle(event: RunEvent) {
  if (event.kind === 'provider_request') return event.data.phase === 'intent' ? 'Understanding the complete task' : 'LLM deciding the next action'
  if (event.kind === 'task_interpreted') return event.data.cached ? 'Using the saved task interpretation' : 'Task understood'
  if (typeof event.data.intent === 'string') return event.data.intent
  if (typeof event.data.message === 'string') return event.data.message
  const frame = event.data.frame as { action?: { intent?: string; action?: string }; element?: { name?: string; label?: string } } | undefined
  if (frame?.action) {
    if (frame.action.intent && frame.action.intent !== frame.action.action) return frame.action.intent
    const target = frame.element?.label || frame.element?.name
    return `${humanize(frame.action.action || event.kind)}${target ? ` · ${target}` : ''}`
  }
  return humanize(event.kind)
}
function eventDescription(event: RunEvent) {
  if (event.kind === 'task_interpreted') {
    const query = typeof event.data.query === 'string' ? `Search: ${event.data.query}. ` : ''
    const objectives = Array.isArray(event.data.objectives) ? event.data.objectives.filter((item): item is string => typeof item === 'string').join(' · ') : ''
    return query + objectives
  }
  const frame = event.data.frame as { error?: string; action?: { action?: string } } | undefined
  const value = event.data.error ?? frame?.error ?? event.data.mode ?? event.data.status ?? event.data.action ?? frame?.action ?? event.data.step_id ?? event.data.model
  return typeof value === 'string' ? value : typeof value === 'object' && value ? humanize(String((value as Record<string, unknown>).action ?? '')) : ''
}

function RunHistory({ runs, selected, onSelect }: { runs: Run[]; selected?: string; onSelect: (id: string) => void }) {
  return <section className="panel history-panel"><div className="panel-heading"><div className="panel-title"><FolderClock size={15}/><h2>Recent runs</h2></div><span className="panel-kicker">{runs.length} TOTAL</span></div>{runs.length ? <div className="history-list">{runs.slice(0, 7).map(run => <button className={`history-row ${selected === run.run_id ? 'selected' : ''}`} key={run.run_id} onClick={() => onSelect(run.run_id)}><span className={`history-icon ${run.status.toLowerCase()}`}>{run.status === 'SUCCEEDED' ? <Check size={13}/> : activeStatuses.includes(run.status) ? <LoaderCircle size={13} className="spinning"/> : <Circle size={8}/>}</span><span className="history-description"><strong>{runTitle(run)}</strong><small>{sitesLabel(run.request.site_id, run.request.target_url)} · {run.metrics.simulated ? 'Simulation' : humanize(run.mode)}</small></span><span className="history-duration">{formatDuration(run.metrics.duration_ms)}</span><ChevronRight size={12}/></button>)}</div> : <div className="small-empty"><FolderClock size={22}/><p>Your runs will appear here.</p><span>Every execution leaves an inspectable record.</span></div>}</section>
}

function ChaosControls() {
  const [site, setSite] = useState('shop')
  const [mutation, setMutation] = useState('none')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  async function change(reset = false) {
    setBusy(true); setMessage(''); setError('')
    try {
      await request(`/testbed/${reset ? 'reset' : 'mutations'}`, reset ? { seed: 42 } : { site_id: site, mutation, seed: 7 })
      if (reset) setMutation('none')
      setMessage(reset ? 'Original fixtures restored.' : `${humanize(mutation)} applied. Run a learned workflow to test it.`)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setBusy(false) }
  }
  return <section className="panel chaos-panel"><div className="panel-heading"><div className="panel-title"><FlaskConical size={15}/><h2>Chaos controls</h2></div><span className="panel-kicker">LOCAL ONLY</span></div><p className="chaos-intro">Change the interface.<br/><span>Keep the task the same.</span></p><div className="chaos-form"><select aria-label="Test application" value={site} onChange={event => setSite(event.target.value)}><option value="shop">Shopping store</option><option value="admin">Order portal</option></select><select aria-label="Mutation scenario" value={mutation} onChange={event => setMutation(event.target.value)}><option value="none">Original interface</option><option value="selector_drift">Selector drift</option><option value="spatial_shift">Spatial shift</option><option value="copy_mutation">Copy mutation</option><option value="dom_nesting">DOM nesting</option><option value="obstacle">Blocking popup</option><option value="compound">Combined redesign</option></select><div className="chaos-buttons"><button className="secondary-button" disabled={busy} onClick={() => void change()}><WandSparkles size={12}/> Apply mutation</button><button className="reset-button" disabled={busy} aria-label="Reset all fixtures" title="Reset all fixtures" onClick={() => void change(true)}><RefreshCw size={14}/></button></div></div>{message && <p className="chaos-message"><Check size={12}/>{message}</p>}{error && <p className="inline-error">{error}</p>}</section>
}

function WorkflowLibrary({ workflows, onChanged }: { workflows: Workflow[]; onChanged: () => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [versions, setVersions] = useState<WorkflowVersion[]>([])
  const [version, setVersion] = useState(1)
  const [format, setFormat] = useState<'python' | 'ir' | 'diff'>('python')
  const [content, setContent] = useState('')
  const [before, setBefore] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const workflow = workflows.find(item => item.workflow_id === selected) ?? workflows[0]
  useEffect(() => {
    if (!workflow) return
    let active = true
    setVersion(workflow.version); setVersions([]); setContent(''); setError('')
    void request<(WorkflowVersion | number)[]>(`/workflows/${workflow.workflow_id}/versions`).then(data => { if (active) setVersions(data.map(item => typeof item === 'number' ? { version: item } : item).sort((a, b) => b.version - a.version)) }).catch(err => { if (active) setError(String(err)) })
    return () => { active = false }
  }, [workflow?.workflow_id, workflow?.version])
  useEffect(() => {
    if (!workflow) return
    let active = true
    setLoading(true); setError(''); setBefore('')
    const selectedFormat = format === 'diff' ? 'python' : format
    const earlier = versions.find(item => item.version < version)?.version
    void Promise.all([fetch(exportUrl(workflow.workflow_id, version, selectedFormat)).then(async response => { if (!response.ok) throw new Error('Artifact could not be loaded.'); return response.text() }), format === 'diff' && earlier ? fetch(exportUrl(workflow.workflow_id, earlier, 'python')).then(async response => { if (!response.ok) throw new Error('Previous version could not be loaded.'); return response.text() }) : Promise.resolve('')]).then(([current, previous]) => { if (active) { setContent(current); setBefore(previous) } }).catch(err => { if (active) setError(String(err)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [workflow?.workflow_id, version, format, versions])
  const activate = async () => { if (!workflow) return; try { await request(`/workflows/${workflow.workflow_id}/activate`, { version }); onChanged() } catch (err) { setError(String(err)) } }
  return <><div className="page-heading"><div><p className="eyebrow">COMPILED KNOWLEDGE</p><h1>A library that learns<span>.</span></h1><p>Inspect the workflows your agent has discovered and improved.</p></div><span className="heading-label"><Boxes size={13}/>{workflows.length} WORKFLOWS</span></div>{!workflows.length ? <div className="panel library-empty"><div className="browser-empty-icon"><Boxes size={29} strokeWidth={1.25}/></div><h2>No compiled workflows yet.</h2><p>Complete a learning run in the workbench to create your first reusable workflow.</p><div className="empty-pipeline"><span>TRACE</span><ArrowRight size={10}/><span>IR</span><ArrowRight size={10}/><span>PYTHON</span></div></div> : <div className="library-grid"><section className="panel workflow-list"><div className="panel-heading"><div className="panel-title"><Boxes size={14}/><h2>Workflows</h2></div></div>{workflows.map(item => <button className={`workflow-item ${workflow?.workflow_id === item.workflow_id ? 'selected' : ''}`} key={item.workflow_id} onClick={() => setSelected(item.workflow_id)}><span className="workflow-item-icon"><GitBranch size={17}/></span><span><strong>{item.intent_signature}</strong><small>{humanize(item.site_id)} · {item.step_count ?? item.steps?.length ?? '—'} steps{item.metadata?.simulated ? ' · Simulation' : ''}</small><code>{item.workflow_id.slice(0, 12)}</code></span><span className="workflow-version">v{item.version}</span></button>)}</section><section className="panel artifact-panel"><div className="panel-heading"><div className="panel-title"><Code2 size={15}/><h2>Compiled artifact</h2></div><div className="version-selector"><GitBranch size={12}/><select aria-label="Workflow version" value={version} onChange={event => setVersion(Number(event.target.value))}>{versions.map(item => <option key={item.version} value={item.version}>Version {item.version}{item.version === workflow?.version ? ' · active' : ''}</option>)}</select></div></div><div className="artifact-toolbar"><div className="artifact-tabs">{(['python', 'ir', 'diff'] as const).map(value => <button className={format === value ? 'selected' : ''} key={value} onClick={() => setFormat(value)}>{value === 'python' ? <Code2 size={13}/> : value === 'ir' ? <FileJson size={13}/> : <GitBranch size={13}/>} {value === 'python' ? 'Python' : value === 'ir' ? 'Workflow IR' : 'Repair diff'}</button>)}</div><a className="artifact-download" href={exportUrl(workflow!.workflow_id, version, format === 'ir' ? 'ir' : 'python')} title="Download artifact"><Download size={14}/></a></div>{error && <div className="inline-error">{error}</div>}{loading ? <div className="code-loading"><LoaderCircle size={20} className="spinning"/><span>Loading artifact…</span></div> : format === 'diff' ? <CodeDiff before={before} after={content}/> : <pre className="code-view"><code>{content}</code></pre>}<div className="artifact-footer"><span><ShieldCheck size={12}/> Versioned IR is the source of truth.</span>{version !== workflow?.version && <button className="secondary-button" onClick={() => void activate()}><RefreshCw size={11}/> Activate v{version}</button>}</div></section></div>}</>
}

function CodeDiff({ before, after }: { before: string; after: string }) {
  if (!before) return <div className="diff-empty"><GitBranch size={24}/><p>No earlier version to compare.</p><span>Repair diffs appear after a workflow is patched.</span></div>
  const oldLines = before.split('\n'), newLines = after.split('\n')
  let prefix = 0, suffix = 0
  while (prefix < Math.min(oldLines.length, newLines.length) && oldLines[prefix] === newLines[prefix]) prefix++
  while (suffix < Math.min(oldLines.length, newLines.length) - prefix && oldLines[oldLines.length - suffix - 1] === newLines[newLines.length - suffix - 1]) suffix++
  const rows = [...oldLines.slice(0, prefix).map(text => ({ text, kind: 'same' })), ...oldLines.slice(prefix, oldLines.length - suffix).map(text => ({ text, kind: 'removed' })), ...newLines.slice(prefix, newLines.length - suffix).map(text => ({ text, kind: 'added' })), ...newLines.slice(newLines.length - suffix).map(text => ({ text, kind: 'same' }))]
  return <div className="code-diff">{rows.map((row, index) => <div className={`diff-line ${row.kind}`} key={index}><span>{row.kind === 'added' ? '+' : row.kind === 'removed' ? '−' : ' '}</span><code>{row.text || ' '}</code></div>)}</div>
}

function Evaluation({ runs, reports }: { runs: Run[]; reports: Record<string, unknown>[] }) {
  const [selected, setSelected] = useState<number | null>(null)
  const realRuns = runs.filter(run => !run.metrics.simulated && run.status === 'SUCCEEDED')
  const cold = realRuns.filter(run => run.mode === 'COLD' || run.mode === 'COMPILING')
  const hot = realRuns.filter(run => run.mode === 'HOT')
  const mean = (items: Run[]) => items.length ? items.reduce((total, item) => total + item.metrics.duration_ms, 0) / items.length : 0
  const maxDuration = Math.max(...realRuns.slice(0, 8).map(run => run.metrics.duration_ms), 1)
  return <><div className="page-heading"><div><p className="eyebrow">MEASURED, NOT ASSUMED</p><h1>Proof in the execution<span>.</span></h1><p>Compare real runs and inspect reproducible evaluation reports.</p></div><span className="heading-label"><BarChart3 size={13}/> EVALUATION</span></div><div className="evaluation-metrics"><Metric label="Real successful runs" value={String(realRuns.length)} icon={<CheckCircle2 size={14}/>}/><Metric label="Mean cold duration" value={formatDuration(mean(cold))} icon={<Sparkles size={14}/>}/><Metric label="Mean hot duration" value={formatDuration(mean(hot))} icon={<Activity size={14}/>}/><Metric label="Simulation runs" value={String(runs.filter(run => run.metrics.simulated).length)} icon={<FlaskConical size={14}/>}/></div><AutomationPanel runs={runs}/><div className="evaluation-grid"><section className="panel chart-panel"><div className="panel-heading"><div className="panel-title"><BarChart3 size={15}/><h2>Recent execution times</h2></div><span className="panel-kicker">REAL RUNS ONLY</span></div>{realRuns.length ? <div className="bar-chart">{realRuns.slice(0, 8).reverse().map(run => <div className="chart-row" key={run.run_id}><span>{run.run_id.slice(0, 6)}<small>{run.mode}</small></span><div className="chart-track"><div className={`chart-bar ${run.mode.toLowerCase()}`} style={{ width: `${Math.max(2, run.metrics.duration_ms / maxDuration * 100)}%` }}/></div><strong>{formatDuration(run.metrics.duration_ms)}</strong></div>)}</div> : <div className="small-empty"><BarChart3 size={25}/><p>No real-model measurements yet.</p><span>Simulation runs are excluded from AI comparisons.</span></div>}<p className="chart-note">These are descriptive run measurements. Use a controlled benchmark report for comparisons across matching tasks.</p></section><section className="panel reports-panel"><div className="panel-heading"><div className="panel-title"><BookOpen size={15}/><h2>Benchmark reports</h2></div><span className="panel-kicker">{reports.length} REPORTS</span></div>{reports.length ? reports.map((report, index) => <button className="report-row" key={index} onClick={() => setSelected(selected === index ? null : index)}><FileJson size={17}/><span><strong>{String(report.name ?? report.suite_id ?? report.title ?? `Evaluation ${index + 1}`)}</strong><small>{String(report.created_at ?? report.provider ?? 'Recorded evaluation')}</small></span><ChevronRight size={13}/></button>) : <div className="small-empty"><FileJson size={23}/><p>Your evidence belongs here.</p><span>Reports appear after an evaluation suite finishes.</span></div>}{selected !== null && reports[selected] && <pre className="report-json">{JSON.stringify(reports[selected], null, 2)}</pre>}</section></div></>
}

export default App
