<template>
  <div class="compliance-view">
    <div class="view-header">
      <h2>合规风险管理</h2>
      <p>企业合规风险评估、检查清单生成、法规动态追踪、合规审计报告</p>
    </div>

    <!-- 风险仪表盘 -->
    <el-row :gutter="20" style="margin-bottom: 24px;">
      <el-col :xs="24" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon high-risk">
              <el-icon :size="32"><Warning /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ riskStats.highRisk }}</div>
              <div class="stat-label">高风险项</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon medium-risk">
              <el-icon :size="32"><InfoFilled /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ riskStats.mediumRisk }}</div>
              <div class="stat-label">中风险项</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8" :md="8">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-icon low-risk">
              <el-icon :size="32"><CircleCheck /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ riskStats.lowRisk }}</div>
              <div class="stat-label">低风险项</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" type="border-card" @tab-change="onTabChange">
      <!-- 动态监控 (P1.5) -->
      <el-tab-pane label="动态监控" name="monitor">
        <div class="toolbar">
          <el-button type="primary" :loading="scanning" @click="scanNow">
            <el-icon><Bell /></el-icon>
            立即扫描法规动态
          </el-button>
          <el-button @click="openWatchlistForm()">新建监控配置</el-button>
          <span class="muted" v-if="lastScanText">{{ lastScanText }}</span>
        </div>

        <h4 style="margin: 12px 0;">监控配置</h4>
        <el-table :data="watchlists" v-loading="watchlistLoading" stripe>
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column prop="industry" label="行业" width="110" />
          <el-table-column label="主题关键词" min-width="180">
            <template #default="{ row }">
              <el-tag v-for="t in row.topics" :key="t" size="small" style="margin: 2px;">{{ t }}</el-tag>
              <span v-if="!row.topics?.length" class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="合规领域" min-width="160">
            <template #default="{ row }">
              <el-tag v-for="d in row.compliance_domains" :key="d" size="small" type="info" style="margin: 2px;">
                {{ domainName(d) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="启用" width="80">
            <template #default="{ row }">
              <el-switch v-model="row.enabled" @change="toggleWatchlist(row)" />
            </template>
          </el-table-column>
          <el-table-column label="最近扫描" width="160">
            <template #default="{ row }">{{ formatTime(row.last_scan_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openWatchlistForm(row)">编辑</el-button>
              <el-button size="small" type="danger" @click="removeWatchlist(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="alert-header">
          <h4>风险告警</h4>
          <el-radio-group v-model="alertStatusFilter" size="small" @change="loadAlerts">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="open">待处理</el-radio-button>
            <el-radio-button value="acknowledged">已确认</el-radio-button>
          </el-radio-group>
        </div>
        <el-table :data="alerts" v-loading="alertLoading" stripe>
          <el-table-column label="法规" min-width="220">
            <template #default="{ row }">
              <a v-if="row.regulation?.url" :href="row.regulation.url" target="_blank" class="link">
                {{ row.regulation.title }}
              </a>
              <span v-else>{{ row.regulation?.title }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">{{ row.regulation?.law_type || '—' }}</template>
          </el-table-column>
          <el-table-column label="公布日期" width="110">
            <template #default="{ row }">{{ row.regulation?.publish_date || '—' }}</template>
          </el-table-column>
          <el-table-column prop="matched_keyword" label="命中关键词" width="110" />
          <el-table-column label="风险等级" width="90">
            <template #default="{ row }">
              <el-tag :type="row.risk_level === '高' ? 'danger' : row.risk_level === '中' ? 'warning' : 'success'">
                {{ row.risk_level }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="影响分析" min-width="240">
            <template #default="{ row }">
              <div v-if="row.analysis">
                <div>{{ row.analysis.impact }}</div>
                <div v-if="row.analysis.suggested_actions?.length" class="muted">
                  建议：{{ row.analysis.suggested_actions.join('；') }}
                </div>
              </div>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 'open' ? 'danger' : 'info'" size="small">
                {{ row.status === 'open' ? '待处理' : '已确认' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.status === 'open'" size="small" type="primary"
                @click="ackAlert(row)">确认</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-if="alertTotal > alertPageSize" layout="prev, pager, next, total"
          :total="alertTotal" :page-size="alertPageSize" v-model:current-page="alertPage"
          style="margin-top: 12px; justify-content: flex-end;" @current-change="loadAlerts" />
      </el-tab-pane>

      <!-- 风险评估 -->
      <el-tab-pane label="风险评估" name="assess">
        <el-form :model="assessForm" label-width="120px">
          <el-form-item label="业务描述" required>
            <el-input v-model="assessForm.business_description" type="textarea" :rows="4"
              placeholder="描述企业的业务模式、经营范围、数据处理方式等..." />
          </el-form-item>
          <el-form-item label="所属行业">
            <el-select v-model="assessForm.industry" placeholder="选择行业" clearable>
              <el-option label="互联网/科技" value="互联网科技" />
              <el-option label="金融" value="金融" />
              <el-option label="医疗健康" value="医疗健康" />
              <el-option label="教育培训" value="教育培训" />
              <el-option label="电商零售" value="电商零售" />
              <el-option label="制造业" value="制造业" />
              <el-option label="房地产" value="房地产" />
            </el-select>
          </el-form-item>
          <el-form-item label="合规领域">
            <el-checkbox-group v-model="assessForm.compliance_domains">
              <el-checkbox label="data_privacy">数据隐私</el-checkbox>
              <el-checkbox label="labor">劳动用工</el-checkbox>
              <el-checkbox label="anti_corruption">反腐败</el-checkbox>
              <el-checkbox label="ip">知识产权</el-checkbox>
              <el-checkbox label="consumer">消费者保护</el-checkbox>
              <el-checkbox label="ecommerce">电子商务</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="assessRisk">
              <el-icon><Search /></el-icon>
              开始评估
            </el-button>
          </el-form-item>
        </el-form>

        <div v-if="assessResult" class="result-section">
          <el-result :icon="assessResult.overall_risk_level === '高' ? 'warning' : 'success'"
            :title="`整体风险等级: ${assessResult.overall_risk_level || '中'}`"
            :sub-title="`合规评分: ${assessResult.overall_score || 50}/100`" />

          <el-card shadow="never" style="margin-top: 16px;">
            <template #header>
              <div class="card-header">
                <span>风险等级详情</span>
              </div>
            </template>
            <el-progress :percentage="assessResult.overall_score || 50"
              :color="getRiskColor(assessResult.overall_risk_level)"
              :stroke-width="24"
              :text-inside="true"
              style="margin-bottom: 16px;" />
            <div v-if="assessResult.summary" class="summary-text" v-html="assessResult.summary.replace(/\n/g, '<br>')" />
          </el-card>

          <div v-if="assessResult.action_plan && assessResult.action_plan.length > 0" style="margin-top: 20px;">
            <h4 style="margin-bottom: 12px;">优先行动计划</h4>
            <el-timeline>
              <el-timeline-item
                v-for="(action, i) in assessResult.action_plan"
                :key="i"
                :type="i === 0 ? 'primary' : 'info'"
                :hollow="i !== 0"
                size="large">
                <el-card shadow="hover">
                  {{ action }}
                </el-card>
              </el-timeline-item>
            </el-timeline>
          </div>
        </div>
      </el-tab-pane>

      <!-- 合规清单 -->
      <el-tab-pane label="合规清单" name="checklist">
        <el-form :model="checklistForm" label-width="120px">
          <el-form-item label="行业" required>
            <el-input v-model="checklistForm.industry" placeholder="输入行业名称" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="generateChecklist">
              <el-icon><Document /></el-icon>
              生成检查清单
            </el-button>
          </el-form-item>
        </el-form>
        <div v-if="checklistResult" class="result-section">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span>合规检查清单</span>
                <el-tag type="success">已生成</el-tag>
              </div>
            </template>
            <div v-html="renderJSON(checklistResult)" />
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 法规追踪 -->
      <el-tab-pane label="法规追踪" name="track">
        <el-form :model="trackForm" label-width="120px">
          <el-form-item label="追踪主题" required>
            <el-select v-model="trackForm.topics" multiple filterable allow-create placeholder="添加要追踪的法规主题">
              <el-option label="个人信息保护" value="个人信息保护" />
              <el-option label="劳动法" value="劳动法" />
              <el-option label="数据安全" value="数据安全" />
              <el-option label="反垄断" value="反垄断" />
              <el-option label="知识产权" value="知识产权" />
            </el-select>
          </el-form-item>
          <el-form-item label="时间范围">
            <el-select v-model="trackForm.days_back">
              <el-option :value="7" label="近7天" />
              <el-option :value="30" label="近30天" />
              <el-option :value="90" label="近3个月" />
              <el-option :value="365" label="近1年" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="trackRegulations">
              <el-icon><Bell /></el-icon>
              追踪法规更新
            </el-button>
          </el-form-item>
        </el-form>
        <div v-if="trackResult" class="result-section">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span>法规更新追踪</span>
                <el-tag>共 {{ trackResult.total_results }} 条结果</el-tag>
              </div>
            </template>
            <p v-if="trackResult.summary" v-html="trackResult.summary.replace(/\n/g, '<br>')" />
            <p style="color: var(--text-secondary); margin-top: 12px;">共检索到 {{ trackResult.total_results }} 条相关结果</p>
          </el-card>
        </div>
      </el-tab-pane>

      <!-- 合规领域 -->
      <el-tab-pane label="合规领域" name="domains">
        <div v-if="domains.length === 0" style="text-align: center; padding: 40px;">
          <el-button @click="loadDomains">加载合规领域列表</el-button>
        </div>
        <el-card v-for="d in domains" :key="d.id" shadow="hover" style="margin-bottom: 12px;">
          <h4 style="margin: 0 0 8px;">{{ d.name }}</h4>
          <p style="margin: 0 0 8px; font-size: 13px; color: var(--text-secondary);">
            适用法规: {{ d.regulations.join('、') }}
          </p>
          <div>
            <el-tag v-for="kw in d.keywords" :key="kw" size="small" type="info" effect="plain" style="margin: 2px;">{{ kw }}</el-tag>
          </div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 监控配置编辑对话框 -->
    <el-dialog v-model="watchlistDialogVisible" :title="watchlistForm.id ? '编辑监控配置' : '新建监控配置'" width="520px">
      <el-form :model="watchlistForm" label-width="100px">
        <el-form-item label="名称" required>
          <el-input v-model="watchlistForm.name" placeholder="如：数据合规监控" />
        </el-form-item>
        <el-form-item label="行业">
          <el-input v-model="watchlistForm.industry" placeholder="如：互联网科技" />
        </el-form-item>
        <el-form-item label="主题关键词">
          <el-select v-model="watchlistForm.topics" multiple filterable allow-create
            placeholder="输入关键词回车添加，如：个人信息、算法">
            <el-option label="个人信息" value="个人信息" />
            <el-option label="数据出境" value="数据出境" />
            <el-option label="算法推荐" value="算法推荐" />
            <el-option label="平台经济" value="平台经济" />
            <el-option label="反垄断" value="反垄断" />
          </el-select>
        </el-form-item>
        <el-form-item label="合规领域">
          <el-checkbox-group v-model="watchlistForm.compliance_domains">
            <el-checkbox v-for="d in domains" :key="d.id" :label="d.id">{{ d.name }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="watchlistForm.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="watchlistDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="saveWatchlist">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning, InfoFilled, CircleCheck, Search, Document, Bell } from '@element-plus/icons-vue'
import service from '@/api'

interface AssessForm {
  business_description: string
  industry: string
  compliance_domains: string[]
}

interface AssessResult {
  overall_risk_level: string
  overall_score: number
  summary: string
  action_plan: string[]
  domain_risks?: Array<{
    domain: string
    risk_level: string
    score: number
    issues: string[]
  }>
}

interface ChecklistResult {
  items: Array<{
    item: string
    status: string
    description: string
  }>
}

interface TrackResult {
  summary: string
  total_results: number
  updates?: Array<{
    title: string
    date: string
    source: string
    impact: string
  }>
}

interface ComplianceDomain {
  id: string
  name: string
  regulations: string[]
  keywords: string[]
}

interface RiskStats {
  highRisk: number
  mediumRisk: number
  lowRisk: number
}

interface Watchlist {
  id: string
  name: string
  industry: string
  topics: string[]
  compliance_domains: string[]
  enabled: boolean
  last_scan_at: string | null
  created_at: string | null
}

interface ComplianceAlertItem {
  id: string
  risk_level: string
  matched_keyword: string
  status: string
  created_at: string | null
  watchlist_name: string
  regulation: {
    title: string
    law_type: string
    publish_date: string
    effective_date: string
    status: string
    url: string | null
  }
  analysis: { impact?: string; risk_level?: string; suggested_actions?: string[] } | null
}

const activeTab = ref('monitor')
const loading = ref(false)
const domains = ref<ComplianceDomain[]>([])
const riskStats = ref<RiskStats>({ highRisk: 0, mediumRisk: 0, lowRisk: 0 })

// P1.5 动态监控
const watchlists = ref<Watchlist[]>([])
const watchlistLoading = ref(false)
const scanning = ref(false)
const lastScanText = ref('')
const alerts = ref<ComplianceAlertItem[]>([])
const alertLoading = ref(false)
const alertStatusFilter = ref('')
const alertPage = ref(1)
const alertPageSize = 20
const alertTotal = ref(0)
const watchlistDialogVisible = ref(false)
const watchlistForm = ref<Partial<Watchlist>>({})

const assessForm = ref<AssessForm>({ business_description: '', industry: '', compliance_domains: [] })
const checklistForm = ref<{ industry: string }>({ industry: '' })
const trackForm = ref<{ topics: string[]; days_back: number }>({ topics: [], days_back: 30 })

const assessResult = ref<AssessResult | null>(null)
const checklistResult = ref<ChecklistResult | null>(null)
const trackResult = ref<TrackResult | null>(null)

function getRiskColor(level: string): string {
  if (level === '高') return '#f56c6c'
  if (level === '中') return '#e6a23c'
  return '#67c23a'
}

function renderJSON(data: any): string {
  return `<pre style="white-space: pre-wrap; font-size: 13px;">${JSON.stringify(data, null, 2)}</pre>`
}

function updateRiskStats(result: AssessResult) {
  if (!result.domain_risks) return
  let high = 0, medium = 0, low = 0
  result.domain_risks.forEach(d => {
    if (d.risk_level === '高') high++
    else if (d.risk_level === '中') medium++
    else low++
  })
  riskStats.value = { highRisk: high, mediumRisk: medium, lowRisk: low }
}

async function assessRisk() {
  if (!assessForm.value.business_description) return ElMessage.warning('请输入业务描述')
  loading.value = true
  try {
    const res = await service.post('/compliance/assess', assessForm.value)
    assessResult.value = res.data
    updateRiskStats(res.data)
  } catch { ElMessage.error('评估失败') }
  finally { loading.value = false }
}

async function generateChecklist() {
  if (!checklistForm.value.industry) return ElMessage.warning('请输入行业')
  loading.value = true
  try {
    const res = await service.post('/compliance/checklist', checklistForm.value)
    checklistResult.value = res.data
  } catch { ElMessage.error('生成失败') }
  finally { loading.value = false }
}

async function trackRegulations() {
  if (trackForm.value.topics.length === 0) return ElMessage.warning('请添加追踪主题')
  loading.value = true
  try {
    const res = await service.post('/compliance/track', trackForm.value)
    trackResult.value = res.data
  } catch { ElMessage.error('追踪失败') }
  finally { loading.value = false }
}

async function loadDomains() {
  try {
    const res = await service.get('/compliance/domains')
    domains.value = res.data.domains || []
  } catch { ElMessage.error('加载失败') }
}

// ------------------------------------------------------------------
// P1.5 动态监控
// ------------------------------------------------------------------

function domainName(id: string): string {
  return domains.value.find(d => d.id === id)?.name || id
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 16)
}

function onTabChange(tab: string | number) {
  if (tab === 'monitor') {
    loadWatchlists()
    loadAlerts()
  }
}

async function loadWatchlists() {
  watchlistLoading.value = true
  try {
    const res = await service.get('/compliance/watchlists')
    watchlists.value = res.data.items || []
  } catch { ElMessage.error('加载监控配置失败') }
  finally { watchlistLoading.value = false }
}

async function loadAlerts() {
  alertLoading.value = true
  try {
    const params: Record<string, unknown> = { page: alertPage.value, page_size: alertPageSize }
    if (alertStatusFilter.value) params.status = alertStatusFilter.value
    const res = await service.get('/compliance/alerts', { params })
    alerts.value = res.data.items || []
    alertTotal.value = res.data.total || 0
  } catch { ElMessage.error('加载告警失败') }
  finally { alertLoading.value = false }
}

async function scanNow() {
  scanning.value = true
  try {
    const res = await service.post('/compliance/scan', null, { params: { days_back: 30 } })
    lastScanText.value = `同步 ${res.data.changes_synced} 条法规动态，生成 ${res.data.alerts_created} 条告警`
    ElMessage.success(lastScanText.value)
    await Promise.all([loadWatchlists(), loadAlerts()])
  } catch { ElMessage.error('扫描失败，请稍后重试') }
  finally { scanning.value = false }
}

function openWatchlistForm(row?: Watchlist) {
  watchlistForm.value = row
    ? { ...row, topics: [...row.topics], compliance_domains: [...row.compliance_domains] }
    : { name: '', industry: '', topics: [], compliance_domains: [], enabled: true }
  watchlistDialogVisible.value = true
}

async function saveWatchlist() {
  const form = watchlistForm.value
  if (!form.name?.trim()) return ElMessage.warning('请输入监控配置名称')
  loading.value = true
  try {
    if (form.id) {
      await service.put(`/compliance/watchlists/${form.id}`, {
        name: form.name,
        industry: form.industry || '',
        topics: form.topics || [],
        compliance_domains: form.compliance_domains || [],
        enabled: form.enabled ?? true,
      })
    } else {
      await service.post('/compliance/watchlists', {
        name: form.name,
        industry: form.industry || '',
        topics: form.topics || [],
        compliance_domains: form.compliance_domains || [],
        enabled: form.enabled ?? true,
      })
    }
    watchlistDialogVisible.value = false
    ElMessage.success('监控配置已保存')
    await loadWatchlists()
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    ElMessage.error(detail || '保存失败')
  } finally { loading.value = false }
}

async function toggleWatchlist(row: Watchlist) {
  try {
    await service.put(`/compliance/watchlists/${row.id}`, { enabled: row.enabled })
    ElMessage.success(row.enabled ? '已启用' : '已停用')
  } catch {
    row.enabled = !row.enabled
    ElMessage.error('操作失败')
  }
}

async function removeWatchlist(row: Watchlist) {
  try {
    await service.delete(`/compliance/watchlists/${row.id}`)
    ElMessage.success('监控配置已删除')
    await loadWatchlists()
  } catch { ElMessage.error('删除失败') }
}

async function ackAlert(row: ComplianceAlertItem) {
  try {
    await service.post(`/compliance/alerts/${row.id}/ack`)
    row.status = 'acknowledged'
    ElMessage.success('告警已确认')
  } catch { ElMessage.error('操作失败') }
}

onMounted(() => {
  loadDomains()
  loadWatchlists()
  loadAlerts()
})
</script>

<style scoped>
.compliance-view { padding: 24px; }
.view-header { margin-bottom: 20px; }
.view-header h2 { margin: 0 0 4px; color: var(--primary-color); }
.view-header p { margin: 0; color: var(--text-secondary); font-size: 14px; }
.stat-card { margin-bottom: 0; }
.stat-content { display: flex; align-items: center; gap: 16px; }
.stat-icon { width: 60px; height: 60px; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #fff; }
.stat-icon.high-risk { background: linear-gradient(135deg, #f56c6c, #e74c3c); }
.stat-icon.medium-risk { background: linear-gradient(135deg, #e6a23c, #f39c12); }
.stat-icon.low-risk { background: linear-gradient(135deg, #67c23a, #2ecc71); }
.stat-info { flex: 1; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--text-primary); }
.stat-label { font-size: 13px; color: var(--text-secondary); margin-top: 2px; }
.result-section { margin-top: 20px; }
.summary-text { margin-top: 12px; font-size: 14px; line-height: 1.8; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
</style>
