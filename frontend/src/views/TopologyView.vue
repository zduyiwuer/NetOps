<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">网络拓扑 <span class="sub">基于 LLDP / CDP 邻居关系</span></h2>
      <div class="toolbar">
        <el-select v-model="storeId" placeholder="全部门店" clearable style="width: 150px" @change="load">
          <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
        </el-select>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button v-if="userStore.canOperate" type="primary" :icon="Link" :loading="collecting" @click="collect">
          采集邻居
        </el-button>
        <el-button v-if="userStore.isAdmin" :icon="Delete" @click="clear">清空</el-button>
      </div>
    </div>

    <el-alert v-if="!graph.nodes.length" type="info" :closable="false" show-icon style="margin-bottom: 12px">
      <template #title>暂无拓扑数据</template>
      点击「采集邻居」从设备读取 LLDP / CDP 邻居信息（优先 SNMP，其次 SSH）。
    </el-alert>

    <div class="stat-grid" style="margin-bottom: 12px">
      <div class="stat-card">
        <div class="label"><el-icon><Share /></el-icon>拓扑节点</div>
        <div class="value">{{ graph.nodes.length }}</div>
        <div class="foot">设备 {{ deviceNodeCount }} · 外部邻居 {{ graph.nodes.length - deviceNodeCount }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Connection /></el-icon>连接关系</div>
        <div class="value">{{ graph.edges.length }}</div>
        <div class="foot">LLDP / CDP 邻居链路</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Grid /></el-icon>涉及设备</div>
        <div class="value">{{ involvedDevices }}</div>
        <div class="foot">有邻居的设备数</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Warning /></el-icon>疑似环路</div>
        <div class="value" :class="loopCount ? 'text-warn' : ''">{{ loopCount }}</div>
        <div class="foot">双向邻居对</div>
      </div>
    </div>

    <div class="gap-row">
      <div class="chart-card" style="flex: 2; min-width: 480px">
        <div class="card-title">
          <span>拓扑关系图</span>
          <span class="text-muted" style="font-size: 12px">力导向布局 · 滚轮缩放 · 拖拽节点</span>
        </div>
        <div v-loading="loading" ref="graphEl" class="graph-box"></div>
      </div>

      <div class="table-card" style="flex: 1; min-width: 380px">
        <div class="card-title">邻居关系明细</div>
        <el-table border :data="neighbors" size="small" stripe max-height="480">
          <el-table-column prop="local_device_name" label="本地设备" width="130" show-overflow-tooltip />
          <el-table-column prop="local_interface" label="本地接口" width="130" show-overflow-tooltip />
          <el-table-column prop="remote_device" label="远端设备" min-width="130" show-overflow-tooltip />
          <el-table-column prop="remote_interface" label="远端接口" width="130" show-overflow-tooltip />
          <el-table-column label="协议" width="70" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.protocol.toUpperCase() }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Delete, Grid, Link, Refresh, Share, Warning } from '@element-plus/icons-vue'
import { deviceApi, topologyApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const stores = ref([])
const storeId = ref(null)
const neighbors = ref([])
const graph = ref({ nodes: [], edges: [] })
const loading = ref(false)
const collecting = ref(false)
const graphEl = ref(null)
let chart = null

const deviceNodeCount = computed(() => graph.value.nodes.filter((n) => n.category === 'device').length)
const involvedDevices = computed(() => new Set(graph.value.edges.map((e) => e.source)).size)
const loopCount = computed(() => {
  const pairs = new Set()
  let count = 0
  graph.value.edges.forEach((e) => {
    const key = `${e.source}|${e.target}`
    const rev = `${e.target}|${e.source}`
    if (pairs.has(rev)) count += 1
    pairs.add(key)
  })
  return count
})

const NODE_COLORS = {
  online: '#16a34a',
  offline: '#dc2626',
  unknown: '#94a3b8',
}

async function load() {
  loading.value = true
  try {
    const [n, g] = await Promise.all([
      topologyApi.neighbors({ store_id: storeId.value || undefined }),
      topologyApi.graph({ store_id: storeId.value || undefined }),
    ])
    neighbors.value = n.items || []
    graph.value = g || { nodes: [], edges: [] }
    await nextTick()
    renderGraph()
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

function renderGraph() {
  if (!graphEl.value) return
  if (!chart) {
    chart = echarts.init(graphEl.value)
    window.addEventListener('resize', () => chart?.resize())
  }
  const nodes = graph.value.nodes.map((n) => ({
    id: n.id,
    name: n.id,
    category: n.category === 'device' ? 0 : 1,
    value: n.value,
    symbolSize: n.category === 'device' ? 34 : 22,
    itemStyle: {
      color: n.category === 'device' ? NODE_COLORS[n.status] || '#3b82f6' : '#a78bfa',
    },
    label: {
      show: true,
      position: 'bottom',
      fontSize: 11,
      color: getComputedStyle(document.documentElement).getPropertyValue('--netops-text') || '#333',
    },
  }))
  const edges = graph.value.edges.map((e) => ({
    source: e.source,
    target: e.target,
    protocol: e.protocol,
    lineStyle: {
      color: e.protocol === 'cdp' ? '#f59e0b' : '#3b82f6',
      width: 1.4,
      curveness: 0.15,
      opacity: 0.75,
    },
  }))
  chart.setOption(
    {
      tooltip: {
        formatter: (p) => {
          if (p.dataType === 'edge') {
            return `${p.data.source} → ${p.data.target}<br/>协议：${(p.data.protocol || '').toUpperCase()}`
          }
          return `<b>${p.name}</b>`
        },
      },
      legend: [
        { data: ['平台设备', '外部邻居'], bottom: 0, textStyle: { fontSize: 11 } },
      ],
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories: [
            { name: '平台设备', itemStyle: { color: '#3b82f6' } },
            { name: '外部邻居', itemStyle: { color: '#a78bfa' } },
          ],
          force: { repulsion: 260, edgeLength: 110, gravity: 0.08 },
          data: nodes,
          links: edges,
          emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
          lineStyle: { curveness: 0.15 },
        },
      ],
    },
    true,
  )
}

async function collect() {
  collecting.value = true
  try {
    const res = await topologyApi.collect()
    ElMessage.success(`采集完成：${res.devices} 台设备，新增 ${res.added} 条邻居关系`)
    load()
  } catch (e) {
    /* 忽略 */
  } finally {
    collecting.value = false
  }
}

async function clear() {
  try {
    await ElMessageBox.confirm('确定清空所有拓扑数据吗？', '提示', { type: 'warning' })
    await topologyApi.clear()
    ElMessage.success('已清空')
    load()
  } catch (e) {
    /* 取消 */
  }
}

import * as echarts from 'echarts'

watch(graph, () => nextTick(renderGraph))

onMounted(async () => {
  try {
    const res = await deviceApi.stores()
    stores.value = res.items || []
  } catch (e) {
    /* 忽略 */
  }
  await load()
  window.addEventListener('netops:refresh', load)
})
onUnmounted(() => window.removeEventListener('netops:refresh', load))
</script>

<style scoped>
.graph-box {
  width: 100%;
  height: 520px;
}
</style>
