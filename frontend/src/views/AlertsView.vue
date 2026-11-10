<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">告警中心</h2>
      <div class="toolbar">
        <el-button :icon="Refresh" @click="reload">刷新</el-button>
        <el-button v-if="userStore.canOperate" :icon="Setting" @click="ruleDialog = true">告警规则</el-button>
        <el-button v-if="userStore.isAdmin" :icon="Bell" @click="channelDialog = true">通知渠道</el-button>
      </div>
    </div>

    <el-tabs v-model="tab" @tab-change="onTabChange">
      <el-tab-pane label="告警事件" name="events">
        <div class="stat-grid" style="margin-bottom: 12px">
          <div class="stat-card">
            <div class="label">活动告警</div>
            <div class="value">{{ stats.firing ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">严重告警</div>
            <div class="value text-danger">{{ stats.critical ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">已确认</div>
            <div class="value text-warn">{{ stats.acked ?? 0 }}</div>
          </div>
          <div class="stat-card">
            <div class="label">已恢复</div>
            <div class="value text-ok">{{ stats.resolved ?? 0 }}</div>
          </div>
        </div>

        <div class="table-card">
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
            <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 130px" @change="load">
              <el-option label="未恢复" value="firing" />
              <el-option label="已确认" value="acked" />
              <el-option label="已恢复" value="resolved" />
            </el-select>
            <el-select v-model="query.severity" placeholder="全部级别" clearable style="width: 120px" @change="load">
              <el-option label="严重" value="critical" />
              <el-option label="警告" value="warning" />
              <el-option label="提示" value="info" />
            </el-select>
            <DeviceSelect
              v-model="query.device_id"
              :store-id="query.store_id"
              placeholder="全部设备"
              style="width: 190px"
              @change="load"
            />
            <el-input
              v-model="query.keyword"
              placeholder="搜索告警内容"
              clearable
              style="width: 200px"
              :prefix-icon="Search"
              @keyup.enter="load"
              @clear="load"
            />
            <el-button :icon="Search" @click="load">查询</el-button>
            <el-button
              v-if="userStore.canOperate && selection.length"
              type="warning"
              plain
              :icon="Select"
              @click="batchAck"
            >
              批量确认 ({{ selection.length }})
            </el-button>
          </div>

          <el-table
            v-loading="loading"
            :data="events"
            size="small"
            stripe
            row-key="id"
            @selection-change="(v) => (selection = v)"
          >
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="alert-detail">
                  <div class="detail-summary">
                    <el-tag size="small" effect="plain" type="info">
                      {{ row.metric_name || row.metric }}
                    </el-tag>
                    <span v-if="row.detail?.summary" class="text-muted">
                      {{ row.detail.summary }}
                    </span>
                    <span v-if="row.value !== null && row.value !== undefined" class="text-muted">
                      当前值 <b class="mono">{{ row.value }}</b>
                      / 阈值 <b class="mono">{{ row.threshold }}</b>
                    </span>
                  </div>
                  <div v-if="row.message" class="detail-message">{{ row.message }}</div>
                  <el-table
                    v-if="row.detail?.items?.length"
                    :data="row.detail.items"
                    size="small"
                    border
                    style="margin-top: 10px"
                  >
                    <el-table-column prop="interface" label="接口" min-width="190" show-overflow-tooltip />
                    <el-table-column label="状态" width="75" align="center">
                      <template #default="{ row: r }">
                        <el-tag size="small" effect="plain" :type="r.status === 'UP' ? 'success' : 'danger'">
                          {{ r.status }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column label="指标值" width="110" align="right">
                      <template #default="{ row: r }">
                        <b class="mono">{{ r.value }}</b>
                      </template>
                    </el-table-column>
                    <el-table-column label="速率" width="100" align="right">
                      <template #default="{ row: r }">
                        <span class="mono">{{ r.speed_mbps ?? '-' }} Mbps</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="入/出向" width="150" align="right">
                      <template #default="{ row: r }">
                        <span v-if="r.in_mbps !== undefined" class="mono">
                          {{ r.in_mbps }} / {{ r.out_mbps }} Mbps
                        </span>
                        <span v-else-if="r.in_errors !== undefined" class="mono">
                          {{ r.in_errors }} / {{ r.out_errors }} 个
                        </span>
                        <span v-else>-</span>
                      </template>
                    </el-table-column>
                  </el-table>
                  <el-empty
                    v-else
                    description="该告警无接口级明细"
                    :image-size="52"
                    style="margin-top: 8px"
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column type="selection" width="42" />
            <el-table-column label="级别" width="76" align="center">
              <template #default="{ row }">
                <el-tag :type="severityType(row.severity)" size="small" effect="dark">
                  {{ severityText(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="statusTagType(row.status)" size="small" effect="plain">
                  {{ statusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="title" label="告警内容" min-width="260" show-overflow-tooltip />
            <el-table-column prop="device_name" label="设备" width="130" show-overflow-tooltip />
            <el-table-column prop="store_name" label="门店" width="110" show-overflow-tooltip />
            <el-table-column label="触发值" width="120" align="right">
              <template #default="{ row }">
                <span class="mono">{{ row.value ?? '-' }}</span>
                <span class="text-muted" style="font-size: 11px"> / {{ row.threshold ?? '-' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="触发时间" width="150">
              <template #default="{ row }">{{ fmtTime(row.fired_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.status !== 'resolved' && row.status !== 'acked'"
                  link
                  type="primary"
                  size="small"
                  @click="ack(row)"
                >
                  确认
                </el-button>
                <el-button
                  v-if="row.status !== 'resolved'"
                  link
                  type="success"
                  size="small"
                  @click="resolve(row)"
                >
                  恢复
                </el-button>
                <el-tooltip v-if="row.ack_by" :content="`确认人：${row.ack_by}`">
                  <span class="text-muted" style="font-size: 12px">{{ row.ack_by }}</span>
                </el-tooltip>
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
      </el-tab-pane>

      <el-tab-pane label="告警统计" name="stats">
        <div class="gap-row">
          <div class="chart-card" style="flex: 2; min-width: 420px">
            <div class="card-title">近 {{ statsDays }} 天告警趋势</div>
            <BaseChart :option="trendOption" height="300px" />
          </div>
          <div class="chart-card" style="flex: 1; min-width: 320px">
            <div class="card-title">告警级别分布</div>
            <BaseChart :option="severityOption" height="300px" />
          </div>
        </div>
        <div class="gap-row" style="margin-top: 12px">
          <div class="table-card" style="flex: 1; min-width: 320px">
            <div class="card-title">触发指标分布</div>
            <el-table :data="metricRows" size="small">
              <el-table-column prop="metric" label="指标" />
              <el-table-column prop="count" label="次数" width="90" align="right" />
            </el-table>
          </div>
          <div class="table-card" style="flex: 1; min-width: 320px">
            <div class="card-title">高发设备 Top 10</div>
            <el-table :data="deviceRows" size="small">
              <el-table-column prop="name" label="设备" show-overflow-tooltip />
              <el-table-column prop="count" label="次数" width="90" align="right" />
            </el-table>
          </div>
          <div class="table-card" style="flex: 1; min-width: 320px">
            <div class="card-title">门店告警分布</div>
            <el-table :data="storeRows" size="small">
              <el-table-column prop="name" label="门店" show-overflow-tooltip />
              <el-table-column prop="count" label="次数" width="90" align="right" />
            </el-table>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="告警规则" name="rules">
        <div class="table-card">
          <div class="filter-bar">
            <el-button v-if="userStore.canOperate" type="primary" :icon="Plus" @click="openRule()">
              新增规则
            </el-button>
            <span class="text-muted">规则在每次采集后自动评估，条件满足即触发告警</span>
          </div>
          <el-table :data="rules" size="small" stripe>
            <el-table-column prop="name" label="规则名称" min-width="160" />
            <el-table-column label="作用范围" width="150">
              <template #default="{ row }">
                <el-tag v-if="row.device_id" size="small" effect="plain">{{ row.device_name }}</el-tag>
                <el-tag v-else-if="row.store_id" size="small" effect="plain" type="warning">{{ row.store_name }}</el-tag>
                <el-tag v-else size="small" effect="plain" type="info">全部设备</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="metric" label="指标" width="110" />
            <el-table-column label="条件" width="150">
              <template #default="{ row }">
                <span class="mono">{{ metricText(row.metric) }} {{ row.operator }} {{ row.threshold }}</span>
              </template>
            </el-table-column>
            <el-table-column label="级别" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="severityType(row.severity)" size="small" effect="plain">
                  {{ severityText(row.severity) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="silence_minutes" label="静默(分)" width="90" align="right" />
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
                  {{ row.enabled ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button v-if="userStore.canOperate" link type="primary" size="small" @click="openRule(row)">
                  编辑
                </el-button>
                <el-button
                  v-if="userStore.isAdmin"
                  link
                  type="danger"
                  size="small"
                  @click="removeRule(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane label="通知渠道" name="channels">
        <div class="table-card">
          <div class="filter-bar">
            <el-button v-if="userStore.isAdmin" type="primary" :icon="Plus" @click="openChannel()">
              新增渠道
            </el-button>
            <span class="text-muted">支持 Webhook、企业微信机器人、钉钉机器人、邮件 SMTP</span>
          </div>
          <el-table :data="channels" size="small" stripe>
            <el-table-column prop="name" label="渠道名称" width="150" />
            <el-table-column label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ channelTypeText(row.type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="target" label="地址" min-width="280" show-overflow-tooltip class-name="mono" />
            <el-table-column label="最低级别" width="100">
              <template #default="{ row }">{{ severityText(row.min_severity) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
                  {{ row.enabled ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="testChannel(row)">测试</el-button>
                <el-button v-if="userStore.isAdmin" link type="primary" size="small" @click="openChannel(row)">
                  编辑
                </el-button>
                <el-button
                  v-if="userStore.isAdmin"
                  link
                  type="danger"
                  size="small"
                  @click="removeChannel(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 规则编辑 -->
    <el-dialog v-model="ruleDialog" :title="ruleForm.id ? '编辑告警规则' : '新增告警规则'" width="620px">
      <el-form :model="ruleForm" label-width="120px" size="small">
        <el-form-item label="规则名称">
          <el-input v-model="ruleForm.name" placeholder="如：核心交换机 CPU 高" />
        </el-form-item>
        <el-form-item label="作用范围">
          <el-radio-group v-model="scopeType" @change="onScopeChange">
            <el-radio-button value="all">全部设备</el-radio-button>
            <el-radio-button value="store">指定门店</el-radio-button>
            <el-radio-button value="device">指定设备</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="scopeType === 'store'" label="门店">
          <el-select v-model="ruleForm.store_id" style="width: 100%">
            <el-option v-for="s in stores" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="scopeType === 'device'" label="设备">
          <DeviceSelect v-model="ruleForm.device_id" style="width: 100%" />
        </el-form-item>
        <el-form-item label="监控指标">
          <el-select v-model="ruleForm.metric" style="width: 100%">
            <el-option v-for="m in metricOptions" :key="m.value" :label="m.label" :value="m.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="触发条件">
          <el-select v-model="ruleForm.operator" style="width: 90px">
            <el-option v-for="op in ['>', '>=', '<', '<=', '==']" :key="op" :label="op" :value="op" />
          </el-select>
          <el-input-number v-model="ruleForm.threshold" :precision="2" style="width: 150px; margin-left: 8px" />
          <span class="text-muted" style="margin-left: 8px; font-size: 12px">{{ unitText(ruleForm.metric) }}</span>
        </el-form-item>
        <el-form-item label="告警级别">
          <el-radio-group v-model="ruleForm.severity">
            <el-radio-button value="info">提示</el-radio-button>
            <el-radio-button value="warning">警告</el-radio-button>
            <el-radio-button value="critical">严重</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="静默时间(分)">
          <el-input-number v-model="ruleForm.silence_minutes" :min="0" :max="1440" />
          <span class="text-muted" style="margin-left: 8px; font-size: 12px">同一规则在此时间内不重复告警</span>
        </el-form-item>
        <el-form-item label="通知渠道">
          <el-select v-model="ruleForm.notify_channels" multiple style="width: 100%" placeholder="不选则使用全部启用渠道">
            <el-option v-for="c in channels" :key="c.name" :label="c.name" :value="c.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="ruleForm.enabled" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="ruleForm.description" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveRule">保存</el-button>
      </template>
    </el-dialog>

    <!-- 渠道编辑 -->
    <el-dialog v-model="channelDialog" :title="channelForm.id ? '编辑通知渠道' : '新增通知渠道'" width="580px">
      <el-form :model="channelForm" label-width="110px" size="small">
        <el-form-item label="渠道名称">
          <el-input v-model="channelForm.name" placeholder="如：运维值班群" />
        </el-form-item>
        <el-form-item label="渠道类型">
          <el-select v-model="channelForm.type" style="width: 100%">
            <el-option label="Webhook（通用）" value="webhook" />
            <el-option label="企业微信机器人" value="wecom" />
            <el-option label="钉钉机器人" value="dingtalk" />
            <el-option label="邮件 SMTP" value="email" />
          </el-select>
        </el-form-item>
        <el-form-item label="地址">
          <el-input
            v-model="channelForm.target"
            type="textarea"
            :rows="2"
            :placeholder="targetPlaceholder"
            class-name="mono"
          />
        </el-form-item>
        <el-form-item v-if="channelForm.type === 'email'" label="SMTP 配置">
          <el-input v-model="channelForm.extra" type="textarea" :rows="3"
            placeholder='{"host":"smtp.example.com","port":465,"from":"a@b.com","to":"c@d.com","password":"xxx"}' />
        </el-form-item>
        <el-form-item label="最低级别">
          <el-select v-model="channelForm.min_severity" style="width: 100%">
            <el-option label="提示" value="info" />
            <el-option label="警告" value="warning" />
            <el-option label="严重" value="critical" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="channelForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="channelDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, Plus, Refresh, Search, Select, Setting } from '@element-plus/icons-vue'
import BaseChart from '../components/BaseChart.vue'
import DeviceSelect from '../components/DeviceSelect.vue'
import { alertApi, deviceApi } from '../api'
import { useChartTheme } from '../utils/chartTheme'
import { useUserStore } from '../store/user'

const userStore = useUserStore()
const tab = ref('events')
const events = ref([])
const rules = ref([])
const channels = ref([])
const stores = ref([])
const total = ref(0)
const loading = ref(false)
const saving = ref(false)
const selection = ref([])
const statsDays = ref(7)
const stats = ref({ firing: 0, acked: 0, resolved: 0 })
const statistics = ref({ by_day: [], by_severity: {}, by_metric: {}, by_device: {}, by_store: {} })

const query = reactive({
  store_id: null, status: 'firing', severity: null, device_id: null, keyword: '', page: 1, page_size: 20,
})

const metricOptions = [
  { value: 'cpu', label: 'CPU 使用率 (%)' },
  { value: 'mem', label: '内存使用率 (%)' },
  { value: 'temperature', label: '设备温度 (℃)' },
  { value: 'traffic', label: '接口流量 (Mbps)' },
  { value: 'util', label: '接口利用率 (%)' },
  { value: 'loss', label: '接口错包数' },
  { value: 'offline', label: '设备离线' },
  { value: 'interface_down', label: 'Down 接口数' },
  { value: 'response_time', label: 'SNMP 响应 (ms)' },
]

function metricText(m) {
  return metricOptions.find((o) => o.value === m)?.label.split(' (')[0] || m
}
function unitText(m) {
  const label = metricOptions.find((o) => o.value === m)?.label || ''
  const p = label.match(/\((.*)\)/)
  return p ? p[1] : ''
}
function severityType(s) {
  return { critical: 'danger', crit: 'danger', warning: 'warning', info: 'info' }[s] || 'info'
}
function severityText(s) {
  return { critical: '严重', crit: '严重', warning: '警告', info: '提示' }[s] || s
}
function statusTagType(s) {
  return { firing: 'danger', acked: 'warning', resolved: 'success' }[s] || 'info'
}
function statusText(s) {
  return { firing: '未恢复', acked: '已确认', resolved: '已恢复' }[s] || s
}
function channelTypeText(t) {
  return { webhook: 'Webhook', wecom: '企业微信', dingtalk: '钉钉', email: '邮件' }[t] || t
}
function fmtTime(t) {
  return t ? t.replace('T', ' ').slice(0, 19) : '-'
}

async function load() {
  loading.value = true
  try {
    const res = await alertApi.events({ ...query })
    events.value = res.items || []
    total.value = res.total || 0
    const s = { firing: 0, acked: 0, resolved: 0, critical: 0 }
    events.value.forEach((e) => {
      s[e.status] = (s[e.status] || 0) + 1
      if (['critical', 'crit'].includes(e.severity) && e.status !== 'resolved') s.critical += 1
    })
    stats.value = s
  } catch (e) {
    /* 忽略 */
  } finally {
    loading.value = false
  }
}

/** 门店切换：清空不属于该门店的设备筛选 */
function onStoreChange() {
  query.device_id = null
  query.page = 1
  load()
}

async function loadRules() {
  try {
    const res = await alertApi.rules()
    rules.value = res.items || []
  } catch (e) {
    rules.value = []
  }
}

async function loadChannels() {
  try {
    const res = await alertApi.channels()
    channels.value = res.items || []
  } catch (e) {
    channels.value = []
  }
}

async function loadStats() {
  try {
    const res = await alertApi.statistics({ days: statsDays.value })
    statistics.value = res
  } catch (e) {
    statistics.value = { by_day: [], by_severity: {}, by_metric: {}, by_device: {}, by_store: {} }
  }
}

function onTabChange(name) {
  if (name === 'rules') loadRules()
  else if (name === 'channels') loadChannels()
  else if (name === 'stats') loadStats()
}

async function reload() {
  await Promise.all([load(), loadRules(), loadChannels(), loadStats()])
}

async function ack(row) {
  await alertApi.ack(row.id)
  ElMessage.success('已确认')
  load()
}
async function resolve(row) {
  await alertApi.resolve(row.id)
  ElMessage.success('已恢复')
  load()
}
async function batchAck() {
  await alertApi.batchAck(selection.value.map((i) => i.id))
  ElMessage.success(`已确认 ${selection.value.length} 条告警`)
  load()
}

// ---------------------------------------------------------------- 规则
const ruleDialog = ref(false)
const scopeType = ref('all')
const ruleForm = reactive({})

function openRule(row) {
  scopeType.value = row?.device_id ? 'device' : row?.store_id ? 'store' : 'all'
  Object.keys(ruleForm).forEach((k) => delete ruleForm[k])
  Object.assign(ruleForm, {
    id: null, name: '', device_id: null, store_id: null, metric: 'cpu', operator: '>',
    threshold: 80, severity: 'warning', enabled: true, silence_minutes: 10,
    notify_channels: [], description: '',
  }, row ? { ...row, notify_channels: row.notify_channels ? row.notify_channels.split(',').filter(Boolean) : [] } : {})
  ruleDialog.value = true
}

function onScopeChange(t) {
  if (t !== 'device') ruleForm.device_id = null
  if (t !== 'store') ruleForm.store_id = null
}

async function saveRule() {
  if (!ruleForm.name) {
    ElMessage.warning('请填写规则名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: ruleForm.name,
      device_id: scopeType.value === 'device' ? ruleForm.device_id : null,
      store_id: scopeType.value === 'store' ? ruleForm.store_id : null,
      metric: ruleForm.metric,
      operator: ruleForm.operator,
      threshold: ruleForm.threshold,
      severity: ruleForm.severity,
      enabled: ruleForm.enabled,
      silence_minutes: ruleForm.silence_minutes,
      notify_channels: Array.isArray(ruleForm.notify_channels)
        ? ruleForm.notify_channels.join(',')
        : ruleForm.notify_channels || '',
      description: ruleForm.description || '',
    }
    if (ruleForm.id) await alertApi.updateRule(ruleForm.id, payload)
    else await alertApi.createRule(payload)
    ElMessage.success('保存成功')
    ruleDialog.value = false
    loadRules()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function removeRule(row) {
  try {
    await ElMessageBox.confirm(`确定删除规则「${row.name}」吗？`, '提示', { type: 'warning' })
    await alertApi.deleteRule(row.id)
    ElMessage.success('已删除')
    loadRules()
  } catch (e) {
    /* 取消 */
  }
}

// ---------------------------------------------------------------- 渠道
const channelDialog = ref(false)
const channelForm = reactive({})

const targetPlaceholder = computed(() => {
  return {
    webhook: 'https://your-webhook-endpoint',
    wecom: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx',
    dingtalk: 'https://oapi.dingtalk.com/robot/send?access_token=xxx',
    email: 'a@example.com,b@example.com',
  }[channelForm.type] || '渠道地址'
})

function openChannel(row) {
  Object.keys(channelForm).forEach((k) => delete channelForm[k])
  Object.assign(
    channelForm,
    { id: null, name: '', type: 'webhook', target: '', extra: '{}', enabled: true, min_severity: 'warning' },
    row || {},
  )
  channelDialog.value = true
}

async function saveChannel() {
  if (!channelForm.name) {
    ElMessage.warning('请填写渠道名称')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: channelForm.name,
      type: channelForm.type,
      target: channelForm.target,
      extra: channelForm.extra || '{}',
      enabled: channelForm.enabled,
      min_severity: channelForm.min_severity,
    }
    if (channelForm.id) await alertApi.updateChannel(channelForm.id, payload)
    else await alertApi.createChannel(payload)
    ElMessage.success('保存成功')
    channelDialog.value = false
    loadChannels()
  } catch (e) {
    /* 忽略 */
  } finally {
    saving.value = false
  }
}

async function removeChannel(row) {
  try {
    await ElMessageBox.confirm(`确定删除渠道「${row.name}」吗？`, '提示', { type: 'warning' })
    await alertApi.deleteChannel(row.id)
    ElMessage.success('已删除')
    loadChannels()
  } catch (e) {
    /* 取消 */
  }
}

async function testChannel(row) {
  const res = await alertApi.testChannel(row.id)
  if (res.ok) ElMessage.success(res.message || '发送成功')
  else ElMessage.warning(res.message || '发送失败')
}

const AXIS = { color: '#6b7280', fontSize: 11 }
const SPLIT = { lineStyle: { color: '#eef1f5' } }

// 主题自适应：坐标轴与图例文字跟随明暗模式
const { axis: AX, split: SPL, tooltip: TIP } = useChartTheme()

const trendOption = computed(() => ({
  color: ['#dc2626'],
  tooltip: { ...TIP.value, trigger: 'axis' },
  grid: { left: 40, right: 20, top: 20, bottom: 28 },
  xAxis: {
    type: 'category',
    data: (statistics.value.by_day || []).map((d) => d.date.slice(5)),
    axisLabel: AX.value,
    axisLine: { lineStyle: { color: SPL.value.lineStyle.color } },
  },
  yAxis: { type: 'value', axisLabel: AX.value, splitLine: SPL.value },
  series: [
    {
      name: '告警数',
      type: 'bar',
      barWidth: 18,
      itemStyle: { color: '#f87171', borderRadius: [3, 3, 0, 0] },
      data: (statistics.value.by_day || []).map((d) => d.count),
    },
  ],
}))

const severityOption = computed(() => {
  const s = statistics.value.by_severity || {}
  const data = [
    { name: '严重', value: s.critical || s.crit || 0, itemStyle: { color: '#dc2626' } },
    { name: '警告', value: s.warning || 0, itemStyle: { color: '#f59e0b' } },
    { name: '提示', value: s.info || 0, itemStyle: { color: '#3b82f6' } },
  ].filter((i) => i.value > 0)
  return {
    tooltip: { ...TIP.value, trigger: 'item' },
    legend: { bottom: 0, textStyle: AX.value },
    series: [
      {
        type: 'pie',
        radius: ['46%', '68%'],
        center: ['50%', '45%'],
        label: { formatter: '{b}: {c}', fontSize: 12, color: '#e5eaf3' },
        data: data.length ? data : [{ name: '暂无数据', value: 1, itemStyle: { color: '#e5e7eb' } }],
      },
    ],
  }
})

const metricRows = computed(() =>
  Object.entries(statistics.value.by_metric || {}).map(([metric, count]) => ({
    metric: metricText(metric),
    count,
  })),
)
const deviceRows = computed(() =>
  Object.entries(statistics.value.by_device || {}).map(([name, count]) => ({ name, count })),
)
const storeRows = computed(() =>
  Object.entries(statistics.value.by_store || {}).map(([name, count]) => ({ name, count })),
)

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
.pager {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}

.alert-detail {
  padding: 6px 14px 12px 50px;
}

.detail-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
  margin-bottom: 8px;
}

.detail-message {
  font-size: 13px;
  line-height: 1.8;
  color: var(--netops-text);
  white-space: pre-wrap;
  background: var(--netops-fill-color-light, #f4f6f8);
  border-radius: 6px;
  padding: 10px 12px;
}

html.dark .detail-message {
  background: #22252a;
}
</style>
