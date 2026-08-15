<script setup lang="ts">
import { computed, inject, nextTick, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { fmtCost, fmtNum, fmtTokens, RANGE_PRESETS } from '../format'
import type { ModelStat, ProjectStat, RangeState, Summary, TimelinePoint } from '../types'
import { RANGE_KEY, REFRESH_KEY, TOOL_KEY } from '../injectKeys'
import { useChart, TOKEN_AXIS, type ChartOption } from '../useChart'

const rangeState = inject(RANGE_KEY) as ComputedRef<RangeState>
const refreshTick = inject(REFRESH_KEY) as Ref<number>
const toolFilter = inject(TOOL_KEY) as Ref<string>

const loading = ref(false)
const summary = ref<Summary | null>(null)
const tl = ref<TimelinePoint[]>([])
// 趋势图粒度自动：今天/昨天按小时，其他按天（不再手动切换）
const gran = computed<'day' | 'hour'>(() => (['1d', 'yesterday'].includes(rangeState.value.key) ? 'hour' : 'day'))

// 图表容器
const trendEl = ref<HTMLElement>()
const modelEl = ref<HTMLElement>()
const trend = useChart(trendEl)
const modelChart = useChart(modelEl)

async function load() {
  loading.value = true
  try {
    const rp = { from: rangeState.value.from, to: rangeState.value.to, tool: toolFilter.value === 'all' ? null : toolFilter.value }
    const [s, t] = await Promise.all([
      api.summary(rp),
      api.timeline(rp, gran.value),
    ])
    summary.value = s
    tl.value = t.points
    await nextTick()
    renderCharts()
  } catch (e) {
    ElMessage.error(`加载失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

watch([rangeState, gran, refreshTick, toolFilter], () => load(), { immediate: true })

// ---------- 核心指标 ----------
const t = computed(() => summary.value?.totals)
const prev = computed(() => summary.value?.prev_totals)

/** 环比变化百分比（无上一周期或基数为 0 时为 null） */
function deltaOf(cur: number | null | undefined, pv: number | null | undefined): number | null {
  if (cur == null || pv == null || pv === 0) return null
  return ((cur - pv) / pv) * 100
}

const totalTokens = computed(() => {
  const tot = t.value
  return (tot?.input ?? 0) + (tot?.output ?? 0) + (tot?.cache_read ?? 0)
})

const cacheHitRate = computed<number | null>(() => {
  const tot = t.value
  if (!tot) return null
  const denom = (tot.input ?? 0) + (tot.cache_read ?? 0)
  return denom > 0 ? ((tot.cache_read ?? 0) / denom) * 100 : null
})

const prevHitRate = computed<number | null>(() => {
  const pv = prev.value
  if (!pv) return null
  const denom = (pv.input ?? 0) + (pv.cache_read ?? 0)
  return denom > 0 ? ((pv.cache_read ?? 0) / denom) * 100 : null
})

const cacheSavings = computed(() => t.value?.cache_savings ?? 0)

/** 命中率环比（百分点） */
const hitDelta = computed<number | null>(() => {
  const cur = cacheHitRate.value
  const pv = prevHitRate.value
  if (cur == null || pv == null) return null
  return cur - pv
})

// 统计卡片：核心三项（总消耗 / 成本 / 缓存命中率）置前并高亮
const cards = computed(() => {
  const tot = t.value
  const input = tot?.input ?? 0
  const output = tot?.output ?? 0
  const prevTotal = prev.value ? prev.value.input + prev.value.output + prev.value.cache_read : null
  return [
    {
      label: '总 Token 消耗', value: fmtTokens(totalTokens.value), sub: '输入 + 输出 + 缓存读取',
      icon: 'Σ', cls: 'bg-indigo-50 text-indigo-600', accent: '#6366f1', featured: true,
      delta: deltaOf(totalTokens.value, prevTotal), deltaUnit: '%',
    },
    {
      label: '估算成本',
      value: tot?.cost != null ? fmtCost(tot.cost) : '—',
      sub: tot?.cost != null
        ? (tot.unpriced ? `${tot.unpriced} 个会话未定价` : '按定价表估算')
        : '未配置定价，见「定价设置」',
      icon: '$', cls: 'bg-emerald-50 text-emerald-600', accent: '#10b981', featured: true,
      delta: deltaOf(tot?.cost, prev.value?.cost), deltaUnit: '%',
    },
    {
      label: '缓存命中率',
      value: cacheHitRate.value != null ? `${cacheHitRate.value.toFixed(1)}%` : '—',
      sub: '缓存读取 ÷（输入 + 缓存读取）',
      icon: '◎', cls: 'bg-fuchsia-50 text-fuchsia-600', accent: '#d946ef', featured: true,
      delta: hitDelta.value, deltaUnit: 'pp',
    },
    {
      label: '输入 Tokens', value: fmtTokens(input), sub: `共 ${fmtNum(input)}`,
      icon: '↓', cls: 'bg-blue-50 text-blue-600',
      delta: deltaOf(tot?.input, prev.value?.input), deltaUnit: '%',
    },
    {
      label: '输出 Tokens', value: fmtTokens(output), sub: `共 ${fmtNum(output)}`,
      icon: '↑', cls: 'bg-cyan-50 text-cyan-600',
      delta: deltaOf(tot?.output, prev.value?.output), deltaUnit: '%',
    },
    {
      label: 'API 调用', value: fmtNum(tot?.api_calls ?? 0),
      sub: `共 ${tot?.sessions ?? 0} 个会话`, icon: '⇄', cls: 'bg-rose-50 text-rose-500',
      delta: deltaOf(tot?.api_calls, prev.value?.api_calls), deltaUnit: '%',
    },
  ]
})

// ---------- 摘要横幅（聚焦核心诉求：总 token / 成本 / 缓存） ----------
const rangeLabel = computed(() => {
  if (rangeState.value.key === 'custom') return '自定义范围'
  return RANGE_PRESETS.find((p) => p.key === rangeState.value.key)?.label ?? '全部'
})
const summaryText = computed(() => {
  const s = summary.value
  if (!s) return ''
  const tot = s.totals
  const parts: string[] = [
    rangeLabel.value,
    `${tot.sessions} 个会话`,
    `总消耗 ${fmtTokens(totalTokens.value)}`,
  ]
  if (tot.cost != null) parts.push(`估算成本 ${fmtCost(tot.cost)}`)
  else parts.push('未配置定价')
  if (cacheHitRate.value != null) parts.push(`缓存命中率 ${cacheHitRate.value.toFixed(1)}%`)
  if (cacheSavings.value > 0) parts.push(`缓存约节省 ${fmtCost(cacheSavings.value)}`)
  return parts.join(' · ')
})

// ---------- 图表 ----------
function renderCharts() {
  renderTrend()
  renderModel()
}

function renderTrend() {
  const pts = tl.value
  const dates = pts.map((p) => p.date)
  const opt: ChartOption = {
    color: ['#3b82f6', '#06b6d4', '#f59e0b', '#8b5cf6'],
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => fmtTokens(Number(v ?? 0)),
    },
    legend: { data: ['输入', '输出', '推理', '缓存读取'], top: 0 },
    grid: { left: 8, right: 8, top: 36, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: {
        fontSize: 11,
        rotate: pts.length > 20 ? 40 : 0,
        formatter: gran.value === 'hour' ? (v: string) => v.slice(-5) : undefined,
      },
    },
    yAxis: [
      { type: 'value', ...TOKEN_AXIS, splitLine: { lineStyle: { color: '#f1f5f9' } } },
      { type: 'value', ...TOKEN_AXIS, splitLine: { show: false } },
    ],
    series: [
      { name: '输入', type: 'bar', stack: 't', data: pts.map((p) => p.input), barMaxWidth: 26, itemStyle: { borderRadius: [0, 0, 0, 0] } },
      { name: '输出', type: 'bar', stack: 't', data: pts.map((p) => p.output), barMaxWidth: 26 },
      { name: '推理', type: 'bar', stack: 't', data: pts.map((p) => p.reasoning), barMaxWidth: 26, itemStyle: { borderRadius: [4, 4, 0, 0] } },
      {
        name: '缓存读取', type: 'line', yAxisIndex: 1, data: pts.map((p) => p.cache_read),
        smooth: true, symbol: 'none', lineStyle: { width: 2 },
      },
    ],
  }
  trend.setOption(opt)
}

function renderModel() {
  const ms = summary.value?.by_model ?? []
  const names = ms.map((m) => m.model)
  const opt: ChartOption = {
    color: ['#3b82f6', '#06b6d4'],
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: (v) => fmtTokens(Number(v ?? 0)),
    },
    legend: { data: ['输入', '输出'], top: 0 },
    grid: { left: 8, right: 20, top: 32, bottom: 4, containLabel: true },
    xAxis: { type: 'value', ...TOKEN_AXIS, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    yAxis: {
      type: 'category', data: names,
      axisLabel: { fontSize: 11, width: 110, overflow: 'truncate' },
    },
    series: [
      { name: '输入', type: 'bar', stack: 'm', data: ms.map((m) => m.input), barMaxWidth: 18, itemStyle: { borderRadius: [0, 0, 0, 0] } },
      { name: '输出', type: 'bar', stack: 'm', data: ms.map((m) => m.output), barMaxWidth: 18, itemStyle: { borderRadius: [0, 4, 4, 0] } },
    ],
  }
  modelChart.setOption(opt)
}

// ---------- 缓存命中率 ----------
const RING_C = 2 * Math.PI * 50
const ringDash = computed(() => {
  const rate = cacheHitRate.value
  if (rate == null) return '0 1000'
  return `${(RING_C * rate) / 100} ${RING_C}`
})

/** 命中/未命中比例条：缓存读取(命中) vs 输入(未命中) */
const cacheBar = computed(() => {
  const tot = t.value
  const input = tot?.input ?? 0
  const cr = tot?.cache_read ?? 0
  const total = input + cr
  if (total <= 0) return { hit: 0, miss: 0, hitPct: 0, missPct: 0 }
  return {
    hit: cr, miss: input,
    hitPct: (cr / total) * 100,
    missPct: (input / total) * 100,
  }
})

const hitDeltaText = computed(() => {
  const d = hitDelta.value
  if (d == null) return '—'
  return d >= 0 ? `▲ ${d.toFixed(1)} pp` : `▼ ${Math.abs(d).toFixed(1)} pp`
})
const hitDeltaCls = computed(() => {
  const d = hitDelta.value
  if (d == null) return 'text-slate-300'
  return d >= 0 ? 'text-emerald-600' : 'text-rose-500'
})

function modelHitRate(m: ModelStat): number | null {
  const denom = (m.input ?? 0) + (m.cache_read ?? 0)
  return denom > 0 ? ((m.cache_read ?? 0) / denom) * 100 : null
}
function projectHitRate(p: ProjectStat): number | null {
  const denom = (p.input ?? 0) + (p.cache_read ?? 0)
  return denom > 0 ? ((p.cache_read ?? 0) / denom) * 100 : null
}

// ---------- 模型明细表 ----------
const modelRows = computed<ModelStat[]>(() => summary.value?.by_model ?? [])
const projectRows = computed<ProjectStat[]>(() => summary.value?.by_project ?? [])
</script>

<template>
  <div class="space-y-5">
    <!-- 加载遮罩 -->
    <div v-if="loading" class="flex justify-center py-10">
      <div class="text-blue-500 text-sm">加载中…</div>
    </div>

    <!-- 摘要横幅 -->
    <div
      v-if="summaryText"
      class="rounded-2xl bg-gradient-to-r from-blue-50 to-cyan-50 border border-blue-100/60 px-5 py-3.5 text-[13px] text-slate-600 leading-relaxed"
    >
      <span class="font-bold text-slate-800">📊 {{ summaryText }}</span>
    </div>

    <!-- 统计卡片（核心三项置前高亮） -->
    <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
      <div
        v-for="c in cards"
        :key="c.label"
        class="stat-card p-4"
        :class="c.featured ? 'featured-stat' : ''"
        :style="c.featured ? { borderTop: '3px solid ' + c.accent } : {}"
      >
        <div class="flex items-center gap-2 mb-2">
          <span class="w-7 h-7 rounded-lg flex items-center justify-center text-sm font-bold" :class="c.cls">
            {{ c.icon }}
          </span>
          <span class="text-xs text-slate-500 font-medium">{{ c.label }}</span>
        </div>
        <div class="text-[22px] leading-8 font-bold text-slate-800 tnum" :class="c.featured ? 'text-[24px]' : ''">{{ c.value }}</div>
        <div class="flex items-center gap-1.5 mt-0.5 min-h-[16px]">
          <span
            v-if="c.delta != null"
            class="text-[11px] font-semibold"
            :class="c.delta >= 0 ? 'text-emerald-500' : 'text-rose-500'"
            :title="`较上一周期${c.delta >= 0 ? '增长' : '下降'} ${Math.abs(c.delta).toFixed(1)}${c.deltaUnit}`"
          >
            {{ c.delta >= 0 ? '▲' : '▼' }} {{ Math.abs(c.delta).toFixed(1) }}{{ c.deltaUnit }}
          </span>
          <span v-else class="text-[11px] text-slate-400 truncate">{{ c.sub }}</span>
          <span v-if="c.delta != null" class="text-[10px] text-slate-300 ml-auto truncate">{{ c.sub }}</span>
        </div>
      </div>
    </div>

    <!-- 趋势主图 -->
    <div class="stat-card p-5">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-[15px] font-bold text-slate-800">Token 消耗趋势</h2>
        <span class="text-xs text-slate-400">{{ gran === 'hour' ? '按小时' : '按天' }}</span>
      </div>
      <div ref="trendEl" class="h-[320px] w-full" />
    </div>

    <!-- 副图：按模型 / 缓存命中率 -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
      <div class="stat-card p-5">
        <h2 class="text-[15px] font-bold text-slate-800 mb-2">按模型</h2>
        <div ref="modelEl" class="h-[280px] w-full" />
      </div>

      <!-- 缓存命中率卡片（替代原“按工具”） -->
      <div class="stat-card p-5">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-[15px] font-bold text-slate-800">缓存命中率</h2>
          <span class="text-[11px] text-slate-400">缓存读取 ÷（输入 + 缓存读取）</span>
        </div>

        <div class="flex items-center gap-5">
          <div class="relative w-32 h-32 shrink-0">
            <svg viewBox="0 0 120 120" class="w-full h-full -rotate-90">
              <defs>
                <linearGradient id="cacheRingGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stop-color="#a855f7" />
                  <stop offset="100%" stop-color="#ec4899" />
                </linearGradient>
              </defs>
              <circle cx="60" cy="60" r="50" fill="none" stroke="#f1f5f9" stroke-width="12" />
              <circle
                cx="60" cy="60" r="50" fill="none" stroke="url(#cacheRingGrad)" stroke-width="12"
                stroke-linecap="round"
                :stroke-dasharray="ringDash"
              />
            </svg>
            <div class="absolute inset-0 flex flex-col items-center justify-center">
              <div class="text-[24px] leading-7 font-bold text-slate-800 tnum">
                {{ cacheHitRate != null ? cacheHitRate.toFixed(1) + '%' : '—' }}
              </div>
              <div class="text-[11px] text-slate-400">命中率</div>
            </div>
          </div>

          <div class="flex-1 grid grid-cols-2 gap-x-4 gap-y-3 min-w-0">
            <div>
              <div class="text-[11px] text-slate-400">缓存读取</div>
              <div class="text-[16px] font-bold text-slate-800 tnum">{{ fmtTokens(t?.cache_read ?? 0) }}</div>
              <div class="text-[11px] text-slate-400 truncate">{{ fmtNum(t?.cache_read ?? 0) }} tokens</div>
            </div>
            <div>
              <div class="text-[11px] text-slate-400">缓存写入</div>
              <div class="text-[16px] font-bold text-slate-800 tnum">{{ fmtTokens(t?.cache_write ?? 0) }}</div>
              <div class="text-[11px] text-slate-400 truncate">{{ fmtNum(t?.cache_write ?? 0) }} tokens</div>
            </div>
            <div>
              <div class="text-[11px] text-slate-400">约节省成本</div>
              <div class="text-[16px] font-bold text-emerald-600 tnum">{{ fmtCost(cacheSavings) }}</div>
              <div class="text-[11px] text-slate-400 truncate" title="按「输入价 − 缓存价」估算">按输入价 − 缓存价估算</div>
            </div>
            <div>
              <div class="text-[11px] text-slate-400">较上一周期</div>
              <div class="text-[16px] font-bold tnum" :class="hitDeltaCls">{{ hitDeltaText }}</div>
              <div class="text-[11px] text-slate-400 truncate">{{ rangeLabel }}</div>
            </div>
          </div>
        </div>

        <!-- 命中 / 未命中比例条 -->
        <div class="mt-4">
          <div class="flex h-2.5 rounded-full overflow-hidden bg-slate-100">
            <div
              class="bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-all"
              :style="{ width: cacheBar.hitPct + '%' }"
              :title="`缓存命中 ${fmtTokens(cacheBar.hit)}（${cacheBar.hitPct.toFixed(1)}%）`"
            />
            <div
              class="bg-blue-200 transition-all"
              :style="{ width: cacheBar.missPct + '%' }"
              :title="`未命中输入 ${fmtTokens(cacheBar.miss)}（${cacheBar.missPct.toFixed(1)}%）`"
            />
          </div>
          <div class="flex justify-between mt-1.5 text-[11px] text-slate-500">
            <span class="flex items-center gap-1">
              <span class="w-2 h-2 rounded-full bg-fuchsia-500" /> 命中 {{ fmtTokens(cacheBar.hit) }}
              <b class="text-slate-700 tnum">{{ cacheBar.hitPct.toFixed(1) }}%</b>
            </span>
            <span class="flex items-center gap-1">
              <span class="w-2 h-2 rounded-full bg-blue-300" /> 未命中 {{ fmtTokens(cacheBar.miss) }}
              <b class="text-slate-700 tnum">{{ cacheBar.missPct.toFixed(1) }}%</b>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 模型明细表 -->
    <div class="stat-card p-5">
      <h2 class="text-[15px] font-bold text-slate-800 mb-3">模型明细</h2>
      <el-table :data="modelRows" size="small">
        <el-table-column prop="model" label="模型" min-width="170" />
        <el-table-column prop="sessions" label="会话数" align="right" width="80" />
        <el-table-column prop="api_calls" label="调用次数" align="right" width="90" />
        <el-table-column label="输入" align="right" width="100">
          <template #default="{ row }">{{ fmtTokens(row.input) }}</template>
        </el-table-column>
        <el-table-column label="输出" align="right" width="100">
          <template #default="{ row }">{{ fmtTokens(row.output) }}</template>
        </el-table-column>
        <el-table-column label="缓存读取" align="right" width="110">
          <template #default="{ row }">{{ fmtTokens(row.cache_read) }}</template>
        </el-table-column>
        <el-table-column label="缓存命中率" align="right" width="105">
          <template #default="{ row }">
            <span :class="modelHitRate(row) != null ? 'text-violet-600 font-medium' : 'text-slate-300'">
              {{ modelHitRate(row) != null ? modelHitRate(row)!.toFixed(1) + '%' : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="缓存节省" align="right" width="100">
          <template #default="{ row }">
            <span :class="row.cache_savings > 0 ? 'text-emerald-600' : 'text-slate-300'">
              {{ fmtCost(row.cache_savings) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="推理" align="right" width="90">
          <template #default="{ row }">{{ fmtTokens(row.reasoning) }}</template>
        </el-table-column>
        <el-table-column label="估算成本" align="right" width="100">
          <template #default="{ row }">
            <span :class="row.cost != null ? '' : 'text-slate-300'" :title="row.cost != null ? '' : '该模型未配置定价'">
              {{ fmtCost(row.cost) }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 按项目明细表 -->
    <div class="stat-card p-5">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-[15px] font-bold text-slate-800">按项目（工作目录）</h2>
        <span class="text-[11px] text-slate-400">共 {{ projectRows.length }} 个项目</span>
      </div>
      <el-table :data="projectRows" size="small">
        <el-table-column prop="key" label="项目路径" min-width="270" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="font-mono text-[12px]">{{ row.key }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="sessions" label="会话数" align="right" width="80" />
        <el-table-column prop="api_calls" label="调用次数" align="right" width="90" />
        <el-table-column label="输入" align="right" width="100">
          <template #default="{ row }">{{ fmtTokens(row.input) }}</template>
        </el-table-column>
        <el-table-column label="输出" align="right" width="100">
          <template #default="{ row }">{{ fmtTokens(row.output) }}</template>
        </el-table-column>
        <el-table-column label="缓存读取" align="right" width="110">
          <template #default="{ row }">{{ fmtTokens(row.cache_read) }}</template>
        </el-table-column>
        <el-table-column label="缓存命中率" align="right" width="105">
          <template #default="{ row }">
            <span :class="projectHitRate(row) != null ? 'text-violet-600 font-medium' : 'text-slate-300'">
              {{ projectHitRate(row) != null ? projectHitRate(row)!.toFixed(1) + '%' : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="推理" align="right" width="90">
          <template #default="{ row }">{{ fmtTokens(row.reasoning) }}</template>
        </el-table-column>
        <el-table-column label="估算成本" align="right" width="100">
          <template #default="{ row }">{{ fmtCost(row.cost) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
