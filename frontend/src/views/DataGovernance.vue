<template>
  <div class="data-governance">
    <div class="page-header">
      <div>
        <h2>数据资产治理</h2>
        <p class="subtitle">
          语料来源与可援引性登记 · 合规审计留痕与导出 · 租户数据隔离
        </p>
      </div>
      <el-button :loading="loading" @click="refreshAll">
        <el-icon><Refresh /></el-icon>
        <span style="margin-left: 4px">刷新</span>
      </el-button>
    </div>

    <!-- 概览卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">可援引权威语料</div>
          <div class="stat-value primary">{{ formatNumber(registry.citable_total) }}</div>
          <div class="stat-hint">已获授权、可作为法律依据引用</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">目标条数</div>
          <div class="stat-value">{{ formatNumber(registry.target) }}</div>
          <div class="stat-hint">商业化目标规模</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">非可援引语料</div>
          <div class="stat-value warning">{{ formatNumber(registry.non_citable_total) }}</div>
          <div class="stat-hint">仅用于评测/检索兜底，不得直接援引</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">目标完成度</div>
          <div class="stat-value" :class="completionClass">
            {{ registry.completion_pct }}%
          </div>
          <el-progress
            :percentage="Math.min(Number(registry.completion_pct) || 0, 100)"
            :stroke-width="8"
            :show-text="false"
            :status="completionStatus"
            style="margin-top: 8px"
          />
        </el-card>
      </el-col>
    </el-row>

    <!-- 语料登记账本 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span>语料来源登记账本</span>
          <el-tag size="small" type="info">
            共 {{ registry.items.length }} 个来源
          </el-tag>
        </div>
      </template>
      <el-table :data="registry.items" stripe v-loading="loading" empty-text="暂无登记数据">
        <el-table-column prop="display_name" label="来源" min-width="180" show-overflow-tooltip />
        <el-table-column prop="provenance" label="来源性质" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="provenanceType(row.provenance)">
              {{ provenanceLabel(row.provenance) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="可援引" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.citable ? 'success' : 'danger'">
              {{ row.citable ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="record_count" label="条数" width="130" align="right">
          <template #default="{ row }">
            {{ formatNumber(row.record_count) }}
          </template>
        </el-table-column>
        <el-table-column prop="storage" label="存储" width="120" show-overflow-tooltip />
        <el-table-column prop="commercial_use" label="商业使用" min-width="160" show-overflow-tooltip />
        <el-table-column prop="note" label="备注" min-width="200" show-overflow-tooltip />
      </el-table>
    </el-card>

    <!-- 合规审计 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span>合规审计日志</span>
          <div class="header-actions">
            <el-select v-model="auditFilter.action" placeholder="全部动作" clearable
                       size="small" style="width: 170px" @change="loadAuditLogs">
              <el-option label="AI 引用校验" value="ai.citation_verify" />
              <el-option label="会话创建" value="create_conversation" />
              <el-option label="文档上传" value="upload_document" />
            </el-select>
            <el-button size="small" :loading="exporting" @click="exportCsv">
              <el-icon><Download /></el-icon>
              <span style="margin-left: 4px">导出 CSV</span>
            </el-button>
            <el-button size="small" :loading="exporting" @click="exportJson">
              导出 JSON
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="auditLogs" stripe v-loading="auditLoading" empty-text="暂无审计记录">
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="action" label="操作 / AI 动作" width="180" />
        <el-table-column prop="user_id" label="操作人" width="120" show-overflow-tooltip />
        <el-table-column prop="request_summary" label="摘要 / 引用校验结果" min-width="280"
                         show-overflow-tooltip />
        <el-table-column prop="status_code" label="状态" width="80" align="center" />
        <el-table-column prop="duration_ms" label="耗时" width="90" align="right">
          <template #default="{ row }">
            {{ row.duration_ms != null ? row.duration_ms + 'ms' : '-' }}
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-bar">
        <el-pagination
          layout="total, prev, pager, next"
          :total="auditTotal"
          :page-size="auditPageSize"
          :current-page="auditPage"
          @current-change="onAuditPageChange"
        />
      </div>
    </el-card>

    <!-- 租户 -->
    <el-card shadow="never" class="section-card">
      <template #header>
        <div class="card-header">
          <span>租户与数据隔离</span>
          <el-tag size="small" type="warning">
            仅平台管理员可见
          </el-tag>
        </div>
      </template>
      <el-table :data="tenants" stripe v-loading="tenantLoading"
                empty-text="无权限或暂无租户（需平台管理员账号）">
        <el-table-column prop="name" label="租户名称" min-width="160" />
        <el-table-column prop="code" label="短代码" width="140" />
        <el-table-column prop="plan" label="版本" width="120" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="user_count" label="用户数" width="100" align="right" />
        <el-table-column prop="max_users" label="上限" width="90" align="right" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import adminApi from '@/api/admin'

const loading = ref(false)
const auditLoading = ref(false)
const tenantLoading = ref(false)
const exporting = ref(false)

const registry = reactive<{
  items: any[]
  citable_total: number
  non_citable_total: number
  synthetic_total: number
  target: number
  completion_pct: number | string
}>({
  items: [],
  citable_total: 0,
  non_citable_total: 0,
  synthetic_total: 0,
  target: 100000000,
  completion_pct: 0,
})

const auditLogs = ref<any[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditPageSize = ref(20)
const auditFilter = reactive<{ action?: string }>({})

const tenants = ref<any[]>([])

const completionStatus = computed(() => {
  const pct = Number(registry.completion_pct) || 0
  if (pct >= 100) return 'success'
  if (pct >= 30) return 'warning'
  return 'exception'
})

const completionClass = computed(() => {
  const pct = Number(registry.completion_pct) || 0
  return pct >= 100 ? 'success' : pct >= 30 ? 'warning' : 'danger'
})

function formatNumber(value: number | string | null | undefined) {
  const n = Number(value ?? 0)
  if (!Number.isFinite(n)) return '0'
  return n.toLocaleString('zh-CN')
}

function formatTime(value: string) {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleString('zh-CN')
  } catch {
    return value
  }
}

function provenanceLabel(value: string) {
  return (
    { authoritative: '权威授权', open_source: '开放来源', synthetic: '合成数据' }[value] ||
    value ||
    '未知'
  )
}

function provenanceType(value: string) {
  return (
    { authoritative: 'success', open_source: 'warning', synthetic: 'info' }[value] || 'info'
  ) as any
}

async function loadRegistry() {
  loading.value = true
  try {
    const { data } = await adminApi.getCorpusRegistry()
    registry.items = data.items || []
    registry.citable_total = data.citable_total || 0
    registry.non_citable_total = data.non_citable_total || 0
    registry.synthetic_total = data.synthetic_total || 0
    registry.target = data.target || 100000000
    registry.completion_pct = data.completion_pct ?? 0
  } catch (e: any) {
    ElMessage.warning('数据资产登记获取失败：' + (e?.message || '未知错误'))
  } finally {
    loading.value = false
  }
}

async function loadAuditLogs() {
  auditLoading.value = true
  try {
    const { data } = await adminApi.listAuditLogs({
      page: auditPage.value,
      page_size: auditPageSize.value,
      action: auditFilter.action || undefined,
    })
    auditLogs.value = data.logs || []
    auditTotal.value = data.total || 0
  } catch (e: any) {
    ElMessage.warning('审计日志获取失败（可能需要管理员权限）')
  } finally {
    auditLoading.value = false
  }
}

function onAuditPageChange(page: number) {
  auditPage.value = page
  loadAuditLogs()
}

async function loadTenants() {
  tenantLoading.value = true
  try {
    const { data } = await adminApi.listTenants({ page: 1, page_size: 50 })
    tenants.value = data.tenants || []
  } catch {
    tenants.value = []
  } finally {
    tenantLoading.value = false
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(url)
}

async function exportCsv() {
  exporting.value = true
  try {
    const { data } = await adminApi.exportAuditLogs({
      format: 'csv',
      action: auditFilter.action || undefined,
    })
    downloadBlob(new Blob([data], { type: 'text/csv;charset=utf-8' }),
      `audit_logs_${Date.now()}.csv`)
    ElMessage.success('审计日志已导出（CSV）')
  } catch {
    ElMessage.error('导出失败，请确认管理员权限')
  } finally {
    exporting.value = false
  }
}

async function exportJson() {
  exporting.value = true
  try {
    const { data } = await adminApi.exportAuditLogs({
      format: 'json',
      action: auditFilter.action || undefined,
    })
    downloadBlob(new Blob([data], { type: 'application/json;charset=utf-8' }),
      `audit_logs_${Date.now()}.json`)
    ElMessage.success('审计日志已导出（JSON）')
  } catch {
    ElMessage.error('导出失败，请确认管理员权限')
  } finally {
    exporting.value = false
  }
}

function refreshAll() {
  loadRegistry()
  loadAuditLogs()
  loadTenants()
}

onMounted(() => {
  loadRegistry()
  loadAuditLogs()
  loadTenants()
})
</script>

<style scoped>
.data-governance {
  padding: 20px 24px 40px;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0 0 4px;
  font-size: 20px;
  color: #1a365d;
}
.subtitle {
  margin: 0;
  font-size: 13px;
  color: #7a8699;
}
.stat-row {
  margin-bottom: 16px;
}
.stat-card {
  border-radius: 10px;
}
.stat-label {
  font-size: 13px;
  color: #7a8699;
  margin-bottom: 6px;
}
.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: #1a365d;
}
.stat-value.primary { color: #2b6cb0; }
.stat-value.success { color: #2f9e6e; }
.stat-value.warning { color: #d69e2e; }
.stat-value.danger { color: #c53030; }
.stat-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #a0aec0;
}
.section-card {
  margin-bottom: 16px;
  border-radius: 10px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pagination-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
