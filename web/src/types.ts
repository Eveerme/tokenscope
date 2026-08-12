// ---------- 后端 API 数据类型 ----------

export interface Totals {
  sessions: number
  input: number
  output: number
  cache_read: number
  cache_write: number
  reasoning: number
  api_calls: number
  est_cost: number
  priced_cost: number
  unpriced: number
  /** 估算成本（有定价时为数值，否则 null） */
  cost: number | null
  priced: boolean
}

export interface ModelStat {
  model: string
  sessions: number
  input: number
  output: number
  cache_read: number
  cache_write: number
  reasoning: number
  api_calls: number
  cost: number | null
  priced: boolean
}

export interface GroupStat {
  key: string
  label: string
  sessions: number
  input: number
  output: number
  cache_read: number
  api_calls: number
  cost?: number | null
}

export interface ToolStat extends GroupStat {
  key: string
  label: string
}

export interface ProjectStat {
  key: string
  sessions: number
  input: number
  output: number
  cache_read: number
  reasoning: number
  api_calls: number
  cost: number | null
  priced: boolean
}

export interface Summary {
  totals: Totals
  /** 上一周期等长窗口的 totals（"全部"视图为 null），用于环比 */
  prev_totals: Totals | null
  by_model: ModelStat[]
  by_tool: ToolStat[]
  by_source: GroupStat[]
  by_project: ProjectStat[]
  by_task: GroupStat[]
}

export interface TimelinePoint {
  date: string
  input: number
  output: number
  cache_read: number
  cache_write: number
  reasoning: number
}

export interface Timeline {
  granularity: 'day' | 'week' | 'month'
  points: TimelinePoint[]
}

export interface SessionRow {
  id: string
  title: string
  model: string
  tool: string
  tool_label: string
  cwd: string
  source: string
  source_label: string
  profile: string
  message_count: number
  tool_call_count: number
  api_call_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  reasoning_tokens: number
  started_at: number
  ended_at: number
  db_cost: number
  cost: number | null
}

export interface SessionList {
  total: number
  page: number
  page_size: number
  items: SessionRow[]
}

export interface UsageRow {
  model: string
  task: string
  task_label: string
  api_call_count: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  reasoning_tokens: number
  first_seen: number
  last_seen: number
  cost: number | null
}

export interface SessionDetail {
  session: SessionRow
  usage: UsageRow[]
}

export interface SourceInfo {
  path: string
  name: string
  type: string
  type_label: string
  auto: boolean
  exists: boolean
  size: number
  modified_at: number
  db_sessions: number
  total_input: number
  total_output: number
  total_cache_read: number
  last_activity: number
  est_cost: number | null
}

export interface SourcesResp {
  sources: SourceInfo[]
  hermes_home: string
}

export interface ModelOption {
  model: string
  sessions: number
  api_calls: number
  input_tokens: number
}

export interface Pricing {
  [model: string]: {
    input: number
    output: number
    cache_read: number
    cache_write: number
  }
}

// ---------- 前端共享状态 ----------

export type RangeKey = '1d' | '7d' | '30d' | '90d' | 'all' | 'custom'

export interface RangeState {
  key: RangeKey
  /** unix 秒 */
  from: number | null
  to: number | null
}

export const GRANULARITY_OPTIONS = [
  { label: '按天', value: 'day' },
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' },
] as const
