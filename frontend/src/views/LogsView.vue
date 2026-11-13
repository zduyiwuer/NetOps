<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">日志中心 <span class="sub">设备日志与平台系统日志检索</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="log-tabs" @tab-change="onTabChange">
    <!-- ============================================ 设备日志 -->
    <el-tab-pane label="设备日志" name="device">
    <div class="stat-grid" style="margin-bottom: 12px">
      <div class="stat-card">
        <div class="label">日志总量 ({{ hours }}h)</div>
        <div class="value">{{ stats.total ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label">严重 / 错误</div>
        <div class="value text-danger">{{ (stats.by_severity?.err || 0) + (stats.by_severity?.crit || 0) }}</div>
      </div>
      <div class="stat-card">
        <div class="label">警告</div>
        <div class="value text-warn">{{ stats.by_severity?.warning ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label">信息 / 通知</div>
        <div class="value">{{ (stats.by_severity?.info || 0) + (stats.by_severity?.notice || 0) }}</div>
      </div>
    </div>

    <div class="gap-row">
      <div class="table-card" style="flex: 2; min-width: 560px">
        <div class="filter-bar">
          <DeviceSelect v-model="query.device_id" placeholder="全部设备" @change="load" />
          <el-select v-model="query.store_id" placeholder="全部门店" clearable style="width: 140px" @change="load">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-select v-model="query.severity" placeholder="全部级别" clearable style="width: 130px" @change="load">
            <el-option v-for="(label, key) in severityLabels" :key="key" :label="label" :value="key" />
          </el-select>
          <el-select v-model="query.protocol" placeholder="全部来源" clearable style="width: 120px" @change="load">
            <el-option label="Syslog 接收" value="syslog" />
            <el-option label="SSH 采集" value="ssh" />
          </el-select>
          <el-select v-model="query.hours" style="width: 110px" @change="load">
            <el-option label="近 1 小时" :value="1" />
            <el-option label="近 6 小时" :value="6" />
            <el-option label="近 24 小时" :value="24" />
            <el-option label="近 7 天" :value="168" />
            <el-option label="近 30 天" :value="720" />
          </el-select>
          <el-input
            v-model="query.keyword"
            placeholder="搜索日志内容"
            clearable
            style="width: 180px"
            :prefix-icon="Search"
            @keyup.enter="load"
            @clear="load"
          />
          <el-button :icon="Search" @click="load">查询</el-button>
        </div>

        <el-table border v-loading="loading" :data="logs" size="small" stripe max-height="560">
          <el-table-column label="级别" width="76" align="center">
            <template #default="{ row }">
              <el-tag :type="severityType(row.severity)" size="small" effect="plain">
                {{ severityLabels[row.severity] || row.severity }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="timestamp_or_device" label="设备" width="140" show-overflow-tooltip>
            <template #default="{ row }">{{ row.device_name || row.hostname || row.source_ip || '-' }}</template>
          </el-table-column>
          <el-table-column prop="source_ip" label="源地址" width="120" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="facility" label="Facility" width="100" />
          <el-table-column prop="message" label="日志内容" min-width="320" show-overflow-tooltip />
          <el-table-column label="来源" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.protocol === 'syslog' ? 'success' : 'warning'">
                {{ row.protocol === 'syslog' ? 'Syslog' : 'SSH' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
          </el-table-column>
        </el-table>

        <div class="pager">
          <el-pagination
            v-model:current-page="query.page"
            v-model:page-size="query.page_size"
            :total="total"
            :page-sizes="[20, 50, 100, 200]"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="load"
            @current-change="load"
          />
        </div>
      </div>

      <div style="flex: 1; min-width: 340px; display: flex; flex-direction: column; gap: 12px">
        <div class="chart-card">
          <div class="card-title">日志级别分布</div>
          <BaseChart :option="severityOption" height="240px" />
        </div>
        <div class="chart-card">
          <div class="card-title">日志量趋势（按小时）</div>
          <BaseChart :option="hourOption" height="240px" />
        </div>
        <div class="table-card">
          <div class="card-title">设备日志 Top 10</div>
          <el-table border :data="deviceRows" size="small">
            <el-table-column prop="name" label="设备" show-overflow-tooltip />
            <el-table-column prop="count" label="条数" width="80" align="right" />
          </el-table>
        </div>
      </div>
    </div>
    </el-tab-pane>
    <!-- ============================================ 系统日志 -->
    <el-tab-pane label="系统日志" name="system">
      <div class="stat-grid" style="margin-bottom: 12px">
        <div class="stat-card">
          <div class="label">系统日志总量 ({{ sysHours }}h)</div>
          <div class="value">{{ sysStats.total ?? 0 }}</div>
          <div class="foot">平台自身的运行问题</div>
        </div>
        <div class="stat-card">
          <div class="label">严重 / 错误</div>
          <div class="value text-danger">
            {{ (sysStats.by_level?.error || 0) + (sysStats.by_level?.critical || 0) }}
          </div>
        </div>
        <div class="stat-card">
          <div class="label">警告</div>
          <div class="value text-warn">{{ sysStats.by_level?.warning || 0 }}</div>
        </div>
        <div class="stat-card">
          <div class="label">端口问题</div>
          <div class="value" :class="(sysStats.by_category?.port || 0) ? 'text-warn' : ''">
            {{ sysStats.by_category?.port || 0 }}
          </div>
          <div class="foot">端口占用 / 被系统排除</div>
        </div>
      </div>
      <div class="table-card">
        <div class="filter-bar">
          <el-select
            v-model="sysQuery.store_id"
            placeholder="全部门店"
            clearable
            size="small"
            style="width: 140px"
            @change="loadSystemLogs"
          >
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <el-select
            v-model="sysQuery.level"
            placeholder="全部级别"
            clearable
            size="small"
            style="width: 120px"
            @change="loadSystemLogs"
          >
            <el-option label="严重" value="critical" />
            <el-option label="错误" value="error" />
            <el-option label="警告" value="warning" />
            <el-option label="信息" value="info" />
          </el-select>
          <el-select
            v-model="sysQuery.category"
            placeholder="全部分类"
            clearable
            size="small"
            style="width: 130px"
            @change="loadSystemLogs"
          >
            <el-option v-for="(label, key) in categoryLabels" :key="key" :label="label" :value="key" />
          </el-select>
          <el-input
            v-model="sysQuery.keyword"
            placeholder="搜索消息 / 来源"
            clearable
            size="small"
            style="width: 220px"
            :prefix-icon="Search"
            @keyup.enter="loadSystemLogs"
          />
          <el-select v-model="sysQuery.hours" size="small" style="width: 120px" @change="loadSystemLogs">
            <el-option :value="1" label="近 1 小时" />
            <el-option :value="24" label="近 24 小时" />
            <el-option :value="168" label="近 7 天" />
            <el-option :value="720" label="近 30 天" />
          </el-select>
          <el-button size="small" @click="loadSystemLogs">查询</el-button>
          <span class="text-muted">记录端口占用、采集器启动失败等平台自身的运行问题</span>
        </div>
        <el-table border v-loading="sysLoading" :data="sysLogs" size="small" stripe empty-text="暂无系统日志">
          <el-table-column label="级别" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="dark" :type="sysLevelType(row.level)">
                {{ sysLevelText(row.level) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="分类" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ categoryLabels[row.category] || row.category }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="来源" width="140" class-name="mono" show-overflow-tooltip />
          <el-table-column label="关联设备" width="140" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link
                v-if="row.device_id"
                type="primary"
                :underline="false"
                @click="$router.push(`/device/${row.device_id}`)"
              >
                {{ row.device_name || `设备 #${row.device_id}` }}
              </el-link>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="消息" min-width="300" show-overflow-tooltip>
            <template #default="{ row }">
              <el-link type="primary" :underline="false" @click="openSysDetail(row)">
                {{ row.message }}
              </el-link>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="70" align="center">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openSysDetail(row)">详情</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-pagination
            v-model:current-page="sysQuery.page"
            :page-size="sysQuery.page_size"
            :total="sysTotal"
            layout="total, prev, pager, next"
            @current-change="loadSystemLogs"
          />
        </div>
      </div>
    </el-tab-pane>
    </el-tabs>
  </div>
  <!-- 系统日志详情 -->
  <el-dialog v-model="sysDetailVisible" title="系统日志详情" width="640px">
    <template v-if="sysDetail">
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="级别">
          <el-tag size="small" effect="dark" :type="sysLevelType(sysDetail.level)">
            {{ sysLevelText(sysDetail.level) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="分类">
          {{ categoryLabels[sysDetail.category] || sysDetail.category }}
        </el-descriptions-item>
        <el-descriptions-item label="来源">
          <span class="mono">{{ sysDetail.source }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="关联设备">
          <el-link
            v-if="sysDetail.device_id"
            type="primary"
            :underline="false"
            @click="$router.push(`/device/${sysDetail.device_id}`)"
          >
            {{ sysDetail.device_name || `设备 #${sysDetail.device_id}` }}
          </el-link>
          <span v-else class="text-muted">平台级问题，与具体设备无关</span>
        </el-descriptions-item>
        <el-descriptions-item label="时间">{{ fmtTime(sysDetail.ts) }}</el-descriptions-item>
        <el-descriptions-item label="消息">{{ sysDetail.message }}</el-descriptions-item>
        <el-descriptions-item v-if="sysDetail.detail" label="详细诊断">
          <pre class="log-detail">{{ sysDetail.detail }}</pre>
        </el-descriptions-item>
        <el-descriptions-item v-if="Object.keys(sysDetail.context || {}).length" label="上下文">
          <pre class="log-detail">{{ JSON.stringify(sysDetail.context, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { deviceApi, logApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'

const logs = ref([])
const stats = ref({})
const total = ref(0)
const loading = ref(false)
const stores = ref([])

// 系统日志
const activeTab = ref('device')
const sysLogs = ref([])
const sysStats = ref({})
const sysTotal = ref(0)
const sysLoading = ref(false)
const sysDetailVisible = ref(false)
const sysDetail = ref(null)
const sysHours = ref(168)
const sysQuery = reactive({
  level: null, category: null, keyword: '', store_id: null, hours: 168, page: 1, page_size: 50,
})

const categoryLabels = {
  collector: '采集服务',
  port: '端口',
  scheduler: '调度',
  storage: '存储',
  database: '数据库',
  network: '网络',
  security: '安全',
  system: '系统',
}

function sysLevelType(l) {
  return { critical: 'danger', error: 'danger', warning: 'warning', info: 'info' }[l] || 'info'
}
function sysLevelText(l) {
  return { critical: '严重', error: '错误', warning: '警告', info: '信息' }[l] || l
}
function openSysDetail(row) {
  sysDetail.value = row
  sysDetailVisible.value = true
}

async function loadSystemLogs() {
  sysLoading.value = true
  try {
    const res = await logApi.systemLogs({
      level: sysQuery.level || undefined,
      category: sysQuery.category || undefined,
      keyword: sysQuery.keyword || undefined,
      store_id: sysQuery.store_id || undefined,
      hours: sysQuery.hours,
      page: sysQuery.page,
      page_size: sysQuery.page_size,
    })
    sysLogs.value = res.items || []
    sysTotal.value = res.total || 0
    sysHours.value = sysQuery.hours
    const st = await logApi.systemLogStatistics({ hours: sysQuery.hours })
    sysStats.value = st || {}
  } catch (e) {
    sysLogs.value = []
    sysTotal.value = 0
  } finally {
    sysLoading.value = false
  }
}

function onTabChange(name) {
  if (name === 'system') loadSystemLogs()
}


const query = reactive({
  device_id: null, store_id: null, severity: null, protocol: null, keyword: '', hours: 24, page: 1, page_size: 50,
})

const severityLabels = {
  emerg: '紧急', alert: '严重', crit: '致命', err: '错误',
  warning: '警告', notice: '通知', info: '信息', debug: '调试',
}

function severityType(s) {
  return { emerg: 'danger', alert: 'danger', crit: 'danger', err: 'danger', warning: 'warning', notice: 'info', info: 'info', debug: 'info' }[s] || 'info'
}
function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function load() {
  loading.value = true
  try {
    const res = await logApi.list({ ...query })
    logs.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    stats.value = await logApi.statistics({ hours: query.hours })
  } catch (e) {
    stats.value = {}
  }
}

async function reload() {
  await Promise.all([load(), loadStats()])
}

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

const severityOption = computed(() => {
  const s = stats.value.by_severity || {}
  const colors = {
    emerg: '#7f1d1d', alert: '#991b1b', crit: '#dc2626', err: '#f87171',
    warning: '#f59e0b', notice: '#60a5fa', info: '#93c5fd', debug: '#cbd5e1',
  }
  const data = Object.entries(s).map(([name, value]) => ({ name: severityLabels[name] || name, value, itemStyle: { color: colors[name] || '#94a3b8' } }))
  return {
    tooltip: { ...TIP.value, trigger: 'item' },
    legend: { bottom: 0, type: 'scroll', textStyle: AX.value },
    series: [
      {
        type: 'pie',
        radius: ['45%', '68%'],
        center: ['50%', '43%'],
        label: { formatter: '{b}: {c}\n{d}%', fontSize: 11, color: '#e5eaf3' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e5e7eb' } }],
      },
    ],
  }
})

const hourOption = computed(() => ({
  color: ['#3b82f6'],
  tooltip: { ...TIP.value, trigger: 'axis' },
  grid: { left: 40, right: 16, top: 16, bottom: 26 },
  xAxis: {
    type: 'category',
    data: (stats.value.by_hour || []).map((i) => i.ts.slice(5, 13)),
    axisLabel: AX.value,
    axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
  },
  yAxis: { type: 'value', axisLabel: AX.value, splitLine: SPL.value },
  series: [
    {
      name: '日志数',
      type: 'bar',
      barWidth: 10,
      itemStyle: { color: '#93c5fd', borderRadius: [2, 2, 0, 0] },
      data: (stats.value.by_hour || []).map((i) => i.count),
    },
  ],
}))

const deviceRows = computed(() =>
  Object.entries(stats.value.by_device || {}).map(([name, count]) => ({ name, count })),
)

onMounted(async () => {
  // 门店列表用于筛选
  try {
    const res = await deviceApi.stores()
    stores.value = res.items || []
  } catch (e) {
    stores.value = []
  }
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>

<style scoped>
.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}

/* 系统日志详情里的诊断信息 */
.log-detail {
  margin: 0;
  padding: 8px 10px;
  background: var(--netops-bg-soft, #f7f8fa);
  border: 1px solid var(--netops-border, #e5e7eb);
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 260px;
  overflow: auto;
}

/* 标签页内容区不再额外留白 */
.log-tabs :deep(.el-tabs__content) {
  overflow: visible;
}
</style>
