<script setup lang="ts">
import { computed, onMounted, provide, ref, type ComputedRef, type Ref } from 'vue'
import { Connection, DataAnalysis, FolderOpened, List, Refresh, Setting } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api } from './api'
import { dayStart, RANGE_PRESETS } from './format'
import type { RangeKey, RangeState } from './types'
import { RANGE_KEY, REFRESH_KEY, TOOL_KEY, TOOL_OPTIONS } from './injectKeys'
import Dashboard from './views/Dashboard.vue'
import Sessions from './views/Sessions.vue'
import Requests from './views/Requests.vue'
import Sources from './views/Sources.vue'
import Settings from './views/Settings.vue'

type ViewKey = 'dashboard' | 'sessions' | 'requests' | 'sources' | 'settings'

const view = ref<ViewKey>('dashboard')
const rangeKey = ref<RangeKey>('1d')
const customRange = ref<[Date, Date] | null>(null)
const refreshTick = ref(0)

/**
 * 已挂载过的视图集合。视图首次进入时才挂载（v-if），之后用 v-show 保留状态。
 * 避免在 display:none 下初始化 el-table 导致高度塌陷（行不可见不可点击）。
 */
const mountedViews = ref<Set<ViewKey>>(new Set(['dashboard']))

function goto(v: ViewKey) {
  const s = new Set(mountedViews.value)
  s.add(v)
  mountedViews.value = s
  view.value = v
}

const rangeState = computed<RangeState>(() => {
  if (rangeKey.value === 'custom' && customRange.value) {
    const [a, b] = customRange.value
    return {
      key: 'custom',
      from: Math.floor(a.getTime() / 1000),
      to: Math.floor(b.getTime() / 1000) + 86399,
    }
  }
  const preset = RANGE_PRESETS.find((p) => p.key === rangeKey.value)
  if (!preset || preset.fromDays == null || preset.toDays == null) {
    return { key: rangeKey.value, from: null, to: null }
  }
  return {
    key: rangeKey.value,
    from: dayStart(preset.fromDays),
    // toDays=0 截至此刻；toDays>=1 为对应日结束（如昨天 = 今天 0 点前 1 秒）
    to: preset.toDays === 0 ? Math.floor(Date.now() / 1000) : dayStart(preset.toDays - 1) - 1,
  }
})

provide(RANGE_KEY, rangeState)
provide(REFRESH_KEY, refreshTick)

/** 全局工具筛选（'all' = 全部） */
const toolFilter = ref('all')
provide(TOOL_KEY, toolFilter)

const appVersion = ref('')
onMounted(() => {
  api.config()
    .then((c) => (appVersion.value = c.version))
    .catch(() => {})
})

const navItems = [
  { key: 'dashboard' as ViewKey, label: '仪表盘', icon: DataAnalysis },
  { key: 'sessions' as ViewKey, label: '会话明细', icon: List },
  { key: 'requests' as ViewKey, label: '请求明细', icon: Connection },
  { key: 'sources' as ViewKey, label: '数据源', icon: FolderOpened },
  { key: 'settings' as ViewKey, label: '定价设置', icon: Setting },
]
const currentTitle = computed(() => navItems.find((n) => n.key === view.value)?.label ?? '')

const refreshing = ref(false)
function refreshAll() {
  refreshing.value = true
  refreshTick.value++
  ElMessage.success('已刷新数据')
  setTimeout(() => (refreshing.value = false), 600)
}

// ---------- 自定义时间快捷项 ----------
function dayAt(offsetDays: number, endOfDay = false): Date {
  const d = new Date()
  d.setDate(d.getDate() - offsetDays)
  d.setHours(endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0, endOfDay ? 999 : 0)
  return d
}
const dateShortcuts: { text: string; value: () => [Date, Date] }[] = [
  { text: '昨天', value: () => [dayAt(1), dayAt(1, true)] },
  { text: '最近 7 天', value: () => [dayAt(6), dayAt(0, true)] },
  { text: '最近 30 天', value: () => [dayAt(29), dayAt(0, true)] },
  { text: '本月', value: () => {
    const s = new Date(); s.setDate(1); s.setHours(0, 0, 0, 0)
    return [s, dayAt(0, true)]
  } },
  { text: '上月', value: () => {
    const now = new Date()
    const s = new Date(now.getFullYear(), now.getMonth() - 1, 1)
    const e = new Date(now.getFullYear(), now.getMonth(), 0)
    e.setHours(23, 59, 59, 999)
    return [s, e]
  } },
]
/** 不允许选择未来日期 */
function disabledDate(d: Date) {
  return d.getTime() > Date.now()
}
</script>

<template>
  <div class="flex h-full">
    <!-- 左侧导航 -->
    <aside class="w-52 shrink-0 bg-white border-r border-[#eef1f6] flex flex-col">
      <div class="flex items-center gap-2.5 px-5 h-16 border-b border-[#eef1f6]">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-400 text-white flex items-center justify-center font-bold text-base shadow-md shadow-blue-200">
          T
        </div>
        <div class="min-w-0">
          <div class="text-[15px] font-bold text-slate-800 leading-tight truncate">TokenScope</div>
          <div class="text-[11px] text-slate-400">v{{ appVersion || '—' }}</div>
        </div>
      </div>

      <nav class="flex-1 py-4 px-3 space-y-1">
        <button
          v-for="item in navItems"
          :key="item.key"
          class="w-full flex items-center gap-2.5 px-3.5 py-2.5 rounded-lg text-sm font-medium transition-colors"
          :class="view === item.key
            ? 'bg-blue-50 text-blue-600'
            : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'"
          @click="goto(item.key)"
        >
          <el-icon :size="17"><component :is="item.icon" /></el-icon>
          {{ item.label }}
        </button>
      </nav>

      <div class="p-3 border-t border-[#eef1f6]">
        <el-button class="w-full" :icon="Refresh" :loading="refreshing" @click="refreshAll">
          刷新数据
        </el-button>
      </div>
    </aside>

    <!-- 主区 -->
    <main class="flex-1 flex flex-col min-w-0">
      <header class="h-16 shrink-0 bg-white/85 backdrop-blur border-b border-[#eef1f6] flex items-center justify-between px-6 gap-4">
        <h1 class="text-lg font-bold text-slate-800 shrink-0">{{ currentTitle }}</h1>
        <div class="flex flex-wrap items-center justify-end gap-3 min-w-0">
          <el-select v-model="toolFilter" size="small" class="!w-32 shrink-0">
            <el-option v-for="o in TOOL_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-radio-group v-model="rangeKey" size="small">
            <el-radio-button v-for="p in RANGE_PRESETS" :key="p.key" :value="p.key">
              {{ p.label }}
            </el-radio-button>
            <el-radio-button value="custom">自定义</el-radio-button>
          </el-radio-group>
          <el-date-picker
            v-if="rangeKey === 'custom'"
            v-model="customRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            :clearable="false"
            :disabled-date="disabledDate"
            :shortcuts="dateShortcuts"
            style="width: 252px"
          />
        </div>
      </header>

      <section class="flex-1 overflow-y-auto p-6">
        <Dashboard v-if="mountedViews.has('dashboard')" v-show="view === 'dashboard'" />
        <Sessions v-if="mountedViews.has('sessions')" v-show="view === 'sessions'" />
        <Requests v-if="mountedViews.has('requests')" v-show="view === 'requests'" />
        <Sources v-if="mountedViews.has('sources')" v-show="view === 'sources'" />
        <Settings v-if="mountedViews.has('settings')" v-show="view === 'settings'" />
      </section>
    </main>
  </div>
</template>