import { defineStore } from 'pinia'

const STORAGE_KEY = 'netops_dashboard_layout'

/**
 * 大屏可配置模块清单。
 * key 唯一标识，visible 控制显示，order 控制顺序，size 控制栅格宽度。
 */
export const DASHBOARD_WIDGETS = [
  { key: 'stats', title: '指标卡片', desc: '设备总数 / 流量 / 告警 / 日志 / 流记录', size: 'full', visible: true, order: 1 },
  { key: 'traffic', title: '流量趋势', desc: '全网入向出向流量曲线', size: 'half', visible: true, order: 2 },
  { key: 'status', title: '设备状态分布', desc: '在线 / 离线 / 未知占比', size: 'quarter', visible: true, order: 3 },
  { key: 'cpu', title: 'CPU 使用率 Top 10', desc: 'CPU 占用最高的设备', size: 'quarter', visible: true, order: 4 },
  { key: 'mem', title: '内存使用率 Top 10', desc: '内存占用最高的设备', size: 'quarter', visible: true, order: 5 },
  { key: 'vendor', title: '厂商分布', desc: '各厂商设备数量占比', size: 'quarter', visible: true, order: 6 },
  { key: 'alerts', title: '最近告警', desc: '最新告警事件列表', size: 'half', visible: true, order: 7 },
  { key: 'stores', title: '门店设备分布', desc: '各门店设备数量与占比', size: 'half', visible: true, order: 8 },
]

const DEFAULT_LAYOUT = DASHBOARD_WIDGETS.map((w) => ({
  key: w.key,
  visible: w.visible,
  order: w.order,
  size: w.size,
}))

function normalize(saved) {
  const meta = Object.fromEntries(DASHBOARD_WIDGETS.map((w) => [w.key, w]))
  const list = Array.isArray(saved) && saved.length ? saved : DEFAULT_LAYOUT
  // 补齐缺失项、剔除未知项
  const known = list.filter((i) => meta[i.key])
  const seen = new Set(known.map((i) => i.key))
  const missing = DEFAULT_LAYOUT.filter((i) => !seen.has(i.key)).map((i) => ({ ...i }))
  const merged = [...known, ...missing]
    .map((i) => ({
      key: i.key,
      visible: i.visible !== false,
      order: i.order ?? 99,
      size: meta[i.key]?.size || i.size || 'quarter',
    }))
    .sort((a, b) => a.order - b.order)
  return merged
}

export const useDashboardStore = defineStore('dashboard', {
  state: () => ({
    layout: normalize(readStored()),
    editing: false,
  }),
  getters: {
    // 按顺序返回可见模块
    visibleWidgets: (state) =>
      state.layout
        .filter((i) => i.visible)
        .sort((a, b) => a.order - b.order)
        .map((i) => ({
          ...DASHBOARD_WIDGETS.find((w) => w.key === i.key),
          order: i.order,
          size: i.size,
        })),
    allWidgets: () => DASHBOARD_WIDGETS,
  },
  actions: {
    save() {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.layout))
    },
    setVisible(key, visible) {
      const item = this.layout.find((i) => i.key === key)
      if (item) {
        item.visible = visible
        this.save()
      }
    },
    move(key, direction) {
      const sorted = [...this.layout].sort((a, b) => a.order - b.order)
      const idx = sorted.findIndex((i) => i.key === key)
      const target = direction === 'up' ? idx - 1 : idx + 1
      if (idx < 0 || target < 0 || target >= sorted.length) return
      const tmp = sorted[idx].order
      sorted[idx].order = sorted[target].order
      sorted[target].order = tmp
      this.layout = sorted
      this.save()
    },
    setSize(key, size) {
      const item = this.layout.find((i) => i.key === key)
      if (item) {
        item.size = size
        this.save()
      }
    },
    reset() {
      this.layout = DEFAULT_LAYOUT.map((i) => ({ ...i }))
      this.save()
    },
  },
})

function readStored() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch (e) {
    return null
  }
}
