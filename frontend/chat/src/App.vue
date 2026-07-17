<template>
  <el-container class="app-container">
    <!-- 顶部栏 -->
    <el-header class="app-header">
      <div class="header-left">
        <h2>制造业生产运维助手</h2>
      </div>
      <div class="header-right">
        <el-select v-model="currentRole" style="width: 140px" size="small">
          <el-option label="产线工人" value="worker_zhang" />
          <el-option label="维修工" value="maintainer_li" />
          <el-option label="班组长" value="shift_lead_wang" />
          <el-option label="车间主任" value="director_zhao" />
          <el-option label="工艺工程师" value="engineer_chen" />
          <el-option label="厂长" value="manager_zhou" />
        </el-select>
        <el-select v-model="currentWorkshop" style="width: 120px; margin-left: 8px" size="small">
          <el-option label="A车间" value="workshop-a" />
          <el-option label="B车间" value="workshop-b" />
        </el-select>
        <el-tag size="small" type="info" style="margin-left: 12px">
          {{ statusText }}
        </el-tag>
      </div>
    </el-header>

    <el-container class="main-area">
      <!-- 左侧对话区 -->
      <el-main class="chat-area">
        <div class="messages" ref="messagesContainer">
          <div v-if="messages.length === 0" class="welcome">
            <el-icon :size="48" color="#409EFF"><ChatDotRound /></el-icon>
            <p>你好！我是制造业生产运维助手。</p>
            <p>可以帮你查询设备手册、SOP、报警码，或者问故障处理建议。</p>
          </div>

          <div v-for="(msg, idx) in messages" :key="idx" :class="['message', msg.role]">
            <div class="message-avatar">
              <el-avatar v-if="msg.role === 'user'" :size="32" icon="UserFilled" />
              <el-avatar v-else :size="32" style="background: #409EFF">
                <el-icon><Cpu /></el-icon>
              </el-avatar>
            </div>
            <div class="message-content">
              <div class="message-text" v-html="formatMessage(msg.content)" />
              <div v-if="msg.citations && msg.citations.length" class="citations">
                <el-tag
                  v-for="(cit, ci) in msg.citations"
                  :key="ci"
                  size="small"
                  type="info"
                  style="margin-right: 6px; margin-top: 4px"
                >
                  📎 {{ cit.doc_title || cit.chunk_id }}
                </el-tag>
              </div>
              <div v-if="msg.model" class="message-meta">
                {{ msg.model }} · {{ msg.time }}
              </div>
            </div>
          </div>

          <div v-if="streaming" class="message assistant">
            <div class="message-avatar">
              <el-avatar :size="32" style="background: #409EFF">
                <el-icon><Cpu /></el-icon>
              </el-avatar>
            </div>
            <div class="message-content">
              <div v-if="streamingText" class="message-text">{{ streamingText }}</div>
              <div v-else class="thinking-text">
                <span class="thinking-dots">智能运维助手正在思考中</span>
                <span class="dot-anim">...</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="input-area">
          <div class="quick-actions">
            <el-button size="small" @click="quickAction('查SOP')">📖 查SOP</el-button>
            <el-button size="small" @click="quickAction('查报警码')">⚠ 查报警码</el-button>
            <el-button size="small" @click="quickAction('故障处理')">🔧 故障处理</el-button>
            <el-button size="small" @click="quickAction('设备点检')">✅ 设备点检</el-button>
            <el-button size="small" @click="quickAction('换模步骤')">🔄 换模步骤</el-button>
          </div>
          <div class="input-row">
            <el-input
              v-model="inputText"
              placeholder="输入问题，如：料筒温度异常怎么处理..."
              @keyup.enter="sendMessage"
              :disabled="streaming"
              size="large"
              clearable
            />
            <el-button
              type="primary"
              size="large"
              @click="sendMessage"
              :loading="streaming"
              :disabled="!inputText.trim()"
              style="margin-left: 8px"
            >
              <el-icon><Promotion /></el-icon>
              发送
            </el-button>
          </div>
        </div>
      </el-main>

      <!-- 右侧信息面板 -->
      <el-aside class="info-panel" width="320px">
        <el-card header="📋 当前状态" shadow="never">
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item label="角色">{{ roleLabel }}</el-descriptions-item>
            <el-descriptions-item label="车间">{{ currentWorkshop }}</el-descriptions-item>
            <el-descriptions-item label="会话ID">{{ sessionId?.slice(0, 12) || '未开始' }}</el-descriptions-item>
            <el-descriptions-item label="消息数">{{ messages.length }}</el-descriptions-item>
          </el-descriptions>
        </el-card>

        <el-card header="⚙ 快捷设备" shadow="never" style="margin-top: 12px">
          <el-tag
            v-for="eq in equipmentList"
            :key="eq"
            size="small"
            style="margin: 4px; cursor: pointer"
            @click="quickAction(eq)"
          >
            {{ eq }}
          </el-tag>
        </el-card>

        <el-card header="📖 最近引用" shadow="never" style="margin-top: 12px">
          <div v-if="recentCitations.length === 0" class="text-secondary">
            暂无引用记录
          </div>
          <div v-for="(cit, idx) in recentCitations" :key="idx" class="citation-item">
            <el-tag size="small" type="success">{{ cit.source }}</el-tag>
            {{ cit.doc_title || cit.chunk_id }}
          </div>
        </el-card>
      </el-aside>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, nextTick, watch } from 'vue'

// ── 状态 ──────────────────────────────────────
const currentRole = ref('worker_zhang')
const currentWorkshop = ref('workshop-a')
const inputText = ref('')
const messages = ref([])
const streaming = ref(false)
const streamingText = ref('')
const sessionId = ref('')
const statusText = computed(() => streaming.value ? 'AI思考中...' : '就绪')

const roleLabel = computed(() => {
  const labels = {
    worker_zhang: '产线工人',
    maintainer_li: '维修工',
    shift_lead_wang: '班组长',
    director_zhao: '车间主任',
    engineer_chen: '工艺工程师',
    manager_zhou: '厂长',
  }
  return labels[currentRole.value] || currentRole.value
})

const equipmentList = ['海天MA1200', 'FANUC CNC', '扬力冲压线', 'HA-003', 'HA-005']

const recentCitations = computed(() => {
  const all = []
  for (const msg of [...messages.value].reverse()) {
    if (msg.citations) {
      all.push(...msg.citations)
    }
    if (all.length >= 5) break
  }
  return all.slice(0, 5)
})

const messagesContainer = ref(null)

// ── 滚动到底部 ───────────────────────────────
async function scrollToBottom() {
  await nextTick()
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

// ── 发送消息 ──────────────────────────────────
async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || streaming.value) return
  inputText.value = ''

  // 添加用户消息
  messages.value.push({
    role: 'user',
    content: text,
    time: new Date().toLocaleTimeString(),
  })
  await scrollToBottom()

  // SSE 流式
  streaming.value = true
  streamingText.value = ''

  try {
    const response = await fetch('/api/v1/conversation/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        user_id: currentRole.value,
        workshop_id: currentWorkshop.value,
        session_id: sessionId.value || null,
      }),
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let assistantContent = ''
    let citations = []
    let modelUsed = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          continue // 下一行是 data
        }
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.type === 'text') {
              assistantContent += data.content
              streamingText.value = assistantContent
              await scrollToBottom()
            } else if (data.session_id) {
              // done event
              sessionId.value = data.session_id
              modelUsed = data.model_used
            } else if (data.chunk_id) {
              // citation event
              citations.push(data)
            }
          } catch {
            // 非 JSON 片段，忽略
          }
        }
      }
    }

    // 添加 AI 回复
    messages.value.push({
      role: 'assistant',
      content: assistantContent || '（无响应）',
      citations,
      model: modelUsed,
      time: new Date().toLocaleTimeString(),
    })

  } catch (error) {
    messages.value.push({
      role: 'assistant',
      content: `请求失败: ${error.message}`,
      time: new Date().toLocaleTimeString(),
    })
  } finally {
    streaming.value = false
    streamingText.value = ''
    await scrollToBottom()
  }
}

// ── 快捷指令 ──────────────────────────────────
function quickAction(action) {
  const prompts = {
    '查SOP': '请帮我查找SOP，关于',
    '查报警码': '请查一下报警码',
    '故障处理': '设备出现故障，',
    '设备点检': '设备每日点检需要检查哪些项目？',
    '换模步骤': '注塑机换模的具体步骤是什么？',
  }
  inputText.value = prompts[action] || `${action} 相关问题`
}

// ── 格式化消息 ────────────────────────────────
function formatMessage(text) {
  if (!text) return ''
  // 简单 Markdown 渲染
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/⚠️/g, '<span style="color: #E6A23C">⚠️</span>')
    .replace(/📎/g, '📎')
    .replace(/\n/g, '<br>')
    .replace(/步骤(\d+):/g, '<br><strong>步骤$1:</strong>')
  return html
}

// ── 新会话 ────────────────────────────────────
function newSession() {
  messages.value = []
  sessionId.value = ''
}
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

.app-container { height: 100vh; display: flex; flex-direction: column; }
.app-header {
  display: flex; align-items: center; justify-content: space-between;
  background: #409EFF; color: white; padding: 0 20px; height: 56px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}
.app-header h2 { font-size: 18px; font-weight: 600; }
.header-right { display: flex; align-items: center; }

.main-area { flex: 1; overflow: hidden; }

/* 对话区 */
.chat-area {
  display: flex; flex-direction: column;
  background: #f5f7fa; padding: 0;
}
.messages {
  flex: 1; overflow-y: auto; padding: 20px;
}
.welcome {
  text-align: center; padding: 60px 20px; color: #909399;
}
.welcome p { margin-top: 16px; font-size: 16px; }

.message { display: flex; margin-bottom: 20px; }
.message.user { flex-direction: row-reverse; }
.message-avatar { flex-shrink: 0; }
.message.user .message-avatar { margin-left: 10px; }
.message.assistant .message-avatar { margin-right: 10px; }

.message-content { max-width: 70%; }
.message.user .message-content { text-align: right; }
.message-text {
  background: white; border-radius: 12px; padding: 12px 16px;
  line-height: 1.6; font-size: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.message.assistant .message-text { background: white; }
.message.user .message-text { background: #409EFF; color: white; }
.message-meta { font-size: 11px; color: #909399; margin-top: 4px; }

/* 思考中提示 */
.thinking-text {
  color: #909399; font-size: 14px; padding: 8px 0;
  display: flex; align-items: center; gap: 2px;
}
.dot-anim {
  display: inline-block; width: 18px; text-align: left;
  animation: dotPulse 1.4s infinite;
}
@keyframes dotPulse {
  0%, 20%   { opacity: 0; }
  50%       { opacity: 1; }
  80%, 100% { opacity: 0; }
}

/* 输入区 */
.input-area {
  padding: 12px 20px 16px; background: white;
  border-top: 1px solid #e4e7ed;
}
.quick-actions { margin-bottom: 10px; display: flex; gap: 6px; flex-wrap: wrap; }
.input-row { display: flex; align-items: center; }

/* 信息面板 */
.info-panel {
  background: white; border-left: 1px solid #e4e7ed;
  padding: 16px; overflow-y: auto;
}
.info-panel .el-card { margin-bottom: 12px; }
.text-secondary { color: #909399; font-size: 13px; }
.citation-item { margin: 6px 0; font-size: 12px; }

@media (max-width: 768px) {
  .info-panel { display: none; }
  .message-content { max-width: 85%; }
}
</style>
