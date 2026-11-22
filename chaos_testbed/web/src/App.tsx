import { useEffect, useState } from 'react'
import { ArrowDownUp, ArrowRight, Check, ChevronDown, FlaskConical, Headphones, LogOut, Package, Search, ShieldCheck, ShoppingBag, SlidersHorizontal, Star, Truck, X } from 'lucide-react'

type MutationName = 'none' | 'selector_drift' | 'spatial_shift' | 'copy_mutation' | 'dom_nesting' | 'obstacle' | 'compound'
type Mutation = { mutation: MutationName; seed: number; generation: number; modal_visible: boolean }
type Product = { id: string; name: string; category: string; price: number; rating: number; color: string; kind: string }
type ProductResponse = { products: Product[]; query: string; max_price: number | null; sort_by: string; count: number }
type Order = { order_id: string; customer: string; item: string; total: number; status: string; placed_at: string; address: string }
const emptyMutation: Mutation = { mutation: 'none', seed: 42, generation: 0, modal_visible: false }
const mutationLabels: Record<MutationName, string> = { none: 'Original interface', selector_drift: 'Selector drift', spatial_shift: 'Spatial shift', copy_mutation: 'Copy mutation', dom_nesting: 'DOM nesting', obstacle: 'Blocking modal', compound: 'Combined redesign' }
const currency = (value: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value)

async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'same-origin', ...(body !== undefined ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The fixture service could not complete this request.')
  return data as T
}

function useMutation(site: 'shop' | 'admin') {
  const [mutation, setMutation] = useState<Mutation>(emptyMutation)
  useEffect(() => {
    let active = true
    const refresh = () => api<{ mutations: Record<string, Mutation> }>('/state').then(data => { if (active) setMutation(data.mutations[site]) }).catch(() => {})
    void refresh()
    const interval = window.setInterval(refresh, 700)
    return () => { active = false; window.clearInterval(interval) }
  }, [site])
  const dismiss = async () => {
    await api('/mutations/dismiss', { site_id: site, generation: mutation.generation })
    setMutation(value => ({ ...value, modal_visible: false }))
  }
  return { mutation, dismiss }
}

function Logo({ portal = false }: { portal?: boolean }) {
  return <a className="store-logo" href={portal ? '/admin' : '/shop'}><span className="star-logo">✳</span><span>northstar<span className="logo-caption">{portal ? 'OPERATIONS' : 'EVERYDAY ESSENTIALS'}</span></span></a>
}

function ProductArt({ kind }: { kind: string }) {
  if (kind === 'headphones') return <svg viewBox="0 0 160 130" aria-hidden="true"><path d="M39 79V63c0-54 82-54 82 0v16" fill="none" stroke="currentColor" strokeWidth="14" strokeLinecap="round"/><rect x="27" y="66" width="27" height="44" rx="11" fill="currentColor"/><rect x="106" y="66" width="27" height="44" rx="11" fill="currentColor"/><path d="M55 39c13-15 35-15 48 0" fill="none" stroke="#fff" strokeOpacity=".45" strokeWidth="5" strokeLinecap="round"/></svg>
  if (kind === 'backpack') return <svg viewBox="0 0 160 130" aria-hidden="true"><path d="M64 26c0-20 32-20 32 0" fill="none" stroke="currentColor" strokeWidth="8"/><rect x="40" y="25" width="80" height="94" rx="24" fill="currentColor"/><rect x="54" y="73" width="52" height="34" rx="11" fill="#fff" fillOpacity=".22"/><path d="M56 42h47M66 63h28" stroke="#fff" strokeOpacity=".5" strokeWidth="4" strokeLinecap="round"/></svg>
  return <svg viewBox="0 0 180 130" aria-hidden="true"><path d="m36 49 25 13 21-23 19 8 9 27 40 13c13 4 14 20 2 24H30c-20 0-20-24-8-27z" fill="currentColor"/><path d="m57 64 31 12m-22-23 31 12m-13-22 17 14" stroke="#fff" strokeOpacity=".65" strokeWidth="5" strokeLinecap="round"/><path d="M21 99h135" stroke="#fff" strokeOpacity=".65" strokeWidth="8"/><path d="m56 79 29 12 31-11" fill="none" stroke="#fff" strokeOpacity=".28" strokeWidth="8" strokeLinecap="round"/></svg>
}

function Obstacle({ dismiss }: { dismiss: () => Promise<void> }) {
  const [error, setError] = useState('')
  return <div className="modal-backdrop"><section className="store-modal" role="dialog" aria-modal="true" aria-labelledby="notice-title"><span className="modal-icon"><Package size={28}/></span><p className="eyebrow">A SMALL UPDATE</p><h2 id="notice-title">A refreshed experience.</h2><p>We have updated a few things around here. Your products and orders are exactly where you left them.</p>{error && <p className="form-error">{error}</p>}<button className="store-primary" data-testid="dismiss-notice" onClick={() => void dismiss().catch(err => setError(String(err)))}>Continue browsing <ArrowRight size={16}/></button></section></div>
}

function LabControls({ site, mutation }: { site: 'shop' | 'admin'; mutation: Mutation }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const change = async (value: MutationName) => {
    setBusy(true); setError('')
    try { await api('/mutations', { site_id: site, mutation: value, seed: 7 }) } catch (err) { setError(String(err)) } finally { setBusy(false) }
  }
  return <aside className="lab-dock"><button className="lab-toggle" onClick={() => setOpen(!open)}><FlaskConical size={15}/> Testbed controls <span className="lab-mode">{mutationLabels[mutation.mutation]}</span><ChevronDown size={14}/></button>{open && <div className="lab-popover"><p>Apply a repeatable interface change.</p><select aria-label="UI mutation" value={mutation.mutation} disabled={busy} onChange={event => void change(event.target.value as MutationName)}>{Object.entries(mutationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><button disabled={busy} onClick={() => void api('/reset', { seed: 42 }).then(() => window.location.reload()).catch(err => setError(String(err)))}>Reset fixtures</button>{error && <p className="form-error">{error}</p>}</div>}</aside>
}

function Shop() {
  const { mutation, dismiss } = useMutation('shop')
  const [query, setQuery] = useState('')
  const [maxPrice, setMaxPrice] = useState('')
  const [sortBy, setSortBy] = useState('rating_desc')
  const [applied, setApplied] = useState<ProductResponse>({ products: [], query: '', max_price: null, sort_by: 'rating_desc', count: 0 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refineOpen, setRefineOpen] = useState(false)
  const compound = mutation.mutation === 'compound'
  const renamed = compound || mutation.mutation === 'copy_mutation'
  const nested = compound || mutation.mutation === 'dom_nesting'
  const drift = compound || mutation.mutation === 'selector_drift' || mutation.mutation === 'copy_mutation'
  const moved = mutation.mutation === 'spatial_shift'
  const id = (name: string) => drift ? `control-${mutation.seed}-${name}` : name

  async function load(search: string, price: number | null, sort: string) {
    setBusy(true); setError('')
    const params = new URLSearchParams({ q: search, sort_by: sort })
    if (price !== null) params.set('max_price', String(price))
    try { setApplied(await api<ProductResponse>(`/products?${params}`)); window.history.replaceState({}, '', `/shop${search ? `?q=${encodeURIComponent(search)}` : ''}`) } catch (err) { setError(String(err)) } finally { setBusy(false) }
  }
  useEffect(() => { void load('', null, 'rating_desc') }, [])
  const submitSearch = () => void load(query, applied.max_price, sortBy)
  const submitFilters = () => {
    const numeric = maxPrice.trim() ? Number(maxPrice) : null
    if (numeric !== null && (!Number.isFinite(numeric) || numeric < 0)) { setError('Enter a valid maximum price.'); return }
    void load(applied.query, numeric, sortBy)
  }
  const filterContent = <><div className="filter-heading"><SlidersHorizontal size={17}/><h3>{renamed ? 'Refine your selection' : 'Filters'}</h3></div><label htmlFor={id('max-price')}>{renamed ? 'Budget limit' : 'Max price'}</label><div className="price-input"><span>₹</span><input id={id('max-price')} data-testid={id('max-price')} type={compound ? 'text' : 'number'} min="0" inputMode="decimal" placeholder={renamed ? 'Your budget' : 'No limit'} value={maxPrice} onChange={event => setMaxPrice(event.target.value)}/></div>{nested ? <section className="nested-controls"><div><div>{compound ? <a href="#apply" role="button" className="store-primary filter-apply" data-testid={id('apply-filter')} onClick={event => { event.preventDefault(); submitFilters() }}>Apply budget <Check size={15}/></a> : <button className="store-primary filter-apply" data-testid={id('apply-filter')} onClick={submitFilters}>{renamed ? 'Update selection' : 'Apply filters'} <Check size={15}/></button>}</div></div></section> : <button className="store-primary filter-apply" data-testid={id('apply-filter')} onClick={submitFilters}>{renamed ? 'Update selection' : 'Apply filters'} <Check size={15}/></button>}<div className="filter-separator"/><p className="filter-note"><ShieldCheck size={17}/>Thoughtfully selected.<br/>Made for every day.</p></>
  return <div className={`store-app ${moved ? 'spatial-variant' : ''}`}><div className="announcement">A little more everyday. Free delivery on orders over ₹2,000.</div><header className="store-header"><Logo/><nav><a href="/shop">Discover</a><a href="/shop" className="nav-muted">Our collection</a></nav><a className="header-bag" href="/admin" title="Operations portal"><ShoppingBag size={20}/></a></header><main className="shop-main"><section className="shop-hero"><div><p className="eyebrow">THE EVERYDAY COLLECTION</p><h1>Good things.<br/><span>For your everyday.</span></h1><p>A considered collection of things you'll reach for again.</p></div><div className="hero-art"><span className="hero-circle"/><ProductArt kind="shoe"/><span className="hero-note">Comfort, considered.</span></div></section><form className="store-search" onSubmit={event => { event.preventDefault(); submitSearch() }}><Search size={19}/><input type="search" aria-label="Search products" id={id('search-input')} data-testid={id('search-input')} placeholder="Search products, brands, and more" value={query} onChange={event => setQuery(event.target.value)}/><button type="submit" data-testid={id('search-submit')} className="search-button">Search <ArrowRight size={16}/></button></form><div className="catalog-layout">{!compound && <aside className="filter-panel">{filterContent}</aside>}<section className="catalog"><div className="catalog-heading"><div><p className="eyebrow">{applied.query ? 'YOUR SEARCH' : 'EXPLORE THE COLLECTION'}</p><h2>{applied.query ? `Results for “${applied.query}”` : 'Everyday favorites'} <span>{applied.count}</span></h2></div><div className="catalog-tools">{compound && <button className="refine-button" onClick={() => setRefineOpen(!refineOpen)}><SlidersHorizontal size={15}/> Refine results <ChevronDown size={14}/></button>}<label className="sort-label"><ArrowDownUp size={15}/><span className="sr-only">Sort by</span><select data-testid="sort-select" aria-label="Sort by" value={sortBy} onChange={event => { setSortBy(event.target.value); void load(applied.query, applied.max_price, event.target.value) }}><option value="rating_desc">Highest rated</option><option value="price_asc">Price: low to high</option></select></label></div></div>{compound && refineOpen && <aside className="filter-panel expanded-refine">{filterContent}</aside>}{error && <p role="alert" className="form-error">{error}</p>}<div className={`product-grid ${busy ? 'loading' : ''}`} data-testid="search-results" data-query={applied.query} data-max-price={applied.max_price === null ? '' : String(applied.max_price)} data-sort-by={applied.sort_by}>{applied.products.map(product => <article key={product.id} className="product-card" data-testid="product-card" data-product-name={product.name} data-price={product.price} data-rating={product.rating}><a href={`/shop?product=${product.id}`} className={`product-art ${product.color}`} aria-label={product.name}><ProductArt kind={product.kind}/><span className="product-rating"><Star size={12} fill="currentColor"/>{product.rating}</span></a><p className="product-category">{product.category}</p><a className="product-name" href={`/shop?product=${product.id}`}>{product.name}</a><p className="product-price">{currency(product.price)} <span>Inclusive of taxes</span></p></article>)}{!busy && !applied.products.length && <div className="empty-products"><Search size={26}/><h3>No products found</h3><p>Try a different search or a higher maximum price.</p></div>}</div></section></div></main><footer className="store-footer"><Logo/><p>Small choices. Better everyday.</p><span>LOCAL DEMONSTRATION STORE</span></footer><LabControls site="shop" mutation={mutation}/>{mutation.modal_visible && <Obstacle dismiss={dismiss}/>}</div>
}

function Admin() {
  const { mutation, dismiss } = useMutation('admin')
  const [loggedIn, setLoggedIn] = useState(false)
  const [sessionReady, setSessionReady] = useState(false)
  const [demoCredentials, setDemoCredentials] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [query, setQuery] = useState('')
  const [orders, setOrders] = useState<Order[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [menu, setMenu] = useState<string | null>(null)
  const compound = mutation.mutation === 'compound'
  const renamed = compound || mutation.mutation === 'copy_mutation'
  const nested = compound || mutation.mutation === 'dom_nesting'
  const drift = compound || mutation.mutation === 'selector_drift' || mutation.mutation === 'copy_mutation'
  const moved = mutation.mutation === 'spatial_shift'
  const id = (name: string) => drift ? `control-${mutation.seed}-${name}` : name
  const loadOrders = async (q: string) => { setError(''); try { setOrders((await api<{ orders: Order[] }>(`/orders?q=${encodeURIComponent(q)}`)).orders) } catch (err) { setError(String(err)) } }
  useEffect(() => { void api<{ logged_in: boolean; demo_credentials?: boolean }>('/session').then(data => { setLoggedIn(data.logged_in); setDemoCredentials(data.demo_credentials === true); if (data.logged_in) void loadOrders('') }).catch(err => setError(String(err))).finally(() => setSessionReady(true)) }, [])
  const login = async () => { setBusy(true); setError(''); try { await api('/login', { username, password }); setLoggedIn(true); await loadOrders('') } catch (err) { setError(String(err)) } finally { setBusy(false) } }
  const ship = async (orderId: string) => { setBusy(true); setError(''); try { const result = await api<{ order: Order }>(`/orders/${orderId}/ship`, {}); setOrders(current => current.map(order => order.order_id === orderId ? result.order : order)); setMenu(null) } catch (err) { setError(String(err)) } finally { setBusy(false) } }
  const action = (order: Order) => order.status === 'Shipped' ? <span className="shipped-badge"><Check size={15}/> Dispatch complete</span> : compound ? <div className="order-action-menu"><button className="order-menu-toggle" onClick={() => setMenu(menu === order.order_id ? null : order.order_id)}>Order actions <ChevronDown size={15}/></button>{menu === order.order_id && <div className="order-menu"><a href="#dispatch" role="button" data-testid={id('ship-action')} onClick={event => { event.preventDefault(); void ship(order.order_id) }}><Truck size={16}/> Dispatch package</a></div>}</div> : <button className="store-primary" data-testid={id('ship-action')} disabled={busy} onClick={() => void ship(order.order_id)}><Truck size={16}/>{renamed ? 'Dispatch package' : 'Mark shipped'}</button>
  return <div className="admin-app"><header className="store-header"><Logo portal/><a href="/shop" className="back-to-store">View storefront <ArrowRight size={15}/></a>{loggedIn && <button className="logout-button" onClick={() => void api('/logout', {}).then(() => { setLoggedIn(false); setOrders([]) })}><LogOut size={16}/> Sign out</button>}</header>{!loggedIn ? <main className="login-main"><section className="login-brand"><p className="eyebrow">NORTHSTAR OPERATIONS</p><h1>Everything in order.<br/><span>Every step of the way.</span></h1><p>A quiet workspace for keeping things moving.</p><div className="login-illustration"><Package size={80} strokeWidth={1}/><span><Check size={20}/></span></div></section><section className="login-panel"><span className="login-icon"><ShieldCheck size={23}/></span><h2>Welcome back.</h2><p>Sign in to your operations workspace.</p><form onSubmit={event => { event.preventDefault(); void login() }}><label htmlFor="username">Username</label><input id="username" data-testid="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)}/><label htmlFor="password">Password</label><input id="password" data-testid="password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)}/>{error && <p role="alert" className="form-error">{error}</p>}<button className="store-primary" data-testid="login-submit" type="submit" disabled={busy || !sessionReady}>{busy ? 'Signing in…' : 'Sign in'} <ArrowRight size={16}/></button></form><p className="demo-credentials">{demoCredentials ? <>Demo account: <code>demo</code> / <code>demo-password</code></> : 'Use your configured fixture account.'}</p></section></main> : <main className="orders-main"><div className="orders-title"><div><p className="eyebrow">YOUR OPERATIONS WORKSPACE</p><h1>Orders<span>.</span></h1><p>Find an order. Keep it moving.</p></div><span className="operations-badge"><span/> All systems operational</span></div><div className="order-summary"><div><Package size={20}/><span>Order management<strong>Ready to dispatch</strong></span></div><p>Search an order ID to view its current status and shipment action.</p></div><form className="store-search order-search" onSubmit={event => { event.preventDefault(); void loadOrders(query) }}><Search size={19}/><input type="search" aria-label="Search order ID" id={id('order-search')} data-testid={id('order-search')} placeholder="Enter order ID, e.g. 1042" value={query} onChange={event => setQuery(event.target.value)}/><button type="submit" data-testid={id('order-search-submit')} className="search-button">Find order <ArrowRight size={16}/></button></form>{error && <p role="alert" className="form-error">{error}</p>}<section className="order-list"><div className="order-list-heading"><h2>{query ? 'Matching orders' : 'Recent orders'}</h2><span>{orders.length} {orders.length === 1 ? 'order' : 'orders'}</span></div>{orders.map(order => <article className={`order-card ${moved ? 'moved-action' : ''}`} data-testid="order-detail" data-order-id={order.order_id} key={order.order_id}><div className="order-card-top"><div><p className="eyebrow">ORDER NUMBER</p><h3>#{order.order_id}</h3></div><span className={`status-pill ${order.status.toLowerCase()}`} data-testid="order-status">{order.status}</span></div><div className="order-information"><div><p>Customer</p><strong>{order.customer}</strong><span>{order.address}</span></div><div><p>Product</p><strong>{order.item}</strong><span>{currency(order.total)}</span></div><div><p>Placed on</p><strong>{order.placed_at}</strong><span>Standard delivery</span></div></div>{nested ? <section className="dispatch-panel"><div className="dispatch-inner"><div className="order-card-action"><span><Package size={15}/> {order.status === 'Shipped' ? 'Your shipment is on its way.' : 'This order is ready for dispatch.'}</span>{action(order)}</div></div></section> : <div className="order-card-action"><span><Package size={15}/> {order.status === 'Shipped' ? 'Your shipment is on its way.' : 'This order is ready for dispatch.'}</span>{action(order)}</div>}</article>)}{!orders.length && <div className="empty-products"><Package size={28}/><h3>No matching order</h3><p>Try 1042, 2089, or 3015.</p></div>}</section></main>}<LabControls site="admin" mutation={mutation}/>{loggedIn && mutation.modal_visible && <Obstacle dismiss={dismiss}/>}</div>
}

export default function App() { return window.location.pathname.startsWith('/admin') ? <Admin/> : <Shop/> }
