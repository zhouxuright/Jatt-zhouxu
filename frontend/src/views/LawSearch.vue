<template>
  <div class="law-search">
    <div class="page-container">
      <h2 class="section-title">法规检索</h2>
      <p class="section-desc">检索中国法律法规，快速定位相关法条</p>

      <!-- 搜索栏 -->
      <div class="search-bar">
        <el-input
          v-model="keyword"
          size="large"
          placeholder="输入关键词搜索法律法规，例如：劳动合同法、民法典、知识产权..."
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

      <!-- 筛选条件：效力级别（基于返回结果客户端过滤，检索结果的 category 字段即法规效力级别） -->
      <SearchFilterBar v-model="filterValues" :filters="lawFilterConfigs" />

      <!-- 搜索结果 -->
      <div class="results-section" v-loading="searching">
        <div v-if="!hasSearched" class="search-empty">
          <el-empty description="请输入关键词开始搜索" :image-size="100" />
        </div>

        <div v-else-if="filteredResults.length === 0" class="search-empty">
          <el-empty description="当前筛选条件下没有匹配结果，可调整关键词或清除筛选" :image-size="100">
            <el-button v-if="hasActiveFilter" type="primary" plain size="small" @click="clearFilters">
              清除筛选条件
            </el-button>
          </el-empty>
        </div>

        <div v-else class="results-list">
          <div class="result-count">
            找到 <strong>{{ filteredResults.length }}</strong> 条相关结果
            <span v-if="hasActiveFilter && filteredResults.length !== results.length" class="filter-note">
              （已从 {{ results.length }} 条中按效力级别筛选）
            </span>
          </div>

          <div
            v-for="item in filteredResults"
            :key="item.article_number + item.law_name"
            class="result-card"
          >
            <div class="result-header">
              <h3 class="result-title">
                <el-icon><Document /></el-icon>
                {{ item.law_name }} - {{ formatArticleNumber(item.article_number) }}
              </h3>
              <div class="result-header-tags">
                <el-tag v-if="item.category" size="small" type="info">{{ item.category }}</el-tag>
                <el-tag size="small" type="success">{{ statusLabel(item.effective_status) }}</el-tag>
              </div>
            </div>

            <div class="result-meta">
              <span v-if="item.publish_year">
                <el-icon><Calendar /></el-icon>
                发布年份：{{ item.publish_year }}
              </span>
              <span v-if="item.relevance_score > 0">
                <el-icon><Clock /></el-icon>
                相关度：{{ (item.relevance_score * 100).toFixed(0) }}%
              </span>
            </div>

            <div class="result-content" :class="{ expanded: isExpanded(item) }">
              <p>{{ item.article_content }}</p>
            </div>

            <div class="result-actions">
              <el-button type="primary" link size="small" @click="handleCopy(item)">
                <el-icon><CopyDocument /></el-icon>复制条文
              </el-button>
              <el-button v-if="isLongContent(item)" type="primary" link size="small" @click="toggleExpand(item)">
                {{ isExpanded(item) ? '收起' : '展开全文' }}
              </el-button>
            </div>
          </div>
        </div>

        <!-- 格式化输出 -->
        <div v-if="formattedOutput" class="formatted-output">
          <el-divider />
          <h3>AI 分析总结</h3>
          <div class="output-content" v-html="renderMarkdown(formattedOutput)"></div>
        </div>

        <!-- 分页 -->
        <div v-if="hasSearched && totalResults > 0" class="pagination-wrapper">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50]"
            :total="totalResults"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="searchLaws"
            @size-change="handleSizeChange"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  Document,
  Calendar,
  Clock,
  CopyDocument,
} from '@element-plus/icons-vue'
import { useClipboard } from '@vueuse/core'
import MarkdownIt from 'markdown-it'
import { lawApi } from '@/api/law'
import SearchFilterBar, { type FilterConfig } from '@/components/SearchFilterBar.vue'

const { copy } = useClipboard()
const md = new MarkdownIt({ html: false, linkify: true, typographer: true })

interface LawResult {
  law_name: string
  article_number: string
  article_content: string
  relevance_score: number
  effective_status: string
  category: string
  publish_year: string
  source: string
}

const keyword = ref('')
const searching = ref(false)
const hasSearched = ref(false)
const results = ref<LawResult[]>([])
const totalResults = ref(0)
const formattedOutput = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const expandedCards = ref<Record<string, boolean>>({})

// 效力级别筛选：检索结果的 category 字段实际存的是法规效力级别（法律/行政法规/司法解释...）
const filterValues = ref<Record<string, string>>({ level: '' })

const lawFilterConfigs: FilterConfig[] = [
  {
    key: 'level',
    label: '效力级别',
    placeholder: '全部级别',
    width: 170,
    options: [
      { label: '法律', value: '法律' },
      { label: '行政法规', value: '行政法规' },
      { label: '司法解释', value: '司法解释' },
      { label: '部门规章', value: '部门规章' },
      { label: '地方性法规', value: '地方性法规' },
      { label: '规范性文件', value: '规范性文件' },
    ],
  },
]

const hasActiveFilter = computed(() => filterValues.value.level !== '')

const filteredResults = computed(() => {
  const level = filterValues.value.level
  if (!level) return results.value
  return results.value.filter((r) => r.category === level)
})

function clearFilters() {
  filterValues.value = { level: '' }
}

function statusLabel(s: string): string {
  if (!s || s === 'active' || s === '有效') return '现行有效'
  return s
}

function cardKey(item: LawResult): string {
  return `${item.law_name}|${item.article_number}`
}

function isExpanded(item: LawResult): boolean {
  return !!expandedCards.value[cardKey(item)]
}

function isLongContent(item: LawResult): boolean {
  return (item.article_content || '').length > 180
}

function toggleExpand(item: LawResult) {
  const key = cardKey(item)
  expandedCards.value[key] = !expandedCards.value[key]
}

function handleSearch() {
  if (!keyword.value.trim()) {
    ElMessage.warning('请输入搜索关键词')
    return
  }
  currentPage.value = 1
  searchLaws()
}

async function searchLaws() {
  searching.value = true
  hasSearched.value = true

  try {
    const res = await lawApi.searchLaws({
      query: keyword.value,
      top_k: pageSize.value,
    })
    results.value = res.data.results || []
    totalResults.value = res.data.total || 0
    formattedOutput.value = res.data.formatted_output || ''
  } catch {
    results.value = []
    totalResults.value = 0
    formattedOutput.value = ''
  } finally {
    searching.value = false
  }
}

function handleSizeChange() {
  currentPage.value = 1
  searchLaws()
}

function handleClear() {
  results.value = []
  totalResults.value = 0
  hasSearched.value = false
  formattedOutput.value = ''
  currentPage.value = 1
  clearFilters()
  expandedCards.value = {}
}

function handleCopy(item: LawResult) {
  const text = `${item.law_name} ${formatArticleNumber(item.article_number)}：${item.article_content}`
  copy(text)
  ElMessage.success('已复制到剪贴板')
}

function formatArticleNumber(raw: string): string {
  if (!raw) return ''
  const s = raw.trim().replace(/_\d+$/, '').trim()
  if (!s) return raw
  if (/^第[零〇一二三四五六七八九十百千万0-9]+条$/.test(s)) return s
  const core = s.replace(/^第/, '').replace(/条$/, '')
  if (/^[零〇一二三四五六七八九十百千万0-9]+$/.test(core)) return `第${core}条`
  return raw
}

function renderMarkdown(content: string) {
  return md.render(content)
}
</script>

<style scoped>
.law-search {
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

.search-bar {
  margin-bottom: 16px;
}

.filter-note {
  font-size: 13px;
  color: var(--text-secondary);
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
  transition: box-shadow 0.2s;
}

.result-card:hover {
  box-shadow: var(--shadow-md);
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.result-header-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.result-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
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
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.result-content.expanded p {
  display: block;
  -webkit-line-clamp: unset;
  overflow: visible;
}

.result-actions {
  display: flex;
  gap: 16px;
}

.formatted-output {
  margin-top: 24px;
  background-color: var(--bg-white);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  padding: 20px;
}

.formatted-output h3 {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0 0 12px 0;
}

.output-content {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-regular);
}

.output-content :deep(p) {
  margin: 0 0 8px 0;
}

.output-content :deep(ul), .output-content :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: 24px;
  padding: 16px 0;
}
</style>
