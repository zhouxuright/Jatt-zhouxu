<template>
  <div class="skills-view">
    <div class="view-header">
      <h2>法律技能包</h2>
      <p>选择专业技能包，一键完成复杂法律分析任务</p>
    </div>

    <!-- 技能包列表 -->
    <div class="skills-grid">
      <el-card
        v-for="skill in skills"
        :key="skill.id"
        shadow="hover"
        class="skill-card"
        @click="openSkillDialog(skill)"
      >
        <div class="skill-card-header">
          <el-tag :type="getCategoryTagType(skill.category)" size="small">
            {{ getCategoryLabel(skill.category) }}
          </el-tag>
          <span class="skill-steps">{{ skill.steps_count }} 步骤</span>
        </div>
        <h3>{{ skill.name }}</h3>
        <p>{{ skill.description }}</p>
        <div class="skill-tags">
          <el-tag v-for="tag in skill.tags" :key="tag" size="small" effect="plain" type="info">
            {{ tag }}
          </el-tag>
        </div>
        <div class="skill-footer">
          <span class="difficulty">
            <el-icon><Timer /></el-icon>
            ~{{ skill.estimated_time_seconds }}秒
          </span>
          <el-button type="primary" size="small">开始执行</el-button>
        </div>
      </el-card>
    </div>

    <!-- 技能执行对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="selectedSkill?.name || '执行技能'"
      width="700px"
      destroy-on-close
    >
      <div v-if="selectedSkill">
        <el-alert :title="selectedSkill.description" type="info" :closable="false" show-icon style="margin-bottom: 16px" />

        <!-- 动态表单 -->
        <el-form :model="formData" label-width="120px" v-if="selectedSkill.input_schema?.properties">
          <el-form-item
            v-for="(prop, key) in selectedSkill.input_schema.properties"
            :key="String(key)"
            :label="(prop as any).description || String(key)"
            :required="selectedSkill.input_schema.required?.includes(String(key))"
          >
            <el-select
              v-if="(prop as any).enum"
              v-model="formData[String(key)]"
              placeholder="请选择"
              style="width: 100%"
            >
              <el-option v-for="opt in (prop as any).enum" :key="opt" :label="opt" :value="opt" />
            </el-select>
            <el-input
              v-else-if="(prop as any).type === 'string'"
              v-model="formData[String(key)]"
              type="textarea"
              :rows="3"
              :placeholder="(prop as any).description || ''"
            />
            <el-input-number
              v-else-if="(prop as any).type === 'number'"
              v-model="formData[String(key)]"
              style="width: 100%"
            />
            <div v-else-if="(prop as any).type === 'object'" class="object-field-group">
              <template v-if="hasSubProps(prop)">
                <div
                  v-for="(subProp, subKey) in (prop as any).properties"
                  :key="String(subKey)"
                  class="object-field"
                >
                  <span class="object-field-label">
                    {{ (subProp as any).description || String(subKey) }}
                  </span>
                  <el-input-number
                    v-if="(subProp as any).type === 'number'"
                    v-model="formData[String(key)][String(subKey)]"
                    style="width: 100%"
                  />
                  <el-input
                    v-else
                    v-model="formData[String(key)][String(subKey)]"
                    :placeholder="(subProp as any).description || ''"
                    clearable
                  />
                </div>
              </template>
              <el-input
                v-else
                type="textarea"
                :rows="3"
                :model-value="objectDrafts[String(key)]"
                placeholder='请填写 JSON，例如 {"name": "张三"}'
                @update:model-value="(val: string) => applyObjectJson(String(key), val)"
              />
            </div>
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="isExecuting" @click="executeSkill">
          {{ isExecuting ? '执行中...' : '执行技能' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 执行结果 -->
    <el-dialog v-model="resultVisible" title="执行结果" width="800px" destroy-on-close>
      <div v-if="execResult">
        <el-result
          :icon="execResult.success ? 'success' : 'error'"
          :title="execResult.success ? '执行成功' : '执行失败'"
          :sub-title="execResult.success ? `耗时 ${execResult.elapsed_seconds}秒，完成 ${execResult.steps_executed} 个步骤` : execResult.error"
        />
        <div v-if="execResult.final_summary" class="result-summary">
          <h4>分析结果</h4>
          <div v-html="renderMarkdown(execResult.final_summary)" />
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { skillsApi } from '@/api'

interface Skill {
  id: string
  name: string
  description: string
  category: string
  tags: string[]
  steps_count: number
  estimated_time_seconds: number
  difficulty: string
  input_schema?: any
}

const skills = ref<Skill[]>([])
const dialogVisible = ref(false)
const resultVisible = ref(false)
const selectedSkill = ref<Skill | null>(null)
const formData = ref<Record<string, any>>({})
const objectDrafts = ref<Record<string, string>>({})
const isExecuting = ref(false)
const execResult = ref<any>(null)

/** Whether an `object` schema prop declares concrete sub-fields. */
function hasSubProps(prop: any): boolean {
  return !!prop?.properties && Object.keys(prop.properties).length > 0
}

/** Parse the JSON fallback textarea into formData (schema without `properties`). */
function applyObjectJson(key: string, raw: string) {
  objectDrafts.value = { ...objectDrafts.value, [key]: raw }
  if (!raw.trim()) {
    formData.value[key] = {}
    return
  }
  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      formData.value[key] = parsed
    }
  } catch {
    // Invalid JSON: keep the draft so the user can fix it; validated on submit.
  }
}

/** Keys whose JSON draft cannot be parsed — blocks submission with a clear hint. */
function invalidObjectKeys(): string[] {
  const props = selectedSkill.value?.input_schema?.properties || {}
  const invalid: string[] = []
  for (const [key, prop] of Object.entries<any>(props)) {
    if ((prop as any).type !== 'object' || hasSubProps(prop)) continue
    const raw = (objectDrafts.value[key] || '').trim()
    if (!raw) continue
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) invalid.push(key)
    } catch {
      invalid.push(key)
    }
  }
  return invalid
}

function getCategoryLabel(cat: string) {
  const map: Record<string, string> = {
    labor: '劳动争议', contract: '合同分析', ip: '知识产权',
    criminal: '刑事辩护', compliance: '企业合规', litigation: '诉讼准备', general: '通用',
  }
  return map[cat] || cat
}

function getCategoryTagType(cat: string) {
  const map: Record<string, string> = {
    labor: '', contract: 'success', ip: 'warning',
    criminal: 'danger', compliance: 'info', litigation: '', general: 'info',
  }
  return (map[cat] || 'info') as any
}

function renderMarkdown(text: string) {
  return text
    .replace(/## (.*)/g, '<h3>$1</h3>')
    .replace(/### (.*)/g, '<h4>$1</h4>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}

async function loadSkills() {
  try {
    const res = await skillsApi.listSkills()
    skills.value = res.data.skills || []
  } catch {
    ElMessage.error('加载技能包失败')
  }
}

function openSkillDialog(skill: Skill) {
  selectedSkill.value = skill
  const props: Record<string, any> = skill.input_schema?.properties || {}
  const init: Record<string, any> = {}
  const drafts: Record<string, string> = {}

  for (const [key, prop] of Object.entries<any>(props)) {
    const type = (prop as any)?.type
    if (type === 'object' && hasSubProps(prop)) {
      const nested: Record<string, any> = {}
      for (const [subKey, subProp] of Object.entries<any>((prop as any).properties)) {
        nested[subKey] = (subProp as any)?.type === 'number' ? undefined : ''
      }
      init[key] = nested
    } else if (type === 'object') {
      init[key] = {}
      drafts[key] = ''
    } else if (type === 'number') {
      init[key] = undefined
    } else {
      init[key] = ''
    }
  }

  formData.value = init
  objectDrafts.value = drafts
  dialogVisible.value = true
}

async function executeSkill() {
  if (!selectedSkill.value) return

  const invalid = invalidObjectKeys()
  if (invalid.length > 0) {
    ElMessage.warning(`以下字段需要填写合法的 JSON 对象：${invalid.join('、')}`)
    return
  }

  isExecuting.value = true

  try {
    const res = await skillsApi.executeSkill(selectedSkill.value.id, formData.value)
    execResult.value = res.data
    dialogVisible.value = false
    resultVisible.value = true
  } catch {
    ElMessage.error('技能执行失败')
  } finally {
    isExecuting.value = false
  }
}

onMounted(loadSkills)
</script>

<style scoped>
.skills-view {
  padding: 24px;
}

.view-header {
  margin-bottom: 24px;
}

.view-header h2 {
  margin: 0 0 4px;
  color: var(--primary-color);
}

.view-header p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 14px;
}

.skills-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.skill-card {
  cursor: pointer;
  transition: transform 0.2s;
}

.skill-card:hover {
  transform: translateY(-2px);
}

.skill-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.skill-steps {
  font-size: 12px;
  color: var(--text-secondary);
}

.skill-card h3 {
  margin: 0 0 8px;
  font-size: 16px;
  color: var(--text-primary);
}

.skill-card p {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--text-regular);
  line-height: 1.5;
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 12px;
}

.skill-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.difficulty {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--text-secondary);
}

.result-summary {
  margin-top: 16px;
  padding: 16px;
  background: var(--bg-color);
  border-radius: var(--radius-md);
  max-height: 400px;
  overflow-y: auto;
  line-height: 1.8;
}

.result-summary h4 {
  color: var(--primary-color);
  margin: 0 0 8px;
}

.object-field-group {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px dashed var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-color);
}

.object-field {
  display: flex;
  align-items: center;
  gap: 8px;
}

.object-field-label {
  flex: 0 0 110px;
  font-size: 13px;
  color: var(--text-regular);
}
</style>
