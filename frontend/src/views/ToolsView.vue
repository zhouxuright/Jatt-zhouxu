<template>
  <div class="tools-view">
    <div class="view-header">
      <h2>MCP 工具调用</h2>
      <p>使用外部工具辅助法律分析 — 裁判文书搜索、法条检索、企业查询、法律计算器</p>
    </div>

    <!-- 工具列表 -->
    <div class="tools-grid">
      <el-card v-for="tool in tools" :key="tool.name" shadow="hover" class="tool-card">
        <div class="tool-header">
          <el-tag :type="getCategoryType(tool.category)" size="small">{{ tool.category }}</el-tag>
          <span class="tool-version">v{{ tool.version }}</span>
        </div>
        <h3>{{ tool.name }}</h3>
        <p>{{ tool.description }}</p>
        <el-button type="primary" size="small" plain @click="openToolDialog(tool)">
          调用工具
        </el-button>
      </el-card>
    </div>

    <!-- 工具调用对话框 -->
    <el-dialog v-model="dialogVisible" :title="`调用工具: ${selectedTool?.name || ''}`" width="600px" destroy-on-close>
      <div v-if="selectedTool">
        <el-alert :title="selectedTool.description" type="info" :closable="false" show-icon style="margin-bottom: 16px" />
        <el-form :model="toolParams" label-width="100px">
          <el-form-item
            v-for="(prop, key) in parameterProperties"
            :key="String(key)"
            :label="String(key)"
            :required="selectedTool.parameters?.required?.includes(String(key))"
          >
            <el-select
              v-if="prop.enum"
              v-model="toolParams[String(key)]"
              placeholder="请选择"
              style="width: 100%"
            >
              <el-option v-for="opt in prop.enum" :key="opt" :label="opt" :value="opt" />
            </el-select>
            <el-input-number
              v-else-if="prop.type === 'integer'"
              v-model="toolParams[String(key)]"
              :placeholder="prop.description"
              style="width: 100%"
            />
            <el-input
              v-else
              v-model="toolParams[String(key)]"
              :placeholder="prop.description || ''"
            />
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="isExecuting" @click="executeTool">执行</el-button>
      </template>
    </el-dialog>

    <!-- 结果展示 -->
    <el-dialog v-model="resultVisible" title="工具调用结果" width="700px" destroy-on-close>
      <div v-if="toolResult">
        <el-result
          :icon="toolResult.success ? 'success' : 'error'"
          :title="toolResult.success ? '调用成功' : '调用失败'"
          :sub-title="toolResult.success ? `耗时 ${toolResult.elapsed_seconds}s` : toolResult.error"
        />
        <pre v-if="toolResult.data" class="result-data">{{ JSON.stringify(toolResult.data, null, 2) }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { mcpApi } from '@/api'

interface Tool {
  name: string
  description: string
  category: string
  version: string
  parameters: {
    properties: Record<string, any>
    required: string[]
  }
}

const tools = ref<Tool[]>([])
const dialogVisible = ref(false)
const resultVisible = ref(false)
const selectedTool = ref<Tool | null>(null)
const toolParams = ref<Record<string, any>>({})
const isExecuting = ref(false)
const toolResult = ref<any>(null)

const parameterProperties = computed(() => selectedTool.value?.parameters?.properties || {})

function getCategoryType(cat: string) {
  const map: Record<string, string> = {
    legal_research: '', enterprise: 'success',
    government: 'warning', knowledge: 'info', utility: 'danger',
  }
  return (map[cat] || 'info') as any
}

async function loadTools() {
  try {
    const res = await mcpApi.listTools()
    tools.value = res.data.tools || []
  } catch {
    ElMessage.error('加载工具列表失败')
  }
}

function openToolDialog(tool: Tool) {
  selectedTool.value = tool
  toolParams.value = {}
  dialogVisible.value = true
}

async function executeTool() {
  if (!selectedTool.value) return
  isExecuting.value = true
  try {
    const res = await mcpApi.executeTool(selectedTool.value.name, toolParams.value)
    toolResult.value = res.data
    dialogVisible.value = false
    resultVisible.value = true
  } catch {
    ElMessage.error('工具调用失败')
  } finally {
    isExecuting.value = false
  }
}

onMounted(loadTools)
</script>

<style scoped>
.tools-view {
  padding: 24px;
}

.view-header {
  margin-bottom: 24px;
}

.view-header h2 { margin: 0 0 4px; color: var(--primary-color); }
.view-header p { margin: 0; color: var(--text-secondary); font-size: 14px; }

.tools-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.tool-card h3 { margin: 8px 0; font-size: 15px; }
.tool-card p { font-size: 13px; color: var(--text-regular); margin: 0 0 12px; line-height: 1.5; }

.tool-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tool-version { font-size: 12px; color: var(--text-secondary); }

.result-data {
  background: var(--bg-color);
  padding: 16px;
  border-radius: var(--radius-md);
  max-height: 400px;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
}
</style>
