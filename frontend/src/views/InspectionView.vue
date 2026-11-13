<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">基线巡检 <span class="sub">自动化基线核查与报告生成</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
        <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openTask()">
          发起巡检
        </el-button>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="巡检任务" name="tasks">
        <div class="table-card">
          <el-table border v-loading="loading" :data="tasks" size="small" stripe>
            <el-table-column prop="name" label="任务名称" min-width="170" />
            <el-table-column prop="template_name" label="巡检模板" width="160" show-overflow-tooltip />
            <el-table-column label="范围" width="130">
              <template #default="{ row }">{{ scopeText(row) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90" align="center">
              <template #default="{ row }">
                <el-tooltip v-if="row.remark" :content="row.remark" placement="top">
                  <el-tag :type="statusType(row.status)" size="small" effect="plain">
                    {{ statusText(row.status) }}
                  </el-tag>
                </el-tooltip>
                <el-tag v-else :type="statusType(row.status)" size="small" effect="plain">
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="设备数" width="80" align="right">
              <template #default="{ row }">{{ row.total }}</template>
            </el-table-column>
            <el-table-column label="通过项" width="80" align="right">
              <template #default="{ row }">
                <span class="text-ok">{{ row.passed }}</span>
              </template>
            </el-table-column>
            <el-table-column label="不通过" width="80" align="right">
              <template #default="{ row }">
                <span :class="row.failed ? 'text-danger' : ''">{{ row.failed }}</span>
              </template>
            </el-table-column>
            <el-table-column label="跳过" width="75" align="right">
              <template #default="{ row }">{{ row.warn }}</template>
            </el-table-column>
            <el-table-column prop="operator" label="执行人" width="90" />
            <el-table-column label="完成时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.finished_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="showDetail(row)">详情</el-button>
                <el-button
                  v-if="row.report_file"
                  link
                  type="primary"
                  size="small"
                  @click="openReport(row)"
                >
                  报告
                </el-button>
                <el-button
                  v-if="row.report_file"
                  link
                  type="primary"
                  size="small"
                  @click="downloadExcel(row)"
                >
                  Excel
                </el-button>
                <el-button
                  v-if="userStore.canOperate"
                  link
                  type="danger"
                  size="small"
                  @click="remove(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="巡检模板" name="templates">
        <div class="table-card">
          <div class="filter-bar">
            <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openTemplate()">
              新增模板
            </el-button>
            <span class="text-muted">基线模板定义检查项与阈值，可按厂商定制</span>
          </div>
          <el-table border :data="templates" size="small" stripe>
            <el-table-column prop="name" label="模板名称" min-width="200" />
            <el-table-column label="适用厂商" width="140">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.vendor || '通用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
            <el-table-column label="检查项" width="90" align="right">
              <template #default="{ row }">{{ (row.items || []).length }}</template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="viewItems(row)">查看检查项</el-button>
                <el-button
                  v-if="userStore.isAdmin"
                  link
                  type="danger"
                  size="small"
                  @click="removeTemplate(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 发起巡检 -->
    <el-dialog v-model="taskDialog" title="发起基线巡检" width="560px">
      <el-form :model="taskForm" label-width="110px" size="small">
        <el-form-item label="任务名称">
          <el-input v-model="taskForm.name" placeholder="如：2026年11月全网基线巡检" />
        </el-form-item>
        <el-form-item label="巡检模板">
          <el-select v-model="taskForm.template_id" style="width: 100%">
            <el-option
              v-for="t in templates"
              :key="t.id"
              :label="t.name"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="巡检范围">
          <el-radio-group v-model="taskForm.scope_type">
            <el-radio-button value="all">全部设备</el-radio-button>
            <el-radio-button value="store">指定门店</el-radio-button>
            <el-radio-button value="device">指定设备</el-radio-button>
            <el-radio-button value="scheduled">计划任务</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="taskForm.scope_type === 'store'" label="门店">
          <el-select v-model="taskForm.scope_value" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="String(s.id)" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="taskForm.scope_type === 'device'" label="设备">
          <DeviceSelect v-model="deviceId" style="width: 100%" />
        </el-form-item>
        <el-alert
          v-if="taskForm.scope_type === 'scheduled'"
          type="info"
          :closable="false"
          show-icon
          title="计划任务将进入待执行队列，由调度器按小时检查并自动执行"
        />
      </el-form>
      <template #footer>
        <el-button @click="taskDialog = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="runTask">开始巡检</el-button>
      </template>
    </el-dialog>

    <!-- 巡检详情 -->
    <el-dialog v-model="detailDialog" title="巡检结果详情" width="1100px" top="5vh">
      <div v-if="detail" class="detail-summary">
        <div class="ds-item"><span>巡检设备</span><b>{{ detail.total }}</b></div>
        <div class="ds-item ok"><span>通过检查项</span><b>{{ detail.passed }}</b></div>
        <div class="ds-item danger"><span>不通过项</span><b>{{ detail.failed }}</b></div>
        <div class="ds-item muted"><span>跳过项</span><b>{{ detail.warn }}</b></div>
        <div class="ds-item">
          <span>合规得分</span>
          <b :class="score >= 90 ? 'text-ok' : score >= 75 ? 'text-warn' : 'text-danger'">{{ score }}%</b>
        </div>
      </div>
      <el-table border :data="detailRows" size="small" stripe max-height="520">
        <el-table-column prop="store_name" label="门店" width="110" show-overflow-tooltip />
        <el-table-column prop="device_name" label="设备" width="140" show-overflow-tooltip />
        <el-table-column prop="device_ip" label="IP" width="125" class-name="mono" />
        <el-table-column label="检查项" min-width="380">
          <template #default="{ row }">
            <div v-for="c in row.checks" :key="c.key" class="check-line">
              <el-tag :type="checkType(c.status)" size="small" effect="plain">
                {{ checkText(c.status) }}
              </el-tag>
              <span class="c-title">{{ c.title }}</span>
              <span class="mono c-msg">{{ c.message }}</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 检查项查看 -->
    <el-dialog v-model="itemsDialog" title="检查项定义" width="760px">
      <el-table border :data="currentItems" size="small" stripe>
        <el-table-column prop="title" label="检查项" width="140" />
        <el-table-column prop="metric" label="指标" width="120" class-name="mono" />
        <el-table-column label="基线" width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.operator }} {{ row.threshold }} {{ row.unit }}</span>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="severityType(row.level)" size="small" effect="plain">
              {{ severityText(row.level) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="desc" label="说明" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { deviceApi, inspectionApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const tab = ref('tasks')
const tasks = ref([])
const templates = ref([])
const stores = ref([])
const loading = ref(false)
const running = ref(false)

const taskDialog = ref(false)
const taskForm = reactive({})
const deviceId = ref(null)

const detailDialog = ref(false)
const detail = ref(null)
const detailRows = ref([])

const itemsDialog = ref(false)
const currentItems = ref([])

function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}
function statusType(s) {
  return { success: 'success', running: 'warning', pending: 'info', failed: 'danger' }[s] || 'info'
}
function statusText(s) {
  return { success: '已完成', running: '执行中', pending: '待执行', failed: '失败' }[s] || s
}
function scopeText(row) {
  if (row.scope_type === 'store') {
    const s = stores.value.find((x) => String(x.id) === String(row.scope_value))
    return s ? s.name : '指定门店'
  }
  if (row.scope_type === 'device') return '指定设备'
  if (row.scope_type === 'scheduled') return '计划任务'
  return '全部设备'
}
function checkType(s) {
  return { pass: 'success', fail: 'danger', skip: 'info' }[s] || 'info'
}
function checkText(s) {
  return { pass: '通过', fail: '不通过', skip: '跳过' }[s] || s
}
function severityType(s) {
  return { critical: 'danger', warning: 'warning', info: 'info' }[s] || 'info'
}
function severityText(s) {
  return { critical: '严重', warning: '警告', info: '提示' }[s] || s
}

const score = computed(() => {
  if (!detail.value) return 0
  // 合规得分 = 通过 / 已判定项（跳过项不计入分母）
  let judged = 0
  let passed = 0
  for (const r of detail.value.results || []) {
    const checks = r.checks || []
    if (checks.length) {
      const valid = checks.filter((c) => c.status === 'pass' || c.status === 'fail')
      judged += valid.length
      passed += valid.filter((c) => c.status === 'pass').length
    } else {
      judged += r.summary?.total || 0
      passed += r.summary?.passed || 0
    }
  }
  if (!judged) return 0
  return Math.round((passed / judged) * 10000) / 100
})

async function loadTasks() {
  loading.value = true
  try {
    const res = await inspectionApi.tasks({ page: 1, page_size: 100 })
    tasks.value = res.items || []
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadTemplates() {
  try {
    const res = await inspectionApi.templates()
    templates.value = res.items || []
  } catch (e) {
    templates.value = []
  }
}

async function reload() {
  await Promise.all([loadTasks(), loadTemplates()])
}

function onTabChange(name) {
  if (name === 'templates') loadTemplates()
}

function openTask() {
  Object.keys(taskForm).forEach((k) => delete taskForm[k])
  Object.assign(taskForm, {
    name: `基线巡检 ${new Date().toISOString().slice(0, 10)}`,
    template_id: templates.value[0]?.id || null,
    scope_type: 'all',
    scope_value: '',
  })
  deviceId.value = null
  taskDialog.value = true
}

async function runTask() {
  if (!taskForm.template_id) {
    ElMessage.warning('请选择巡检模板')
    return
  }
  if (taskForm.scope_type === 'device' && !deviceId.value) {
    ElMessage.warning('请选择设备')
    return
  }
  if (taskForm.scope_type === 'store' && !taskForm.scope_value) {
    ElMessage.warning('请选择门店')
    return
  }
  running.value = true
  try {
    const payload = {
      name: taskForm.name,
      template_id: taskForm.template_id,
      scope_type: taskForm.scope_type,
      scope_value:
        taskForm.scope_type === 'device'
          ? String(deviceId.value)
          : taskForm.scope_type === 'store'
            ? taskForm.scope_value
            : '',
    }
    const res = await inspectionApi.createTask(payload)
    if (res.status === 'pending') {
      ElMessage.success('计划巡检任务已创建，将由调度器自动执行')
    } else {
      ElMessage.success(
        `巡检完成：${res.devices} 台设备，通过 ${res.passed} 项，不通过 ${res.failed} 项`,
      )
    }
    taskDialog.value = false
    loadTasks()
  } catch (e) {
    /* 忽略 */
  } finally {
    running.value = false
  }
}

async function showDetail(row) {
  const res = await inspectionApi.task(row.id)
  detail.value = res
  detailRows.value = res.results || []
  detailDialog.value = true
}

async function openReport(row) {
  const token = localStorage.getItem('netops_token')
  const resp = await fetch(inspectionApi.reportUrl(row.id), {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!resp.ok) {
    ElMessage.warning('报告尚未生成')
    return
  }
  const html = await resp.text()
  // 在新窗口渲染报告内容
  const win = window.open('', '_blank')
  if (!win) {
    ElMessage.warning('浏览器阻止了新窗口，请允许弹出窗口')
    return
  }
  win.document.write(html)
  win.document.close()
}

async function downloadExcel(row) {
  const token = localStorage.getItem('netops_token')
  const resp = await fetch(inspectionApi.reportExcelUrl(row.id), {
    headers: { Authorization: `Bearer ${token}` },
  })
  const blob = await resp.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `巡检报告_${row.id}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除巡检任务「${row.name}」吗？`, '提示', { type: 'warning' })
    await inspectionApi.deleteTask(row.id)
    ElMessage.success('已删除')
    loadTasks()
  } catch (e) {
    /* 取消 */
  }
}

function viewItems(row) {
  currentItems.value = row.items || []
  itemsDialog.value = true
}

async function removeTemplate(row) {
  try {
    await ElMessageBox.confirm(`确定删除模板「${row.name}」吗？`, '提示', { type: 'warning' })
    await inspectionApi.deleteTemplate(row.id)
    ElMessage.success('已删除')
    loadTemplates()
  } catch (e) {
    /* 取消 */
  }
}

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

<style scoped>
.detail-summary {
  display: flex;
  gap: 14px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.ds-item {
  background: #f9fafb;
  border: 1px solid var(--netops-border);
  border-radius: 8px;
  padding: 8px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.ds-item span {
  font-size: 12px;
  color: var(--netops-text-muted);
}

.ds-item b {
  font-size: 20px;
}

.ds-item.ok b {
  color: var(--netops-ok);
}
.ds-item.danger b {
  color: var(--netops-danger);
}
.ds-item.muted b {
  color: #94a3b8;
}

.check-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  flex-wrap: wrap;
}

.c-title {
  font-weight: 500;
  min-width: 100px;
}

.c-msg {
  font-size: 12px;
  color: var(--netops-text-muted);
}
</style>
