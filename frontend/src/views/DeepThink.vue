<template>
  <div class="deep-think-view">
    <!-- Header -->
    <div class="think-header">
      <div class="header-info">
        <h2>
          <el-icon><Cpu /></el-icon>
          深度推理分析
        </h2>
        <p>基于 IRAC 法律推理框架，逐步分析复杂法律问题，展示完整思维链</p>
      </div>
      <div class="header-badges">
        <el-tag type="info" effect="plain">
          <el-icon><Monitor /></el-icon>
          思维链可视化
        </el-tag>
        <el-tag type="success" effect="plain">
          <el-icon><Connection /></el-icon>
          SSE 实时流式
        </el-tag>
      </div>
    </div>

    <!-- Input Section -->
    <div class="input-section">
      <el-input
        v-model="query"
        type="textarea"
        :rows="6"
        placeholder="请输入需要深度分析的法律问题...&#10;&#10;示例：&#10;• 公司未签劳动合同超过一年，员工可以主张哪些权利？&#10;• 房屋买卖中卖家隐瞒抵押信息，买家的法律救济途径有哪些？&#10;• 合同纠纷中，如何判断违约金是否过高？"
        resize="none"
        :disabled="isThinking"
        class="query-textarea"
      />
      <div class="input-actions">
        <div class="input-left">
          <el-select v-model="selectedModel" style="width: 220px" placeholder="推理模型">
            <el-option label="DeepSeek-R1 (推荐)" value="deepseek-reasoner" />
            <el-option label="DeepSeek-V3" value="deepseek-chat" />
          </el-select>
          <el-select v-model="analysisDepth" style="width: 150px" placeholder="分析深度">
            <el-option label="标准分析" value="standard" />
            <el-option label="深度分析" value="deep" />
            <el-option label="专家级分析" value="expert" />
          </el-select>
        </div>
        <el-button
          type="primary"
          size="large"
          :loading="isThinking"
          :disabled="!query.trim()"
          @click="startThinking"
          class="start-btn"
        >
          <el-icon v-if="!isThinking"><Cpu /></el-icon>
          {{ isThinking ? '深度分析中...' : '开始深度分析' }}
        </el-button>
      </div>
    </div>

    <!-- Reasoning Steps Progress -->
    <div v-if="isThinking || reasoningSteps.length > 0" class="thinking-progress">
      <h3 class="section-title">
        <el-icon><DataAnalysis /></el-icon>
        推理流程
      </h3>
      <el-steps :active="currentStep" finish-status="success" align-center class="reasoning-steps">
        <el-step title="问题分解" description="将问题拆解为子问题">
          <template #icon>
            <el-icon :class="{ 'is-loading': currentStep === 0 && isThinking }"><SetUp /></el-icon>
          </template>
        </el-step>
        <el-step title="法律争点识别" description="识别核心法律争议">
          <template #icon>
            <el-icon :class="{ 'is-loading': currentStep === 1 && isThinking }"><Search /></el-icon>
          </template>
        </el-step>
        <el-step title="法律依据检索" description="检索相关法律法规">
          <template #icon>
            <el-icon :class="{ 'is-loading': currentStep === 2 && isThinking }"><Document /></el-icon>
          </template>
        </el-step>
        <el-step title="多角度分析" description="原告/被告/法院视角">
          <template #icon>
            <el-icon :class="{ 'is-loading': currentStep === 3 && isThinking }"><View /></el-icon>
          </template>
        </el-step>
        <el-step title="风险评估" description="评估法律风险">
          <template #icon>
            <el-icon :class="{ 'is-loading': currentStep === 4 && isThinking }"><Warning /></el-icon>
          </template>
        </el-step>
        <el-step title="综合结论" description="生成最终分析报告">
          <template #icon>
            <el-icon :class="{ 'is-loading': currentStep === 5 && isThinking }"><DocumentChecked /></el-icon>
          </template>
        </el-step>
      </el-steps>
      <div v-if="statusMessage" class="status-message">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>{{ statusMessage }}</span>
      </div>
    </div>

    <!-- Reasoning Step Cards -->
    <div v-if="reasoningSteps.length > 0" class="reasoning-chain">
      <h3 class="section-title">
        <el-icon><Connection /></el-icon>
        推理过程（思维链）
      </h3>
      <div class="steps-grid">
        <el-card
          v-for="step in reasoningSteps"
          :key="step.step_id"
          shadow="hover"
          class="step-card"
          :class="'step-type-' + step.step_type"
        >
          <div class="step-header">
            <el-tag :type="getStepTagType(step.step_type)" size="small" effect="dark">
              {{ getStepLabel(step.step_type) }}
            </el-tag>
            <span class="step-title">{{ step.title }}</span>
            <el-tag
              v-if="step.confidence"
              :type="getConfidenceType(step.confidence)"
              size="small"
              class="confidence-badge"
            >
              置信度: {{ (step.confidence * 100).toFixed(0) }}%
            </el-tag>
          </div>
          <div class="step-content">{{ step.content }}</div>
          <!-- Show retrieved laws for legal basis step -->
          <div v-if="step.step_type === 'legal_basis' && step.sources && step.sources.length > 0" class="step-sources">
            <el-divider content-position="left">引用法条</el-divider>
            <div class="source-list">
              <el-tag
                v-for="(source, idx) in step.sources"
                :key="idx"
                type="info"
                effect="plain"
                size="small"
                class="source-tag"
              >
                {{ source }}
              </el-tag>
            </div>
          </div>
          <!-- Show multi-angle analysis -->
          <div v-if="step.step_type === 'multi_angle' && step.angles" class="angles-grid">
            <div v-for="(angle, key) in step.angles" :key="key" class="angle-item">
              <el-tag size="small" effect="plain">{{ getAngleLabel(String(key)) }}</el-tag>
              <p>{{ angle }}</p>
            </div>
          </div>
        </el-card>
      </div>
    </div>

    <!-- IRAC Analysis Panel -->
    <div v-if="iracData.issue || iracData.rule || iracData.application || iracData.conclusion" class="irac-panel">
      <h3 class="section-title">
        <el-icon><Grid /></el-icon>
        IRAC 结构化分析
      </h3>
      <el-collapse v-model="iracActivePanels" class="irac-collapse">
        <el-collapse-item name="issue">
          <template #title>
            <div class="irac-title">
              <el-icon color="#409EFF"><Search /></el-icon>
              <span>Issue（法律争点）</span>
            </div>
          </template>
          <div class="irac-content">
            <el-tag type="primary" effect="dark" size="small">争点识别</el-tag>
            <p>{{ iracData.issue || '正在分析...' }}</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="rule">
          <template #title>
            <div class="irac-title">
              <el-icon color="#67C23A"><Document /></el-icon>
              <span>Rule（法律规则）</span>
            </div>
          </template>
          <div class="irac-content">
            <el-tag type="success" effect="dark" size="small">适用规则</el-tag>
            <p>{{ iracData.rule || '正在检索...' }}</p>
            <div v-if="iracRuleCitations.length > 0" class="irac-citations">
              <el-divider content-position="left">引用法律依据</el-divider>
              <div class="citation-links">
                <a
                  v-for="(cite, idx) in iracRuleCitations"
                  :key="idx"
                  :href="cite.url || '#'"
                  target="_blank"
                  class="citation-link"
                >
                  <el-icon><Link /></el-icon>
                  {{ cite.text }}
                </a>
              </div>
            </div>
          </div>
        </el-collapse-item>
        <el-collapse-item name="application">
          <template #title>
            <div class="irac-title">
              <el-icon color="#E6A23C"><EditPen /></el-icon>
              <span>Application（规则适用）</span>
            </div>
          </template>
          <div class="irac-content">
            <el-tag type="warning" effect="dark" size="small">法律适用</el-tag>
            <p>{{ iracData.application || '正在分析...' }}</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="conclusion">
          <template #title>
            <div class="irac-title">
              <el-icon color="#F56C6C"><DocumentChecked /></el-icon>
              <span>Conclusion（结论）</span>
            </div>
          </template>
          <div class="irac-content">
            <el-tag type="danger" effect="dark" size="small">分析结论</el-tag>
            <p>{{ iracData.conclusion || '正在生成...' }}</p>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <!-- Confidence Indicator -->
    <div v-if="confidence > 0 || isThinking" class="confidence-section">
      <h3 class="section-title">
        <el-icon><TrendCharts /></el-icon>
        分析置信度
      </h3>
      <el-card shadow="hover" class="confidence-card">
        <div class="confidence-layout">
          <div class="confidence-gauge">
            <el-progress
              type="dashboard"
              :percentage="Math.round(confidence * 100)"
              :color="confidenceColors"
              :width="180"
              :stroke-width="12"
            >
              <template #default="{ percentage }">
                <span class="percentage-value">{{ percentage }}%</span>
                <span class="percentage-label">置信度</span>
              </template>
            </el-progress>
          </div>
          <div class="confidence-details">
            <div class="confidence-item">
              <span class="label">分析耗时</span>
              <span class="value">{{ elapsedSeconds }}s</span>
            </div>
            <div class="confidence-item">
              <span class="label">推理步骤</span>
              <span class="value">{{ reasoningSteps.length }}</span>
            </div>
            <div class="confidence-item">
              <span class="label">引用法条</span>
              <span class="value">{{ sourceCitations.length }}</span>
            </div>
            <div class="confidence-item">
              <span class="label">分析深度</span>
              <span class="value">{{ getDepthLabel(analysisDepth) }}</span>
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- Source Citations -->
    <div v-if="sourceCitations.length > 0" class="citations-section">
      <h3 class="section-title">
        <el-icon><Link /></el-icon>
        引用法律依据
      </h3>
      <el-card shadow="hover" class="citations-card">
        <div class="citations-grid">
          <div
            v-for="(citation, idx) in sourceCitations"
            :key="idx"
            class="citation-item"
          >
            <el-icon class="citation-icon"><Document /></el-icon>
            <div class="citation-info">
              <a
                :href="citation.url || '#'"
                target="_blank"
                class="citation-title"
              >
                {{ citation.title }}
              </a>
              <span class="citation-detail">{{ citation.detail }}</span>
            </div>
            <el-tag size="small" type="info">{{ citation.type }}</el-tag>
          </div>
        </div>
      </el-card>
    </div>

    <!-- Final Answer -->
    <div v-if="finalAnswer" class="final-answer">
      <h3 class="section-title">
        <el-icon><DocumentChecked /></el-icon>
        综合结论
      </h3>
      <el-card shadow="hover" class="answer-card">
        <div class="answer-meta">
          <el-tag type="primary">置信度: {{ (confidence * 100).toFixed(0) }}%</el-tag>
          <el-tag type="info">耗时: {{ elapsedSeconds }}s</el-tag>
          <el-tag type="success">推理步骤: {{ reasoningSteps.length }}</el-tag>
          <el-tag type="warning">引用法条: {{ sourceCitations.length }}</el-tag>
        </div>
        <div class="answer-content" v-html="renderMarkdown(finalAnswer)" />
      </el-card>
    </div>

    <!-- Verification Notes -->
    <div v-if="verificationNotes.length > 0" class="verification-section">
      <h3 class="section-title">
        <el-icon><CircleCheck /></el-icon>
        验证意见
      </h3>
      <el-alert
        v-for="(note, i) in verificationNotes"
        :key="i"
        :title="note"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 8px"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { reasoningApi } from '@/api'

interface ReasoningStep {
  step_id: number
  step_type: string
  title: string
  content: string
  confidence: number
  sources?: string[]
  angles?: Record<string, string>
}

interface SourceCitation {
  title: string
  detail: string
  url: string
  type: string
}

interface IracData {
  issue: string
  rule: string
  application: string
  conclusion: string
}

interface RuleCitation {
  text: string
  url: string
}

const query = ref('')
const selectedModel = ref('deepseek-reasoner')
const analysisDepth = ref('deep')
const isThinking = ref(false)
const currentStep = ref(0)
const statusMessage = ref('')
const reasoningSteps = ref<ReasoningStep[]>([])
const finalAnswer = ref('')
const confidence = ref(0)
const elapsedSeconds = ref(0)
const verificationNotes = ref<string[]>([])
const iracData = ref<IracData>({ issue: '', rule: '', application: '', conclusion: '' })
const iracActivePanels = ref(['issue', 'rule', 'application', 'conclusion'])
const sourceCitations = ref<SourceCitation[]>([])
const iracRuleCitations = ref<RuleCitation[]>([])

const confidenceColors = [
  { color: '#F56C6C', percentage: 30 },
  { color: '#E6A23C', percentage: 50 },
  { color: '#409EFF', percentage: 70 },
  { color: '#67C23A', percentage: 100 },
]

function getStepTagType(type: string): string {
  const map: Record<string, string> = {
    decomposition: '',
    issue_identification: 'primary',
    legal_basis: 'success',
    multi_angle: 'warning',
    risk_assessment: 'danger',
    conclusion: 'success',
    issue: 'primary',
    rule: 'success',
    application: 'warning',
    verification: 'danger',
  }
  return map[type] || 'info'
}

function getStepLabel(type: string): string {
  const map: Record<string, string> = {
    decomposition: '问题分解',
    issue_identification: '争点识别',
    legal_basis: '法律检索',
    multi_angle: '多角度分析',
    risk_assessment: '风险评估',
    conclusion: '综合结论',
    issue: '争点 Issue',
    rule: '规则 Rule',
    application: '适用 Application',
    verification: '验证 Verification',
  }
  return map[type] || type
}

function getConfidenceType(conf: number): string {
  if (conf >= 0.8) return 'success'
  if (conf >= 0.6) return 'primary'
  if (conf >= 0.4) return 'warning'
  return 'danger'
}

function getAngleLabel(key: string): string {
  const map: Record<string, string> = {
    plaintiff: '原告视角',
    defendant: '被告视角',
    court: '法院视角',
  }
  return map[key] || key
}

function getDepthLabel(depth: string): string {
  const map: Record<string, string> = {
    standard: '标准',
    deep: '深度',
    expert: '专家级',
  }
  return map[depth] || depth
}

function renderMarkdown(text: string): string {
  return text
    .replace(/### (.*)/g, '<h4>$1</h4>')
    .replace(/## (.*)/g, '<h3>$1</h3>')
    .replace(/# (.*)/g, '<h2>$1</h2>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>')
}

async function startThinking() {
  if (!query.value.trim()) return

  isThinking.value = true
  currentStep.value = 0
  statusMessage.value = '正在初始化深度分析引擎...'
  reasoningSteps.value = []
  finalAnswer.value = ''
  confidence.value = 0
  elapsedSeconds.value = 0
  verificationNotes.value = []
  iracData.value = { issue: '', rule: '', application: '', conclusion: '' }
  sourceCitations.value = []
  iracRuleCitations.value = []

  try {
    const response = await reasoningApi.deepThinkStream(query.value, {
      model_override: selectedModel.value,
      depth: analysisDepth.value,
    })
    if (!response.body) throw new Error('No response body')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const text = decoder.decode(value, { stream: true })
      const lines = text.split('\n')

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const event = JSON.parse(line.slice(6))
          handleStreamEvent(event)
        } catch {
          /* skip invalid JSON */
        }
      }
    }
  } catch (err) {
    // Fallback: use non-streaming API
    try {
      statusMessage.value = '正在执行深度推理（非流式模式）...'
      currentStep.value = 1
      const result = await reasoningApi.deepThink(query.value, {
        model_override: selectedModel.value,
        depth: analysisDepth.value,
      })
      const data = result.data

      reasoningSteps.value = data.reasoning_steps || []
      finalAnswer.value = data.final_answer || ''
      confidence.value = data.confidence || 0
      elapsedSeconds.value = data.elapsed_seconds || 0
      verificationNotes.value = data.verification_notes || []

      if (data.irac) {
        iracData.value = data.irac
      }
      if (data.source_citations) {
        sourceCitations.value = data.source_citations
      }

      currentStep.value = 6
    } catch (fallbackErr) {
      ElMessage.error('深度推理失败，请稍后重试')
      console.error(fallbackErr)
    }
  }

  isThinking.value = false
  statusMessage.value = ''
}

function handleStreamEvent(event: any) {
  switch (event.type) {
    case 'thinking_start':
      currentStep.value = 0
      statusMessage.value = event.message || '开始深度分析...'
      break

    case 'phase_start':
      switch (event.phase) {
        case 'decomposition':
          currentStep.value = 0
          break
        case 'issue_identification':
          currentStep.value = 1
          break
        case 'legal_retrieval':
          currentStep.value = 2
          break
        case 'multi_angle':
          currentStep.value = 3
          break
        case 'risk_assessment':
          currentStep.value = 4
          break
        case 'conclusion':
          currentStep.value = 5
          break
      }
      statusMessage.value = event.message || ''
      break

    case 'phase_complete':
      statusMessage.value = event.message || '阶段完成'
      break

    case 'reasoning_step':
      if (event.data) {
        const step: ReasoningStep = {
          step_id: reasoningSteps.value.length + 1,
          step_type: event.data.step_type || event.phase || 'unknown',
          title: event.data.title || '',
          content: event.data.content || '',
          confidence: event.data.confidence || 0.7,
          sources: event.data.sources,
          angles: event.data.angles,
        }
        reasoningSteps.value = [...reasoningSteps.value, step]
      }
      break

    case 'irac_update':
      if (event.data) {
        if (event.data.issue) iracData.value.issue = event.data.issue
        if (event.data.rule) iracData.value.rule = event.data.rule
        if (event.data.application) iracData.value.application = event.data.application
        if (event.data.conclusion) iracData.value.conclusion = event.data.conclusion
        if (event.data.citations) {
          iracRuleCitations.value = event.data.citations
        }
      }
      break

    case 'source_citation':
      if (event.data) {
        sourceCitations.value = [
          ...sourceCitations.value,
          {
            title: event.data.title || '',
            detail: event.data.detail || '',
            url: event.data.url || '',
            type: event.data.type || '法律',
          },
        ]
      }
      break

    case 'confidence_update':
      if (event.confidence !== undefined) {
        confidence.value = event.confidence
      }
      break

    case 'token':
      finalAnswer.value += event.content || ''
      break

    case 'thinking_complete':
      currentStep.value = 6
      finalAnswer.value = event.final_answer || finalAnswer.value
      confidence.value = event.confidence || confidence.value
      elapsedSeconds.value = event.elapsed_seconds || 0
      statusMessage.value = '深度分析完成'
      if (event.source_citations) {
        sourceCitations.value = event.source_citations
      }
      if (event.irac) {
        iracData.value = event.irac
      }
      break

    case 'error':
      ElMessage.error(event.error || '推理出错')
      isThinking.value = false
      break
  }
}
</script>

<style scoped>
.deep-think-view {
  padding: 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.think-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}

.header-info h2 {
  margin: 0 0 4px;
  color: var(--primary-color);
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 22px;
}

.header-info p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 14px;
}

.header-badges {
  display: flex;
  gap: 8px;
}

.input-section {
  background: var(--bg-white);
  border-radius: var(--radius-lg);
  padding: 24px;
  border: 1px solid var(--border-color);
  margin-bottom: 24px;
}

.query-textarea {
  margin-bottom: 16px;
}

.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.input-left {
  display: flex;
  gap: 12px;
}

.start-btn {
  min-width: 160px;
}

.thinking-progress {
  background: var(--bg-white);
  border-radius: var(--radius-lg);
  padding: 24px;
  border: 1px solid var(--border-color);
  margin-bottom: 24px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--primary-color);
  margin: 0 0 20px;
  font-size: 16px;
}

.reasoning-steps {
  margin-bottom: 16px;
}

.status-message {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  padding: 12px 16px;
  background: #f0f9ff;
  border-radius: 8px;
  color: var(--primary-color);
  font-size: 14px;
}

.reasoning-chain {
  margin-bottom: 24px;
}

.steps-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.step-card {
  border-left: 4px solid var(--border-color);
}

.step-type-decomposition {
  border-left-color: #909399;
}

.step-type-issue_identification {
  border-left-color: #409eff;
}

.step-type-legal_basis {
  border-left-color: #67c23a;
}

.step-type-multi_angle {
  border-left-color: #e6a23c;
}

.step-type-risk_assessment {
  border-left-color: #f56c6c;
}

.step-type-conclusion {
  border-left-color: #67c23a;
}

.step-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.step-title {
  font-weight: 600;
  font-size: 15px;
  flex: 1;
}

.confidence-badge {
  margin-left: auto;
}

.step-content {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.8;
  white-space: pre-wrap;
  padding: 8px 0;
}

.step-sources {
  margin-top: 12px;
}

.source-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.source-tag {
  cursor: pointer;
}

.angles-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.angle-item {
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}

.angle-item p {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
}

.irac-panel {
  margin-bottom: 24px;
}

.irac-collapse {
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
}

.irac-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
}

.irac-content {
  padding: 8px 0;
}

.irac-content p {
  margin: 12px 0 0;
  line-height: 1.8;
  font-size: 14px;
}

.irac-citations {
  margin-top: 16px;
}

.citation-links {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.citation-link {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--primary-color);
  text-decoration: none;
  font-size: 13px;
  padding: 6px 10px;
  background: #f0f9ff;
  border-radius: 4px;
  transition: background 0.2s;
}

.citation-link:hover {
  background: #e0f2fe;
}

.confidence-section {
  margin-bottom: 24px;
}

.confidence-card {
  padding: 8px;
}

.confidence-layout {
  display: flex;
  align-items: center;
  gap: 40px;
}

.confidence-gauge {
  flex-shrink: 0;
}

.percentage-value {
  display: block;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}

.percentage-label {
  display: block;
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.confidence-details {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  flex: 1;
}

.confidence-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 8px;
}

.confidence-item .label {
  font-size: 12px;
  color: var(--text-secondary);
}

.confidence-item .value {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.citations-section {
  margin-bottom: 24px;
}

.citations-card {
  padding: 8px;
}

.citations-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.citation-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 8px;
  transition: background 0.2s;
}

.citation-item:hover {
  background: #f0f2f5;
}

.citation-icon {
  color: var(--primary-color);
  font-size: 18px;
  flex-shrink: 0;
}

.citation-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.citation-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--primary-color);
  text-decoration: none;
}

.citation-title:hover {
  text-decoration: underline;
}

.citation-detail {
  font-size: 12px;
  color: var(--text-secondary);
}

.final-answer {
  margin-bottom: 24px;
}

.answer-card {
  padding: 8px;
}

.answer-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.answer-content {
  font-size: 15px;
  line-height: 1.8;
  color: var(--text-primary);
  padding: 8px;
}

.answer-content :deep(h2),
.answer-content :deep(h3),
.answer-content :deep(h4) {
  color: var(--primary-color);
  margin: 16px 0 8px;
}

.verification-section {
  margin-bottom: 24px;
}
</style>
