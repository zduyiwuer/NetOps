<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">实时接口速率 <span class="sub">采样时间 {{ sampleTime }}</span></h2>
      <div class="toolbar">
        <el-select v-model="storeId" placeholder="全部门店" clearable style="width: 150px" @change="onStoreChange">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-tag v-if="currentStoreName" type="primary" effect="plain" size="default">
          当前筛选：{{ currentStoreName }}
        </el-tag>
        <el-select v-model="sortBy" style="width: 140px" @change="load">
          <el-option label="按利用率排序" value="utilization" />
          <el-option label="按入向排序" value="in_bps" />
          <el-option label="按出向排序" value="out_bps" />
          <el-option label="按名称排序" value="name" />
        </el-select>
        <el-switch v-model="onlyActive" active-text="仅 UP 接口" @change="load" />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Grid /></el-icon>接口总数</div>
        <div class="value">{{ items.length }}</div>
        <div class="foot">Down {{ downCount }} 个</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Download /></el-icon>入向总速率</div>
        <div class="value">{{ totalIn }}<span class="unit">Mbps</span></div>
        <div class="foot">全网汇总</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Upload /></el-icon>出向总速率</div>
        <div class="value">{{ totalOut }}<span class="unit">Mbps</span></div>
        <div class="foot">全网汇总</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Warning /></el-icon>高利用率接口</div>
        <div class="value" :class="highUtil > 0 ? 'text-warn' : ''">{{ highUtil }}</div>
        <div class="foot">利用率 &gt; 80%</div>
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="table-card" style="flex: 2; min-width: 560px">
        <div class="filter-bar">
          <el-input
            v-model="keyword"
            placeholder="搜索接口名 / 设备"
            clearable
            style="width: 220px"
            :prefix-icon="Search"
            @keyup.enter="applyFilter"
            @clear="applyFilter"
          />
          <DeviceSelect
            v-model="deviceId"
            :store-id="storeId"
            placeholder="全部设备"
            style="width: 200px"
            @change="load"
          />
          <el-button :icon="Search" @click="applyFilter">筛选</el-button>
        </div>
        <el-table v-loading="loading" :data="filtered" size="small" stripe max-height="600">
          <el-table-column prop="device_name" label="设备" width="140" show-overflow-tooltip fixed />
          <el-table-column prop="store_name" label="门店" width="110" show-overflow-tooltip />
          <el-table-column prop="if_name" label="接口名" min-width="190" show-overflow-tooltip />
          <el-table-column label="速率" width="95" align="right">
            <template #default="{ row }">{{ row.speed_mbps }}M</template>
          </el-table-column>
          <el-table-column label="状态" width="72" align="center">
            <template #default="{ row }">
              <el-tag :type="row.oper_status === 1 ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.oper_status === 1 ? 'UP' : 'DOWN' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="入向速率" width="115" align="right">
            <template #default="{ row }">
              <span :class="rateClass(row.in_mbps, row.speed_mbps)">{{ row.in_mbps }} Mbps</span>
            </template>
          </el-table-column>
          <el-table-column label="出向速率" width="115" align="right">
            <template #default="{ row }">
              <span :class="rateClass(row.out_mbps, row.speed_mbps)">{{ row.out_mbps }} Mbps</span>
            </template>
          </el-table-column>
          <el-table-column label="利用率" width="150">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.min(100, row.utilization)"
                :status="row.utilization > 85 ? 'exception' : row.utilization > 60 ? 'warning' : 'success'"
                :stroke-width="9"
                :format="(p) => `${p}%`"
              />
            </template>
          </el-table-column>
          <el-table-column label="累计入向" width="130" align="right">
            <template #default="{ row }">{{ fmtBytes(row.in_octets) }}</template>
          </el-table-column>
          <el-table-column label="累计出向" width="130" align="right">
            <template #default="{ row }">{{ fmtBytes(row.out_octets) }}</template>
          </el-table-column>
          <el-table-column label="错包" width="80" align="right">
            <template #default="{ row }">
              <span :class="row.in_errors + row.out_errors > 0 ? 'text-danger' : ''">
                {{ row.in_errors + row.out_errors }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="chart-card" style="flex: 1; min-width: 340px">
        <div class="card-title">接口流量 Top 10</div>
        <BaseChart :option="topOption" height="520px" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Download, Grid, Refresh, Search, Upload, Warning } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { deviceApi, monitorApi } from '../api'

const items = ref([])
const stores = ref([])
const storeId = ref(null)
// 当前选中的门店名——用于在页面上明确显示筛选状态
const currentStoreName = computed(
  () => stores.value.find((s) => s.id === storeId.value)?.name || '',
)
const deviceId = ref(null)
const sortBy = ref('utilization')
const onlyActive = ref(false)
const keyword = ref('')
const appliedKeyword = ref('')
const loading = ref(false)

const sampleTime = computed(() => {
  const t = items.value[0]?.ts
  return t ? t.replace('T', ' ').slice(0, 19) : '暂无数据'
})

const filtered = computed(() => {
  const kw = appliedKeyword.value.toLowerCase()
  if (!kw) return items.value
  return items.value.filter(
    (i) =>
      (i.if_name || '').toLowerCase().includes(kw) ||
      (i.device_name || '').toLowerCase().includes(kw) ||
      (i.device_ip || '').includes(kw),
  )
})

const downCount = computed(() => items.value.filter((i) => i.oper_status !== 1).length)
const totalIn = computed(() => (items.value.reduce((a, i) => a + i.in_mbps, 0) || 0).toFixed(1))
const totalOut = computed(() => (items.value.reduce((a, i) => a + i.out_mbps, 0) || 0).toFixed(1))
const highUtil = computed(() => items.value.filter((i) => i.utilization > 80).length)

function rateClass(mbps, speed) {
  if (!speed) return ''
  const p = (mbps * 1_000_000) / speed * 100
  return p > 80 ? 'text-danger' : p > 60 ? 'text-warn' : ''
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

async function load() {
  loading.value = true
  try {
    const res = await monitorApi.realtimeInterfaces({
      store_id: storeId.value || undefined,
      device_id: deviceId.value || undefined,
      only_active: onlyActive.value || undefined,
      sort_by: sortBy.value,
    })
    items.value = res.items || []
  } catch (e) {
    items.value = []
  } finally {
    loading.value = false
  }
}

function onStoreChange() {
  // 门店变化时清空设备筛选，避免设备与门店不匹配
  deviceId.value = null
  load()
}

function applyFilter() {
  appliedKeyword.value = keyword.value
}

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

const topOption = computed(() => {
  const top = [...items.value]
    .sort((a, b) => Math.max(b.in_bps, b.out_bps) - Math.max(a.in_bps, a.out_bps))
    .slice(0, 10)
    .reverse()
  return {
    color: ['#2563eb', '#16a34a'],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => `${v} Mbps` },
    legend: { data: ['入向', '出向'], right: 0, textStyle: AXIS },
    grid: { left: 150, right: 30, top: 30, bottom: 20 },
    xAxis: { type: 'value', axisLabel: AXIS, splitLine: SPLIT },
    yAxis: {
      type: 'category',
      data: top.map((i) => `${i.device_name} / ${i.if_name}`.slice(0, 24)),
      axisLabel: { ...AXIS, width: 140, overflow: 'truncate' },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
    series: [
      { name: '入向', type: 'bar', barWidth: 9, data: top.map((i) => i.in_mbps) },
      { name: '出向', type: 'bar', barWidth: 9, data: top.map((i) => i.out_mbps) },
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
  const t = setInterval(load, 15000)
  onUnmounted(() => clearInterval(t))
  window.addEventListener('netops:refresh', load)
})
onUnmounted(() => window.removeEventListener('netops:refresh', load))
</script>

<style scoped>
.unit {
  font-size: 13px;
  font-weight: 400;
  color: var(--netops-text-muted);
  margin-left: 3px;
}
</style>
