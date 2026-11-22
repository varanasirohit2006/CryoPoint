export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, { signal: AbortSignal.timeout(15000), ...(body !== undefined ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail ?? data?.error
    const message = Array.isArray(detail) ? detail.map((item: { msg?: string; loc?: string[] }) => `${item.loc?.slice(1).join('.') || 'Request'}: ${item.msg || 'Invalid value'}`).join('; ') : typeof detail === 'string' ? detail : `Request failed (${response.status})`
    throw new Error(message)
  }
  return data as T
}

export function artifactUrl(path: string): string {
  const normalized = path.replaceAll('\\', '/').replace(/^\.?\/?\.data\//, '').replace(/^\/+/, '')
  return `/api/artifacts/${normalized.split('/').map(encodeURIComponent).join('/')}`
}

export const exportUrl = (id: string, version: number, format: 'python' | 'ir') => `/api/workflows/${encodeURIComponent(id)}/versions/${version}/export?format=${format}`
export const formatDuration = (ms?: number) => !ms ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
export const humanize = (value: string) => value.replaceAll('_', ' ').toLowerCase().replace(/(^|\s)\S/g, letter => letter.toUpperCase())

export function findScreenshot(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const data = value as Record<string, unknown>
  for (const key of ['screenshot_path', 'screenshot', 'artifact_path', 'path']) {
    if (typeof data[key] === 'string' && /\.(png|jpe?g|webp)$/i.test(data[key] as string)) return data[key] as string
  }
  for (const key of ['observation', 'frame', 'before', 'after']) { const nested = findScreenshot(data[key]); if (nested) return nested }
  return null
}
