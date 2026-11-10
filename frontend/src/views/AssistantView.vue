<template>
  <div class="page assistant-page">
    <div class="page-header">
      <h2 class="page-title">
        AI 网络助手
        <span class="sub">基于平台实时数据诊断网络问题</span>
      </h2>
      <div class="toolbar">
        <el-tag v-if="config.enabled" type="success" effect="plain" size="small">
          {{ providerLabel }} · {{ config.model }}
        </el-tag>
        <el-tag v-else type="info" effect="plain" size="small">未启用</el-tag>
        <el-button :icon="Setting" @click="$router.push('/system')">配置</el-button>
        <el-button :icon="Delete" @click="clearChat">清空对话</el-button>
      </div>
    </div>

    <el-alert v-if="!config.enabled" type="warning" :closable="false" show-icon style="margin-bottom: 12px">
      <template #title>AI 助手尚未启用</template>
      请前往「系统管理 → AI 助手」配置 API 地址与密钥并启用。支持 OpenAI、Claude、
      DeepSeek、通义千问、Kimi、智谱、百度千帆、豆包、SiliconFlow 及 Ollama 本地模型。
    </el-alert>

    <div class="chat-layout">
      <!-- 侧边栏：会话历史 + 上下文选项 -->
      <div class="chat-side">
        <div class="side-block">
          <div class="side-title">上下文范围</div>
          <el-select v-model="deviceId" placeholder="全网设备" clearable size="small" style="width: 100%">
            <el-option v-for="d in devices" :key="d.id" :label="`${d.name} (${d.ip})`" :value="d.id" />
          </el-select>
          <div class="side-row">
            <el-checkbox v-model="contextEnabled" size="small">注入平台数据</el-checkbox>
          </div>
          <el-select v-model="contextHours" size="small" style="width: 100%; margin-top: 8px">
            <el-option label="最近 1 小时" :value="1" />
            <el-option label="最近 6 小时" :value="6" />
            <el-option label="最近 24 小时" :value="24" />
          </el-select>
          <el-button
            size="small"
            text
            type="primary"
            style="margin-top: 6px; padding-left: 0"
            @click="previewContext"
          >
            预览上下文
          </el-button>
        </div>

        <div class="side-block grow">
          <div class="side-title">历史对话</div>
          <el-scrollbar class="conv-scroll">
            <div
              v-for="c in conversations"
              :key="c.id"
              class="conv-item"
              :class="{ active: c.id === currentConvId }"
              @click="openConversation(c.id)"
            >
              <span class="conv-title">{{ c.title || '未命名对话' }}</span>
              <el-icon class="conv-del" @click.stop="removeConversation(c.id)"><Delete /></el-icon>
            </div>
            <el-empty v-if="!conversations.length" description="暂无历史对话" :image-size="60" />
          </el-scrollbar>
        </div>
      </div>

      <!-- 对话区 -->
      <div class="chat-main">
        <el-scrollbar ref="scrollRef" class="msg-scroll" v-loading="loading">
          <div v-if="!messages.length" class="welcome">
            <el-icon :size="46" color="#3b82f6"><ChatDotRound /></el-icon>
            <h3>我是您的网络运维助手</h3>
            <p>我可以结合平台的实时数据（设备、接口流量、CPU/内存、告警、日志、拓扑）分析网络问题。</p>
            <div class="suggest">
              <div
                v-for="s in suggestions"
                :key="s"
                class="suggest-item"
                @click="ask(s)"
              >
                {{ s }}
              </div>
            </div>
          </div>

          <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
            <div class="msg-avatar">
              <el-icon v-if="m.role === 'user'"><User /></el-icon>
              <el-icon v-else><Cpu /></el-icon>
            </div>
            <div class="msg-body">
              <div class="msg-content" v-html="renderMarkdown(m.content)"></div>
              <div v-if="m.role === 'assistant' && m.elapsed_ms" class="msg-meta">
                耗时 {{ (m.elapsed_ms / 1000).toFixed(1) }}s
              </div>
            </div>
          </div>

          <div v-if="loading" class="msg-row assistant">
            <div class="msg-avatar"><el-icon><Cpu /></el-icon></div>
            <div class="msg-body">
              <div class="msg-content typing">
                <el-icon class="is-loading"><Loading /></el-icon>
                正在分析平台数据…
              </div>
            </div>
          </div>
        </el-scrollbar>

        <div class="input-bar">
          <el-input
            v-model="question"
            type="textarea"
            :rows="2"
            resize="none"
            placeholder="描述你遇到的网络问题，例如：上海总部的 10.10.1.1 交换机 CPU 持续偏高，帮我分析原因"
            @keydown.enter.exact.prevent="ask()"
          />
          <el-button
            type="primary"
            :icon="Promotion"
            :loading="loading"
            :disabled="!question.trim()"
            @click="ask()"
          >
            发送
          </el-button>
        </div>
      </div>
    </div>

    <!-- 上下文预览 -->
    <el-dialog v-model="contextDialog" title="将发送给模型的平台数据上下文" width="800px" top="6vh">
      <pre class="ctx-box mono">{{ contextPreview }}</pre>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatDotRound, Cpu, Delete, Loading, Promotion, Setting, User } from '@element-plus/icons-vue'
import { aiApi, deviceApi } from '../api'

const config = ref({ enabled: false, provider: '', model: '' })
const providers = ref([])
const devices = ref([])
const conversations = ref([])
const messages = ref([])
const currentConvId = ref(null)
const question = ref('')
const loading = ref(false)
const deviceId = ref(null)
const contextEnabled = ref(true)
const contextHours = ref(6)
const contextDialog = ref(false)
const contextPreview = ref('')
const scrollRef = ref(null)

const providerLabel = computed(
  () => providers.value.find((p) => p.value === config.value.provider)?.label || config.value.provider,
)

const suggestions = [
  '当前网络有哪些活跃告警？按严重程度排序',
  '哪些接口流量最高？是否存在带宽瓶颈',
  '最近 6 小时有哪些 CPU 或内存异常的设备',
  '设备离线可能是什么原因？给出排查步骤',
  '统计各门店的设备在线率与资产分布',
]

function renderMarkdown(text) {
  if (!text) return ''
  let html = escapeHtml(text)
  // 代码块
  html = html.replace(/```([\s\S]*?)```/g, '<pre class="md-pre">$1</pre>')
  // 行内代码
  html = html.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>')
  // 粗体
  html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  // 列表
  html = html.replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>')
  html = html.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>')
  // 标题
  html = html.replace(/^###\s+(.+)$/gm, '<h4>$1</h4>')
  html = html.replace(/^##\s+(.+)$/gm, '<h3 class="md-h3">$1</h3>')
  html = html.replace(/^#\s+(.+)$/gm, '<h3 class="md-h2">$1</h3>')
  // 换行
  html = html.replace(/\n/g, '<br/>')
  return html
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

async function loadConfig() {
  try {
    config.value = await aiApi.config()
  } catch (e) {
    config.value = { enabled: false }
  }
}

async function loadProviders() {
  try {
    const res = await aiApi.providers()
    providers.value = res.items || []
  } catch (e) {
    providers.value = []
  }
}

async function loadDevices() {
  try {
    const res = await deviceApi.list({ page_size: 500 })
    devices.value = res.items || []
  } catch (e) {
    devices.value = []
  }
}

async function loadConversations() {
  try {
    const res = await aiApi.conversations({ limit: 30 })
    conversations.value = res.items || []
  } catch (e) {
    conversations.value = []
  }
}

async function openConversation(id) {
  try {
    const res = await aiApi.conversation(id)
    currentConvId.value = id
    messages.value = (res.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      elapsed_ms: m.elapsed_ms,
    }))
    scrollToBottom()
  } catch (e) {
    /* 忽略 */
  }
}

async function removeConversation(id) {
  try {
    await aiApi.deleteConversation(id)
    if (currentConvId.value === id) {
      currentConvId.value = null
      messages.value = []
    }
    loadConversations()
  } catch (e) {
    /* 忽略 */
  }
}

function clearChat() {
  currentConvId.value = null
  messages.value = []
  question.value = ''
}

async function previewContext() {
  try {
    const res = await aiApi.previewContext({
      device_id: deviceId.value || undefined,
      hours: contextHours.value,
    })
    contextPreview.value = res.content || ''
    contextDialog.value = true
  } catch (e) {
    ElMessage.warning('获取上下文失败')
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.setScrollTop(100000)
  })
}

async function ask(text) {
  const q = (text || question.value || '').trim()
  if (!q || loading.value) return
  question.value = ''
  messages.value.push({ role: 'user', content: q })
  scrollToBottom()
  loading.value = true

  const answer = { role: 'assistant', content: '', elapsed_ms: 0 }
  messages.value.push(answer)
  scrollToBottom()

  try {
    const res = await aiApi.chat({
      question: q,
      conversation_id: currentConvId.value,
      device_id: deviceId.value || undefined,
      context_enabled: contextEnabled.value,
      hours: contextHours.value,
    })
    // 解析 SSE
    const raw = typeof res.data === 'string' ? res.data : await res.data.text()
    let done = false
    for (const line of raw.split('\n')) {
      if (!line.startsWith('data:')) continue
      const payload = JSON.parse(line.slice(5).trim())
      if (payload.error) {
        answer.content = `错误：${payload.error}`
        break
      }
      if (payload.delta) {
        answer.content += payload.delta
        scrollToBottom()
      }
      if (payload.done) {
        done = true
        currentConvId.value = payload.conversation_id
        answer.elapsed_ms = payload.elapsed_ms || 0
      }
    }
    if (!answer.content) answer.content = '（模型未返回内容）'
  } catch (e) {
    answer.content = `请求失败：${e?.response?.data?.detail || e.message || '未知错误'}`
  } finally {
    loading.value = false
    scrollToBottom()
    loadConversations()
  }
}

onMounted(async () => {
  await Promise.all([loadConfig(), loadProviders(), loadDevices(), loadConversations()])
})
</script>

<style scoped>
.assistant-page {
  height: calc(100vh - 56px);
  display: flex;
  flex-direction: column;
}

.chat-layout {
  flex: 1;
  display: flex;
  gap: 12px;
  min-height: 0;
}

.chat-side {
  width: 250px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid var(--netops-border);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  padding: 12px;
  gap: 14px;
  min-height: 0;
}

.side-block.grow {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.side-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--netops-text-muted);
  margin-bottom: 8px;
}

.side-row {
  margin-top: 8px;
}

.conv-scroll {
  flex: 1;
  min-height: 0;
}

.conv-item {
  padding: 7px 8px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.conv-item:hover {
  background: var(--netops-sidebar-active);
}

.conv-item.active {
  background: var(--netops-sidebar-active);
  color: var(--el-color-primary);
  font-weight: 500;
}

.conv-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-del {
  opacity: 0;
  font-size: 13px;
}

.conv-item:hover .conv-del {
  opacity: 0.6;
}

.chat-main {
  flex: 1;
  background: #fff;
  border: 1px solid var(--netops-border);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.msg-scroll {
  flex: 1;
  padding: 18px 22px;
  min-height: 0;
}

.welcome {
  text-align: center;
  padding: 46px 20px;
  color: var(--netops-text-muted);
}

.welcome h3 {
  margin: 14px 0 6px;
  color: var(--netops-text);
}

.welcome p {
  margin: 0 0 22px;
  font-size: 13px;
}

.suggest {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 560px;
  margin: 0 auto;
}

.suggest-item {
  text-align: left;
  padding: 9px 14px;
  border: 1px solid var(--netops-border);
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.suggest-item:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: var(--netops-sidebar-active);
}

.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.msg-row.user {
  flex-direction: row-reverse;
}

.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--netops-sidebar-active);
  color: var(--el-color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 15px;
}

.msg-row.user .msg-avatar {
  background: var(--el-color-primary);
  color: #fff;
}

.msg-body {
  max-width: 78%;
}

.msg-row.user .msg-body {
  text-align: right;
}

.msg-content {
  display: inline-block;
  text-align: left;
  background: #f4f6f8;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 13.5px;
  line-height: 1.75;
  word-break: break-word;
}

.msg-row.user .msg-content {
  background: var(--el-color-primary);
  color: #fff;
}

html.dark .msg-content {
  background: #26272a;
}

.msg-row.user .msg-content {
  background: var(--el-color-primary);
}

.msg-meta {
  font-size: 11px;
  color: var(--netops-text-muted);
  margin-top: 4px;
}

.typing {
  color: var(--netops-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.md-pre {
  background: #1f2937;
  color: #e5e7eb;
  padding: 10px 12px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 12px;
  margin: 8px 0;
}

.md-code {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}

.msg-row.user .md-code {
  background: rgba(255, 255, 255, 0.22);
}

.msg-content ul {
  padding-left: 20px;
  margin: 6px 0;
}

.msg-content h4 {
  margin: 8px 0 4px;
  font-size: 13.5px;
}

.md-h3 {
  margin: 10px 0 6px;
  font-size: 14px;
}

.md-h2 {
  margin: 12px 0 6px;
  font-size: 15px;
}

.input-bar {
  display: flex;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid var(--netops-border);
  align-items: flex-end;
}

.input-bar .el-textarea {
  flex: 1;
}

.ctx-box {
  background: #f8fafc;
  border: 1px solid var(--netops-border);
  border-radius: 8px;
  padding: 14px;
  max-height: 520px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.7;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

html.dark .ctx-box {
  background: #17181a;
}
</style>
