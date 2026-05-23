import React, { useState, useCallback, useEffect } from 'react'
import {
  User, Bot, Wrench, CheckCircle2, ChevronDown, ChevronRight,
  BarChart3, Image as ImageIcon, Eye, Copy, Check as CheckIcon,
  Brain, Timer, Download as DownloadIcon, Eye as EyeIcon,
  ChevronUp as ChevronUpIcon, ChevronDown as ChevronDownIcon,
  FileText as FileTextIcon, FileImage as FileImageIcon,
  FileCode as FileCodeIcon, FileSpreadsheet as FileSpreadsheetIcon,
  File as FileIcon, FileType2 as FileType2Icon,
  Terminal as TerminalIcon, ExternalLink as ExternalLinkIcon,
} from 'lucide-react'
import LazyPlot from './LazyPlot'

// Compute a suitable inline height for Plotly charts based on
// subplot grid rows and trace count so multiplots don't get cropped.
function computePlotHeight(figData) {
  const layout = figData.layout || {}
  if (layout.grid && layout.grid.rows) {
    return Math.min(900, Math.max(350, layout.grid.rows * 220))
  }
  const traceCount = (figData.data || []).length
  if (traceCount > 10) return 600
  if (traceCount > 5) return 450
  return 350
}

// Format an elapsed millisecond count for the response-duration chip.
// Sub-second values use one decimal so a snappy reply still shows
// motion ("0.4s"); longer ones break into m/s ("1m 12s").
function formatDuration(ms) {
  if (ms == null) return ''
  const sec = ms / 1000
  if (sec < 1) return `${sec.toFixed(1)}s`
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m ${s.toString().padStart(2, '0')}s`
}

function ResponseTimer({ startedAt, durationMs }) {
  // Live tick while streaming; freeze on the recorded final duration once
  // the "done" event lands. Both branches share the same chip styling so
  // the transition is invisible.
  const [now, setNow] = useState(() => Date.now())
  const isDone = durationMs != null

  useEffect(() => {
    if (isDone || !startedAt) return
    const id = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(id)
  }, [isDone, startedAt])

  if (!startedAt && !isDone) return null
  const elapsed = isDone ? durationMs : Math.max(0, now - startedAt)

  return (
    <div className={`response-timer ${isDone ? 'is-final' : 'is-live'}`}
         title={isDone ? 'Total response time' : 'Elapsed response time'}>
      <Timer size={11} />
      <span className="response-timer-value">{formatDuration(elapsed)}</span>
      {!isDone && <span className="response-timer-tag">elapsed</span>}
    </div>
  )
}

// Some models (Gemma "thinking" variants, DeepSeek-R1, Qwen-thinking,
// Anthropic extended thinking, etc.) emit their reasoning inside
// <think>…</think> or <thinking>…</thinking>. A single assistant turn
// may contain *multiple* thinking blocks — e.g. when the agentic loop
// runs ``think → tool call → tool result → think → final answer`` —
// so we parse the streamed content into an ordered list of segments
// and render each thinking block in its own collapsible box. This
// keeps every thought process visible even after additional rounds.
function parseMessageSegments(text) {
  if (!text) return []
  const segments = []
  // Two alternatives: a fully-closed <think>…</think> block, or an
  // unclosed <think>… that runs to the end of the streamed buffer
  // (the in-progress case).
  const re = /<(think(?:ing)?)>([\s\S]*?)<\/\1>|<(think(?:ing)?)>([\s\S]*)$/gi
  let cursor = 0
  let match
  while ((match = re.exec(text)) !== null) {
    if (match.index > cursor) {
      segments.push({ type: 'text', text: text.slice(cursor, match.index) })
    }
    if (match[1] !== undefined) {
      segments.push({ type: 'thinking', text: match[2], complete: true })
    } else {
      segments.push({ type: 'thinking', text: match[4], complete: false })
    }
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) {
    segments.push({ type: 'text', text: text.slice(cursor) })
  }
  return segments
}

function ThinkingBlock({ text, complete }) {
  // While streaming, leave the thought open so users see it tick along.
  // Once complete, collapse it to a clickable dropdown (closed by default).
  const [isOpen, setIsOpen] = useState(!complete)

  // Auto-collapse once the thought is sealed off, but only on the
  // transition (don't fight the user if they re-open it).
  const prevCompleteRef = React.useRef(complete)
  React.useEffect(() => {
    if (!prevCompleteRef.current && complete) setIsOpen(false)
    prevCompleteRef.current = complete
  }, [complete])

  const trimmed = (text || '').trim()
  if (!trimmed) return null

  return (
    <div className={`thinking-block ${complete ? 'thinking-complete' : 'thinking-active'}`}>
      <button
        className="thinking-header"
        onClick={() => setIsOpen((v) => !v)}
        type="button"
      >
        <span className="thinking-chevron">
          {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
        <Brain size={13} className={complete ? '' : 'thinking-pulse'} />
        <span className="thinking-label">
          {complete ? 'Thought process' : 'Thinking…'}
        </span>
      </button>
      {isOpen && (
        <div className="thinking-body">
          {trimmed}
          {!complete && <span className="thinking-cursor">▍</span>}
        </div>
      )}
    </div>
  )
}

// Map a language tag to a file extension and canvas content type.
const LANG_EXT_MAP = {
  markdown: { ext: 'md', contentType: 'markdown' },
  md: { ext: 'md', contentType: 'markdown' },
  html: { ext: 'html', contentType: 'html' },
  htm: { ext: 'html', contentType: 'html' },
  python: { ext: 'py', contentType: 'text' },
  py: { ext: 'py', contentType: 'text' },
  c: { ext: 'c', contentType: 'text' },
  cpp: { ext: 'cpp', contentType: 'text' },
  'c++': { ext: 'cpp', contentType: 'text' },
  cxx: { ext: 'cpp', contentType: 'text' },
  javascript: { ext: 'js', contentType: 'text' },
  js: { ext: 'js', contentType: 'text' },
  typescript: { ext: 'ts', contentType: 'text' },
  ts: { ext: 'ts', contentType: 'text' },
  java: { ext: 'java', contentType: 'text' },
  go: { ext: 'go', contentType: 'text' },
  rust: { ext: 'rs', contentType: 'text' },
  json: { ext: 'json', contentType: 'text' },
  yaml: { ext: 'yaml', contentType: 'text' },
  yml: { ext: 'yml', contentType: 'text' },
  xml: { ext: 'xml', contentType: 'text' },
  css: { ext: 'css', contentType: 'text' },
  sql: { ext: 'sql', contentType: 'text' },
  shell: { ext: 'sh', contentType: 'text' },
  bash: { ext: 'sh', contentType: 'text' },
  dockerfile: { ext: 'dockerfile', contentType: 'text' },
}

function getLangInfo(lang) {
  const key = (lang || '').toLowerCase().trim()
  return LANG_EXT_MAP[key] || { ext: 'txt', contentType: 'text' }
}

// Pick an icon + colour-class for a downloadable file. Anything that has
// a viewer in the canvas (PDF, HTML, Markdown, JSON, PNG, etc.) returns
// previewable: true so the card can show a Preview button.
function getFileTypeMeta(filename, contentType) {
  const name = (filename || '').toLowerCase()
  const ext = name.includes('.') ? name.split('.').pop() : ''
  const ct = (contentType || '').toLowerCase()

  const isPdf = ext === 'pdf' || ct.includes('pdf')
  const isImg = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'].includes(ext) || ct.startsWith('image/')
  const isHtml = ['html', 'htm'].includes(ext) || ct.includes('html')
  const isMd = ['md', 'markdown'].includes(ext) || ct.includes('markdown')
  const isJson = ext === 'json' || ct.includes('json')
  const isDoc = ['doc', 'docx'].includes(ext) || ct.includes('wordprocessing')
  const isPpt = ['ppt', 'pptx'].includes(ext) || ct.includes('presentation')
  const isSheet = ['xls', 'xlsx', 'csv'].includes(ext) || ct.includes('spreadsheet') || ct.includes('csv')
  const isCode = ['py', 'js', 'ts', 'jsx', 'tsx', 'c', 'cpp', 'java', 'go', 'rs', 'rb', 'php', 'sh', 'yaml', 'yml', 'xml', 'css'].includes(ext)
  const isText = ['txt', 'log'].includes(ext) || ct.startsWith('text/')

  let icon = <FileIcon size={14} />
  let className = 'file-icon-generic'
  if (isPdf) { icon = <FileType2Icon size={14} />; className = 'file-icon-pdf' }
  else if (isImg) { icon = <FileImageIcon size={14} />; className = 'file-icon-image' }
  else if (isDoc) { icon = <FileTextIcon size={14} />; className = 'file-icon-doc' }
  else if (isPpt) { icon = <FileTextIcon size={14} />; className = 'file-icon-ppt' }
  else if (isSheet) { icon = <FileSpreadsheetIcon size={14} />; className = 'file-icon-sheet' }
  else if (isCode || isJson) { icon = <FileCodeIcon size={14} />; className = 'file-icon-code' }
  else if (isMd || isHtml || isText) { icon = <FileTextIcon size={14} />; className = 'file-icon-text' }

  const previewable = isPdf || isImg || isHtml || isMd || isJson || isText || isCode

  return { icon, className, previewable, ext, isImg, isPdf }
}

// Copy-to-clipboard code block wrapper with language-aware download & canvas preview
function CodeBlock({ code, lang, onOpenCanvas }) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [code])

  const lines = code.split('\n')
  const isLarge = lines.length > 200
  const displayCode = isLarge && !expanded ? lines.slice(0, 100).join('\n') + '\n\n... (' + (lines.length - 100) + ' more lines)' : code

  const { ext, contentType } = getLangInfo(lang)
  const isKnownLang = !!(lang && LANG_EXT_MAP[(lang || '').toLowerCase().trim()])

  // Show download/preview for:
  //   - any block with a known file-type language tag (markdown, html, python, c, etc.)
  //   - any very large block regardless of language
  const showFileActions = isKnownLang || isLarge

  const handleDownload = useCallback(() => {
    const blob = new Blob([code], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `snippet.${ext}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [code, ext])

  const handleViewInCanvas = useCallback(() => {
    if (onOpenCanvas) {
      onOpenCanvas({
        content: code,
        contentType: contentType,
        title: `${lang || 'Snippet'}.${ext}`,
      })
    }
  }, [onOpenCanvas, code, contentType, lang, ext])

  return (
    <div className="code-block-wrapper">
      <div className="code-block-header">
        {lang && <span className="code-block-lang">{lang}</span>}
        <div className="code-block-actions">
          {showFileActions && (
            <>
              <button className="code-block-action" onClick={handleDownload} title={`Download .${ext}`}>
                <DownloadIcon size={12} />
              </button>
              <button className="code-block-action" onClick={handleViewInCanvas} title="View in Canvas">
                <EyeIcon size={12} />
              </button>
              {isLarge && (
                <button className="code-block-action" onClick={() => setExpanded((v) => !v)} title={expanded ? 'Collapse' : 'Expand'}>
                  {expanded ? <ChevronUpIcon size={12} /> : <ChevronDownIcon size={12} />}
                </button>
              )}
            </>
          )}
          <button
            className={`code-block-copy ${copied ? 'copied' : ''}`}
            onClick={handleCopy}
            title="Copy code"
          >
            {copied ? <CheckIcon size={12} /> : <Copy size={12} />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
      <pre className={`code-block-pre ${isLarge && !expanded ? 'truncated' : ''}`}>
        <code>{displayCode}</code>
      </pre>
    </div>
  )
}

// Parse markdown table lines into a structured table
function parseMarkdownTable(lines) {
  if (lines.length < 2) return null

  const parseRow = (line) =>
    line.split('|').map((c) => c.trim()).filter((c, i, arr) =>
      // filter out leading/trailing empty cells from || borders
      !(c === '' && (i === 0 || i === arr.length - 1))
    )

  const headers = parseRow(lines[0])
  // Check separator row (e.g. |---|---|)
  const sep = lines[1]
  if (!/^[\s|:-]+$/.test(sep)) return null

  const rows = lines.slice(2).map(parseRow)

  return { headers, rows }
}

// Apply inline markdown formatting: bold, italic, inline code, links, strikethrough, images
function formatInline(text) {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" class="md-inline-img" />')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
}

// Render rich cell content: inline formatting + list support
function renderCellContent(cellText) {
  if (!cellText) return null

  // Handle HTML lists: <ul><li>...</li></ul> or <ol><li>...</li></ol>
  if (/<[uo]l>/i.test(cellText)) {
    const html = formatInline(cellText)
    return <span dangerouslySetInnerHTML={{ __html: html }} />
  }

  // Split on <br>, \n, or before • bullets glued together
  // Also split glued numbered lists: "1. text2. text" → separate lines
  const lines = cellText
    .replace(/([^•\n])•/g, '$1\n•')
    .replace(/(\S)(\d+\.\s)/g, '$1\n$2')
    .split(/<br\s*\/?>|\n/)
    .map((l) => l.trim())
    .filter(Boolean)

  return renderLinesList(lines)
}

// Match unordered list markers: - or • followed by space, but NOT * (conflicts with bold **)
const BULLET_RE = /^[-•]\s+(.+)$/
// Match ordered list: "1. text", "2. text"
const ORDERED_RE = /^\d+\.\s+(.+)$/

// Shared helper: convert an array of text lines into React elements,
// recognizing list markers and applying inline formatting.
function renderLinesList(lines, keyPrefix = '') {
  const parts = []
  let bulletItems = []
  let orderedItems = []

  const flushBullets = () => {
    if (bulletItems.length === 0) return
    parts.push(
      <ul key={`${keyPrefix}ul-${parts.length}`} className="cell-list">
        {bulletItems.splice(0).map((item, j) => (
          <li key={j} dangerouslySetInnerHTML={{ __html: formatInline(item) }} />
        ))}
      </ul>
    )
  }

  const flushOrdered = () => {
    if (orderedItems.length === 0) return
    parts.push(
      <ol key={`${keyPrefix}ol-${parts.length}`} className="cell-list">
        {orderedItems.splice(0).map((item, j) => (
          <li key={j} dangerouslySetInnerHTML={{ __html: formatInline(item) }} />
        ))}
      </ol>
    )
  }

  for (const line of lines) {
    const bulletMatch = line.match(BULLET_RE)
    const orderedMatch = line.match(ORDERED_RE)
    if (bulletMatch) {
      flushOrdered()
      bulletItems.push(bulletMatch[1])
    } else if (orderedMatch) {
      flushBullets()
      orderedItems.push(orderedMatch[1])
    } else {
      flushBullets()
      flushOrdered()
      parts.push(
        <span key={`${keyPrefix}t-${parts.length}`} dangerouslySetInnerHTML={{ __html: formatInline(line) }} />
      )
    }
  }
  flushBullets()
  flushOrdered()

  if (parts.length === 0) {
    return <span dangerouslySetInnerHTML={{ __html: formatInline(lines.join(' ')) }} />
  }
  if (parts.length === 1) return parts[0]
  return <>{parts}</>
}

// Markdown-like renderer with tables & rich code blocks
function renderContent(text, onOpenCanvas) {
  if (!text) return null

  // Split into code blocks and text
  const parts = text.split(/(```[\s\S]*?```)/g)

  return parts.map((part, i) => {
    // Code block
    if (part.startsWith('```')) {
      const lines = part.slice(3, -3)
      const firstNewline = lines.indexOf('\n')
      const lang = firstNewline > 0 ? lines.slice(0, firstNewline).trim() : ''
      const code = firstNewline > 0 ? lines.slice(firstNewline + 1) : lines
      return <CodeBlock key={i} code={code} lang={lang} onOpenCanvas={onOpenCanvas} />
    }

    // Regular text: handle tables, inline code, bold, links
    const textLines = part.split('\n')
    const elements = []
    let idx = 0

    while (idx < textLines.length) {
      // Detect markdown table: line starts with | and next line is separator
      if (
        textLines[idx].trim().startsWith('|') &&
        idx + 1 < textLines.length &&
        /^[\s|:-]+$/.test(textLines[idx + 1])
      ) {
        // Collect all contiguous table lines
        const tableLines = []
        while (idx < textLines.length && textLines[idx].trim().startsWith('|')) {
          tableLines.push(textLines[idx])
          idx++
        }
        const table = parseMarkdownTable(tableLines)
        if (table) {
          elements.push(
            <div key={`${i}-table-${idx}`} className="md-table-wrapper">
              <table className="md-table">
                <thead>
                  <tr>
                    {table.headers.map((h, hi) => (
                      <th key={hi}>{renderCellContent(h)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => (
                        <td key={ci}>{renderCellContent(cell)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
          continue
        }
      }

      const line = textLines[idx]
      idx++

      if (!line.trim()) {
        elements.push(<br key={`${i}-${idx}`} />)
        continue
      }

      // Headings: # to ######
      const headingMatch = line.match(/^(#{1,6})\s+(.+)$/)
      if (headingMatch) {
        const level = headingMatch[1].length
        const Tag = `h${level}`
        elements.push(
          <Tag
            key={`${i}-${idx}`}
            className={`md-heading md-h${level}`}
            dangerouslySetInnerHTML={{ __html: formatInline(headingMatch[2]) }}
          />
        )
        continue
      }

      // Horizontal rule: ---, ***, ___
      if (/^[-*_]{3,}\s*$/.test(line.trim())) {
        elements.push(<hr key={`${i}-${idx}`} className="md-hr" />)
        continue
      }

      // Blockquote: > text (collect consecutive > lines)
      if (/^>\s?/.test(line.trim())) {
        const quoteLines = [line.replace(/^>\s?/, '')]
        while (idx < textLines.length && /^>\s?/.test(textLines[idx].trim())) {
          quoteLines.push(textLines[idx].replace(/^>\s?/, ''))
          idx++
        }
        elements.push(
          <blockquote
            key={`${i}-${idx}-bq`}
            className="md-blockquote"
            dangerouslySetInnerHTML={{ __html: quoteLines.map(formatInline).join('<br/>') }}
          />
        )
        continue
      }

      // Detect bullet lines: • or - at start (not * which conflicts with bold)
      // Also detect • glued mid-line, or numbered lists glued together
      if (/•/.test(line) || /^[-•]\s+/.test(line.trim()) || /^\d+\.\s+/.test(line.trim())) {
        // Split glued • bullets and glued numbered items into separate lines
        const splitLines = line
          .replace(/([^•\n])•/g, '$1\n•')
          .replace(/(\S)(\d+\.\s)/g, '$1\n$2')
          .split('\n')
          .map((l) => l.trim())
          .filter(Boolean)

        // For single-line items (not glued), also collect consecutive list lines
        if (splitLines.length === 1) {
          const isBullet = BULLET_RE.test(splitLines[0])
          const isOrdered = ORDERED_RE.test(splitLines[0])
          if (isBullet || isOrdered) {
            const re = isBullet ? BULLET_RE : ORDERED_RE
            const collected = [splitLines[0]]
            while (idx < textLines.length && re.test(textLines[idx].trim())) {
              collected.push(textLines[idx].trim())
              idx++
            }
            const Tag = isBullet ? 'ul' : 'ol'
            elements.push(
              <Tag key={`${i}-${idx}-clist`} className="md-list">
                {collected.map((item, j) => {
                  const content = item.replace(isBullet ? /^[-•]\s+/ : /^\d+\.\s+/, '')
                  return (
                    <li key={j} dangerouslySetInnerHTML={{ __html: formatInline(content) }} />
                  )
                })}
              </Tag>
            )
            continue
          }
        }

        // Check if they're bullet items (- or •, NOT *)
        const allBullets = splitLines.length > 1 && splitLines.every((l) => BULLET_RE.test(l))
        if (allBullets) {
          elements.push(
            <ul key={`${i}-${idx}-list`} className="md-list">
              {splitLines.map((item, j) => {
                const content = item.replace(/^[-•]\s+/, '')
                return (
                  <li key={j} dangerouslySetInnerHTML={{ __html: formatInline(content) }} />
                )
              })}
            </ul>
          )
          continue
        }

        // Check if they're numbered list items
        const allOrdered = splitLines.length > 1 && splitLines.every((l) => ORDERED_RE.test(l))
        if (allOrdered) {
          elements.push(
            <ol key={`${i}-${idx}-olist`} className="md-list">
              {splitLines.map((item, j) => {
                const content = item.replace(/^\d+\.\s+/, '')
                return (
                  <li key={j} dangerouslySetInnerHTML={{ __html: formatInline(content) }} />
                )
              })}
            </ol>
          )
          continue
        }
      }

      elements.push(
        <p
          key={`${i}-${idx}`}
          dangerouslySetInnerHTML={{ __html: formatInline(line) }}
        />
      )
    }

    return elements
  })
}

// Inspect a tool result and decide what canvas view (if any) it should
// reopen into. Returns null when the result has no canvas-renderable
// artifact, otherwise an object suitable for handing to onOpenCanvas.
function buildCanvasDataForResult(toolName, parsedResult) {
  if (!parsedResult) return null
  if (parsedResult.figure_json) {
    let fig = parsedResult.figure_json
    if (typeof fig === 'string') {
      try { fig = JSON.parse(fig) } catch { return null }
    }
    return {
      figureJson: fig,
      title: parsedResult.title || fig?.layout?.title?.text || 'Interactive Plot',
    }
  }
  if (parsedResult.plot_image) {
    return { image: parsedResult.plot_image, title: parsedResult.title || 'Generated Plot' }
  }
  if (parsedResult.downloadable) {
    const d = parsedResult.downloadable
    return { filename: d.filename, contentType: d.content_type, title: d.filename }
  }
  if (parsedResult.interactive_session) {
    return {
      sandboxSessionId: parsedResult.interactive_session,
      title: 'Sandbox Console',
      contentType: 'terminal',
      chunks: [],
    }
  }
  if (toolName === 'code_executor' && parsedResult.output) {
    return {
      title: 'Code Output',
      contentType: 'terminal',
      chunks: parsedResult.output.split('\n'),
    }
  }
  return null
}

function ToolCallDropdown({ toolCall, toolResult, onOpenCanvas }) {
  const [isOpen, setIsOpen] = useState(false)

  // Parse result to check for special types
  let parsed = null
  if (toolResult) {
    try { parsed = JSON.parse(toolResult.result) } catch (_e) { /* not JSON */ }
  }

  const hasError = parsed && parsed.error
  const canvasData = buildCanvasDataForResult(toolCall.name, parsed)

  // Determine status icon and label
  let statusIcon, statusText, statusColor
  if (!toolResult) {
    statusIcon = <Wrench size={12} className="tool-status-spin" />
    statusText = `Running ${toolCall.name}...`
    statusColor = 'var(--accent)'
  } else if (hasError) {
    statusIcon = <CheckCircle2 size={12} />
    statusText = `${toolCall.name} — error`
    statusColor = 'var(--error)'
  } else {
    statusIcon = <CheckCircle2 size={12} />
    statusText = `${toolCall.name} — done`
    statusColor = 'var(--success)'
  }

  return (
    <div className="tool-dropdown">
      <div className="tool-dropdown-header-row">
        <button
          className="tool-dropdown-header"
          onClick={() => setIsOpen(!isOpen)}
          style={{ color: statusColor }}
        >
          <span className="tool-dropdown-chevron">
            {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
          {statusIcon}
          <span className="tool-dropdown-label">{statusText}</span>
        </button>
        {canvasData && onOpenCanvas && (
          <button
            className="tool-dropdown-canvas-btn"
            onClick={(e) => { e.stopPropagation(); onOpenCanvas(canvasData) }}
            title="Reopen this result in the canvas panel"
          >
            <ExternalLinkIcon size={11} />
            <span>Open in canvas</span>
          </button>
        )}
      </div>
      {isOpen && (
        <div className="tool-dropdown-body">
          {/* Tool call arguments */}
          <div className="tool-dropdown-section">
            <div className="tool-dropdown-section-label">
              <Wrench size={11} /> Arguments
            </div>
            <div className="tool-dropdown-code">
              {toolCall.arguments
                ? (typeof toolCall.arguments === 'string'
                    ? toolCall.arguments
                    : JSON.stringify(toolCall.arguments, null, 2))
                : '(none)'}
            </div>
          </div>
          {/* Tool result */}
          {toolResult && (
            <div className="tool-dropdown-section">
              <div className="tool-dropdown-section-label" style={{ color: hasError ? 'var(--error)' : 'var(--success)' }}>
                <CheckCircle2 size={11} /> Result
              </div>
              <div className="tool-dropdown-code">
                {parsed ? JSON.stringify(parsed, null, 2) : toolResult.result}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Reusable card for a static PNG plot (matplotlib) with preview /
// download / copy actions, matching the code-block button row.
function PlotImageCard({ imageName, imageUrl, title, chartType, filename, onOpenCanvas }) {
  const [copied, setCopied] = useState(false)

  const handleDownload = useCallback(() => {
    const a = document.createElement('a')
    a.href = imageUrl
    a.download = imageName || 'plot.png'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }, [imageUrl, imageName])

  const handleCopyImage = useCallback(async () => {
    try {
      const blob = await fetch(imageUrl).then((r) => r.blob())
      if (navigator.clipboard && window.ClipboardItem) {
        await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })])
      } else {
        await navigator.clipboard.writeText(window.location.origin + imageUrl)
      }
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      console.error('Copy image failed:', e)
    }
  }, [imageUrl])

  return (
    <div className="plot-result-card">
      <div className="plot-result-header">
        <BarChart3 size={14} />
        <span>{title}</span>
        <div className="plot-result-actions">
          <button
            className="plot-result-action"
            onClick={() => onOpenCanvas && onOpenCanvas({ image: imageName, title })}
            title="Preview in canvas"
          >
            <EyeIcon size={12} />
          </button>
          <button
            className="plot-result-action"
            onClick={handleDownload}
            title="Download PNG"
          >
            <DownloadIcon size={12} />
          </button>
          <button
            className={`plot-result-action ${copied ? 'copied' : ''}`}
            onClick={handleCopyImage}
            title={copied ? 'Copied!' : 'Copy image'}
          >
            {copied ? <CheckIcon size={12} /> : <Copy size={12} />}
          </button>
        </div>
      </div>
      <div className="plot-result-preview">
        <img src={imageUrl} alt={title || 'Plot'} className="plot-result-img" />
      </div>
      <div className="plot-result-footer">
        <span className="plot-result-meta">
          {chartType ? `${chartType} chart — ${filename}` : filename}
        </span>
      </div>
    </div>
  )
}

// Plotly card: inline interactive chart plus the same Preview/Download/Copy
// actions as the static image card. Download converts the plotly JSON to
// PNG via /api/plots/from-json; copy copies the figure JSON.
function PlotlyChartCard({ figData, layout, title, fallbackImage, onOpenCanvas }) {
  const [copied, setCopied] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const handleCopyJson = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(figData, null, 2))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (e) {
      console.error('Copy plotly JSON failed:', e)
    }
  }, [figData])

  const handleDownload = useCallback(async () => {
    // If the tool already produced a static PNG, just download it.
    if (fallbackImage) {
      const a = document.createElement('a')
      a.href = `/api/plots/${encodeURIComponent(fallbackImage)}`
      a.download = fallbackImage
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      return
    }
    setDownloading(true)
    try {
      const res = await fetch('/api/plots/from-json', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ figure_json: figData }),
      })
      if (!res.ok) throw new Error('Export failed')
      const { filename } = await res.json()
      const a = document.createElement('a')
      a.href = `/api/plots/${encodeURIComponent(filename)}`
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } catch (e) {
      console.error('Plotly PNG download failed:', e)
    } finally {
      setDownloading(false)
    }
  }, [figData, fallbackImage])

  return (
    <div className="plot-result-card plotly-result-card">
      <div className="plot-result-header">
        <BarChart3 size={14} />
        <span>{title}</span>
        <div className="plot-result-actions">
          <button
            className="plot-result-action"
            onClick={() => onOpenCanvas && onOpenCanvas({ figureJson: figData, title })}
            title="Preview in canvas"
          >
            <EyeIcon size={12} />
          </button>
          <button
            className="plot-result-action"
            onClick={handleDownload}
            disabled={downloading}
            title="Download PNG"
          >
            <DownloadIcon size={12} />
          </button>
          <button
            className={`plot-result-action ${copied ? 'copied' : ''}`}
            onClick={handleCopyJson}
            title={copied ? 'Copied!' : 'Copy figure JSON'}
          >
            {copied ? <CheckIcon size={12} /> : <Copy size={12} />}
          </button>
        </div>
      </div>
      <div className="plotly-chart-container">
        <LazyPlot
          data={figData.data || []}
          layout={layout}
          config={{ responsive: true, displayModeBar: false }}
          useResizeHandler
          style={{ width: '100%', height: computePlotHeight(figData) }}
        />
      </div>
      <div className="plot-result-footer">
        <span className="plot-result-meta">Interactive Plotly chart</span>
      </div>
    </div>
  )
}

// Generic downloadable-file card. Icon and colour change by file type;
// Preview is only shown when the file is renderable in the canvas
// (text, code, JSON, Markdown, HTML, PDF, PNG/JPG, etc.).
function DownloadableFileCard({ file, onOpenCanvas }) {
  const downloadHref = file.download_url || `/api/download/${encodeURIComponent(file.filename)}`
  const { icon, className, previewable } = getFileTypeMeta(file.filename, file.content_type)
  const sizeLabel = file.size != null ? `${(file.size / 1024).toFixed(1)} KB` : ''

  return (
    <div className="downloadable-card">
      <div className="downloadable-header">
        <span className={`downloadable-icon ${className}`}>{icon}</span>
        <span className="downloadable-filename">{file.filename}</span>
        <span className="downloadable-meta">
          {file.content_type}{sizeLabel ? ` — ${sizeLabel}` : ''}
        </span>
      </div>
      <div className="downloadable-actions">
        {previewable && (
          <button
            className="downloadable-btn"
            onClick={() => onOpenCanvas && onOpenCanvas({
              filename: file.filename,
              contentType: file.content_type,
              previewUrl: downloadHref,
              title: file.filename,
            })}
          >
            <EyeIcon size={13} /> Preview
          </button>
        )}
        <a
          className="downloadable-btn"
          href={downloadHref}
          download={file.filename}
        >
          <DownloadIcon size={13} /> Download
        </a>
      </div>
    </div>
  )
}

export default function ChatMessage({ message, onOpenCanvas }) {
  const isUser = message.role === 'user'

  // Match tool calls with their results
  const toolCalls = message.toolCalls || []
  const toolResults = message.toolResults || []

  // Build paired list of tool calls + results
  const toolPairs = toolCalls.map((tc, i) => {
    const tr = toolResults.find((r) => r.name === tc.name) || toolResults[i]
    return { call: tc, result: tr }
  })

  // Find any plot, SVG, or downloadable results for inline display
  const specialResults = toolResults.filter((tr) => {
    try {
      const p = JSON.parse(tr.result)
      return (p && p.svg && p.svg.includes('<svg')) || (p && p.plot_image) || (p && p.figure_json) || (p && p.downloadable)
    } catch (_e) { return false }
  })

  // For assistant turns, walk the streamed buffer as an ordered list of
  // segments so every <think>…</think> block (there can be several per
  // turn) renders in its own collapsible box, interleaved with the text
  // it was emitted alongside. User messages skip this entirely.
  const segments = isUser ? null : parseMessageSegments(message.content)

  return (
    <div className={`message ${isUser ? 'message-user' : ''}`}>
      <div className="message-header">
        <div className={`message-avatar ${message.role}`}>
          {isUser ? <User size={14} /> : <Bot size={14} />}
        </div>
        <span className="message-role">
          {isUser ? 'You' : 'Assistant'}
        </span>
      </div>
      <div className="message-body">
        {isUser ? (
          <p>{message.content}</p>
        ) : (
          segments.map((seg, i) => {
            if (seg.type === 'thinking') {
              return (
                <ThinkingBlock
                  key={`think-${i}`}
                  text={seg.text}
                  complete={seg.complete}
                />
              )
            }
            return (
              <React.Fragment key={`text-${i}`}>
                {renderContent(seg.text, onOpenCanvas)}
              </React.Fragment>
            )
          })
        )}

        {/* Collapsible tool reasoning */}
        {toolPairs.length > 0 && (
          <div className="tool-reasoning-group">
            {toolPairs.map((pair, i) => (
              <ToolCallDropdown
                key={i}
                toolCall={pair.call}
                toolResult={pair.result}
                onOpenCanvas={onOpenCanvas}
              />
            ))}
          </div>
        )}

        {/* Special inline results: SVG diagrams */}
        {specialResults.map((tr, i) => {
          let parsed
          try { parsed = JSON.parse(tr.result) } catch (_e) { return null }

          // SVG diagram
          if (parsed.svg && parsed.svg.includes('<svg')) {
            return (
              <div key={`svg-${i}`} className="svg-diagram-card">
                {parsed.title && (
                  <div className="svg-diagram-title">{parsed.title}</div>
                )}
                <div
                  className="svg-diagram-render"
                  dangerouslySetInnerHTML={{ __html: parsed.svg }}
                />
                {parsed.filename && (
                  <div className="svg-diagram-footer">
                    Saved as {parsed.filename}
                  </div>
                )}
              </div>
            )
          }

          // Graph plot — show thumbnail + open in canvas. If the tool
          // also produced an interactive figure_json, the Plotly
          // renderer below picks it up instead; otherwise we render the
          // static PNG here with preview/download/copy actions.
          if (parsed.plot_image && !parsed.figure_json) {
            const imgUrl = `/api/plots/${encodeURIComponent(parsed.plot_image)}`
            return (
              <PlotImageCard
                key={`plot-${i}`}
                imageName={parsed.plot_image}
                imageUrl={imgUrl}
                title={parsed.title || 'Generated Plot'}
                chartType={parsed.chart_type}
                filename={parsed.filename}
                onOpenCanvas={onOpenCanvas}
              />
            )
          }

          // Plotly JSON — render interactive chart inline
          if (parsed.figure_json) {
            let figData = null
            let figureJsonError = null

            if (typeof parsed.figure_json === 'string') {
              try {
                figData = JSON.parse(parsed.figure_json)
              } catch (_e) {
                figureJsonError = 'Invalid plot JSON'
              }
            } else if (typeof parsed.figure_json === 'object' && parsed.figure_json !== null) {
              figData = parsed.figure_json
            } else {
              figureJsonError = 'Unsupported plot JSON format'
            }

            if (!figData || typeof figData !== 'object') {
              return (
                <div key={`plotly-${i}`} className="plot-result-card plotly-result-card">
                  <div className="plot-result-header">
                    <BarChart3 size={14} />
                    <span>{parsed.title || 'Interactive Plot'}</span>
                  </div>
                  <div className="plot-result-footer">
                    <span className="plot-result-meta">
                      {figureJsonError || 'Unable to render interactive plot'}
                    </span>
                  </div>
                  <pre className="message-pre">
                    {typeof parsed.figure_json === 'string'
                      ? parsed.figure_json
                      : JSON.stringify(parsed.figure_json, null, 2)}
                  </pre>
                </div>
              )
            }
            const layout = {
              ...(figData.layout || {}),
              autosize: true,
              margin: { l: 50, r: 30, t: 40, b: 40 },
              paper_bgcolor: 'transparent',
              plot_bgcolor: 'transparent',
              font: { color: '#c9d1d9' },
            }
            const plotTitle = parsed.title || figData.layout?.title?.text || 'Interactive Plot'
            return (
              <PlotlyChartCard
                key={`plotly-${i}`}
                figData={figData}
                layout={layout}
                title={plotTitle}
                fallbackImage={parsed.plot_image}
                onOpenCanvas={onOpenCanvas}
              />
            )
          }

          // Downloadable file result
          if (parsed.downloadable) {
            const d = parsed.downloadable
            return (
              <DownloadableFileCard
                key={`dl-${i}`}
                file={d}
                onOpenCanvas={onOpenCanvas}
              />
            )
          }

          return null
        })}
      </div>
      {!isUser && (message.startedAt || message.durationMs != null) && (
        <ResponseTimer
          startedAt={message.startedAt}
          durationMs={message.durationMs}
        />
      )}
    </div>
  )
}

export function TypingIndicator() {
  return (
    <div className="message">
      <div className="message-header">
        <div className="message-avatar assistant">
          <Bot size={14} />
        </div>
        <span className="message-role">Assistant</span>
      </div>
      <div className="typing-indicator typing-indicator-connected" aria-label="Assistant is responding">
        <span className="typing-link" />
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className="typing-dot" style={{ animationDelay: `${i * 0.12}s` }} />
        ))}
      </div>
    </div>
  )
}
