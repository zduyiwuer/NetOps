<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        总览大屏
        <span class="sub">实时掌握全网设备健康状况</span>
      </h2>
      <div class="toolbar">
        <el-select v-model="storeId" placeholder="全部门店" clearable style="width: 150px" @change="load">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-tag v-if="currentStoreName" type="primary" effect="plain" size="default">
          当前筛选：{{ currentStoreName }}
        </el-tag>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button :icon="Setting" @click="configVisible = true">自定义</el-button>
      </div>
    </div>

    <!-- 指标卡 -->
    <div v-if="has('stats')" class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Switch /></el-icon>设备总数</div>
        <div class="value">{{ overview.device_total ?? 0 }}</div>
        <div class="foot">
          在线 <b class="text-ok">{{ overview.online ?? 0 }}</b> ·
          离线 <b class="text-danger">{{ overview.offline ?? 0 }}</b> ·
          可用率 {{ overview.availability ?? 0 }}%
        </div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><TrendCharts /></el-icon>实时总流量</div>
        <div class="value">{{ fmtNum(overview.total_in_mbps) }}<span class="unit">Mbps</span></div>
        <div class="foot">出向 {{ fmtNum(overview.total_out_mbps) }} Mbps</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Bell /></el-icon>活动告警</div>
        <div class="value" :class="overview.alerts_open ? 'text-danger' : ''">{{ overview.alerts_open ?? 0 }}</div>
        <div class="foot">严重 {{ overview.alerts_critical ?? 0 }} · 24h 新增 {{ overview.alerts_24h ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Document /></el-icon>日志量(24h)</div>
        <div class="value">{{ overview.logs_24h ?? 0 }}</div>
        <div class="foot">异常级别 {{ overview.error_logs_24h ?? 0 }} 条</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Share /></el-icon>流记录(24h)</div>
        <div class="value">{{ flowStat.total_flows ?? 0 }}</div>
        <div class="foot">NetFlow 会话 · {{ fmtBytes(flowStat.total_bytes) }}</div>
      </div>
    </div>

    <!-- 图表区 -->
    <div class="gap-row" style="margin-top: 12px">
      <template v-for="w in widgets" :key="w.key">
        <div v-if="w.key === 'traffic'" class="chart-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">
            <span>流量趋势</span>
            <el-radio-group v-model="trafficHours" size="small" @change="loadTraffic">
              <el-radio-button :value="1">1h</el-radio-button>
              <el-radio-button :value="6">6h</el-radio-button>
              <el-radio-button :value="24">24h</el-radio-button>
            </el-radio-group>
          </div>
          <BaseChart :option="trafficOption" height="280px" />
        </div>

        <div v-else-if="w.key === 'status'" class="chart-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">设备状态分布</div>
          <BaseChart :option="statusOption" height="280px" />
        </div>

        <div v-else-if="w.key === 'cpu'" class="chart-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">CPU 使用率 Top 10</div>
          <BaseChart :option="cpuOption" height="300px" />
        </div>

        <div v-else-if="w.key === 'mem'" class="chart-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">内存使用率 Top 10</div>
          <BaseChart :option="memOption" height="300px" />
        </div>

        <div v-else-if="w.key === 'vendor'" class="chart-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">厂商分布</div>
          <BaseChart :option="vendorOption" height="300px" />
        </div>

        <div v-else-if="w.key === 'alerts'" class="table-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">
            <span>最近告警</span>
            <el-link type="primary" :underline="false" @click="$router.push('/alerts')">查看全部</el-link>
          </div>
          <el-table border :data="overview.recent_alerts || []" size="small" :max-height="280">
            <el-table-column label="级别" width="80">
              <template #default="{ row }">
                <el-tag :type="severityType(row.severity)" size="small" effect="plain">
                  {{ severityText(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="告警内容" show-overflow-tooltip />
            <el-table-column prop="device_name" label="设备" width="120" show-overflow-tooltip />
            <el-table-column label="时间" width="150">
              <template #default="{ row }">{{ shortTime(row.fired_at) }}</template>
            </el-table-column>
          </el-table>
        </div>

        <div v-else-if="w.key === 'stores'" class="table-card" :style="{ flex: sizeFlex(w.size) }">
          <div class="card-title">
            <span>门店设备分布</span>
            <el-link type="primary" :underline="false" @click="$router.push('/stores')">门店管理</el-link>
          </div>
          <el-table border :data="storeRows" size="small" :max-height="280">
            <el-table-column prop="name" label="门店" />
            <el-table-column prop="count" label="设备数" width="90" align="right" />
            <el-table-column label="占比" width="130">
              <template #default="{ row }">
                <el-progress :percentage="row.percent" :stroke-width="10" />
              </template>
            </el-table-column>
          </el-table>
        </div>
      </template>
    </div>

    <!-- 自定义配置 -->
    <el-dialog v-model="configVisible" title="自定义总览大屏" width="620px">
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px">
        <template #title>勾选需要显示的模块，调整顺序与宽度，配置保存在本地浏览器</template>
      </el-alert>
      <el-table border :data="dashStore.allWidgets" size="small">
        <el-table-column label="显示" width="70" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="isVisible(row.key)"
              @change="(v) => dashStore.setVisible(row.key, v)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="title" label="模块" width="130" />
        <el-table-column prop="desc" label="说明" min-width="200" show-overflow-tooltip />
        <el-table-column label="宽度" width="150">
          <template #default="{ row }">
            <el-select
              :model-value="sizeOf(row.key)"
              size="small"
              style="width: 100%"
              @change="(v) => dashStore.setSize(row.key, v)"
            >
              <el-option label="1/4 宽" value="quarter" />
              <el-option label="1/2 宽" value="half" />
              <el-option label="整行" value="full" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="排序" width="100" align="center">
          <template #default="{ row }">
            <el-button link size="small" :icon="Top" @click="dashStore.move(row.key, 'up')" />
            <el-button link size="small" :icon="Bottom" @click="dashStore.move(row.key, 'down')" />
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="dashStore.reset()">恢复默认</el-button>
        <el-button type="primary" @click="configVisible = false">完成</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script>
export default { name: 'DashboardView' }
</script>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Bottom, Refresh, Setting, Top } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { deviceApi, monitorApi, netflowApi } from '../api'
import { useDashboardStore } from '../store/dashboard'
import { useChartTheme } from '../utils/chartTheme'

const dashStore = useDashboardStore()
const overview = ref({})
const flowStat = ref({})
const stores = ref([])
const storeId = ref(null)
const trafficHours = ref(6)
const trafficData = ref([])
const configVisible = ref(false)
let timer = null

const widgets = computed(() => dashStore.visibleWidgets)

function has(key) {
  return widgets.value.some((w) => w.key === key)
}
function isVisible(key) {
  const item = dashStore.layout.find((i) => i.key === key)
  return item ? item.visible !== false : true
}
function sizeOf(key) {
  const item = dashStore.layout.find((i) => i.key === key)
  return item?.size || 'quarter'
}
function sizeFlex(size) {
  return { full: '1 1 100%', half: '1 1 460px', quarter: '1 1 300px' }[size] || '1 1 300px'
}

function fmtNum(v) {
  if (v === null || v === undefined) return '0'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
function fmtBytes(n) {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}
function severityType(s) {
  return { critical: 'danger', crit: 'danger', warning: 'warning', info: 'info' }[s] || 'info'
}
function severityText(s) {
  return { critical: '严重', crit: '严重', warning: '警告', info: '提示' }[s] || s
}
function shortTime(t) {
  return t ? t.replace('T', ' ').slice(5, 16) : ''
}

async function load() {
  const params = storeId.value ? { store_id: storeId.value } : {}
  try {
    overview.value = await monitorApi.overview(params)
  } catch (e) {
    /* 忽略 */
  }
  try {
    flowStat.value = await netflowApi.statistics({ hours: 24, ...params })
  } catch (e) {
    flowStat.value = {}
  }
  await loadTraffic()
}

async function loadTraffic() {
  try {
    const res = await monitorApi.trafficTimeseries({
      hours: trafficHours.value,
      store_id: storeId.value || undefined,
    })
    trafficData.value = res.items || []
  } catch (e) {
    trafficData.value = []
  }
}

// 当前选中的门店名——用于在页面上明确显示筛选状态
const currentStoreName = computed(
  () => stores.value.find((s) => s.id === storeId.value)?.name || '',
)

const storeRows = computed(() => {  const data = overview.value.by_store || {}
  const total = Object.values(data).reduce((a, b) => a + b, 0) || 1
  return Object.entries(data)
    .map(([name, count]) => ({ name, count, percent: Math.round((count / total) * 100) }))
    .sort((a, b) => b.count - a.count)
})

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

const trafficOption = computed(() => {
  const times = trafficData.value.map((i) => i.ts.slice(5, 16))
  return {
    color: ['#2563eb', '#16a34a'],
    tooltip: { ...TIP.value, trigger: 'axis', valueFormatter: (v) => `${v} Mbps` },
    legend: { data: ['入向', '出向'], right: 0, textStyle: AX.value },
    grid: { left: 68, right: 24, top: 34, bottom: 28 },
    xAxis: {
      type: 'category',
      data: times,
      axisLabel: AX.value,
      axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
    },
    yAxis: { type: 'value', name: 'Mbps', axisLabel: AX.value, splitLine: SPL.value },
    series: [
      {
        name: '入向',
        type: 'line',
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.12 },
        data: trafficData.value.map((i) => i.in_mbps),
      },
      {
        name: '出向',
        type: 'line',
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.12 },
        data: trafficData.value.map((i) => i.out_mbps),
      },
    ],
  }
})

const statusOption = computed(() => {
  const o = overview.value
  const data = [
    { name: '在线', value: o.online ?? 0, itemStyle: { color: '#16a34a' } },
    { name: '离线', value: o.offline ?? 0, itemStyle: { color: '#dc2626' } },
    { name: '未知', value: o.unknown ?? 0, itemStyle: { color: '#94a3b8' } },
  ].filter((i) => i.value > 0)
  return {
    tooltip: { ...TIP.value, trigger: 'item' },
    legend: { bottom: 0, textStyle: AX.value },
    series: [
      {
        type: 'pie',
        radius: ['48%', '70%'],
        center: ['50%', '45%'],
        label: { formatter: '{b}: {c}\n{d}%', fontSize: 12, color: '#e5eaf3' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e5e7eb' } }],
      },
    ],
  }
})

function barOption(items, color) {
  const sorted = [...(items || [])].sort((a, b) => a.value - b.value)
  return {
    tooltip: {
      ...TIP.value,
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (p) => `${p[0].name}<br/>${p[0].value}%`,
    },
    grid: { left: 100, right: 40, top: 10, bottom: 24 },
    xAxis: { type: 'value', max: 100, axisLabel: { ...AX.value, formatter: '{value}%' }, splitLine: SPL.value },
    yAxis: {
      type: 'category',
      data: sorted.map((i) => i.name),
      axisLabel: { ...AX.value, width: 90, overflow: 'truncate' },
      axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        itemStyle: { color, borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', formatter: '{c}%', fontSize: 11, color: AX.value.color },
        data: sorted.map((i) => i.value),
      },
    ],
  }
}

const cpuOption = computed(() => barOption(overview.value.cpu_top, '#f59e0b'))
const memOption = computed(() => barOption(overview.value.mem_top, '#8b5cf6'))

const vendorOption = computed(() => {
  const data = Object.entries(overview.value.by_vendor || {}).map(([name, value]) => ({ name, value }))
  return {
    tooltip: { ...TIP.value, trigger: 'item' },
    legend: { bottom: 0, textStyle: AX.value, type: 'scroll' },
    series: [
      {
        type: 'pie',
        radius: ['42%', '66%'],
        center: ['50%', '44%'],
        label: { formatter: '{b}\n{c}', fontSize: 12, color: '#e5eaf3' },
        data: data.length ? data : [{ name: '暂无设备', value: 1, itemStyle: { color: '#e5e7eb' } }],
      },
    ],
  }
})

onMounted(async () => {
  try {
    const res = await deviceApi.stores()
    stores.value = res.items || []
  } catch (e) {
    /* 忽略 */
  }
  await load()
  timer = setInterval(load, 30000)
  window.addEventListener('netops:refresh', load)
})

onUnmounted(() => {
  timer && clearInterval(timer)
  window.removeEventListener('netops:refresh', load)
})
</script>

<style scoped>
.unit {
  font-size: 13px;
  font-weight: 400;
  color: var(--netops-text-muted);
  margin-left: 3px;
}
</style>
