/**
 * API key input component.
 */

import { useState, FormEvent } from 'react'

interface ApiKeyInputProps {
  onSubmit: (apiKey: string) => void
}

export function ApiKeyInput({ onSubmit }: ApiKeyInputProps) {
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()

    if (!apiKey.trim()) {
      setError('API key is required')
      return
    }

    if (!apiKey.startsWith('sk-agent-')) {
      setError('Invalid API key format. Key should start with "sk-agent-"')
      return
    }

    onSubmit(apiKey.trim())
  }

  return (
    <div className="max-w-md mx-auto bg-white rounded-lg shadow p-6">
      <h2 className="text-xl font-semibold text-gray-900 mb-4">
        Enter API Key
      </h2>
      <p className="text-gray-600 mb-4">
        Please enter your Agent System API key to start chatting.
      </p>

      <form onSubmit={handleSubmit}>
        <div className="mb-4">
          <label
            htmlFor="apiKey"
            className="block text-sm font-medium text-gray-700 mb-1"
          >
            API Key
          </label>
          <input
            type="password"
            id="apiKey"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value)
              setError('')
            }}
            placeholder="sk-agent-..."
            className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
          />
          {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
        </div>

        <button
          type="submit"
          className="w-full px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-500 transition-colors"
        >
          Connect
        </button>
      </form>

      <div className="mt-4 text-sm text-gray-500">
        <p>
          Don't have an API key?{' '}
          <a href="#" className="text-primary-600 hover:underline">
            Create a tenant account
          </a>
        </p>
      </div>
    </div>
  )
}
