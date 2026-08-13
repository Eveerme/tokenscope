<script setup lang="ts">
import { inject, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { fmtCost, fmtDateTime, fmtTokens } from '../format'
import { RANGE_KEY, REFRESH_KEY, TOOL_KEY } from '../injectKeys'
import type { RequestRecord } from '../types'

const rangeState = inject(RANGE_KEY)!
const refreshTick = inject(REFRESH_KEY)!
const toolFilter = inject(TOOL_KEY)!

const TOOL_LABELS: Record<string, string> = {
  hermes: 'Hermes',
  codex: 'Codex',
  claude: 'Claude Code',
  zcode: 'zcode',
}

const loading = ref(false)
const items = ref<RequestRecord[]>([])
const total = ref(0)
const totals = ref({ count: 0, input: 0, output: 0, cache_read: 0, cost: 0 })
const page = ref(1)
const pageSize = ref(50)
const modelFilter = ref('')
const models = ref<string[]>([])

async function load() {
  loading.value = true
  try {
    const res = await api.requests({
      from: rangeState.value.from,
      to: rangeState.value.to,
      tool: toolFilter.value === 'all' ? '' : toolFilter.value,
      model: modelFilter.value,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = res.items
    total.value = res.total
    totals.value = res.totals
  } finally {
    loading.value = false
  }
}

async function loadModels() {
  const res = await api.models({
    from: rangeState.value.from,
    to: rangeState.value.to,
    tool: toolFilter.value === 'all' ? '' : toolFilter.value,
  })
  models.value = res.models.map((m) => m.model).filter(Boolean).sort()
}

watch([rangeState, toolFilter, refreshTick], () => {
  page.value = 1
  load()
  loadModels()
})
watch(modelFilter, () => {
  page.value = 1
  load()
})
watch(page, () => load())

onMounted(() => {
  load()
  loadModels()
})

function fmtDur(ms: number): string {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60000)}m${Math.round((ms % 60000) / 1000)}s`
}

function statusInfo(s: string): { label: string; type: 'success' | 'danger' | 'info' } {
  if (s === 'error') return { label: '失败', type: 'danger' }
  if (s === 'cancelled') return { label: '取消', type: 'info' }
  return { label: '成功', type: 'success' }
}

function sessionLabel(r: RequestRecord): string {
  if (r.tool === 'hermes' && r.task) return r.task
  return r.session_id
}
</script>

<template>
  <div class="space-y-5">
    <!-- 汇总卡片 -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
      <div class="stat-card p-4">
        <div class="text-xs text-slate-400">请求数</div>
        <div class="text-2xl font-bold text-slate-800 mt-1">{{ totals.count.toLocaleString() }}</div>
      </div>
      <div class="stat-card p-4">
        <div class="text-xs text-slate-400">输入 Tokens</div>
        <div class="text-2xl font-bold text-slate-800 mt-1">{{ fmtTokens(totals.input) }}</div>
      </div>
      <div class="stat-card p-4">
        <div class="text-xs text-slate-400">输出 Tokens</div>
        <div class="text-2xl font-bold text-slate-800 mt-1">{{ fmtTokens(totals.output) }}</div>
      </div>
      <div class="stat-card p-4">
        <div class="text-xs text-slate-400">缓存读取</div>
        <div class="text-2xl font-bold text-slate-800 mt-1">{{ fmtTokens(totals.cache_read) }}</div>
      </div>
      <div class="stat-card p-4">
        <div class="text-xs text-slate-400">估算成本</div>
        <div class="text-2xl font-bold text-slate-800 mt-1">{{ fmtCost(totals.cost) }}</div>
      </div>
    </div>

    <!-- 请求明细表 -->
    <div class="stat-card p-5">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-[15px] font-bold text-slate-800">请求明细</h2>
        <el-select v-model="modelFilter" size="small" clearable placeholder="按模型筛选" class="!w-56">
          <el-option v-for="m in models" :key="m" :label="m" :value="m" />
        </el-select>
      </div>

      <el-table :data="items" v-loading="loading" stripe size="default" style="width: 100%">
        <el-table-column label="时间" width="130">
          <template #default="{ row }">{{ fmtDateTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="工具" width="110">
          <template #default="{ row }">
            <el-tag size="small" type="info" effect="light">{{ TOOL_LABELS[row.tool] || row.tool }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="模型" prop="model" min-width="140" show-overflow-tooltip />
        <el-table-column label="会话" min-width="170" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-slate-600">{{ sessionLabel(row) }}</span>
            <el-tag v-if="row.api_calls" size="small" type="warning" effect="plain" class="ml-1">
              聚合 {{ row.api_calls }} 次
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入" align="right" width="90">
          <template #default="{ row }">{{ fmtTokens(row.input) }}</template>
        </el-table-column>
        <el-table-column label="输出" align="right" width="90">
          <template #default="{ row }">{{ fmtTokens(row.output) }}</template>
        </el-table-column>
        <el-table-column label="缓存读" align="right" width="90">
          <template #default="{ row }">{{ fmtTokens(row.cache_read) }}</template>
        </el-table-column>
        <el-table-column label="成本" align="right" width="90">
          <template #default="{ row }">{{ fmtCost(row.cost) }}</template>
        </el-table-column>
        <el-table-column label="时长" align="right" width="90">
          <template #default="{ row }">{{ fmtDur(row.duration_ms) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="76">
          <template #default="{ row }">
            <el-tag size="small" :type="statusInfo(row.status).type" effect="light">
              {{ statusInfo(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div class="flex items-center justify-between mt-3">
        <span class="text-xs text-slate-400">共 {{ total.toLocaleString() }} 条请求</span>
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          background
          small
        />
      </div>
    </div>
  </div>
</template>
