<template>
  <div class="chat-view">
    <!-- 左侧对话列表 -->
    <div class="chat-sidebar-wrapper">
      <ChatSidebar
        :conversations="chatStore.conversations"
        :active-id="chatStore.currentConversationId"
        :loading="chatStore.isLoading"
        @select="handleSelectConversation"
        @new-chat="handleNewChat"
        @refresh="chatStore.loadConversations()"
      />
    </div>

    <!-- 右侧聊天区域 -->
    <div class="chat-main">
      <!-- AI 免责声明 -->
      <ChatDisclaimer />

      <!-- 空状态 -->
      <div v-if="!chatStore.currentConversationId && chatStore.currentMessages.length === 0" class="chat-welcome">
        <div class="welcome-content">
          <el-icon :size="64" color="#2b6cb0"><ScaleToOriginal /></el-icon>
          <h2>法律智能助手</h2>
          <p>我是您的专业法律AI助手，可以为您提供以下帮助：</p>
          <div class="feature-list">
            <div class="feature-item">
              <el-icon><ChatDotRound /></el-icon>
              <span>法律问题咨询与解答</span>
            </div>
            <div class="feature-item">
              <el-icon><Document /></el-icon>
              <span>合同条款审查与风险分析</span>
            </div>
            <div class="feature-item">
              <el-icon><EditPen /></el-icon>
              <span>法律文书起草与生成</span>
            </div>
            <div class="feature-item">
              <el-icon><Search /></el-icon>
              <span>法律法规条文检索</span>
            </div>
            <div class="feature-item">
              <el-icon><Cpu /></el-icon>
              <span>深度推理与思维链分析</span>
            </div>
            <div class="feature-item">
              <el-icon><Connection /></el-icon>
              <span>外部工具调用（企业/计算器）</span>
            </div>
          </div>
          <div class="quick-questions">
            <p class="quick-title">快速提问</p>
            <el-tag
              v-for="q in quickQuestions"
              :key="q"
              class="quick-tag"
              @click="handleQuickQuestion(q)"
            >
              {{ q }}
            </el-tag>
          </div>
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-else class="message-area">
        <div
          v-for="msg in chatStore.currentMessages"
          :key="msg.id"
        >
          <ChatMessage
            :message="msg"
            :show-feedback="!msg.isStreaming"
            :follow-ups="msg.followUps || []"
            @feedback="handleFeedback"
            @follow-up="handleFollowUp"
          />
        </div>

        <!-- 发送中指示器 -->
        <div v-if="chatStore.isSending && !hasStreamingMessage" class="typing-indicator">
          <el-avatar :size="36" class="ai-avatar">
            <el-icon :size="18"><ScaleToOriginal /></el-icon>
          </el-avatar>
          <div class="typing-dots">
            <span></span><span></span><span></span>
          </div>
        </div>

        <div ref="messageEndRef" />
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <div class="input-wrapper">

          <!-- 已上传文件列表 -->
          <div v-if="uploadedFiles.length > 0" class="uploaded-files-bar">
            <div
              v-for="(file, idx) in uploadedFiles"
              :key="idx"
              class="uploaded-file-tag"
            >
              <el-icon :size="14"><Document /></el-icon>
              <span class="file-name">{{ file.name }}</span>
              <el-icon class="file-remove" :size="14" @click="removeFile(idx)"><Close /></el-icon>
            </div>
          </div>

          <!-- 功能工具栏：深度思考 / 联网搜索 / 文件上传 / 语音 / MCP工具 / 技能包 -->
          <div class="toolbar-row">
            <!-- 深度思考 -->
            <button
              :class="['toolbar-btn', { active: enableDeepThink }]"
              @click="enableDeepThink = !enableDeepThink"
              title="深度思考 — 使用 IRAC 法律推理框架逐步分析"
            >
              <el-icon :size="16"><Cpu /></el-icon>
              <span class="toolbar-label">深度思考</span>
              <span v-if="enableDeepThink" class="toolbar-dot"></span>
            </button>

            <!-- 联网搜索 -->
            <button
              :class="['toolbar-btn', { active: enableWebSearch }]"
              @click="enableWebSearch = !enableWebSearch"
              title="联网搜索 — 实时检索互联网法律信息"
            >
              <el-icon :size="16"><Search /></el-icon>
              <span class="toolbar-label">联网搜索</span>
              <span v-if="enableWebSearch" class="toolbar-dot"></span>
            </button>

            <!-- 文件上传 -->
            <button
              class="toolbar-btn"
              @click="triggerFileUpload"
              title="上传文件 — 支持 PDF / DOCX / DOC / TXT / Markdown"
            >
              <el-icon :size="16"><UploadFilled /></el-icon>
              <span class="toolbar-label">上传文件</span>
            </button>

            <!-- 语音输入 -->
            <button
              :class="['toolbar-btn', { active: isRecording }]"
              @click="toggleVoiceInput"
              :title="isRecording ? '停止录音' : '语音输入'"
            >
              <el-icon :size="16"><Microphone /></el-icon>
              <span class="toolbar-label">{{ isRecording ? '录音中...' : '语音' }}</span>
            </button>

            <!-- MCP工具 -->
            <el-popover
              :visible="mcpToolsPopoverVisible"
              placement="top-start"
              :width="320"
              trigger="manual"
              popper-class="mcp-popover-popper"
            >
              <template #reference>
                <button
                  :class="['toolbar-btn', { active: selectedMcpTools.length > 0 }]"
                  @click.stop="mcpToolsPopoverVisible = !mcpToolsPopoverVisible"
                  title="MCP外部工具调用"
                >
                  <el-icon :size="16"><SetUp /></el-icon>
                  <span class="toolbar-label">MCP工具</span>
                  <el-tag v-if="selectedMcpTools.length > 0" size="small" round type="primary" style="margin-left:4px">
                    {{ selectedMcpTools.length }}
                  </el-tag>
                </button>
              </template>
              <div class="mcp-tools-popover" @click.stop>
                <h4>MCP 外部工具</h4>
                <div v-if="mcpToolsLoading" class="mcp-loading">加载中...</div>
                <div v-else-if="mcpToolsList.length === 0" class="mcp-empty">暂无可用工具</div>
                <div v-else class="mcp-tools-list">
                  <label
                    v-for="tool in mcpToolsList"
                    :key="tool.name"
                    class="mcp-tool-item"
                  >
                    <input
                      type="checkbox"
                      :value="tool.name"
                      v-model="selectedMcpTools"
                    />
                    <span class="mcp-tool-name">{{ tool.name }}</span>
                    <el-tag size="small" effect="plain" type="info">{{ tool.category }}</el-tag>
                  </label>
                </div>
              </div>
            </el-popover>

            <!-- 技能包 -->
            <el-popover
              :visible="skillsPopoverVisible"
              placement="top-start"
              :width="360"
              trigger="manual"
              popper-class="skills-popover-popper"
            >
              <template #reference>
                <button
                  :class="['toolbar-btn', { active: selectedSkill !== null }]"
                  @click.stop="skillsPopoverVisible = !skillsPopoverVisible"
                  title="法律技能包"
                >
                  <el-icon :size="16"><MagicStick /></el-icon>
                  <span class="toolbar-label">技能包</span>
                </button>
              </template>
              <div class="skills-popover" @click.stop>
                <h4>法律技能包</h4>
                <div v-if="skillsLoading" class="mcp-loading">加载中...</div>
                <div v-else-if="skillsList.length === 0" class="mcp-empty">暂无可用技能</div>
                <div v-else class="mcp-tools-list">
                  <div
                    v-for="skill in skillsList"
                    :key="skill.id"
                    :class="['mcp-tool-item', 'skill-selectable', { selected: selectedSkill?.id === skill.id }]"
                    @click.stop="handleSelectSkill(skill)"
                  >
                    <span class="mcp-tool-name">{{ skill.name }}</span>
                    <el-tag size="small" effect="plain" type="info">{{ skill.category }}</el-tag>
                  </div>
                </div>
              </div>
            </el-popover>
          </div>

          <!-- 隐藏的 file input -->
          <input
            ref="fileInputRef"
            type="file"
            multiple
            accept=".pdf,.docx,.doc,.txt,.md,.markdown"
            style="display:none"
            @change="handleFileSelect"
          />

          <el-input
            v-model="inputText"
            type="textarea"
            :rows="3"
            placeholder="请输入您的问题，按 Enter 发送，Shift+Enter 换行..."
            resize="none"
            :disabled="chatStore.isSending"
            @keydown.enter.exact="handleSend"
          />
          <div class="input-actions">
            <span class="input-hint">
              <span v-if="enableDeepThink" class="mode-badge deep">🧠 深度思考</span>
              <span v-if="enableWebSearch" class="mode-badge search">🔍 联网搜索</span>
              <span v-if="uploadedFiles.length > 0" class="mode-badge file">📎 {{ uploadedFiles.length }} 个文件</span>
              <span v-if="selectedMcpTools.length > 0" class="mode-badge mcp">🔧 {{ selectedMcpTools.length }} 个工具</span>
              <span v-if="selectedSkill" class="mode-badge skill">✨ {{ selectedSkill.name }}</span>
              Enter 发送 / Shift+Enter 换行
            </span>
            <el-button
              type="primary"
              :icon="Promotion"
              :loading="chatStore.isSending"
              :disabled="!inputText.trim() && uploadedFiles.length === 0"
              @click="handleSend"
            >
              发送
            </el-button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, Close } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ChatMessage from '@/components/ChatMessage.vue'
import ChatDisclaimer from '@/components/ChatDisclaimer.vue'
import { mcpApi, skillsApi } from '@/api'

interface McpTool {
  name: string
  description: string
  category: string
}
interface SkillItem {
  id: string
  name: string
  category: string
  description: string
}

const chatStore = useChatStore()

const inputText = ref('')
const messageEndRef = ref<HTMLElement | null>(null)

// ── 功能开关状态 ──
const enableDeepThink = ref(false)
const enableWebSearch = ref(false)

// ── 文件上传 ──
const uploadedFiles = ref<File[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)

// ── 语音输入 ──
const isRecording = ref(false)

// ── MCP 工具 ──
const mcpToolsPopoverVisible = ref(false)
const mcpToolsLoading = ref(false)
const mcpToolsList = ref<McpTool[]>([])
const selectedMcpTools = ref<string[]>([])

// ── 技能包 ──
const skillsPopoverVisible = ref(false)
const skillsLoading = ref(false)
const skillsList = ref<SkillItem[]>([])
const selectedSkill = ref<SkillItem | null>(null)

const hasStreamingMessage = computed(() =>
  chatStore.currentMessages.some(m => m.isStreaming)
)

const quickQuestions = [
  '劳动合同到期不续签，公司需要赔偿吗？',
  '如何起草一份有效的借条？',
  '公司股权转让需要注意什么？',
  '离婚财产如何分割？',
]

onMounted(() => {
  chatStore.loadConversations()
  loadMcpTools()
  loadSkills()

  // Close popovers when clicking outside
  document.addEventListener('click', handleGlobalClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleGlobalClick)
})

function handleGlobalClick(e: MouseEvent) {
  const target = e.target as HTMLElement
  // Don't close if click is inside a popover or on a toolbar button
  if (target.closest('.mcp-popover-popper') || target.closest('.skills-popover-popper')) return
  if (target.closest('.toolbar-btn')) return
  mcpToolsPopoverVisible.value = false
  skillsPopoverVisible.value = false
}

// ── 加载 MCP 工具列表 ──
async function loadMcpTools() {
  mcpToolsLoading.value = true
  try {
    const res = await mcpApi.listTools()
    mcpToolsList.value = (res.data.tools || []).map((t: any) => ({
      name: t.name,
      description: t.description || '',
      category: t.category || 'utility',
    }))
  } catch {
    console.warn('加载 MCP 工具列表失败')
  } finally {
    mcpToolsLoading.value = false
  }
}

// ── 加载技能包列表 ──
async function loadSkills() {
  skillsLoading.value = true
  try {
    const res = await skillsApi.listSkills()
    skillsList.value = (res.data.skills || []).map((s: any) => ({
      id: s.id,
      name: s.name,
      category: s.category || 'general',
      description: s.description || '',
    }))
  } catch {
    console.warn('加载技能包列表失败')
  } finally {
    skillsLoading.value = false
  }
}

function handleSelectSkill(skill: SkillItem) {
  if (selectedSkill.value?.id === skill.id) {
    selectedSkill.value = null
  } else {
    selectedSkill.value = skill
  }
  skillsPopoverVisible.value = false
}

// ── 文件上传 ──
function triggerFileUpload() {
  fileInputRef.value?.click()
}

function handleFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files) {
    const allowedExts = ['.pdf', '.docx', '.doc', '.txt', '.md', '.markdown']
    for (const file of Array.from(input.files)) {
      const ext = '.' + file.name.split('.').pop()?.toLowerCase()
      if (!allowedExts.includes(ext)) {
        ElMessage.warning(`不支持的文件格式: ${file.name}，仅支持 PDF/DOCX/DOC/TXT/Markdown`)
        continue
      }
      if (file.size > 50 * 1024 * 1024) {
        ElMessage.warning(`文件 ${file.name} 超过50MB限制`)
        continue
      }
      uploadedFiles.value.push(file)
    }
    // Reset input so same file can be selected again
    input.value = ''
  }
}

function removeFile(index: number) {
  uploadedFiles.value.splice(index, 1)
}

// ── 语音输入 ──
async function toggleVoiceInput() {
  if (isRecording.value) {
    // Stop recording
    isRecording.value = false
    return
  }

  // Check browser support for Web Speech API
  const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
  if (!SpeechRecognition) {
    ElMessage.warning('您的浏览器不支持语音识别功能，请使用 Chrome 浏览器')
    return
  }

  isRecording.value = true
  const recognition = new SpeechRecognition()
  recognition.lang = 'zh-CN'
  recognition.interimResults = true
  recognition.continuous = false

  recognition.onresult = (event: any) => {
    let transcript = ''
    for (let i = event.resultIndex; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript
    }
    inputText.value += transcript
  }

  recognition.onend = () => {
    isRecording.value = false
  }

  recognition.onerror = (event: any) => {
    isRecording.value = false
    if (event.error !== 'aborted') {
      ElMessage.error('语音识别失败，请重试')
    }
  }

  recognition.start()
}

// 自动滚动到底部 - 监听消息内容变化（流式输出时）
watch(
  () => chatStore.currentMessages.map(m => m.content).join(''),
  async () => {
    await nextTick()
    messageEndRef.value?.scrollIntoView({ behavior: 'smooth' })
  }
)

// 也监听消息数量变化（新消息出现时）
watch(
  () => chatStore.currentMessages.length,
  async () => {
    await nextTick()
    messageEndRef.value?.scrollIntoView({ behavior: 'smooth' })
  }
)

function handleSelectConversation(id: string) {
  chatStore.loadMessages(id)
}

function handleNewChat() {
  chatStore.createNewConversation()
  inputText.value = ''
}

async function handleSend() {
  const text = inputText.value.trim()
  if ((!text && uploadedFiles.value.length === 0) || chatStore.isSending) return

  // 上传文件
  let fileMetadata: any[] = []
  if (uploadedFiles.value.length > 0) {
    for (const file of uploadedFiles.value) {
      const formData = new FormData()
      formData.append('file', file)
      try {
        const token = localStorage.getItem('token')
        const resp = await fetch('/api/v1/chat/upload', {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        })
        if (resp.ok) {
          const data = await resp.json()
          fileMetadata.push({
            filename: file.name,
            file_id: data.file_id || data.filename,
            content: data.extracted_text || '',
          })
        }
      } catch (err) {
        console.warn(`文件上传失败: ${file.name}`, err)
        fileMetadata.push({ filename: file.name, error: '上传失败' })
      }
    }
    uploadedFiles.value = []
  }

  inputText.value = ''
  try {
    await chatStore.sendMessage(text, undefined, {
      enable_deep_think: enableDeepThink.value,
      enable_web_search: enableWebSearch.value,
      files: fileMetadata.length > 0 ? fileMetadata : undefined,
      mcp_tools: selectedMcpTools.value.length > 0 ? selectedMcpTools.value : undefined,
      skill_id: selectedSkill.value?.id || undefined,
    })
  } catch (error) {
    ElMessage.error('发送失败，请检查网络连接后重试')
  }

  await nextTick()
  messageEndRef.value?.scrollIntoView({ behavior: 'smooth' })
}

function handleQuickQuestion(question: string) {
  inputText.value = question
  handleSend()
}

function handleFeedback(messageId: string, type: 'like' | 'dislike') {
  chatStore.setFeedback(messageId, type)
}

function handleFollowUp(question: string) {
  inputText.value = question
  handleSend()
}
</script>

<style scoped>
.chat-view {
  display: flex;
  height: calc(100vh - 60px);
}

.chat-sidebar-wrapper {
  width: 280px;
  flex-shrink: 0;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background-color: var(--bg-color);
}

.chat-welcome {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.welcome-content {
  text-align: center;
  max-width: 520px;
}

.welcome-content h2 {
  font-size: 24px;
  color: var(--primary-color);
  margin: 16px 0 8px;
}

.welcome-content p {
  color: var(--text-regular);
  margin-bottom: 24px;
}

.feature-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 32px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background-color: var(--bg-white);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}

.quick-questions {
  text-align: center;
}

.quick-title {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.quick-tag {
  margin: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.quick-tag:hover {
  background-color: var(--primary-color);
  color: #fff;
  border-color: var(--primary-color);
}

.message-area {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
}

.typing-indicator {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 0;
}

.ai-avatar {
  background-color: var(--primary-light) !important;
}

.typing-dots {
  display: flex;
  gap: 4px;
  padding: 12px 16px;
  background-color: var(--bg-white);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
}

.typing-dots span {
  width: 8px;
  height: 8px;
  background-color: var(--text-secondary);
  border-radius: 50%;
  animation: typing 1.4s infinite ease-in-out both;
}

.typing-dots span:nth-child(1) { animation-delay: -0.32s; }
.typing-dots span:nth-child(2) { animation-delay: -0.16s; }
.typing-dots span:nth-child(3) { animation-delay: 0s; }

@keyframes typing {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

.input-area {
  padding: 12px 24px 16px;
  background-color: var(--bg-white);
  border-top: 1px solid var(--border-color);
}

.input-wrapper {
  max-width: 900px;
  margin: 0 auto;
}

/* ── Toolbar Row ── */
.toolbar-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.toolbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  border: 1px solid var(--border-color, #dcdfe6);
  border-radius: 18px;
  background: transparent;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-regular, #606266);
  transition: all 0.15s ease;
  user-select: none;
  position: relative;
}

.toolbar-btn:hover {
  border-color: var(--primary-color, #409eff);
  color: var(--primary-color, #409eff);
  background-color: var(--primary-light-9, #ecf5ff);
}

.toolbar-btn.active {
  border-color: var(--primary-color, #409eff);
  color: var(--primary-color, #409eff);
  background-color: var(--primary-light-9, #ecf5ff);
  font-weight: 500;
}

.toolbar-label {
  font-size: 12px;
  line-height: 1;
}

.toolbar-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary-color, #409eff);
  margin-left: 2px;
}

/* ── Uploaded files bar ── */
.uploaded-files-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.uploaded-file-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: var(--primary-light-9, #ecf5ff);
  border: 1px solid var(--border-color, #dcdfe6);
  border-radius: 4px;
  font-size: 12px;
  color: var(--text-primary, #303133);
}

.file-name {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-remove {
  cursor: pointer;
  color: var(--text-secondary, #909399);
  transition: color 0.15s;
}

.file-remove:hover {
  color: #f56c6c;
}

/* ── Mode badges ── */
.mode-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  margin-right: 4px;
  font-weight: 500;
}

.mode-badge.deep {
  background: #f0f9eb;
  color: #67c23a;
}

.mode-badge.search {
  background: #ecf5ff;
  color: #409eff;
}

.mode-badge.file {
  background: #fdf6ec;
  color: #e6a23c;
}

.mode-badge.mcp {
  background: #f4ecff;
  color: #7c3aed;
}

.mode-badge.skill {
  background: #fef0f0;
  color: #f56c6c;
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.input-hint {
  font-size: 12px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
}

/* ── Popover panels ── */
.mcp-tools-popover h4,
.skills-popover h4 {
  margin: 0 0 10px;
  font-size: 14px;
  color: var(--primary-color);
}

.mcp-loading,
.mcp-empty {
  text-align: center;
  color: var(--text-secondary);
  padding: 16px 0;
  font-size: 13px;
}

.mcp-tools-list {
  max-height: 280px;
  overflow-y: auto;
}

.mcp-tool-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.mcp-tool-item:hover,
.mcp-tool-item.selected {
  background: var(--bg-color, #f5f7fa);
}

.mcp-tool-item input[type="checkbox"] {
  accent-color: var(--primary-color, #409eff);
}

.mcp-tool-name {
  flex: 1;
  font-size: 13px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
