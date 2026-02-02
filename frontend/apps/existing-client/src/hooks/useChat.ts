/**
 * React hook for chat state management.
 */

import { useState, useCallback, useEffect, useRef } from 'react'
import { create } from 'zustand'
import { getClient, ChatMessage as ApiMessage, ChatCompletionRequest } from '../services/api'
import { useSSE } from './useSSE'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  toolCalls?: Array<{
    id: string
    name: string
    arguments: Record<string, unknown>
  }>
  toolCallId?: string
  isStreaming?: boolean
  timestamp: Date
}

export interface ChatState {
  messages: Message[]
  isLoading: boolean
  error: string | null
  currentJobId: string | null
  streamUrl: string | null
}

interface ChatStore extends ChatState {
  addMessage: (message: Omit<Message, 'id' | 'timestamp'>) => void
  updateMessage: (id: string, updates: Partial<Message>) => void
  appendToMessage: (id: string, content: string) => void
  setLoading: (loading: boolean) => void
  setError: (error: string | null) => void
  setJob: (jobId: string | null, streamUrl: string | null) => void
  clearMessages: () => void
  reset: () => void
}

const generateId = () => Math.random().toString(36).substring(2, 15)

export const useChatStore = create<ChatStore>((set) => ({
  messages: [],
  isLoading: false,
  error: null,
  currentJobId: null,
  streamUrl: null,

  addMessage: (message) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          ...message,
          id: generateId(),
          timestamp: new Date(),
        },
      ],
    })),

  updateMessage: (id, updates) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === id ? { ...msg, ...updates } : msg
      ),
    })),

  appendToMessage: (id, content) =>
    set((state) => ({
      messages: state.messages.map((msg) =>
        msg.id === id ? { ...msg, content: msg.content + content } : msg
      ),
    })),

  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error }),
  setJob: (jobId, streamUrl) => set({ currentJobId: jobId, streamUrl }),
  clearMessages: () => set({ messages: [] }),
  reset: () =>
    set({
      messages: [],
      isLoading: false,
      error: null,
      currentJobId: null,
      streamUrl: null,
    }),
}))

export function useChat() {
  const store = useChatStore()
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null)
  // Track the last processed event index to ensure all events are processed
  // even when the browser tab is in the background
  const lastProcessedIndexRef = useRef<number>(-1)

  const { events, isConnected, error: sseError } = useSSE(store.streamUrl, {
    enabled: !!store.streamUrl,
  })

  // Handle SSE events - process ALL pending events, not just the latest
  // This fixes the issue where streaming stops when browser tab is not focused
  useEffect(() => {
    if (events.length === 0) return

    // Process all events that haven't been processed yet
    const startIndex = lastProcessedIndexRef.current + 1
    if (startIndex >= events.length) return

    for (let i = startIndex; i < events.length; i++) {
      const event = events[i]

      switch (event.type) {
        case 'open':
          // Connection opened
          break

        case 'delta':
          // Streaming content
          if (streamingMessageId) {
            store.appendToMessage(
              streamingMessageId,
              (event.data.content as string) || ''
            )
          }
          break

        case 'message':
          // Complete message
          if (event.data.content) {
            if (streamingMessageId) {
              store.updateMessage(streamingMessageId, {
                content: event.data.content as string,
                isStreaming: false,
              })
            }
          }
          break

        case 'tool_call':
          // Tool invocation
          const toolCalls = event.data.tool_calls as Array<{
            id: string
            name: string
            arguments: Record<string, unknown>
          }>
          if (streamingMessageId && toolCalls) {
            store.updateMessage(streamingMessageId, {
              toolCalls,
              isStreaming: false,
            })
          }
          break

        case 'tool_result':
          // Tool result - add as tool message
          store.addMessage({
            role: 'tool',
            content: (event.data.result as string) || '',
            toolCallId: event.data.tool_call_id as string,
          })
          break

        case 'complete':
          // Job complete
          store.setLoading(false)
          store.setJob(null, null)
          if (streamingMessageId) {
            store.updateMessage(streamingMessageId, { isStreaming: false })
          }
          setStreamingMessageId(null)
          break

        case 'error':
          // Error occurred
          store.setError((event.data.error as string) || 'Unknown error')
          store.setLoading(false)
          store.setJob(null, null)
          setStreamingMessageId(null)
          break

        case 'cancelled':
          // Job cancelled
          store.setLoading(false)
          store.setJob(null, null)
          setStreamingMessageId(null)
          break
      }
    }

    // Update the last processed index
    lastProcessedIndexRef.current = events.length - 1
  }, [events, streamingMessageId, store])

  // Reset the processed index when events are cleared or stream URL changes
  useEffect(() => {
    lastProcessedIndexRef.current = -1
  }, [store.streamUrl])

  // Handle SSE errors
  useEffect(() => {
    if (sseError) {
      store.setError(sseError.message)
    }
  }, [sseError, store])

  const sendMessage = useCallback(
    async (content: string, options: Partial<ChatCompletionRequest> = {}) => {
      try {
        store.setError(null)
        store.setLoading(true)

        // Add user message
        store.addMessage({
          role: 'user',
          content,
        })

        // Create placeholder for assistant response
        const placeholderId = generateId()
        store.addMessage({
          role: 'assistant',
          content: '',
          isStreaming: true,
        })
        setStreamingMessageId(placeholderId)

        // Build messages for API
        const apiMessages: ApiMessage[] = store.messages.map((msg) => ({
          role: msg.role,
          content: msg.content,
          tool_calls: msg.toolCalls,
          tool_call_id: msg.toolCallId,
        }))

        // Add the new user message
        apiMessages.push({
          role: 'user',
          content,
        })

        // Create chat completion
        const client = getClient()
        const response = await client.createChatCompletion({
          messages: apiMessages,
          stream: true,
          ...options,
        })

        // Set up stream
        store.setJob(response.job_id, response.stream_url)
      } catch (error) {
        store.setError(
          error instanceof Error ? error.message : 'Failed to send message'
        )
        store.setLoading(false)
        setStreamingMessageId(null)
      }
    },
    [store]
  )

  const cancelJob = useCallback(async () => {
    if (store.currentJobId) {
      try {
        const client = getClient()
        await client.cancelJob(store.currentJobId)
        store.setJob(null, null)
        store.setLoading(false)
      } catch (error) {
        console.error('Failed to cancel job:', error)
      }
    }
  }, [store])

  return {
    messages: store.messages,
    isLoading: store.isLoading,
    error: store.error,
    isConnected,
    sendMessage,
    cancelJob,
    clearMessages: store.clearMessages,
    reset: store.reset,
  }
}
