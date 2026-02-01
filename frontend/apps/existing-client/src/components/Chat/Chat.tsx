/**
 * Main Chat component.
 */

import { useState } from 'react'
import { useChat } from '../../hooks/useChat'
import { setApiKey } from '../../services/api'
import { MessageList } from './MessageList'
import { MessageInput } from './MessageInput'
import { ApiKeyInput } from './ApiKeyInput'

export function Chat() {
  const [apiKeySet, setApiKeySet] = useState(false)
  const { messages, isLoading, error, sendMessage, cancelJob, clearMessages } =
    useChat()

  const handleApiKeySubmit = (apiKey: string) => {
    setApiKey(apiKey)
    setApiKeySet(true)
  }

  if (!apiKeySet) {
    return <ApiKeyInput onSubmit={handleApiKeySubmit} />
  }

  return (
    <div className="flex flex-col h-[calc(100vh-120px)] bg-white rounded-lg shadow">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="text-lg font-medium text-gray-900">Chat</h2>
        <button
          onClick={clearMessages}
          className="text-sm text-gray-500 hover:text-gray-700"
        >
          Clear
        </button>
      </div>

      {/* Error display */}
      {error && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-100">
          <p className="text-sm text-red-600">{error}</p>
        </div>
      )}

      {/* Messages */}
      <MessageList messages={messages} />

      {/* Input */}
      <MessageInput
        onSend={sendMessage}
        onCancel={cancelJob}
        isLoading={isLoading}
      />
    </div>
  )
}
