<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">流量分析 <span class="sub">TopN 排行与流量趋势</span></h2>
      <div class="toolbar">
        <el-radio-group v-model="dimension" size="small" @change="loadTopn">
          <el-radio-button value="interface">按接口</el-radio-button>
          <el-radio-button value="device">按设备</el-radio-button>
          <el-radio-button value="store">按门店</el-radio-button>
        </el-radio-group>
        <el-select v-model="storeId" placeholder="全部门店" clearable style="width: 150px" @change="reload">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select v-model="hours" style="width: 120px" @change="reload">
          <el-option label="近 1 小时" :value="1" />
          <el-option label="近 6 小时" :value="6" />
          <el-option label="近 24 小时" :value="24" />
          <el-option label="近 7 天" :value="168" />
        </el-select>
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <div class="gap-row">
      <div class="chart-card" style="flex: 1; min-width: 480px">
        <div class="card-title">流量 Top {{ limit }}（{{ dimensionLabel }}维度）</div>
        <BaseChart :option="topOption" height="420px" />
      </div>
      <div class="chart-card" style="flex: 1; min-width: 420px">
        <div class="card-title">流量趋势</div>
        <BaseChart :option="trendOption" height="420px" />
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="table-card" style="flex: 2; min-width: 520px">
        <div class="card-title">流量排行明细</div>
        <el-table border :data="topn" size="small" stripe max-height="420">
          <el-table-column type="index" label="#" width="55" />
          <el-table-column prop="device_name" label="设备" min-width="140" show-overflow-tooltip />
          <el-table-column prop="target" label="接口 / 目标" min-width="180" show-overflow-tooltip />
          <el-table-column prop="store_name" label="门店" width="110" show-overflow-tooltip />
          <el-table-column label="入向峰值" width="120" align="right">
            <template #default="{ row }">{{ row.in_mbps }} Mbps</template>
          </el-table-column>
          <el-table-column label="出向峰值" width="120" align="right">
            <template #default="{ row }">{{ row.out_mbps }} Mbps</template>
          </el-table-column>
          <el-table-column label="综合峰值" width="120" align="right">
            <template #default="{ row }">
              <b :class="row.peak_mbps > 500 ? 'text-warn' : ''">{{ row.peak_mbps }} Mbps</b>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="table-card" style="flex: 1; min-width: 340px">
        <div class="card-title">错包 Top 10</div>
        <el-table border :data="errors" size="small" stripe max-height="420">
          <el-table-column prop="device_name" label="设备" min-width="120" show-overflow-tooltip />
          <el-table-column prop="target" label="接口" min-width="150" show-overflow-tooltip />
          <el-table-column label="错包总数" width="100" align="right">
            <template #default="{ row }">
              <span class="text-danger">{{ row.total_errors }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { deviceApi, monitorApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'

const stores = ref([])
const storeId = ref(null)
const dimension = ref('interface')
const hours = ref(6)
const limit = ref(15)
const topn = ref([])
const errors = ref([])
const trend = ref([])
const loading = ref(false)

const dimensionLabel = computed(
  () => ({ interface: '接口', device: '设备', store: '门店' })[dimension.value],
)

async function loadTopn() {
  loading.value = true
  try {
    const res = await monitorApi.trafficTopn({
      dimension: dimension.value,
      hours: hours.value,
      limit: limit.value,
      store_id: storeId.value || undefined,
    })
    topn.value = res.items || []
  } catch (e) {
    topn.value = []
  } finally {
    loading.value = false
  }
}

async function loadTrend() {
  try {
    const res = await monitorApi.trafficTimeseries({
      hours: hours.value,
      store_id: storeId.value || undefined,
    })
    trend.value = res.items || []
  } catch (e) {
    trend.value = []
  }
}

async function loadErrors() {
  try {
    const res = await monitorApi.errorTopn({ hours: hours.value, limit: 10 })
    errors.value = res.items || []
  } catch (e) {
    errors.value = []
  }
}

async function reload() {
  await Promise.all([loadTopn(), loadTrend(), loadErrors()])
}

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

const topOption = computed(() => {
  const data = [...topn.value].reverse()
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v) => `${v} Mbps`,
    },
    grid: { left: 190, right: 40, top: 16, bottom: 24 },
    xAxis: { type: 'value', axisLabel: AX.value, splitLine: SPL.value },
    yAxis: {
      type: 'category',
      data: data.map((i) =>
        dimension.value === 'interface'
          ? `${i.device_name}/${i.target}`.slice(0, 26)
          : dimension.value === 'device'
            ? i.device_name
            : i.target,
      ),
      axisLabel: { ...AX.value, width: 180, overflow: 'truncate' },
      axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
    },
    series: [
      {
        name: '入向',
        type: 'bar',
        stack: 'total',
        barWidth: 15,
        itemStyle: { color: '#2563eb' },
        data: data.map((i) => i.in_mbps),
      },
      {
        name: '出向',
        type: 'bar',
        stack: 'total',
        barWidth: 15,
        itemStyle: { color: '#16a34a', borderRadius: [0, 4, 4, 0] },
        data: data.map((i) => i.out_mbps),
      },
    ],
  }
})

const trendOption = computed(() => ({
  color: ['#2563eb', '#16a34a'],
  tooltip: { ...TIP.value, trigger: 'axis', valueFormatter: (v) => `${v} Mbps` },
  legend: { data: ['入向', '出向'], right: 0, textStyle: AX.value },
  grid: { left: 68, right: 24, top: 34, bottom: 28 },
  xAxis: {
    type: 'category',
    data: trend.value.map((i) => i.ts.slice(5, 16)),
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
      data: trend.value.map((i) => i.in_mbps),
    },
    {
      name: '出向',
      type: 'line',
      smooth: true,
      showSymbol: false,
      areaStyle: { opacity: 0.12 },
      data: trend.value.map((i) => i.out_mbps),
    },
  ],
}))

onMounted(async () => {
  try {
    const res = await deviceApi.stores()
    stores.value = res.items || []
  } catch (e) {
    /* 忽略 */
  }
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>
