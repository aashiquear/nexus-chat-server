import React, { useRef, useEffect, useState, useCallback } from 'react'
import {
  Send, Paperclip, X, BarChart2, SlidersHorizontal, ImagePlus, Square,
  Sparkles, Brain, MessageSquare, Wrench, CircleCheck, AlertCircle, Scissors,
} from 'lucide-react'
import StatsPopup from './StatsPopup'
import GenerationSettings from './GenerationSettings'

// Map an llmStatus.stage to the icon + label shown inside the pop-up.
// `stage: 'idle'` collapses the pop-up to a quiet "awaiting prompt" state.
const STATUS_VIEW = {
  initiated:      { Icon: Sparkles,     label: 'Initiated' },
  compacting:     { Icon: Scissors,     label: 'Compacting context' },
  thinking:       { Icon: Brain,        label: 'Thinking' },
  responding:     { Icon: MessageSquare,label: 'Responding' },
  tool_calling:   { Icon: Wrench,       label: 'Calling tool' },
  tool_executing: { Icon: Wrench,       label: 'Executing tool' },
  idle:           { Icon: CircleCheck,  label: 'Awaiting chat prompt' },
  error:          { Icon: AlertCircle,  label: 'Error' },
}

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
const MAX_IMAGE_EDGE = 1568 // Claude/GPT sweet spot; Ollama downsizes anyway
const MAX_IMAGES = 6

// Downscale + re-encode an image file to a data URL so a 12 MP photo does
// not become a 20 MB websocket frame.
export async function fileToImageDataUrl(file) {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise((resolve, reject) => {
      const i = new Image()
      i.onload = () => resolve(i)
      i.onerror = reject
      i.src = url
    })
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(img.width, img.height))
    if (scale === 1 && file.size < 1.5 * 1024 * 1024 && file.type !== 'image/gif') {
      return await new Promise((resolve) => {
        const r = new FileReader()
        r.onload = () => resolve(r.result)
        r.readAsDataURL(file)
      })
    }
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(img.width * scale)
    canvas.height = Math.round(img.height * scale)
    canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
    const hasAlpha = file.type === 'image/png' || file.type === 'image/webp' || file.type === 'image/gif'
    return canvas.toDataURL(hasAlpha ? 'image/png' : 'image/jpeg', 0.9)
  } finally {
    URL.revokeObjectURL(url)
  }
}

function ChatbotPulse() {
  return (
    <svg className="chatbot-pulse" width="22" height="14" viewBox="0 0 22 14" aria-hidden="true">
      <line className="chatbot-pulse-spoke" x1="3"  y1="3"  x2="11" y2="7" />
      <line className="chatbot-pulse-spoke" x1="19" y1="3"  x2="11" y2="7" />
      <line className="chatbot-pulse-spoke" x1="6"  y1="11" x2="11" y2="7" />
      <line className="chatbot-pulse-spoke" x1="16" y1="11" x2="11" y2="7" />
      <circle className="chatbot-pulse-dot" cx="3"  cy="3"  r="1.6" style={{ animationDelay: '0s' }} />
      <circle className="chatbot-pulse-dot" cx="19" cy="3"  r="1.6" style={{ animationDelay: '0.15s' }} />
      <circle className="chatbot-pulse-dot" cx="6"  cy="11" r="1.6" style={{ animationDelay: '0.3s' }} />
      <circle className="chatbot-pulse-dot" cx="16" cy="11" r="1.6" style={{ animationDelay: '0.45s' }} />
      <circle className="chatbot-pulse-core" cx="11" cy="7" r="2" />
    </svg>
  )
}

function LlmStatusPopup({ status, isStreaming, notice }) {
  const stage = status?.stage || 'idle'
  const view = STATUS_VIEW[stage] || STATUS_VIEW.idle
  const { Icon, label } = view
  const active = isStreaming && stage !== 'idle' && stage !== 'error'
  return (
    <div className={`llm-status-popup stage-${stage} ${active ? 'is-active' : 'is-quiet'}`}>
      <Icon size={14} className="llm-status-icon" />
      <span className="llm-status-label">
        {label}
        {status?.detail ? <span className="llm-status-detail"> · {status.detail}</span> : null}
        {!status?.detail && notice ? <span className="llm-status-detail"> · {notice}</span> : null}
      </span>
      {active && <ChatbotPulse />}
    </div>
  )
}

function CircularProgress({ progress, size = 32, strokeWidth = 3 }) {
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (progress / 100) * circumference
  return (
    <svg width={size} height={size} className="circular-progress">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--border)" strokeWidth={strokeWidth} />
      <circle
        cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--accent)" strokeWidth={strokeWidth}
        strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: 'stroke-dashoffset 0.2s ease' }}
      />
      <text x={size / 2} y={size / 2} textAnchor="middle" dominantBaseline="central" fill="var(--text-secondary)" fontSize="9" fontWeight="600" fontFamily="var(--font-body)">
        {progress}%
      </text>
    </svg>
  )
}

export default function ChatInput({
  value,
  onChange,
  onSend,
  onUpload,
  disabled,
  selectedFiles,
  onRemoveFile,
  uploadProgress,
  conversationStats,
  lastResponseStats,
  llmStatus,
  isStreaming,
  notice,
  // image attachments for vision models
  images,
  onImagesChange,
  visionCapable,
  // generation settings control
  generationProps,
  contextInfo,
}) {
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)
  const imageInputRef = useRef(null)
  const [showStats, setShowStats] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 180) + 'px'
    }
  }, [value])

  const canSend = (value.trim() || (images && images.length > 0)) && !disabled

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (canSend) onSend()
    }
  }

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0]
    if (file) {
      await onUpload(file)
      e.target.value = ''
    }
  }

  const addImages = useCallback(
    async (files) => {
      if (!onImagesChange) return
      const picked = Array.from(files || []).filter((f) => IMAGE_TYPES.includes(f.type))
      if (!picked.length) return
      const encoded = []
      for (const f of picked.slice(0, MAX_IMAGES)) {
        try {
          encoded.push({ name: f.name || 'image', dataUrl: await fileToImageDataUrl(f) })
        } catch (err) {
          console.error('Image encode failed:', err)
        }
      }
      onImagesChange([...(images || []), ...encoded].slice(0, MAX_IMAGES))
    },
    [images, onImagesChange],
  )

  const handleImageChange = async (e) => {
    await addImages(e.target.files)
    e.target.value = ''
  }

  // Paste / drop images straight into the prompt box.
  const handlePaste = (e) => {
    const items = Array.from(e.clipboardData?.items || [])
    const files = items.filter((i) => i.kind === 'file').map((i) => i.getAsFile()).filter(Boolean)
    if (files.length) {
      e.preventDefault()
      addImages(files)
    }
  }
  const handleDrop = (e) => {
    const files = Array.from(e.dataTransfer?.files || [])
    const imgs = files.filter((f) => IMAGE_TYPES.includes(f.type))
    if (imgs.length) {
      e.preventDefault()
      addImages(imgs)
    } else if (files.length && onUpload) {
      e.preventDefault()
      onUpload(files[0])
    }
  }

  const removeImage = (idx) => onImagesChange && onImagesChange(images.filter((_, i) => i !== idx))

  const ctxPct =
    contextInfo?.budget && contextInfo?.lastPromptTokens
      ? Math.min(100, Math.round((contextInfo.lastPromptTokens / contextInfo.budget) * 100))
      : null

  return (
    <div className="input-area" onDrop={handleDrop} onDragOver={(e) => e.preventDefault()}>
      <div className="input-container">
        <LlmStatusPopup status={llmStatus} isStreaming={isStreaming} notice={notice} />
        {uploadProgress && (
          <div className={`upload-progress-chip stage-${uploadProgress.stage || 'uploading'}`}>
            <CircularProgress progress={uploadProgress.percent || 0} />
            <div className="upload-progress-chip-text">
              <span className="upload-progress-chip-name">{uploadProgress.filename}</span>
              <span className="upload-progress-chip-stage">
                {uploadProgress.stage === 'embedding'
                  ? 'Embedding into vector store…'
                  : uploadProgress.stage === 'error'
                  ? 'Failed'
                  : 'Uploading…'}
              </span>
            </div>
          </div>
        )}
        {(selectedFiles.length > 0 || (images && images.length > 0)) && (
          <div className="file-chips">
            {images &&
              images.map((img, i) => (
                <div key={`img-${i}`} className="file-chip image-chip" title={img.name}>
                  <img src={img.dataUrl} alt={img.name} />
                  <span className="file-chip-remove" onClick={() => removeImage(i)}>
                    <X size={12} />
                  </span>
                </div>
              ))}
            {selectedFiles.map((name) => (
              <div key={name} className="file-chip">
                {name}
                <span className="file-chip-remove" onClick={() => onRemoveFile(name)}>
                  <X size={12} />
                </span>
              </div>
            ))}
          </div>
        )}
        {images && images.length > 0 && visionCapable === false && (
          <div className="input-warning">
            <AlertCircle size={12} /> The selected model is not vision-capable; images will be ignored. Pick a vision model (gemma4, llama4, qwen3-vl, Claude, GPT…).
          </div>
        )}
        <div className="input-wrapper">
          <button className="upload-btn" onClick={() => fileInputRef.current?.click()} title="Upload a file for RAG / tools">
            <Paperclip size={17} />
          </button>
          <button
            className={`upload-btn ${images && images.length ? 'active' : ''}`}
            onClick={() => imageInputRef.current?.click()}
            title={visionCapable === false ? 'Attach image (selected model is not vision-capable)' : 'Attach image for the vision model'}
          >
            <ImagePlus size={17} />
          </button>
          <div className="stats-btn-wrapper">
            <button
              className={`stats-btn ${showSettings ? 'active' : ''}`}
              onClick={() => {
                setShowSettings(!showSettings)
                setShowStats(false)
              }}
              title="Model & context settings"
            >
              <SlidersHorizontal size={17} />
              {ctxPct != null && <span className={`ctx-badge ${ctxPct > 85 ? 'hot' : ''}`}>{ctxPct}%</span>}
            </button>
            {showSettings && generationProps && (
              <GenerationSettings {...generationProps} contextInfo={contextInfo} onClose={() => setShowSettings(false)} />
            )}
          </div>
          <div className="stats-btn-wrapper">
            <button
              className={`stats-btn ${showStats ? 'active' : ''}`}
              onClick={() => {
                setShowStats(!showStats)
                setShowSettings(false)
              }}
              title="Conversation statistics"
            >
              <BarChart2 size={17} />
            </button>
            {showStats && conversationStats && (
              <StatsPopup stats={conversationStats} lastResponse={lastResponseStats} onClose={() => setShowStats(false)} />
            )}
          </div>
          <input ref={fileInputRef} type="file" hidden onChange={handleFileChange} />
          <input ref={imageInputRef} type="file" hidden accept={IMAGE_TYPES.join(',')} multiple onChange={handleImageChange} />
          <textarea
            ref={textareaRef}
            className="chat-input"
            placeholder="Type your message… (Shift+Enter for new line, paste or drop images)"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            rows={1}
            disabled={disabled && !isStreaming}
          />
          <button className="send-btn" onClick={onSend} disabled={!canSend} title="Send message">
            {isStreaming ? <Square size={14} /> : <Send size={16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
