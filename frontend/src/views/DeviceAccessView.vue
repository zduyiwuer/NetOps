<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">设备接入 <span class="sub">设备本地账号与统一认证服务</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <!-- 设备本地账号 -->
      <el-tab-pane label="设备账号" name="accounts">
        <div class="table-card">
          <div class="filter-bar">
            <DeviceSelect
              v-model="deviceFilter"
              placeholder="全部设备"
              style="width: 200px"
              @change="loadAccounts"
            />
            <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openAccount()">
              新增账号
            </el-button>
            <span class="text-muted">
              设备本地账号用于平台代登录设备，也可在设备侧作为本地认证账户
            </span>
          </div>

          <el-table v-loading="loading" :data="accounts" size="small" stripe>
            <el-table-column prop="device_name" label="设备" width="150" show-overflow-tooltip />
            <el-table-column prop="device_ip" label="管理IP" width="130" class-name="mono" />
            <el-table-column prop="username" label="用户名" width="140" class-name="mono" />
            <el-table-column prop="password" label="密码" width="110" align="center">
              <template #default="{ row }">
                <span class="mono text-muted">{{ row.password || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="Enable密码" width="110" align="center">
              <template #default="{ row }">
                <span class="mono text-muted">{{ row.enable_password || '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="权限级别" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ row.privilege }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="认证方式" width="100" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.auth_type === 'tacacs' ? 'warning' : 'info'" effect="plain">
                  {{ { local: '本地', tacacs: 'TACACS', radius: 'RADIUS' }[row.auth_type] || row.auth_type }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'" effect="plain">
                  {{ row.enabled ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_by" label="创建人" width="100" show-overflow-tooltip />
            <el-table-column label="创建时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column v-if="userStore.canOperate" label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="openAccount(row)">编辑</el-button>
                <el-button link type="danger" size="small" @click="removeAccount(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 统一认证服务 -->
      <el-tab-pane label="统一认证 (TACACS/RADIUS)" name="aaa">
        <div class="gap-row">
          <div class="table-card" style="flex: 1; min-width: 400px">
            <div class="card-title">
              <span>AAA 认证服务</span>
              <el-switch
                v-model="aaaForm.enabled"
                :disabled="!userStore.isAdmin"
                active-text="启用"
                @change="saveAaa"
              />
            </div>
            <el-form label-width="130px" size="small" :disabled="!userStore.isAdmin">
              <el-form-item label="共享密钥">
                <el-input
                  v-model="aaaForm.tacacs_secret"
                  type="password"
                  show-password
                  placeholder="设备与平台之间的 TACACS+ 共享密钥"
                />
              </el-form-item>
              <el-form-item label="TACACS+ 端口">
                <el-input-number v-model="aaaForm.tacacs_port" :min="1" :max="65535" />
                <span class="text-muted" style="margin-left: 8px; font-size: 12px">TCP，默认 49</span>
              </el-form-item>
              <el-form-item label="RADIUS 端口">
                <el-input-number v-model="aaaForm.radius_port" :min="1" :max="65535" />
                <span class="text-muted" style="margin-left: 8px; font-size: 12px">UDP，默认 1812</span>
              </el-form-item>
            </el-form>
            <div style="display: flex; gap: 10px; flex-wrap: wrap">
              <el-button v-if="userStore.isAdmin" type="primary" :icon="Check" :loading="saving" @click="saveAaa">
                保存并重启服务
              </el-button>
              <el-button :icon="Connection" @click="testAaa">测试认证</el-button>
            </div>

            <el-descriptions :column="2" size="small" border style="margin-top: 16px">
              <el-descriptions-item label="TACACS+">
                <el-tag size="small" :type="aaaStatus.tacacs?.running ? 'success' : 'info'" effect="plain">
                  {{ aaaStatus.tacacs?.running ? `运行中 :${aaaStatus.tacacs?.port}` : '未运行' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="RADIUS">
                <el-tag size="small" :type="aaaStatus.radius?.running ? 'success' : 'info'" effect="plain">
                  {{ aaaStatus.radius?.running ? `运行中 :${aaaStatus.radius?.port}` : '未运行' }}
                </el-tag>
              </el-descriptions-item>
            </el-descriptions>

            <el-alert type="info" :closable="false" show-icon style="margin-top: 14px">
              <template #title>设备侧配置示例</template>
              <div class="cfg-sample mono">
                <div># Cisco / H3C / 华为</div>
                <div>tacacs server 10.10.0.100</div>
                <div>tacacs key 0 &lt;共享密钥&gt;</div>
                <div>aaa authentication login default group tacacs+ local</div>
                <div>aaa authorization exec default group tacacs+ local</div>
              </div>
            </el-alert>
          </div>

          <div class="table-card" style="flex: 1; min-width: 420px">
            <div class="card-title">
              <span>认证会话日志</span>
              <el-select v-model="sessionFilter" placeholder="全部结果" clearable size="small"
                style="width: 110px" @change="loadSessions">
                <el-option label="成功" value="pass" />
                <el-option label="失败" value="fail" />
              </el-select>
            </div>
            <el-table :data="sessions" size="small" stripe max-height="440">
              <el-table-column label="时间" width="150">
                <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="协议" width="80" align="center">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain">{{ row.protocol.toUpperCase() }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="username" label="用户" width="110" show-overflow-tooltip />
              <el-table-column prop="nas_ip" label="设备IP" width="125" class-name="mono" />
              <el-table-column label="结果" width="75" align="center">
                <template #default="{ row }">
                  <el-tag size="small" :type="row.result === 'pass' ? 'success' : 'danger'" effect="plain">
                    {{ row.result === 'pass' ? '通过' : '拒绝' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="privilege" label="权限" width="65" align="center" />
              <el-table-column prop="reason" label="说明" min-width="150" show-overflow-tooltip />
            </el-table>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 账号编辑 -->
    <el-dialog v-model="accountDialog" :title="accountForm.id ? '编辑设备账号' : '新增设备账号'" width="560px">
      <el-form :model="accountForm" label-width="110px" size="small">
        <el-form-item label="所属设备">
          <DeviceSelect v-model="accountForm.device_id" placeholder="选择设备" width="100%" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="accountForm.username" placeholder="如：netops" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="accountForm.password" type="password" show-password
            :placeholder="accountForm.id ? '留空表示不修改' : ''" />
        </el-form-item>
        <el-form-item label="Enable密码">
          <el-input v-model="accountForm.enable_password" type="password" show-password
            :placeholder="accountForm.id ? '留空表示不修改' : ''" />
        </el-form-item>
        <el-form-item label="权限级别">
          <el-input-number v-model="accountForm.privilege" :min="0" :max="15" />
          <span class="text-muted" style="margin-left: 8px; font-size: 12px">15 = enable 全部权限</span>
        </el-form-item>
        <el-form-item label="认证方式">
          <el-select v-model="accountForm.auth_type" style="width: 100%">
            <el-option label="本地账号" value="local" />
            <el-option label="TACACS+" value="tacacs" />
            <el-option label="RADIUS" value="radius" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="accountForm.remark" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="accountForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="accountDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveAccount">保存</el-button>
      </template>
    </el-dialog>

    <!-- 认证测试 -->
    <el-dialog v-model="testDialog" title="测试认证" width="480px">
      <el-form label-width="110px" size="small">
        <el-form-item label="用户名">
          <el-input v-model="testForm.username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="testForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="设备IP">
          <el-input v-model="testForm.nas_ip" placeholder="可选，限定设备范围" />
        </el-form-item>
      </el-form>
      <el-alert
        v-if="testResult"
        :type="testResult.ok ? 'success' : 'error'"
        :closable="false"
        show-icon
        :title="testResult.ok ? `认证通过（权限 ${testResult.privilege}）` : `认证失败：${testResult.reason}`"
        style="margin-top: 10px"
      />
      <template #footer>
        <el-button @click="testDialog = false">关闭</el-button>
        <el-button type="primary" :loading="saving" @click="doTestAaa">测试</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Connection, Plus, Refresh } from '@element-plus/icons-vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { accessApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const tab = ref('accounts')
const accounts = ref([])
const sessions = ref([])
const loading = ref(false)
const saving = ref(false)

const deviceFilter = ref(null)
const sessionFilter = ref(null)

const aaaStatus = ref({})
const aaaForm = reactive({ enabled: false, tacacs_secret: '', tacacs_port: 49, radius_port: 1812 })

const accountDialog = ref(false)
const accountForm = reactive({})

const testDialog = ref(false)
const testForm = reactive({ username: '', password: '', nas_ip: '' })
const testResult = ref(null)

function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function loadAccounts() {
  loading.value = true
  try {
    const res = await accessApi.accounts({ device_id: deviceFilter.value || undefined })
    accounts.value = res.items || []
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadSessions() {
  try {
    const res = await accessApi.aaaSessions({
      limit: 200,
      result: sessionFilter.value || undefined,
    })
    sessions.value = res.items || []
  } catch (e) {
    sessions.value = []
  }
}

async function loadAaaStatus() {
  try {
    const res = await accessApi.aaaStatus()
    aaaStatus.value = res || {}
    aaaForm.enabled = !!res.enabled
    aaaForm.tacacs_secret = res.secret_masked || ''
    aaaForm.tacacs_port = res.tacacs_port || 49
    aaaForm.radius_port = res.radius_port || 1812
  } catch (e) {
    aaaStatus.value = {}
  }
}

async function reload() {
  await Promise.all([loadAccounts(), loadSessions(), loadAaaStatus()])
}

function onTabChange(name) {
  if (name === 'aaa') {
    loadAaaStatus()
    loadSessions()
  }
}

function openAccount(row) {
  Object.keys(accountForm).forEach((k) => delete accountForm[k])
  Object.assign(
    accountForm,
    {
      id: null, device_id: deviceFilter.value || null, username: '', password: '',
      enable_password: '', privilege: 15, auth_type: 'local', remark: '', enabled: true,
    },
    row ? { ...row, password: '', enable_password: '' } : {},
  )
  accountDialog.value = true
}

async function saveAccount() {
  if (!accountForm.device_id) {
    ElMessage.warning('请选择设备')
    return
  }
  if (!accountForm.username) {
    ElMessage.warning('请填写用户名')
    return
  }
  if (!accountForm.id && !accountForm.password) {
    ElMessage.warning('请填写密码')
    return
  }
  saving.value = true
  try {
    if (accountForm.id) await accessApi.updateAccount(accountForm.id, { ...accountForm })
    else await accessApi.createAccount({ ...accountForm })
    ElMessage.success('保存成功')
    accountDialog.value = false
    loadAccounts()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function removeAccount(row) {
  try {
    await ElMessageBox.confirm(`确定删除账号「${row.username}」吗？`, '提示', { type: 'warning' })
    await accessApi.deleteAccount(row.id)
    ElMessage.success('已删除')
    loadAccounts()
  } catch (e) {
    /* 取消 */
  }
}

async function saveAaa() {
  saving.value = true
  try {
    await accessApi.updateAaaConfig({ ...aaaForm })
    ElMessage.success('已保存，认证服务已重启')
    await loadAaaStatus()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

function testAaa() {
  testResult.value = null
  testDialog.value = true
}

async function doTestAaa() {
  saving.value = true
  try {
    testResult.value = await accessApi.aaaTest({ ...testForm })
  } catch (e) {
    testResult.value = { ok: false, reason: '请求失败' }
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>

<style scoped>
.cfg-sample {
  font-size: 12px;
  line-height: 1.8;
  background: #f8fafc;
  border-radius: 6px;
  padding: 8px 10px;
  margin-top: 4px;
}

html.dark .cfg-sample {
  background: #17181a;
}
</style>
