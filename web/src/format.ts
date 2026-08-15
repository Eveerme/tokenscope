// ---------- 数字 / 日期格式化 ----------

/** 大数友好格式：1200000 -> 1.2M */
export function fmtTokens(n: number): string {
  if (n == null || Number.isNaN(n)) return '0'
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (Math.abs(n) >= 1e4) return `${(n / 1e3).toFixed(1)}K`
  return String(Math.round(n))
}

/** 千分位 */
export function fmtNum(n: number): string {
  if (n == null || Number.isNaN(n)) return '0'
  return Math.round(n).toLocaleString('en-US')
}

export function fmtCost(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—'
  if (n === 0) return '$0.00'
  if (n < 0.01) return `$${n.toFixed(4)}`
  if (n < 100) return `$${n.toFixed(2)}`
  return `$${Math.round(n).toLocaleString('en-US')}`
}

export function fmtDateTime(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function fmtDate(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function fmtDuration(start: number | null, end: number | null): string {
  if (!start) return '—'
  const secs = Math.max(0, Math.round(((end ?? Date.now() / 1000) - start)))
  if (secs < 60) return `${secs}秒`
  if (secs < 3600) return `${Math.floor(secs / 60)}分${secs % 60 ? `${secs % 60}秒` : ''}`
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return `${h}时${m}分`
}

export function fmtBytes(n: number): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(1)} ${units[i]}`
}

/** unix 秒时间戳（当天 0 点，offsetDays=0 为今天，1 为昨天…） */
export function dayStart(offsetDays: number): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - offsetDays)
  return Math.floor(d.getTime() / 1000)
}

export interface RangePreset {
  key: string
  label: string
  /** 起始日距今天的天数（0=今天 0 点，1=昨天 0 点…），null=不限 */
  fromDays: number | null
  /** 结束日距今天的天数（0=截至此刻，1=昨天 23:59:59…），null=不限 */
  toDays: number | null
}

export const RANGE_PRESETS: RangePreset[] = [
  { key: '1d', label: '今天', fromDays: 0, toDays: 0 },
  { key: 'yesterday', label: '昨天', fromDays: 1, toDays: 1 },
  { key: '7d', label: '近 7 天', fromDays: 6, toDays: 0 },
  { key: '30d', label: '近 30 天', fromDays: 29, toDays: 0 },
  { key: 'all', label: '全部', fromDays: null, toDays: null },
]
