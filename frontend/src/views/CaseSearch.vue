<template>
  <div class="case-search">
    <div class="page-container">
      <h2 class="section-title">案例检索</h2>
      <p class="section-desc">检索中国裁判文书，获取相关案例的裁判要旨和法律适用</p>

      <!-- 搜索栏 -->
      <div class="search-bar">
        <el-input
          v-model="keyword"
          size="large"
          placeholder="输入关键词搜索案例，例如：劳动合同纠纷、离婚财产分割、盗窃案..."
          :prefix-icon="Search"
          clearable
          @keyup.enter="handleSearch"
          @clear="handleClear"
        >
          <template #append>
            <el-button
              type="primary"
              :icon="Search"
              :loading="searching"
              @click="handleSearch"
            >
              搜索
            </el-button>
          </template>
        </el-input>
      </div>

      <!-- 筛选条件 -->
      <div class="filter-bar">
        <el-select v-model="caseType" placeholder="案件类型" clearable size="default" @change="handleSearch">
          <el-option label="全部" value="" />
          <el-option label="民事" value="民事" />
          <el-option label="刑事" value="刑事" />
          <el-option label="行政" value="行政" />
        </el-select>

        <el-select v-model="yearRange" placeholder="年份范围" clearable size="default" @change="handleSearch">
          <el-option label="全部年份" value="" />
          <el-option label="2024年" value="2024" />
          <el-option label="2023年" value="2023" />
          <el-option label="2022年" value="2022" />
          <el-option label="2021年" value="2021" />
          <el-option label="2020年及以前" value="2020" />
        </el-select>
      </div>

      <!-- AI 总结 -->
      <div v-if="aiSummary" class="ai-summary">
        <el-alert
          :title="`AI 分析：${aiSummary}`"
          type="info"
          :closable="false"
          show-icon
        />
      </div>

      <!-- 搜索结果 -->
      <div class="results-section" v-loading="searching">
        <div v-if="!hasSearched" class="search-empty">
          <el-empty description="请输入关键词开始搜索案例" :image-size="100" />
        </div>

        <div v-else-if="results.length === 0" class="search-empty">
          <el-empty description="未找到相关案例，请尝试其他关键词" :image-size="100" />
        </div>

        <div v-else class="results-list">
          <div class="result-count">
            找到 <strong>{{ totalResults }}</strong> 条相关案例
          </div>

          <div
            v-for="item in results"
            :key="item.id"
            class="result-card"
            @click="handleViewDetail(item)"
          >
            <div class="result-header">
              <h3 class="result-title">
                <el-icon><Document /></el-icon>
                {{ item.title }}
              </h3>
              <div class="result-meta-top">
                <el-tag v-if="item.case_type" size="small" :type="getCaseTypeTag(item.case_type)">
                  {{ item.case_type }}
                </el-tag>
                <span v-if="item.decision_date" class="result-date">
                  {{ item.decision_date }}
                </span>
              </div>
            </div>

            <div class="result-meta">
              <span v-if="item.case_number">
                <el-icon><Files /></el-icon>
                {{ item.case_number }}
              </span>
              <span v-if="item.court_name">
                <el-icon><OfficeBuilding /></el-icon>
                {{ item.court_name }}
              </span>
              <span v-if="item.cause_of_action">
                <el-icon><Collection /></el-icon>
                {{ item.cause_of_action }}
              </span>
            </div>

            <div class="result-content">
              <p>{{ item.summary }}</p>
            </div>

            <div v-if="item.key_points" class="key-points">
              <strong>裁判要旨：</strong>
              <p>{{ item.key_points }}</p>
            </div>

            <div v-if="item.referenced_laws" class="referenced-laws">
              <strong>引用法条：</strong>
              <span>{{ item.referenced_laws }}</span>
            </div>

            <div class="result-actions">
              <el-button type="primary" link size="small" @click.stop="handleViewDetail(item)">
                <el-icon><View /></el-icon>查看详情
              </el-button>
              <el-button type="primary" link size="small" @click.stop="handleCopyCase(item)">
                <el-icon><CopyDocument /></el-icon>复制摘要
              </el-button>
            </div>
          </div>
        </div>

        <!-- 分页 -->
        <div v-if="hasSearched && totalResults > 0" class="pagination-wrapper">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50]"
            :total="totalResults"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </div>
    </div>

    <!-- 案例详情对话框 -->
    <el-dialog
      v-model="detailDialogVisible"
      :title="selectedCase?.title"
      width="800px"
      :close-on-click-modal="false"
    >
      <div v-if="selectedCase" class="case-detail">
        <div class="detail-section">
          <h4>案件信息</h4>
          <p><strong>案号：</strong>{{ selectedCase.case_number }}</p>
          <p v-if="selectedCase.court_name"><strong>审理法院：</strong>{{ selectedCase.court_name }}</p>
          <p v-if="selectedCase.case_type"><strong>案件类型：</strong>{{ selectedCase.case_type }}</p>
          <p v-if="selectedCase.cause_of_action"><strong>案由：</strong>{{ selectedCase.cause_of_action }}</p>
          <p v-if="selectedCase.decision_date"><strong>裁判日期：</strong>{{ selectedCase.decision_date }}</p>
        </div>

        <div v-if="selectedCase.summary" class="detail-section">
          <h4>案件摘要</h4>
          <p>{{ selectedCase.summary }}</p>
        </div>

        <div v-if="selectedCase.key_points" class="detail-section">
          <h4>裁判要旨</h4>
          <p>{{ selectedCase.key_points }}</p>
        </div>

        <div v-if="selectedCase.referenced_laws" class="detail-section">
          <h4>引用法条</h4>
          <p>{{ selectedCase.referenced_laws }}</p>
        </div>

        <div v-if="selectedCase.judgment_result" class="detail-section">
          <h4>裁判结果</h4>
          <p>{{ selectedCase.judgment_result }}</p>
        </div>

        <div v-if="selectedCase.full_text" class="detail-section">
          <h4>裁判文书全文</h4>
          <div class="full-text">{{ selectedCase.full_text }}</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  Document,
  Files,
  OfficeBuilding,
  Collection,
  View,
  CopyDocument,
} from '@element-plus/icons-vue'
import { useClipboard } from '@vueuse/core'
import { caseApi, type CaseItem, type CaseDetail } from '@/api/cases'

const { copy } = useClipboard()

const keyword = ref('')
const caseType = ref('')
const yearRange = ref('')
const searching = ref(false)
const hasSearched = ref(false)
const results = ref<CaseItem[]>([])
const totalResults = ref(0)
const aiSummary = ref('')
const currentPage = ref(1)
const pageSize = ref(20)

const detailDialogVisible = ref(false)
const selectedCase = ref<CaseDetail | null>(null)

function getCaseTypeTag(type: string) {
  const map: Record<string, string> = {
    '民事': '',
    '刑事': 'danger',
    '行政': 'warning',
  }
  return map[type] || 'info'
}

async function handleSearch() {
  if (!keyword.value.trim()) {
    ElMessage.warning('请输入搜索关键词')
    return
  }

  currentPage.value = 1
  searching.value = true
  hasSearched.value = true

  try {
    const params: any = {
      query: keyword.value,
      top_k: pageSize.value,
    }

    if (caseType.value) {
      params.case_type = caseType.value
    }

    if (yearRange.value) {
      const year = parseInt(yearRange.value)
      if (year === 2020) {
        params.year_to = 2020
      } else {
        params.year_from = year
        params.year_to = year
      }
    }

    const res = await caseApi.searchCases(params)
    results.value = res.data.results
    totalResults.value = res.data.total
    aiSummary.value = res.data.ai_summary || ''
  } catch (error) {
    results.value = []
    totalResults.value = 0
    aiSummary.value = ''
  } finally {
    searching.value = false
  }
}

function handlePageChange() {
  handleSearch()
}

function handleSizeChange() {
  currentPage.value = 1
  handleSearch()
}

function handleClear() {
  results.value = []
  totalResults.value = 0
  hasSearched.value = false
  aiSummary.value = ''
  currentPage.value = 1
}

async function handleViewDetail(item: CaseItem) {
  try {
    const res = await caseApi.getCaseDetail(item.id)
    selectedCase.value = res.data
    detailDialogVisible.value = true
  } catch (error) {
    ElMessage.error('获取案例详情失败')
  }
}

function handleCopyCase(item: CaseItem) {
  const text = `${item.title}\n案号：${item.case_number}\n${item.summary || ''}\n裁判要旨：${item.key_points || ''}`
  copy(text)
  ElMessage.success('已复制到剪贴板')
}
</script>

<style scoped>
.case-search {
  height: 100%;
  overflow-y: auto;
}

.page-container {
  max-width: 1000px;
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

.search-bar {
  margin-bottom: 16px;
}

.filter-bar {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
}

.ai-summary {
  margin-bottom: 20px;
}

.search-empty {
  padding: 60px 0;
}

.result-count {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.results-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.result-card {
  background-color: var(--bg-white);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 20px;
  transition: all 0.2s;
  cursor: pointer;
}

.result-card:hover {
  box-shadow: var(--shadow-md);
  border-color: var(--primary-light);
}

.result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.result-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
}

.result-meta-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.result-date {
  font-size: 13px;
  color: var(--text-secondary);
}

.result-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 12px;
  font-size: 13px;
  color: var(--text-secondary);
}

.result-meta span {
  display: flex;
  align-items: center;
  gap: 4px;
}

.result-content {
  margin-bottom: 12px;
}

.result-content p {
  font-size: 14px;
  color: var(--text-regular);
  line-height: 1.7;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.key-points, .referenced-laws {
  background-color: var(--bg-color);
  padding: 12px;
  border-radius: var(--radius-sm);
  margin-bottom: 12px;
  font-size: 13px;
}

.key-points strong, .referenced-laws strong {
  color: var(--primary-color);
  display: block;
  margin-bottom: 4px;
}

.key-points p {
  margin: 0;
  color: var(--text-regular);
  line-height: 1.6;
}

.referenced-laws span {
  color: var(--text-regular);
}

.result-actions {
  display: flex;
  gap: 16px;
}

/* Case detail dialog */
.case-detail {
  max-height: 70vh;
  overflow-y: auto;
}

.detail-section {
  margin-bottom: 24px;
}

.detail-section h4 {
  font-size: 15px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0 0 12px 0;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border-color);
}

.detail-section p {
  font-size: 14px;
  color: var(--text-regular);
  line-height: 1.7;
  margin: 8px 0;
}

.full-text {
  background-color: var(--bg-color);
  padding: 16px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  max-height: 400px;
  overflow-y: auto;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: 24px;
  padding: 16px 0;
}
</style>
