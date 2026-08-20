<script setup lang="ts">
import { computed, inject, ref, watch, type ComputedRef, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { fmtCost, fmtDateTime, fmtDuration, fmtNum, fmtTokens } from '../format'
import type { RangeState, SessionDetail, SessionRow, Summary } from '../types'
import { RANGE_KEY, REFRESH_KEY, TOOL_KEY } from '../injectKeys'

const rangeState = inject(RANGE_KEY) as ComputedRef<RangeState>
const refreshTick = inject(REFRESH_KEY) as Ref<number>
const toolFilter = inject(TOOL_KEY) as Ref<string>

const loading = ref(false)
const rows = ref<SessionRow[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(15)
const q = ref('')
const model = ref('')
const source = ref('')

const sortField = ref('started_at')
const sortOrder = ref<'asc' | 'desc'>('desc')

const TOOL_TAG: Record<string, { type: 'primary' | 'success' | 'warning' | 'danger' | 'info'; cls: string }> = {
  hermes: { type: 'primary', cls: 'bg-blue-50 text-blue-600' },
  codex: { type: 'warning', cls: 'bg-violet-50 text-violet-600' },
  claude: { type: 'warning', cls: 'bg-orange-50 text-orange-600' },
  zcode: { type: 'success', cls: 'bg-cyan-50 text-cyan-600' },
  dsh: { type: 'success', cls: 'bg-emerald-50 text-emerald-600' },
}
function toolTag(tool: string) {
  return TOOL_TAG[tool] ?? { type: 'info' as const, cls: 'bg-slate-100 text-slate-500' }
}

// 筛选下拉选项
const modelOptions = ref<{ model: string }[]>([])
const sourceOptions = ref<{ key: string; label: string }[]>([])

async function loadOptions() {
  try {
    const [m, s] = await Promise.all([
      api.models({ from: rangeState.value.from, to: rangeState.value.to }),
      api.summary({ from: rangeState.value.from, to: rangeState.value.to }),
    ])
    modelOptions.value = m.models
    sourceOptions.value = (s as Summary).by_source.map((x) => ({ key: x.key, label: x.label }))
  } catch { /* 下拉失败不阻塞表格 */ }
}

async function load() {
  loading.value = true
  try {
    const res = await api.sessions({
      from: rangeState.value.from,
      to: rangeState.value.to,
      tool: toolFilter.value === 'all' ? null : toolFilter.value,
      q: q.value || null,
      model: model.value || null,
      source: source.value || null,
      sort: sortField.value,
      order: sortOrder.value,
      page: page.value,
      page_size: pageSize.value,
    })
    rows.value = res.items
    total.value = res.total
  } catch (e) {
    ElMessage.error(`加载失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

watch([rangeState, refreshTick, toolFilter], () => {
  page.value = 1
  loadOptions()
  load()
}, { immediate: true })

// 搜索防抖
let timer: ReturnType<typeof setTimeout> | undefined
watch(q, () => {
  clearTimeout(timer)
  timer = setTimeout(() => {
    page.value = 1
    load()
  }, 400)
})
watch([model, source], () => {
  page.value = 1
  load()
})

function onSortChange({ prop, order }: { prop: string; order: string | null }) {
  if (!prop) return
  sortField.value = prop
  sortOrder.value = order === 'ascending' ? 'asc' : 'desc'
  page.value = 1
  load()
}

function onPageChange(p: number) {
  page.value = p
  load()
}

// ---------- CSV 导出 ----------
const exportUrl = computed(() => {
  const p = new URLSearchParams()
  if (rangeState.value.from != null) p.set('from', String(rangeState.value.from))
  if (rangeState.value.to != null) p.set('to', String(rangeState.value.to))
  if (toolFilter.value !== 'all') p.set('tool', toolFilter.value)
  if (q.value.trim()) p.set('q', q.value.trim())
  if (model.value) p.set('model', model.value)
  if (source.value) p.set('source', source.value)
  return `/api/export.csv?${p.toString()}`
})

// ---------- 详情抽屉 ----------
const drawerOpen = ref(false)
const detail = ref<SessionDetail | null>(null)
const detailLoading = ref(false)

async function openDetail(row: SessionRow) {
  drawerOpen.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.sessionDetail(row.id)
  } catch (e) {
    ElMessage.error(`详情加载失败: ${(e as Error).message}`)
  } finally {
    detailLoading.value = false
  }
}

const d = computed(() => detail.value)

const apiTotal = computed(() => (d.value?.usage ?? []).reduce((s, u) => s + (u.api_call_count || 0), 0))

function usageTotal(field: 'input_tokens' | 'output_tokens' | 'cache_read_tokens' | 'reasoning_tokens') {
  return (d.value?.usage ?? []).reduce((s, u) => s + (u[field] || 0), 0)
}
</script>

<template>
  <div class="space-y-4">
    <!-- 筛选栏 -->
    <div class="stat-card p-4 flex flex-wrap items-center gap-3">
      <el-input
        v-model="q"
        placeholder="搜索标题 / 会话 ID / 模型…"
        clearable
        class="!w-72"
        :prefix-icon="'Search'"
      />
      <el-select v-model="model" placeholder="全部模型" clearable class="!w-44">
        <el-option v-for="m in modelOptions" :key="m.model" :label="m.model" :value="m.model" />
      </el-select>
      <el-select v-model="source" placeholder="全部来源" clearable class="!w-36">
        <el-option v-for="s in sourceOptions" :key="s.key" :label="s.label" :value="s.key" />
      </el-select>
      <a :href="exportUrl" download class="no-underline">
        <el-button size="small" type="primary" plain :icon="'Download'">导出 CSV</el-button>
      </a>
      <span class="text-xs text-slate-400 ml-auto">共 {{ fmtNum(total) }} 个会话</span>
    </div>

    <!-- 表格 -->
    <div class="stat-card p-4">
      <el-table
        v-loading="loading"
        :data="rows"
        size="small"
        height="calc(100vh - 320px)"
        class="w-full"
        @sort-change="onSortChange"
        @row-click="openDetail"
      >
        <el-table-column label="标题" min-width="230" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="flex items-center gap-2 min-w-0">
              <el-tag size="small" :type="toolTag(row.tool).type" effect="light" class="shrink-0 !border-0">
                {{ row.tool_label }}
              </el-tag>
              <span class="text-slate-700 cursor-pointer hover:text-blue-600 truncate">{{ row.title }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag size="small" effect="plain" class="font-mono !text-[11px]">{{ row.model }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="输入" align="right" width="100" sortable="custom" prop="input_tokens">
          <template #default="{ row }">{{ fmtTokens(row.input_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出" align="right" width="100" sortable="custom" prop="output_tokens">
          <template #default="{ row }">{{ fmtTokens(row.output_tokens) }}</template>
        </el-table-column>
        <el-table-column label="缓存读" align="right" width="100" sortable="custom" prop="cache_read_tokens">
          <template #default="{ row }">{{ fmtTokens(row.cache_read_tokens) }}</template>
        </el-table-column>
        <el-table-column label="成本" align="right" width="90" sortable="custom" prop="cost">
          <template #default="{ row }">
            <span :class="row.cost != null ? 'text-emerald-600 font-medium' : 'text-slate-300'">
              {{ fmtCost(row.cost) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="开始时间" align="right" width="135" sortable="custom" prop="started_at">
          <template #default="{ row }">{{ fmtDateTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="时长" align="right" width="90">
          <template #default="{ row }">{{ fmtDuration(row.started_at, row.ended_at) }}</template>
        </el-table-column>
      </el-table>

      <div class="flex justify-end mt-3">
        <el-pagination
          layout="total, prev, pager, next, sizes"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          :page-sizes="[15, 30, 50, 100]"
          background
          @current-change="onPageChange"
          @size-change="(s: number) => { pageSize = s; page = 1; load() }"
        />
      </div>
    </div>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawerOpen" size="560px" :with-header="true">
      <template #header>
        <div class="flex items-center gap-2 w-full">
          <span class="text-[15px] font-bold text-slate-800">会话详情</span>
          <el-tag size="small" type="info" v-if="d">{{ d.session.profile }}</el-tag>
          <a
            v-if="d"
            :href="`/api/session/${encodeURIComponent(d.session.id)}/export.md`"
            download
            class="no-underline ml-auto"
          >
            <el-button size="small" type="primary" plain :icon="'Download'">导出 MD</el-button>
          </a>
        </div>
      </template>

      <div v-loading="detailLoading" class="space-y-4">
        <template v-if="d">
          <div class="text-sm text-slate-700 font-medium leading-relaxed">{{ d.session.title }}</div>
          <div v-if="d.session.cwd" class="text-[11px] text-slate-400 font-mono break-all">
            工作目录：{{ d.session.cwd }}
          </div>

          <el-descriptions :column="2" size="small" border>
            <el-descriptions-item label="会话 ID">
              <span class="font-mono text-[11px]">{{ d.session.id }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="模型">{{ d.session.model }}</el-descriptions-item>
            <el-descriptions-item label="来源">{{ d.session.source_label }}</el-descriptions-item>
            <el-descriptions-item label="开始时间">{{ fmtDateTime(d.session.started_at) }}</el-descriptions-item>
            <el-descriptions-item label="消息数">{{ fmtNum(d.session.message_count) }}</el-descriptions-item>
            <el-descriptions-item label="工具调用">{{ fmtNum(d.session.tool_call_count) }}</el-descriptions-item>
          </el-descriptions>

          <div class="grid grid-cols-4 gap-3">
            <div class="rounded-lg bg-blue-50/70 p-3 text-center">
              <div class="text-[11px] text-slate-500">输入</div>
              <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtTokens(d.session.input_tokens) }}</div>
            </div>
            <div class="rounded-lg bg-cyan-50/70 p-3 text-center">
              <div class="text-[11px] text-slate-500">输出</div>
              <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtTokens(d.session.output_tokens) }}</div>
            </div>
            <div class="rounded-lg bg-violet-50/70 p-3 text-center">
              <div class="text-[11px] text-slate-500">缓存读取</div>
              <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtTokens(d.session.cache_read_tokens) }}</div>
            </div>
            <div class="rounded-lg bg-amber-50/70 p-3 text-center">
              <div class="text-[11px] text-slate-500">推理</div>
              <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtTokens(d.session.reasoning_tokens) }}</div>
            </div>
          </div>

          <div>
            <div class="flex items-center justify-between mb-2">
              <h3 class="text-[13px] font-bold text-slate-700">按任务拆分（session_model_usage）</h3>
              <span v-if="d.usage.length" class="text-[11px] text-slate-400">调用合计 {{ fmtNum(apiTotal) }}</span>
            </div>
            <el-table v-if="d.usage.length" :data="d.usage" size="small" max-height="360">
              <el-table-column prop="task_label" label="任务" width="100">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" type="warning">{{ row.task_label }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="model" label="模型" min-width="140" show-overflow-tooltip />
              <el-table-column prop="api_call_count" label="调用" align="right" width="70" />
              <el-table-column label="输入" align="right" width="90">
                <template #default="{ row }">{{ fmtTokens(row.input_tokens) }}</template>
              </el-table-column>
              <el-table-column label="输出" align="right" width="90">
                <template #default="{ row }">{{ fmtTokens(row.output_tokens) }}</template>
              </el-table-column>
              <el-table-column label="缓存读" align="right" width="90">
                <template #default="{ row }">{{ fmtTokens(row.cache_read_tokens) }}</template>
              </el-table-column>
              <el-table-column label="成本" align="right" width="80">
                <template #default="{ row }">{{ fmtCost(row.cost) }}</template>
              </el-table-column>
            </el-table>
            <div v-else class="rounded-lg bg-slate-50 p-4 text-xs text-slate-400 text-center">
              {{ d.session.tool === 'hermes' ? '该会话无任务级明细' : `${d.session.tool_label} 暂无任务级拆分，明细为工具汇总值` }}
            </div>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>
