<template>
  <div class="dashboard-view">
    <!-- 欢迎横幅 -->
    <div class="welcome-banner">
      <div class="welcome-text">
        <h2>{{ greeting }}，{{ authStore.user?.username || '用户' }}</h2>
        <p>{{ todayText }} · 今天有 {{ stats.daily_conversations }} 个新对话、{{ stats.daily_messages }} 条新消息</p>
      </div>
      <el-button type="primary" size="large" round @click="router.push('/chat')">
        <el-icon style="margin-right: 6px"><ChatDotRound /></el-icon>
        开始咨询
      </el-button>
    </div>

    <!-- 快捷操作 -->
    <div class="section-title">快捷操作</div>
    <div class="quick-grid">
      <div v-for="item in quickActions" :key="item.path" class="quick-card" @click="router.push(item.path)">
        <div class="quick-icon" :style="{ backgroundColor: item.bg }">
          <el-icon :size="26" :color="item.color"><component :is="item.icon" /></el-icon>
        </div>
        <div class="quick-info">
          <div class="quick-title">{{ item.title }}</div>
          <div class="quick-desc">{{ item.desc }}</div>
        </div>
        <el-icon class="quick-arrow"><ArrowRight /></el-icon>
      </div>
    </div>

    <!-- 数据概览 -->
    <div class="section-title">数据概览</div>
    <el-row :gutter="16" class="stat-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">知识库规模</div>
          <div class="stat-value primary">{{ formatNumber(knowledgeTotal) }}</div>
          <div class="stat-hint">法规 / 案例 / 问答语料总量</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">累计合同审查</div>
          <div class="stat-value">{{ formatNumber(stats.total_reviews) }}</div>
          <div class="stat-hint">文档总量 {{ formatNumber(stats.total_documents) }} 份</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">智能对话</div>
          <div class="stat-value">{{ formatNumber(stats.total_conversations) }}</div>
          <div class="stat-hint">{{ formatNumber(stats.total_messages) }} 条消息 · 满意度 {{ stats.average_feedback_score || '—' }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-label">合规预警</div>
          <div class="stat-value" :class="openAlertTotal > 0 ? 'warning' : ''">
            {{ formatNumber(openAlertTotal) }}
          </div>
          <div class="stat-hint">{{ openAlertTotal > 0 ? '有待处理告警' : '暂无待处理告警' }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 待办与最近动态 -->
    <el-row :gutter="16" class="bottom-grid">
      <el-col :xs="24" :lg="10">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-header">
              <span>待办 · 合规预警</span>
              <el-badge v-if="openAlertTotal > 0" :value="openAlertTotal" :max="99" class="alert-badge" />
              <el-button size="small" text type="primary" @click="router.push('/compliance')">
                查看全部
              </el-button>
            </div>
          </template>
          <div v-if="alerts.length === 0" class="empty-tip">
            <el-icon :size="36" color="#c0c4cc"><CircleCheckFilled /></el-icon>
            <p>暂无待处理预警</p>
          </div>
          <div
            v-for="alert in alerts"
            :key="alert.id"
            class="todo-item"
            @click="router.push('/compliance')"
          >
            <el-tag :type="riskTagType(alert.risk_level)" size="small" effect="dark">
              {{ riskLabel(alert.risk_level) }}
            </el-tag>
            <div class="todo-body">
              <div class="todo-title">{{ alert.regulation?.title || '法规变更' }}</div>
              <div class="todo-meta">
                命中「{{ alert.matched_keyword }}」 · {{ alert.watchlist_name }} · {{ formatDate(alert.created_at) }}
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="7">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-header">
              <span>最近对话</span>
              <el-button size="small" text type="primary" @click="router.push('/chat')">
                查看全部
              </el-button>
            </div>
          </template>
          <div v-if="conversations.length === 0" class="empty-tip">
            <el-icon :size="36" color="#c0c4cc"><ChatDotRound /></el-icon>
            <p>暂无对话记录</p>
          </div>
          <div
            v-for="conv in conversations"
            :key="conv.id"
            class="recent-item"
            @click="router.push('/chat')"
          >
            <el-icon class="recent-icon"><ChatDotRound /></el-icon>
            <div class="recent-body">
              <div class="recent-title">{{ conv.title || '新对话' }}</div>
              <div class="recent-meta">{{ formatDate(conv.updated_at || conv.created_at) }}</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="7">
        <el-card shadow="never" class="list-card">
          <template #header>
            <div class="card-header">
              <span>最近合同审查</span>
              <el-button size="small" text type="primary" @click="router.push('/contract-review')">
                查看全部
              </el-button>
            </div>
          </template>
          <div v-if="reviews.length === 0" class="empty-tip">
            <el-icon :size="36" color="#c0c4cc"><Document /></el-icon>
            <p>暂无审查记录</p>
          </div>
          <div
            v-for="review in reviews"
            :key="review.id"
            class="recent-item"
            @click="router.push('/contract-review')"
          >
            <el-icon class="recent-icon"><Document /></el-icon>
            <div class="recent-body">
              <div class="recent-title">
                {{ review.summary || `审查报告 #${String(review.id).slice(0, 8)}` }}
              </div>
              <div class="recent-meta">
                <span v-if="review.overall_score != null" :style="{ color: scoreColor(review.overall_score) }">
                  风险评分 {{ review.overall_score }}
                </span>
                <span v-else>已生成</span>
                · {{ formatDate(review.created_at) }}
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { analyticsApi } from '@/api/analytics'
import { chatApi } from '@/api/chat'
import { contractApi } from '@/api/contract'
import request, { dataApi } from '@/api/index'

const router = useRouter()
const authStore = useAuthStore()

const quickActions = [
  {
    path: '/contract-review',
    title: '合同审查',
    desc: '上传合同，AI 识别 8 类风险',
    icon: 'Document',
    color: '#1a365d',
    bg: '#ebf4ff',
  },
  {
    path: '/document-generate',
    title: '文书生成',
    desc: '起诉状 / 答辩状 / 律师函',
    icon: 'EditPen',
    color: '#38a169',
    bg: '#f0fff4',
  },
  {
    path: '/chat',
    title: '智能对话',
    desc: '法律咨询 · 法条溯源',
    icon: 'ChatDotRound',
    color: '#2b6cb0',
    bg: '#ebf8ff',
  },
  {
    path: '/compliance',
    title: '合规扫描',
    desc: '企业风险评估与法规监测',
    icon: 'Lock',
    color: '#dd6b20',
    bg: '#fffaf0',
  },
]

interface DashboardStats {
  total_users: number
  total_conversations: number
  total_messages: number
  total_documents: number
  total_reviews: number
  average_feedback_score: number | string
  daily_conversations: number
  daily_messages: number
}

interface ComplianceAlert {
  id: string
  risk_level: string
  matched_keyword: string
  status: string
  created_at: string
  watchlist_name: string
  regulation?: { title?: string }
}

interface ReviewItem {
  id: string
  overall_score: number | null
  summary: string | null
  created_at: string
}

const stats = reactive<DashboardStats>({
  total_users: 0,
  total_conversations: 0,
  total_messages: 0,
  total_documents: 0,
  total_reviews: 0,
  average_feedback_score: 0,
  daily_conversations: 0,
  daily_messages: 0,
})
const knowledgeTotal = ref(0)
const openAlertTotal = ref(0)
const alerts = ref<ComplianceAlert[]>([])
const conversations = ref<Array<{ id: string; title: string; created_at: string; updated_at: string }>>([])
const reviews = ref<ReviewItem[]>([])

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '凌晨好'
  if (hour < 9) return '早上好'
  if (hour < 12) return '上午好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const todayText = computed(() => {
  const d = new Date()
  const weekdays = ['日', '一', '二', '三', '四', '五', '六']
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 星期${weekdays[d.getDay()]}`
})

function formatNumber(n: number | null | undefined): string {
  if (n == null) return '—'
  return Number(n).toLocaleString('zh-CN')
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

function riskLabel(level: string): string {
  return { high: '高风险', medium: '中风险', low: '低风险' }[level] || '关注'
}

function riskTagType(level: string): 'danger' | 'warning' | 'info' {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  return 'info'
}

function scoreColor(score: number): string {
  if (score >= 70) return 'var(--danger-color)'
  if (score >= 40) return 'var(--warning-color)'
  return 'var(--success-color)'
}

// Dashboard data cache (sessionStorage, TTL 1 minute)
const DASHBOARD_CACHE_KEY = 'dashboard_cache'
const DASHBOARD_CACHE_TTL = 60 * 1000 // 1 minute

async function loadAll() {
  // Check cache first
  try {
    const cached = sessionStorage.getItem(DASHBOARD_CACHE_KEY)
    if (cached) {
      const { data, timestamp } = JSON.parse(cached)
      if (Date.now() - timestamp < DASHBOARD_CACHE_TTL) {
        Object.assign(stats, data.stats || {})
        knowledgeTotal.value = data.knowledgeTotal || 0
        openAlertTotal.value = data.openAlertTotal || 0
        alerts.value = data.alerts || []
        conversations.value = data.conversations || []
        reviews.value = data.reviews || []
        return
      }
    }
  } catch { /* ignore cache parse errors */ }

  const tasks: Promise<void>[] = [
    analyticsApi.getDashboard().then((res) => {
      Object.assign(stats, res.data)
    }),
    dataApi.getStats().then((res) => {
      knowledgeTotal.value = Number(res.data?.total_records_in_db) || 0
    }),
    request
      .get('/compliance/alerts', { params: { status: 'open', page: 1, page_size: 5 } })
      .then((res) => {
        openAlertTotal.value = Number(res.data?.total) || 0
        alerts.value = res.data?.items || []
      }),
    chatApi.getConversations(1, 5).then((res) => {
      conversations.value = res.data?.conversations || []
    }),
    contractApi.getReviewHistory(1, 5).then((res) => {
      reviews.value = res.data?.reviews || []
    }),
  ]
  await Promise.allSettled(tasks)

  // Save to cache
  try {
    sessionStorage.setItem(DASHBOARD_CACHE_KEY, JSON.stringify({
      data: {
        stats: { ...stats },
        knowledgeTotal: knowledgeTotal.value,
        openAlertTotal: openAlertTotal.value,
        alerts: alerts.value,
        conversations: conversations.value,
        reviews: reviews.value,
      },
      timestamp: Date.now(),
    }))
  } catch { /* ignore storage quota errors */ }
}

onMounted(loadAll)
</script>

<style scoped>
.dashboard-view {
  padding: 24px;
  max-width: 1440px;
  margin: 0 auto;
}

/* 欢迎横幅 */
.welcome-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 28px 32px;
  margin-bottom: 24px;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-light) 100%);
  color: #fff;
  box-shadow: var(--shadow-md);
}

.welcome-text h2 {
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 6px;
}

.welcome-text p {
  margin: 0;
  font-size: 13px;
  opacity: 0.85;
}

/* 分区标题 */
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 4px 0 12px;
}

/* 快捷操作宫格 */
.quick-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.quick-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px;
  background: var(--bg-white);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all 0.25s ease;
}

.quick-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-lg);
  border-color: var(--primary-lighter);
}

.quick-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.quick-info {
  flex: 1;
  min-width: 0;
}

.quick-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.quick-desc {
  font-size: 12px;
  color: var(--text-regular);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.quick-arrow {
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* 数据概览 */
.stat-row {
  margin-bottom: 24px;
}

.stat-card {
  border-radius: var(--radius-lg);
}

.stat-label {
  font-size: 13px;
  color: var(--text-regular);
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 6px 0 4px;
  font-variant-numeric: tabular-nums;
}

.stat-value.primary {
  color: var(--primary-light);
}

.stat-value.warning {
  color: var(--warning-color);
}

.stat-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

/* 底部列表区 */
.bottom-grid {
  row-gap: 16px;
}

.list-card {
  border-radius: var(--radius-lg);
  height: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.card-header span {
  flex: 1;
}

.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.empty-tip p {
  margin: 0;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color 0.2s;
}

.todo-item:hover {
  background-color: var(--primary-bg);
}

.todo-item + .todo-item {
  border-top: 1px solid var(--border-color);
}

.todo-body {
  flex: 1;
  min-width: 0;
}

.todo-title {
  font-size: 14px;
  color: var(--text-primary);
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.todo-meta {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.recent-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color 0.2s;
}

.recent-item:hover {
  background-color: var(--primary-bg);
}

.recent-item + .recent-item {
  border-top: 1px solid var(--border-color);
}

.recent-icon {
  color: var(--primary-light);
  flex-shrink: 0;
}

.recent-body {
  flex: 1;
  min-width: 0;
}

.recent-title {
  font-size: 14px;
  color: var(--text-primary);
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.recent-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

@media (max-width: 992px) {
  .quick-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 576px) {
  .quick-grid {
    grid-template-columns: 1fr;
  }

  .welcome-banner {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }
}
</style>
