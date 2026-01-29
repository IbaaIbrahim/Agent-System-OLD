/**
 * React hook for SSE connections.
 */

import { useEffect, useRef, useCallback, useState } from 'react'
import { SSEClient, SSEEvent, SSEClientOptions } from '../services/sse'

export interface UseSSEOptions extends Omit<SSEClientOptions, 'onEvent' | 'onError' | 'onOpen' | 'onClose'> {
  enabled?: boolean
}

export interface UseSSEResult {
  events: SSEEvent[]
  isConnected: boolean
  error: Error | null
  connect: () => void
  disconnect: () => void
  clearEvents: () => void
}

export function useSSE(
  url: string | null,
  options: UseSSEOptions = {}
): UseSSEResult {
  const { enabled = true, ...sseOptions } = options

  const [events, setEvents] = useState<SSEEvent[]>([])
  const [isConnected, setIsConnected] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const clientRef = useRef<SSEClient | null>(null)

  const handleEvent = useCallback((event: SSEEvent) => {
    setEvents((prev) => [...prev, event])
  }, [])

  const handleError = useCallback((err: Error) => {
    setError(err)
  }, [])

  const handleOpen = useCallback(() => {
    setIsConnected(true)
    setError(null)
  }, [])

  const handleClose = useCallback(() => {
    setIsConnected(false)
  }, [])

  const connect = useCallback(() => {
    if (!url) return

    if (clientRef.current) {
      clientRef.current.close()
    }

    clientRef.current = new SSEClient(url, {
      ...sseOptions,
      onEvent: handleEvent,
      onError: handleError,
      onOpen: handleOpen,
      onClose: handleClose,
    })

    clientRef.current.connect()
  }, [url, sseOptions, handleEvent, handleError, handleOpen, handleClose])

  const disconnect = useCallback(() => {
    if (clientRef.current) {
      clientRef.current.close()
      clientRef.current = null
    }
  }, [])

  const clearEvents = useCallback(() => {
    setEvents([])
  }, [])

  // Connect when URL changes and enabled
  useEffect(() => {
    if (enabled && url) {
      connect()
    }

    return () => {
      disconnect()
    }
  }, [url, enabled, connect, disconnect])

  return {
    events,
    isConnected,
    error,
    connect,
    disconnect,
    clearEvents,
  }
}
