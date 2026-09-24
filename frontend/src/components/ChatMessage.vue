<template>
  <div :class="['chat-message', `chat-message--${message.role}`]">
    <div class="message-avatar">
      <el-avatar :size="36" v-if="message.role === 'user'">
        <el-icon :size="18"><UserFilled /></el-icon>
      </el-avatar>
      <el-avatar :size="36" v-else class="ai-avatar">
        <el-icon :size="18"><ScaleToOriginal /></el-icon>
      </el-avatar>
    </div>

    <div class="message-body">
      <div class="message-header">
        <span class="message-role">
          {{ message.role === 'user' ? '我' : '法律AI助手' }}
        </span>
        <el-tag v-if="message.role === 'assistant'" type="info" size="small" round class="ai-generated-badge">
          AI 生成
        </el-tag>
        <span class="message-time">{{ formatTime(message.created_at) }}</span>
        <span v-if="message.isStreaming" class="streaming-indicator">正在输入...</span>
      </div>

      <div class="message-content">
        <div v-if="message.role === 'assistant'" class="markdown-body" v-html="renderedContent" />
        <div v-else class="message-text">{{ message.content }}</div>
      </div>

      <!-- 功能标签：深度思考 / 联网搜索 / 附件 / MCP工具 -->
      <div
        v-if="message.role === 'assistant' && !message.isStreaming && featuresUsed"
        class="features-bar"
      >
        <span v-if="featuresUsed.deep_think" class="feature-tag deep-think">
          🧠 深度思考
        </span>
        <span v-if="featuresUsed.web_search" class="feature-tag web-search">
          🔍 联网搜索
        </span>
        <span v-if="featuresUsed.files > 0" class="feature-tag file-attach">
          📎 {{ featuresUsed.files }} 个附件
        </span>
        <span v-if="featuresUsed.mcp_tools > 0" class="feature-tag mcp-tool">
          🔧 {{ featuresUsed.mcp_tools }} 个工具
        </span>
      </div>

      <!-- 深度思考推理步骤面板 -->
      <!-- The backend computes IRAC reasoning steps for every deep-think turn and
           returns them in the SSE `meta` frame; the store already persists them
           on the message. Nothing rendered them, so choosing 深度思考 looked
           identical to a plain answer. -->
      <div
        v-if="message.role === 'assistant' && !message.isStreaming && reasoningSteps.length > 0"
        class="reasoning-panel"
      >
        <div class="reasoning-header" @click="stepsExpanded = !stepsExpanded">
          <span class="reasoning-icon">🧠</span>
          <span class="reasoning-title">推理过程（IRAC）</span>
          <el-tag size="small" type="info" round>{{ reasoningSteps.length }} 步</el-tag>
          <el-icon :size="12" :class="['citation-toggle', { expanded: stepsExpanded }]">
            <ArrowDown />
          </el-icon>
        </div>
        <transition name="citation-slide">
          <ol v-show="stepsExpanded" class="reasoning-list">
            <li v-for="(step, idx) in reasoningSteps" :key="idx" class="reasoning-step">
              <span class="reasoning-step-title">{{ step.title || step.step_type }}</span>
              <span v-if="step.content" class="reasoning-step-content">{{ step.content }}</span>
            </li>
          </ol>
        </transition>
      </div>

      <!-- 来源引用面板 -->
      <div
        v-if="message.role === 'assistant' && !message.isStreaming && citations.length > 0"
        class="citation-panel"
      >
        <div class="citation-header" @click="citationsExpanded = !citationsExpanded">
          <el-icon :size="14"><Document /></el-icon>
          <span class="citation-title">引用来源</span>
          <el-tag size="small" type="info" round>{{ citations.length }}</el-tag>
          <el-icon :size="12" :class="['citation-toggle', { expanded: citationsExpanded }]">
            <ArrowDown />
          </el-icon>
        </div>
        <transition name="citation-slide">
          <div v-show="citationsExpanded" class="citation-list">
            <div
              v-for="(cite, idx) in citations"
              :key="idx"
              :class="['citation-item', { 'citation-web': cite.source_type === 'web_search' }]"
              @click="handleCitationClick(cite)"
            >
              <span :class="['citation-index', { 'citation-index-web': cite.source_type === 'web_search' }]">
                {{ cite.source_type === 'web_search' ? '🌐' : idx + 1 }}
              </span>
              <div class="citation-detail">
                <span class="citation-law">{{ cite.law_name }}</span>
                <span v-if="cite.article_number" class="citation-article">
                  {{ cite.article_number }}
                </span>
                <span v-if="cite.content" class="citation-preview">
                  {{ truncateText(cite.content, 80) }}
                </span>
                <span v-if="cite.url" class="citation-url">
                  {{ truncateText(cite.url, 60) }}
                </span>
              </div>
              <span v-if="cite.score" class="citation-score">
                {{ (cite.score * 100).toFixed(0) }}%
              </span>
            </div>
          </div>
        </transition>
      </div>

      <!-- 上下文关联的继续追问按钮 -->
      <div
        v-if="message.role === 'assistant' && !message.isStreaming && followUps.length > 0"
        class="followup-section"
      >
        <div class="followup-header">
          <el-icon :size="14"><ChatDotRound /></el-icon>
          <span>继续追问</span>
        </div>
        <div class="followup-buttons">
          <button
            v-for="(q, idx) in followUps"
            :key="idx"
            type="button"
            class="followup-button"
            @click="handleFollowUpClick(q)"
          >
            <span class="followup-arrow">↪</span>
            <span class="followup-text">{{ q }}</span>
          </button>
        </div>
      </div>

      <div v-if="message.role === 'assistant' && !message.isStreaming && showFeedback" class="message-actions">
        <div class="message-actions-row">
          <button
            type="button"
            class="tts-button"
            :class="{ 'tts-button--loading': ttsLoading, 'tts-button--playing': ttsPlaying }"
            :disabled="ttsLoading"
            :title="ttsPlaying ? '暂停朗读' : ttsLoading ? '正在生成语音...' : '朗读'"
            @click="handleTtsToggle"
          >
            <span v-if="ttsLoading" class="tts-spinner" />
            <span v-else class="tts-icon">{{ ttsPlaying ? '⏸' : '🔊' }}</span>
          </button>
          <FeedbackButtons
            :feedback="message.feedback"
            :message-id="message.id"
            @feedback="handleFeedback"
          />
        </div>
      </div>

      <div v-if="message.role === 'assistant' && !message.isStreaming" class="disclaimer-text">
        🤖 本内容由 AI 生成，仅供法律参考，不构成正式法律建议
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onBeforeUnmount } from 'vue'
import { UserFilled, Document, ArrowDown, ChatDotRound } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import FeedbackButtons from './FeedbackButtons.vue'
import { chatApi } from '@/api/chat'
import type { Message } from '@/stores/chat'

const props = withDefaults(defineProps<{
  message: Message
  showFeedback?: boolean
  followUps?: string[]
}>(), {
  showFeedback: true,
  followUps: () => [],
})

const emit = defineEmits<{
  feedback: [messageId: string, type: 'like' | 'dislike']
  followUp: [question: string]
}>()

const citationsExpanded = ref(true)
const stepsExpanded = ref(false)

/** Extract citations from message metadata or content */
const citations = computed(() => {
  const raw = props.message.metadata?.citations
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.map((c: any) => ({
      law_name: c.law_name || c.law || '',
      article_number: c.article_number || c.article || '',
      content: c.content || '',
      score: c.score || c.relevance_score || 0,
      source_type: c.source_type || 'knowledge_base',
      url: c.url || '',
    })).filter((c: any) => c.law_name)
  }
  return []
})

/** Extract features_used from message metadata */
const featuresUsed = computed(() => {
  return props.message.metadata?.features_used || null
})

/** IRAC reasoning steps emitted by the deep-thinking agent (see chat.py meta frame) */
const reasoningSteps = computed(() => {
  const raw = props.message.metadata?.deep_think_steps
  return Array.isArray(raw) ? raw : []
})

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight: function (str: string, lang: string) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang }).value
      } catch {
        // fallback
      }
    }
    return ''
  },
})

/**
 * Clean up AI-generated markdown artifacts.
 * Removes excessive/decorative markdown that looks bad when rendered:
 * - Excessive ## headers without content
 * - Redundant ** bold markers on empty text
 * - Decorative --- separators
 * - Stray - list markers without content
 */
function cleanMarkdown(text: string): string {
  if (!text) return ''

  let cleaned = text

  // Remove empty headers: ## or ## followed by whitespace only on that line
  cleaned = cleaned.replace(/^#{1,6}\s*$/gm, '')

  // Remove empty bold: **** or ** **
  cleaned = cleaned.replace(/\*{2,4}\s*\*{0,2}/g, '')

  // Remove decorative horizontal rules (standalone --- or ***)
  cleaned = cleaned.replace(/^\s*[-*_]{3,}\s*$/gm, '')

  // Remove stray bullet markers on empty lines
  cleaned = cleaned.replace(/^\s*[-*+]\s*$/gm, '')

  // Remove excessive consecutive blank lines (keep max 1)
  cleaned = cleaned.replace(/\n{3,}/g, '\n\n')

  // Clean up the legal disclaimer section - make it more compact
  cleaned = cleaned.replace(
    /---\s*\n\s*\*{0,2}【法律声明】\*{0,2}\s*\n/g,
    '\n\n> **【法律声明】**\n> '
  )

  return cleaned.trim()
}

const renderedContent = computed(() => {
  const raw = props.message.content || ''
  const cleaned = cleanMarkdown(raw)
  return md.render(cleaned)
})

function formatTime(date: string): string {
  return dayjs(date).format('HH:mm')
}

function handleFeedback(type: 'like' | 'dislike') {
  emit('feedback', props.message.id, type)
}

function truncateText(text: string, maxLen: number): string {
  if (!text) return ''
  const cleaned = text.replace(/\s+/g, ' ').trim()
  return cleaned.length > maxLen ? cleaned.slice(0, maxLen) + '...' : cleaned
}

function handleCitationClick(cite: any) {
  // Future: open law article detail panel
  // For now, copy citation to clipboard
  const text = `《${cite.law_name}》${cite.article_number}`
  navigator.clipboard?.writeText(text)
}

function handleFollowUpClick(question: string) {
  emit('followUp', question)
}

// ── TTS (text-to-speech) playback ──
const ttsLoading = ref(false)
const ttsPlaying = ref(false)
let ttsAudio: HTMLAudioElement | null = null
let ttsObjectUrl: string | null = null

function stripHtml(html: string): string {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  return doc.body.textContent || ''
}

async function handleTtsToggle() {
  if (ttsPlaying && ttsAudio) {
    ttsAudio.pause()
    ttsPlaying.value = false
    return
  }

  if (ttsAudio && ttsAudio.paused && ttsObjectUrl) {
    ttsAudio.play()
    ttsPlaying.value = true
    return
  }

  ttsLoading.value = true
  try {
    const plainText = stripHtml(renderedContent.value) || props.message.content || ''
    const blob = await chatApi.ttsMessage(props.message.id, plainText)
    if (ttsObjectUrl) URL.revokeObjectURL(ttsObjectUrl)
    ttsObjectUrl = URL.createObjectURL(blob)
    ttsAudio = new Audio(ttsObjectUrl)
    ttsAudio.onended = () => {
      ttsPlaying.value = false
    }
    ttsAudio.onpause = () => {
      ttsPlaying.value = false
    }
    ttsAudio.onplay = () => {
      ttsPlaying.value = true
    }
    await ttsAudio.play()
    ttsPlaying.value = true
  } catch (err) {
    console.error('TTS playback failed:', err)
    ttsPlaying.value = false
  } finally {
    ttsLoading.value = false
  }
}

function stopTts() {
  if (ttsAudio) {
    ttsAudio.pause()
    ttsAudio.currentTime = 0
    ttsAudio = null
  }
  if (ttsObjectUrl) {
    URL.revokeObjectURL(ttsObjectUrl)
    ttsObjectUrl = null
  }
  ttsPlaying.value = false
  ttsLoading.value = false
}

onBeforeUnmount(() => {
  stopTts()
})
</script>

<style scoped>
.chat-message {
  display: flex;
  gap: 12px;
  padding: 16px 0;
  animation: slideIn 0.3s ease;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.chat-message--assistant {
  flex-direction: row;
}

.chat-message--user {
  flex-direction: row-reverse;
}

.message-avatar {
  flex-shrink: 0;
}

.ai-avatar {
  background-color: var(--primary-light) !important;
}

.message-body {
  max-width: 75%;
  min-width: 0;
}

.chat-message--user .message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.message-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.message-role {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular);
}

.message-time {
  font-size: 12px;
  color: var(--text-secondary);
}

.streaming-indicator {
  font-size: 12px;
  color: var(--primary-light);
  animation: blink 1.2s infinite;
}

.ai-generated-badge {
  font-size: 11px;
  padding: 0 6px;
  height: 20px;
  line-height: 18px;
  opacity: 0.75;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.message-content {
  padding: 12px 16px;
  border-radius: var(--radius-md);
  line-height: 1.65;
}

.chat-message--assistant .message-content {
  background-color: var(--bg-white);
  border: 1px solid var(--border-color);
}

.chat-message--user .message-content {
  background-color: var(--primary-light);
  color: #ffffff;
}

.chat-message--user .message-text {
  color: #ffffff;
}

.message-text {
  font-size: 14px;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.message-actions {
  margin-top: 8px;
  padding-left: 0;
}

.chat-message--user .message-actions {
  display: none;
}

/* ── Citation Panel ── */
.citation-panel {
  margin-top: 10px;
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: var(--radius-md, 8px);
  overflow: hidden;
  background: var(--bg-color, #f8f9fa);
}

/* 深度思考推理步骤 —— 与引用面板同一视觉语言 */
.reasoning-panel {
  margin-top: 10px;
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: var(--radius-md, 8px);
  overflow: hidden;
  background: var(--bg-color, #f8f9fa);
}

.reasoning-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary, #303133);
  transition: background-color 0.15s;
}

.reasoning-header:hover {
  background-color: var(--border-color, #e4e7ed);
}

.reasoning-icon {
  font-size: 14px;
}

.reasoning-title {
  flex: 1;
}

.reasoning-list {
  border-top: 1px solid var(--border-color, #e4e7ed);
  margin: 0;
  padding: 8px 12px 8px 30px;
}

.reasoning-step {
  padding: 4px 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular, #606266);
}

.reasoning-step-title {
  font-weight: 600;
  color: var(--text-primary, #303133);
  margin-right: 6px;
}

.reasoning-step-content {
  white-space: pre-wrap;
}

.citation-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary, #303133);
  transition: background-color 0.15s;
}

.citation-header:hover {
  background-color: var(--border-color, #e4e7ed);
}

.citation-title {
  flex: 1;
}

.citation-toggle {
  transition: transform 0.2s;
}

.citation-toggle.expanded {
  transform: rotate(180deg);
}

.citation-list {
  border-top: 1px solid var(--border-color, #e4e7ed);
}

.citation-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  transition: background-color 0.15s;
  border-bottom: 1px solid var(--border-light, #f0f0f0);
}

.citation-item:last-child {
  border-bottom: none;
}

.citation-item:hover {
  background-color: var(--bg-white, #fff);
}

.citation-index {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--primary-light, #409eff);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}

.citation-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.citation-law {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary, #303133);
}

.citation-article {
  font-size: 12px;
  color: var(--primary, #409eff);
  font-weight: 500;
}

.citation-preview {
  font-size: 12px;
  color: var(--text-secondary, #909399);
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.citation-score {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-secondary, #909399);
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}

/* Citation slide transition */
.citation-slide-enter-active,
.citation-slide-leave-active {
  transition: all 0.2s ease;
  overflow: hidden;
}

.citation-slide-enter-from,
.citation-slide-leave-to {
  opacity: 0;
  max-height: 0;
}

.citation-slide-enter-to,
.citation-slide-leave-from {
  opacity: 1;
  max-height: 500px;
}

.disclaimer-text {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-secondary, #909399);
  line-height: 1.4;
}

/* ── Features bar ── */
.features-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.feature-tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}

.feature-tag.deep-think {
  background: #f0f9eb;
  color: #67c23a;
}

.feature-tag.web-search {
  background: #ecf5ff;
  color: #409eff;
}

.feature-tag.file-attach {
  background: #fdf6ec;
  color: #e6a23c;
}

.feature-tag.mcp-tool {
  background: #f4ecff;
  color: #7c3aed;
}

/* ── Web search citation style ── */
.citation-item.citation-web {
  background: linear-gradient(to right, #ecf5ff20, transparent);
}

.citation-index-web {
  background: #409eff !important;
  font-size: 10px !important;
}

.citation-url {
  font-size: 11px;
  color: #409eff;
  line-height: 1.4;
  word-break: break-all;
}

/* ── Follow-up suggestions ── */
.followup-section {
  margin-top: 10px;
}

.followup-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular, #606266);
  margin-bottom: 8px;
}

.followup-header .el-icon {
  color: var(--primary, #409eff);
}

.followup-buttons {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.followup-button {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: var(--radius-md, 8px);
  background: var(--bg-color, #f8f9fa);
  cursor: pointer;
  text-align: left;
  font-size: 14px;
  line-height: 1.5;
  color: var(--text-primary, #303133);
  transition: all 0.15s ease;
}

.followup-button:hover {
  border-color: var(--primary, #409eff);
  background: var(--primary-light, #ecf5ff);
  color: var(--primary, #409eff);
}

.followup-button:active {
  transform: translateY(1px);
}

.followup-arrow {
  flex-shrink: 0;
  color: var(--primary, #409eff);
  font-weight: 600;
  line-height: 1.5;
}

.followup-text {
  flex: 1;
  min-width: 0;
}

/* ── TTS button ── */
.message-actions-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tts-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: 50%;
  background: var(--bg-color, #f8f9fa);
  cursor: pointer;
  transition: all 0.15s ease;
  padding: 0;
  flex-shrink: 0;
}

.tts-button:hover:not(:disabled) {
  border-color: var(--primary, #409eff);
  background: var(--primary-light, #ecf5ff);
  transform: scale(1.08);
}

.tts-button:active:not(:disabled) {
  transform: scale(0.95);
}

.tts-button:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.tts-button--playing {
  border-color: var(--primary, #409eff);
  background: var(--primary-light, #ecf5ff);
  animation: tts-pulse 1.5s infinite;
}

.tts-button--loading {
  border-color: var(--primary, #409eff);
}

.tts-icon {
  font-size: 14px;
  line-height: 1;
}

.tts-spinner {
  display: block;
  width: 14px;
  height: 14px;
  border: 2px solid var(--primary-light, #409eff);
  border-top-color: transparent;
  border-radius: 50%;
  animation: tts-spin 0.7s linear infinite;
}

@keyframes tts-spin {
  to { transform: rotate(360deg); }
}

@keyframes tts-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.3); }
  50% { box-shadow: 0 0 0 6px rgba(64, 158, 255, 0); }
}
</style>
