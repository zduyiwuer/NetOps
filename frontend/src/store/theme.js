import { defineStore } from 'pinia'

export const THEME_OPTIONS = [
  { value: 'light', label: '浅色', icon: 'Sunny' },
  { value: 'dark', label: '深色', icon: 'Moon' },
  { value: 'system', label: '跟随系统', icon: 'Monitor' },
]

const STORAGE_KEY = 'netops_theme'

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: localStorage.getItem(STORAGE_KEY) || 'system',
    systemPrefersDark: false,
  }),
  getters: {
    // 实际生效的主题（system 模式解析为 light/dark）
    resolved: (state) => {
      if (state.mode === 'system') {
        return state.systemPrefersDark ? 'dark' : 'light'
      }
      return state.mode
    },
    isDark: (state) => {
      if (state.mode === 'system') return state.systemPrefersDark
      return state.mode === 'dark'
    },
  },
  actions: {
    init() {
      const mq = window.matchMedia('(prefers-color-scheme: dark)')
      this.systemPrefersDark = mq.matches
      const handler = (e) => {
        this.systemPrefersDark = e.matches
        this.apply()
      }
      if (mq.addEventListener) {
        mq.addEventListener('change', handler)
      } else if (mq.addListener) {
        mq.addListener(handler)
      }
      this.apply()
    },
    setMode(mode) {
      this.mode = mode
      localStorage.setItem(STORAGE_KEY, mode)
      this.apply()
    },
    apply() {
      const dark = this.isDark
      document.documentElement.classList.toggle('dark', dark)
      document.documentElement.setAttribute('data-theme', this.resolved)
    },
  },
})
