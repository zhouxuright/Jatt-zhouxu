<template>
  <div class="feedback-buttons">
    <el-button
      :type="feedback === 'like' ? 'primary' : 'default'"
      :icon="CircleCheck"
      circle
      size="small"
      @click="handleLike"
      :class="{ 'is-active': feedback === 'like' }"
    />
    <el-button
      :type="feedback === 'dislike' ? 'danger' : 'default'"
      :icon="CircleClose"
      circle
      size="small"
      @click="handleDislike"
      :class="{ 'is-active': feedback === 'dislike' }"
    />
  </div>
</template>

<script setup lang="ts">
import { CircleCheck, CircleClose } from '@element-plus/icons-vue'

const props = defineProps<{
  feedback: 'like' | 'dislike' | null | undefined
  messageId: string
}>()

const emit = defineEmits<{
  feedback: [type: 'like' | 'dislike']
}>()

function handleLike() {
  emit('feedback', 'like')
}

function handleDislike() {
  emit('feedback', 'dislike')
}
</script>

<style scoped>
.feedback-buttons {
  display: flex;
  gap: 6px;
}

.feedback-buttons .el-button {
  width: 30px;
  height: 30px;
  padding: 0;
}

.feedback-buttons .el-button.is-active {
  transform: scale(1.1);
}
</style>