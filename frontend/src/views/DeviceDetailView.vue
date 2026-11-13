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
      <!-- 无线控制器：AP 与客户端 -->
      <div v-if="isWireless" class="stat-card">
        <div class="label"><el-icon><Connection /></el-icon>无线 AP</div>
        <div class="value" style="font-size: 20px">{{ wirelessAps.length }}</div>
        <div class="foot">
          已注册 {{ registeredApCount }} 个 ·
          {{ apModels.length }} 种型号
        </div>
      </div>
      <div v-if="isWireless" class="stat-card">
        <div class="label"><el-icon><User /></el-icon>关联客户端</div>
        <div class="value" style="font-size: 20px">{{ wirelessClients.length }}</div>
        <div class="foot">
          {{ clientSsids.length }} 个 SSID ·
          {{ clientApCount }} 个 AP 有客户端
        </div>
      </div>
      <!-- 防火墙实时会话数 -->
      <div v-if="isFirewall" class="stat-card">
        <div class="label"><el-icon><Connection /></el-icon>{{ sessionLabel }}</div>
        <div class="value" style="font-size: 20px">
          {{ sessions.current != null ? sessions.current : '-' }}
        </div>
        <div class="foot">
          <span v-if="sessions.latest?.conn_peak != null">
            峰值 {{ sessions.latest.conn_peak }} ·
          </span>
          <span v-if="sessions.latest?.user_count != null">
            用户 {{ sessions.latest.user_count }} ·
          </span>
          <span v-if="sessions.latest?.vpn_tunnel_count != null">
            VPN 隧道 {{ sessions.latest.vpn_tunnel_count }}
          </span>
          <span v-if="!sessions.latest || !Object.keys(sessions.latest).length">
            暂无会话数据
          </span>
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
      <!-- 接口流量趋势（可按接口过滤） -->
      <div class="chart-card" style="flex: 1; min-width: 420px">
        <div class="card-title">
          <span>接口流量趋势</span>
          <div class="iface-filter">
            <el-select
              v-model="trendIfIndex"
              placeholder="自动（流量最大）"
              clearable
              size="small"
              style="width: 190px"
              @change="loadIfaceHistory"
            >
              <el-option
                v-for="i in trendInterfaces"
                :key="i.if_index"
                :label="i.if_name || `接口 ${i.if_index}`"
                :value="i.if_index"
              />
            </el-select>
            <el-select v-model="trendHours" size="small" style="width: 100px" @change="loadIfaceHistory">
              <el-option :value="1" label="近 1 小时" />
              <el-option :value="6" label="近 6 小时" />
              <el-option :value="24" label="近 24 小时" />
              <el-option :value="72" label="近 3 天" />
            </el-select>
          </div>
        </div>
        <BaseChart :option="ifaceTrendOption" height="280px" />
      </div>
      <!-- 防火墙会话趋势 -->
      <div v-if="isFirewall" class="chart-card" style="flex: 1; min-width: 420px">
        <div class="card-title">
          <span>防火墙会话趋势</span>
          <span class="text-muted" style="font-size: 12px">{{ sessionLabel }}</span>
        </div>
        <BaseChart :option="sessionOption" height="280px" />
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

    <!-- 无线数据：AP 与客户端 -->
    <template v-if="isWireless">
      <div class="table-card" style="margin-top: 12px">
        <div class="card-title">
          <span>无线 AP</span>
          <div class="iface-filter">
            <el-input
              v-model="apKeyword"
              placeholder="搜索 AP 名称 / 型号 / IP"
              clearable
              size="small"
              style="width: 200px"
              :prefix-icon="Search"
              @keyup.enter="loadWireless"
            />
            <el-button size="small" @click="loadWireless">查询</el-button>
          </div>
        </div>
        <el-table
          v-loading="wirelessLoading"
          :data="wirelessAps"
          size="small"
          stripe
          max-height="420"
          empty-text="暂无 AP 数据"
        >
          <el-table-column prop="name" label="AP 名称" min-width="130" show-overflow-tooltip />
          <el-table-column prop="model" label="型号" width="110" show-overflow-tooltip />
          <el-table-column prop="ip" label="IP 地址" width="135" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="client_count" label="客户端数" width="90" align="center" sortable>
            <template #default="{ row }">
              <el-tag v-if="row.client_count > 0" size="small" effect="plain" type="success">
                {{ row.client_count }}
              </el-tag>
              <span v-else class="text-muted">0</span>
            </template>
          </el-table-column>
          <el-table-column prop="channels" label="信道" width="140" class-name="mono" show-overflow-tooltip>
            <template #default="{ row }">
              <span v-if="row.channels">{{ row.channels }}</span>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="radio_mac" label="Radio MAC" width="135" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="serial_number" label="序列号" width="130" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="location" label="位置" min-width="120" show-overflow-tooltip />
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.status === 1 ? 'success' : 'danger'">
                {{ row.state || row.status }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="table-card" style="margin-top: 12px">
        <div class="card-title">
          <span>无线客户端</span>
          <div class="iface-filter">
            <el-select
              v-model="clientApFilter"
              placeholder="全部 AP"
              clearable
              filterable
              size="small"
              style="width: 160px"
              @change="loadWireless"
            >
              <el-option
                v-for="a in wirelessAps"
                :key="a.name"
                :label="a.name"
                :value="a.name"
              />
            </el-select>
            <el-select
              v-model="clientSsidFilter"
              placeholder="全部 SSID"
              clearable
              filterable
              size="small"
              style="width: 160px"
              @change="loadWireless"
            >
              <el-option v-for="s in clientSsids" :key="s" :label="s" :value="s" />
            </el-select>
            <el-input
              v-model="clientKeyword"
              placeholder="搜索 MAC / AP / SSID"
              clearable
              size="small"
              style="width: 190px"
              :prefix-icon="Search"
              @keyup.enter="loadWireless"
            />
            <el-button size="small" @click="loadWireless">查询</el-button>
          </div>
        </div>
        <el-table
          v-loading="wirelessLoading"
          :data="wirelessClients"
          size="small"
          stripe
          max-height="420"
          empty-text="暂无客户端数据"
        >
          <el-table-column prop="mac" label="MAC 地址" width="135" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="ip" label="IP 地址" width="130" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="ap_name" label="所属 AP" min-width="115" show-overflow-tooltip />
          <el-table-column prop="ssid" label="SSID" min-width="130" show-overflow-tooltip />
          <el-table-column label="信道" width="70" align="center">
            <template #default="{ row }">
              <span class="mono">{{ row.channel || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="RSSI" width="80" align="center">
            <template #default="{ row }">
              <span :class="rssiClass(row.rssi)">{{ row.rssi ? row.rssi + ' dBm' : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="SNR" width="70" align="center">
            <template #default="{ row }">
              <span class="mono">{{ row.snr ? row.snr + ' dB' : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="已收 / 已发" width="150" align="right">
            <template #default="{ row }">
              <span class="text-muted" style="font-size: 12px">
                ↓{{ fmtBytes(row.bytes_in) }} / ↑{{ fmtBytes(row.bytes_out) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="state" label="状态" width="85" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.state === 'Run' ? 'success' : 'info'">
                {{ row.state }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="table-card" style="margin-top: 12px">
        <div class="card-title">
          <span>漫游记录</span>
          <div class="iface-filter">
            <el-select v-model="roamingHours" size="small" style="width: 120px" @change="loadRoaming">
              <el-option :value="1" label="近 1 小时" />
              <el-option :value="6" label="近 6 小时" />
              <el-option :value="24" label="近 24 小时" />
              <el-option :value="168" label="近 7 天" />
            </el-select>
            <el-checkbox v-model="roamingBandOnly" size="small" @change="applyRoamingFilter">
              只看跨频段
            </el-checkbox>
            <el-input
              v-model="roamingKeyword"
              placeholder="搜索 MAC / AP"
              clearable
              size="small"
              style="width: 180px"
              :prefix-icon="Search"
              @keyup.enter="loadRoaming"
            />
            <el-button size="small" @click="loadRoaming">查询</el-button>
          </div>
        </div>
        <el-table
          v-loading="roamingLoading"
          :data="filteredRoaming"
          size="small"
          stripe
          max-height="360"
          :empty-text="roamingKeyword || roamingBandOnly ? '没有符合条件的漫游记录' : '所选时间范围内没有漫游记录'"
        >
          <el-table-column prop="ts" label="时间" width="160">
            <template #default="{ row }">{{ (row.ts || '').replace('T', ' ').slice(0, 19) }}</template>
          </el-table-column>
          <el-table-column prop="mac" label="客户端 MAC" width="135" class-name="mono" show-overflow-tooltip />
          <el-table-column label="漫游路径" min-width="280">
            <template #default="{ row }">
              <span class="text-muted">{{ row.from_ap || row.from_bssid }}</span>
              <span style="margin: 0 6px">→</span>
              <span :class="row.from_ap !== row.to_ap ? 'text-ok' : ''">{{ row.to_ap || row.to_bssid }}</span>
              <el-tag v-if="isBandRoam(row)" size="small" type="warning" effect="plain" style="margin-left: 6px">
                跨频段
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="信道变化" width="120" align="center">
            <template #default="{ row }">
              <span class="mono">{{ row.from_channel || '-' }} → {{ row.to_channel || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="漫游后 RSSI" width="110" align="center">
            <template #default="{ row }">
              <span :class="rssiClass(row.rssi)">{{ row.rssi ? row.rssi + ' dBm' : '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Back, Connection, Cpu, FolderOpened, Refresh, Search, User } from '@element-plus/icons-vue'
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
  await loadSessions()
}

async function loadAll() {
  // 先加载设备信息——会话/无线数据依赖 device.vendor 判断设备类型
  await loadDevice()
  await Promise.all([loadInterfaces(), loadMetrics(), loadWireless(), loadRoaming()])
  // 接口列表就绪后再取趋势（要按流量挑接口）
  await loadIfaceHistory()
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

// ---------------------------------------------------------------- 防火墙会话
const sessions = ref({ current: null, latest: {}, items: [] })
// ASA 用连接数、飞塔用会话数，其余厂商不展示
const FIREWALL_VENDORS = ['cisco_asa', 'fortinet']
const isFirewall = computed(() => FIREWALL_VENDORS.includes(device.value?.vendor))
const sessionLabel = computed(() => {
  if (device.value?.vendor === 'cisco_asa') return '当前连接数'
  if (device.value?.vendor === 'fortinet') return '当前会话数'
  return '会话数'
})

async function loadSessions() {
  if (!isFirewall.value) return
  try {
    const res = await monitorApi.deviceSessions(deviceId.value, hours.value)
    sessions.value = res || { current: null, latest: {}, items: [] }
  } catch (e) {
    sessions.value = { current: null, latest: {}, items: [] }
  }
}

// ---------------------------------------------------------------- 接口流量趋势
const trendIfIndex = ref(null)
const trendHours = ref(6)
const ifaceHistory = ref([])

// 只列出有流量数据的接口（有速率或曾有历史），避免下拉里全是闲置口
const trendInterfaces = computed(() =>
  interfaces.value.filter((i) => i.in_bps > 0 || i.out_bps > 0 || i.oper_status === 1),
)

async function loadIfaceHistory() {
  if (!deviceId.value) return
  // 未指定接口时后端返回全部接口的历史——按接口分组后选流量最大的那条画曲线
  if (trendIfIndex.value == null) {
    const top = trendInterfaces.value
      .slice()
      .sort((a, b) => (b.in_bps + b.out_bps) - (a.in_bps + a.out_bps))[0]
    if (top) trendIfIndex.value = top.if_index
  }
  if (trendIfIndex.value == null) {
    ifaceHistory.value = []
    return
  }
  try {
    const res = await monitorApi.interfaceHistory(
      deviceId.value, trendIfIndex.value, trendHours.value,
    )
    ifaceHistory.value = res?.items || []
  } catch (e) {
    ifaceHistory.value = []
  }
}

const ifaceTrendOption = computed(() => {
  const rows = ifaceHistory.value
  const times = rows.map((r) => (r.ts || '').replace('T', ' ').slice(5, 16))
  const inM = rows.map((r) => Math.round(((r.in_bps || 0) / 1000000) * 100) / 100)
  const outM = rows.map((r) => Math.round(((r.out_bps || 0) / 1000000) * 100) / 100)
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['入向', '出向'], right: 0, textStyle: { color: '#6b7280', fontSize: 11 } },
    grid: { left: 52, right: 20, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: times,
      axisLabel: { color: '#6b7280', fontSize: 11 },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
    yAxis: {
      type: 'value',
      name: 'Mbps',
      axisLabel: { color: '#6b7280', fontSize: 11 },
      splitLine: { lineStyle: { color: '#eef1f5' } },
    },
    series: [
      {
        name: '入向',
        type: 'line',
        smooth: true,
        showSymbol: false,
        itemStyle: { color: '#10b981' },
        areaStyle: { color: 'rgba(16,185,129,0.08)' },
        data: inM,
      },
      {
        name: '出向',
        type: 'line',
        smooth: true,
        showSymbol: false,
        itemStyle: { color: '#3b82f6' },
        areaStyle: { color: 'rgba(59,130,246,0.08)' },
        data: outM,
      },
    ],
  }
})

// ---------------------------------------------------------------- 无线数据
const wirelessAps = ref([])
const wirelessClients = ref([])
const wirelessLoading = ref(false)
const apKeyword = ref('')
const clientKeyword = ref('')
const clientApFilter = ref('')
const clientSsidFilter = ref('')
const clientSsids = ref([])
const roamingEvents = ref([])
const roamingLoading = ref(false)
const roamingHours = ref(24)
const roamingKeyword = ref('')
const roamingBandOnly = ref(false)

// 漫游列表过滤：跨频段筛选在前端做（数据量小），文本筛选传后端
const filteredRoaming = computed(() => {
  if (!roamingBandOnly.value) return roamingEvents.value
  return roamingEvents.value.filter((r) => isBandRoam(r))
})

function applyRoamingFilter() {
  /* 跨频段是前端筛选，无需重新请求 */
}

const WIRELESS_VENDORS = ['cisco_wlc', 'aruba', 'h3c_wlc']
const isWireless = computed(() => WIRELESS_VENDORS.includes(device.value?.vendor))

const registeredApCount = computed(
  () => wirelessAps.value.filter((a) => a.status === 1).length,
)
const apModels = computed(
  () => [...new Set(wirelessAps.value.map((a) => a.model).filter(Boolean))],
)
const clientApCount = computed(
  () => new Set(wirelessClients.value.map((c) => c.ap_name).filter(Boolean)).size,
)

// RSSI 分级：>-65 优秀 / -65~-75 一般 / <-75 较弱
function rssiClass(rssi) {
  if (!rssi) return 'text-muted'
  if (rssi >= -65) return 'text-ok'
  if (rssi >= -75) return ''
  return 'text-danger'
}

// 跨频段漫游：2.4G（信道 1-14）与 5G（信道 34+）之间切换
function isBandRoam(row) {
  const a = row.from_channel || 0
  const b = row.to_channel || 0
  if (!a || !b) return false
  return (a <= 14 && b >= 34) || (a >= 34 && b <= 14)
}

function fmtBytes(n) {
  if (!n) return '0'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

async function loadWireless() {
  if (!isWireless.value) return
  wirelessLoading.value = true
  try {
    const [apRes, cliRes] = await Promise.all([
      deviceApi.wirelessAps(deviceId.value, { keyword: apKeyword.value || undefined }),
      deviceApi.wirelessClients(deviceId.value, {
        keyword: clientKeyword.value || undefined,
        ap_name: clientApFilter.value || undefined,
        ssid: clientSsidFilter.value || undefined,
      }),
    ])
    wirelessAps.value = apRes?.items || []
    wirelessClients.value = cliRes?.items || []
    clientSsids.value = cliRes?.ssids || []
  } catch (e) {
    wirelessAps.value = []
    wirelessClients.value = []
  } finally {
    wirelessLoading.value = false
  }
}

async function loadRoaming() {
  if (!isWireless.value) return
  roamingLoading.value = true
  try {
    const res = await deviceApi.wirelessRoaming(deviceId.value, {
      hours: roamingHours.value,
      keyword: roamingKeyword.value || undefined,
    })
    roamingEvents.value = res?.items || []
  } catch (e) {
    roamingEvents.value = []
  } finally {
    roamingLoading.value = false
  }
}

const SESSION_SERIES = [
  { key: 'conn_count', label: '连接数', color: '#f59e0b' },
  { key: 'session_count', label: '会话数', color: '#f59e0b' },
  { key: 'conn_peak', label: '并发峰值', color: '#ef4444' },
  { key: 'user_count', label: '登录用户', color: '#8b5cf6' },
  { key: 'vpn_tunnel_count', label: 'VPN 隧道', color: '#10b981' },
]

const sessionOption = computed(() => {
  const items = sessions.value.items || []
  const times = items.map((p) => p.ts.replace('T', ' ').slice(5, 16))
  // 只画本次数据里实际出现过的指标
  const series = SESSION_SERIES.filter((s) => items.some((p) => p[s.key] != null)).map((s) => ({
    name: s.label,
    type: 'line',
    smooth: true,
    showSymbol: false,
    itemStyle: { color: s.color },
    data: items.map((p) => p[s.key] ?? null),
  }))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: series.map((s) => s.name), right: 0, textStyle: AXIS },
    grid: { left: 52, right: 20, top: 32, bottom: 28 },
    xAxis: { type: 'category', data: times, axisLabel: AXIS, axisLine: { lineStyle: { color: '#e5e7eb' } } },
    yAxis: { type: 'value', axisLabel: AXIS, splitLine: SPLIT },
    series,
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
