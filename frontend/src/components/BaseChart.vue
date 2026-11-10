<template>
  <div ref="el" class="chart-box" :style="{ height: height }"></div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: String, default: '300px' },
})

const el = ref()
let chart = null
let observer = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(props.option, true)
}

function resize() {
  chart?.resize()
}

onMounted(async () => {
  await nextTick()
  render()
  window.addEventListener('resize', resize)
  observer = new ResizeObserver(resize)
  if (el.value) observer.observe(el.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  observer?.disconnect()
  chart?.dispose()
  chart = null
})

watch(
  () => props.option,
  () => {
    render()
  },
  { deep: true }
)
</script>
