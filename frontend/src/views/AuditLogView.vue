<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">审计日志 <span class="sub">平台操作与登录痕迹</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="操作审计" name="audit">
        <div class="stat-grid" style="margin-bottom: 12px">
          <div class="stat-card">
            <div class="label"><el-icon><Files /></el-icon>操作总数</div>
            <div class="value">{{ summary.total ?? 0 }}</div>
            <div class="foot">近 {{ days }} 天</div>
          </div>
          <div class="stat-card">
            <div class="label"><el-icon><User /></el-icon>活跃用户</div>
            <div class="value">{{ Object.keys(summary.by_user || {}).length }}</div>
            <div class="foot">{{ topUsers.length ? topUsers[0].name : '-' }} 最多</div>
          </div>
          <div class="stat-card">
            <div class="label"><el-icon><Operation /></el-icon>操作类型</div>
            <div class="value">{{ Object.keys(summary.by_action || {}).length }}</div>
            <div class="foot">不同操作种类</div>
          </div>
        </div>

        <div class="gap-row" style="margin-bottom: 12px">
          <div class="chart-card" style="flex: 1; min-width: 380px">
            <div class="card-title">操作类型分布</div>
            <BaseChart :option="actionOption" height="260px" />
          </div>
          <div class="chart-card" style="flex: 1; min-width: 380px">
            <div class="card-title">活跃用户 Top 10</div>
            <BaseChart :option="userOption" height="260px" />
          </div>
        </div>

        <div class="table-card">
          <div class="filter-bar">
            <el-input
              v-model="query.keyword"
              placeholder="搜索操作 / 目标 / 用户"
              clearable
              style="width: 240px"
              :prefix-icon="Search"
              @keyup.enter="load"
              @clear="load"
            />
            <el-select v-model="query.action" placeholder="全部操作类型" clearable style="width: 190px" @change="load">
              <el-option v-for="a in actions" :key="a" :label="a" :value="a" />
            </el-select>
            <el-input v-model="query.username" placeholder="操作人" clearable style="width: 140px" @keyup.enter="load" />
            <el-select v-model="days" style="width: 120px" @change="load">
              <el-option label="近 1 天" :value="1" />
              <el-option label="近 7 天" :value="7" />
              <el-option label="近 30 天" :value="30" />
              <el-option label="近 90 天" :value="90" />
            </el-select>
            <el-button :icon="Search" @click="load">查询</el-button>
          </div>

          <el-table border v-loading="loading" :data="items" size="small" stripe max-height="520">
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="username" label="操作人" width="120" show-overflow-tooltip />
            <el-table-column prop="action" label="操作" width="200" show-overflow-tooltip class-name="mono" />
            <el-table-column prop="target" label="目标" min-width="200" show-overflow-tooltip class-name="mono" />
            <el-table-column prop="detail" label="详情" min-width="240" show-overflow-tooltip />
            <el-table-column prop="ip" label="来源 IP" width="130" class-name="mono" />
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
      </el-tab-pane>

      <el-tab-pane label="登录审计" name="login">
        <div class="table-card">
          <el-table border :data="loginLogs" size="small" stripe max-height="600">
            <el-table-column label="时间" width="160">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column prop="username" label="用户名" width="140" show-overflow-tooltip />
            <el-table-column label="结果" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.success ? 'success' : 'danger'">
                  {{ row.success ? '成功' : '失败' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="source" label="来源" width="90" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="row.source === 'ldap' ? 'warning' : 'info'">
                  {{ row.source === 'ldap' ? '域控' : '本地' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="ip" label="IP 地址" width="140" class-name="mono" />
            <el-table-column prop="user_agent" label="客户端" min-width="240" show-overflow-tooltip />
            <el-table-column prop="message" label="说明" min-width="160" show-overflow-tooltip />
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Refresh, Search } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { auditApi, authApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'

const tab = ref('audit')
const items = ref([])
const actions = ref([])
const summary = ref({})
const loginLogs = ref([])
const total = ref(0)
const loading = ref(false)
const days = ref(7)

const query = reactive({ keyword: '', action: '', username: '', page: 1, page_size: 50 })

function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function load() {
  loading.value = true
  try {
    const res = await auditApi.list({ ...query, days: days.value })
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadSummary() {
  try {
    summary.value = await auditApi.summary({ days: days.value })
  } catch (e) {
    summary.value = {}
  }
}

async function loadActions() {
  try {
    const res = await auditApi.actions()
    actions.value = res.items || []
  } catch (e) {
    actions.value = []
  }
}

async function loadLoginLogs() {
  try {
    const res = await authApi.loginLogs(200)
    loginLogs.value = res.items || []
  } catch (e) {
    loginLogs.value = []
  }
}

async function reload() {
  await Promise.all([load(), loadSummary(), loadActions(), loadLoginLogs()])
}

function onTabChange(name) {
  if (name === 'login') loadLoginLogs()
}

const topUsers = computed(() =>
  Object.entries(summary.value.by_user || {}).map(([name, count]) => ({ name, count })),
)

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

const actionOption = computed(() => {
  const entries = Object.entries(summary.value.by_action || {}).slice(0, 10)
  return {
    color: ['#3b82f6'],
    tooltip: { ...TIP.value, trigger: 'axis' },
    grid: { left: 170, right: 40, top: 12, bottom: 24 },
    xAxis: { type: 'value', axisLabel: AX.value, splitLine: SPL.value },
    yAxis: {
      type: 'category',
      data: entries.map((e) => e[0]).reverse(),
      axisLabel: { ...AX.value, width: 160, overflow: 'truncate' },
      axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        itemStyle: { color: '#60a5fa', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', fontSize: 11, color: '#6b7280' },
        data: entries.map((e) => e[1]).reverse(),
      },
    ],
  }
})

const userOption = computed(() => {
  const data = topUsers.value.slice(0, 10).reverse()
  return {
    color: ['#8b5cf6'],
    tooltip: { ...TIP.value, trigger: 'axis' },
    grid: { left: 100, right: 40, top: 12, bottom: 24 },
    xAxis: { type: 'value', axisLabel: AX.value, splitLine: SPL.value },
    yAxis: {
      type: 'category',
      data: data.map((d) => d.name),
      axisLabel: { ...AX.value, width: 90, overflow: 'truncate' },
      axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        itemStyle: { color: '#a78bfa', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', fontSize: 11, color: '#6b7280' },
        data: data.map((d) => d.count),
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

<style scoped>
.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}
</style>
