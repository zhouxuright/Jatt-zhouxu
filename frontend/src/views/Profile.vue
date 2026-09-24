<template>
  <div class="profile-page">
    <div class="page-container">
      <h2 class="section-title">个人中心</h2>

      <div class="profile-grid">
        <!-- 用户信息卡片 -->
        <el-card class="profile-card" shadow="never">
          <div class="profile-info">
            <el-avatar :size="72" :icon="UserFilled" />
            <div class="profile-detail">
              <h3>{{ authStore.user?.username || '用户' }}</h3>
              <p>
                <el-icon><Message /></el-icon>
                {{ authStore.user?.email || '未设置邮箱' }}
              </p>
              <p class="join-date">
                <el-icon><Calendar /></el-icon>
                注册时间：{{ formatDate(authStore.user?.created_at) }}
              </p>
            </div>
          </div>
        </el-card>

        <!-- 使用统计 -->
        <el-card class="stats-card" shadow="never">
          <template #header>
            <span class="card-title">使用统计</span>
          </template>
          <div class="stats-grid">
            <div class="stat-item">
              <div class="stat-value">{{ stats.totalConversations }}</div>
              <div class="stat-label">对话次数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.totalMessages }}</div>
              <div class="stat-label">消息数量</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.contractReviews }}</div>
              <div class="stat-label">合同审查</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.documentsGenerated }}</div>
              <div class="stat-label">文书生成</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.lawSearches }}</div>
              <div class="stat-label">法规检索</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.feedbacksSubmitted }}</div>
              <div class="stat-label">反馈建议</div>
            </div>
          </div>
        </el-card>

        <!-- 反馈历史 -->
        <el-card class="feedback-card" shadow="never">
          <template #header>
            <div class="card-header">
              <span class="card-title">反馈记录</span>
              <el-button type="primary" size="small" @click="showFeedbackDialog = true">
                <el-icon><Edit /></el-icon>提交反馈
              </el-button>
            </div>
          </template>

          <div v-if="feedbacks.length === 0" class="empty-feedback">
            <el-empty description="暂无反馈记录" :image-size="80" />
          </div>

          <div v-else class="feedback-list">
            <div
              v-for="fb in feedbacks"
              :key="fb.id"
              class="feedback-item"
            >
              <div class="feedback-header">
                <el-tag size="small" :type="feedbackTagType(fb.category)">
                  {{ feedbackCategoryLabel(fb.category) }}
                </el-tag>
                <span class="feedback-time">{{ formatDate(fb.created_at) }}</span>
              </div>
              <p class="feedback-content">{{ fb.content }}</p>
              <div v-if="fb.reply" class="feedback-reply">
                <el-icon><ChatDotRound /></el-icon>
                <span>{{ fb.reply }}</span>
              </div>
            </div>

            <el-pagination
              v-if="feedbackTotal > feedbacks.length"
              v-model:current-page="feedbackPage"
              :page-size="feedbackPageSize"
              :total="feedbackTotal"
              layout="prev, pager, next"
              small
              class="feedback-pagination"
              @current-change="loadFeedbacks"
            />
          </div>
        </el-card>
      </div>
    </div>

    <!-- 提交反馈对话框 -->
    <el-dialog
      v-model="showFeedbackDialog"
      title="提交反馈"
      width="500px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="feedbackFormRef"
        :model="feedbackForm"
        :rules="feedbackRules"
        label-position="top"
      >
        <el-form-item label="反馈类型" prop="category">
          <el-select v-model="feedbackForm.category" placeholder="请选择反馈类型" style="width: 100%">
            <el-option label="功能建议" value="suggestion" />
            <el-option label="问题反馈" value="bug" />
            <el-option label="使用体验" value="experience" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="反馈内容" prop="content">
          <el-input
            v-model="feedbackForm.content"
            type="textarea"
            :rows="5"
            placeholder="请详细描述您的建议或问题..."
          />
        </el-form-item>
        <el-form-item label="联系方式（选填）" prop="contact">
          <el-input v-model="feedbackForm.contact" placeholder="邮箱或手机号，方便我们回复您" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showFeedbackDialog = false">取消</el-button>
        <el-button type="primary" :loading="submittingFeedback" @click="handleSubmitFeedback">
          提交
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UserFilled, Message, Calendar, Edit, ChatDotRound } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import dayjs from 'dayjs'
import { useAuthStore } from '@/stores/auth'
import { analyticsApi } from '@/api/analytics'
import { feedbackApi } from '@/api/feedback'

const authStore = useAuthStore()

const stats = reactive({
  totalConversations: 0,
  totalMessages: 0,
  contractReviews: 0,
  documentsGenerated: 0,
  lawSearches: 0,
  feedbacksSubmitted: 0,
})

const feedbacks = ref<any[]>([])
const feedbackTotal = ref(0)
const feedbackPage = ref(1)
const feedbackPageSize = 10

const showFeedbackDialog = ref(false)
const submittingFeedback = ref(false)
const feedbackFormRef = ref<FormInstance>()

const feedbackForm = reactive({
  category: '',
  content: '',
  contact: '',
})

const feedbackRules: FormRules = {
  category: [{ required: true, message: '请选择反馈类型', trigger: 'change' }],
  content: [{ required: true, message: '请输入反馈内容', trigger: 'blur' }],
}

function formatDate(date?: string) {
  return date ? dayjs(date).format('YYYY-MM-DD') : '--'
}

function feedbackTagType(category: string) {
  const map: Record<string, string> = {
    suggestion: 'success', bug: 'danger', experience: 'warning', other: 'info',
  }
  return map[category] || 'info'
}

function feedbackCategoryLabel(category: string) {
  const map: Record<string, string> = {
    suggestion: '功能建议', bug: '问题反馈', experience: '使用体验', other: '其他',
  }
  return map[category] || category
}

async function loadStats() {
  try {
    const res = await analyticsApi.getDashboard()
    const data = res.data
    stats.totalConversations = data.total_conversations || 0
    stats.totalMessages = data.total_messages || 0
    stats.contractReviews = data.total_reviews || 0
    stats.documentsGenerated = data.total_documents || 0
    stats.feedbacksSubmitted = data.feedback_count || 0
  } catch {
    // Stats will remain at 0
  }
}

async function loadFeedbacks() {
  try {
    const res = await feedbackApi.getMyFeedback(feedbackPage.value, feedbackPageSize)
    const data = res.data
    feedbacks.value = (data.items || data.feedbacks || []).map((fb: any) => ({
      id: fb.id || fb.feedback_id,
      category: fb.feedback_type || fb.category || 'other',
      content: fb.comment || fb.content || '',
      reply: fb.reply || '',
      created_at: fb.created_at || '',
    }))
    feedbackTotal.value = data.total_feedback || data.total || feedbacks.value.length
  } catch {
    feedbacks.value = []
  }
}

async function handleSubmitFeedback() {
  if (!feedbackFormRef.value) return

  await feedbackFormRef.value.validate(async (valid) => {
    if (!valid) return

    submittingFeedback.value = true
    try {
      await feedbackApi.submitFeedback({
        message_id: '', // general feedback
        rating: 5,
        comment: feedbackForm.content,
        feedback_type: feedbackForm.category,
      })
      ElMessage.success('反馈提交成功，感谢您的建议')
      showFeedbackDialog.value = false
      feedbackForm.category = ''
      feedbackForm.content = ''
      feedbackForm.contact = ''
      loadFeedbacks()
    } catch {
      // 错误已在拦截器中处理
    } finally {
      submittingFeedback.value = false
    }
  })
}

onMounted(() => {
  loadStats()
  loadFeedbacks()
})
</script>

<style scoped>
.profile-page {
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
  margin: 0 0 24px 0;
}

.profile-grid {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-color);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.profile-card {
  margin-bottom: 0;
}

.profile-info {
  display: flex;
  align-items: center;
  gap: 24px;
}

.profile-detail h3 {
  font-size: 20px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0 0 8px;
}

.profile-detail p {
  font-size: 14px;
  color: var(--text-regular);
  margin: 4px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.join-date {
  color: var(--text-secondary) !important;
  font-size: 13px !important;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}

.stat-item {
  text-align: center;
  padding: 16px;
  background-color: var(--bg-color);
  border-radius: var(--radius-md);
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--primary-color);
  margin-bottom: 4px;
}

.stat-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.empty-feedback {
  padding: 20px 0;
}

.feedback-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.feedback-item {
  padding: 16px;
  background-color: var(--bg-color);
  border-radius: var(--radius-md);
}

.feedback-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.feedback-time {
  font-size: 12px;
  color: var(--text-secondary);
}

.feedback-content {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.6;
  margin: 0 0 8px;
}

.feedback-reply {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 10px 12px;
  background-color: var(--primary-bg);
  border-radius: var(--radius-sm);
  font-size: 13px;
  color: var(--primary-light);
}

.feedback-pagination {
  margin-top: 8px;
  justify-content: center;
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .profile-info {
    flex-direction: column;
    align-items: center;
    text-align: center;
  }
}
</style>