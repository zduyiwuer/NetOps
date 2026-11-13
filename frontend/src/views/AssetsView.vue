<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">资产管理 <span class="sub">设备台账与生命周期</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
        <el-button :icon="Download" @click="exportAssets">导出台账</el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Box /></el-icon>资产总数</div>
        <div class="value">{{ summary.total ?? 0 }}</div>
        <div class="foot">总价值 ¥{{ (summary.total_value ?? 0).toLocaleString('zh-CN') }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Clock /></el-icon>保修临期</div>
        <div class="value" :class="summary.warranty_soon ? 'text-warn' : ''">{{ summary.warranty_soon ?? 0 }}</div>
        <div class="foot">180 天内到期</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><CircleCheck /></el-icon>在线设备</div>
        <div class="value text-ok">{{ summary.by_status?.online ?? 0 }}</div>
        <div class="foot">离线 {{ summary.by_status?.offline ?? 0 }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Files /></el-icon>设备类型</div>
        <div class="value">{{ Object.keys(summary.by_type || {}).length }}</div>
        <div class="foot">{{ Object.keys(summary.by_vendor || {}).join(' / ') }}</div>
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="chart-card" style="flex: 1; min-width: 320px">
        <div class="card-title">厂商分布</div>
        <BaseChart :option="vendorOption" height="280px" />
      </div>
      <div class="chart-card" style="flex: 1; min-width: 320px">
        <div class="card-title">设备类型分布</div>
        <BaseChart :option="typeOption" height="280px" />
      </div>
      <div class="chart-card" style="flex: 1; min-width: 320px">
        <div class="card-title">门店资产分布</div>
        <BaseChart :option="storeOption" height="280px" />
      </div>
    </div>

    <div class="table-card" style="margin-top: 12px">
      <div class="filter-bar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索名称 / IP / 型号 / 序列号 / 资产编号"
          clearable
          style="width: 300px"
          :prefix-icon="Search"
          @keyup.enter="load"
          @clear="load"
        />
        <el-select v-model="query.store_id" placeholder="全部门店" clearable style="width: 150px" @change="load">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-select v-model="query.vendor" placeholder="全部厂商" clearable style="width: 140px" @change="load">
          <el-option v-for="v in vendors" :key="v.value" :label="v.label" :value="v.value" />
        </el-select>
        <el-button :icon="Search" @click="load">查询</el-button>
      </div>

      <el-table border v-loading="loading" :data="assets" size="small" stripe>
        <el-table-column prop="asset_tag" label="资产编号" width="130" class-name="mono" />
        <el-table-column prop="name" label="设备名称" min-width="150" show-overflow-tooltip />
        <el-table-column prop="ip" label="管理IP" width="125" class-name="mono" />
        <el-table-column label="厂商" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.vendor_label || row.vendor }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="型号" width="150" show-overflow-tooltip />
        <el-table-column prop="serial_number" label="序列号" width="150" class-name="mono" show-overflow-tooltip />
        <el-table-column prop="store_name" label="门店" width="110" show-overflow-tooltip />
        <el-table-column prop="firmware_version" label="固件版本" width="130" show-overflow-tooltip />
        <el-table-column prop="purchase_date" label="采购日期" width="105" />
        <el-table-column label="保修到期" width="115">
          <template #default="{ row }">
            <span :class="warrantyClass(row.warranty_end)">{{ row.warranty_end || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="价格" width="100" align="right">
          <template #default="{ row }">¥{{ (row.price || 0).toLocaleString('zh-CN') }}</template>
        </el-table-column>
        <el-table-column prop="contract_no" label="合同编号" width="120" show-overflow-tooltip />
        <el-table-column prop="vendor_contact" label="厂商联系方式" width="130" show-overflow-tooltip />
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="query.page"
          v-model:page-size="query.page_size"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @size-change="load"
          @current-change="load"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, Refresh, Search } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { deviceApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'

const summary = ref({})
const assets = ref([])
const stores = ref([])
const vendors = ref([])
const total = ref(0)
const loading = ref(false)

const query = reactive({ keyword: '', store_id: null, vendor: null, page: 1, page_size: 50 })

function warrantyClass(d) {
  if (!d) return ''
  const days = (new Date(d) - new Date()) / 86400000
  if (days < 0) return 'text-danger'
  if (days < 180) return 'text-warn'
  return ''
}

async function load() {
  loading.value = true
  try {
    const res = await deviceApi.list({ ...query })
    assets.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    summary.value = await deviceApi.assetSummary()
  } catch (e) {
    summary.value = {}
  }
}

async function reload() {
  await Promise.all([load(), loadSummary()])
}

async function exportAssets() {
  const res = await deviceApi.exportXlsx({
    store_id: query.store_id || undefined,
    vendor: query.vendor || undefined,
  })
  const blob = new Blob([res.data])
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `资产台账_${new Date().toISOString().slice(0, 10)}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('导出成功')
}

const AXIS = { color: '#6b7280', fontSize: 11 }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

function pieOption(dataObj, labelKey = 'name') {
  const entries = Object.entries(dataObj || {})
  const data = entries.map(([name, value]) => ({ name, value }))
  return {
    tooltip: { ...TIP.value, trigger: 'item' },
    legend: { bottom: 0, type: 'scroll', textStyle: AX.value },
    series: [
      {
        type: 'pie',
        radius: ['42%', '66%'],
        center: ['50%', '43%'],
        label: { formatter: '{b}: {c}', fontSize: 11, color: '#e5eaf3' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e5e7eb' } }],
      },
    ],
  }
}

const vendorOption = computed(() => pieOption(summary.value.by_vendor))
const typeOption = computed(() => pieOption(summary.value.by_type))
const storeOption = computed(() => pieOption(summary.value.by_store))

onMounted(async () => {
  try {
    const [s, v] = await Promise.all([deviceApi.stores(), deviceApi.vendors()])
    stores.value = s.items || []
    vendors.value = v.items || []
  } catch (e) {
    /* 忽略 */
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
</style>
