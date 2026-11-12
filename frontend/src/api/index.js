import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('netops_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => {
    // 文件下载直接返回
    if (response.config.responseType === 'blob') return response
    return response.data
  },
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail || error.message || '请求失败'

    if (status === 401) {
      localStorage.removeItem('netops_token')
      localStorage.removeItem('netops_user')
      if (!location.hash.includes('/login')) {
        location.hash = '#/login'
      }
    }
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    return Promise.reject(error)
  }
)

export default http

// ---------------------------------------------------------------- 认证
export const authApi = {
  config: () => http.get('/auth/config'),
  login: (data) => http.post('/auth/login', data),
  me: () => http.get('/auth/me'),
  listUsers: () => http.get('/auth/users'),
  listRoles: () => http.get('/auth/roles'),
  listVendors: () => http.get('/vendors'),
  createUser: (data) => http.post('/auth/users', data),
  updateUser: (id, data) => http.put(`/auth/users/${id}`, data),
  deleteUser: (id) => http.delete(`/auth/users/${id}`),
  loginLogs: (limit = 100) => http.get(`/auth/login-logs?limit=${limit}`),
  ldapTest: () => http.get('/auth/ldap/test'),
  ldapUsers: () => http.get('/auth/ldap/users'),
  ldapSync: () => http.post('/auth/ldap/sync'),
}

// ---------------------------------------------------------------- 设备
export const deviceApi = {
  list: (params) => http.get('/devices', { params }),
  get: (id) => http.get(`/devices/${id}`),
  create: (data) => http.post('/devices', data),
  update: (id, data) => http.put(`/devices/${id}`, data),
  remove: (id) => http.delete(`/devices/${id}`),
  collect: (id) => http.post(`/devices/${id}/collect`),
  test: (id) => http.post(`/devices/${id}/test`),
  probe: (id) => http.post(`/devices/${id}/probe`),
  probeBatch: (data = {}) => http.post('/devices/probe/batch', data),
  stores: () => http.get('/stores'),
  createStore: (data) => http.post('/stores', data),
  updateStore: (id, data) => http.put(`/stores/${id}`, data),
  deleteStore: (id) => http.delete(`/stores/${id}`),
  storeDevices: (id) => http.get(`/stores/${id}/devices`),
  vendors: () => http.get('/vendors'),
  assetSummary: () => http.get('/assets/summary'),
  importTemplateCsv: () =>
    http.get('/import/template.csv', { responseType: 'blob' }),
  importTemplateXlsx: () =>
    http.get('/import/template.xlsx', { responseType: 'blob' }),
  importDevices: (file, updateExisting = false) => {
    const fd = new FormData()
    fd.append('file', file)
    return http.post(`/import/devices?update_existing=${updateExisting}`, fd)
  },
  exportCsv: (params) => http.get('/export/devices.csv', { params, responseType: 'blob' }),
  exportXlsx: (params) => http.get('/export/devices.xlsx', { params, responseType: 'blob' }),
}

// ---------------------------------------------------------------- 监控
export const monitorApi = {
  overview: (params) => http.get('/monitor/overview', { params }),
  realtimeInterfaces: (params) => http.get('/monitor/interfaces/realtime', { params }),
  deviceInterfaces: (id) => http.get(`/monitor/devices/${id}/interfaces`),
  deviceMetrics: (id, hours = 24) => http.get(`/monitor/devices/${id}/metrics`, { params: { hours } }),
  deviceSessions: (id, hours = 24) => http.get(`/monitor/devices/${id}/sessions`, { params: { hours } }),
  firewallSessions: (hours = 1) => http.get('/monitor/firewalls/sessions', { params: { hours } }),
  interfaceHistory: (id, ifIndex, hours = 6) =>
    http.get(`/monitor/devices/${id}/interfaces/history`, {
      params: { if_index: ifIndex, hours },
    }),
  trafficTopn: (params) => http.get('/monitor/topn/traffic', { params }),
  errorTopn: (params) => http.get('/monitor/topn/errors', { params }),
  trafficTimeseries: (params) => http.get('/monitor/traffic/timeseries', { params }),
}

// ---------------------------------------------------------------- 告警
export const alertApi = {
  rules: (params) => http.get('/alerts/rules', { params }),
  createRule: (data) => http.post('/alerts/rules', data),
  updateRule: (id, data) => http.put(`/alerts/rules/${id}`, data),
  deleteRule: (id) => http.delete(`/alerts/rules/${id}`),
  events: (params) => http.get('/alerts/events', { params }),
  ack: (id) => http.post(`/alerts/events/${id}/ack`),
  resolve: (id) => http.post(`/alerts/events/${id}/resolve`),
  batchAck: (ids) => http.post('/alerts/events/batch-ack', ids),
  statistics: (params) => http.get('/alerts/statistics', { params }),
  channels: () => http.get('/alerts/channels'),
  createChannel: (data) => http.post('/alerts/channels', data),
  updateChannel: (id, data) => http.put(`/alerts/channels/${id}`, data),
  deleteChannel: (id) => http.delete(`/alerts/channels/${id}`),
  testChannel: (id) => http.post(`/alerts/channels/${id}/test`),
}

// ---------------------------------------------------------------- 日志
export const logApi = {
  list: (params) => http.get('/logs', { params }),
  statistics: (params) => http.get('/logs/statistics', { params }),
  collect: (deviceId) => http.post(`/logs/collect/${deviceId}`),
  cleanup: (days) => http.delete('/logs/cleanup', { params: { days } }),
  systemLogs: (params) => http.get('/logs/system', { params }),
  systemLogStatistics: (params) => http.get('/logs/system/statistics', { params }),
  cleanupSystemLogs: (days) => http.delete('/logs/system/cleanup', { params: { days } }),
}

// ---------------------------------------------------------------- 备份
export const backupApi = {
  list: (params) => http.get('/backups', { params }),
  stores: () => http.get('/backups/stores'),
  create: (deviceId) => http.post(`/backups/${deviceId}`),
  batch: (ids) => http.post('/backups/batch', ids),
  download: (id) => http.get(`/backups/download/${id}`, { responseType: 'blob' }),
  downloadStore: (storeId) => http.get(`/backups/download-store/${storeId}`, { responseType: 'blob' }),
  view: (id) => http.get(`/backups/view/${id}`),
  preview: (id, lines = 200) => http.get(`/backups/preview/${id}`, { params: { lines } }),
  diff: (id, againstId) => http.get(`/backups/diff/${id}`, { params: { against_id: againstId } }),
  remove: (id) => http.delete(`/backups/${id}`),
}

// ---------------------------------------------------------------- 平台设置 / 拓扑 / 审计 / AI
export const settingsApi = {
  get: () => http.get('/settings'),
  update: (data) => http.put('/settings', data),
  collectorStatus: () => http.get('/settings/collector-status'),
  toggleCollector: (target, enabled) => http.post('/settings/collector/toggle', { target, enabled }),
}

export const topologyApi = {
  neighbors: (params) => http.get('/topology/neighbors', { params }),
  graph: (params) => http.get('/topology/graph', { params }),
  collect: (deviceId) => http.post('/topology/collect', null, { params: { device_id: deviceId } }),
  clear: () => http.delete('/topology/neighbors'),
}

export const auditApi = {
  list: (params) => http.get('/audit', { params }),
  actions: () => http.get('/audit/actions'),
  summary: (params) => http.get('/audit/summary', { params }),
}

export const aiApi = {
  providers: () => http.get('/ai/providers'),
  config: () => http.get('/ai/config'),
  updateConfig: (data) => http.post('/ai/config', data),
  test: () => http.post('/ai/test'),
  conversations: (params) => http.get('/ai/conversations', { params }),
  conversation: (id) => http.get(`/ai/conversations/${id}`),
  deleteConversation: (id) => http.delete(`/ai/conversations/${id}`),
  previewContext: (params) => http.post('/ai/context', null, { params }),
  chat: (payload) => http.post('/ai/chat', payload, { responseType: 'text', timeout: 300000 }),
}

// ---------------------------------------------------------------- 巡检
export const inspectionApi = {
  templates: () => http.get('/inspection/templates'),
  createTemplate: (data) => http.post('/inspection/templates', data),
  updateTemplate: (id, data) => http.put(`/inspection/templates/${id}`, data),
  deleteTemplate: (id) => http.delete(`/inspection/templates/${id}`),
  tasks: (params) => http.get('/inspection/tasks', { params }),
  createTask: (data) => http.post('/inspection/tasks', data),
  task: (id) => http.get(`/inspection/tasks/${id}`),
  deleteTask: (id) => http.delete(`/inspection/tasks/${id}`),
  reportUrl: (id) => `/api/inspection/tasks/${id}/report`,
  reportExcelUrl: (id) => `/api/inspection/tasks/${id}/report.xlsx`,
}

// ---------------------------------------------------------------- 系统
export const systemApi = {
  health: () => http.get('/health'),
}

// ---------------------------------------------------------------- NetFlow
export const netflowApi = {
  statistics: (params) => http.get('/netflow/statistics', { params }),
  topTalkers: (params) => http.get('/netflow/top-talkers', { params }),
  conversations: (params) => http.get('/netflow/conversations', { params }),
  timeseries: (params) => http.get('/netflow/timeseries', { params }),
  flows: (params) => http.get('/netflow/flows', { params }),
  exporters: () => http.get('/netflow/exporters'),
  createExporter: (data) => http.post('/netflow/exporters', data),
  updateExporter: (id, data) => http.put(`/netflow/exporters/${id}`, data),
  deleteExporter: (id) => http.delete(`/netflow/exporters/${id}`),
  cleanup: (days) => http.delete('/netflow/cleanup', { params: { days } }),
}

// ---------------------------------------------------------------- Telemetry
export const telemetryApi = {
  status: () => http.get('/telemetry/status'),
  paths: () => http.get('/telemetry/paths'),
  createPath: (data) => http.post('/telemetry/paths', data),
  deletePath: (id) => http.delete(`/telemetry/paths/${id}`),
  configs: () => http.get('/telemetry/configs'),
  createConfig: (data) => http.post('/telemetry/configs', data),
  updateConfig: (id, data) => http.put(`/telemetry/configs/${id}`, data),
  deleteConfig: (id) => http.delete(`/telemetry/configs/${id}`),
  samples: (params) => http.get('/telemetry/samples', { params }),
  simulate: (deviceId) => http.post(`/telemetry/simulate?device_id=${deviceId}`),
}

// ---------------------------------------------------------------- 设备接入与审批
export const accessApi = {
  accounts: (params) => http.get('/device-access/accounts', { params }),
  createAccount: (data) => http.post('/device-access/accounts', data),
  updateAccount: (id, data) => http.put(`/device-access/accounts/${id}`, data),
  deleteAccount: (id) => http.delete(`/device-access/accounts/${id}`),
  changes: (params) => http.get('/device-access/changes', { params }),
  change: (id) => http.get(`/device-access/changes/${id}`),
  createChange: (data) => http.post('/device-access/changes', data),
  reviewChange: (id, data) => http.post(`/device-access/changes/${id}/review`, data),
  applyChange: (id) => http.post(`/device-access/changes/${id}/apply`),
  aaaStatus: () => http.get('/device-access/aaa/status'),
  updateAaaConfig: (data) => http.put('/device-access/aaa/config', data),
  aaaSessions: (params) => http.get('/device-access/aaa/sessions', { params }),
  aaaTest: (params) => http.post('/device-access/aaa/test', null, { params }),
}

// ---------------------------------------------------------------- 定时任务
export const schedulerApi = {
  jobs: () => http.get('/scheduler/jobs'),
  updateJob: (jobId, data) => http.put(`/scheduler/jobs/${jobId}`, data),
  toggleJob: (jobId) => http.post(`/scheduler/jobs/${jobId}/toggle`),
  runJob: (jobId) => http.post(`/scheduler/jobs/${jobId}/run`),
  resetJob: (jobId) => http.post(`/scheduler/jobs/${jobId}/reset`),
}
