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

      <!-- 筛选条件 -->
      <div class="filter-bar">
        <el-radio-group v-model="filterType" size="small" @change="handleSearch">
          <el-radio-button label="all">全部</el-radio-button>
          <el-radio-button label="constitutional">宪法</el-radio-button>
          <el-radio-button label="criminal">刑法</el-radio-button>
          <el-radio-button label="civil">民法</el-radio-button>
          <el-radio-button label="administrative">行政法</el-radio-button>
          <el-radio-button label="commercial">商法</el-radio-button>
          <el-radio-button label="labor">劳动法</el-radio-button>
          <el-radio-button label="intellectual_property">知识产权</el-radio-button>
        </el-radio-group>
      </div>

      <!-- 搜索结果 -->
      <div class="results-section" v-loading="searching">
        <div v-if="!hasSearched" class="search-empty">
          <el-empty description="请输入关键词开始搜索" :image-size="100" />
        </div>

        <div v-else-if="results.length === 0" class="search-empty">
          <el-empty description="未找到相关法规，请尝试其他关键词" :image-size="100" />
        </div>

        <div v-else class="results-list">
          <div class="result-count">
            找到 <strong>{{ totalResults }}</strong> 条相关结果
          </div>

          <div
            v-for="item in results"
            :key="item.article_number + item.law_name"
            class="result-card"
          >
            <div class="result-header">
              <h3 class="result-title">
                <el-icon><Document /></el-icon>
                {{ item.law_name }} - {{ formatArticleNumber(item.article_number) }}
              </h3>
              <el-tag size="small" type="success">
                {{ item.effective_status }}
              </el-tag>
            </div>

            <div class="result-meta">
              <span v-if="item.category">
                <el-icon><OfficeBuilding /></el-icon>
                {{ item.category }}
              </span>
              <span v-if="item.publish_year">
                <el-icon><Calendar /></el-icon>
                发布年份：{{ item.publish_year }}
              </span>
              <span v-if="item.relevance_score > 0">
                <el-icon><Clock /></el-icon>
                相关度：{{ (item.relevance_score * 100).toFixed(0) }}%
              </span>
            </div>

            <div class="result-content">
              <p>{{ item.article_content }}</p>
            </div>

            <div class="result-actions">
              <el-button type="primary" link size="small" @click="handleCopy(item)">
                <el-icon><CopyDocument /></el-icon>复制条文
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
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Search,
  Document,
  OfficeBuilding,
  Calendar,
  Clock,
  CopyDocument,
} from '@element-plus/icons-vue'
import { useClipboard } from '@vueuse/core'
import MarkdownIt from 'markdown-it'
import { lawApi } from '@/api/law'

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
const filterType = ref('all')
const searching = ref(false)
const hasSearched = ref(false)
const results = ref<LawResult[]>([])
const totalResults = ref(0)
const formattedOutput = ref('')
const currentPage = ref(1)
const pageSize = ref(20)

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
    const category = filterType.value === 'all' ? undefined : filterType.value
    const res = await lawApi.searchLaws({
      query: keyword.value,
      category: category,
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

.filter-bar {
  margin-bottom: 24px;
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
