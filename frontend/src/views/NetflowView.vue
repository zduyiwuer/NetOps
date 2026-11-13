<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        NetFlow 流量分析
        <span class="sub">基于流记录的五元组会话分析</span>
      </h2>
      <div class="toolbar">
        <DeviceSelect v-model="deviceId" placeholder="全部设备" style="width: 190px" @change="reload" />
        <el-select v-model="hours" style="width: 130px" @change="reload">
          <el-option label="近 1 小时" :value="1" />
          <el-option label="近 6 小时" :value="6" />
          <el-option label="近 24 小时" :value="24" />
          <el-option label="近 7 天" :value="168" />
        </el-select>
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="!hasData" type="info" :closable="false" show-icon style="margin-bottom: 12px">
      <template #title>暂无流记录</template>
      尚未收到 NetFlow / IPFIX 导出报文。请在设备侧配置流量导出指向本平台
      （UDP 2055 / 4739 / 9995），或参考下方「导出器配置」完成设置。
    </el-alert>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Files /></el-icon>流记录数</div>
        <div class="value">{{ stats.total_flows ?? 0 }}</div>
        <div class="foot">近 {{ hours }} 小时</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Upload /></el-icon>总流量</div>
        <div class="value">{{ formatBytes(stats.total_bytes) }}</div>
        <div class="foot">{{ formatPackets(stats.total_packets) }} 包</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Connection /></el-icon>通信源 / 目的</div>
        <div class="value">{{ stats.unique_sources ?? 0 }} <span class="unit">/ {{ stats.unique_destinations ?? 0 }}</span></div>
        <div class="foot">唯一 IP 地址数</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Grid /></el-icon>应用协议数</div>
        <div class="value">{{ Object.keys(stats.by_application || {}).length }}</div>
        <div class="foot">按端口识别</div>
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="chart-card" style="flex: 1; min-width: 420px">
        <div class="card-title">流量趋势</div>
        <BaseChart :option="trendOption" height="300px" />
      </div>
      <div class="chart-card" style="flex: 1; min-width: 380px">
        <div class="card-title">应用分布 Top 10</div>
        <BaseChart :option="appOption" height="300px" />
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="table-card" style="flex: 1; min-width: 380px">
        <div class="card-title">
          <span>Top 通信源</span>
          <el-radio-group v-model="talkerDirection" size="small" @change="loadTalkers">
            <el-radio-button value="src">源</el-radio-button>
            <el-radio-button value="dst">目的</el-radio-button>
          </el-radio-group>
        </div>
        <el-table border :data="talkers" size="small" stripe max-height="320">
          <el-table-column prop="ip" label="IP 地址" min-width="140" class-name="mono" />
          <el-table-column label="流量" width="110" align="right">
            <template #default="{ row }">{{ formatBytes(row.bytes_total) }}</template>
          </el-table-column>
          <el-table-column prop="packets_total" label="包数" width="95" align="right" />
          <el-table-column prop="flow_count" label="会话数" width="85" align="right" />
        </el-table>
      </div>

      <div class="table-card" style="flex: 1; min-width: 380px">
        <div class="card-title">Top 会话对</div>
        <el-table border :data="conversations" size="small" stripe max-height="320">
          <el-table-column label="源 → 目的" min-width="220">
            <template #default="{ row }">
              <span class="mono">{{ row.src_ip }}</span>
              <el-icon class="text-muted"><Right /></el-icon>
              <span class="mono">{{ row.dst_ip }}</span>
            </template>
          </el-table-column>
          <el-table-column label="协议" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ protoName(row.protocol) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="流量" width="105" align="right">
            <template #default="{ row }">{{ formatBytes(row.bytes_total) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="table-card" style="margin-top: 12px">
      <div class="card-title">
        <span>流记录明细</span>
        <el-input
          v-model="keyword"
          placeholder="搜索源/目的 IP 或应用"
          clearable
          style="width: 220px"
          :prefix-icon="Search"
          @keyup.enter="loadFlows"
          @clear="loadFlows"
        />
      </div>
      <el-table border :data="flows" size="small" stripe max-height="420">
        <el-table-column prop="device_name" label="设备" width="130" show-overflow-tooltip />
        <el-table-column prop="exporter_ip" label="导出源" width="120" class-name="mono" />
        <el-table-column label="版本" width="80" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ versionLabel(row.version) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="源地址" min-width="180">
          <template #default="{ row }">
            <span class="mono">{{ row.src_ip }}</span>
            <span class="text-muted">:{{ row.src_port }}</span>
          </template>
        </el-table-column>
        <el-table-column label="目的地址" min-width="180">
          <template #default="{ row }">
            <span class="mono">{{ row.dst_ip }}</span>
            <span class="text-muted">:{{ row.dst_port }}</span>
          </template>
        </el-table-column>
        <el-table-column label="应用" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.application || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="流量" width="100" align="right">
          <template #default="{ row }">{{ row.kb }} KB</template>
        </el-table-column>
        <el-table-column prop="packets_total" label="包数" width="95" align="right" />
        <el-table-column prop="duration_sec" label="时长(s)" width="85" align="right" />
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 导出器配置 -->
    <div class="table-card" style="margin-top: 12px">
      <div class="card-title">
        <span>导出器配置（设备侧流量导出目标）</span>
        <el-button v-if="userStore.canOperate" type="primary" size="small" :icon="Plus" @click="openExporter()">
          新增导出器
        </el-button>
      </div>
      <el-table border :data="exporters" size="small" stripe>
        <el-table-column prop="name" label="名称" width="150" />
        <el-table-column prop="device_name" label="关联设备" width="140" show-overflow-tooltip />
        <el-table-column label="版本" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.version }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="listen_port" label="监听端口" width="100" align="right" />
        <el-table-column prop="source_ip" label="设备源地址" width="140" class-name="mono" />
        <el-table-column prop="remark" label="备注" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="userStore.canOperate" label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openExporter(row)">编辑</el-button>
            <el-button link type="danger" size="small" @click="removeExporter(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="exporterDialog" :title="exporterForm.id ? '编辑导出器' : '新增导出器'" width="520px">
      <el-form :model="exporterForm" label-width="110px" size="small">
        <el-form-item label="名称">
          <el-input v-model="exporterForm.name" placeholder="如：上海出口流量" />
        </el-form-item>
        <el-form-item label="关联设备">
          <DeviceSelect v-model="exporterForm.device_id" style="width: 100%" />
        </el-form-item>
        <el-form-item label="导出版本">
          <el-select v-model="exporterForm.version" style="width: 100%">
            <el-option label="NetFlow v5" value="v5" />
            <el-option label="NetFlow v9" value="v9" />
            <el-option label="IPFIX (v10)" value="ipfix" />
          </el-select>
        </el-form-item>
        <el-form-item label="监听端口">
          <el-select v-model="exporterForm.listen_port" style="width: 100%">
            <el-option label="2055 (NetFlow v9)" :value="2055" />
            <el-option label="4739 (IPFIX)" :value="4739" />
            <el-option label="9995 (NetFlow v5)" :value="9995" />
          </el-select>
        </el-form-item>
        <el-form-item label="设备源地址">
          <el-input v-model="exporterForm.source_ip" placeholder="设备发起导出的源 IP" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="exporterForm.remark" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="exporterForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exporterDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveExporter">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Files, Grid, Plus, Refresh, Right, Search, Upload } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { netflowApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const stats = ref({})
const talkers = ref([])
const conversations = ref([])
const flows = ref([])
const trend = ref([])
const exporters = ref([])
const deviceId = ref(null)
const hours = ref(6)
const keyword = ref('')
const talkerDirection = ref('src')
const loading = ref(false)
const saving = ref(false)

const exporterDialog = ref(false)
const exporterForm = reactive({})

const hasData = computed(() => (stats.value.total_flows || 0) > 0)

function formatBytes(n) {
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
function formatPackets(n) {
  if (!n) return '0'
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}G`
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`
  return String(n)
}
function protoName(p) {
  return { 6: 'TCP', 17: 'UDP', 1: 'ICMP', 47: 'GRE', 89: 'OSPF' }[p] || `P${p}`
}
function versionLabel(v) {
  return { 5: 'v5', 9: 'v9', 10: 'IPFIX' }[v] || `v${v}`
}
function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function loadStats() {
  try {
    stats.value = await netflowApi.statistics({
      hours: hours.value,
      device_id: deviceId.value || undefined,
    })
  } catch (e) {
    stats.value = {}
  }
}
async function loadTalkers() {
  try {
    const res = await netflowApi.topTalkers({
      hours: hours.value,
      limit: 20,
      direction: talkerDirection.value,
      device_id: deviceId.value || undefined,
    })
    talkers.value = res.items || []
  } catch (e) {
    talkers.value = []
  }
}
async function loadConversations() {
  try {
    const res = await netflowApi.conversations({
      hours: hours.value,
      limit: 20,
      device_id: deviceId.value || undefined,
    })
    conversations.value = res.items || []
  } catch (e) {
    conversations.value = []
  }
}
async function loadTrend() {
  try {
    const res = await netflowApi.timeseries({
      hours: hours.value,
      device_id: deviceId.value || undefined,
    })
    trend.value = res.items || []
  } catch (e) {
    trend.value = []
  }
}
async function loadFlows() {
  try {
    const res = await netflowApi.flows({
      hours: Math.max(1, hours.value),
      limit: 200,
      device_id: deviceId.value || undefined,
      keyword: keyword.value || undefined,
    })
    flows.value = res.items || []
  } catch (e) {
    flows.value = []
  }
}
async function loadExporters() {
  try {
    const res = await netflowApi.exporters()
    exporters.value = res.items || []
  } catch (e) {
    exporters.value = []
  }
}

async function reload() {
  loading.value = true
  try {
    await Promise.all([
      loadStats(),
      loadTalkers(),
      loadConversations(),
      loadTrend(),
      loadFlows(),
      loadExporters(),
    ])
  } finally {
    loading.value = false
  }
}

function openExporter(row) {
  Object.keys(exporterForm).forEach((k) => delete exporterForm[k])
  Object.assign(
    exporterForm,
    {
      id: null, device_id: null, name: '', version: 'v9', listen_port: 2055,
      source_ip: '', enabled: true, remark: '',
    },
    row || {},
  )
  exporterDialog.value = true
}

async function saveExporter() {
  if (!exporterForm.name) {
    ElMessage.warning('请填写名称')
    return
  }
  saving.value = true
  try {
    const payload = { ...exporterForm }
    delete payload.id
    delete payload.device_name
    if (exporterForm.id) await netflowApi.updateExporter(exporterForm.id, payload)
    else await netflowApi.createExporter(payload)
    ElMessage.success('保存成功')
    exporterDialog.value = false
    loadExporters()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function removeExporter(row) {
  try {
    await ElMessageBox.confirm(`确定删除导出器「${row.name}」吗？`, '提示', { type: 'warning' })
    await netflowApi.deleteExporter(row.id)
    ElMessage.success('已删除')
    loadExporters()
  } catch (e) {
    /* 取消 */
  }
}

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

const trendOption = computed(() => ({
  color: ['#8b5cf6'],
  tooltip: { ...TIP.value, trigger: 'axis', valueFormatter: (v) => `${v} MB` },
  grid: { left: 60, right: 24, top: 24, bottom: 28 },
  xAxis: {
    type: 'category',
    data: trend.value.map((i) => i.ts.replace('T', ' ').slice(5, 16)),
    axisLabel: AX.value,
    axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
  },
  yAxis: { type: 'value', name: 'MB', axisLabel: AX.value, splitLine: SPL.value },
  series: [
    {
      name: '流量',
      type: 'bar',
      barWidth: 14,
      itemStyle: { color: '#a78bfa', borderRadius: [3, 3, 0, 0] },
      data: trend.value.map((i) => i.mb),
    },
  ],
}))

const appOption = computed(() => {
  const entries = Object.entries(stats.value.by_application || {}).slice(0, 10)
  const data = entries.map(([name, value]) => ({ name, value }))
  return {
    tooltip: { ...TIP.value, trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { bottom: 0, type: 'scroll', textStyle: AX.value },
    series: [
      {
        type: 'pie',
        radius: ['42%', '66%'],
        center: ['50%', '43%'],
        label: { formatter: '{b}\n{c}', fontSize: 11, color: '#e5eaf3' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e5e7eb' } }],
      },
    ],
  }
})

onMounted(async () => {
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>
