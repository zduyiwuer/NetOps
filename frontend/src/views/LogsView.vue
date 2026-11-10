<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">日志中心 <span class="sub">交换机日志集中采集与检索</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

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

        <el-table v-loading="loading" :data="logs" size="small" stripe max-height="560">
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
          <el-table :data="deviceRows" size="small">
            <el-table-column prop="name" label="设备" show-overflow-tooltip />
            <el-table-column prop="count" label="条数" width="80" align="right" />
          </el-table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { logApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'

const logs = ref([])
const stats = ref({})
const total = ref(0)
const loading = ref(false)

const query = reactive({
  device_id: null, severity: null, protocol: null, keyword: '', hours: 24, page: 1, page_size: 50,
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
</style>
