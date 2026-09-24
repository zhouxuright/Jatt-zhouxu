import service from './index'

export const chatApi = {
  sendChat(data: { message: string; conversation_id?: string; agent_type?: string }) {
    return service.post('/chat/chat', data)
  },

  /**
   * Send a message and receive a streaming SSE response.
   * Returns an EventSource-compatible ReadableStream.
   * Supports enhanced options: deep thinking, web search, file attachments, MCP tools, skills.
   */
  async sendChatStream(
    data: {
      message: string;
      conversation_id?: string;
      agent_type?: string;
      enable_deep_think?: boolean;
      enable_web_search?: boolean;
      files?: Array<{ filename: string; file_id?: string; content?: string; error?: string }>;
      mcp_tools?: string[];
      skill_id?: string;
    },
    onToken: (token: string) => void,
    onMeta: (meta: any) => void,
    onDone: (fullContent: string) => void,
    onError: (error: string) => void,
  ) {
    const token = localStorage.getItem('token')
    try {
      const response = await fetch('/api/v1/chat/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(data),
      })

      if (!response.ok) {
        onError(`请求失败: ${response.status}`)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        onError('无法读取响应流')
        return
      }

      const decoder = new TextDecoder()
      let fullContent = ''
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Parse SSE lines
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // Keep incomplete line in buffer

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const parsed = JSON.parse(line.slice(6))
              if (parsed.type === 'meta') {
                onMeta(parsed.data)
              } else if (parsed.type === 'token') {
                const token = parsed.content
                fullContent += token
                onToken(token)
              } else if (parsed.type === 'done') {
                onDone(parsed.content || fullContent)
              }
            } catch {
              // Skip malformed SSE data
            }
          }
        }
      }

      // If we didn't receive a done signal, call onDone anyway
      if (fullContent) {
        onDone(fullContent)
      }
    } catch (err: any) {
      onError(err.message || '网络请求失败')
    }
  },

  getConversations(page = 1, pageSize = 20) {
    return service.get('/chat/conversations', { params: { page, page_size: pageSize } })
  },

  getConversationMessages(conversationId: string) {
    return service.get(`/chat/conversations/${conversationId}`)
  },

  /**
   * Generate 3 context-aware follow-up questions for a multi-turn conversation.
   */
  getFollowUps(conversationId: string) {
    return service.post('/chat/follow-ups', { conversation_id: conversationId })
  },

  deleteConversation(conversationId: string) {
    return service.delete(`/chat/conversations/${conversationId}`)
  },

  async ttsMessage(messageId: string, text: string): Promise<Blob> {
    const response = await service.post('/chat/voice/tts',
      { message_id: messageId, text, voice: 'female_formal' },
      { responseType: 'blob' }
    )
    return response.data
  },
}
