import type {
  ModelOption, Pricing, RequestList, SessionDetail, SessionList, SourcesResp, Summary, Timeline,
} from './types'

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.error) msg = body.error
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export interface RangeParams {
  from?: number | null
  to?: number | null
  tool?: string | null
}

function rangeQS(r: RangeParams): string {
  const p: string[] = []
  if (r.from != null) p.push(`from=${r.from}`)
  if (r.to != null) p.push(`to=${r.to}`)
  if (r.tool) p.push(`tool=${encodeURIComponent(r.tool)}`)
  return p.length ? `?${p.join('&')}` : ''
}

export const api = {
  health: () => req<{ ok: boolean; version: string; sources: number }>('/api/health'),

  config: () =>
    req<{ app: string; version: string; hermes_home: string; config_path: string }>('/api/config'),

  sources: () => req<SourcesResp>('/api/sources'),

  addSource: (path: string, name: string, type = 'hermes') =>
    req<SourcesResp>('/api/sources', {
      method: 'POST',
      body: JSON.stringify({ path, name, type }),
    }),

  removeSource: (path: string) =>
    req<SourcesResp>(`/api/sources?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),

  summary: (r: RangeParams = {}) => req<Summary>(`/api/summary${rangeQS(r)}`),

  timeline: (r: RangeParams = {}, granularity = 'day') => {
    const p: string[] = []
    if (r.from != null) p.push(`from=${r.from}`)
    if (r.to != null) p.push(`to=${r.to}`)
    p.push(`granularity=${granularity}`)
    return req<Timeline>(`/api/timeline?${p.join('&')}`)
  },

  sessions: (params: Record<string, string | number | null | undefined>) => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') p.set(k, String(v))
    }
    const q = p.toString()
    return req<SessionList>(`/api/sessions${q ? `?${q}` : ''}`)
  },

  sessionDetail: (id: string) => req<SessionDetail>(`/api/session/${encodeURIComponent(id)}`),

  requests: (params: Record<string, string | number | null | undefined>) => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v != null && v !== '') p.set(k, String(v))
    }
    const q = p.toString()
    return req<RequestList>(`/api/requests${q ? `?${q}` : ''}`)
  },

  models: (r: RangeParams = {}) => req<{ models: ModelOption[] }>(`/api/models${rangeQS(r)}`),

  getPricing: () => req<{ pricing: Pricing }>('/api/pricing'),

  savePricing: (pricing: Pricing) =>
    req<{ pricing: Pricing }>('/api/pricing', {
      method: 'POST',
      body: JSON.stringify({ pricing }),
    }),

  examplePricing: () =>
    req<{ pricing: Pricing }>('/api/pricing/example', { method: 'POST', body: '{}' }),
}
