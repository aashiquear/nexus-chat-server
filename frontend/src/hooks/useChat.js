import { useRef, useCallback, useState } from 'react'

export function useChat() {
  const wsRef = useRef(null)
  const [isConnected, setIsConnected] = useState(false)
  const [isStreaming, setIsStreaming] = useState(false)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = localStorage.getItem('nexus_token') || ''
    const wsUrl = `${protocol}//${window.location.host}/ws/chat?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(wsUrl)

    ws.onopen = () => setIsConnected(true)
    ws.onclose = () => {
      setIsConnected(false)
      // Auto-reconnect after 3s
      setTimeout(connect, 3000)
    }
    ws.onerror = () => setIsConnected(false)

    wsRef.current = ws
    return ws
  }, [])

  const sendMessage = useCallback(
    ({ messages, model, tools, files, system_prompt }, onEvent) => {
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
        JSON.stringify({ messages, model, tools, files, system_prompt })
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
