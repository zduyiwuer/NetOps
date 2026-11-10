<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        {{ device.name || '设备详情' }}
        <el-tag :type="statusType(device.status)" size="small" effect="dark">{{ statusText(device.status) }}</el-tag>
        <span class="sub mono">{{ device.ip }}</span>
      </h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="loadAll">刷新</el-button>
        <el-button :icon="Cpu" :loading="collecting" @click="collect">立即采集</el-button>
        <el-button v-if="userStore.canOperate" :icon="FolderOpened" :loading="backingUp" @click="backup">配置备份</el-button>
        <el-button :icon="Back" @click="$router.back()">返回</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Cpu /></el-icon>CPU 使用率</div>
        <div class="value" :class="cpuClass">{{ fmt(metric.cpu_usage) }}<span class="unit">%</span></div>
        <div class="foot">厂商：{{ device.vendor_label || device.vendor }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Coin /></el-icon>内存使用率</div>
        <div class="value" :class="memClass">{{ fmt(metric.mem_usage) }}<span class="unit">%</span></div>
        <div class="foot">总内存 {{ fmt(metric.mem_total_mb) }} MB</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Odometer /></el-icon>运行时长</div>
        <div class="value" style="font-size: 20px">{{ uptimeText }}</div>
        <div class="foot">SNMP 响应 {{ fmt(metric.response_ms) }} ms</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><DataLine /></el-icon>接口数量</div>
        <div class="value">{{ interfaces.length }}</div>
        <div class="foot">
          UP <b class="text-ok">{{ upCount }}</b> ·
          Down <b class="text-danger">{{ downCount }}</b>
          <span v-if="ifaceStatus !== 'all'"> · 当前显示 {{ filteredInterfaces.length }}</span>
        </div>
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="chart-card" style="flex: 1; min-width: 420px">
        <div class="card-title">
          <span>CPU / 内存趋势</span>
          <el-radio-group v-model="hours" size="small" @change="loadMetrics">
            <el-radio-button :value="1">1h</el-radio-button>
            <el-radio-button :value="6">6h</el-radio-button>
            <el-radio-button :value="24">24h</el-radio-button>
            <el-radio-button :value="72">3天</el-radio-button>
          </el-radio-group>
        </div>
        <BaseChart :option="metricOption" height="280px" />
      </div>
      <div class="chart-card" style="flex: 1; min-width: 420px">
        <div class="card-title">接口流量速率（Top 8）</div>
        <BaseChart :option="ifaceOption" height="280px" />
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="table-card" style="flex: 2; min-width: 520px">
        <div class="card-title">
          <span>接口列表</span>
          <div class="iface-filter">
            <el-radio-group v-model="ifaceStatus" size="small">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="up">只看 UP</el-radio-button>
              <el-radio-button value="down">只看 DOWN</el-radio-button>
            </el-radio-group>
            <el-input
              v-model="ifaceKeyword"
              placeholder="搜索接口名"
              clearable
              size="small"
              style="width: 150px"
              :prefix-icon="Search"
            />
          </div>
        </div>
        <el-table :data="filteredInterfaces" size="small" stripe max-height="380" empty-text="没有符合筛选条件的接口">
          <el-table-column prop="if_index" label="Index" width="65" />
          <el-table-column prop="if_name" label="接口名" min-width="180" show-overflow-tooltip />
          <el-table-column label="速率" width="90" align="right">
            <template #default="{ row }">{{ row.speed_mbps }} Mbps</template>
          </el-table-column>
          <el-table-column label="状态" width="80" align="center">
            <template #default="{ row }">
              <el-tag :type="row.oper_status === 1 ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.oper_status === 1 ? 'UP' : 'DOWN' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="入向" width="110" align="right">
            <template #default="{ row }">{{ row.in_mbps }} Mbps</template>
          </el-table-column>
          <el-table-column label="出向" width="110" align="right">
            <template #default="{ row }">{{ row.out_mbps }} Mbps</template>
          </el-table-column>
          <el-table-column label="利用率" width="130">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.min(100, row.utilization)"
                :status="row.utilization > 85 ? 'exception' : row.utilization > 60 ? 'warning' : 'success'"
                :stroke-width="9"
              />
            </template>
          </el-table-column>
          <el-table-column label="错包" width="90" align="right">
            <template #default="{ row }">
              <span :class="row.in_errors + row.out_errors > 0 ? 'text-danger' : ''">
                {{ row.in_errors + row.out_errors }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="table-card" style="flex: 1; min-width: 320px">
        <div class="card-title">设备信息</div>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item label="设备名称">{{ device.name }}</el-descriptions-item>
          <el-descriptions-item label="管理 IP"><span class="mono">{{ device.ip }}</span></el-descriptions-item>
          <el-descriptions-item label="厂商">{{ device.vendor_label || device.vendor }}</el-descriptions-item>
          <el-descriptions-item label="型号">{{ device.model || '-' }}</el-descriptions-item>
          <el-descriptions-item label="序列号"><span class="mono">{{ device.serial_number || '-' }}</span></el-descriptions-item>
          <el-descriptions-item label="所属门店">{{ device.store_name || '未分配' }}</el-descriptions-item>
          <el-descriptions-item label="位置">{{ device.location || '-' }}</el-descriptions-item>
          <el-descriptions-item label="SNMP">{{ device.snmp_version }} / {{ device.snmp_community }}</el-descriptions-item>
          <el-descriptions-item label="资产编号"><span class="mono">{{ device.asset_tag || '-' }}</span></el-descriptions-item>
          <el-descriptions-item label="固件版本">{{ device.firmware_version || '-' }}</el-descriptions-item>
          <el-descriptions-item label="保修到期">{{ device.warranty_end || '-' }}</el-descriptions-item>
          <el-descriptions-item label="最后在线">{{ device.last_seen || '-' }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, Cpu, FolderOpened, Refresh, Search } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { backupApi, deviceApi, monitorApi } from '../api'
import { useUserStore } from '../store/user'

const route = useRoute()
const userStore = useUserStore()
const deviceId = computed(() => Number(route.params.id))

const device = ref({})
const metric = ref({})
const metrics = ref([])
const interfaces = ref([])
const hours = ref(6)
const collecting = ref(false)
const backingUp = ref(false)

function fmt(v) {
  if (v === null || v === undefined) return '-'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 1 })
}
function statusType(s) {
  return { online: 'success', offline: 'danger', unknown: 'info' }[s] || 'info'
}
function statusText(s) {
  return { online: '在线', offline: '离线', unknown: '未知' }[s] || s
}
const cpuClass = computed(() => {
  const v = metric.value.cpu_usage
  return v === null || v === undefined ? '' : v > 80 ? 'text-danger' : v > 60 ? 'text-warn' : 'text-ok'
})
const memClass = computed(() => {
  const v = metric.value.mem_usage
  return v === null || v === undefined ? '' : v > 85 ? 'text-danger' : v > 70 ? 'text-warn' : 'text-ok'
})
const downCount = computed(() => interfaces.value.filter((i) => i.oper_status !== 1).length)
const upCount = computed(() => interfaces.value.filter((i) => i.oper_status === 1).length)

// 接口列表筛选：状态（全部 / 只看 UP / 只看 DOWN）+ 接口名关键字
const ifaceStatus = ref('all')
const ifaceKeyword = ref('')

const filteredInterfaces = computed(() => {
  const kw = ifaceKeyword.value.trim().toLowerCase()
  return interfaces.value.filter((i) => {
    if (ifaceStatus.value === 'up' && i.oper_status !== 1) return false
    if (ifaceStatus.value === 'down' && i.oper_status === 1) return false
    if (kw) {
      const name = String(i.if_name || '').toLowerCase()
      const descr = String(i.if_descr || '').toLowerCase()
      const alias = String(i.if_alias || '').toLowerCase()
      if (!name.includes(kw) && !descr.includes(kw) && !alias.includes(kw)) return false
    }
    return true
  })
})

const uptimeText = computed(() => {
  const s = metric.value.uptime_sec
  if (!s) return '-'
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d > 0) return `${d} 天 ${h} 小时`
  if (h > 0) return `${h} 小时 ${m} 分`
  return `${m} 分钟`
})

async function loadDevice() {
  try {
    const res = await deviceApi.get(deviceId.value)
    device.value = res
    metric.value = res.latest_metric || {}
  } catch (e) {
    /* 忽略 */
  }
}

async function loadInterfaces() {
  try {
    const res = await monitorApi.deviceInterfaces(deviceId.value)
    interfaces.value = res.items || []
  } catch (e) {
    interfaces.value = []
  }
}

async function loadMetrics() {
  try {
    const res = await monitorApi.deviceMetrics(deviceId.value, hours.value)
    metrics.value = res.items || []
  } catch (e) {
    metrics.value = []
  }
}

async function loadAll() {
  await Promise.all([loadDevice(), loadInterfaces(), loadMetrics()])
}

async function collect() {
  collecting.value = true
  try {
    const res = await deviceApi.collect(deviceId.value)
    if (res.ok) {
      ElMessage.success(`采集成功 · CPU ${res.cpu_usage ?? '-'}% · 内存 ${res.mem_usage ?? '-'}%`)
    } else {
      ElMessage.warning(`采集失败：${res.error}`)
    }
    await loadAll()
  } catch (e) {
    /* 忽略 */
  } finally {
    collecting.value = false
  }
}

async function backup() {
  backingUp.value = true
  try {
    const res = await backupApi.create(deviceId.value)
    if (res.ok) {
      ElMessage.success(`备份成功：${res.backup?.filename || ''}`)
    } else {
      ElMessage.warning(res.message || '备份失败')
    }
  } catch (e) {
    /* 忽略 */
  } finally {
    backingUp.value = false
  }
}

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

const metricOption = computed(() => {
  const times = metrics.value.map((m) => m.ts.replace('T', ' ').slice(5, 16))
  return {
    color: ['#f59e0b', '#8b5cf6'],
    tooltip: { trigger: 'axis', valueFormatter: (v) => (v === null ? '-' : `${v}%`) },
    legend: { data: ['CPU', '内存'], right: 0, textStyle: AXIS },
    grid: { left: 46, right: 20, top: 32, bottom: 28 },
    xAxis: { type: 'category', data: times, axisLabel: AXIS, axisLine: { lineStyle: { color: '#e5e7eb' } } },
    yAxis: { type: 'value', max: 100, axisLabel: { ...AXIS, formatter: '{value}%' }, splitLine: SPLIT },
    series: [
      { name: 'CPU', type: 'line', smooth: true, showSymbol: false, data: metrics.value.map((m) => m.cpu_usage) },
      { name: '内存', type: 'line', smooth: true, showSymbol: false, data: metrics.value.map((m) => m.mem_usage) },
    ],
  }
})

const ifaceOption = computed(() => {
  const top = [...interfaces.value]
    .sort((a, b) => Math.max(b.in_bps, b.out_bps) - Math.max(a.in_bps, a.out_bps))
    .slice(0, 8)
  const names = top.map((i) => i.if_name || i.if_descr)
  return {
    color: ['#2563eb', '#16a34a'],
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v} Mbps` },
    legend: { data: ['入向', '出向'], right: 0, textStyle: AXIS },
    grid: { left: 130, right: 30, top: 30, bottom: 24 },
    xAxis: { type: 'value', axisLabel: AXIS, splitLine: SPLIT },
    yAxis: {
      type: 'category',
      data: names,
      axisLabel: { ...AXIS, width: 120, overflow: 'truncate' },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
    series: [
      { name: '入向', type: 'bar', barWidth: 8, data: top.map((i) => i.in_mbps) },
      { name: '出向', type: 'bar', barWidth: 8, data: top.map((i) => i.out_mbps) },
    ],
  }
})

onMounted(async () => {
  await loadAll()
  window.addEventListener('netops:refresh', loadAll)
})
onUnmounted(() => window.removeEventListener('netops:refresh', loadAll))
</script>

<style scoped>
.unit {
  font-size: 13px;
  font-weight: 400;
  color: var(--netops-text-muted);
  margin-left: 3px;
}

.iface-filter {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-title {
  flex-wrap: wrap;
  gap: 8px;
}
</style>
