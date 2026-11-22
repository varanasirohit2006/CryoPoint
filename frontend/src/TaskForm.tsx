import { memo, useEffect, useRef, useState } from 'react'
import { ChevronDown, Cpu, FlaskConical, LoaderCircle, Monitor, Play, Plus, ShieldCheck, Sparkles, Square, TriangleAlert } from 'lucide-react'
import { humanize, request } from './api'
import type { Automation, Health, ProviderName, Run, Site, TaskRequest } from './types'

const examples: Record<string, { task: string; parameters: Record<string, string> }> = {
  web: { task: 'Go to Amazon and change the language to Hindi.', parameters: {} },
  shop: { task: 'Search for Sony headphones under 5000 and sort by rating.', parameters: { product_name: 'Sony headphones', max_price: '5000', sort_by: 'rating_desc' } },
  admin: { task: 'Find order 1042 and mark it as shipped.', parameters: { order_id: '1042' } },
  amazon_in: { task: 'Go to Amazon and search for 5 years girl toys which are below 500 rupees.', parameters: {} },
}
const labels: Record<string, string> = { product_name: 'Product to find', query: 'Search keywords', language: 'Language', min_price: 'Minimum price · ₹', max_price: 'Maximum price · ₹', sort_by: 'Sort results', min_rating: 'Minimum stars', order_id: 'Order ID' }
const sortLabels: Record<string, string> = { rating_desc: 'Highest rated', price_asc: 'Price: low to high', 'price-asc-rank': 'Price: low to high', 'price-desc-rank': 'Price: high to low', 'review-rank': 'Customer reviews', 'date-desc-rank': 'Newest arrivals', relevanceblender: 'Featured' }
const providers: Record<ProviderName, string> = { gemini: 'Gemini · cloud vision', ollama: 'Ollama · local vision', fixture: 'Offline simulation · local tests' }

function TaskForm({ sites, health, onSubmitted }: { sites: Site[]; health: Health | null; onSubmitted: (run: Run) => void }) {
  const [siteId, setSiteId] = useState('web')
  const [task, setTask] = useState(examples.web.task)
  const [values, setValues] = useState<Record<string, string>>({})
  const [targetUrl, setTargetUrl] = useState('')
  const [interactive, setInteractive] = useState(() => {
    try { return localStorage.getItem('jit-ui.manual-browser') === 'true' } catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('jit-ui.manual-browser', String(interactive)) } catch { /* Storage may be disabled. */ }
  }, [interactive])
  const [manual, setManual] = useState(false)
  const [preview, setPreview] = useState<TaskRequest | null>(null)
  const [previewError, setPreviewError] = useState('')
  const [provider, setProvider] = useState<ProviderName>('gemini')
  const [mode, setMode] = useState<TaskRequest['mode']>('auto')
  const [automations, setAutomations] = useState<Automation[]>([])
  const [automationId, setAutomationId] = useState('')
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const providerTouched = useRef(false)
  const availableSites: Site[] = sites.some(item => item.site_id === 'web') ? sites : [
    { site_id: 'web', label: 'Any website', start_url: '', goals: [{ goal_id: 'browser_task', label: 'Browser task', parameters: {} }] }, ...sites]
  const site = availableSites.find(item => item.site_id === siteId) ?? availableSites[0]
  const goal = site?.goals[0]
  const natural = siteId === 'web' || (siteId === 'amazon_in' && !manual)
  const resolvedSite = preview?.site_id ?? siteId
  const resolvedGoal = preview?.goal_id ?? goal?.goal_id ?? 'browser_task'
  const resolvedUrl = preview?.target_url ?? (targetUrl.trim() || site?.start_url)
  useEffect(() => {
    let active = true
    let timer = 0
    const refresh = async () => {
      if (!document.hidden) await request<Automation[]>('/automations').then(items => {
        if (active) setAutomations(previous => JSON.stringify(previous) === JSON.stringify(items) ? previous : items)
      }).catch(() => {})
      if (active) timer = window.setTimeout(refresh, 15000)
    }
    void refresh()
    return () => { active = false; window.clearTimeout(timer) }
  }, [])
  useEffect(() => { if (!providerTouched.current && health) { if (health.providers.gemini?.configured) setProvider('gemini'); else if (health.providers.ollama?.installed) setProvider('ollama') } }, [health])
  useEffect(() => {
    setPreviewError('')
    if (!natural || !goal || !task.trim()) { setPreview(null); return }
    let active = true
    const timer = window.setTimeout(() => {
      request<TaskRequest>('/tasks/preview', { site_id: siteId, goal_id: goal.goal_id, task, target_url: siteId === 'web' ? targetUrl.trim() || undefined : undefined, parameters: {}, parameter_source: 'task', provider, mode })
        .then(result => { if (active) setPreview(result) }).catch(err => { if (active) setPreviewError(String(err)) })
    }, 350)
    return () => { active = false; window.clearTimeout(timer) }
  }, [natural, siteId, goal?.goal_id, task, targetUrl, provider, mode])
  useEffect(() => { setAutomationId('') }, [resolvedSite, resolvedGoal])
  const available = (name: ProviderName) => name === 'fixture' ? siteId === 'shop' || siteId === 'admin' : !!health?.providers[name]?.configured && (name !== 'ollama' || health.providers.ollama.installed !== false)
  const chooseSite = (id: string) => {
    const example = examples[id] ?? examples.web
    setSiteId(id); setTask(example.task); setValues(example.parameters); setTargetUrl(''); setInteractive(false); setManual(false); setPreview(null); setAutomationId(''); setError('')
    if ((id === 'amazon_in' || id === 'web') && provider === 'fixture') setProvider(health?.providers.gemini?.configured ? 'gemini' : 'ollama')
  }
  const createSet = async () => {
    if (!goal || !newName.trim()) return
    setCreating(true); setError('')
    try { const item = await request<Automation>('/automations', { name: newName.trim(), site_id: resolvedSite, goal_id: resolvedGoal, ...(resolvedSite === 'web' && resolvedUrl ? { target_url: resolvedUrl } : {}) }); setAutomations(current => [...current, item]); setAutomationId(item.automation_id); setNewName('') } catch (err) { setError(String(err)) } finally { setCreating(false) }
  }
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError('')
    if (!site || !goal) return
    const parameters: Record<string, unknown> = {}
    if (!natural) for (const [key, spec] of Object.entries(goal.parameters)) {
      const value = values[key] ?? (spec.default == null ? '' : String(spec.default))
      if (!value.trim()) { if (spec.required) { setError(`Enter ${labels[key] || key}.`); return } continue }
      if (spec.type === 'number' || spec.type === 'integer') {
        const parsed = Number(value)
        if (!Number.isFinite(parsed) || (spec.type === 'integer' && !Number.isInteger(parsed)) || (spec.minimum != null && parsed < spec.minimum) || (spec.maximum != null && parsed > spec.maximum)) { setError(`Enter a valid ${labels[key] || key}.`); return }
        parameters[key] = parsed
      } else parameters[key] = value
    }
    setSubmitting(true)
    try { onSubmitted(await request<Run>('/runs', { site_id: siteId, goal_id: goal.goal_id, task: task.trim(), target_url: siteId === 'web' ? targetUrl.trim() || undefined : undefined, interactive, parameters, parameter_source: natural ? 'task' : 'explicit', automation_id: automationId || null, provider, mode })) } catch (err) { setError(err instanceof Error ? err.message : String(err)) } finally { setSubmitting(false) }
  }
  return <section className="panel task-panel">
    <div className="panel-heading"><div className="panel-title"><Square size={15}/><h2>New task</h2></div><span className="panel-kicker">01 / INPUT</span></div>
    <form onSubmit={event => void submit(event)}>
      <label className="field-label" htmlFor="site-select">Target website</label>
      <div className="select-wrap"><Monitor size={15}/><select id="site-select" data-testid="site-select" value={siteId} onChange={event => chooseSite(event.target.value)}>{availableSites.map(item => <option key={item.site_id} value={item.site_id}>{item.label}</option>)}</select><ChevronDown size={13}/></div>
      {siteId === 'web' && <><label className="field-label" htmlFor="target-url">Website URL <span className="optional-field">optional</span></label><input id="target-url" data-testid="target-url" type="text" inputMode="url" value={targetUrl} onChange={event => setTargetUrl(event.target.value)} placeholder="https://example.com"/><p className="form-footnote">Leave blank when the task names the website.</p></>}
      {siteId === 'web' && <label className="manual-toggle"><input type="checkbox" checked={interactive} onChange={event => setInteractive(event.target.checked)}/> Show browser for manual steps</label>}
      <label className="field-label task-label" htmlFor="task-input">What should the agent do?<button type="button" className="example-button" onClick={() => { const example = examples[siteId] ?? examples.web; setTask(example.task); setValues(example.parameters) }}><Sparkles size={11}/> Use example</button></label>
      <textarea id="task-input" data-testid="task-input" value={task} onChange={event => setTask(event.target.value)} placeholder="Describe the website, what to do, and the result you expect…" required maxLength={2000}/>
      {siteId === 'amazon_in' && resolvedSite !== 'web' && <label className="manual-toggle"><input type="checkbox" checked={manual} onChange={event => { setManual(event.target.checked); if (event.target.checked && preview) setValues(Object.fromEntries(Object.entries(preview.parameters).map(([key, value]) => [key, String(value)]))) }}/> Set keywords and filters manually</label>}
      {natural && <div className="intent-preview" aria-live="polite"><strong>Understood request</strong>{preview && !previewError ? <dl>{preview.target_url && <div><dt>Website</dt><dd>{preview.target_url}</dd></div>}<div><dt>Action</dt><dd>{humanize(preview.goal_id)}</dd></div>{Object.entries(preview.parameters).map(([key, value]) => <div key={key}><dt>{labels[key] || humanize(key)}</dt><dd>{sortLabels[String(value)] || (typeof value === 'object' ? JSON.stringify(value) : String(value))}</dd></div>)}</dl> : <p>{previewError || 'Reading your task…'}</p>}<small>Edit the task to change what the agent should do.</small></div>}
      {!natural && <><div className="parameter-heading"><span>RUN PARAMETERS</span><span>{goal?.label}</span></div><div className="parameter-grid">{goal && Object.entries(goal.parameters).map(([key, spec]) => <div key={`${siteId}-${key}`}><label className="field-label" htmlFor={`parameter-${key}`}>{labels[key] || humanize(key)}</label>{spec.enum ? <select id={`parameter-${key}`} value={values[key] ?? String(spec.default ?? '')} onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))}>{!spec.required && <option value="">No sort preference</option>}{spec.enum.map(value => <option key={String(value)} value={String(value)}>{sortLabels[String(value)] || String(value)}</option>)}</select> : <input id={`parameter-${key}`} value={values[key] ?? ''} onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))} type={spec.type === 'number' || spec.type === 'integer' ? 'number' : 'text'} min={spec.minimum ?? undefined} max={spec.maximum ?? undefined} step={spec.type === 'number' ? 'any' : undefined} required={spec.required}/>}</div>)}</div></>}
      <label className="field-label" htmlFor="automation-set">Track in automation set</label>
      <select id="automation-set" value={automationId} onChange={event => setAutomationId(event.target.value)}><option value="">Automatic grouping by website and task</option>{automations.filter(item => item.site_id === resolvedSite && item.goal_id === resolvedGoal).map(item => <option key={item.automation_id} value={item.automation_id}>{item.name}</option>)}</select>
      <div className="new-set"><input aria-label="New automation set name" value={newName} onChange={event => setNewName(event.target.value)} placeholder="Name a new set (optional)" maxLength={120}/><button type="button" className="secondary-button" disabled={creating || !newName.trim()} onClick={() => void createSet()}><Plus size={12}/> Create</button></div>
      <div className="form-divider"/><div className="provider-row"><label className="field-label" htmlFor="provider-select">Vision provider</label><select id="provider-select" data-testid="provider-select" value={provider} onChange={event => { providerTouched.current = true; setProvider(event.target.value as ProviderName) }}>{(Object.keys(providers) as ProviderName[]).map(name => <option key={name} value={name} disabled={!available(name)}>{providers[name]}{!available(name) ? ' · unavailable' : ''}</option>)}</select></div>
      <p className={`provider-note ${provider === 'fixture' ? 'simulation' : ''}`}>{provider === 'fixture' ? <FlaskConical size={12}/> : <Cpu size={12}/>} {provider === 'fixture' ? 'Simulated decisions for local testing.' : health?.providers[provider]?.model}</p>
      {(siteId === 'amazon_in' || siteId === 'web') && <p className="provider-note">The selected LLM chooses each next action from the live page. Automatic reuses successful workflows learned by the model. Usage and estimated savings appear after completion.</p>}
      <div className="mode-controls" aria-label="Execution mode">{(['auto', 'cold', 'hot'] as const).map(value => <button type="button" key={value} className={mode === value ? 'selected' : ''} onClick={() => setMode(value)}>{value === 'auto' ? 'Automatic' : value === 'cold' ? 'Learn fresh' : 'Compiled only'}</button>)}</div>
      {error && <div className="inline-error" role="alert"><TriangleAlert size={13}/><span>{error}</span></div>}
      <button className="primary-button run-button" data-testid="run-task" type="submit" disabled={submitting || !health || !available(provider) || !task.trim()}>{submitting ? <LoaderCircle size={15} className="spinning"/> : <Play size={15} fill="currentColor"/>}{submitting ? 'Submitting…' : 'Run workflow'}<span>↵</span></button>
      <p className="form-footnote"><ShieldCheck size={11}/> Usage and estimated savings are saved for every run.</p>
    </form>
  </section>
}

export default memo(TaskForm)
