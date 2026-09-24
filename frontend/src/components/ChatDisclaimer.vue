<template>
  <transition name="el-fade-in">
    <div v-if="visible" class="chat-disclaimer">
      <el-alert
        type="warning"
        :closable="true"
        show-icon
        @close="handleClose"
      >
        <template #title>
          AI 生成的内容仅供参考，不构成法律建议。重要法律事项请咨询专业律师。
        </template>
      </el-alert>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const DISMISSAL_KEY = 'chat_disclaimer_dismissed_at'
const DISMISSAL_DURATION_MS = 7 * 24 * 60 * 60 * 1000 // 7 days

const visible = ref(false)

onMounted(() => {
  const dismissedAt = localStorage.getItem(DISMISSAL_KEY)
  if (dismissedAt) {
    const dismissedTime = parseInt(dismissedAt, 10)
    const now = Date.now()
    if (now - dismissedTime < DISMISSAL_DURATION_MS) {
      // Still within 7-day dismissal window; keep hidden
      return
    }
    // Dismissal expired; clean up and show again
    localStorage.removeItem(DISMISSAL_KEY)
  }
  visible.value = true
})

function handleClose() {
  visible.value = false
  localStorage.setItem(DISMISSAL_KEY, String(Date.now()))
}
</script>

<style scoped>
.chat-disclaimer {
  padding: 8px 16px 0 16px;
  background-color: var(--bg-white);
  border-bottom: 1px solid var(--border-color);
}

.chat-disclaimer :deep(.el-alert) {
  padding: 8px 16px;
}

.chat-disclaimer :deep(.el-alert__title) {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.6;
}
</style>
