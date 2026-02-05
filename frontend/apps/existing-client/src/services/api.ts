/**
 * API client for the Agent System.
 */

const API_BASE = '/api/v1'

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: ToolCall[]
  tool_call_id?: string
}

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

export interface ToolDefinition {
  name: string
  description: string
  parameters: Record<string, unknown>
}

export interface ChatCompletionRequest {
  messages: ChatMessage[]
  model?: string
  provider?: 'anthropic' | 'openai'
  system?: string
  tools?: ToolDefinition[]
  temperature?: number
  max_tokens?: number
  stream?: boolean
  metadata?: Record<string, unknown>
}

export interface ChatCompletionResponse {
  job_id: string
  stream_url: string
  stream_token: string
  status: string
}

export interface JobStatus {
  job_id: string
  status: string
  provider: string
  model: string
  created_at: string
  completed_at?: string
  error?: string
  total_input_tokens: number
  total_output_tokens: number
  metadata: Record<string, unknown>
}

export class ApiClient {
  private apiKey: string

  constructor(apiKey: string) {
    this.apiKey = apiKey
  }

  private async fetch<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.apiKey}`,
        ...options.headers,
      },
    })

    if (!response.ok) {
      const error = await response.json().catch(() => ({}))
      throw new ApiError(
        error.error?.message || 'Request failed',
        response.status,
        error.error?.code
      )
    }

    return response.json()
  }

  /**
   * Create a new chat completion.
   */
  async createChatCompletion(
    request: ChatCompletionRequest
  ): Promise<ChatCompletionResponse> {
    return this.fetch('/chat/completions', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  /**
   * Get job status.
   */
  async getJobStatus(jobId: string): Promise<JobStatus> {
    return this.fetch(`/jobs/${jobId}`)
  }

  /**
   * Cancel a job.
   */
  async cancelJob(jobId: string): Promise<{ message: string; job_id: string }> {
    return this.fetch(`/jobs/${jobId}`, {
      method: 'DELETE',
    })
  }
}

export class ApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

// Default client (will be configured with API key from user)
let defaultClient: ApiClient | null = null

export function setApiKey(apiKey: string): void {
  defaultClient = new ApiClient(apiKey)
}

export function getClient(): ApiClient {
  if (!defaultClient) {
    throw new Error('API client not configured. Call setApiKey first.')
  }
  return defaultClient
}
