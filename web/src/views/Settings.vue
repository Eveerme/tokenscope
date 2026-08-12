<script setup lang="ts">
import { inject, onMounted, ref, watch, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Plus } from '@element-plus/icons-vue'
import { api } from '../api'
import type { Pricing } from '../types'
import { REFRESH_KEY } from '../injectKeys'

const refreshTick = inject(REFRESH_KEY) as Ref<number>

interface PricingRow {
  model: string
  input: number
  output: number
  cache_read: number
  cache_write: number
}

const rows = ref<PricingRow[]>([])
const saving = ref(false)
const loading = ref(false)

function toRows(p: Pricing): PricingRow[] {
  return Object.entries(p).map(([model, v]) => ({
    model,
    input: v.input ?? 0,
    output: v.output ?? 0,
    cache_read: v.cache_read ?? 0,
    cache_write: v.cache_write ?? 0,
  }))
}

function toPricing(list: PricingRow[]): Pricing {
  const p: Pricing = {}
  for (const r of list) {
    const name = r.model.trim()
    if (!name) continue
    p[name] = {
      input: Number(r.input) || 0,
      output: Number(r.output) || 0,
      cache_read: Number(r.cache_read) || 0,
      cache_write: Number(r.cache_write) || 0,
    }
  }
  return p
}

async function load() {
  loading.value = true
  try {
    const res = await api.getPricing()
    rows.value = toRows(res.pricing)
  } catch (e) {
    ElMessage.error(`加载失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

watch(refreshTick, () => load())
onMounted(load)

function addRow() {
  rows.value.push({ model: '', input: 0, output: 0, cache_read: 0, cache_write: 0 })
}

function removeRow(i: number) {
  rows.value.splice(i, 1)
}

async function save() {
  const empty = rows.value.some((r) => !r.model.trim())
  if (empty) {
    ElMessage.warning('存在未填写模型名称的行，请补全或删除')
    return
  }
  saving.value = true
  try {
    const res = await api.savePricing(toPricing(rows.value))
    rows.value = toRows(res.pricing)
    refreshTick.value++
    ElMessage.success('定价已保存，各页成本已重新估算')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function fillExample() {
  try {
    await ElMessageBox.confirm(
      '将用示例定价（DeepSeek / GPT / Claude 官方参考价）覆盖当前全部定价，继续？',
      '填充示例定价',
      { type: 'warning', confirmButtonText: '覆盖', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await api.examplePricing()
    rows.value = toRows(res.pricing)
    refreshTick.value++
    ElMessage.success('已填充示例定价')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}
</script>

<template>
  <div class="space-y-5">
    <!-- 说明 -->
    <div class="stat-card p-5">
      <h2 class="text-[15px] font-bold text-slate-800 mb-2">模型定价表（成本估算）</h2>
      <p class="text-xs text-slate-500 leading-relaxed">
        Hermes 会话数据里记录了 tokens 消耗，但成本需要按模型定价估算。
        价格单位为 <b>USD / 每百万 tokens</b>。未配置定价的模型，成本显示为「—」。
        公式：<code class="bg-slate-100 rounded px-1 font-mono">输入 ÷ 1e6 × 单价 + 输出 ÷ 1e6 × 单价 + 缓存读取 ÷ 1e6 × 单价</code>
      </p>
    </div>

    <!-- 定价表 -->
    <div class="stat-card p-5" v-loading="loading">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-[13px] font-bold text-slate-700">定价配置</h3>
        <div class="flex items-center gap-2">
          <el-button size="small" @click="fillExample">填充示例定价</el-button>
          <el-button size="small" :icon="Plus" @click="addRow">添加模型</el-button>
          <el-button size="small" type="primary" :loading="saving" @click="save">保存</el-button>
        </div>
      </div>

      <el-table :data="rows" size="small">
        <el-table-column label="模型名称" min-width="200">
          <template #default="{ row }">
            <el-input v-model="row.model" placeholder="如 deepseek-chat" class="font-mono" />
          </template>
        </el-table-column>
        <el-table-column label="输入 $/M" min-width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.input" :min="0" :step="0.01" :precision="4" :controls="false" size="small" class="!w-full" />
          </template>
        </el-table-column>
        <el-table-column label="输出 $/M" min-width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.output" :min="0" :step="0.01" :precision="4" :controls="false" size="small" class="!w-full" />
          </template>
        </el-table-column>
        <el-table-column label="缓存读取 $/M" min-width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.cache_read" :min="0" :step="0.01" :precision="4" :controls="false" size="small" class="!w-full" />
          </template>
        </el-table-column>
        <el-table-column label="缓存写入 $/M" min-width="130">
          <template #default="{ row }">
            <el-input-number v-model="row.cache_write" :min="0" :step="0.01" :precision="4" :controls="false" size="small" class="!w-full" />
          </template>
        </el-table-column>
        <el-table-column label="" width="60" align="center">
          <template #default="{ $index }">
            <el-button size="small" text type="danger" :icon="Delete" @click="removeRow($index)" />
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
