import { onBeforeUnmount, onMounted, type Ref } from 'vue'
import * as echarts from 'echarts'

export type ChartOption = echarts.EChartsOption

const DEFAULT_COLORS = [
  '#3b82f6', '#06b6d4', '#8b5cf6', '#f59e0b', '#10b981',
  '#ef4444', '#ec4899', '#84cc16', '#f97316', '#6366f1',
]

/**
 * 简易 ECharts 封装：挂载时 init，窗口缩放自适应，卸载时 dispose。
 * setOption(option, true) 每次全量替换，避免增量合并残留。
 */
export function useChart(el: Ref<HTMLElement | undefined>) {
  let chart: echarts.ECharts | null = null

  function ensureChart(): echarts.ECharts {
    if (!chart && el.value) {
      chart = echarts.init(el.value)
    }
    return chart as echarts.ECharts
  }

  function setOption(option: ChartOption) {
    ensureChart()?.setOption(option, true)
  }

  function resize() {
    chart?.resize()
  }

  onMounted(() => {
    ensureChart()
    window.addEventListener('resize', resize)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('resize', resize)
    chart?.dispose()
    chart = null
  })

  return { setOption, resize, colors: DEFAULT_COLORS }
}

/** 通用 tooltip 数字格式化（token 友好格式） */
export const TOKEN_AXIS = {
  axisLabel: {
    formatter: (v: number) => {
      if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`
      if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(0)}K`
      return String(v)
    },
  },
}
