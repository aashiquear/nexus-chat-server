import { useRef, useCallback, useState } from 'react'
import { authHeaders } from './api'

// Build the WebSocket URL. The server edition appends its auth token as a
// query parameter here (browsers cannot set headers on WebSockets).
export function chatSocketUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const headers = authHeaders()
  const token = headers.Authorization ? headers.Authorization.replace(/^Bearer\s+/i, '') : ''
  const qs = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${protocol}//${window.location.host}/ws/chat${qs}`
}

export function useChat() {
  const wsRef = useRef(null)
  const [isConnected, setIsConnected] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(chatSocketUrl())

    ws.onopen = () => setIsConnected(true)
    ws.onclose = () => {
      setIsConnected(false)
      setIsStreaming(false)
      // Auto-reconnect after 3s
      setTimeout(connect, 3000)
    }
    ws.onerror = () => setIsConnected(false)

    wsRef.current = ws
    return ws
  }, [])

  const sendMessage = useCallback(
    ({ messages, model, tools, files, system_prompt, generation, compaction_state, tool_mode }, onEvent) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        onEvent({ type: 'error', content: 'Not connected to server' })
        return
      }

      setIsStreaming(true)

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          onEvent(data)
          if (data.type === 'done' || data.type === 'error') {
            setIsStreaming(false)
          }
        } catch (e) {
          console.error('Parse error:', e)
        }
      }

      ws.send(
        JSON.stringify({ messages, model, tools, files, system_prompt, generation, compaction_state, tool_mode })
      )
    },
    []
  )

  const disconnect = useCallback(() => {
    wsRef.current?.close()
    wsRef.current = null
  }, [])

  return { connect, disconnect, sendMessage, isConnected, isStreaming }
}
