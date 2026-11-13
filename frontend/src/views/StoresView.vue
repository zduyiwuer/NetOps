<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">门店管理 <span class="sub">按门店 / 网点组织设备</span></h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openDialog()">
          新增门店
        </el-button>
      </div>
    </div>

    <div class="gap-row">
      <div class="table-card" style="flex: 2; min-width: 520px">
        <el-table border v-loading="loading" :data="stores" size="small" stripe>
          <el-table-column prop="code" label="门店编码" width="110" class-name="mono" />
          <el-table-column prop="name" label="门店名称" min-width="140" />
          <el-table-column prop="region" label="区域" width="90" />
          <el-table-column prop="address" label="地址" min-width="200" show-overflow-tooltip />
          <el-table-column prop="contact" label="联系人" width="90" />
          <el-table-column prop="phone" label="联系电话" width="130" class-name="mono" />
          <el-table-column label="设备" width="110" align="center">
            <template #default="{ row }">
              <el-tag size="small" effect="plain">{{ row.device_count }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="在线" width="80" align="center">
            <template #default="{ row }">
              <span class="text-ok">{{ row.online_count }}</span>
              <span v-if="row.offline_count" class="text-danger">/{{ row.offline_count }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="viewDevices(row)">查看设备</el-button>
              <el-button v-if="userStore.canOperate" link type="primary" size="small" @click="openDialog(row)">
                编辑
              </el-button>
              <el-button v-if="userStore.isAdmin" link type="danger" size="small" @click="remove(row)">
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="chart-card" style="flex: 1; min-width: 320px">
        <div class="card-title">各门店设备数量</div>
        <BaseChart :option="chartOption" height="380px" />
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="form.id ? '编辑门店' : '新增门店'" width="520px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px" size="small">
        <el-form-item label="门店编码" prop="code">
          <el-input v-model="form.code" placeholder="如：SH001" />
        </el-form-item>
        <el-form-item label="门店名称" prop="name">
          <el-input v-model="form.name" placeholder="如：上海总部" />
        </el-form-item>
        <el-form-item label="所属区域">
          <el-input v-model="form.region" placeholder="如：华东" />
        </el-form-item>
        <el-form-item label="地址">
          <el-input v-model="form.address" />
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="form.contact" />
        </el-form-item>
        <el-form-item label="联系电话">
          <el-input v-model="form.phone" />
        </el-form-item>
        <el-form-item label="负责人">
          <el-input v-model="form.manager" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remark" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="devicesDialog" :title="`${currentStore?.name || ''} 设备列表`" width="900px">
      <el-table border :data="storeDevices" size="small" stripe max-height="460">
        <el-table-column prop="name" label="设备名称" min-width="160" show-overflow-tooltip />
        <el-table-column prop="ip" label="管理IP" width="130" class-name="mono" />
        <el-table-column prop="vendor_label" label="厂商" width="130" />
        <el-table-column prop="model" label="型号" width="150" show-overflow-tooltip />
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.status === 'online' ? 'success' : 'danger'" size="small" effect="plain">
              {{ row.status === 'online' ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="location" label="位置" min-width="160" show-overflow-tooltip />
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import { deviceApi } from '../api'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const stores = ref([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const devicesDialog = ref(false)
const storeDevices = ref([])
const currentStore = ref(null)
const formRef = ref()
const form = reactive({})

const rules = {
  code: [{ required: true, message: '请输入门店编码', trigger: 'blur' }],
  name: [{ required: true, message: '请输入门店名称', trigger: 'blur' }],
}

async function load() {
  loading.value = true
  try {
    const res = await deviceApi.stores()
    stores.value = res.items || []
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

function openDialog(row) {
  Object.keys(form).forEach((k) => delete form[k])
  Object.assign(
    form,
    { id: null, code: '', name: '', region: '', address: '', contact: '', phone: '', manager: '', remark: '' },
    row || {},
  )
  dialogVisible.value = true
}

async function save() {
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return
  saving.value = true
  try {
    const payload = {
      code: form.code, name: form.name, region: form.region, address: form.address,
      contact: form.contact, phone: form.phone, manager: form.manager, remark: form.remark,
    }
    if (form.id) await deviceApi.updateStore(form.id, payload)
    else await deviceApi.createStore(payload)
    ElMessage.success('保存成功')
    dialogVisible.value = false
    load()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除门店「${row.name}」吗？该门店下的 ${row.device_count} 台设备将变为未分配。`,
      '提示',
      { type: 'warning' },
    )
    await deviceApi.deleteStore(row.id)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    /* 取消 */
  }
}

async function viewDevices(row) {
  currentStore.value = row
  const res = await deviceApi.storeDevices(row.id)
  storeDevices.value = res.items || []
  devicesDialog.value = true
}

const AXIS = { color: '#6b7280', fontSize: 11 }

const chartOption = computed(() => {
  const data = [...stores.value].sort((a, b) => a.device_count - b.device_count)
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 90, right: 30, top: 16, bottom: 24 },
    xAxis: { type: 'value', axisLabel: AXIS, splitLine: { lineStyle: { color: '#eef1f5' } } },
    yAxis: {
      type: 'category',
      data: data.map((s) => s.name),
      axisLabel: { ...AXIS, width: 80, overflow: 'truncate' },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
    },
    series: [
      {
        type: 'bar',
        barWidth: 16,
        itemStyle: { color: '#3b82f6', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', fontSize: 11, color: '#6b7280' },
        data: data.map((s) => s.device_count),
      },
    ],
  }
})

onMounted(async () => {
  await load()
  window.addEventListener('netops:refresh', load)
})
onUnmounted(() => window.removeEventListener('netops:refresh', load))
</script>
