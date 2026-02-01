/**
 * Message list component.
 */

import { useEffect, useRef } from 'react'
import { Message } from '../../hooks/useChat'

interface MessageListProps {
  messages: Message[]
}

export function MessageList({ messages }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500">
        <p>Send a message to start the conversation</p>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto p-4 space-y-4">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
    </div>
  )
}

interface MessageBubbleProps {
  message: Message
}

function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'
  const isAssistant = message.role === 'assistant'
  const isTool = message.role === 'tool'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-lg px-4 py-2 ${
          isUser
            ? 'bg-primary-600 text-white'
            : isTool
            ? 'bg-yellow-50 border border-yellow-200 text-gray-800'
            : 'bg-gray-100 text-gray-800'
        }`}
      >
        {/* Role label */}
        <div
          className={`text-xs mb-1 ${
            isUser ? 'text-primary-200' : 'text-gray-500'
          }`}
        >
          {message.role}
          {isTool && message.toolCallId && (
            <span className="ml-2 text-yellow-600">
              (call: {message.toolCallId.slice(0, 8)}...)
            </span>
          )}
        </div>

        {/* Content */}
        <div className="whitespace-pre-wrap">
          {message.content}
          {message.isStreaming && (
            <span className="inline-block w-2 h-4 ml-1 bg-gray-400 animate-pulse" />
          )}
        </div>

        {/* Tool calls */}
        {isAssistant && message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mt-2 space-y-2">
            {message.toolCalls.map((tc) => (
              <div
                key={tc.id}
                className="bg-white bg-opacity-20 rounded p-2 text-sm"
              >
                <div className="font-medium">Tool: {tc.name}</div>
                <pre className="text-xs mt-1 overflow-x-auto">
                  {JSON.stringify(tc.arguments, null, 2)}
                </pre>
              </div>
            ))}
          </div>
        )}

        {/* Timestamp */}
        <div
          className={`text-xs mt-1 ${
            isUser ? 'text-primary-200' : 'text-gray-400'
          }`}
        >
          {message.timestamp.toLocaleTimeString()}
        </div>
      </div>
    </div>
  )
}
