<template>
  <div class="litigation-view">
    <div class="view-header">
      <h2>诉讼支持</h2>
      <p>案件分析、证据清单、类案比对、庭审提纲、裁判预测、一键诉讼报告 — 一站式诉讼辅助</p>
    </div>

    <!-- 功能标签页 -->
    <el-tabs v-model="activeTab" type="border-card">
      <!-- 案件分析 -->
      <el-tab-pane label="案件分析" name="analyze">
        <el-form :model="analyzeForm" label-width="100px">
          <el-form-item label="案情描述" required>
            <el-input v-model="analyzeForm.case_description" type="textarea" :rows="4"
              placeholder="请详细描述案件经过、当事人信息、争议焦点..." />
          </el-form-item>
          <el-form-item label="现有证据">
            <el-input v-model="analyzeForm.evidence_list" type="textarea" :rows="2"
              placeholder="列出已有的证据材料..." />
          </el-form-item>
          <el-form-item label="诉讼请求">
            <el-input v-model="analyzeForm.claims" placeholder="希望达到的诉讼目标..." />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading.analyze" @click="analyzeCase">开始分析</el-button>
          </el-form-item>
        </el-form>
        <div v-if="analyzeResult" class="result-section">
          <h4>分析结果</h4>
          <div v-html="renderContent(analyzeResult)" />
        </div>
      </el-tab-pane>

      <!-- 证据清单 -->
      <el-tab-pane label="证据清单" name="evidence">
        <el-form :model="evidenceForm" label-width="100px">
          <el-form-item label="案件描述" required>
            <el-input v-model="evidenceForm.case_description" type="textarea" :rows="3"
              placeholder="描述案件基本情况..." />
          </el-form-item>
          <el-form-item label="案由">
            <el-select v-model="evidenceForm.cause_of_action" placeholder="选择案由" clearable>
              <el-option v-for="c in CAUSES" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading.evidence" @click="generateEvidence">生成证据清单</el-button>
          </el-form-item>
        </el-form>
        <div v-if="evidenceResult" class="result-section">
          <h4>证据清单</h4>
          <div v-html="renderContent(evidenceResult)" />
        </div>
      </el-tab-pane>

      <!-- 类案比对 (P1.4) -->
      <el-tab-pane label="类案比对" name="similar">
        <el-form :model="similarForm" label-width="100px">
          <el-form-item label="案件描述" required>
            <el-input v-model="similarForm.case_description" type="textarea" :rows="3"
              placeholder="描述案件情况，系统将从本地判例库（121万+裁判文书）语义检索相似案例..." />
          </el-form-item>
          <el-form-item label="案由">
            <el-select v-model="similarForm.cause_of_action" placeholder="筛选案由（可选）" clearable>
              <el-option v-for="c in CAUSES" :key="c" :label="c" :value="c" />
            </el-select>
          </el-form-item>
          <el-form-item label="类案数量">
            <el-slider v-model="similarForm.top_k" :min="3" :max="10" :step="1" show-stops style="width: 300px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading.similar" @click="compareSimilarCases">
              检索并比对类案
            </el-button>
          </el-form-item>
        </el-form>

        <div v-if="similarResult" class="result-section">
          <h4>相似类案（{{ similarResult.similar_cases?.length || 0 }} 条，来源：本地裁判文书库）</h4>

          <el-table v-if="similarResult.similar_cases?.length" :data="similarResult.similar_cases" border stripe>
            <el-table-column type="index" label="#" width="46" />
            <el-table-column prop="title" label="案件" min-width="220" show-overflow-tooltip />
            <el-table-column prop="court_name" label="法院" width="160" show-overflow-tooltip />
            <el-table-column prop="cause_of_action" label="案由" width="120" show-overflow-tooltip />
            <el-table-column label="判决结果" width="110">
              <template #default="{ row }">
                <el-tag :type="outcomeTagType(row.outcome_bucket)" size="small">{{ row.outcome_bucket }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="相似度" width="90" align="center">
              <template #default="{ row }">{{ (row.relevance_score ?? 0).toFixed(2) }}</template>
            </el-table-column>
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="case-detail">
                  <p><strong>案号：</strong>{{ row.case_number }}</p>
                  <p v-if="row.decision_date"><strong>裁判日期：</strong>{{ row.decision_date }}</p>
                  <p v-if="row.judgment_result"><strong>判决结果：</strong>{{ row.judgment_result }}</p>
                  <p v-if="row.key_points"><strong>裁判要旨：</strong>{{ row.key_points }}</p>
                  <p v-if="row.referenced_laws"><strong>引用法条：</strong>{{ row.referenced_laws }}</p>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <!-- 统计（代码统计，非 LLM） -->
          <div v-if="similarResult.statistics?.outcome_distribution" class="stats-row">
            <h4>结果分布（代码统计）</h4>
            <div class="outcome-tags">
              <el-tag v-for="(count, bucket) in similarResult.statistics.outcome_distribution" :key="bucket"
                :type="outcomeTagType(String(bucket))" size="large">
                {{ bucket }}: {{ count }}
              </el-tag>
            </div>
            <p v-if="similarResult.statistics.amount_stats" class="amount-stats">
              金额参考：中位数 <strong>{{ similarResult.statistics.amount_stats.median.toLocaleString() }}</strong> 元 ｜
              区间 {{ similarResult.statistics.amount_stats.min.toLocaleString() }} ~
              {{ similarResult.statistics.amount_stats.max.toLocaleString() }} 元（样本 {{ similarResult.statistics.amount_stats.count }} 个）
            </p>
          </div>

          <!-- LLM 比对分析 -->
          <div v-if="comparisonSections.length" class="comparison-section">
            <h4>比对分析</h4>
            <div v-for="sec in comparisonSections" :key="sec.title" class="comparison-block">
              <h5>{{ sec.title }}</h5>
              <ul v-if="sec.items.length">
                <li v-for="(item, i) in sec.items" :key="i">{{ item }}</li>
              </ul>
              <p v-else>{{ sec.text }}</p>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- 庭审提纲 -->
      <el-tab-pane label="庭审提纲" name="trial">
        <el-form :model="trialForm" label-width="100px">
          <el-form-item label="案件描述" required>
            <el-input v-model="trialForm.case_description" type="textarea" :rows="3" />
          </el-form-item>
          <el-form-item label="代理方">
            <el-radio-group v-model="trialForm.party">
              <el-radio value="plaintiff">原告</el-radio>
              <el-radio value="defendant">被告</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading.trial" @click="generateTrialOutline">生成庭审提纲</el-button>
          </el-form-item>
        </el-form>
        <div v-if="trialResult" class="result-section">
          <h4>庭审提纲</h4>
          <div v-html="renderContent(trialResult)" />
        </div>
      </el-tab-pane>

      <!-- 一键诉讼报告 (P1.4) -->
      <el-tab-pane label="诉讼报告" name="report">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px"
          title="一键生成完整《诉讼分析报告》：案件分析 → 证据清单 → 类案比对 → 裁判预测，自动组装成文（约 1~2 分钟）" />

        <el-form :model="reportForm" label-width="100px">
          <el-form-item label="案情描述" required>
            <el-input v-model="reportForm.case_description" type="textarea" :rows="4"
              placeholder="请详细描述案件经过、当事人信息、争议焦点..." />
          </el-form-item>
          <el-form-item label="现有证据">
            <el-input v-model="reportForm.evidence_list" type="textarea" :rows="2"
              placeholder="列出已有的证据材料（可选）..." />
          </el-form-item>
          <el-form-item label="诉讼请求">
            <el-input v-model="reportForm.claims" placeholder="希望达到的诉讼目标（可选）..." />
          </el-form-item>
          <el-form-item label="代理视角">
            <el-radio-group v-model="reportForm.party">
              <el-radio value="plaintiff">原告</el-radio>
              <el-radio value="defendant">被告</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="类案数量">
            <el-slider v-model="reportForm.top_k" :min="3" :max="10" :step="1" show-stops style="width: 300px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" size="large" :loading="loading.report" @click="generateReport">
              {{ loading.report ? '报告生成中（分析 → 证据 → 类案 → 预测）…' : '一键生成诉讼分析报告' }}
            </el-button>
          </el-form-item>
        </el-form>

        <div v-if="reportResult" class="result-section">
          <div class="report-toolbar">
            <h4>诉讼分析报告 <el-tag size="small" type="success">耗时 {{ reportResult.elapsed_seconds }}s</el-tag></h4>
            <div>
              <el-button size="small" @click="downloadReport(reportResult.report_markdown, 'litigation-report.md')">
                下载 Markdown
              </el-button>
            </div>
          </div>
          <div class="markdown-body report-body" v-html="renderedReport" />
        </div>
      </el-tab-pane>

      <!-- 费用计算 -->
      <el-tab-pane label="费用计算" name="costs">
        <el-form :model="costForm" label-width="120px">
          <el-form-item label="诉讼标的额" required>
            <el-input-number v-model="costForm.claim_amount" :min="1" :step="10000"
              :format="(v: number) => `${v.toLocaleString()} 元`" style="width: 300px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="calculateCosts">计算费用</el-button>
          </el-form-item>
        </el-form>
        <div v-if="costResult" class="result-section">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="诉讼标的额">{{ costResult.claim_amount?.toLocaleString() }} 元</el-descriptions-item>
            <el-descriptions-item label="案件受理费">{{ costResult.court_fee?.toLocaleString() }} 元</el-descriptions-item>
            <el-descriptions-item label="律师费估算">{{ costResult.lawyer_fee_range }}</el-descriptions-item>
            <el-descriptions-item label="总计估算">{{ costResult.total_estimated_range }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-for="(note, i) in costResult.notes" :key="i" :title="note" type="info" :closable="false" show-icon style="margin-top: 8px" />
          <p style="margin-top: 8px; color: var(--text-secondary); font-size: 13px;">
            法律依据：{{ costResult.legal_basis }}
          </p>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'
import service from '@/api'

const activeTab = ref('analyze')
const loading = reactive({ analyze: false, evidence: false, similar: false, trial: false, report: false, costs: false })

const CAUSES = ['合同纠纷', '劳动争议', '侵权纠纷', '婚姻家庭', '民间借贷', '房产纠纷', '买卖合同纠纷', '网络侵权责任纠纷']

const analyzeForm = ref({ case_description: '', evidence_list: '', claims: '' })
const evidenceForm = ref({ case_description: '', cause_of_action: '' })
const similarForm = ref({ case_description: '', cause_of_action: '', top_k: 5 })
const trialForm = ref({ case_description: '', party: 'plaintiff' })
const reportForm = ref({ case_description: '', evidence_list: '', claims: '', party: 'plaintiff', top_k: 5 })
const costForm = ref({ claim_amount: 100000 })

const analyzeResult = ref<any>(null)
const evidenceResult = ref<any>(null)
const similarResult = ref<any>(null)
const trialResult = ref<any>(null)
const reportResult = ref<any>(null)
const costResult = ref<any>(null)

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
const renderedReport = computed(() => md.render(reportResult.value?.report_markdown || ''))

function renderContent(data: any): string {
  if (!data) return ''
  const analysis = data.analysis || data
  if (typeof analysis === 'string') return analysis.replace(/\n/g, '<br>')
  return Object.entries(analysis)
    .filter(([k, v]) => v && k !== 'raw_content')
    .map(([k, v]) => {
      const label = k.replace(/_/g, ' ')
      const value = Array.isArray(v) ? v.map((i: any) => typeof i === 'object' ? JSON.stringify(i) : i).join('<br>• ') : String(v)
      return `<strong>${label}:</strong><br>${value}`
    }).join('<br><br>')
}

function outcomeTagType(bucket: string): 'success' | 'danger' | 'warning' | 'info' {
  if (bucket.includes('支持') || bucket.includes('准许')) return 'success'
  if (bucket.includes('驳回') || bucket.includes('不予')) return 'danger'
  if (bucket.includes('调解') || bucket.includes('撤诉')) return 'warning'
  return 'info'
}

const comparisonSections = computed(() => {
  const cmp = similarResult.value?.comparison
  if (!cmp || typeof cmp === 'string') return []
  const sections: { title: string; items: string[]; text?: string }[] = []
  if (cmp.fact_commonalities?.length) sections.push({ title: '事实共同点', items: cmp.fact_commonalities })
  if (cmp.fact_differences?.length) sections.push({ title: '关键差异点', items: cmp.fact_differences })
  if (cmp.outcome_analysis) sections.push({ title: '结果走势分析', items: [], text: cmp.outcome_analysis })
  if (cmp.strategy_implications?.length) sections.push({ title: '策略启示', items: cmp.strategy_implications })
  if (cmp.risk_factors?.length) sections.push({ title: '类案揭示的风险', items: cmp.risk_factors })
  return sections
})

function downloadReport(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function analyzeCase() {
  if (!analyzeForm.value.case_description) return ElMessage.warning('请输入案情描述')
  loading.analyze = true
  try {
    const res = await service.post('/litigation/analyze', analyzeForm.value)
    analyzeResult.value = res.data
  } catch { ElMessage.error('分析失败') }
  finally { loading.analyze = false }
}

async function generateEvidence() {
  if (!evidenceForm.value.case_description) return ElMessage.warning('请输入案件描述')
  loading.evidence = true
  try {
    const res = await service.post('/litigation/evidence', evidenceForm.value)
    evidenceResult.value = res.data
  } catch { ElMessage.error('生成失败') }
  finally { loading.evidence = false }
}

async function compareSimilarCases() {
  if (!similarForm.value.case_description) return ElMessage.warning('请输入案件描述')
  loading.similar = true
  similarResult.value = null
  try {
    const res = await service.post('/litigation/similar-cases', similarForm.value)
    similarResult.value = res.data
    const count = res.data.similar_cases?.length || 0
    if (!count) ElMessage.warning('未检索到相似类案，请补充更多案件细节')
  } catch { ElMessage.error('类案比对失败') }
  finally { loading.similar = false }
}

async function generateTrialOutline() {
  if (!trialForm.value.case_description) return ElMessage.warning('请输入案件描述')
  loading.trial = true
  try {
    const res = await service.post('/litigation/trial-outline', trialForm.value)
    trialResult.value = res.data
  } catch { ElMessage.error('生成失败') }
  finally { loading.trial = false }
}

async function generateReport() {
  if (!reportForm.value.case_description) return ElMessage.warning('请输入案情描述')
  loading.report = true
  reportResult.value = null
  try {
    const res = await service.post('/litigation/report', reportForm.value, { timeout: 300000 })
    reportResult.value = res.data
    ElMessage.success(`报告已生成（耗时 ${res.data.elapsed_seconds}s）`)
  } catch { ElMessage.error('报告生成失败，请稍后重试') }
  finally { loading.report = false }
}

async function calculateCosts() {
  loading.costs = true
  try {
    const res = await service.post('/litigation/costs', costForm.value)
    costResult.value = res.data
  } catch { ElMessage.error('计算失败') }
  finally { loading.costs = false }
}
</script>

<style scoped>
.litigation-view { padding: 24px; }
.view-header { margin-bottom: 20px; }
.view-header h2 { margin: 0 0 4px; color: var(--primary-color); }
.view-header p { margin: 0; color: var(--text-secondary); font-size: 14px; }
.result-section { margin-top: 20px; padding: 16px; background: var(--bg-white); border-radius: var(--radius-md); border: 1px solid var(--border-color); line-height: 1.8; }
.result-section h4 { color: var(--primary-color); margin: 0 0 12px; }
.stats-row { margin-top: 20px; }
.stats-row h4 { color: var(--primary-color); margin: 0 0 10px; }
.outcome-tags { display: flex; gap: 10px; flex-wrap: wrap; }
.amount-stats { margin: 12px 0 0; color: var(--text-secondary); }
.comparison-section { margin-top: 24px; }
.comparison-section h4 { color: var(--primary-color); }
.comparison-block { margin-bottom: 14px; padding: 12px; background: var(--el-fill-color-light); border-radius: 8px; }
.comparison-block h5 { margin: 0 0 8px; color: var(--primary-color); }
.comparison-block ul { margin: 0; padding-left: 20px; }
.case-detail { padding: 8px 16px; }
.case-detail p { margin: 6px 0; }
.report-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.report-body { max-height: 640px; overflow-y: auto; padding: 4px; }
.report-body :deep(table) { border-collapse: collapse; width: 100%; margin: 10px 0; }
.report-body :deep(th), .report-body :deep(td) { border: 1px solid var(--border-color); padding: 6px 10px; font-size: 13px; text-align: left; }
.report-body :deep(th) { background: var(--el-fill-color-light); }
</style>
