<template>
  <div class="login-page">
    <div class="login-left">
      <div class="brand">
        <el-icon :size="34"><Monitor /></el-icon>
        <h1>{{ appName }}</h1>
      </div>
      <p class="tagline">面向多厂商网络设备的统一运维与监控平台</p>
      <ul class="features">
        <li><el-icon><DataLine /></el-icon>实时接口速率与流量分析</li>
        <li><el-icon><Cpu /></el-icon>CPU / 内存等资源指标监控</li>
        <li><el-icon><Bell /></el-icon>多级告警与通知分发</li>
        <li><el-icon><Document /></el-icon>交换机日志集中采集</li>
        <li><el-icon><Checked /></el-icon>基线巡检与自动报告</li>
        <li><el-icon><Box /></el-icon>资产台账与门店化管理</li>
      </ul>
      <div class="vendors">
        <span>支持厂商</span>
        <el-tag v-for="v in vendorTags" :key="v" size="small" effect="plain">{{ v }}</el-tag>
      </div>
    </div>

    <div class="login-right">
      <div class="login-box">
        <h2>账号登录</h2>
        <p class="hint">请使用平台账号或域账号登录</p>

        <el-form ref="formRef" :model="form" :rules="rules" size="large" @keyup.enter="submit">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名 / 域账号" clearable>
              <template #prefix><el-icon><User /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="密码"
              show-password
            >
              <template #prefix><el-icon><Lock /></el-icon></template>
            </el-input>
          </el-form-item>
          <el-form-item v-if="userStore.ldapEnabled">
            <el-radio-group v-model="form.source" size="small">
              <el-radio-button value="auto">自动</el-radio-button>
              <el-radio-button value="local">本地账号</el-radio-button>
              <el-radio-button value="ldap">域账号</el-radio-button>
            </el-radio-group>
            <div v-if="userStore.ldapDomain" class="domain-tip">
              域：{{ userStore.ldapDomain }}
            </div>
          </el-form-item>
          <el-button
            type="primary"
            class="submit-btn"
            size="large"
            :loading="loading"
            @click="submit"
          >
            登 录
          </el-button>
        </el-form>

        <el-alert
          v-if="errorMsg"
          :title="errorMsg"
          type="error"
          show-icon
          :closable="false"
          class="err-alert"
        />

        <div class="footer-tip">
          <el-icon><InfoFilled /></el-icon>
          初始账号 admin / admin123，登录后请及时修改
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../store/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const formRef = ref()
const loading = ref(false)
const errorMsg = ref('')
const appName = ref('网络设备运维平台')
// 厂商列表由后端 /api/auth/config 下发，与设备管理页同源；
// 拉取失败时回退到内置清单，保证登录页始终有内容
const FALLBACK_VENDORS = [
  'MikroTik RouterOS',
  'Cisco IOS/NXOS',
  'Cisco ASA 防火墙',
  'Cisco WLC 无线控制器',
  '华为 VRP',
  '新华三 H3C Comware',
  '飞塔 Fortinet FortiGate',
  '通用标准 MIB 设备',
]
const vendorTags = ref([...FALLBACK_VENDORS])

const form = reactive({
  username: '',
  password: '',
  source: 'auto',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  errorMsg.value = ''
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return
  loading.value = true
  try {
    const user = await userStore.login(form.username, form.password, form.source)
    ElMessage.success(`欢迎回来，${user.display_name || user.username}`)
    router.push(route.query.redirect || '/dashboard')
  } catch (e) {
    errorMsg.value = e?.response?.data?.detail || '登录失败，请检查用户名与密码'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await userStore.loadConfig()
  // 公开配置里带回了厂商列表与平台名，直接用于登录页展示
  const cfg = userStore.config
  if (cfg?.vendors?.length) {
    vendorTags.value = cfg.vendors.map((v) => v.label)
  }
  if (cfg?.app_name) {
    appName.value = cfg.app_name
  }
})
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  background: #fff;
}

.login-left {
  width: 46%;
  min-width: 380px;
  background: linear-gradient(150deg, #1e3a8a 0%, #2563eb 55%, #3b82f6 100%);
  color: #fff;
  padding: 56px 52px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand h1 {
  font-size: 26px;
  margin: 0;
  letter-spacing: 1px;
}

.tagline {
  font-size: 17px;
  opacity: 0.9;
  margin: 10px 0 36px;
}

.features {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.features li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  opacity: 0.95;
}

.features .el-icon {
  background: rgba(255, 255, 255, 0.18);
  border-radius: 6px;
  padding: 5px;
  font-size: 16px;
}

.vendors {
  margin-top: 44px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 13px;
  opacity: 0.9;
}

.vendors .el-tag {
  background: rgba(255, 255, 255, 0.15);
  border-color: rgba(255, 255, 255, 0.3);
  color: #fff;
}

.login-right {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.login-box {
  width: 100%;
  max-width: 380px;
}

.login-box h2 {
  font-size: 22px;
  margin: 0 0 6px;
}

.hint {
  color: var(--netops-text-muted);
  font-size: 13px;
  margin: 0 0 26px;
}

.submit-btn {
  width: 100%;
  margin-top: 4px;
}

.err-alert {
  margin-top: 14px;
}

.footer-tip {
  margin-top: 22px;
  font-size: 12px;
  color: var(--netops-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.domain-tip {
  font-size: 12px;
  color: var(--netops-text-muted);
  margin-top: 6px;
}

@media (max-width: 900px) {
  .login-left {
    display: none;
  }
}
</style>
