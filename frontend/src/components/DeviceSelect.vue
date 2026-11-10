<template>
  <el-select
    v-model="inner"
    :placeholder="placeholder"
    :clearable="clearable"
    :multiple="multiple"
    :style="{ width }"
    filterable
    @change="onChange"
  >
    <el-option
      v-for="item in options"
      :key="item.value"
      :label="item.label"
      :value="item.value"
    >
      <span>{{ item.label }}</span>
      <span v-if="item.extra" class="opt-extra">{{ item.extra }}</span>
    </el-option>
  </el-select>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { deviceApi } from '../api'

const props = defineProps({
  modelValue: { default: null },
  placeholder: { type: String, default: '全部设备' },
  clearable: { type: Boolean, default: true },
  multiple: { type: Boolean, default: false },
  width: { type: String, default: '200px' },
  storeId: { type: [Number, String], default: null },
})
const emit = defineEmits(['update:modelValue', 'change'])

const devices = ref([])
const inner = ref(props.modelValue)

watch(
  () => props.modelValue,
  (v) => (inner.value = v)
)

// 门店变化时重新拉取设备列表（实现门店 → 设备联动）
watch(
  () => props.storeId,
  async () => {
    await load()
    // 已选设备若不在新范围内则清空，避免残留其他门店的设备
    if (inner.value !== null && inner.value !== undefined && inner.value !== '') {
      const ids = devices.value.map((d) => d.id)
      const selected = Array.isArray(inner.value) ? inner.value : [inner.value]
      const stillValid = selected.every((id) => ids.includes(id))
      if (!stillValid) {
        inner.value = props.multiple ? [] : null
        emit('update:modelValue', inner.value)
        emit('change', inner.value)
      }
    }
  }
)

async function load() {
  try {
    const res = await deviceApi.list({
      page_size: 500,
      store_id: props.storeId || undefined,
    })
    devices.value = res.items || []
  } catch (e) {
    devices.value = []
  }
}

const options = computed(() =>
  devices.value.map((d) => ({
    value: d.id,
    label: d.name,
    extra: `${d.ip}${d.store_name ? ' · ' + d.store_name : ''}`,
  }))
)

function onChange(v) {
  emit('update:modelValue', v)
  emit('change', v)
}

load()
</script>

<style scoped>
.opt-extra {
  float: right;
  color: var(--netops-text-muted);
  font-size: 12px;
  margin-left: 14px;
}
</style>
