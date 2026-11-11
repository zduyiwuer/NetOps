<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">
        Telemetry 遥测
        <span class="sub">gNMI 流式遥测订阅与样本</span>
      </h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
        <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openConfig()">
          新增订阅
        </el-button>
      </div>
    </div>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="label"><el-icon><Monitor /></el-icon>Collector 服务</div>
        <div class="value" style="font-size: 20px" :class="status.collector_running ? 'text-ok' : 'text-muted'">
          {{ status.collector_running ? '运行中' : '未运行' }}
        </div>
        <div class="foot">gNMI 端口 {{ status.collector_port || '-' }}</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Connection /></el-icon>活跃会话</div>
        <div class="value">{{ (status.sessions || []).length }}</div>
        <div class="foot">设备接入 / 客户端</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><List /></el-icon>订阅配置</div>
        <div class="value">{{ configs.length }}</div>
        <div class="foot">启用 {{ enabledCount }} 条</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><DataLine /></el-icon>累计样本</div>
        <div class="value">{{ totalReceived }}</div>
        <div class="foot">已归一化为平台指标</div>
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="table-card" style="flex: 2; min-width: 560px">
        <div class="card-title">订阅配置</div>
        <el-table :data="configs" size="small" stripe>
          <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
          <el-table-column prop="device_name" label="设备" width="130" show-overflow-tooltip />
          <el-table-column label="模式" width="110" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.mode === 'collector' ? 'success' : 'warning'">
                {{ row.mode === 'collector' ? '设备推送' : '平台订阅' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="目标" min-width="180">
            <template #default="{ row }">
              <span v-if="row.mode === 'client'" class="mono">{{ row.target_addr }}:{{ row.target_port }}</span>
              <span v-else class="text-muted">设备主动连接本平台</span>
            </template>
          </el-table-column>
          <el-table-column label="订阅路径" width="90" align="right">
            <template #default="{ row }">{{ (row.subscribe_paths || []).length }}</template>
          </el-table-column>
          <el-table-column prop="sample_interval_ms" label="采样间隔" width="100" align="right">
            <template #default="{ row }">{{ row.sample_interval_ms }} ms</template>
          </el-table-column>
          <el-table-column prop="received_count" label="样本数" width="90" align="right" />
          <el-table-column label="状态" width="110" align="center">
            <template #default="{ row }">
              <el-tooltip v-if="row.status_message" :content="row.status_message" placement="top">
                <el-tag :type="statusType(row.status)" size="small" effect="plain">
                  {{ statusText(row.status) }}
                </el-tag>
              </el-tooltip>
              <el-tag v-else :type="statusType(row.status)" size="small" effect="plain">
                {{ statusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="userStore.canOperate" label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openConfig(row)">编辑</el-button>
              <el-button
                v-if="row.device_id"
                link
                type="success"
                size="small"
                @click="simulate(row)"
              >
                模拟推送
              </el-button>
              <el-button link type="danger" size="small" @click="removeConfig(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="table-card" style="flex: 1; min-width: 340px">
        <div class="card-title">活跃会话</div>
        <el-table :data="status.sessions || []" size="small" stripe max-height="300">
          <el-table-column prop="peer" label="对端" min-width="150" class-name="mono" show-overflow-tooltip />
          <el-table-column label="模式" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.mode === 'collector' ? '推送' : '订阅' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="received" label="样本" width="80" align="right" />
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="row.status === 'connected' ? 'success' : 'info'" size="small" effect="plain">
                {{ row.status === 'connected' ? '已连接' : '断开' }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <div class="gap-row" style="margin-top: 12px">
      <div class="table-card" style="flex: 1; min-width: 420px">
        <div class="card-title">
          <span>可订阅路径</span>
          <span class="text-muted" style="font-size: 12px">
            内置 {{ pathCounts.builtin }} 条 · 自定义 {{ pathCounts.custom }} 条
          </span>
          <el-input
            v-model="pathKeyword"
            placeholder="搜索路径 / 指标"
            clearable
            size="small"
            style="width: 170px; margin-left: 10px"
            :prefix-icon="Search"
          />
          <el-select
            v-model="pathCategory"
            placeholder="全部分类"
            clearable
            size="small"
            style="width: 120px; margin-left: 8px"
          >
            <el-option label="无线终端" value="wlan" />
            <el-option label="接口" value="iface" />
            <el-option label="系统" value="system" />
            <el-option label="自定义" value="custom" />
          </el-select>
          <el-button
            v-if="userStore.canOperate"
            size="small"
            type="primary"
            :icon="Plus"
            style="margin-left: 8px"
            @click="openPathDialog()"
          >
            新增路径
          </el-button>
        </div>
        <el-table :data="filteredPaths" size="small" stripe max-height="360">
          <el-table-column prop="path" label="遥测路径" min-width="260" class-name="mono" show-overflow-tooltip />
          <el-table-column prop="metric" label="平台指标" width="130" class-name="mono" show-overflow-tooltip />
          <el-table-column label="类型" width="90" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.per_interface ? 'warning' : 'info'">
                {{ row.per_interface ? '接口级' : '设备级' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.source === 'custom' ? 'success' : 'info'">
                {{ row.source === 'custom' ? '自定义' : '内置' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="70" align="center">
            <template #default="{ row }">
              <el-button
                v-if="row.source === 'custom' && userStore.isAdmin"
                link
                type="danger"
                size="small"
                @click="removePath(row)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="table-card" style="flex: 1; min-width: 420px">
        <div class="card-title">
          <span>遥测样本</span>
          <el-select v-model="sampleMetric" placeholder="全部指标" clearable style="width: 150px" @change="loadSamples">
            <el-option v-for="m in metricOptions" :key="m" :label="m" :value="m" />
          </el-select>
        </div>
        <el-table :data="samples" size="small" stripe max-height="360">
          <el-table-column prop="ts" label="时间" width="150">
            <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
          </el-table-column>
          <el-table-column prop="metric" label="指标" width="130" class-name="mono" show-overflow-tooltip />
          <el-table-column label="值" width="100" align="right">
            <template #default="{ row }">
              {{ row.value_num !== null ? row.value_num : row.value_text }}
            </template>
          </el-table-column>
          <el-table-column prop="interface" label="接口" width="150" show-overflow-tooltip />
          <el-table-column prop="source_path" label="原始路径" min-width="220" class-name="mono" show-overflow-tooltip />
        </el-table>
      </div>
    </div>

    <el-dialog v-model="configDialog" :title="configForm.id ? '编辑遥测订阅' : '新增遥测订阅'" width="680px">
      <el-form :model="configForm" label-width="120px" size="small">
        <el-form-item label="名称">
          <el-input v-model="configForm.name" placeholder="如：上海核心交换机遥测" />
        </el-form-item>
        <el-form-item label="关联设备">
          <DeviceSelect v-model="configForm.device_id" style="width: 100%" />
        </el-form-item>
        <el-form-item label="工作模式">
          <el-radio-group v-model="configForm.mode">
            <el-radio-button value="collector">设备推送（dial-out）</el-radio-button>
            <el-radio-button value="client">平台订阅（dial-in）</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
          <template #title>
            {{
              configForm.mode === 'collector'
                ? '设备主动连接平台 gNMI 服务（默认端口 57400）并推送遥测数据，平台侧无需额外配置目标地址。'
                : '平台主动连接设备的 gNMI 服务并订阅路径，需要填写设备地址与端口。'
            }}
          </template>
        </el-alert>
        <el-form-item v-if="configForm.mode === 'client'" label="设备地址">
          <el-input v-model="configForm.target_addr" placeholder="如：10.10.1.1" style="width: 240px" />
          <el-input-number v-model="configForm.target_port" :min="1" :max="65535" style="margin-left: 8px" />
        </el-form-item>
        <el-form-item label="采样间隔">
          <el-input-number v-model="configForm.sample_interval_ms" :min="1000" :step="1000" />
          <span class="text-muted" style="margin-left: 8px; font-size: 12px">毫秒</span>
        </el-form-item>
        <el-form-item label="订阅路径">
          <el-select
            v-model="configForm.subscribe_paths"
            multiple
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            placeholder="选择或输入自定义路径"
          >
            <el-option v-for="p in paths" :key="p.path" :label="p.path" :value="p.path" />
          </el-select>
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="configForm.username" style="width: 240px" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="configForm.password" type="password" show-password style="width: 240px" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="configForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="configDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveConfig">保存</el-button>
      </template>
    </el-dialog>

    <!-- 新增自定义遥测路径 -->
    <el-dialog v-model="pathDialog" title="新增自定义遥测路径" width="560px">
      <el-form ref="pathFormRef" :model="pathForm" :rules="pathRules" label-width="90px" size="small">
        <el-form-item label="遥测路径" prop="path">
          <el-input
            v-model="pathForm.path"
            placeholder="如 access-points/access-point/state/num-clients"
            class="mono"
          />
          <div class="form-tip">填写设备 gNMI 可订阅的完整路径（不含 origin 前缀）</div>
        </el-form-item>
        <el-form-item label="平台指标" prop="metric">
          <el-input v-model="pathForm.metric" placeholder="如 wlan_client_count" class="mono" />
          <div class="form-tip">采集到的值会归一化到该指标名，写入设备指标表</div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="pathForm.description" placeholder="这条路径采集什么（可选）" />
        </el-form-item>
        <el-form-item label="接口级">
          <el-switch v-model="pathForm.per_interface" />
          <span class="form-tip" style="margin-left: 10px">按接口/客户端维度分别采集</span>
        </el-form-item>
        <el-form-item label="数值缩放">
          <el-input-number v-model="pathForm.scale" :step="0.01" :precision="4" />
          <span class="form-tip" style="margin-left: 10px">
            千分之一等比例时填 0.001；不缩放填 1
          </span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pathDialog = false">取消</el-button>
        <el-button type="primary" :loading="pathSaving" @click="savePath">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, DataLine, List, Monitor, Plus, Refresh, Search } from '@element-plus/icons-vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { telemetryApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const status = ref({})
const configs = ref([])
const paths = ref([])
const samples = ref([])
const sampleMetric = ref(null)
const saving = ref(false)

// 路径筛选
const pathKeyword = ref('')
const pathCategory = ref('')

// 自定义路径对话框
const pathDialog = ref(false)
const pathSaving = ref(false)
const pathFormRef = ref()
const pathForm = reactive({
  path: '', metric: '', description: '', per_interface: false, scale: 1,
})
const pathRules = {
  path: [{ required: true, message: '请输入遥测路径', trigger: 'blur' }],
  metric: [{ required: true, message: '请输入平台指标名', trigger: 'blur' }],
}

const configDialog = ref(false)
const configForm = reactive({})

// 内置 / 自定义路径计数
const pathCounts = computed(() => ({
  builtin: paths.value.filter((p) => p.source !== 'custom').length,
  custom: paths.value.filter((p) => p.source === 'custom').length,
}))

// 无线终端相关路径的识别（用于分类筛选）
function isWlanPath(p) {
  return /wlan|wifi|wireless|access-point|client/i.test(`${p.path} ${p.metric}`)
}

// 按关键字与分类筛选路径
const filteredPaths = computed(() => {
  const kw = pathKeyword.value.trim().toLowerCase()
  return paths.value.filter((p) => {
    if (kw && !`${p.path} ${p.metric}`.toLowerCase().includes(kw)) return false
    if (!pathCategory.value) return true
    if (pathCategory.value === 'custom') return p.source === 'custom'
    if (pathCategory.value === 'wlan') return isWlanPath(p)
    if (pathCategory.value === 'iface') return p.per_interface || /^if_/.test(p.metric)
    if (pathCategory.value === 'system') {
      return !p.per_interface && !/^if_/.test(p.metric) && !isWlanPath(p)
    }
    return true
  })
})

const metricOptions = [
  'cpu_usage', 'mem_used', 'mem_available', 'mem_free', 'mem_total',
  'if_in_octets', 'if_out_octets', 'if_oper_status', 'if_admin_status',
  'if_descr', 'if_name', 'if_speed', 'if_in_errors', 'if_out_errors',
]

const enabledCount = computed(() => configs.value.filter((c) => c.enabled).length)
const totalReceived = computed(() =>
  configs.value.reduce((a, c) => a + (c.received_count || 0), 0),
)

function statusType(s) {
  return { connected: 'success', pending: 'info', error: 'danger', disconnected: 'warning' }[s] || 'info'
}
function statusText(s) {
  return { connected: '已连接', pending: '待连接', error: '异常', disconnected: '已断开' }[s] || s || '-'
}
function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function loadStatus() {
  try {
    status.value = await telemetryApi.status()
  } catch (e) {
    status.value = {}
  }
}
async function loadConfigs() {
  try {
    const res = await telemetryApi.configs()
    configs.value = res.items || []
  } catch (e) {
    configs.value = []
  }
}
async function loadPaths() {
  try {
    const res = await telemetryApi.paths()
    paths.value = res.items || []
  } catch (e) {
    paths.value = []
  }
}
async function loadSamples() {
  try {
    const res = await telemetryApi.samples({
      limit: 100,
      metric: sampleMetric.value || undefined,
    })
    samples.value = res.items || []
  } catch (e) {
    samples.value = []
  }
}

async function reload() {
  await Promise.all([loadStatus(), loadConfigs(), loadPaths(), loadSamples()])
}

function openConfig(row) {
  Object.keys(configForm).forEach((k) => delete configForm[k])
  Object.assign(
    configForm,
    {
      id: null, device_id: null, name: '', mode: 'collector', target_addr: '',
      target_port: 57400, subscribe_paths: [], sample_interval_ms: 10000,
      username: '', password: '', enabled: true,
    },
    row ? { ...row, password: '' } : {},
  )
  configDialog.value = true
}

async function saveConfig() {
  if (!configForm.name) {
    ElMessage.warning('请填写名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      device_id: configForm.device_id,
      name: configForm.name,
      mode: configForm.mode,
      target_addr: configForm.target_addr,
      target_port: configForm.target_port,
      subscribe_paths: configForm.subscribe_paths,
      sample_interval_ms: configForm.sample_interval_ms,
      username: configForm.username,
      password: configForm.password,
      enabled: configForm.enabled,
    }
    if (configForm.id) await telemetryApi.updateConfig(configForm.id, payload)
    else await telemetryApi.createConfig(payload)
    ElMessage.success('保存成功')
    configDialog.value = false
    reload()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function removeConfig(row) {
  try {
    await ElMessageBox.confirm(`确定删除订阅「${row.name}」吗？`, '提示', { type: 'warning' })
    await telemetryApi.deleteConfig(row.id)
    ElMessage.success('已删除')
    reload()
  } catch (e) {
    /* 取消 */
  }
}

// ---------------------------------------------------------------- 自定义路径
function openPathDialog() {
  Object.assign(pathForm, {
    path: '', metric: '', description: '', per_interface: false, scale: 1,
  })
  pathDialog.value = true
}

async function savePath() {
  const ok = await pathFormRef.value?.validate().catch(() => false)
  if (!ok) return
  pathSaving.value = true
  try {
    await telemetryApi.createPath({
      path: pathForm.path.trim(),
      metric: pathForm.metric.trim(),
      description: pathForm.description.trim(),
      per_interface: pathForm.per_interface,
      scale: pathForm.scale,
    })
    ElMessage.success('已添加，可在下方的订阅路径中选择')
    pathDialog.value = false
    await loadPaths()
  } catch (e) {
    /* 忽略 */
  } finally {
    pathSaving.value = false
  }
}

async function removePath(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除路径「${row.path}」吗？已使用该路径的订阅配置需要重新选择。`,
      '提示',
      { type: 'warning' },
    )
    await telemetryApi.deletePath(row.id)
    ElMessage.success('已删除')
    await loadPaths()
  } catch (e) {
    /* 取消 */
  }
}

async function simulate(row) {
  const res = await telemetryApi.simulate(row.device_id)
  ElMessage.success(`已注入 ${res.stored} 条遥测样本`)
  loadSamples()
  loadConfigs()
}

onMounted(async () => {
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>
