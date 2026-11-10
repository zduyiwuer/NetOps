/**
 * ECharts 主题适配。
 *
 * 图表配置中的文字颜色需要跟随明暗主题变化，
 * 这里统一从 CSS 变量读取，保证深色模式下坐标轴、图例清晰可读。
 */
import { computed } from 'vue'
import { useThemeStore } from '../store/theme'

function cssVar(name, fallback) {
  if (typeof window === 'undefined') return fallback
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

/** 图表通用样式工厂（响应主题变化） */
export function useChartTheme() {
  const themeStore = useThemeStore()

  const axis = computed(() => ({
    color: cssVar('--netops-text-muted', '#6b7280'),
    fontSize: 11,
  }))

  const split = computed(() => ({
    lineStyle: { color: cssVar('--netops-border', '#eef1f5') },
  }))

  const text = computed(() => cssVar('--netops-text', '#1f2937'))

  const tooltip = computed(() => ({
    backgroundColor: cssVar('--el-bg-color-overlay', '#fff'),
    borderColor: cssVar('--netops-border', '#e5e7eb'),
    textStyle: { color: text.value, fontSize: 12 },
  }))

  return { axis, split, text, tooltip, themeStore }
}

/** 构造一个响应主题的图表 option（传入的 option 用函数形式生成） */
export function themedOption(builder) {
  const { axis, split, text, tooltip } = useChartTheme()
  return computed(() => {
    const t = {
      axis: axis.value,
      split: split.value,
      text: text.value,
      tooltip: { ...tooltip.value },
    }
    return builder(t)
  })
}
