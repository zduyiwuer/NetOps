import { defineStore } from 'pinia'
import { authApi } from '../api'

// 角色中文名与配色（与后端 app/core/roles.py 的 ROLE_META 对应）
export const ROLE_META = {
  admin: { label: '管理员', type: 'danger' },
  operator: { label: '操作员', type: 'warning' },
  approver: { label: '审批员', type: 'success' },
  viewer: { label: '只读', type: 'info' },
}

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('netops_token') || '',
    user: JSON.parse(localStorage.getItem('netops_user') || 'null'),
    ldapEnabled: false,
    ldapDomain: '',
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    // 用户实际拥有的角色集合（历史数据只有单角色时回退到 role）
    roles: (state) => {
      const list = state.user?.roles
      if (Array.isArray(list) && list.length) return list
      return state.user?.role ? [state.user.role] : ['viewer']
    },
    // 主角色：权限最高者
    role: (state) => state.user?.role || 'viewer',
    isAdmin: (state) => {
      const list = state.user?.roles
      if (Array.isArray(list) && list.length) return list.includes('admin')
      return state.user?.role === 'admin'
    },
    canOperate: (state) => {
      const list = state.user?.roles
      const roles = Array.isArray(list) && list.length ? list : [state.user?.role]
      return roles.includes('admin') || roles.includes('operator')
    },
    // 审批权：审批员或管理员
    canApprove: (state) => {
      const list = state.user?.roles
      const roles = Array.isArray(list) && list.length ? list : [state.user?.role]
      return roles.includes('admin') || roles.includes('approver')
    },
    // 角色中文标签列表
    roleLabels: (state) => {
      const list = state.user?.roles
      const roles = Array.isArray(list) && list.length ? list : [state.user?.role || 'viewer']
      return roles.map((r) => ROLE_META[r]?.label || r)
    },
    displayName: (state) => state.user?.display_name || state.user?.username || '',
  },
  actions: {
    async login(username, password, source = 'auto') {
      const res = await authApi.login({ username, password, source })
      this.token = res.access_token
      this.user = res.user
      localStorage.setItem('netops_token', res.access_token)
      localStorage.setItem('netops_user', JSON.stringify(res.user))
      return res.user
    },
    async loadConfig() {
      try {
        const cfg = await authApi.config()
        this.ldapEnabled = !!cfg.ldap_enabled
        this.ldapDomain = cfg.ldap_domain || ''
      } catch (e) {
        /* 忽略 */
      }
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('netops_token')
      localStorage.removeItem('netops_user')
    },
  },
})
