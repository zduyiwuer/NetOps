<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">设备管理 <span class="sub">共 {{ total }} 台设备</span></h2>
      <div class="toolbar">
        <el-button :icon="Upload" @click="importDialog = true">批量导入</el-button>
        <el-dropdown @command="onExport">
          <el-button :icon="Download">导出<el-icon class="el-icon--right"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="csv">导出 CSV</el-dropdown-item>
              <el-dropdown-item command="xlsx">导出 Excel</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openDialog()">
          新增设备
        </el-button>
      </div>
    </div>

    <div class="table-card">
      <div class="filter-bar">
        <el-input
          v-model="query.keyword"
          placeholder="搜索名称 / IP / 型号 / 序列号"
          clearable
          style="width: 240px"
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
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 120px" @change="load">
          <el-option label="在线" value="online" />
          <el-option label="离线" value="offline" />
          <el-option label="未知" value="unknown" />
        </el-select>
        <el-button :icon="Search" @click="load">查询</el-button>
        <el-button :icon="RefreshLeft" @click="resetQuery">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="items" size="small" stripe>
        <el-table-column label="设备名称" min-width="170" fixed>
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="$router.push(`/device/${row.id}`)">
              {{ row.name }}
            </el-link>
            <el-tag v-if="row.mock_mode" size="small" type="info" effect="plain" style="margin-left: 6px">
              模拟
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="ip" label="管理IP" width="130" class-name="mono" />
        <el-table-column label="厂商" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="vendorTagType(row.vendor)">{{ row.vendor_label || row.vendor }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="型号" width="150" show-overflow-tooltip />
        <el-table-column prop="store_name" label="门店" width="120" show-overflow-tooltip />
        <el-table-column prop="location" label="位置" width="150" show-overflow-tooltip />
        <el-table-column label="采集通道" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.use_snmp" size="small" effect="plain" style="margin: 1px">SNMP</el-tag>
            <el-tag v-if="row.use_telemetry" size="small" effect="plain" type="success" style="margin: 1px">
              Telemetry
            </el-tag>
            <el-tag v-if="row.use_netflow" size="small" effect="plain" type="warning" style="margin: 1px">
              NetFlow
            </el-tag>
            <span v-if="!row.use_snmp && !row.use_telemetry && !row.use_netflow" class="text-muted">未启用</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="dark">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="asset_tag" label="资产编号" width="130" class-name="mono" />
        <el-table-column prop="serial_number" label="序列号" width="150" class-name="mono" show-overflow-tooltip />
        <el-table-column label="采集" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
              {{ row.enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="$router.push(`/device/${row.id}`)">详情</el-button>
            <el-button link type="primary" size="small" :loading="testingId === row.id" @click="testConn(row)">
              测试
            </el-button>
            <el-button v-if="userStore.canOperate" link type="primary" size="small" @click="openDialog(row)">
              编辑
            </el-button>
            <el-button v-if="userStore.isAdmin" link type="danger" size="small" @click="remove(row)">删除</el-button>
          </template>
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

    <!-- 新增/编辑对话框 -->
    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑设备' : '新增设备'" width="860px" top="6vh">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="120px" size="small">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="基本信息" name="basic">
            <el-row :gutter="16">
              <el-col :span="12">
                <el-form-item label="设备名称" prop="name">
                  <el-input v-model="form.name" placeholder="如：上海核心交换机" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="管理 IP" prop="ip">
                  <el-input v-model="form.ip" placeholder="如：10.10.1.1" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="厂商" prop="vendor">
                  <el-select v-model="form.vendor" style="width: 100%">
                    <el-option v-for="v in vendors" :key="v.value" :label="v.label" :value="v.value" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="设备类型">
                  <el-select v-model="form.device_type" style="width: 100%">
                    <el-option label="交换机" value="switch" />
                    <el-option label="路由器" value="router" />
                    <el-option label="防火墙" value="firewall" />
                    <el-option label="无线控制器" value="wlc" />
                    <el-option label="无线AP" value="ap" />
                    <el-option label="网关" value="gateway" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="型号"><el-input v-model="form.model" /></el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="序列号"><el-input v-model="form.serial_number" /></el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="所属门店">
                  <el-select v-model="form.store_id" clearable style="width: 100%">
                    <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="位置"><el-input v-model="form.location" /></el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="采集间隔(秒)">
                  <el-input-number v-model="form.poll_interval" :min="10" :max="3600" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :span="24">
                <el-form-item label="模拟模式">
                  <el-switch v-model="form.mock_mode" />
                  <span class="text-muted" style="margin-left: 10px; font-size: 12px">
                    开启后不连接真实设备，使用模拟数据（演示 / 测试用）
                  </span>
                </el-form-item>
              </el-col>
            </el-row>
          </el-tab-pane>

          <el-tab-pane label="SNMP 配置" name="snmp">
            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="SNMP 版本">
                  <el-select v-model="form.snmp_version" style="width: 100%">
                    <el-option label="v1" value="1" />
                    <el-option label="v2c" value="2c" />
                    <el-option label="v3" value="3" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="Community">
                  <el-input v-model="form.snmp_community" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="端口">
                  <el-input-number v-model="form.snmp_port" :min="1" :max="65535" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-divider content-position="left">SNMPv3 参数</el-divider>
            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="用户名"><el-input v-model="form.snmp_v3_user" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="认证算法">
                  <el-select v-model="form.snmp_v3_auth_proto" style="width: 100%">
                    <el-option v-for="p in ['MD5','SHA','SHA224','SHA256','SHA384','SHA512']" :key="p" :label="p" :value="p" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="认证密钥"><el-input v-model="form.snmp_v3_auth_key" show-password /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="加密算法">
                  <el-select v-model="form.snmp_v3_priv_proto" style="width: 100%">
                    <el-option v-for="p in ['DES','3DES','AES','AES192','AES256']" :key="p" :label="p" :value="p" />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :span="16">
                <el-form-item label="加密密钥"><el-input v-model="form.snmp_v3_priv_key" show-password /></el-form-item>
              </el-col>
            </el-row>
          </el-tab-pane>

          <el-tab-pane label="采集通道" name="channels">
            <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px">
              <template #title>采集通道说明</template>
              <div style="font-size: 13px; line-height: 1.8">
                <b>SNMP 轮询</b>：平台主动 GET/BULK 采集接口计数器与 CPU/内存，适用于绝大多数设备。<br />
                <b>Telemetry（gNMI）</b>：设备主动向平台推送流式遥测数据，实时性最好；
                需在「Telemetry 遥测」页创建订阅，遥测数据会写入同一套指标表。<br />
                <b>NetFlow</b>：设备导出流记录到平台，用于五元组会话分析与流量 TopN，
                需在「NetFlow 分析」页配置导出器。
              </div>
            </el-alert>
            <el-form-item label="启用通道">
              <el-checkbox v-model="form.use_snmp">SNMP 轮询</el-checkbox>
              <el-checkbox v-model="form.use_telemetry">Telemetry（gNMI 推送）</el-checkbox>
              <el-checkbox v-model="form.use_netflow">NetFlow 流量导出</el-checkbox>
            </el-form-item>
            <el-alert
              v-if="!form.use_snmp && !form.use_telemetry"
              type="warning"
              :closable="false"
              show-icon
              title="未启用任何采集通道，该设备将不会被采集"
            />
          </el-tab-pane>

          <el-tab-pane label="SSH 配置" name="ssh">
            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="SSH 端口">
                  <el-input-number v-model="form.ssh_port" :min="1" :max="65535" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="用户名"><el-input v-model="form.ssh_username" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="密码"><el-input v-model="form.ssh_password" show-password /></el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="Enable 密码">
                  <el-input v-model="form.ssh_enable_password" show-password />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="采集项">
                  <el-checkbox v-model="form.collect_interface">接口数据</el-checkbox>
                  <el-checkbox v-model="form.collect_system">CPU/内存</el-checkbox>
                  <el-checkbox v-model="form.collect_log">日志</el-checkbox>
                  <el-checkbox v-model="form.collect_backup">配置备份</el-checkbox>
                </el-form-item>
              </el-col>
            </el-row>
          </el-tab-pane>

          <el-tab-pane label="资产信息" name="asset">
            <el-row :gutter="16">
              <el-col :span="8">
                <el-form-item label="资产编号"><el-input v-model="form.asset_tag" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="固件版本"><el-input v-model="form.firmware_version" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="MAC 地址"><el-input v-model="form.mac" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="采购日期"><el-input v-model="form.purchase_date" placeholder="YYYY-MM-DD" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="保修到期"><el-input v-model="form.warranty_end" placeholder="YYYY-MM-DD" /></el-form-item>
              </el-col>
              <el-col :span="8">
                <el-form-item label="价格(元)">
                  <el-input-number v-model="form.price" :min="0" :step="100" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="厂商联系方式"><el-input v-model="form.vendor_contact" /></el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="合同编号"><el-input v-model="form.contract_no" /></el-form-item>
              </el-col>
              <el-col :span="24">
                <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
              </el-col>
            </el-row>
          </el-tab-pane>
        </el-tabs>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入对话框 -->
    <el-dialog v-model="importDialog" title="批量导入设备" width="620px">
      <el-steps :active="importStep" simple style="margin-bottom: 18px">
        <el-step title="下载模板" />
        <el-step title="填写数据" />
        <el-step title="上传导入" />
      </el-steps>

      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px">
        <template #title>导入说明</template>
        支持 .xlsx / .csv 文件。表头需与模板一致；门店编码不存在时会自动创建门店；
        同 IP 设备默认跳过，勾选「覆盖已存在」可更新。
      </el-alert>

      <div class="import-actions">
        <el-button :icon="Download" @click="downloadTemplate('xlsx')">下载 Excel 模板</el-button>
        <el-button :icon="Download" @click="downloadTemplate('csv')">下载 CSV 模板</el-button>
      </div>

      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx,.csv"
        :on-change="onFileChange"
        :file-list="fileList"
        style="margin: 14px 0"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">仅支持 xlsx / csv 格式</div>
        </template>
      </el-upload>

      <el-checkbox v-model="updateExisting" style="margin-bottom: 12px">覆盖已存在的同 IP 设备</el-checkbox>

      <el-result v-if="importResult" :icon="importResult.created || importResult.updated ? 'success' : 'warning'" :title="importSummary">
        <template #extra>
          <div v-if="importResult.errors?.length" class="import-errors">
            <div v-for="(e, i) in importResult.errors" :key="i">{{ e }}</div>
          </div>
        </template>
      </el-result>

      <template #footer>
        <el-button @click="importDialog = false">关闭</el-button>
        <el-button type="primary" :loading="importing" :disabled="!selectedFile" @click="doImport">
          开始导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowDown, Download, Plus, RefreshLeft, Search, Upload, UploadFilled,
} from '@element-plus/icons-vue'
import { deviceApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const items = ref([])
const stores = ref([])
const vendors = ref([])
const total = ref(0)
const loading = ref(false)
const saving = ref(false)
const testingId = ref(null)

const query = reactive({
  keyword: '', store_id: null, vendor: null, status: null, page: 1, page_size: 20,
})

const dialogVisible = ref(false)
const formRef = ref()
const activeTab = ref('basic')
const form = reactive({})

const rules = {
  name: [{ required: true, message: '请输入设备名称', trigger: 'blur' }],
  ip: [
    { required: true, message: '请输入管理 IP', trigger: 'blur' },
    {
      pattern: /^(\d{1,3}\.){3}\d{1,3}$/,
      message: 'IP 格式不正确',
      trigger: 'blur',
    },
  ],
  vendor: [{ required: true, message: '请选择厂商', trigger: 'change' }],
}

function statusType(s) {
  return { online: 'success', offline: 'danger', unknown: 'info' }[s] || 'info'
}
function statusText(s) {
  return { online: '在线', offline: '离线', unknown: '未知' }[s] || s
}
function vendorTagType(v) {
  return { h3c: 'warning', huawei: 'danger', cisco: 'primary', routeros: 'success' }[v] || 'info'
}

async function load() {
  loading.value = true
  try {
    const res = await deviceApi.list({ ...query })
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

function resetQuery() {
  Object.assign(query, { keyword: '', store_id: null, vendor: null, status: null, page: 1 })
  load()
}

function openDialog(row) {
  activeTab.value = 'basic'
  const defaults = {
    name: '', ip: '', vendor: 'h3c', device_type: 'switch', model: '', serial_number: '',
    mac: '', location: '', store_id: null, snmp_version: '2c', snmp_community: 'public',
    snmp_port: 161, snmp_v3_user: '', snmp_v3_auth_proto: 'SHA', snmp_v3_auth_key: '',
    snmp_v3_priv_proto: 'AES', snmp_v3_priv_key: '', ssh_port: 22, ssh_username: '',
    ssh_password: '', ssh_enable_password: '', poll_interval: 60, collect_interface: true,
    collect_system: true, collect_log: true, collect_backup: true, mock_mode: false,
    use_snmp: true, use_telemetry: false, use_netflow: false,
    asset_tag: '', firmware_version: '', purchase_date: '', warranty_end: '',
    vendor_contact: '', contract_no: '', price: 0, remark: '', enabled: true,
  }
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(form, defaults, row ? { ...row } : {})
  dialogVisible.value = true
}

async function save() {
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return
  saving.value = true
  try {
    const payload = { ...form }
    delete payload.id
    delete payload.vendor_label
    delete payload.store_name
    delete payload.status
    delete payload.last_seen
    delete payload.created_at
    if (form.id) await deviceApi.update(form.id, payload)
    else await deviceApi.create(payload)
    ElMessage.success('保存成功')
    dialogVisible.value = false
    load()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除设备「${row.name}」吗？相关历史数据将一并删除。`, '删除确认', {
      type: 'warning',
    })
    await deviceApi.remove(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    /* 取消 */
  }
}

async function testConn(row) {
  testingId.value = row.id
  try {
    const res = await deviceApi.test(row.id)
    if (res.snmp_ok) {
      ElMessage.success(
        `连通正常 · 响应 ${res.response_ms}ms · CPU ${res.cpu_usage ?? '-'}% · 接口 ${res.interfaces.length}`,
      )
    } else {
      ElMessage.warning(`SNMP 不可达：${res.error || '超时'}`)
    }
  } catch (e) {
    /* 忽略 */
  } finally {
    testingId.value = null
  }
}

// ---------------------------------------------------------------- 导入导出
const importDialog = ref(false)
const importStep = ref(0)
const importing = ref(false)
const fileList = ref([])
const selectedFile = ref(null)
const updateExisting = ref(false)
const importResult = ref(null)

const importSummary = computed(() => {
  const r = importResult.value
  if (!r) return ''
  return `新增 ${r.created} 台，更新 ${r.updated} 台，跳过 ${r.skipped} 台`
})

function onFileChange(file) {
  fileList.value = [file]
  selectedFile.value = file.raw
  importStep.value = 2
}

async function downloadTemplate(type) {
  const res = type === 'csv' ? await deviceApi.importTemplateCsv() : await deviceApi.importTemplateXlsx()
  const blob = new Blob([res.data])
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `设备导入模板.${type}`
  a.click()
  URL.revokeObjectURL(url)
  importStep.value = 1
}

async function doImport() {
  importing.value = true
  try {
    const res = await deviceApi.importDevices(selectedFile.value, updateExisting.value)
    importResult.value = res
    importStep.value = 3
    ElMessage.success(`导入完成：新增 ${res.created}，更新 ${res.updated}`)
    load()
  } catch (e) {
    /* 忽略 */
  } finally {
    importing.value = false
  }
}

async function onExport(type) {
  const params = {}
  if (query.store_id) params.store_id = query.store_id
  if (query.vendor) params.vendor = query.vendor
  const res = type === 'csv' ? await deviceApi.exportCsv(params) : await deviceApi.exportXlsx(params)
  const blob = new Blob([res.data])
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `设备清单_${new Date().toISOString().slice(0, 10)}.${type}`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('导出成功')
}

onMounted(async () => {
  await load()
  try {
    const [s, v] = await Promise.all([deviceApi.stores(), deviceApi.vendors()])
    stores.value = s.items || []
    vendors.value = v.items || []
  } catch (e) {
    /* 忽略 */
  }
  window.addEventListener('netops:refresh', load)
})
onUnmounted(() => window.removeEventListener('netops:refresh', load))
</script>

<style scoped>
.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}

.import-actions {
  display: flex;
  gap: 10px;
}

.import-errors {
  text-align: left;
  font-size: 12px;
  color: var(--netops-danger);
  max-height: 140px;
  overflow-y: auto;
}
</style>
