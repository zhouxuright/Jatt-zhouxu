<template>
  <div class="chat-sidebar">
    <div class="sidebar-header">
      <el-button type="primary" size="default" @click="handleNewChat" class="new-chat-btn">
        <el-icon><Plus /></el-icon>
        <span>新建对话</span>
      </el-button>
    </div>

    <div class="conversation-list" v-loading="loading">
      <div
        v-for="conv in conversations"
        :key="conv.id"
        :class="['conv-item', { active: conv.id === activeId }]"
        @click="handleSelect(conv.id)"
      >
        <div class="conv-icon">
          <el-icon><ChatDotRound /></el-icon>
        </div>
        <div class="conv-content">
          <div class="conv-title">{{ conv.title || '新对话' }}</div>
          <div class="conv-meta">
            <span class="conv-time">{{ formatTime(conv.updated_at) }}</span>
            <span class="conv-count">{{ conv.message_count }} 条消息</span>
          </div>
        </div>
        <div class="conv-delete" @click.stop="handleDeleteConversation(conv.id)">
          <el-icon :size="14"><Delete /></el-icon>
        </div>
      </div>

      <el-empty
        v-if="!loading && conversations.length === 0"
        description="暂无对话记录"
        :image-size="80"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import isTodayPlugin from 'dayjs/plugin/isToday'
import isYesterdayPlugin from 'dayjs/plugin/isYesterday'
import 'dayjs/locale/zh-cn'
import type { Conversation } from '@/stores/chat'
import { chatApi } from '@/api/chat'

dayjs.extend(relativeTime)
dayjs.extend(isTodayPlugin)
dayjs.extend(isYesterdayPlugin)
dayjs.locale('zh-cn')

const props = defineProps<{
  conversations: Conversation[]
  activeId: string | null
  loading?: boolean
}>()

const emit = defineEmits<{
  select: [id: string]
  newChat: []
  refresh: []
}>()

function formatTime(date: string): string {
  const d = dayjs(date)
  if (d.isToday()) {
    return d.format('HH:mm')
  } else if (d.isYesterday()) {
    return '昨天'
  } else {
    return d.format('MM-DD')
  }
}

function handleSelect(id: string) {
  emit('select', id)
}

function handleNewChat() {
  emit('newChat')
}

async function handleDeleteConversation(conversationId: string) {
  try {
    await ElMessageBox.confirm('确定删除该对话记录吗？', '提示', { type: 'warning' })
    await chatApi.deleteConversation(conversationId)
    ElMessage.success('删除成功')
    emit('refresh')
  } catch (e) {
    // cancelled or error
  }
}
</script>

<style scoped>
.chat-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: var(--bg-white);
  border-right: 1px solid var(--border-color);
}

.sidebar-header {
  padding: 16px;
  border-bottom: 1px solid var(--border-color);
}

.new-chat-btn {
  width: 100%;
}

.conversation-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.conv-item {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
  gap: 10px;
}

.conv-item:hover {
  background-color: var(--bg-color);
}

.conv-item.active {
  background-color: var(--primary-bg);
}

.conv-icon {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--primary-bg);
  border-radius: 8px;
  color: var(--primary-light);
  font-size: 16px;
}

.conv-item.active .conv-icon {
  background-color: var(--primary-light);
  color: #fff;
}

.conv-content {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-meta {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}

.conv-delete {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 4px;
  color: var(--text-secondary);
  opacity: 0;
  transition: all 0.2s;
  cursor: pointer;
}

.conv-item:hover .conv-delete {
  opacity: 1;
}

.conv-delete:hover {
  background-color: rgba(229, 62, 62, 0.1);
  color: var(--danger-color, #e53e3e);
}
</style>