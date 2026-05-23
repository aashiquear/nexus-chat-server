import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Menu, MessageCircle, Wifi, WifiOff } from 'lucide-react'
import Sidebar from './components/Sidebar'
import ChatMessage, { TypingIndicator } from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import CanvasPanel from './components/CanvasPanel'
import SplashScreen from './components/SplashScreen'
import { useChat } from './hooks/useChat'
import {
  fetchModels, fetchTools, fetchFiles, uploadFile, deleteFile,
  fetchUploadProgress,
  fetchMCPServers, reconnectMCPServer,
  fetchConversations, fetchConversation, saveConversation, deleteConversation,
} from './hooks/api'

export default function App() {
  // Splash / title screen - dismissed after user clicks "Start Chat"
  const [showSplash, setShowSplash] = useState(() => {
    try {
      return sessionStorage.getItem('nexus:splashDismissed') !== '1'
    } catch {
      return true
    }
  })

  const dismissSplash = useCallback(() => {
    try { sessionStorage.setItem('nexus:splashDismissed', '1') } catch {}
    setShowSplash(false)
  }, [])

  // State
  const [models, setModels] = useState([])
  const [tools, setTools] = useState([])
  const [files, setFiles] = useState([])
  const [mcpServers, setMcpServers] = useState([])
  const [selectedModel, setSelectedModel] = useState('')
  const [selectedTools, setSelectedTools] = useState([])
  const [selectedFiles, setSelectedFiles] = useState([])
  const [messages, setMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // LLM response status — drives the floating pop-up above the chat input.
  // Shape: { stage: 'initiated'|'thinking'|'responding'|'tool_calling'|'tool_executing'|'idle'|'error', detail?: string }
  const [llmStatus, setLlmStatus] = useState({ stage: 'idle' })

  // Conversation state
  const [conversations, setConversations] = useState([])
  const [activeConversationId, setActiveConversationId] = useState(null)

  // Canvas panel state (right-side panel for graphs)
  const [canvasData, setCanvasData] = useState(null)
  const [canvasWidth, setCanvasWidth] = useState(null) // null = auto-calculate initial
  const isDraggingRef = useRef(false)

  // Upload progress state: { filename, stage, percent (0-100) } or null
  // Stages: 'uploading' (bytes -> server) then 'embedding' (server -> vector store)
  const [uploadProgress, setUploadProgress] = useState(null)

  // Token usage tracking: array of { prompt_tokens, completion_tokens, total_tokens } per response
  const [tokenUsageHistory, setTokenUsageHistory] = useState([])
  const tokenUsageRef = useRef([])

  // Live last-response stats — only populated during active conversation, not from saved data
  const [lastResponseStats, setLastResponseStats] = useState(null)

  const chatEndRef = useRef(null)
  const chatAreaRef = useRef(null)
  const streamBufferRef = useRef('')
  const responseStartRef = useRef(null)
  const userScrolledRef = useRef(false)
  const { connect, sendMessage, isConnected, isStreaming } = useChat()

  // Initialize
  useEffect(() => {
    connect()
    loadData()
  }, [connect])

  const loadData = async () => {
    try {
      const [m, t, f, mcp, convos] = await Promise.all([
        fetchModels(), fetchTools(), fetchFiles(), fetchMCPServers(),
        fetchConversations(),
      ])
      setModels(m)
      setTools(t)
      setFiles(f)
      setMcpServers(mcp)
      setConversations(convos)

      // Default-select a model that is both provider-configured *and*
      // confirmed on the remote (when probe info is available). Fall
      // back to any configured model if no probe data was returned.
      const isUsable = (x) =>
        x.available && (x.remote_available === null || x.remote_available === undefined || x.remote_available)
      const preferred = m.find(isUsable) || m.find((x) => x.available)
      if (preferred) setSelectedModel(preferred.id)
    } catch (err) {
      console.error('Failed to load data:', err)
    }
  }

  // Smart auto-scroll: only auto-scroll if user is near the bottom
  const handleScroll = useCallback(() => {
    const el = chatAreaRef.current
    if (!el) return
    const threshold = 100
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
    userScrolledRef.current = !atBottom
  }, [])

  useEffect(() => {
    const el = chatAreaRef.current
    if (!el) return
    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [handleScroll])

  // Auto-scroll when new content arrives (only if user hasn't scrolled up)
  useEffect(() => {
    if (!userScrolledRef.current) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isTyping])

  // Toggle helpers
  const toggleTool = (id) => {
    // Check if this tool is a toolkit with grouped children
    const tool = tools.find((t) => t.id === id)
    const childIds = tool?.children || []

    setSelectedTools((prev) => {
      if (prev.includes(id)) {
        // Deactivating: remove toolkit and all its children
        const toRemove = new Set([id, ...childIds])
        return prev.filter((x) => !toRemove.has(x))
      } else {
        // Activating: add toolkit and all its children
        const toAdd = [id, ...childIds].filter((x) => !prev.includes(x))
        return [...prev, ...toAdd]
      }
    })
  }

  const toggleFile = (name) => {
    setSelectedFiles((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name]
    )
  }

  const removeFile = (name) => {
    setSelectedFiles((prev) => prev.filter((x) => x !== name))
  }

  // Upload
  const handleUpload = async (file) => {
    try {
      // Stage 1: bytes leaving the browser
      setUploadProgress({ filename: file.name, stage: 'uploading', percent: 0 })
      await uploadFile(file, (percent) => {
        setUploadProgress({ filename: file.name, stage: 'uploading', percent })
      })

      // Stage 2: server-side embedding (chunks → vector store).
      // Backend returns immediately; poll for embedding progress.
      setUploadProgress({ filename: file.name, stage: 'embedding', percent: 0 })

      let done = false
      let attempts = 0
      while (!done && attempts < 600) {
        attempts += 1
        const progress = await fetchUploadProgress(file.name)
        if (!progress || progress.stage === 'unknown') {
          // Backend hasn't recorded progress yet — keep polling briefly
          await new Promise((r) => setTimeout(r, 250))
          continue
        }
        const stage = progress.stage === 'complete' ? 'embedding' : progress.stage
        setUploadProgress({
          filename: file.name,
          stage: stage === 'reading' || stage === 'chunking' || stage === 'queued'
            ? 'embedding'
            : stage,
          percent: progress.percent || 0,
          subStage: progress.stage,
        })
        if (progress.stage === 'complete' || progress.stage === 'error') {
          done = true
          break
        }
        await new Promise((r) => setTimeout(r, 350))
      }

      setUploadProgress(null)
      const updatedFiles = await fetchFiles()
      setFiles(updatedFiles)
      if (!selectedFiles.includes(file.name)) {
        setSelectedFiles((prev) => [...prev, file.name])
      }
    } catch (err) {
      setUploadProgress(null)
      alert(`Upload failed: ${err.message}`)
    }
  }

  const handleReconnectMCP = async (serverId) => {
    try {
      await reconnectMCPServer(serverId)
      const [t, mcp] = await Promise.all([fetchTools(), fetchMCPServers()])
      setTools(t)
      setMcpServers(mcp)
    } catch (err) {
      console.error('MCP reconnect failed:', err)
    }
  }

  const handleDeleteFile = async (filename) => {
    try {
      await deleteFile(filename)
      setFiles((prev) => prev.filter((f) => f.name !== filename))
      setSelectedFiles((prev) => prev.filter((n) => n !== filename))
    } catch (err) {
      console.error('Delete failed:', err)
    }
  }

  // ---- Conversation persistence ----

  const persistConversation = useCallback(async (msgs, convId, tokenUsage) => {
    if (!msgs || msgs.length === 0) return null
    try {
      const result = await saveConversation({
        id: convId || undefined,
        messages: msgs,
        model: selectedModel,
        token_usage: tokenUsage || undefined,
      })
      // Refresh the sidebar list
      const convos = await fetchConversations()
      setConversations(convos)
      return result.id
    } catch (err) {
      console.error('Save conversation failed:', err)
      return convId
    }
  }, [selectedModel])

  const handleSelectConversation = async (id) => {
    try {
      const data = await fetchConversation(id)
      if (data) {
        setMessages(data.messages || [])
        setActiveConversationId(data.id)
        if (data.model) setSelectedModel(data.model)
        setSidebarOpen(false)
        setTokenUsageHistory(data.token_usage || [])
        tokenUsageRef.current = data.token_usage || []
        setLastResponseStats(null)
      }
    } catch (err) {
      console.error('Failed to load conversation:', err)
    }
  }

  const handleDeleteConversation = async (id) => {
    try {
      await deleteConversation(id)
      setConversations((prev) => prev.filter((c) => c.id !== id))
      if (activeConversationId === id) {
        setMessages([])
        setActiveConversationId(null)
      }
    } catch (err) {
      console.error('Delete conversation failed:', err)
    }
  }

  // Open canvas panel for a graph
  const handleOpenCanvas = useCallback((data) => {
    setCanvasData(data)
    setCanvasWidth(null) // reset to auto-calculate on each new open
  }, [])

  // Canvas divider drag-to-resize
  const resizeCanvas = useCallback((delta) => {
    setCanvasWidth((prev) => {
      const current = prev || window.innerWidth * 0.4
      const minWidth = 300
      const maxWidth = window.innerWidth * 0.7
      return Math.max(minWidth, Math.min(maxWidth, current + delta))
    })
  }, [])

  const handleDividerMouseDown = useCallback((e) => {
    e.preventDefault()
    isDraggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMouseMove = (e) => {
      if (!isDraggingRef.current) return
      const newWidth = window.innerWidth - e.clientX
      const minWidth = 300
      const maxWidth = window.innerWidth * 0.7
      setCanvasWidth(Math.max(minWidth, Math.min(maxWidth, newWidth)))
    }

    const onMouseUp = () => {
      isDraggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [])

  const handleDividerKeyDown = useCallback((e) => {
    const step = e.shiftKey ? 50 : 10
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      resizeCanvas(step)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      resizeCanvas(-step)
    }
  }, [resizeCanvas])

  // Compute conversation statistics from messages and token history
  const conversationStats = useMemo(() => {
    const countWords = (text) => text ? text.trim().split(/\s+/).filter(Boolean).length : 0

    const userMsgs = messages.filter((m) => m.role === 'user')
    const assistantMsgs = messages.filter((m) => m.role === 'assistant')

    const userMessages = userMsgs.length
    const assistantMessages = assistantMsgs.length

    const totalUserWords = userMsgs.reduce((sum, m) => sum + countWords(m.content), 0)
    const assistantWordCounts = assistantMsgs.map((m) => countWords(m.content))
    const totalAssistantWords = assistantWordCounts.reduce((a, b) => a + b, 0)

    const maxResponseWords = assistantWordCounts.length > 0 ? Math.max(...assistantWordCounts) : 0
    const avgResponseWords = assistantMessages > 0 ? Math.round(totalAssistantWords / assistantMessages) : 0

    const totalPromptTokens = tokenUsageHistory.reduce((sum, u) => sum + (u.prompt_tokens || 0), 0)
    const totalCompletionTokens = tokenUsageHistory.reduce((sum, u) => sum + (u.completion_tokens || 0), 0)
    const totalTokens = tokenUsageHistory.reduce((sum, u) => sum + (u.total_tokens || 0), 0)

    const completionTokenCounts = tokenUsageHistory.map((u) => u.completion_tokens || 0)
    const maxResponseTokens = completionTokenCounts.length > 0 ? Math.max(...completionTokenCounts) : 0
    const avgResponseTokens = tokenUsageHistory.length > 0
      ? Math.round(totalCompletionTokens / tokenUsageHistory.length) : 0

    return {
      userMessages,
      assistantMessages,
      totalUserWords,
      totalAssistantWords,
      totalPromptTokens,
      totalCompletionTokens,
      totalTokens,
      maxResponseWords,
      maxResponseTokens,
      avgResponseWords,
      avgResponseTokens,
    }
  }, [messages, tokenUsageHistory])

  // Send message
  const handleSend = useCallback(() => {
    const text = inputValue.trim()
    if (!text || !selectedModel) return

    const userMsg = { role: 'user', content: text }
    const newMessages = [...messages, userMsg]
    setMessages(newMessages)
    setInputValue('')
    setIsTyping(true)
    setLlmStatus({ stage: 'initiated' })
    streamBufferRef.current = ''
    responseStartRef.current = Date.now()
    userScrolledRef.current = false  // Reset scroll on new message

    // Build message history for the API
    const apiMessages = newMessages.map((m) => ({
      role: m.role,
      content: m.content,
    }))

    // Track tool events for the current response
    const toolCalls = []
    const toolResults = []

    // Save reference to current conversation id
    let currentConvId = activeConversationId

    sendMessage(
      {
        messages: apiMessages,
        model: selectedModel,
        tools: selectedTools,
        files: selectedFiles,
      },
      async (event) => {
        switch (event.type) {
          case 'status':
            // Backend-emitted lifecycle marker (e.g. "initiated"). The
            // remaining stages are inferred from text/tool events below.
            if (event.stage) {
              setLlmStatus({ stage: event.stage })
            }
            break

          case 'text':
            streamBufferRef.current += event.content
            setMessages((prev) => {
              const updated = [...prev]
              const lastIdx = updated.length - 1
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  content: streamBufferRef.current,
                  toolCalls: [...toolCalls],
                  toolResults: [...toolResults],
                  startedAt: updated[lastIdx].startedAt ?? responseStartRef.current,
                }
              } else {
                updated.push({
                  role: 'assistant',
                  content: streamBufferRef.current,
                  toolCalls: [...toolCalls],
                  toolResults: [...toolResults],
                  startedAt: responseStartRef.current,
                })
              }
              return updated
            })
            setIsTyping(false)
            // A <think>…</think> block that hasn't closed yet means the
            // model is still mid-reasoning. Anything else counts as the
            // visible response.
            {
              const buf = streamBufferRef.current
              const lastOpen = Math.max(buf.lastIndexOf('<think>'), buf.lastIndexOf('<thinking>'))
              const lastClose = Math.max(buf.lastIndexOf('</think>'), buf.lastIndexOf('</thinking>'))
              const inThink = lastOpen > lastClose
              setLlmStatus({ stage: inThink ? 'thinking' : 'responding' })
            }
            break

          case 'tool_call':
            setLlmStatus({ stage: 'tool_calling', detail: event.name })
            toolCalls.push({
              name: event.name,
              arguments: event.arguments,
            })
            setMessages((prev) => {
              const updated = [...prev]
              const lastIdx = updated.length - 1
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  toolCalls: [...toolCalls],
                  startedAt: updated[lastIdx].startedAt ?? responseStartRef.current,
                }
              } else {
                updated.push({
                  role: 'assistant',
                  content: '',
                  toolCalls: [...toolCalls],
                  toolResults: [],
                  startedAt: responseStartRef.current,
                })
              }
              return updated
            })
            break

          case 'tool_stream':
            // Live output from a streaming tool (e.g. code_executor)
            setCanvasData((prev) => {
              if (prev && prev.type === 'terminal' && prev.name === event.name) {
                return { ...prev, chunks: [...prev.chunks, event.chunk] }
              }
              return {
                type: 'terminal',
                name: event.name,
                title: `Running ${event.name}…`,
                chunks: [event.chunk],
                contentType: 'terminal',
              }
            })
            break

          case 'tool_result':
            setLlmStatus({ stage: 'tool_executing', detail: event.name })
            toolResults.push({
              name: event.name,
              result: event.result,
            })
            setMessages((prev) => {
              const updated = [...prev]
              const lastIdx = updated.length - 1
              if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  toolResults: [...toolResults],
                }
              }
              return updated
            })
            // Note: do NOT reset streamBufferRef here. The agentic loop
            // sends another round of streamed text after this tool call,
            // and that text needs to *append* to the prior round (whose
            // thinking block we want to keep visible). Resetting would
            // overwrite the message content and erase that earlier
            // thinking. The buffer is reset at the start of each new
            // user turn (in handleSend) instead.

            // Auto-open canvas panel for graph plots / interactive
            // sandbox sessions. Plotly figure_json wins over PNG so the
            // user sees the interactive chart by default.
            try {
              const parsed = JSON.parse(event.result)
              if (parsed && parsed.figure_json) {
                const figData = typeof parsed.figure_json === 'string'
                  ? JSON.parse(parsed.figure_json) : parsed.figure_json
                setCanvasData({
                  figureJson: figData,
                  title: parsed.title || figData.layout?.title?.text || 'Interactive Plot',
                })
              } else if (parsed && parsed.plot_image) {
                setCanvasData({
                  image: parsed.plot_image,
                  title: parsed.title || 'Generated Plot',
                })
              } else if (parsed && parsed.interactive_session) {
                setCanvasData({
                  sandboxSessionId: parsed.interactive_session,
                  title: 'Sandbox Console',
                  contentType: 'terminal',
                  chunks: [],
                })
              } else if (parsed && parsed.downloadable) {
                // If a downloadable result comes through, the user can
                // click Preview in the inline card; we don't auto-open.
              }
            } catch (_e) { /* not JSON */ }
            break

          case 'done':
            setIsTyping(false)
            setLlmStatus({ stage: 'idle' })
            // Freeze the response timer on the last assistant message.
            {
              const start = responseStartRef.current
              if (start) {
                const finalMs = Date.now() - start
                setMessages((prev) => {
                  const updated = [...prev]
                  const lastIdx = updated.length - 1
                  if (lastIdx >= 0 && updated[lastIdx].role === 'assistant') {
                    updated[lastIdx] = {
                      ...updated[lastIdx],
                      durationMs: finalMs,
                      startedAt: updated[lastIdx].startedAt ?? start,
                    }
                  }
                  return updated
                })
              }
              responseStartRef.current = null
            }
            // Track token usage if provided
            if (event.usage) {
              setTokenUsageHistory((prev) => {
                const updated = [...prev, event.usage]
                tokenUsageRef.current = updated
                return updated
              })
            }
            // Compute last response stats from the streamed content
            {
              const lastWords = streamBufferRef.current
                ? streamBufferRef.current.trim().split(/\s+/).filter(Boolean).length
                : 0
              const lastTokens = event.usage ? (event.usage.completion_tokens || 0) : 0
              const lastPromptTokens = event.usage ? (event.usage.prompt_tokens || 0) : 0
              setLastResponseStats({
                words: lastWords,
                tokens: lastTokens,
                promptTokens: lastPromptTokens,
              })
            }
            // Auto-save conversation when response is complete
            setMessages((prev) => {
              // Use a timeout to ensure state is settled
              setTimeout(async () => {
                const savedId = await persistConversation(prev, currentConvId, tokenUsageRef.current)
                if (savedId && !currentConvId) {
                  setActiveConversationId(savedId)
                  currentConvId = savedId
                }
              }, 100)
              return prev
            })
            break

          case 'error':
            setIsTyping(false)
            setLlmStatus({ stage: 'error', detail: event.content })
            responseStartRef.current = null
            setMessages((prev) => [
              ...prev,
              {
                role: 'assistant',
                content: `\u26a0\ufe0f ${event.content}`,
              },
            ])
            break
        }
      }
    )
  }, [inputValue, messages, selectedModel, selectedTools, selectedFiles, sendMessage, activeConversationId, persistConversation])

  // New chat
  const handleNewChat = () => {
    setMessages([])
    setActiveConversationId(null)
    setInputValue('')
    setSidebarOpen(false)
    setCanvasData(null)
    setTokenUsageHistory([])
    tokenUsageRef.current = []
    setLastResponseStats(null)
    setLlmStatus({ stage: 'idle' })
  }

  return (
    <div className="app-layout">
      {showSplash && <SplashScreen onStart={dismissSplash} />}
      <Sidebar
        models={models}
        selectedModel={selectedModel}
        onModelChange={setSelectedModel}
        tools={tools}
        selectedTools={selectedTools}
        onToggleTool={toggleTool}
        mcpServers={mcpServers}
        onReconnectMCP={handleReconnectMCP}
        files={files}
        selectedFiles={selectedFiles}
        onToggleFile={toggleFile}
        onUpload={handleUpload}
        onDeleteFile={handleDeleteFile}
        uploadProgress={uploadProgress}
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        onNewChat={handleNewChat}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="main-content">
        {/* Header */}
        <div className="header">
          <div className="header-left">
            <button
              className="sidebar-toggle"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Menu size={20} />
            </button>
            <span className="header-title">Conversation</span>
            {selectedModel && (
              <span className="header-model-badge">{selectedModel}</span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: isConnected ? 'var(--success)' : 'var(--error)', fontSize: 12 }}>
            {isConnected ? <Wifi size={14} /> : <WifiOff size={14} />}
            {isConnected ? 'Connected' : 'Disconnected'}
          </div>
        </div>

        {/* Chat Area */}
        <div className="chat-area" ref={chatAreaRef}>
          <div className="chat-container">
            {messages.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-icon">
                  <MessageCircle size={24} />
                </div>
                <div className="empty-state-title">Start a conversation</div>
                <div className="empty-state-hint">
                  Select a model and tools from the sidebar, then type your message below.
                  Upload files for RAG-enhanced responses.
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg, i) => (
                  <ChatMessage
                    key={i}
                    message={msg}
                    onOpenCanvas={handleOpenCanvas}
                  />
                ))}
                {isTyping && <TypingIndicator />}
              </>
            )}
            <div ref={chatEndRef} />
          </div>
        </div>

        {/* Input */}
        <ChatInput
          value={inputValue}
          onChange={setInputValue}
          onSend={handleSend}
          onUpload={handleUpload}
          disabled={isStreaming || !isConnected}
          selectedFiles={selectedFiles}
          onRemoveFile={removeFile}
          uploadProgress={uploadProgress}
          conversationStats={conversationStats}
          lastResponseStats={lastResponseStats}
          llmStatus={llmStatus}
          isStreaming={isStreaming}
        />
      </div>

      {/* Right-side Canvas Panel for graphs */}
      {canvasData && (
        <>
          <div
            className="canvas-divider"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize canvas panel"
            tabIndex={0}
            onMouseDown={handleDividerMouseDown}
            onKeyDown={handleDividerKeyDown}
          />
          <CanvasPanel
            image={canvasData.image}
            figureJson={canvasData.figureJson}
            content={canvasData.content}
            contentType={canvasData.contentType}
            chunks={canvasData.chunks}
            filename={canvasData.filename}
            previewUrl={canvasData.previewUrl}
            sandboxSessionId={canvasData.sandboxSessionId}
            title={canvasData.title}
            onClose={() => setCanvasData(null)}
            style={canvasWidth ? { width: canvasWidth } : undefined}
          />
        </>
      )}
    </div>
  )
}
