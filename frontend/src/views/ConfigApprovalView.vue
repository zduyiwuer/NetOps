<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">配置审批 <span class="sub">设备配置变更申请 · 审批 · 下发</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
        <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openCreate()">
          发起变更申请
        </el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px">
      <template #title>审批流程</template>
      提交申请 → <b>管理员审批</b> → 下发到设备（下发前自动备份当前配置，便于回滚）。
      每一步都会记录到操作日志，可在「审计日志」页追溯。
    </el-alert>

    <div class="stat-grid" style="margin-bottom: 12px">
      <div class="stat-card">
        <div class="label"><el-icon><Clock /></el-icon>待审批</div>
        <div class="value text-warn">{{ stat.pending ?? 0 }}</div>
        <div class="foot">等待管理员处理</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><CircleCheck /></el-icon>已下发</div>
        <div class="value text-ok">{{ stat.applied ?? 0 }}</div>
        <div class="foot">配置已成功推送</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><CloseBold /></el-icon>已驳回</div>
        <div class="value">{{ stat.rejected ?? 0 }}</div>
        <div class="foot">审批未通过</div>
      </div>
      <div class="stat-card">
        <div class="label"><el-icon><Warning /></el-icon>下发失败</div>
        <div class="value text-danger">{{ stat.failed ?? 0 }}</div>
        <div class="foot">需重新处理</div>
      </div>
    </div>

    <div class="table-card">
      <div class="filter-bar">
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 140px" @change="load">
          <el-option label="待审批" value="pending" />
          <el-option label="已批准待下发" value="approved" />
          <el-option label="已下发" value="applied" />
          <el-option label="已驳回" value="rejected" />
          <el-option label="下发失败" value="failed" />
        </el-select>
        <DeviceSelect v-model="query.device_id" placeholder="全部设备" style="width: 190px" @change="load" />
        <el-button :icon="Search" @click="load">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="changes" size="small" stripe>
        <el-table-column prop="ticket_no" label="工单号" width="145" class-name="mono" />
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="device_name" label="设备" width="140" show-overflow-tooltip />
        <el-table-column prop="device_ip" label="IP" width="120" class-name="mono" />
        <el-table-column label="类型" width="95" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">
              {{ { config: '配置变更', rollback: '配置回滚', reboot: '重启', upgrade: '升级' }[row.change_type] || row.change_type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="115" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="plain">{{ row.status_text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="applicant" label="申请人" width="100" show-overflow-tooltip />
        <el-table-column prop="reviewer" label="审批人" width="100" show-overflow-tooltip />
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="showDetail(row)">详情</el-button>
            <el-button
              v-if="row.status === 'pending' && userStore.canApprove && row.applicant !== userStore.user?.username"
              link
              type="success"
              size="small"
              @click="openReview(row, true)"
            >
              批准
            </el-button>
            <el-button
              v-if="row.status === 'pending' && userStore.canApprove && row.applicant !== userStore.user?.username"
              link
              type="danger"
              size="small"
              @click="openReview(row, false)"
            >
              驳回
            </el-button>
            <el-button
              v-if="row.status === 'approved' && userStore.canOperate"
              link
              type="primary"
              size="small"
              :loading="applyingId === row.id"
              @click="apply(row)"
            >
              下发
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 新建申请 -->
    <el-dialog v-model="createDialog" title="发起配置变更申请" width="780px" top="6vh">
      <el-form :model="createForm" label-width="110px" size="small">
        <el-form-item label="目标设备">
          <DeviceSelect v-model="createForm.device_id" placeholder="选择设备" width="100%" />
        </el-form-item>
        <el-form-item label="变更标题">
          <el-input v-model="createForm.title" placeholder="如：调整核心交换机上行链路带宽" />
        </el-form-item>
        <el-form-item label="变更类型">
          <el-radio-group v-model="createForm.change_type">
            <el-radio-button value="config">配置变更</el-radio-button>
            <el-radio-button value="rollback">配置回滚</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="createForm.change_type === 'rollback'" label="回滚目标">
          <el-select v-model="createForm.target_backup_id" placeholder="选择要回滚到的备份" style="width: 100%">
            <el-option
              v-for="b in backups"
              :key="b.id"
              :label="`${b.filename} (${b.size_kb} KB)`"
              :value="b.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="变更说明">
          <el-input v-model="createForm.description" type="textarea" :rows="2" placeholder="说明变更原因与影响范围" />
        </el-form-item>
        <el-form-item v-if="createForm.change_type === 'config'" label="配置内容">
          <el-input
            v-model="createForm.config_content"
            type="textarea"
            :rows="12"
            class="mono"
            placeholder="粘贴要下发的配置命令，每行一条"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">提交申请</el-button>
      </template>
    </el-dialog>

    <!-- 审批 -->
    <el-dialog v-model="reviewDialog" :title="approve ? '批准变更申请' : '驳回变更申请'" width="560px">
      <el-descriptions :column="1" size="small" border v-if="current">
        <el-descriptions-item label="工单号">{{ current.ticket_no }}</el-descriptions-item>
        <el-descriptions-item label="设备">{{ current.device_name }} ({{ current.device_ip }})</el-descriptions-item>
        <el-descriptions-item label="标题">{{ current.title }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ current.applicant }}</el-descriptions-item>
      </el-descriptions>
      <el-form label-width="90px" size="small" style="margin-top: 14px">
        <el-form-item label="审批意见">
          <el-input
            v-model="reviewComment"
            type="textarea"
            :rows="3"
            :placeholder="approve ? '可选' : '必填：说明驳回原因'"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewDialog = false">取消</el-button>
        <el-button :type="approve ? 'success' : 'danger'" :loading="saving" @click="submitReview">
          确认{{ approve ? '批准' : '驳回' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 详情 -->
    <el-dialog v-model="detailDialog" title="变更申请详情" width="820px" top="6vh">
      <el-descriptions :column="2" size="small" border v-if="current">
        <el-descriptions-item label="工单号">{{ current.ticket_no }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="statusType(current.status)" size="small" effect="plain">{{ current.status_text }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="设备">{{ current.device_name }}</el-descriptions-item>
        <el-descriptions-item label="IP">{{ current.device_ip }}</el-descriptions-item>
        <el-descriptions-item label="申请人">{{ current.applicant }}</el-descriptions-item>
        <el-descriptions-item label="审批人">{{ current.reviewer || '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ fmtTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审批时间">
          {{ current.reviewed_at ? fmtTime(current.reviewed_at) : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="变更说明" :span="2">{{ current.description || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.review_comment" label="审批意见" :span="2">
          {{ current.review_comment }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.applied_result" label="下发结果" :span="2">
          {{ current.applied_result }}
        </el-descriptions-item>
      </el-descriptions>
      <div v-if="current?.config_content" style="margin-top: 14px">
        <div class="detail-label">待下发配置内容</div>
        <pre class="cfg-box mono">{{ current.config_content }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Clock, CloseBold, Plus, Refresh, Search, Warning } from '@element-plus/icons-vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { accessApi, backupApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const changes = ref([])
const backups = ref([])
const loading = ref(false)
const saving = ref(false)
const applyingId = ref(null)

const query = reactive({ status: null, device_id: null })
const createDialog = ref(false)
const createForm = reactive({})
const reviewDialog = ref(false)
const approve = ref(true)
const reviewComment = ref('')
const current = ref(null)
const detailDialog = ref(false)

const stat = computed(() => {
  const s = { pending: 0, applied: 0, rejected: 0, failed: 0, approved: 0 }
  changes.value.forEach((c) => {
    if (s[c.status] !== undefined) s[c.status] += 1
  })
  return s
})

function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}
function statusType(s) {
  return {
    pending: 'warning', approved: 'primary', applied: 'success',
    rejected: 'danger', failed: 'danger', rolled_back: 'info',
  }[s] || 'info'
}

async function load() {
  loading.value = true
  try {
    const res = await accessApi.changes({
      status: query.status || undefined,
      device_id: query.device_id || undefined,
    })
    changes.value = res.items || []
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadBackups(deviceId) {
  try {
    const res = await backupApi.list({ device_id: deviceId || undefined, status: 'success', page_size: 100 })
    backups.value = res.items || []
  } catch (e) {
    backups.value = []
  }
}

async function reload() {
  await load()
}

function openCreate() {
  Object.keys(createForm).forEach((k) => delete createForm[k])
  Object.assign(createForm, {
    device_id: query.device_id || null,
    title: '',
    change_type: 'config',
    description: '',
    config_content: '',
    target_backup_id: null,
  })
  loadBackups(createForm.device_id)
  createDialog.value = true
}

async function submitCreate() {
  if (!createForm.device_id) {
    ElMessage.warning('请选择目标设备')
    return
  }
  if (!createForm.title) {
    ElMessage.warning('请填写变更标题')
    return
  }
  if (createForm.change_type === 'config' && !createForm.config_content.trim()) {
    ElMessage.warning('请填写配置内容')
    return
  }
  saving.value = true
  try {
    const res = await accessApi.createChange({ ...createForm })
    ElMessage.success(`申请已提交（${res.ticket_no}），等待审批`)
    createDialog.value = false
    load()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

function openReview(row, ok) {
  current.value = row
  approve.value = ok
  reviewComment.value = ''
  reviewDialog.value = true
}

async function submitReview() {
  if (!approve.value && !reviewComment.value.trim()) {
    ElMessage.warning('驳回时必须填写审批意见')
    return
  }
  saving.value = true
  try {
    await accessApi.reviewChange(current.value.id, {
      approve: approve.value,
      comment: reviewComment.value,
    })
    ElMessage.success(approve.value ? '已批准' : '已驳回')
    reviewDialog.value = false
    load()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function apply(row) {
  try {
    await ElMessageBox.confirm(
      `确定将变更下发到「${row.device_name}」吗？下发前会自动备份当前配置。`,
      '确认下发',
      { type: 'warning' },
    )
  } catch (e) {
    return
  }
  applyingId.value = row.id
  try {
    const res = await accessApi.applyChange(row.id)
    if (res.ok) ElMessage.success(res.message || '配置已下发')
    else ElMessage.warning(res.message || '下发失败')
    load()
  } catch (e) {
    /* 忽略 */
  } finally {
    applyingId.value = null
  }
}

function showDetail(row) {
  current.value = row
  detailDialog.value = true
}

onMounted(async () => {
  await reload()
  window.addEventListener('netops:refresh', reload)
})
onUnmounted(() => window.removeEventListener('netops:refresh', reload))
</script>

<style scoped>
.detail-label {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.cfg-box {
  background: #f8fafc;
  border: 1px solid var(--netops-border);
  border-radius: 8px;
  padding: 12px;
  max-height: 340px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.7;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

html.dark .cfg-box {
  background: #17181a;
}
</style>
