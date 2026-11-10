<template>
  <router-view />
</template>

<script setup>
import { onMounted } from 'vue'
import { useUserStore } from './store/user'
import { useThemeStore } from './store/theme'

const userStore = useUserStore()
const themeStore = useThemeStore()

onMounted(() => {
  userStore.loadConfig()
  themeStore.init()
})
</script>
