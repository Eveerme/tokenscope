<script setup lang="ts">
import { inject, onMounted, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, Plus } from '@element-plus/icons-vue'
import { api } from '../api'
import { fmtBytes, fmtDateTime, fmtNum, fmtTokens } from '../format'
import type { SourceInfo } from '../types'
import { REFRESH_KEY, TOOL_OPTIONS } from '../injectKeys'

const refreshTick = inject(REFRESH_KEY) as Ref<number>

const loading = ref(false)
const sources = ref<SourceInfo[]>([])
const hermesHome = ref('')

async function load() {
  loading.value = true
  try {
    const res = await api.sources()
    sources.value = res.sources
    hermesHome.value = res.hermes_home
  } catch (e) {
    ElMessage.error(`加载失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

watch(refreshTick, () => load())
onMounted(load)

async function copyPath(p: string) {
  try {
    await navigator.clipboard.writeText(p)
    ElMessage.success('路径已复制')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

// ---------- 添加数据源 ----------
const addOpen = ref(false)
const addName = ref('')
const addPath = ref('')
const addType = ref('hermes')
const addSaving = ref(false)

async function addSource() {
  const path = addPath.value.trim()
  if (!path) {
    ElMessage.warning('请填写数据库路径')
    return
  }
  addSaving.value = true
  try {
    const res = await api.addSource(path, addName.value.trim(), addType.value)
    sources.value = res.sources
    addOpen.value = false
    addName.value = ''
    addPath.value = ''
    ElMessage.success('数据源已添加')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    addSaving.value = false
  }
}

// ---------- 移除（两阶段确认） ----------
const confirming = ref<string | null>(null)
let confirmTimer: ReturnType<typeof setTimeout> | undefined

async function removeSource(s: SourceInfo) {
  if (confirming.value !== s.path) {
    confirming.value = s.path
    clearTimeout(confirmTimer)
    confirmTimer = setTimeout(() => (confirming.value = null), 3000)
    return
  }
  clearTimeout(confirmTimer)
  confirming.value = null
  try {
    const res = await api.removeSource(s.path)
    sources.value = res.sources
    ElMessage.success('已移除数据源（配置文件仍保留，未删除任何数据）')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
</script>

<template>
  <div class="space-y-5">
    <!-- 说明 + 操作 -->
    <div class="stat-card p-5 flex flex-wrap items-center gap-4">
      <div class="flex-1 min-w-[260px]">
        <h2 class="text-[15px] font-bold text-slate-800 mb-1">数据源</h2>
        <p class="text-xs text-slate-500 leading-relaxed">
          自动发现四类工具的本地数据（只读访问，不修改任何数据）：
          Hermes 主实例与各 profile 的 <code class="bg-slate-100 rounded px-1 font-mono">state.db</code>、
          Codex 的 <code class="bg-slate-100 rounded px-1 font-mono">~\.codex\state_*.sqlite</code>、
          Claude Code 的 <code class="bg-slate-100 rounded px-1 font-mono">~\.claude\projects</code>、
          zcode 的 <code class="bg-slate-100 rounded px-1 font-mono">~\.zcode\cli\db\db.sqlite</code>。
          也可手动添加其他路径的数据（如其他机器拷贝的文件）。
        </p>
        <p class="text-[11px] text-slate-400 mt-1 font-mono">HERMES_HOME: {{ hermesHome || '—' }}</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="addOpen = true">添加数据源</el-button>
      <el-button :loading="loading" @click="load">刷新统计</el-button>
    </div>

    <!-- 数据源卡片 -->
    <div v-loading="loading" class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div v-for="s in sources" :key="s.path" class="stat-card p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-2 min-w-0">
            <span
              class="w-2.5 h-2.5 rounded-full shrink-0"
              :class="s.exists ? 'bg-emerald-500' : 'bg-rose-400'"
              :title="s.exists ? '数据库可读' : '文件不存在'"
            />
            <span class="text-[15px] font-bold text-slate-800 truncate">{{ s.name }}</span>
            <el-tag size="small" effect="plain" class="shrink-0">{{ s.type_label }}</el-tag>
            <el-tag size="small" :type="s.auto ? 'info' : 'success'" effect="light">
              {{ s.auto ? '自动发现' : '手动添加' }}
            </el-tag>
          </div>
          <div class="flex items-center gap-1 shrink-0">
            <el-button size="small" text :icon="CopyDocument" title="复制路径" @click="copyPath(s.path)" />
            <el-button
              v-if="!s.auto"
              size="small"
              text
              type="danger"
              @click="removeSource(s)"
            >
              {{ confirming === s.path ? '确认移除？' : '移除' }}
            </el-button>
          </div>
        </div>

        <div class="font-mono text-[11px] text-slate-500 bg-slate-50 rounded-lg px-3 py-2 mb-3 break-all leading-relaxed">
          {{ s.path }}
        </div>

        <div v-if="s.exists" class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
          <div class="rounded-lg bg-blue-50/60 p-2.5">
            <div class="text-[11px] text-slate-500">会话数</div>
            <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtNum(s.db_sessions) }}</div>
          </div>
          <div class="rounded-lg bg-cyan-50/60 p-2.5">
            <div class="text-[11px] text-slate-500">输入</div>
            <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtTokens(s.total_input) }}</div>
          </div>
          <div class="rounded-lg bg-violet-50/60 p-2.5">
            <div class="text-[11px] text-slate-500">缓存读取</div>
            <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtTokens(s.total_cache_read) }}</div>
          </div>
          <div class="rounded-lg bg-amber-50/60 p-2.5">
            <div class="text-[11px] text-slate-500">数据库</div>
            <div class="text-[15px] font-bold text-slate-800 tnum">{{ fmtBytes(s.size) }}</div>
          </div>
        </div>

        <div v-else class="rounded-lg bg-rose-50 p-3 text-xs text-rose-500">
          数据库文件不存在或不可读，可移除该数据源。
        </div>

        <div class="flex justify-between mt-3 text-[11px] text-slate-400">
          <span>最后活动：{{ fmtDateTime(s.last_activity) }}</span>
          <span>文件更新：{{ fmtDateTime(s.modified_at) }}</span>
        </div>
      </div>
    </div>

    <!-- 添加对话框 -->
    <el-dialog v-model="addOpen" title="添加数据源" width="480px">
      <el-form label-position="top">
        <el-form-item label="数据类型">
          <el-select v-model="addType" class="!w-full">
            <el-option v-for="o in TOOL_OPTIONS.slice(1)" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </el-form-item>
        <el-form-item :label="addType === 'claude' ? 'Claude 项目目录（含 *.jsonl）' : '数据库路径（文件或包含它的目录）'">
          <el-input v-model="addPath" :placeholder="addType === 'claude' ? '例如 C:\\Users\\me\\.claude\\projects' : '例如 C:\\Users\\me\\AppData\\Local\\hermes\\state.db'" />
        </el-form-item>
        <el-form-item label="显示名称（可选）">
          <el-input v-model="addName" placeholder="默认取文件名" maxlength="30" />
        </el-form-item>
      </el-form>
      <div class="rounded-lg bg-slate-50 p-3 text-[11px] text-slate-500 leading-relaxed">
        支持：本机任意 Hermes 实例的 state.db、profiles 下的 state.db，或从其他机器拷贝过来的数据库文件。添加前会校验文件存在性。
      </div>
      <template #footer>
        <el-button @click="addOpen = false">取消</el-button>
        <el-button type="primary" :loading="addSaving" @click="addSource">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>
