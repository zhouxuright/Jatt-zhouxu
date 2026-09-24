<template>
  <div class="document-generate">
    <div class="page-container">
      <h2 class="section-title">文书生成</h2>
      <p class="section-desc">选择文书类型，填写相关信息，AI 将为您自动生成专业法律文书</p>

      <div class="generate-layout">
        <!-- 左侧：表单 -->
        <div class="form-panel">
          <el-card shadow="never">
            <template #header>
              <span class="card-title">文书信息</span>
            </template>

            <el-form
              ref="formRef"
              :model="form"
              label-position="top"
              size="default"
            >
              <el-form-item label="文书类型" prop="documentType">
                <el-select
                  v-model="form.documentType"
                  placeholder="请选择文书类型"
                  style="width: 100%"
                  :loading="loadingTemplates"
                  @change="onTypeChange"
                >
                  <el-option
                    v-for="tpl in templates"
                    :key="tpl.template_type"
                    :label="tpl.name"
                    :value="tpl.template_type"
                  />
                </el-select>
              </el-form-item>

              <el-alert
                v-if="currentTemplate?.description"
                :title="currentTemplate.description"
                type="info"
                :closable="false"
                show-icon
                style="margin-bottom: 16px"
              />

              <el-form-item
                v-for="field in currentFields"
                :key="field"
                :label="field"
              >
                <el-input
                  v-model="form.fields[field]"
                  type="textarea"
                  :rows="3"
                  :placeholder="`请输入${field}`"
                />
              </el-form-item>

              <el-form-item>
                <el-button
                  type="primary"
                  :loading="generating"
                  :disabled="!form.documentType"
                  size="large"
                  @click="handleGenerate"
                >
                  <el-icon><DocumentAdd /></el-icon>
                  {{ generating ? '生成中...' : '生成文书' }}
                </el-button>
              </el-form-item>
            </el-form>
          </el-card>
        </div>

        <!-- 右侧：预览 -->
        <div class="preview-panel">
          <el-card shadow="never" class="preview-card">
            <template #header>
              <div class="preview-header">
                <span class="card-title">预览</span>
                <el-button
                  v-if="generatedContent"
                  type="primary"
                  size="small"
                  :icon="Download"
                  @click="handleDownload"
                >
                  下载
                </el-button>
              </div>
            </template>

            <div v-if="!generatedContent" class="preview-empty">
              <el-empty description="选择文书类型并填写信息后，点击生成按钮查看结果" :image-size="100" />
            </div>

            <template v-else>
              <el-alert
                class="watermark-notice"
                type="info"
                :closable="true"
                show-icon
              >
                <template #title>
                  🤖 本文书由 AI 辅助生成，仅供法律参考，不构成正式法律建议
                </template>
              </el-alert>
              <div class="markdown-body preview-content" v-html="renderedContent" />
            </template>
          </el-card>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { DocumentAdd, Download } from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import { documentApi } from '@/api/document'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

interface DocTemplate {
  template_type: string
  name: string
  description: string
  category: string
  required_fields: string[]
}

const templates = ref<DocTemplate[]>([])
const loadingTemplates = ref(false)
const generating = ref(false)
const generatedContent = ref('')

const form = reactive<{ documentType: string; fields: Record<string, string> }>({
  documentType: '',
  fields: {},
})

const currentTemplate = computed(() =>
  templates.value.find((t) => t.template_type === form.documentType)
)

const currentFields = computed(() => currentTemplate.value?.required_fields || [])

const renderedContent = computed(() => md.render(generatedContent.value || ''))

async function loadTemplates() {
  loadingTemplates.value = true
  try {
    const res = await documentApi.getTemplates()
    templates.value = res.data.templates || []
  } catch {
    // 错误已在拦截器中处理
  } finally {
    loadingTemplates.value = false
  }
}

function onTypeChange() {
  // 切换文书类型时重置已填写字段
  form.fields = {}
  generatedContent.value = ''
}

async function handleGenerate() {
  if (!form.documentType) {
    ElMessage.warning('请先选择文书类型')
    return
  }

  const tpl = currentTemplate.value
  if (!tpl) return

  generating.value = true
  generatedContent.value = ''
  try {
    // 只提交后端实际需要的字段，未知/多余字段一并透传
    const parameters: Record<string, string> = {}
    for (const field of tpl.required_fields) {
      parameters[field] = (form.fields[field] || '').trim()
    }

    const res = await documentApi.generateDocument({
      template_type: tpl.template_type,
      parameters,
      language: 'zh',
    })
    generatedContent.value = res.data.content
    ElMessage.success('文书生成成功')
  } catch {
    // 错误已在拦截器中处理
  } finally {
    generating.value = false
  }
}

function handleDownload() {
  const blob = new Blob([generatedContent.value], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `法律文书_${new Date().toISOString().slice(0, 10)}.md`
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(loadTemplates)
</script>

<style scoped>
.document-generate {
  height: 100%;
  overflow-y: auto;
}

.page-container {
  max-width: 1200px;
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

.generate-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  align-items: start;
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-color);
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.preview-card {
  position: sticky;
  top: 24px;
}

.preview-empty {
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.preview-content {
  max-height: calc(100vh - 200px);
  overflow-y: auto;
  padding: 16px;
}

.watermark-notice {
  margin-bottom: 12px;
  font-size: 13px;
}

@media (max-width: 900px) {
  .generate-layout {
    grid-template-columns: 1fr;
  }
}
</style>