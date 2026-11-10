import { createRouter, createWebHashHistory } from 'vue-router'

/**
 * 菜单分组定义。
 * key 与路由 meta.group 对应，order 决定分组顺序。
 * icon 为二级菜单的分组图标。
 */
export const MENU_GROUPS = [
  { key: 'overview', title: '概览', icon: 'Odometer', order: 1 },
  { key: 'device', title: '设备管理', icon: 'Switch', order: 2 },
  { key: 'traffic', title: '流量分析', icon: 'TrendCharts', order: 3 },
  { key: 'ops', title: '运维监控', icon: 'Tools', order: 4 },
  { key: 'asset', title: '资产组织', icon: 'Box', order: 5 },
  { key: 'system', title: '系统与智能', icon: 'Setting', order: 6 },
]

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('../layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('../views/DashboardView.vue'),
        meta: { title: '总览大屏', icon: 'Odometer', group: 'overview' },
      },
      {
        path: 'devices',
        name: 'devices',
        component: () => import('../views/DevicesView.vue'),
        meta: { title: '设备管理', icon: 'Switch', group: 'device' },
      },
      {
        path: 'device/:id',
        name: 'device-detail',
        component: () => import('../views/DeviceDetailView.vue'),
        meta: { title: '设备详情', hidden: true },
      },
      {
        path: 'interfaces',
        name: 'interfaces',
        component: () => import('../views/InterfacesView.vue'),
        meta: { title: '实时接口速率', icon: 'DataLine', group: 'device' },
      },
      {
        path: 'topology',
        name: 'topology',
        component: () => import('../views/TopologyView.vue'),
        meta: { title: '网络拓扑', icon: 'Connection', group: 'device' },
      },
      {
        path: 'topn',
        name: 'topn',
        component: () => import('../views/TopNView.vue'),
        meta: { title: '接口流量 TopN', icon: 'TrendCharts', group: 'traffic' },
      },
      {
        path: 'netflow',
        name: 'netflow',
        component: () => import('../views/NetflowView.vue'),
        meta: { title: 'NetFlow 会话分析', icon: 'Share', group: 'traffic' },
      },
      {
        path: 'telemetry',
        name: 'telemetry',
        component: () => import('../views/TelemetryView.vue'),
        meta: { title: 'Telemetry 遥测', icon: 'Cpu', group: 'traffic' },
      },
      {
        path: 'alerts',
        name: 'alerts',
        component: () => import('../views/AlertsView.vue'),
        meta: { title: '告警中心', icon: 'Bell', group: 'ops' },
      },
      {
        path: 'logs',
        name: 'logs',
        component: () => import('../views/LogsView.vue'),
        meta: { title: '日志中心', icon: 'Document', group: 'ops' },
      },
      {
        path: 'inspection',
        name: 'inspection',
        component: () => import('../views/InspectionView.vue'),
        meta: { title: '基线巡检', icon: 'Checked', group: 'ops' },
      },
      {
        path: 'backups',
        name: 'backups',
        component: () => import('../views/BackupsView.vue'),
        meta: { title: '配置备份', icon: 'FolderOpened', group: 'ops' },
      },
      {
        path: 'approval',
        name: 'approval',
        component: () => import('../views/ConfigApprovalView.vue'),
        meta: { title: '配置审批', icon: 'Stamp', group: 'ops' },
      },
      {
        path: 'device-access',
        name: 'device-access',
        component: () => import('../views/DeviceAccessView.vue'),
        meta: { title: '设备接入', icon: 'Key', group: 'ops' },
      },
      {
        path: 'stores',
        name: 'stores',
        component: () => import('../views/StoresView.vue'),
        meta: { title: '门店管理', icon: 'Shop', group: 'asset' },
      },
      {
        path: 'assets',
        name: 'assets',
        component: () => import('../views/AssetsView.vue'),
        meta: { title: '资产管理', icon: 'Box', group: 'asset' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('../views/AuditLogView.vue'),
        meta: { title: '审计日志', icon: 'Finished', group: 'asset' },
      },
      {
        path: 'assistant',
        name: 'assistant',
        component: () => import('../views/AssistantView.vue'),
        meta: { title: 'AI 网络助手', icon: 'ChatDotRound', group: 'system' },
      },
      {
        path: 'system',
        name: 'system',
        component: () => import('../views/SystemView.vue'),
        meta: { title: '系统管理', icon: 'Setting', group: 'system' },
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('netops_token')
  document.title = to.meta?.title ? `${to.meta.title} · NetOps` : 'NetOps 网络设备监控平台'
  if (!to.meta?.public && !token) {
    next({ path: '/login', query: { redirect: to.fullPath } })
    return
  }
  if (to.path === '/login' && token) {
    next({ path: '/dashboard' })
    return
  }
  next()
})

export default router
