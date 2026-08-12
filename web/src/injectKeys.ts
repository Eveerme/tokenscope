import type { ComputedRef, InjectionKey, Ref } from 'vue'
import type { RangeState } from './types'

/** 全局时间范围（App 提供，视图注入） */
export const RANGE_KEY: InjectionKey<ComputedRef<RangeState>> = Symbol('range')
/** 全局刷新信号（App 提供，视图注入，值自增即触发重载） */
export const REFRESH_KEY: InjectionKey<Ref<number>> = Symbol('refresh')
/** 全局工具筛选（'all' = 全部，hermes/codex/claude/zcode） */
export const TOOL_KEY: InjectionKey<Ref<string>> = Symbol('tool')

export const TOOL_OPTIONS = [
  { value: 'all', label: '全部工具' },
  { value: 'hermes', label: 'Hermes' },
  { value: 'codex', label: 'Codex' },
  { value: 'claude', label: 'Claude Code' },
  { value: 'zcode', label: 'zcode' },
]
