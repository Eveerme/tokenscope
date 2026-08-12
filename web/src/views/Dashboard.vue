<script setup lang="ts">
import { computed, inject, nextTick, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { fmtCost, fmtNum, fmtTokens } from '../format'
import type { ModelStat, ProjectStat, RangeState, Summary, TimelinePoint } from '../types'
import { RANGE_KEY, REFRESH_KEY, TOOL_KEY } from '../injectKeys'
import { useChart, TOKEN_AXIS, type ChartOption } from '../useChart'

const rangeState = inject(RANGE_KEY) as ComputedRef<RangeState>
const refreshTick = inject(REFRESH_KEY) as Ref<number>
const toolFilter = inject(TOOL_KEY) as Ref<string>

const loading = ref(false)
const summary = ref<Summary | null>(null)
const tl = ref<TimelinePoint[]>([])
const gran = ref<'day' | 'week' | 'month'>('day')

// 图表容器
const trendEl = ref<HTMLElement>()
const modelEl = ref<HTMLElement>()
const toolEl = ref<HTMLElement>()
const sourceEl = ref<HTMLElement>()
const taskEl = ref<HTMLElement>()
const trend = useChart(trendEl)
const modelChart = useChart(modelEl)
const toolChart = useChart(toolEl)
const sourceChart = useChart(sourceEl)
const taskChart = useChart(taskEl)

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

// ---------- 统计卡片 ----------
const t = computed(() => summary.value?.totals)

const cards = computed(() => {
  const tot = t.value
  const input = tot?.input ?? 0
  const output = tot?.output ?? 0
  const cr = tot?.cache_read ?? 0
  const cacheRatio = input > 0 ? cr / input : 0
  return [
    {
      label: '输入 Tokens', value: fmtTokens(input), sub: `共 ${fmtNum(input)}`,
      icon: '↓', cls: 'bg-blue-50 text-blue-600',
    },
    {
      label: '输出 Tokens', value: fmtTokens(output), sub: `共 ${fmtNum(output)}`,
      icon: '↑', cls: 'bg-cyan-50 text-cyan-600',
    },
    {
      label: '缓存读取', value: fmtTokens(cr),
      sub: input > 0 ? `约为输入的 ${cacheRatio.toFixed(1)} 倍` : '无输入数据',
      icon: '◎', cls: 'bg-violet-50 text-violet-600',
    },
    {
      label: '推理 Tokens', value: fmtTokens(tot?.reasoning ?? 0),
      sub: `共 ${fmtNum(tot?.reasoning ?? 0)}`, icon: '✦', cls: 'bg-amber-50 text-amber-600',
    },
    {
      label: '估算成本',
      value: tot?.cost != null ? fmtCost(tot.cost) : '—',
      sub: tot?.cost != null
        ? `${tot.unpriced ? `${tot.unpriced} 个会话未定价` : '按定价表估算'}`
        : '未配置定价，见「定价设置」',
      icon: '$', cls: 'bg-emerald-50 text-emerald-600',
    },
    {
      label: 'API 调用', value: fmtNum(tot?.api_calls ?? 0),
      sub: `共 ${tot?.sessions ?? 0} 个会话`, icon: '⇄', cls: 'bg-rose-50 text-rose-500',
    },
    {
      label: '总 Token 消耗', value: fmtTokens((input + output + cr)),
      sub: `输入 + 输出 + 缓存读取`, icon: 'Σ', cls: 'bg-indigo-50 text-indigo-600',
    },
    {
      label: '输出 / 输入比', value: input > 0 ? `${((output / input) * 100).toFixed(0)}%` : '—',
      sub: `平均每会话 ${fmtTokens((input + output) / Math.max(1, tot?.sessions ?? 1))}`,
      icon: '≈', cls: 'bg-teal-50 text-teal-600',
    },
  ]
})

// ---------- 图表 ----------
function renderCharts() {
  renderTrend()
  renderModel()
  renderTool()
  renderSource()
  renderTask()
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
      axisLabel: { fontSize: 11, rotate: pts.length > 20 ? 40 : 0 },
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

function pieOption(title: string, data: { name: string; value: number }[]) {
  return {
    tooltip: { trigger: 'item', formatter: '{b}<br/>{c}（{d}%）' },
    legend: { bottom: 0, type: 'scroll', icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 11 } },
    series: [
      {
        name: title, type: 'pie', radius: ['42%', '68%'], center: ['50%', '44%'],
        avoidLabelOverlap: true, itemStyle: { borderRadius: 5, borderColor: '#fff', borderWidth: 2 },
        label: { show: false }, emphasis: { label: { show: true, fontSize: 13, fontWeight: 'bold' } },
        data,
      },
    ],
  } as ChartOption
}

function renderTool() {
  const bt = summary.value?.by_tool ?? []
  toolChart.setOption(pieOption('工具', bt.map((s) => ({ name: s.label, value: s.input }))))
}

function renderSource() {
  const bs = summary.value?.by_source ?? []
  sourceChart.setOption(pieOption('来源', bs.map((s) => ({ name: s.label, value: s.input }))))
}

function renderTask() {
  const bt = summary.value?.by_task ?? []
  taskChart.setOption(pieOption('任务', bt.map((s) => ({ name: s.label, value: s.api_calls }))))
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

    <!-- 统计卡片 -->
    <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-4">
      <div v-for="c in cards" :key="c.label" class="stat-card p-4">
        <div class="flex items-center gap-2 mb-2">
          <span class="w-7 h-7 rounded-lg flex items-center justify-center text-sm font-bold" :class="c.cls">
            {{ c.icon }}
          </span>
          <span class="text-xs text-slate-500 font-medium">{{ c.label }}</span>
        </div>
        <div class="text-[22px] leading-8 font-bold text-slate-800 tnum">{{ c.value }}</div>
        <div class="text-[11px] text-slate-400 mt-0.5 truncate">{{ c.sub }}</div>
      </div>
    </div>

    <!-- 趋势主图 -->
    <div class="stat-card p-5">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-[15px] font-bold text-slate-800">Token 消耗趋势</h2>
        <el-radio-group v-model="gran" size="small">
          <el-radio-button value="day">按天</el-radio-button>
          <el-radio-button value="week">按周</el-radio-button>
          <el-radio-button value="month">按月</el-radio-button>
        </el-radio-group>
      </div>
      <div ref="trendEl" class="h-[320px] w-full" />
    </div>

    <!-- 副图四宫格 -->
    <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5">
      <div class="stat-card p-5">
        <h2 class="text-[15px] font-bold text-slate-800 mb-2">按模型</h2>
        <div ref="modelEl" class="h-[240px] w-full" />
      </div>
      <div class="stat-card p-5">
        <h2 class="text-[15px] font-bold text-slate-800 mb-2">按工具</h2>
        <div ref="toolEl" class="h-[240px] w-full" />
      </div>
      <div class="stat-card p-5">
        <h2 class="text-[15px] font-bold text-slate-800 mb-2">按来源</h2>
        <div ref="sourceEl" class="h-[240px] w-full" />
      </div>
      <div class="stat-card p-5">
        <h2 class="text-[15px] font-bold text-slate-800 mb-2">按任务类型（调用次数）</h2>
        <div ref="taskEl" class="h-[240px] w-full" />
      </div>
    </div>

    <!-- 模型明细表 -->
    <div class="stat-card p-5">
      <h2 class="text-[15px] font-bold text-slate-800 mb-3">模型明细</h2>
      <el-table :data="modelRows" size="small">
        <el-table-column prop="model" label="模型" min-width="180" />
        <el-table-column prop="sessions" label="会话数" align="right" width="90" />
        <el-table-column prop="api_calls" label="调用次数" align="right" width="100" />
        <el-table-column label="输入" align="right" width="110">
          <template #default="{ row }">{{ fmtTokens(row.input) }}</template>
        </el-table-column>
        <el-table-column label="输出" align="right" width="110">
          <template #default="{ row }">{{ fmtTokens(row.output) }}</template>
        </el-table-column>
        <el-table-column label="缓存读取" align="right" width="120">
          <template #default="{ row }">{{ fmtTokens(row.cache_read) }}</template>
        </el-table-column>
        <el-table-column label="推理" align="right" width="100">
          <template #default="{ row }">{{ fmtTokens(row.reasoning) }}</template>
        </el-table-column>
        <el-table-column label="估算成本" align="right" width="110">
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
        <el-table-column prop="key" label="项目路径" min-width="280" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="font-mono text-[12px]">{{ row.key }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="sessions" label="会话数" align="right" width="90" />
        <el-table-column prop="api_calls" label="调用次数" align="right" width="100" />
        <el-table-column label="输入" align="right" width="110">
          <template #default="{ row }">{{ fmtTokens(row.input) }}</template>
        </el-table-column>
        <el-table-column label="输出" align="right" width="110">
          <template #default="{ row }">{{ fmtTokens(row.output) }}</template>
        </el-table-column>
        <el-table-column label="缓存读取" align="right" width="120">
          <template #default="{ row }">{{ fmtTokens(row.cache_read) }}</template>
        </el-table-column>
        <el-table-column label="推理" align="right" width="100">
          <template #default="{ row }">{{ fmtTokens(row.reasoning) }}</template>
        </el-table-column>
        <el-table-column label="估算成本" align="right" width="110">
          <template #default="{ row }">{{ fmtCost(row.cost) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
