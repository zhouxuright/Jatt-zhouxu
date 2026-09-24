import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

// 用于标记请求是否已经尝试过重发，避免无限重试
interface RetriableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

const service: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器 - 添加 JWT Token
service.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('token')
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error: AxiosError) => {
    return Promise.reject(error)
  }
)

// 401 防抖：避免并发请求同时触发多次登出跳转
let isRedirecting401 = false

// 单飞续期：多个并发 401 只触发一次 /auth/refresh
let refreshPromise: Promise<string | null> | null = null

/**
 * 用 refresh_token 换取新的 access_token。
 * 使用裸 axios（不走 service 拦截器），避免刷新请求自身触发 401 递归。
 * 成功后把新 token 写回 localStorage，失败返回 null。
 */
async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return null
  try {
    const resp = await axios.post(
      '/api/v1/auth/refresh',
      { refresh_token: refreshToken },
      { timeout: 30000 }
    )
    const accessToken = resp.data?.access_token
    const newRefreshToken = resp.data?.refresh_token
    if (!accessToken) return null
    localStorage.setItem('token', accessToken)
    if (newRefreshToken) localStorage.setItem('refresh_token', newRefreshToken)
    return accessToken
  } catch {
    return null
  }
}

/** 续期失败或不可续期时，清空登录态并跳转登录页。 */
function redirectToLogin() {
  if (isRedirecting401) return
  isRedirecting401 = true
  localStorage.removeItem('token')
  localStorage.removeItem('refresh_token')
  if (router.currentRoute.value.name !== 'Login') {
    ElMessage.error('登录已过期，请重新登录')
    router.push({ name: 'Login', query: { redirect: router.currentRoute.value.fullPath } })
  }
  setTimeout(() => { isRedirecting401 = false }, 3000)
}

// 响应拦截器 - 统一错误处理
service.interceptors.response.use(
  (response: AxiosResponse) => {
    return response
  },
  async (error: AxiosError) => {
    const { response, config } = error
    const originalConfig = config as RetriableConfig | undefined

    if (response) {
      const { status, data } = response

      switch (status) {
        case 401:
          // 先尝试用 refresh_token 续期并重试原请求（仅重试一次）
          if (originalConfig && !originalConfig._retry) {
            originalConfig._retry = true
            if (!refreshPromise) {
              refreshPromise = refreshAccessToken().finally(() => {
                refreshPromise = null
              })
            }
            const newToken = await refreshPromise
            if (newToken) {
              originalConfig.headers.Authorization = `Bearer ${newToken}`
              return service(originalConfig)
            }
          }
          // 续期失败 / 已重试过 / 无可续期 token → 跳登录
          redirectToLogin()
          break
        case 403:
          ElMessage.error('没有权限执行此操作')
          break
        case 404:
          // 静默处理 404，不弹错误提示（资源不存在是正常情况）
          break
        case 400:
        case 413: {
          // 400/413 多为业务校验失败（如合同文本过长、文件格式不支持）。
          // 以前这两类落到 default 分支，只显示一句笼统的"请求失败"，用户完全
          // 无从判断原因（合同审查超长被拒即为此例）。这里优先展示后端 detail。
          const detail = (data as { detail?: string })?.detail
          ElMessage.error(
            detail || (status === 413 ? '内容过大，请精简后重试' : '请求参数有误，请检查后重试')
          )
          break
        }
        case 422: {
          const detail = (data as { detail?: string })?.detail
          ElMessage.error(
            typeof detail === 'string' ? detail : '请求参数验证失败'
          )
          break
        }
        case 500:
          ElMessage.error('服务器内部错误，请稍后重试')
          break
        default:
          ElMessage.error((data as { message?: string })?.message || '请求失败')
      }
    } else {
      // 网络错误 — 不弹提示，可能是临时断网
      console.warn('Network error:', error.message)
    }

    return Promise.reject(error)
  }
)

export default service

// ============================================================================
// New Module API Services
// ============================================================================

// ---------- MCP Tool Calling API ----------
export const mcpApi = {
  listTools(category?: string) {
    return service.get('/mcp/tools', { params: { category } })
  },
  getToolSchema(toolName: string) {
    return service.get(`/mcp/tools/${toolName}`)
  },
  executeTool(toolName: string, parameters: Record<string, any>) {
    return service.post('/mcp/tools/execute', { tool_name: toolName, parameters })
  },
  executeBatchTools(calls: Record<string, any>[]) {
    return service.post('/mcp/tools/execute-batch', { calls })
  },
  getToolStats() {
    return service.get('/mcp/tools/stats')
  },
}

// ---------- Skills System API ----------
export const skillsApi = {
  listSkills(category?: string) {
    return service.get('/skills', { params: { category } })
  },
  getSkillDetail(skillId: string) {
    return service.get(`/skills/${skillId}`)
  },
  executeSkill(skillId: string, inputData: Record<string, any>) {
    return service.post('/skills/execute', { skill_id: skillId, input_data: inputData })
  },
  searchSkills(query: string, category?: string) {
    return service.post('/skills/search', { query, category })
  },
  getSkillStats() {
    return service.get('/skills/stats')
  },
}

// ---------- Deep Thinking API ----------
export const reasoningApi = {
  deepThink(
    query: string,
    options?: { model_override?: string; include_rag?: boolean; depth?: string },
  ) {
    return service.post('/reasoning/deep-think', {
      query,
      use_reasoning_model: true,
      include_rag: options?.include_rag ?? true,
      model_override: options?.model_override,
      depth: options?.depth,
    })
  },
  listReasoningModels() {
    return service.get('/reasoning/deep-think/models')
  },
  /**
   * Execute deep thinking with SSE streaming. Returns the raw response for stream reading.
   *
   * `model_override` was previously dropped here while the non-streaming
   * fallback passed it, so the model picked in the DeepThink UI only took
   * effect when streaming failed. `/reasoning/deep-think/stream` accepts it
   * (`DeepThinkRequest.model_override`), so forward it.
   */
  deepThinkStream(query: string, options?: { model_override?: string; depth?: string }) {
    const token = localStorage.getItem('token')
    const body: Record<string, unknown> = {
      query,
      use_reasoning_model: true,
      include_rag: true,
    }
    if (options?.model_override) {
      body.model_override = options.model_override
    }
    if (options?.depth) {
      body.depth = options.depth
    }
    return fetch('/api/v1/reasoning/deep-think/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    })
  },
}

// ---------- Web Search API ----------
export const searchApi = {
  webSearch(query: string, options?: { num_results?: number; legal_only?: boolean; time_range?: string }) {
    return service.post('/search/web', {
      query,
      num_results: options?.num_results ?? 10,
      legal_only: options?.legal_only ?? false,
      time_range: options?.time_range,
      summarize: true,
    })
  },
  legalUpdates(topic: string, daysBack: number = 30) {
    return service.post('/search/legal-updates', { topic, days_back: daysBack, summarize: true })
  },
  caseSearch(keywords: string, caseType?: string) {
    return service.post('/search/cases', { keywords, case_type: caseType, summarize: true })
  },
  getTrustedSources() {
    return service.get('/search/sources')
  },
}

// ---------- Data Expansion API ----------
export const dataApi = {
  getSources() {
    return service.get('/data/sources')
  },
  getStats() {
    return service.get('/data/stats')
  },
  importCail(datasetId: string, dataDir?: string) {
    return service.post('/data/import/cail', { dataset_id: datasetId, data_dir: dataDir })
  },
}
