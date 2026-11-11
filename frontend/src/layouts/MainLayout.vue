<template>
  <div class="layout">
    <aside class="sidebar" :class="{ collapsed }">
      <div class="logo">
        <el-icon :size="22"><Monitor /></el-icon>
        <span v-show="!collapsed" class="logo-text">网络设备运维平台</span>
      </div>

      <div v-show="!collapsed" class="menu-search">
        <el-input
          v-model="keyword"
          placeholder="搜索功能"
          size="small"
          clearable
          :prefix-icon="Search"
        />
      </div>

      <div v-show="!collapsed" class="menu-actions">
        <el-button link size="small" @click="toggleAllGroups">
          <el-icon><component :is="allExpanded ? 'Fold' : 'Expand'" /></el-icon>
          {{ allExpanded ? '全部收起' : '全部展开' }}
        </el-button>
      </div>

      <el-scrollbar class="menu-scroll">
        <el-menu
          :key="menuEpoch"
          ref="menuRef"
          :default-active="$route.path"
          :collapse="collapsed"
          :collapse-transition="false"
          :default-openeds="openedGroups"
          router
          class="menu"
          @open-change="onOpenChange"
        >
          <template v-for="group in visibleGroups" :key="group.key">
            <el-sub-menu v-if="!collapsed" :index="group.key">
              <template #title>
                <el-icon><component :is="group.icon" /></el-icon>
                <span>{{ group.title }}</span>
              </template>
              <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
                <el-icon><component :is="item.meta.icon" /></el-icon>
                <template #title>{{ item.meta.title }}</template>
              </el-menu-item>
            </el-sub-menu>
            <!-- 折叠态：分组标题不显示，菜单项平铺 -->
            <template v-else>
              <el-menu-item v-for="item in group.items" :key="item.path" :index="item.path">
                <el-icon><component :is="item.meta.icon" /></el-icon>
                <template #title>{{ item.meta.title }}</template>
              </el-menu-item>
            </template>
          </template>

          <el-empty
            v-if="!visibleGroups.length"
            description="无匹配菜单"
            :image-size="50"
          />
        </el-menu>
      </el-scrollbar>
      <div class="collapse-btn" @click="collapsed = !collapsed">
        <el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="route.meta?.title">{{ route.meta.title }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="right">
          <el-tooltip content="切换外观" placement="bottom">
            <el-dropdown @command="onTheme">
              <el-button circle size="small">
                <el-icon><component :is="themeIcon" /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-for="opt in themeOptions"
                    :key="opt.value"
                    :command="opt.value"
                  >
                    <el-icon><component :is="opt.icon" /></el-icon>
                    {{ opt.label }}
                    <el-icon v-if="themeStore.mode === opt.value" style="margin-left: 8px; color: var(--el-color-primary)">
                      <Select />
                    </el-icon>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </el-tooltip>
          <el-tooltip content="刷新当前页" placement="bottom">
            <el-button circle size="small" @click="refresh">
              <el-icon><Refresh /></el-icon>
            </el-button>
          </el-tooltip>
          <el-badge :value="alertCount" :max="99" class="alert-badge">
            <el-button circle size="small" @click="$router.push('/alerts')">
              <el-icon><Bell /></el-icon>
            </el-button>
          </el-badge>
          <el-dropdown @command="onCommand">
            <span class="user-chip">
              <el-avatar :size="26" class="avatar">{{ avatarText }}</el-avatar>
              <span class="uname">{{ userStore.displayName }}</span>
              <el-tag
                v-for="(tag, idx) in roleTags"
                :key="tag.label"
                size="small"
                :type="tag.type"
                effect="plain"
                :style="{ marginRight: idx < roleTags.length - 1 ? '4px' : '0' }"
              >
                {{ tag.label }}
              </el-tag>
              <el-icon><CaretBottom /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ userStore.user?.username }}（{{ userStore.user?.source === 'ldap' ? '域账号' : '本地账号' }}）
                </el-dropdown-item>
                <el-dropdown-item divided command="system">
                  <el-icon><Setting /></el-icon>系统管理
                </el-dropdown-item>
                <el-dropdown-item command="logout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="content">
        <router-view v-slot="{ Component }">
          <keep-alive :include="['DashboardView']">
            <component :is="Component" :key="route.fullPath" @refresh-data="refresh" />
          </keep-alive>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { useUserStore } from '../store/user'
import { useThemeStore, THEME_OPTIONS } from '../store/theme'
import { alertApi } from '../api'
import { MENU_GROUPS } from '../router'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const themeStore = useThemeStore()
const collapsed = ref(false)
const alertCount = ref(0)
const keyword = ref('')
const menuRef = ref(null)
// 菜单重建计数：用于重新应用 default-openeds
const menuEpoch = ref(0)
// 重建期间抑制 open-change 回调
let suppressOpenChange = false
let timer = null

const themeOptions = THEME_OPTIONS
const themeIcon = computed(
  () => ({ light: 'Sunny', dark: 'Moon', system: 'Monitor' })[themeStore.mode] || 'Monitor',
)

/** 全部可见菜单项（不含隐藏项） */
const allItems = computed(() =>
  router.getRoutes().filter((r) => r.meta?.title && r.meta?.icon && !r.meta?.hidden),
)

/** 二级菜单的展开状态（可持久化） */
const openedGroups = ref(
  (() => {
    try {
      const raw = localStorage.getItem('netops_opened_groups')
      return raw ? JSON.parse(raw) : MENU_GROUPS.map((g) => g.key)
    } catch (e) {
      return MENU_GROUPS.map((g) => g.key)
    }
  })(),
)

/** 是否全部展开（基于实际已展开的分组数判断） */
const allExpanded = computed(
  () => openedGroups.value.length >= MENU_GROUPS.length,
)

/** 全部展开 / 全部收起 */
function toggleAllGroups() {
  const keys = MENU_GROUPS.map((g) => g.key)
  const next = allExpanded.value ? [] : keys
  openedGroups.value = next
  try {
    localStorage.setItem('netops_opened_groups', JSON.stringify(next))
  } catch (e) {
    /* 忽略 */
  }
  // 通过重建菜单组件应用新的展开状态：
  // el-menu 的 default-openeds 只在初始化生效，而直接调用 open()/close()
  // 在关闭最后一个子菜单时会让 activeIndex 变为 undefined（Element Plus 内部缺陷）。
  // 重建组件是最稳妥且行为一致的做法。
  suppressOpenChange = true
  menuEpoch.value += 1
  nextTick(() => {
    suppressOpenChange = false
  })
}

/** 监听子菜单的展开/收起变化并持久化 */
function onOpenChange(key, opened) {
  // 重建菜单时会批量触发 open-change，此时不应覆盖刚设置的状态
  if (suppressOpenChange) return
  const set = new Set(openedGroups.value)
  if (opened) set.add(key)
  else set.delete(key)
  openedGroups.value = [...set]
  try {
    localStorage.setItem('netops_opened_groups', JSON.stringify(openedGroups.value))
  } catch (e) {
    /* 忽略 */
  }
}

/** 按分组归类，并根据搜索关键字过滤 */
const visibleGroups = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  const matched = allItems.value.filter((item) => {
    if (!kw) return true
    const title = String(item.meta.title).toLowerCase()
    const path = String(item.path).toLowerCase()
    return title.includes(kw) || path.includes(kw)
  })

  return MENU_GROUPS.map((g) => ({
    key: g.key,
    title: g.title,
    icon: g.icon,
    order: g.order,
    items: matched
      .filter((i) => i.meta.group === g.key)
      .sort((a, b) => a.path.localeCompare(b.path)),
  }))
    .filter((g) => g.items.length)
    .sort((a, b) => a.order - b.order)
})

function onTheme(mode) {
  themeStore.setMode(mode)
}

// 顶栏角色标签：支持一个用户同时具备多个角色
const roleTags = computed(() => {
  const map = {
    admin: { label: '管理员', type: 'danger' },
    operator: { label: '操作员', type: 'warning' },
    approver: { label: '审批员', type: 'success' },
    viewer: { label: '只读', type: 'info' },
  }
  const roles = userStore.roles || []
  return (roles.length ? roles : ['viewer']).map((r) => map[r] || { label: r, type: 'info' })
})
const avatarText = computed(() => (userStore.displayName || '?').charAt(0).toUpperCase())

async function loadAlertCount() {
  try {
    const res = await alertApi.events({ status: 'firing', page_size: 1 })
    alertCount.value = res.total || 0
  } catch (e) {
    /* 忽略 */
  }
}

function refresh() {
  window.dispatchEvent(new CustomEvent('netops:refresh'))
  loadAlertCount()
}

function onCommand(cmd) {
  if (cmd === 'logout') {
    ElMessageBox.confirm('确定要退出登录吗？', '提示', { type: 'warning' })
      .then(() => {
        userStore.logout()
        router.push('/login')
      })
      .catch(() => {})
  } else if (cmd === 'system') {
    router.push('/system')
  }
}

onMounted(() => {
  loadAlertCount()
  timer = setInterval(loadAlertCount, 30000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.sidebar {
  width: 210px;
  background: var(--netops-sidebar-bg);
  border-right: 1px solid var(--netops-border);
  display: flex;
  flex-direction: column;
  transition: width 0.2s;
  flex-shrink: 0;
}

.sidebar.collapsed {
  width: 64px;
}

.logo {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  color: var(--netops-primary);
  font-weight: 700;
  font-size: 17px;
  border-bottom: 1px solid var(--netops-border);
  flex-shrink: 0;
}

.logo-text {
  color: var(--netops-text);
}

.menu-search {
  padding: 10px 12px 4px;
}

.menu-search :deep(.el-input__wrapper) {
  border-radius: 6px;
}

.menu-actions {
  padding: 0 12px 6px;
  display: flex;
  justify-content: flex-end;
}

.menu-actions .el-button {
  font-size: 12px;
  color: var(--netops-text-muted);
  padding: 0;
}

.menu-actions .el-button:hover {
  color: var(--el-color-primary);
}

.menu-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.menu-scroll :deep(.el-scrollbar__wrap) {
  overflow-x: hidden;
}

.menu {
  border-right: none;
  padding-bottom: 8px;
}

.menu :deep(.el-sub-menu__title) {
  font-size: 12px;
  font-weight: 600;
  color: var(--netops-text-muted);
  letter-spacing: 0.3px;
  padding: 10px 20px 6px !important;
  line-height: 1.4;
  height: auto;
}

.menu :deep(.el-sub-menu__title:hover) {
  background: transparent;
  color: var(--netops-text);
}

.menu :deep(.el-sub-menu .el-menu-item) {
  padding-left: 44px !important;
}

.menu :deep(.el-menu-item) {
  height: 38px;
  line-height: 38px;
  margin: 1px 8px;
  border-radius: 6px;
}

.menu :deep(.el-menu-item.is-active) {
  background: var(--netops-sidebar-active);
  color: var(--netops-primary);
  font-weight: 600;
}

.menu :deep(.el-menu-item:hover) {
  background: var(--netops-fill-color-light, #f4f6f8);
}

.collapse-btn {
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--netops-text-muted);
  border-top: 1px solid var(--netops-border);
}

.collapse-btn:hover {
  color: var(--netops-primary);
  background: #f8fafc;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.topbar {
  height: 56px;
  background: #fff;
  border-bottom: 1px solid var(--netops-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  flex-shrink: 0;
}

.topbar .right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.alert-badge {
  margin-right: 4px;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  outline: none;
  padding: 4px 8px;
  border-radius: 6px;
}

.user-chip:hover {
  background: #f1f5f9;
}

.avatar {
  background: var(--netops-primary);
  color: #fff;
  font-size: 13px;
}

.uname {
  font-size: 13px;
  font-weight: 500;
}

.content {
  flex: 1;
  overflow-y: auto;
  background: var(--netops-bg);
}
</style>
