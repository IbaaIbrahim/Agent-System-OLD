/**
 * SSE client for streaming events.
 */

export interface SSEEvent {
  type: string
  data: Record<string, unknown>
  id?: string
}

export type SSEEventHandler = (event: SSEEvent) => void
export type SSEErrorHandler = (error: Error) => void

export interface SSEClientOptions {
  onEvent?: SSEEventHandler
  onError?: SSEErrorHandler
  onOpen?: () => void
  onClose?: () => void
  lastEventId?: string
  reconnect?: boolean
  reconnectDelay?: number
  maxReconnectAttempts?: number
}

export class SSEClient {
  private url: string
  private options: Required<SSEClientOptions>
  private eventSource: EventSource | null = null
  private reconnectAttempts = 0
  private closed = false

  constructor(url: string, options: SSEClientOptions = {}) {
    this.url = url
    this.options = {
      onEvent: options.onEvent || (() => {}),
      onError: options.onError || console.error,
      onOpen: options.onOpen || (() => {}),
      onClose: options.onClose || (() => {}),
      lastEventId: options.lastEventId || '',
      reconnect: options.reconnect ?? true,
      reconnectDelay: options.reconnectDelay ?? 3000,
      maxReconnectAttempts: options.maxReconnectAttempts ?? 5,
    }
  }

  connect(): void {
    if (this.eventSource) {
      this.eventSource.close()
    }

    // Build URL with Last-Event-ID if available
    let connectUrl = this.url
    if (this.options.lastEventId) {
      const separator = this.url.includes('?') ? '&' : '?'
      connectUrl = `${this.url}${separator}last_event_id=${this.options.lastEventId}`
    }

    this.eventSource = new EventSource(connectUrl)

    this.eventSource.onopen = () => {
      this.reconnectAttempts = 0
      this.options.onOpen()
    }

    this.eventSource.onerror = (event) => {
      const error = new Error('SSE connection error')
      this.options.onError(error)

      if (this.eventSource?.readyState === EventSource.CLOSED) {
        this.handleDisconnect()
      }
    }

    // Handle generic messages
    this.eventSource.onmessage = (event) => {
      this.handleMessage('message', event)
    }

    // Register specific event handlers
    const eventTypes = [
      'open',
      'message',
      'delta',
      'tool_call',
      'tool_result',
      'complete',
      'error',
      'cancelled',
      'keepalive',
    ]

    eventTypes.forEach((type) => {
      this.eventSource!.addEventListener(type, (event) => {
        this.handleMessage(type, event as MessageEvent)
      })
    })
  }

  private handleMessage(type: string, event: MessageEvent): void {
    // Skip keepalive events
    if (type === 'keepalive') {
      return
    }

    try {
      const data = JSON.parse(event.data)
      const sseEvent: SSEEvent = {
        type,
        data,
        id: event.lastEventId,
      }

      // Update last event ID for reconnection
      if (event.lastEventId) {
        this.options.lastEventId = event.lastEventId
      }

      this.options.onEvent(sseEvent)

      // Handle terminal events
      if (type === 'complete' || type === 'error' || type === 'cancelled') {
        this.close()
      }
    } catch (error) {
      console.error('Failed to parse SSE event:', error)
    }
  }

  private handleDisconnect(): void {
    if (this.closed) {
      return
    }

    if (
      this.options.reconnect &&
      this.reconnectAttempts < this.options.maxReconnectAttempts
    ) {
      this.reconnectAttempts++
      console.log(
        `SSE reconnecting (attempt ${this.reconnectAttempts}/${this.options.maxReconnectAttempts})...`
      )

      setTimeout(() => {
        if (!this.closed) {
          this.connect()
        }
      }, this.options.reconnectDelay)
    } else {
      this.options.onClose()
    }
  }

  close(): void {
    this.closed = true
    if (this.eventSource) {
      this.eventSource.close()
      this.eventSource = null
    }
    this.options.onClose()
  }

  get isConnected(): boolean {
    return this.eventSource?.readyState === EventSource.OPEN
  }
}
