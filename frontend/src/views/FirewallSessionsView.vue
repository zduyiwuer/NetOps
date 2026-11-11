<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        防火墙会话
        <span class="sub">防火墙实时会话数与并发情况</span>
      </h2>
      <div class="toolbar">
        <el-radio-group v-model="hours" size="small" @change="load">
          <el-radio-button :value="1">最近数据</el-radio-button>
          <el-radio-button :value="6">6 小时</el-radio-button>
          <el-radio-button :value="24">24 小时</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 汇总卡片 -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Share /></el-icon>防火墙设备</div>
        <div class="value" style="font-size: 20px">{{ items.length }}</div>
        <div class="foot">已启用并纳入监控</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Connection /></el-icon>会话总数</div>
        <div class="value" style="font-size: 20px">{{ totalSessions }}</div>
        <div class="foot">所有防火墙当前会话/连接数之和</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><TrendCharts /></el-icon>最高单台</div>
        <div class="value" style="font-size: 20px">{{ maxSession }}</div>
        <div class="foot">{{ maxSessionDevice || '暂无数据' }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Warning /></el-icon>离线设备</div>
        <div class="value" style="font-size: 20px" :class="offlineCount ? 'text-danger' : 'text-ok'">
          {{ offlineCount }}
        </div>
        <div class="foot">无法采集会话数据</div>
      </div>
    </div>

    <!-- 设备会话一览 -->
    <div class="table-card" style="margin-top: 12px">
      <div class="card-title">
        <span>各防火墙会话数</span>
        <span class="text-muted" style="font-size: 12px">
          数据来自设备采集时的厂商专属指标，采集后自动更新
        </span>
      </div>
      <el-table v-loading="loading" :data="items" size="small" stripe empty-text="暂无防火墙设备">
        <el-table-column label="设备名称" min-width="160">
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="$router.push(`/device/${row.device_id}`)">
              {{ row.name }}
            </el-link>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="管理 IP" width="140" class-name="mono" />
        <el-table-column prop="vendor_label" label="厂商" width="160">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.vendor === 'fortinet' ? 'danger' : 'warning'">
              {{ row.vendor_label || row.vendor }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="当前会话/连接数" width="140" align="center">
          <template #default="{ row }">
            <span v-if="row.value != null" class="session-value">{{ row.value }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="并发峰值" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.peak != null">{{ row.peak }}</span>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="dark" :type="statusType(row.status)">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="采集时间" width="160">
          <template #default="{ row }">{{ row.ts ? fmtTime(row.ts) : '-' }}</template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 单设备会话趋势 -->
    <div v-if="trendDevice" class="table-card" style="margin-top: 12px">
      <div class="card-title">
        <span>{{ trendDevice.name }} · 会话趋势</span>
        <span class="text-muted" style="font-size: 12px">{{ trendDevice.metric_label }}</span>
      </div>
      <BaseChart :option="trendOption" height="280px" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Connection, Refresh, Share, TrendCharts, Warning } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { monitorApi } from '../api'

const items = ref([])
const loading = ref(false)
const hours = ref(1)
const trend = ref({ items: [], metric_key: 'session_count', metric_label: '会话数' })
const trendDevice = ref(null)

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

const totalSessions = computed(() =>
  items.value.reduce((sum, r) => sum + (r.value ?? 0), 0),
)
const maxSession = computed(() => {
  const vals = items.value.filter((r) => r.value != null).map((r) => r.value)
  // 空数组时 Math.max 返回 -Infinity，需显式兜底为 0
  return vals.length ? Math.max(...vals) : 0
})
const maxSessionDevice = computed(() => {
  const withVal = items.value.filter((r) => r.value != null)
  if (!withVal.length) return ''
  // 带初始值，避免空数组 reduce 抛错
  return withVal.reduce((a, b) => (a.value >= b.value ? a : b), withVal[0]).name
})
const offlineCount = computed(() => items.value.filter((r) => r.status === 'offline').length)

const trendOption = computed(() => {
  const rows = trend.value.items || []
  const key = trend.value.metric_key || 'session_count'
  const times = rows.map((p) => p.ts.replace('T', ' ').slice(5, 16))
  // 注意：所有候选项统一用 label 字段（与其它页面一致），
  // 否则 s.label 取到 undefined，ECharts 处理 series.name 时会报错
  const series = [
    { label: trend.value.metric_label || '会话数', key, color: '#f59e0b' },
    { label: '并发峰值', key: 'conn_peak', color: '#ef4444' },
    { label: '登录用户', key: 'user_count', color: '#8b5cf6' },
    { label: 'VPN 隧道', key: 'vpn_tunnel_count', color: '#10b981' },
  ].filter((s) => rows.some((p) => p[s.key] != null))

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: series.map((s) => s.label), right: 0, textStyle: AXIS },
    grid: { left: 52, right: 20, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: times,
      axisLabel: AXIS,
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
    yAxis: { type: 'value', axisLabel: AXIS, splitLine: SPLIT },
    series: series.map((s) => ({
      name: s.label,
      type: 'line',
      smooth: true,
      showSymbol: false,
      itemStyle: { color: s.color },
      data: rows.map((p) => p[s.key] ?? null),
    })),
  }
})

function statusType(s) {
  return { online: 'success', offline: 'danger', unknown: 'info' }[s] || 'info'
}
function statusText(s) {
  return { online: '在线', offline: '离线', unknown: '未知' }[s] || s
}
function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function load() {
  loading.value = true
  try {
    const res = await monitorApi.firewallSessions(hours.value)
    items.value = res.items || []
    // 默认展示会话数最多的那台设备
    const top = items.value
      .filter((r) => r.value != null)
      .sort((a, b) => b.value - a.value)[0]
    if (top) {
      await loadTrend(top)
    } else {
      trendDevice.value = null
      trend.value = { items: [] }
    }
  } catch (e) {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function loadTrend(row) {
  try {
    const res = await monitorApi.deviceSessions(row.device_id, hours.value)
    trend.value = res || { items: [] }
    trendDevice.value = row
  } catch (e) {
    trend.value = { items: [] }
  }
}

onMounted(load)
onUnmounted(() => {})
</script>

<style scoped>
.session-value {
  font-size: 18px;
  font-weight: 600;
  color: var(--netops-primary);
}
</style>
