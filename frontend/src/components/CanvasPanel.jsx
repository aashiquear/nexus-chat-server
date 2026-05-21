import React, { useState, useEffect } from 'react'
import { X, Download, BarChart3, FileText, Terminal } from 'lucide-react'
import LazyPlot from './LazyPlot'
import ReactMarkdown from 'react-markdown'

function normalizeContentType(mime) {
  if (!mime) return 'text'
  if (mime.includes('markdown')) return 'markdown'
  if (mime.includes('html')) return 'html'
  return 'text'
}

export default function CanvasPanel({
  image,
  figureJson,
  content,
  contentType,
  chunks,
  filename,
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

  const effectiveContent = content || previewData?.content || ''
  const effectiveContentType = contentType || normalizeContentType(previewData?.content_type) || 'text'

  const hasContent = image || figureJson || effectiveContent || (chunks && chunks.length > 0)
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
      const { filename } = await res.json()
      const a = document.createElement('a')
      a.href = `/api/plots/${encodeURIComponent(filename)}`
      a.download = filename
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
      return (
        <pre className="canvas-preview-pre">{effectiveContent}</pre>
      )
    }

    return null
  }

  const icon = effectiveContentType === 'terminal' ? <Terminal size={15} /> :
               (effectiveContentType === 'markdown' || effectiveContentType === 'html' || effectiveContentType === 'text') ? <FileText size={15} /> :
               <BarChart3 size={15} />

  return (
    <div className="canvas-panel" style={style}>
      <div className="canvas-panel-header">
        <div className="canvas-panel-title">
          {icon}
          <span>{title || 'Canvas'}</span>
        </div>
        <div className="canvas-panel-actions">
          <button
            className="canvas-panel-btn"
            onClick={handleDownload}
            title={figureJson ? "Export chart as PNG" : filename ? "Download file" : "Download"}
            disabled={downloading}
          >
            <Download size={14} />
          </button>
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
         contentType === 'terminal' ? 'Live execution output' :
         'Preview'}
      </div>
    </div>
  )
}
