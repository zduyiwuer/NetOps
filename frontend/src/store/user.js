import { defineStore } from 'pinia'
import { authApi } from '../api'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('netops_token') || '',
    user: JSON.parse(localStorage.getItem('netops_user') || 'null'),
    ldapEnabled: false,
    ldapDomain: '',
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    role: (state) => state.user?.role || 'viewer',
    isAdmin: (state) => state.user?.role === 'admin',
    canOperate: (state) => ['admin', 'operator'].includes(state.user?.role),
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
