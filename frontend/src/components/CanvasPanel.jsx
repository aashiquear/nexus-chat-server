import React, { useState, useEffect, useRef, useCallback } from 'react'
import { X, Download, BarChart3, FileText, Terminal, Image as ImageIcon, Send } from 'lucide-react'
import LazyPlot from './LazyPlot'
import ReactMarkdown from 'react-markdown'

function normalizeContentType(mime) {
  if (!mime) return 'text'
  if (mime.includes('markdown')) return 'markdown'
  if (mime.includes('html')) return 'html'
  if (mime.startsWith('image/')) return 'image'
  if (mime.includes('pdf')) return 'pdf'
  return 'text'
}

// Live sandbox session terminal. Connects to /ws/sandbox/interact/{id}
// and renders streamed stdout/stderr alongside an input box for stdin.
function SandboxTerminal({ sessionId }) {
  const [lines, setLines] = useState([])
  const [input, setInput] = useState('')
  const [ready, setReady] = useState(false)
  const [exited, setExited] = useState(false)
  const wsRef = useRef(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    if (!sessionId) return
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/sandbox/interact/${sessionId}`)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'ready') { setReady(true); return }
        if (msg.type === 'exit') {
          setExited(true)
          setLines((prev) => [...prev, { kind: 'system', text: `[process exited with code ${msg.code}]` }])
          return
        }
        if (msg.type === 'stdout' || msg.type === 'stderr') {
          setLines((prev) => [...prev, { kind: msg.type, text: msg.data }])
        }
      } catch {
        setLines((prev) => [...prev, { kind: 'stdout', text: e.data }])
      }
    }
    ws.onerror = () => {
      setLines((prev) => [...prev, { kind: 'stderr', text: '[connection error]' }])
    }
    ws.onclose = () => setReady(false)

    return () => { try { ws.close() } catch {} }
  }, [sessionId])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [lines])

  const handleSend = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(input)
    setLines((prev) => [...prev, { kind: 'input', text: `> ${input}` }])
    setInput('')
  }, [input])

  return (
    <div className="sandbox-terminal">
      <div className="terminal-container" ref={scrollRef}>
        {lines.map((l, i) => (
          <div key={i} className={`terminal-line ${l.kind === 'stderr' ? 'stderr' : ''} ${l.kind === 'input' ? 'input' : ''} ${l.kind === 'system' ? 'system' : ''}`}>
            {l.text}
          </div>
        ))}
        {!exited && ready && <div className="terminal-cursor" />}
      </div>
      <form
        className="sandbox-terminal-input"
        onSubmit={(e) => { e.preventDefault(); handleSend() }}
      >
        <span className="sandbox-terminal-prompt">stdin</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={exited ? 'Session ended' : (ready ? 'Type input and press Enter…' : 'Connecting…')}
          disabled={!ready || exited}
        />
        <button type="submit" disabled={!ready || exited || !input.trim()} title="Send">
          <Send size={13} />
        </button>
      </form>
    </div>
  )
}

export default function CanvasPanel({
  image,
  figureJson,
  content,
  contentType,
  chunks,
  filename,
  previewUrl,
  sandboxSessionId,
  title,
  onClose,
  style,
}) {
  const [downloading, setDownloading] = useState(false)
  const [previewData, setPreviewData] = useState(null)

  useEffect(() => {
    if (filename && !content) {
      fetch(`/api/preview/${encodeURIComponent(filename)}`)
        .then((r) => r.json())
        .then((data) => setPreviewData(data))
        .catch((err) => console.error('Preview fetch failed:', err))
    }
  }, [filename, content])

  // Effective content fields — the inline-provided values win, otherwise
  // fall back to whatever /api/preview returned.
  const effectiveContent = content || previewData?.content || ''
  const effectiveContentType = contentType
    || normalizeContentType(previewData?.content_type)
    || (previewData?.kind === 'pdf' ? 'pdf' : null)
    || (previewData?.kind === 'image' ? 'image' : null)
    || 'text'
  const effectivePreviewUrl = previewUrl || previewData?.url

  const hasContent = image || figureJson || effectiveContent || (chunks && chunks.length > 0)
    || sandboxSessionId || effectivePreviewUrl
  if (!hasContent) return null

  const imageUrl = image ? `/api/plots/${encodeURIComponent(image)}` : null

  const handleDownload = async () => {
    if (image) {
      const a = document.createElement('a')
      a.href = imageUrl
      a.download = image
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      return
    }

    if (filename) {
      const a = document.createElement('a')
      a.href = `/api/download/${encodeURIComponent(filename)}`
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      return
    }

    // Convert Plotly JSON to PNG via backend, then download
    setDownloading(true)
    try {
      const res = await fetch('/api/plots/from-json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ figure_json: figureJson }),
      })
      if (!res.ok) throw new Error('Export failed')
      const { filename: fname } = await res.json()
      const a = document.createElement('a')
      a.href = `/api/plots/${encodeURIComponent(fname)}`
      a.download = fname
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } catch (err) {
      console.error('Plotly PNG download failed:', err)
    } finally {
      setDownloading(false)
    }
  }

  const plotlyLayout = figureJson ? {
    ...(figureJson.layout || {}),
    autosize: true,
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    font: { color: '#2c2c2c' },
  } : null

  const renderBody = () => {
    if (figureJson) {
      return (
        <LazyPlot
          data={figureJson.data || []}
          layout={plotlyLayout}
          config={{ responsive: true, displayModeBar: true, displaylogo: false }}
          useResizeHandler
          style={{ width: '100%', height: '100%' }}
        />
      )
    }

    if (image) {
      return (
        <img
          src={imageUrl}
          alt={title || 'Generated plot'}
          className="canvas-panel-image"
        />
      )
    }

    if (sandboxSessionId) {
      return <SandboxTerminal sessionId={sandboxSessionId} />
    }

    if (effectiveContentType === 'terminal' && chunks) {
      return (
        <div className="terminal-container">
          {chunks.map((chunk, i) => (
            <div
              key={i}
              className={chunk.startsWith('[stderr]') ? 'terminal-line stderr' : 'terminal-line'}
            >
              {chunk}
            </div>
          ))}
          <div className="terminal-cursor" />
        </div>
      )
    }

    // Image preview from /api/preview
    if (effectiveContentType === 'image' && effectivePreviewUrl) {
      return (
        <img
          src={effectivePreviewUrl}
          alt={title || 'Image preview'}
          className="canvas-panel-image"
        />
      )
    }

    // PDF preview via iframe
    if (effectiveContentType === 'pdf' && effectivePreviewUrl) {
      return (
        <iframe
          src={effectivePreviewUrl}
          title={title || 'PDF Preview'}
          className="canvas-preview-iframe"
        />
      )
    }

    if (effectiveContentType === 'html' && effectiveContent) {
      return (
        <iframe
          srcDoc={effectiveContent}
          title={title || 'HTML Preview'}
          className="canvas-preview-iframe"
          sandbox="allow-scripts"
        />
      )
    }

    if (effectiveContentType === 'markdown' && effectiveContent) {
      return (
        <div className="canvas-preview-markdown">
          <ReactMarkdown>{effectiveContent}</ReactMarkdown>
        </div>
      )
    }

    if (effectiveContent) {
      // Pretty-print JSON when the content type signals it
      const isJson = (previewData?.content_type || '').includes('json')
      let displayed = effectiveContent
      if (isJson) {
        try { displayed = JSON.stringify(JSON.parse(effectiveContent), null, 2) } catch {}
      }
      return <pre className="canvas-preview-pre">{displayed}</pre>
    }

    return null
  }

  const icon =
    sandboxSessionId ? <Terminal size={15} /> :
    effectiveContentType === 'terminal' ? <Terminal size={15} /> :
    effectiveContentType === 'image' ? <ImageIcon size={15} /> :
    (effectiveContentType === 'markdown' || effectiveContentType === 'html' || effectiveContentType === 'pdf' || effectiveContentType === 'text') ? <FileText size={15} /> :
    <BarChart3 size={15} />

  return (
    <div className="canvas-panel" style={style}>
      <div className="canvas-panel-header">
        <div className="canvas-panel-title">
          {icon}
          <span>{title || 'Canvas'}</span>
        </div>
        <div className="canvas-panel-actions">
          {!sandboxSessionId && (
            <button
              className="canvas-panel-btn"
              onClick={handleDownload}
              title={figureJson ? "Export chart as PNG" : filename ? "Download file" : "Download"}
              disabled={downloading}
            >
              <Download size={14} />
            </button>
          )}
          <button
            className="canvas-panel-btn canvas-panel-close"
            onClick={onClose}
            title="Close panel"
          >
            <X size={16} />
          </button>
        </div>
      </div>
      <div className="canvas-panel-body">
        {renderBody()}
      </div>
      <div className="canvas-panel-footer">
        {image ? `Saved as ${image}` :
         filename ? filename :
         figureJson ? 'Interactive Plotly chart' :
         sandboxSessionId ? `Sandbox session ${sandboxSessionId}` :
         contentType === 'terminal' ? 'Live execution output' :
         'Preview'}
      </div>
    </div>
  )
}
