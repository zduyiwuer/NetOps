<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">配置备份 <span class="sub">设备配置快照与版本对比</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
      </div>
    </div>

    <!-- 门店备份概览 -->
    <div class="table-card" style="margin-bottom: 12px">
      <div class="card-title">
        <span>门店备份概览</span>
        <span class="text-muted" style="font-size: 12px">点击「下载」打包该门店所有设备的最新配置</span>
      </div>
      <el-table border :data="storeRows" size="small" stripe>
        <el-table-column prop="store_code" label="编码" width="100" class-name="mono" />
        <el-table-column prop="store_name" label="门店" min-width="130" />
        <el-table-column prop="region" label="区域" width="90" />
        <el-table-column label="设备数" width="80" align="right">
          <template #default="{ row }">{{ row.device_count }}</template>
        </el-table-column>
        <el-table-column label="已备份" width="90" align="right">
          <template #default="{ row }">
            <span :class="row.backed_count >= row.device_count ? 'text-ok' : 'text-warn'">
              {{ row.backed_count }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="可下载" width="90" align="right">
          <template #default="{ row }">{{ row.downloadable_count }}</template>
        </el-table-column>
        <el-table-column label="覆盖率" width="160">
          <template #default="{ row }">
            <el-progress
              :percentage="row.device_count ? Math.round((row.backed_count / row.device_count) * 100) : 0"
              :status="row.device_count && row.backed_count >= row.device_count ? 'success' : undefined"
              :stroke-width="10"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              :disabled="!row.downloadable_count"
              :loading="downloading === row.store_id"
              @click="downloadStore(row)"
            >
              打包下载
            </el-button>
            <el-button link type="primary" size="small" @click="filterByStore(row)">
              查看备份
            </el-button>
            <el-button
              link
              type="success"
              size="small"
              :loading="backingUp === row.store_id"
              @click="backupStore(row)"
            >
              一键备份
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="gap-row">
      <div class="table-card" style="flex: 2; min-width: 560px">
        <div class="filter-bar">
          <el-select
            v-model="query.store_id"
            placeholder="全部门店"
            clearable
            style="width: 150px"
            @change="onStoreChange"
          >
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
          <DeviceSelect
            v-model="query.device_id"
            :store-id="query.store_id"
            placeholder="全部设备"
            style="width: 190px"
            @change="load"
          />
          <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 120px" @change="load">
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
          </el-select>
          <el-button :icon="Refresh" @click="load">查询</el-button>
        </div>

        <el-table border v-loading="loading" :data="backups" size="small" stripe max-height="440">
          <el-table-column prop="device_name" label="设备" width="140" show-overflow-tooltip />
          <el-table-column prop="store_name" label="门店" width="110" show-overflow-tooltip />
          <el-table-column prop="device_ip" label="IP" width="120" class-name="mono" />
          <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip class-name="mono" />
          <el-table-column label="大小" width="85" align="right">
            <template #default="{ row }">{{ row.size_kb }} KB</template>
          </el-table-column>
          <el-table-column label="触发" width="80" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.trigger_type === 'manual' ? 'primary' : 'info'">
                {{ row.trigger_type === 'manual' ? '手动' : '自动' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="75" align="center">
            <template #default="{ row }">
              <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small" effect="plain">
                {{ row.status === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="viewContent(row)">查看</el-button>
              <el-button link type="primary" size="small" @click="download(row)">下载</el-button>
              <el-button link type="primary" size="small" @click="showDiff(row)">对比</el-button>
              <el-button v-if="userStore.canOperate" link type="danger" size="small" @click="remove(row)">删除</el-button>
            </template>
          </el-table-column>
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

      <div class="table-card" style="flex: 1; min-width: 320px">
        <div class="card-title">快速备份</div>
        <div class="quick">
          <DeviceSelect
            v-model="quickDeviceId"
            :store-id="query.store_id"
            placeholder="选择要备份的设备"
            width="100%"
          />
          <el-button
            type="primary"
            :icon="FolderAdd"
            :loading="quickBusy"
            :disabled="!quickDeviceId"
            @click="doBackup"
            style="width: 100%"
          >
            立即备份该设备
          </el-button>
          <p class="text-muted tip">
            备份通过 SSH 抓取 running-config 并归档。平台每 24 小时自动备份一次，
            备份历史保留全部版本，可随时查看、下载或与最新版本对比。
          </p>
        </div>
      </div>
    </div>

    <!-- 备份内容查看 -->
    <el-dialog v-model="viewDialog" title="备份内容查看" width="900px" top="6vh">
      <div v-if="viewMeta" class="view-meta">
        <el-tag size="small" effect="plain" class="mono">{{ viewMeta.filename }}</el-tag>
        <el-tag size="small" effect="plain">{{ viewMeta.size_bytes }} bytes</el-tag>
        <el-tag size="small" effect="plain">{{ viewMeta.line_count }} 行</el-tag>
        <el-tag size="small" effect="plain">MD5 {{ (viewMeta.md5 || '').slice(0, 10) }}</el-tag>
        <el-tag size="small" effect="plain">{{ fmtTime(viewMeta.created_at) }}</el-tag>
      </div>
      <div class="view-toolbar">
        <el-input
          v-model="viewKeyword"
          placeholder="在配置中搜索"
          clearable
          size="small"
          style="width: 220px"
          :prefix-icon="Search"
        />
        <el-checkbox v-model="viewWrap" size="small">自动换行</el-checkbox>
        <span class="text-muted" style="font-size: 12px">{{ viewLines.length }} 行</span>
      </div>
      <pre class="config-view mono" :class="{ nowrap: !viewWrap }">{{ highlightedContent }}</pre>
      <el-alert v-if="viewMeta?.truncated" type="warning" :closable="false" show-icon
        title="配置内容过长，仅显示前 400000 字符" style="margin-top: 10px" />
    </el-dialog>

    <!-- 差异对比 -->
    <el-dialog v-model="diffDialog" title="配置版本对比" width="900px" top="6vh">
      <div v-if="diffInfo" class="diff-head">
        <el-tag size="small" effect="plain" class="mono">{{ diffInfo.old }}</el-tag>
        <el-icon class="text-muted"><Right /></el-icon>
        <el-tag size="small" effect="plain" class="mono">{{ diffInfo.new || '（无对比版本）' }}</el-tag>
        <el-tag v-if="diffInfo.same" type="success" size="small">内容一致</el-tag>
      </div>
      <pre class="diff-box mono">{{ diffText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderAdd, Refresh, Right, Search } from '@element-plus/icons-vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { backupApi, deviceApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const backups = ref([])
const devices = ref([])
const stores = ref([])
const storeRows = ref([])
const total = ref(0)
const loading = ref(false)
const quickBusy = ref(false)
const backingUp = ref(null)
const downloading = ref(null)
const quickDeviceId = ref(null)

const query = reactive({ store_id: null, device_id: null, status: null, page: 1, page_size: 50 })

// 内容查看
const viewDialog = ref(false)
const viewMeta = ref(null)
const viewLines = ref([])
const viewKeyword = ref('')
const viewWrap = ref(true)

const diffDialog = ref(false)
const diffText = ref('')
const diffInfo = ref(null)

function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function load() {
  loading.value = true
  try {
    const res = await backupApi.list({ ...query })
    backups.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

async function loadStores() {
  try {
    const res = await deviceApi.stores()
    stores.value = res.items || []
  } catch (e) {
    stores.value = []
  }
}

async function loadStoreSummary() {
  try {
    const res = await backupApi.stores()
    storeRows.value = res.items || []
  } catch (e) {
    storeRows.value = []
  }
}

async function loadDevices() {
  try {
    const res = await deviceApi.list({ page_size: 500 })
    devices.value = res.items || []
  } catch (e) {
    devices.value = []
  }
}

async function reload() {
  await Promise.all([load(), loadStores(), loadStoreSummary(), loadDevices()])
}

function filterByStore(row) {
  query.store_id = row.store_id
  query.device_id = null
  query.page = 1
  load()
}

function onStoreChange() {
  // 门店切换后清空设备筛选，避免残留其他门店的设备
  query.device_id = null
  query.page = 1
  load()
}

async function doBackup() {
  quickBusy.value = true
  try {
    const res = await backupApi.create(quickDeviceId.value)
    if (res.ok) {
      ElMessage.success(`备份成功：${res.backup?.filename || ''}`)
    } else {
      ElMessage.warning(res.message || '备份失败')
    }
    reload()
  } catch (e) {
    /* 忽略 */
  } finally {
    quickBusy.value = false
  }
}

async function backupStore(row) {
  const ids = devices.value.filter((d) => d.store_id === row.store_id).map((d) => d.id)
  if (!ids.length) {
    ElMessage.warning('该门店下没有设备')
    return
  }
  backingUp.value = row.store_id
  try {
    const res = await backupApi.batch(ids)
    ElMessage.success(`备份完成：成功 ${res.success} / ${res.total}`)
    reload()
  } catch (e) {
    /* 忽略 */
  } finally {
    backingUp.value = null
  }
}

async function downloadStore(row) {
  downloading.value = row.store_id
  try {
    const res = await backupApi.downloadStore(row.store_id)
    const blob = new Blob([res.data])
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${row.store_name}_配置备份.zip`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已下载该门店配置打包')
  } catch (e) {
    /* 忽略 */
  } finally {
    downloading.value = null
  }
}

async function viewContent(row) {
  try {
    const res = await backupApi.view(row.id)
    viewMeta.value = res
    viewLines.value = (res.content || '').split('\n')
    viewKeyword.value = ''
    viewDialog.value = true
  } catch (e) {
    /* 忽略 */
  }
}

const highlightedContent = computed(() => {
  const kw = viewKeyword.value.trim()
  if (!kw) return viewLines.value.join('\n')
  const pattern = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(pattern, 'gi')
  return viewLines.value
    .map((line) => line.replace(re, (m) => `«${m}»`))
    .join('\n')
})

async function download(row) {
  const res = await backupApi.download(row.id)
  const blob = new Blob([res.data])
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = row.filename
  a.click()
  URL.revokeObjectURL(url)
}

async function showDiff(row) {
  const res = await backupApi.diff(row.id)
  diffInfo.value = { old: res.old, new: res.new, same: res.same }
  diffText.value = res.diff || res.message || '（无差异内容）'
  diffDialog.value = true
}

async function remove(row) {
  try {
    await ElMessageBox.confirm('确定删除该备份记录吗？', '提示', { type: 'warning' })
    await backupApi.remove(row.id)
    ElMessage.success('已删除')
    reload()
  } catch (e) {
    /* 取消 */
  }
}

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

.quick {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tip {
  font-size: 12px;
  line-height: 1.7;
  margin: 0;
}

.view-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.view-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.config-view {
  background: #f8fafc;
  border: 1px solid var(--netops-border);
  border-radius: 8px;
  padding: 14px;
  max-height: 500px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.65;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.config-view.nowrap {
  white-space: pre;
  word-break: normal;
}

html.dark .config-view {
  background: #17181a;
}

.diff-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.diff-box {
  background: #f8fafc;
  border: 1px solid var(--netops-border);
  border-radius: 8px;
  padding: 14px;
  max-height: 560px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
