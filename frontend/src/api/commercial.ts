import service from './index'

// ============================================================================
// 1. MCP Tools API
// ============================================================================
export const mcpApi = {
  /** List all available MCP tools */
  listMcpTools() {
    return service.get('/mcp/tools')
  },

  /** Execute a specific MCP tool */
  executeMcpTool(toolName: string, params: Record<string, any>) {
    return service.post('/mcp/tools/execute', {
      tool_name: toolName,
      parameters: params,
    })
  },

  /** List MCP server tools */
  listMcpServerTools() {
    return service.get('/mcp-server/tools')
  },

  /** Execute MCP server tool */
  executeMcpServerTool(toolName: string, args: Record<string, any>) {
    return service.post('/mcp-server/execute', {
      tool_name: toolName,
      arguments: args,
    })
  },
}

// ============================================================================
// 2. Skills API
// ============================================================================
export const skillsApi = {
  /** List available skills, optionally filtered by category */
  listSkills(category?: string) {
    return service.get('/skills', { params: { category } })
  },

  /** Get skill detail by ID */
  getSkillDetail(skillId: string) {
    return service.get(`/skills/${skillId}`)
  },

  /** Execute a skill synchronously */
  executeSkill(skillId: string, inputData: Record<string, any>) {
    return service.post('/skills/execute', {
      skill_id: skillId,
      input_data: inputData,
    })
  },

  /** Execute a skill with SSE streaming response */
  executeSkillStream(skillId: string, inputData: Record<string, any>) {
    const token = localStorage.getItem('token')
    return fetch('/api/v1/skills/execute-stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        skill_id: skillId,
        input_data: inputData,
      }),
    })
  },
}

// ============================================================================
// 3. Batch Upload API
// ============================================================================
export const batchApi = {
  /** Upload multiple files at once (multipart/form-data) */
  batchUpload(files: File[]) {
    const formData = new FormData()
    files.forEach((file) => {
      formData.append('files', file)
    })
    return service.post('/batch/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** Get batch upload task status */
  getBatchStatus(taskId: string) {
    return service.get(`/batch/status/${taskId}`)
  },

  /** Get batch upload history */
  getBatchHistory() {
    return service.get('/batch/history')
  },

  /** Search within uploaded documents */
  searchInDocuments(query: string, topK?: number) {
    return service.post('/batch/search', {
      query,
      top_k: topK ?? 5,
    })
  },
}

// ============================================================================
// 4. Deep Think API
// ============================================================================
export const deepThinkApi = {
  /** Execute deep thinking reasoning */
  deepThink(query: string, options?: { model_override?: string; include_rag?: boolean }) {
    return service.post('/reasoning/deep-think', {
      query,
      use_reasoning_model: true,
      include_rag: options?.include_rag ?? true,
      model_override: options?.model_override,
    })
  },

  /** Execute deep thinking with SSE streaming response */
  deepThinkStream(query: string, options?: { model_override?: string; include_rag?: boolean }) {
    const token = localStorage.getItem('token')
    return fetch('/api/v1/reasoning/deep-think/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        query,
        use_reasoning_model: true,
        include_rag: options?.include_rag ?? true,
        model_override: options?.model_override,
      }),
    })
  },
}

// ============================================================================
// 5. Voice API
// ============================================================================
export const voiceApi = {
  /** Text-to-speech: convert text to audio blob */
  ttsMessage(text: string, voice?: string): Promise<Blob> {
    return service
      .post('/chat/voice/tts', { text, voice: voice || 'female_formal' }, { responseType: 'blob' })
      .then((res) => res.data)
  },

  /** Get available voice presets */
  getVoicePresets() {
    return service.get('/chat/voice/presets')
  },
}

// ============================================================================
// 6. Compliance API
// ============================================================================
export const complianceApi = {
  /** Get compliance filing information */
  getComplianceInfo() {
    return service.get('/compliance/filing')
  },

  /** Get audit summary */
  getAuditSummary() {
    return service.get('/compliance/audit-summary')
  },
}
