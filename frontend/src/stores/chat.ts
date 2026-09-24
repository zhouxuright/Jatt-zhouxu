import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { chatApi } from '@/api/chat'
import { feedbackApi } from '@/api/feedback'

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  tokens_used?: number
  metadata?: any
  created_at: string
  feedback?: 'like' | 'dislike' | null
  isStreaming?: boolean
  followUps?: string[]
}

export interface Conversation {
  id: string
  user_id: string
  title: string
  agent_type: string
  summary?: string
  created_at: string
  updated_at: string
  message_count?: number
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>([])
  const messages = ref<Message[]>([])
  const currentConversationId = ref<string | null>(null)
  const isLoading = ref(false)
  const isSending = ref(false)

  const currentConversation = computed(() =>
    conversations.value.find(c => c.id === currentConversationId.value) || null
  )

  const currentMessages = computed(() => messages.value)

  async function loadConversations() {
    try {
      isLoading.value = true
      const res = await chatApi.getConversations()
      conversations.value = res.data.conversations || []
    } catch (error) {
      console.error('Failed to load conversations:', error)
    } finally {
      isLoading.value = false
    }
  }

  async function loadMessages(conversationId: string) {
    try {
      isLoading.value = true
      const res = await chatApi.getConversationMessages(conversationId)
      messages.value = (res.data.messages || []).map((m: any) => ({
        ...m,
        feedback: null,
        isStreaming: false,
      }))
      currentConversationId.value = conversationId
    } catch (error) {
      console.error('Failed to load messages:', error)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * Send a message with streaming response.
   * The AI reply appears token-by-token like a printer.
   * Supports enhanced options: deep thinking, web search, file attachments, MCP tools, skills.
   */
  async function sendMessage(
    content: string,
    agentType?: string,
    options?: {
      enable_deep_think?: boolean
      enable_web_search?: boolean
      files?: Array<{ filename: string; file_id?: string; content?: string; error?: string }>
      mcp_tools?: string[]
      skill_id?: string
    },
  ) {
    if (!content.trim() && (!options?.files || options.files.length === 0)) return

    isSending.value = true

    // Add user message immediately
    const userMsg: Message = {
      id: 'temp-user-' + Date.now(),
      conversation_id: currentConversationId.value || '',
      role: 'user',
      content: content,
      created_at: new Date().toISOString(),
      isStreaming: false,
    }
    messages.value.push(userMsg)

    // Create placeholder for streaming assistant message
    const assistantMsg: Message = {
      id: 'temp-assistant-' + Date.now(),
      conversation_id: currentConversationId.value || '',
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      isStreaming: true,
    }
    messages.value.push(assistantMsg)

    const assistantIndex = messages.value.length - 1
    let finalized = false

    return new Promise<void>((resolve, reject) => {
      chatApi.sendChatStream(
        {
          message: content,
          conversation_id: currentConversationId.value || undefined,
          agent_type: agentType,
          enable_deep_think: options?.enable_deep_think,
          enable_web_search: options?.enable_web_search,
          files: options?.files,
          mcp_tools: options?.mcp_tools,
          skill_id: options?.skill_id,
        },
        // onToken - called for each streaming chunk
        (token: string) => {
          messages.value[assistantIndex] = {
            ...messages.value[assistantIndex],
            content: messages.value[assistantIndex].content + token,
          }
        },
        // onMeta - called with conversation metadata
        (meta: any) => {
          if (meta.conversation_id && meta.conversation_id !== currentConversationId.value) {
            currentConversationId.value = meta.conversation_id
            messages.value[assistantIndex] = {
              ...messages.value[assistantIndex],
              conversation_id: meta.conversation_id,
            }
            userMsg.conversation_id = meta.conversation_id
            // Reload conversations to show the new one
            loadConversations()
          }
          if (meta.citations) {
            messages.value[assistantIndex] = {
              ...messages.value[assistantIndex],
              metadata: {
                citations: meta.citations,
                features_used: meta.features_used || null,
                deep_think_steps: meta.deep_think_steps || null,
              },
            }
          }
        },
        // onDone - called when streaming is complete
        (fullContent: string) => {
          if (finalized) return
          finalized = true
          messages.value[assistantIndex] = {
            ...messages.value[assistantIndex],
            content: fullContent,
            isStreaming: false,
          }
          isSending.value = false
          resolve()
          // 从第3轮对话开始，回复完成后生成3个上下文关联的追问按钮
          loadFollowUps(assistantIndex)
        },
        // onError
        (error: string) => {
          messages.value[assistantIndex] = {
            ...messages.value[assistantIndex],
            content: messages.value[assistantIndex].content || `发生错误：${error}`,
            isStreaming: false,
          }
          isSending.value = false
          reject(new Error(error))
        },
      )
    })
  }

  function createNewConversation() {
    currentConversationId.value = null
    messages.value = []
  }

  async function setFeedback(messageId: string, feedback: 'like' | 'dislike') {
    const msg = messages.value.find(m => m.id === messageId)
    if (msg) {
      msg.feedback = msg.feedback === feedback ? null : feedback
    }
    try {
      await feedbackApi.submitFeedback({
        message_id: messageId,
        rating: feedback === 'like' ? 1 : 0,
        comment: '',
        feedback_type: feedback,
      })
    } catch (e) {
      console.warn('Failed to submit feedback:', e)
    }
  }

  /**
   * 从第3轮对话开始，为最新的助手回复生成3个上下文关联的追问建议。
   */
  async function loadFollowUps(assistantIndex: number) {
    const convId = currentConversationId.value
    if (!convId) return
    const userTurnCount = messages.value.filter(m => m.role === 'user').length
    if (userTurnCount < 3) return
    try {
      const res = await chatApi.getFollowUps(convId)
      const suggestions = res.data?.suggestions || []
      if (suggestions.length > 0) {
        messages.value[assistantIndex] = {
          ...messages.value[assistantIndex],
          followUps: suggestions,
        }
      }
    } catch (e) {
      console.warn('Failed to load follow-up suggestions:', e)
    }
  }

  return {
    conversations,
    messages,
    currentConversationId,
    isLoading,
    isSending,
    currentConversation,
    currentMessages,
    loadConversations,
    loadMessages,
    sendMessage,
    createNewConversation,
    setFeedback,
  }
})
