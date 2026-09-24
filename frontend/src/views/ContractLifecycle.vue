<template>
  <div class="contract-lifecycle-view">
    <div class="view-header">
      <h2>合同生命周期管理</h2>
      <p>起草 → 审查 → 对比 → 合规检查 → 日期追踪 → 归档 — 全流程留痕</p>
    </div>

    <el-tabs v-model="activeTab" type="border-card" @tab-change="onTabChange">
      <!-- 我的合同 -->
      <el-tab-pane label="我的合同" name="contracts">
        <div class="toolbar">
          <el-select v-model="statusFilter" placeholder="状态筛选" clearable style="width: 140px" @change="loadContracts">
            <el-option label="草稿" value="draft" />
            <el-option label="已审查" value="reviewed" />
            <el-option label="已归档" value="archived" />
          </el-select>
          <el-button @click="loadContracts">刷新</el-button>
        </div>
        <el-table :data="contracts" v-loading="contractsLoading" stripe>
          <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
          <el-table-column prop="contract_type" label="类型" width="110" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="current_version" label="版本" width="70" />
          <el-table-column label="最近审查" width="150">
            <template #default="{ row }">
              <span v-if="row.latest_review_id" class="link" @click="openContract(row.id)">查看报告</span>
              <span v-else class="muted">未审查</span>
            </template>
          </el-table-column>
          <el-table-column prop="updated_at" label="更新时间" width="170">
            <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="280" fixed="right">
            <template #default="{ row }">
              <el-button size="small" @click="openContract(row.id)">详情</el-button>
              <el-button size="small" type="primary" :disabled="row.status === 'archived'"
                @click="runReview(row)">审查</el-button>
              <el-button size="small" type="warning" :disabled="row.status === 'archived'"
                @click="archiveContract(row)">归档</el-button>
              <el-button size="small" type="danger" @click="removeContract(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination v-if="contractTotal > contractPageSize" layout="prev, pager, next, total"
          :total="contractTotal" :page-size="contractPageSize" v-model:current-page="contractPage"
          style="margin-top: 12px; justify-content: flex-end;" @current-change="loadContracts" />
      </el-tab-pane>

      <!-- 合同起草 -->
      <el-tab-pane label="合同起草" name="draft">
        <el-form :model="draftForm" label-width="100px">
          <el-form-item label="合同标题">
            <el-input v-model="draftForm.title" placeholder="留空则自动生成" style="max-width: 360px" />
          </el-form-item>
          <el-form-item label="合同描述" required>
            <el-input v-model="draftForm.description" type="textarea" :rows="3"
              placeholder="描述合同需求，如：起草一份为期两年的软件开发外包服务合同..." />
          </el-form-item>
          <el-form-item label="合同类型">
            <el-select v-model="draftForm.contract_type" placeholder="选择模板（可选）" clearable>
              <el-option label="劳动合同" value="劳动合同" />
              <el-option label="买卖合同" value="买卖合同" />
              <el-option label="租赁合同" value="租赁合同" />
              <el-option label="借款合同" value="借款合同" />
              <el-option label="服务合同" value="服务合同" />
              <el-option label="保密协议" value="保密协议" />
              <el-option label="技术合同" value="技术合同" />
              <el-option label="合作协议" value="合作协议" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="draftContract">起草合同</el-button>
          </el-form-item>
        </el-form>
        <div v-if="draftResult" class="result-section">
          <div class="result-meta">
            <el-tag v-if="draftResult.method === 'template'" type="success">基于模板: {{ draftResult.template_name }}</el-tag>
            <el-tag v-else type="info">AI生成</el-tag>
            <el-tag v-if="draftResult.contract_id" type="success" style="margin-left: 8px">
              已存为草稿 v{{ draftResult.version }}（{{ draftResult.contract_id.slice(0, 8) }}…）
            </el-tag>
          </div>
          <el-input v-model="draftResult.content" type="textarea" :rows="15" readonly />
          <div style="margin-top: 8px;">
            <el-button size="small" @click="copyContent(draftResult.content)">复制文本</el-button>
            <el-button size="small" type="primary" v-if="draftResult.contract_id"
              @click="openContract(draftResult.contract_id)">查看合同档案</el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- 版本对比 -->
      <el-tab-pane label="版本对比" name="compare">
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="原版合同">
              <el-input v-model="compareForm.original" type="textarea" :rows="8" placeholder="粘贴原版合同..." />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="修改版合同">
              <el-input v-model="compareForm.modified" type="textarea" :rows="8" placeholder="粘贴修改版合同..." />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="保存版本">
          <el-select v-model="compareForm.contract_id" placeholder="将修改版存入合同（可选）" clearable style="max-width: 360px">
            <el-option v-for="c in editableContracts" :key="c.id" :label="`${c.title}（v${c.current_version}）`" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-button type="primary" :loading="loading" :disabled="!compareForm.original || !compareForm.modified" @click="compareContracts">
          对比分析
        </el-button>
        <div v-if="compareResult" class="result-section">
          <div class="result-meta">
            <el-tag v-if="compareResult.saved_version" type="success">
              已保存为 v{{ compareResult.saved_version }}
            </el-tag>
          </div>
          <el-descriptions :column="3" border>
            <el-descriptions-item label="新增行数">{{ compareResult.additions }}</el-descriptions-item>
            <el-descriptions-item label="删除行数">{{ compareResult.deletions }}</el-descriptions-item>
            <el-descriptions-item label="风险等级">
              <el-tag :type="getRiskType(compareResult.analysis?.overall_risk)">{{ compareResult.analysis?.overall_risk || '未知' }}</el-tag>
            </el-descriptions-item>
          </el-descriptions>
          <div v-if="compareResult.analysis?.summary" style="margin-top: 12px;" v-html="String(compareResult.analysis.summary).replace(/\n/g, '<br>')" />
        </div>
      </el-tab-pane>

      <!-- 合规检查 -->
      <el-tab-pane label="合规检查" name="compliance">
        <el-form :model="complianceForm" label-width="100px">
          <el-form-item label="合同文本" required>
            <el-input v-model="complianceForm.contract_text" type="textarea" :rows="6" placeholder="粘贴合同文本..." />
          </el-form-item>
          <el-form-item label="所属行业">
            <el-input v-model="complianceForm.industry" placeholder="如：金融、教育、医疗（可选）" style="max-width: 360px" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="checkCompliance">合规检查</el-button>
          </el-form-item>
        </el-form>
        <div v-if="complianceResult" class="result-section">
          <el-progress :percentage="complianceResult.compliance_score || 0" :color="getScoreColor(complianceResult.compliance_score)" :stroke-width="20" :text-inside="true" style="margin-bottom: 16px;" />
          <div v-html="renderJSON(complianceResult)" />
        </div>
      </el-tab-pane>

      <!-- 日期追踪 -->
      <el-tab-pane label="日期追踪" name="dates">
        <el-form label-width="100px">
          <el-form-item label="合同文本" required>
            <el-input v-model="dateText" type="textarea" :rows="6" placeholder="粘贴合同文本..." />
          </el-form-item>
          <el-form-item label="关联合同">
            <el-select v-model="dateContractId" placeholder="将日期存入合同（可选）" clearable style="max-width: 360px">
              <el-option v-for="c in contracts" :key="c.id" :label="c.title" :value="c.id" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="loading" @click="extractDates">提取关键日期</el-button>
          </el-form-item>
        </el-form>
        <div v-if="dateResult" class="result-section">
          <el-alert v-if="dateResult.saved_to_contract" type="success" :closable="false"
            title="已保存到合同档案" style="margin-bottom: 12px;" />
          <el-timeline v-if="dateResult.dates?.length">
            <el-timeline-item v-for="(d, i) in dateResult.dates" :key="i" :timestamp="d.date || ''" placement="top">
              <el-card shadow="hover"><strong>{{ d.type }}</strong>: {{ d.description }}</el-card>
            </el-timeline-item>
          </el-timeline>
          <p v-else>未提取到关键日期</p>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 合同详情抽屉 -->
    <el-drawer v-model="detailVisible" :title="detail?.title || '合同详情'" size="55%">
      <template v-if="detail">
        <el-descriptions :column="2" border style="margin-bottom: 16px;">
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(detail.status)">{{ statusLabel(detail.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="当前版本">v{{ detail.current_version }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ detail.contract_type || '—' }}</el-descriptions-item>
          <el-descriptions-item label="归档时间">{{ detail.archived_at ? formatTime(detail.archived_at) : '—' }}</el-descriptions-item>
        </el-descriptions>

        <h4>最新审查结果</h4>
        <template v-if="detail.latest_review">
          <el-descriptions :column="2" border style="margin-bottom: 16px;">
            <el-descriptions-item label="风险评分">{{ detail.latest_review.risk_score ?? '—' }}</el-descriptions-item>
            <el-descriptions-item label="审查时间">{{ formatTime(detail.latest_review.created_at) }}</el-descriptions-item>
          </el-descriptions>
          <div class="review-summary">{{ detail.latest_review.summary }}</div>
        </template>
        <el-empty v-else description="尚未审查" :image-size="60" style="padding: 12px 0 16px;" />

        <h4>版本历史</h4>
        <el-timeline style="margin-bottom: 16px;">
          <el-timeline-item v-for="v in detail.versions" :key="v.version_number"
            :timestamp="formatTime(v.created_at)" placement="top">
            <el-card shadow="hover">
              <div class="version-head">
                <strong>v{{ v.version_number }}</strong>
                <el-tag size="small" type="info">{{ sourceLabel(v.source) }}</el-tag>
                <el-button size="small" text type="primary" @click="showVersion(v)">查看内容</el-button>
              </div>
              <div v-if="v.change_summary" class="muted" style="margin-top: 4px;">{{ v.change_summary }}</div>
            </el-card>
          </el-timeline-item>
        </el-timeline>

        <h4>关键日期</h4>
        <template v-if="detail.key_dates?.length">
          <el-tag v-for="d in detail.key_dates" :key="d.id" style="margin: 0 8px 8px 0;" :type="d.notified ? 'info' : 'warning'">
            {{ d.date_type }}: {{ d.date_value }}
          </el-tag>
        </template>
        <el-empty v-else description="未提取关键日期" :image-size="60" style="padding: 12px 0 16px;" />

        <h4>当前内容</h4>
        <el-input :model-value="detail.content" type="textarea" :rows="10" readonly />
      </template>
    </el-drawer>

    <!-- 版本内容对话框 -->
    <el-dialog v-model="versionVisible" :title="`版本内容 v${viewingVersion?.version_number || ''}`" width="60%">
      <el-input :model-value="viewingVersion?.content" type="textarea" :rows="16" readonly />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import service from '@/api'
import { lifecycleApi } from '@/api/contract'

const activeTab = ref('contracts')
const loading = ref(false)

// ---- 我的合同 ----
const contracts = ref<any[]>([])
const contractsLoading = ref(false)
const contractPage = ref(1)
const contractPageSize = 20
const contractTotal = ref(0)
const statusFilter = ref('')

const editableContracts = computed(() => contracts.value.filter(c => c.status !== 'archived'))

const detailVisible = ref(false)
const detail = ref<any>(null)
const versionVisible = ref(false)
const viewingVersion = ref<any>(null)

// ---- 表单 ----
const draftForm = ref({ title: '', description: '', contract_type: '' })
const compareForm = ref({ original: '', modified: '', original_label: '原版', modified_label: '修改版', contract_id: '' })
const complianceForm = ref({ contract_text: '', industry: '' })
const dateText = ref('')
const dateContractId = ref('')

const draftResult = ref<any>(null)
const compareResult = ref<any>(null)
const complianceResult = ref<any>(null)
const dateResult = ref<any>(null)

function statusLabel(s: string) {
  return s === 'draft' ? '草稿' : s === 'reviewed' ? '已审查' : s === 'archived' ? '已归档' : s
}
function statusTagType(s: string) {
  return s === 'draft' ? 'info' : s === 'reviewed' ? 'success' : 'warning'
}
function sourceLabel(s: string) {
  const map: Record<string, string> = {
    draft: '起草', import: '导入', compare: '对比定稿', redline: '红线修改', manual: '手动更新',
  }
  return map[s] || s
}
function formatTime(t?: string) {
  if (!t) return '—'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}
function getRiskType(level: string) {
  if (level === '高') return 'danger'
  if (level === '中') return 'warning'
  return 'success'
}
function getScoreColor(score: number) {
  if (score >= 80) return '#67c23a'
  if (score >= 60) return '#e6a23c'
  return '#f56c6c'
}
function renderJSON(data: any) {
  return `<pre style="white-space: pre-wrap; font-size: 13px; max-height: 400px; overflow: auto;">${JSON.stringify(data, null, 2)}</pre>`
}
function copyContent(text: string) {
  navigator.clipboard.writeText(text)
  ElMessage.success('已复制到剪贴板')
}

async function loadContracts() {
  contractsLoading.value = true
  try {
    const res = await lifecycleApi.listContracts({
      page: contractPage.value,
      page_size: contractPageSize,
      status: statusFilter.value || undefined,
    })
    contracts.value = res.data.items || []
    contractTotal.value = res.data.total || 0
  } catch {
    ElMessage.error('加载合同列表失败')
  } finally {
    contractsLoading.value = false
  }
}

function onTabChange(tab: string) {
  if (tab === 'contracts') loadContracts()
}

async function openContract(id: string) {
  try {
    const res = await lifecycleApi.getContractDetail(id)
    detail.value = res.data
    detailVisible.value = true
  } catch {
    ElMessage.error('加载合同详情失败')
  }
}

function showVersion(v: any) {
  viewingVersion.value = v
  versionVisible.value = true
}

async function runReview(row: any) {
  try {
    await ElMessageBox.confirm(
      `对《${row.title}》当前版本（v${row.current_version}）发起 AI 审查？`,
      '合同审查', { confirmButtonText: '开始审查', type: 'info' },
    )
  } catch { return }
  loading.value = true
  try {
    const res = await lifecycleApi.reviewContract(row.id)
    ElMessage.success(`审查完成：风险评分 ${res.data.risk_score}，等级 ${res.data.risk_level}`)
    await openContract(row.id)
    await loadContracts()
  } catch {
    ElMessage.error('审查失败')
  } finally {
    loading.value = false
  }
}

async function archiveContract(row: any) {
  try {
    await ElMessageBox.confirm(
      `归档后《${row.title}》将锁定为只读状态，可随时删除。确认归档？`,
      '归档确认', { confirmButtonText: '确认归档', type: 'warning' },
    )
  } catch { return }
  try {
    await lifecycleApi.archiveContract(row.id)
    ElMessage.success('已归档')
    loadContracts()
  } catch {
    ElMessage.error('归档失败')
  }
}

async function removeContract(row: any) {
  try {
    await ElMessageBox.confirm(
      `删除《${row.title}》及其全部版本与关键日期？此操作不可恢复。`,
      '删除确认', { confirmButtonText: '确认删除', type: 'error' },
    )
  } catch { return }
  try {
    await lifecycleApi.deleteContract(row.id)
    ElMessage.success('已删除')
    loadContracts()
  } catch {
    ElMessage.error('删除失败')
  }
}

async function draftContract() {
  if (!draftForm.value.description) return ElMessage.warning('请输入合同描述')
  loading.value = true
  try {
    const res = await service.post('/contract/lifecycle/draft', draftForm.value)
    draftResult.value = res.data
    if (res.data.contract_id) ElMessage.success('已起草并保存为草稿')
  } catch {
    ElMessage.error('起草失败')
  } finally {
    loading.value = false
  }
}

async function compareContracts() {
  loading.value = true
  try {
    const payload = { ...compareForm.value }
    if (!payload.contract_id) delete (payload as any).contract_id
    const res = await service.post('/contract/lifecycle/compare', payload)
    compareResult.value = res.data
    if (res.data.saved_version) ElMessage.success(`修改版已保存为 v${res.data.saved_version}`)
  } catch {
    ElMessage.error('对比失败')
  } finally {
    loading.value = false
  }
}

async function checkCompliance() {
  if (!complianceForm.value.contract_text) return ElMessage.warning('请输入合同文本')
  loading.value = true
  try {
    const res = await service.post('/contract/lifecycle/compliance', complianceForm.value)
    complianceResult.value = res.data
  } catch {
    ElMessage.error('检查失败')
  } finally {
    loading.value = false
  }
}

async function extractDates() {
  if (!dateText.value) return ElMessage.warning('请输入合同文本')
  loading.value = true
  try {
    const payload: any = { contract_text: dateText.value }
    if (dateContractId.value) payload.contract_id = dateContractId.value
    const res = await lifecycleApi.extractDates(payload)
    dateResult.value = res.data
  } catch {
    ElMessage.error('提取失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadContracts)
</script>

<style scoped>
.contract-lifecycle-view { padding: 24px; }
.view-header { margin-bottom: 20px; }
.view-header h2 { margin: 0 0 4px; color: var(--primary-color); }
.view-header p { margin: 0; color: var(--text-secondary); font-size: 14px; }
.result-section { margin-top: 20px; padding: 16px; background: var(--bg-white); border-radius: var(--radius-md); border: 1px solid var(--border-color); }
.result-meta { margin-bottom: 12px; display: flex; align-items: center; }
.toolbar { display: flex; gap: 12px; margin-bottom: 12px; }
.muted { color: var(--text-secondary, #999); font-size: 13px; }
.link { color: var(--primary-color, #409eff); cursor: pointer; }
.version-head { display: flex; align-items: center; gap: 8px; }
.review-summary { white-space: pre-wrap; font-size: 13px; padding: 12px; background: var(--bg-secondary, #f5f7fa); border-radius: 6px; max-height: 200px; overflow: auto; }
</style>
