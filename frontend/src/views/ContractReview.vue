<template>
  <div class="contract-review">
    <div class="page-container">
      <h2 class="section-title">合同审查</h2>
      <p class="section-desc">上传合同文件，AI 将自动识别风险条款并提供修改建议</p>

      <!-- 上传区域 -->
      <div class="upload-section">
        <el-upload
          ref="uploadRef"
          class="upload-area"
          drag
          :auto-upload="false"
          :limit="1"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
          :file-list="fileList"
          accept=".pdf,.doc,.docx,.txt"
        >
          <el-icon class="upload-icon" :size="48"><UploadFilled /></el-icon>
          <div class="upload-text">
            <p>将合同文件拖拽到此处，或 <em>点击上传</em></p>
            <p class="upload-hint">支持 PDF、DOC、DOCX、TXT 格式，文件大小不超过 10MB</p>
          </div>
        </el-upload>

        <div class="upload-actions">
          <el-button
            type="primary"
            :icon="Upload"
            :loading="reviewing"
            :disabled="!selectedFile"
            size="large"
            @click="handleReview"
          >
            {{ reviewing ? '审查中...' : '开始审查' }}
          </el-button>
        </div>

        <!-- 审查进度：长文本需数分钟，必须给出可见进度，否则用户会以为卡死 -->
        <div v-if="reviewing" class="progress-block">
          <el-progress
            :percentage="progress"
            :stroke-width="10"
            :text-inside="true"
          />
          <p class="progress-text">{{ progressMessage || '正在处理，请稍候...' }}</p>
          <p class="progress-hint">
            完整审查需逐条分析合同条款，长文本可能需要数分钟，请勿关闭页面。
          </p>
        </div>
      </div>

      <!-- 审查结果 -->
      <div v-if="result" class="result-section">
        <!-- 总体评分 -->
        <div class="summary-card">
          <div class="score-area">
            <el-progress
              type="dashboard"
              :percentage="result.score"
              :color="scoreColor"
              :stroke-width="10"
            >
              <template #default="{ percentage }">
                <span class="score-value">{{ percentage }}分</span>
              </template>
            </el-progress>
            <div>
              <h3>合同风险评分</h3>
              <p class="summary-text">{{ result.summary }}</p>
            </div>
          </div>
        </div>

        <!-- 风险条目列表 -->
        <h3 class="section-title">风险条款详情</h3>
        <div class="risk-list">
          <div
            v-for="item in result.risk_items"
            :key="item.id"
            :class="['risk-card', `risk-${item.risk_level}`]"
          >
            <div class="risk-header">
              <div class="risk-level">
                <el-tag
                  :type="riskTagType(item.risk_level)"
                  size="small"
                  effect="dark"
                >
                  {{ riskLevelText(item.risk_level) }}
                </el-tag>
              </div>
              <span class="risk-clause">{{ item.clause }}</span>
            </div>

            <div class="risk-body">
              <div class="risk-original">
                <label>原文条款：</label>
                <p>{{ item.original_text }}</p>
              </div>
              <div class="risk-description">
                <label>风险分析：</label>
                <p>{{ item.description }}</p>
              </div>
              <div class="risk-suggestion">
                <label>修改建议：</label>
                <p>{{ item.suggestion }}</p>
              </div>
            </div>
          </div>
        </div>

        <el-empty v-if="result.risk_items.length === 0" description="未发现风险条款" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload, UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance } from 'element-plus'
import { contractApi } from '@/api/contract'

const uploadRef = ref<UploadInstance>()
const fileList = ref<UploadFile[]>([])
const selectedFile = ref<File | null>(null)
const reviewing = ref(false)
const result = ref<any>(null)
const progress = ref(0)
const progressMessage = ref('')

const scoreColor = computed(() => {
  const s = result.value?.score ?? 100
  if (s >= 80) return '#38a169'
  if (s >= 60) return '#dd6b20'
  return '#e53e3e'
})

function handleFileChange(file: UploadFile) {
  const MAX_SIZE = 10 * 1024 * 1024 // 10MB
  if (file.raw && file.raw.size > MAX_SIZE) {
    ElMessage.error('文件大小不能超过10MB')
    // Remove the file from the list
    if (uploadRef.value) {
      uploadRef.value.clearFiles()
    }
    return false
  }
  selectedFile.value = file.raw ?? null
  fileList.value = [file]
}

function handleFileRemove() {
  selectedFile.value = null
  fileList.value = []
}

function riskTagType(level: string) {
  const map: Record<string, string> = { high: 'danger', medium: 'warning', low: 'info' }
  return map[level] || 'info'
}

function riskLevelText(level: string) {
  const map: Record<string, string> = { high: '高风险', medium: '中风险', low: '低风险' }
  return map[level] || level
}

/** 轮询间隔与最长等待时长（与后端审查耗时匹配）。 */
const POLL_INTERVAL_MS = 2000
const MAX_WAIT_MS = 15 * 60 * 1000

/**
 * 把后端风险条目映射为展示结构。
 * 注意：同步接口返回 `category/clause/description`，异步任务结果里是 Agent 的
 * 原始字段 `risk_category/clause_text/risk_description`，两种都要兼容。
 */
function mapRiskItems(items: any[]) {
  return (items || []).map((r: any, idx: number) => ({
    id: idx,
    clause: r.risk_category || r.category || '未知条款',
    risk_level: r.risk_level || 'medium',
    description: r.risk_description || r.description || '',
    suggestion: r.suggestion || '',
    original_text: r.clause_text || r.clause || '',
  }))
}

async function handleReview() {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择合同文件')
    return
  }

  reviewing.value = true
  result.value = null
  progress.value = 0
  progressMessage.value = '正在上传合同文件...'

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)

    // 走异步接口：立即拿到 task_id，再由轮询驱动。
    // 同步接口对长合同会超过反向代理超时（120s）而被掐断成 504，
    // 前端只显示一句"请求失败"，用户无从判断原因。
    const res = await contractApi.reviewContractAsync(formData)
    const taskId = res.data?.task_id
    if (!taskId) throw new Error('提交失败：未获取到任务 ID')

    progressMessage.value = '任务已提交，正在排队处理...'
    const startedAt = Date.now()

    // 轮询任务状态（任务状态存于 Redis，多副本间共享，不会因负载均衡而丢失）
    while (true) {
      if (Date.now() - startedAt > MAX_WAIT_MS) {
        throw new Error('审查超时，请稍后在「审查历史」中查看结果')
      }
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))

      const t = (await contractApi.getTaskStatus(taskId)).data
      if (typeof t?.progress === 'number') progress.value = t.progress
      if (t?.progress_message) progressMessage.value = t.progress_message

      if (t?.status === 'completed') {
        const d = t.result || {}
        result.value = {
          score: d.risk_score ?? d.overall_score ?? 70,
          summary: d.summary || d.report || '合同审查完成',
          risk_items: mapRiskItems(d.risk_items || d.risks),
        }
        ElMessage.success('合同审查完成')
        return
      }

      if (t?.status === 'failed') {
        throw new Error(t?.error || '审查失败，请稍后重试')
      }
    }
  } catch (e: any) {
    // 带 response 的错误已由 axios 拦截器统一提示，此处只处理本地抛出的异常，
    // 避免同一错误弹两次。
    if (!e?.response) {
      ElMessage.error(e?.message || '合同审查失败，请稍后重试')
    }
  } finally {
    reviewing.value = false
    progress.value = 0
    progressMessage.value = ''
  }
}
</script>

<style scoped>
.contract-review {
  height: 100%;
  overflow-y: auto;
}

.page-container {
  max-width: 900px;
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

.upload-section {
  margin-bottom: 32px;
}

.upload-area {
  width: 100%;
}

.upload-area :deep(.el-upload-dragger) {
  padding: 40px;
  border-radius: var(--radius-md);
}

.upload-icon {
  color: var(--primary-light);
  margin-bottom: 12px;
}

.upload-text p {
  color: var(--text-regular);
  font-size: 15px;
  margin: 4px 0;
}

.upload-text em {
  color: var(--primary-light);
  font-style: normal;
  cursor: pointer;
}

.upload-hint {
  font-size: 13px !important;
  color: var(--text-secondary) !important;
}

.upload-actions {
  margin-top: 16px;
  text-align: center;
}

.progress-block {
  margin-top: 20px;
  padding: 16px 20px;
  background-color: var(--bg-white);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
}

.progress-text {
  margin: 10px 0 0;
  font-size: 14px;
  color: var(--text-regular);
  text-align: center;
}

.progress-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
}

/* 结果区域 */
.summary-card {
  background-color: var(--bg-white);
  border-radius: var(--radius-md);
  padding: 24px;
  margin-bottom: 32px;
  border: 1px solid var(--border-color);
}

.score-area {
  display: flex;
  align-items: center;
  gap: 32px;
}

.score-area h3 {
  font-size: 18px;
  color: var(--primary-color);
  margin: 0 0 8px 0;
}

.summary-text {
  color: var(--text-regular);
  font-size: 14px;
  margin: 0;
}

.risk-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.risk-card {
  background-color: var(--bg-white);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  border-left: 4px solid;
  overflow: hidden;
}

.risk-card.risk-high {
  border-left-color: var(--danger-color);
}

.risk-card.risk-medium {
  border-left-color: var(--warning-color);
}

.risk-card.risk-low {
  border-left-color: var(--success-color);
}

.risk-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  background-color: var(--bg-color);
  border-bottom: 1px solid var(--border-color);
}

.risk-clause {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.risk-body {
  padding: 16px 20px;
}

.risk-body > div {
  margin-bottom: 12px;
}

.risk-body > div:last-child {
  margin-bottom: 0;
}

.risk-body label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--primary-color);
  margin-bottom: 4px;
}

.risk-body p {
  font-size: 14px;
  color: var(--text-regular);
  line-height: 1.7;
  margin: 0;
}

.risk-original {
  background-color: var(--bg-color);
  padding: 12px;
  border-radius: var(--radius-sm);
}

.risk-suggestion p {
  color: var(--success-color);
  font-weight: 500;
}
</style>