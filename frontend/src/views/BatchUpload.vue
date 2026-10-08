<template>
  <div class="batch-upload">
    <div class="page-container">
      <h2 class="section-title">批量文档处理</h2>
      <p class="section-desc">上传多个文档文件，系统自动提取文本、生成摘要、分析关键要点</p>

      <!-- 上传区域 -->
      <el-card v-if="!batchId" shadow="never" class="upload-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">文件上传</span>
            <div class="mode-selector">
              <span class="mode-label">处理模式：</span>
              <el-radio-group v-model="processingMode" size="default">
                <el-radio-button value="extract">仅提取文本</el-radio-button>
                <el-radio-button value="summarize">提取 + 摘要</el-radio-button>
                <el-radio-button value="analyze">完整分析</el-radio-button>
              </el-radio-group>
            </div>
          </div>
        </template>

        <el-upload
          ref="uploadRef"
          v-model:file-list="fileList"
          class="batch-upload-area"
          drag
          multiple
          :auto-upload="false"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
          :on-exceed="handleExceed"
          :limit="20"
          accept=".pdf,.docx,.doc,.txt"
        >
          <el-icon class="el-icon--upload" :size="48"><UploadFilled /></el-icon>
          <div class="el-upload__text">
            将文件拖到此处，或<em>点击上传</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              支持 PDF、DOCX、TXT 格式，单次最多 20 个文件，总大小不超过 100MB
            </div>
          </template>
        </el-upload>

        <div class="upload-actions">
          <el-button
            type="primary"
            size="large"
            :disabled="fileList.length === 0"
            :loading="uploading"
            @click="handleUpload"
          >
            <el-icon><Upload /></el-icon>
            {{ uploading ? '上传中...' : '开始处理' }}
          </el-button>
          <el-button
            v-if="fileList.length > 0"
            size="large"
            @click="handleClear"
          >
            清空文件
          </el-button>
        </div>
      </el-card>

      <!-- 任务队列：整体进度 + 每个文件的状态机（排队/解析中/AI分析中/完成/失败） -->
      <el-card v-if="batchId" shadow="never" class="queue-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">任务队列</span>
            <div class="batch-actions">
              <el-tag :type="statusTagType" size="large">{{ statusLabel }}</el-tag>
              <el-button
                v-if="batchStatus && batchStatus.status === 'completed'"
                type="success"
                size="small"
                :icon="Download"
                @click="handleDownload"
              >
                下载全部结果
              </el-button>
              <el-button
                v-if="batchStatus && batchStatus.status === 'completed'"
                size="small"
                @click="handleReset"
              >
                重新上传
              </el-button>
            </div>
          </div>
        </template>

        <div class="progress-section">
          <el-progress
            :percentage="batchStatus?.progress_percent || 0"
            :status="progressStatus"
            :stroke-width="20"
            :text-inside="true"
          />
          <div class="progress-stats">
            <span>总计 {{ queueStats.total }}</span>
            <span class="stat-completed">已完成 {{ queueStats.completed }}</span>
            <span v-if="queueStats.active > 0" class="stat-active">处理中 {{ queueStats.active }}</span>
            <span v-if="queueStats.queued > 0" class="stat-queued">排队 {{ queueStats.queued }}</span>
            <span class="stat-failed" v-if="queueStats.failed > 0">
              失败 {{ queueStats.failed }}
            </span>
          </div>
        </div>

        <div class="queue-list">
          <div
            v-for="(result, index) in queueItems"
            :key="index"
            class="queue-item"
            :class="`queue-${fileState(result)}`"
          >
            <div
              class="queue-row"
              :class="{ clickable: isExpandable(result) }"
              @click="toggleExpand(index)"
            >
              <span class="queue-index">{{ index + 1 }}</span>
              <el-icon
                v-if="fileState(result) === 'parsing' || fileState(result) === 'analyzing'"
                :size="16"
                class="queue-state-icon is-loading state-active"
              ><Loading /></el-icon>
              <el-icon
                v-else-if="fileState(result) === 'completed'"
                :size="16"
                class="queue-state-icon state-completed"
              ><SuccessFilled /></el-icon>
              <el-icon
                v-else-if="fileState(result) === 'failed'"
                :size="16"
                class="queue-state-icon state-failed"
              ><CircleCloseFilled /></el-icon>
              <el-icon v-else :size="16" class="queue-state-icon state-queued"><Clock /></el-icon>
              <span class="queue-filename" :title="result.filename">{{ result.filename }}</span>

              <div class="queue-steps">
                <template v-for="(step, si) in queueSteps" :key="step">
                  <div class="step" :class="stepClass(result, si)">
                    <span class="step-dot" />
                  </div>
                  <span
                    v-if="si < queueSteps.length - 1"
                    class="step-line"
                    :class="{ done: si < stepIndexOf(result) }"
                  />
                </template>
              </div>

              <el-tag :type="stateTagTypes[fileState(result)]" size="small" effect="light">
                {{ stateLabels[fileState(result)] }}
              </el-tag>
              <span v-if="result.processing_time_seconds" class="queue-time">
                {{ result.processing_time_seconds }}s
              </span>
              <el-icon v-if="isExpandable(result)" class="queue-expand-icon">
                <ArrowDown v-if="expandedCards.includes(index)" />
                <ArrowRight v-else />
              </el-icon>
            </div>

            <div v-if="expandedCards.includes(index)" class="queue-detail">
              <el-alert
                v-if="result.error"
                :title="`处理失败: ${result.error}`"
                type="error"
                show-icon
                :closable="false"
                class="result-error"
              />

              <div v-if="result.text" class="result-block">
                <div class="block-label">
                  <el-icon><Document /></el-icon>
                  提取文本
                </div>
                <div class="block-content text-content">
                  {{ result.text.length > 2000 ? result.text.substring(0, 2000) + '...' : result.text }}
                </div>
                <el-button
                  v-if="result.text.length > 2000"
                  link
                  type="primary"
                  size="small"
                  @click.stop="showFullText(result)"
                >
                  查看完整文本
                </el-button>
              </div>

              <div v-if="result.summary" class="result-block">
                <div class="block-label">
                  <el-icon><Notebook /></el-icon>
                  文档摘要
                </div>
                <div class="block-content summary-content">{{ result.summary }}</div>
              </div>

              <div v-if="result.key_points" class="result-block">
                <div class="block-label">
                  <el-icon><Star /></el-icon>
                  关键要点
                </div>
                <div class="block-content keypoints-content">
                  <div v-for="(point, pi) in result.key_points.split('\n')" :key="pi" class="keypoint-item">
                    {{ point }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 处理历史 -->
      <el-card v-if="!batchId" shadow="never" class="history-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">处理历史</span>
          </div>
        </template>

        <el-table :data="historyDocuments" v-loading="historyLoading" stripe>
          <el-table-column prop="original_filename" label="文件名" min-width="200" />
          <el-table-column prop="file_type" label="类型" width="80" />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">
              {{ formatFileSize(row.file_size) }}
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="docStatusType(row.status)" size="small">
                {{ docStatusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="upload_time" label="上传时间" width="180">
            <template #default="{ row }">
              {{ formatTime(row.upload_time) }}
            </template>
          </el-table-column>
          <el-table-column prop="summary" label="摘要" min-width="200" show-overflow-tooltip />
        </el-table>

        <div class="history-pagination" v-if="historyTotal > historyPageSize">
          <el-pagination
            v-model:current-page="historyPage"
            :page-size="historyPageSize"
            :total="historyTotal"
            layout="prev, pager, next"
            @current-change="loadHistory"
          />
        </div>
      </el-card>
    </div>

    <!-- 全文查看对话框 -->
    <el-dialog
      v-model="fullTextDialogVisible"
      title="完整文本"
      width="70%"
      destroy-on-close
    >
      <div class="full-text-content">{{ fullTextContent }}</div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  UploadFilled,
  Upload,
  Download,
  SuccessFilled,
  CircleCloseFilled,
  Loading,
  Clock,
  ArrowDown,
  ArrowRight,
  Document,
  Notebook,
  Star,
} from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance } from 'element-plus'
import {
  batchApi,
  type BatchStatusResponse,
  type FileResult,
  type DocumentItem,
} from '@/api/batch'

// --- State ---
const uploadRef = ref<UploadInstance>()
const fileList = ref<UploadFile[]>([])
const processingMode = ref('analyze')
const uploading = ref(false)
const batchId = ref('')
const batchStatus = ref<BatchStatusResponse | null>(null)
const batchResults = ref<FileResult[]>([])
const expandedCards = ref<number[]>([])
const fullTextDialogVisible = ref(false)
const fullTextContent = ref('')

// History state
const historyDocuments = ref<DocumentItem[]>([])
const historyLoading = ref(false)
const historyPage = ref(1)
const historyPageSize = ref(20)
const historyTotal = ref(0)

// Polling
let pollTimer: ReturnType<typeof setInterval> | null = null

// --- Task queue state machine ---
// 后端逐文件状态：pending → parsing → analyzing（仅摘要/分析模式）→ completed / failed
type QueueState = 'queued' | 'parsing' | 'analyzing' | 'completed' | 'failed'

const stateLabels: Record<QueueState, string> = {
  queued: '排队中',
  parsing: '解析中',
  analyzing: 'AI 分析中',
  completed: '已完成',
  failed: '失败',
}

const stateTagTypes: Record<QueueState, string> = {
  queued: 'info',
  parsing: 'warning',
  analyzing: 'warning',
  completed: 'success',
  failed: 'danger',
}

function fileState(result: FileResult): QueueState {
  switch (result.status) {
    case 'completed': return 'completed'
    case 'failed': return 'failed'
    case 'parsing': return 'parsing'
    case 'analyzing': return 'analyzing'
    case 'processing': return 'parsing' // 兼容旧批次状态
    default: return 'queued'
  }
}

const queueItems = computed<FileResult[]>(() => {
  if (batchStatus.value?.results?.length) return batchStatus.value.results
  return batchResults.value
})

const queueSteps = computed<string[]>(() =>
  batchStatus.value?.mode === 'extract'
    ? ['排队', '解析', '完成']
    : ['排队', '解析', 'AI 分析', '完成'],
)

function stepIndexOf(result: FileResult): number {
  const state = fileState(result)
  const extract = batchStatus.value?.mode === 'extract'
  switch (state) {
    case 'queued': return 0
    case 'parsing': return 1
    case 'analyzing': return 2
    case 'completed':
    case 'failed': return extract ? 2 : 3
  }
}

function stepClass(result: FileResult, si: number): string {
  const state = fileState(result)
  const current = stepIndexOf(result)
  if (state === 'failed' && si === current) return 'step-failed'
  if (si < current) return 'step-done'
  if (si === current) return 'step-current'
  return ''
}

const queueStats = computed(() => {
  const stats = { total: 0, queued: 0, active: 0, completed: 0, failed: 0 }
  for (const it of queueItems.value) {
    const s = fileState(it)
    stats.total++
    if (s === 'completed') stats.completed++
    else if (s === 'failed') stats.failed++
    else if (s === 'queued') stats.queued++
    else stats.active++
  }
  return stats
})

function isExpandable(result: FileResult): boolean {
  const s = fileState(result)
  return s === 'completed' || s === 'failed'
}

function toggleExpand(index: number) {
  const i = expandedCards.value.indexOf(index)
  if (i >= 0) {
    expandedCards.value.splice(i, 1)
  } else {
    expandedCards.value.push(index)
  }
}

// --- Computed ---
const statusTagType = computed(() => {
  if (!batchStatus.value) return 'info'
  switch (batchStatus.value.status) {
    case 'completed': return 'success'
    case 'failed': return 'danger'
    case 'processing': return 'warning'
    default: return 'info'
  }
})

const statusLabel = computed(() => {
  if (!batchStatus.value) return ''
  switch (batchStatus.value.status) {
    case 'completed': return '处理完成'
    case 'failed': return '处理失败'
    case 'processing': return '处理中'
    default: return '等待中'
  }
})

const progressStatus = computed(() => {
  if (!batchStatus.value) return ''
  if (batchStatus.value.status === 'completed') return 'success'
  if (batchStatus.value.status === 'failed') return 'exception'
  return ''
})

// --- Methods ---
function handleFileChange(file: UploadFile, newFileList: UploadFile[]) {
  fileList.value = newFileList
}

function handleFileRemove(_file: UploadFile, newFileList: UploadFile[]) {
  fileList.value = newFileList
}

function handleExceed() {
  ElMessage.warning('单次最多上传 20 个文件')
}

function handleClear() {
  fileList.value = []
  uploadRef.value?.clearFiles()
}

async function handleUpload() {
  if (fileList.value.length === 0) return

  uploading.value = true
  try {
    const nativeFiles: File[] = []
    for (const f of fileList.value) {
      if (f.raw) {
        nativeFiles.push(f.raw)
      }
    }

    const res = await batchApi.uploadBatch(nativeFiles, processingMode.value)
    batchId.value = res.data.batch_id
    ElMessage.success(res.data.message)

    // Start polling for status
    startPolling()
  } catch {
    // Error handled by interceptor
  } finally {
    uploading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    if (!batchId.value) return
    try {
      const res = await batchApi.getBatchStatus(batchId.value)
      batchStatus.value = res.data

      if (res.data.status === 'completed' || res.data.status === 'failed') {
        stopPolling()
        // Fetch full results
        await fetchResults()
      }
    } catch {
      // Silently retry on next interval
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function fetchResults() {
  if (!batchId.value) return
  try {
    const res = await batchApi.getBatchResults(batchId.value)
    batchResults.value = res.data.results || []
    // 展开第一个已完成的条目，便于用户直接看到结果样例
    const firstCompleted = batchResults.value.findIndex((r) => r.status === 'completed')
    expandedCards.value = firstCompleted >= 0 ? [firstCompleted] : []
  } catch {
    // Error handled by interceptor
  }
}

async function handleDownload() {
  if (!batchId.value) return
  try {
    const res = await batchApi.downloadBatchResults(batchId.value)
    const blob = new Blob([res.data], { type: 'application/zip' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `batch_results_${batchId.value.substring(0, 8)}.zip`
    link.click()
    URL.revokeObjectURL(url)
    ElMessage.success('下载成功')
  } catch {
    // Error handled by interceptor
  }
}

function handleReset() {
  stopPolling()
  batchId.value = ''
  batchStatus.value = null
  batchResults.value = []
  expandedCards.value = []
  fileList.value = []
  uploadRef.value?.clearFiles()
  loadHistory()
}

function showFullText(result: FileResult) {
  fullTextContent.value = result.text || ''
  fullTextDialogVisible.value = true
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await batchApi.getDocumentHistory(historyPage.value, historyPageSize.value)
    historyDocuments.value = res.data.documents || []
    historyTotal.value = res.data.total
  } catch {
    // Error handled by interceptor
  } finally {
    historyLoading.value = false
  }
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatTime(timeStr: string): string {
  if (!timeStr) return ''
  try {
    const d = new Date(timeStr)
    return d.toLocaleString('zh-CN')
  } catch {
    return timeStr
  }
}

function docStatusType(s: string): string {
  switch (s) {
    case 'ready': return 'success'
    case 'failed': return 'danger'
    case 'processing': return 'warning'
    default: return 'info'
  }
}

function docStatusLabel(s: string): string {
  switch (s) {
    case 'uploaded': return '已上传'
    case 'processing': return '处理中'
    case 'ready': return '就绪'
    case 'failed': return '失败'
    default: return s
  }
}

// --- Lifecycle ---
onMounted(() => {
  loadHistory()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.batch-upload {
  height: 100%;
  overflow-y: auto;
}

.page-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px;
}

.section-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0 0 8px 0;
}

.section-desc {
  color: var(--text-secondary);
  margin-bottom: 24px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-color);
}

.mode-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}

.mode-label {
  font-size: 14px;
  color: var(--text-secondary);
}

.batch-upload-area {
  width: 100%;
}

.batch-upload-area :deep(.el-upload) {
  width: 100%;
}

.batch-upload-area :deep(.el-upload-dragger) {
  width: 100%;
  padding: 40px 20px;
}

.upload-actions {
  margin-top: 20px;
  display: flex;
  gap: 12px;
}

.queue-card {
  margin-bottom: 24px;
}

.progress-section {
  padding: 8px 0;
}

.progress-stats {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  font-size: 14px;
  color: var(--text-secondary);
}

.stat-completed {
  color: var(--el-color-success);
}

.stat-active {
  color: var(--el-color-warning);
}

.stat-queued {
  color: var(--text-secondary);
}

.stat-failed {
  color: var(--el-color-danger);
}

.batch-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.queue-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

.queue-item {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  transition: border-color 0.2s;
}

.queue-item.queue-completed {
  border-color: var(--el-color-success-light-7, #e1f3d8);
}

.queue-item.queue-failed {
  border-color: var(--el-color-danger-light-7, #fde2e2);
}

.queue-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg-white);
}

.queue-row.clickable {
  cursor: pointer;
}

.queue-row.clickable:hover {
  background: var(--bg-color);
}

.queue-index {
  font-size: 12px;
  color: var(--text-secondary);
  min-width: 20px;
  text-align: center;
  flex-shrink: 0;
}

.queue-state-icon {
  flex-shrink: 0;
}

.queue-state-icon.state-completed {
  color: var(--el-color-success);
}

.queue-state-icon.state-failed {
  color: var(--el-color-danger);
}

.queue-state-icon.state-active {
  color: var(--el-color-warning);
}

.queue-state-icon.state-queued {
  color: var(--text-secondary);
}

.queue-filename {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.queue-steps {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  margin-right: 4px;
}

.step {
  display: flex;
  align-items: center;
}

.step-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--border-color);
  transition: background 0.3s;
}

.step-done .step-dot {
  background: var(--el-color-success);
}

.step-current .step-dot {
  background: var(--el-color-warning);
  animation: step-pulse 1.5s ease-in-out infinite;
}

.step-failed .step-dot {
  background: var(--el-color-danger);
}

.step-line {
  width: 16px;
  height: 2px;
  background: var(--border-color);
  margin: 0 2px;
}

.step-line.done {
  background: var(--el-color-success);
}

@keyframes step-pulse {
  0%, 100% { box-shadow: 0 0 0 3px rgba(230, 162, 60, 0.25); }
  50% { box-shadow: 0 0 0 6px rgba(230, 162, 60, 0.08); }
}

.queue-time {
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
  min-width: 36px;
  text-align: right;
}

.queue-expand-icon {
  flex-shrink: 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.queue-detail {
  padding: 14px 16px;
  border-top: 1px solid var(--border-color);
  background: var(--bg-white);
}

.result-error {
  margin-bottom: 12px;
}

.result-block {
  margin-bottom: 16px;
}

.block-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.block-content {
  background: var(--bg-color);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-all;
}

.keypoint-item {
  padding: 4px 0;
  border-bottom: 1px dashed var(--border-color);
}

.keypoint-item:last-child {
  border-bottom: none;
}

.history-card {
  margin-bottom: 24px;
}

.history-pagination {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

.full-text-content {
  max-height: 60vh;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 14px;
  line-height: 1.8;
  padding: 16px;
  background: var(--bg-color);
  border-radius: var(--radius-md);
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .mode-selector {
    flex-wrap: wrap;
  }

  .queue-steps {
    display: none;
  }

  .queue-row {
    flex-wrap: wrap;
  }
}
</style>
