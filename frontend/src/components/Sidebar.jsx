import React, { useState } from 'react'
import {
  Check, Calculator, Search, Code2, Clock, FileText,
  Wrench, Trash2, Paperclip, MessageSquarePlus,
  Database, Server, RefreshCw, ChevronDown, ChevronRight, Image,
  MessageCircle, BarChart3, Eye,
} from 'lucide-react'
import NexusLogoIcon from './NexusLogoIcon'

const ICON_MAP = {
  calculator: Calculator,
  search: Search,
  code: Code2,
  clock: Clock,
  'file-text': FileText,
  database: Database,
  server: Server,
  image: Image,
  'bar-chart': BarChart3,
  eye: Eye,
  'file-generator': FileText,
}

// Collapse long tool descriptions so the MCP list stays scannable.
// Shows a single-line summary; expands to full text on click.
const TOOL_DESC_SUMMARY_LIMIT = 80

function ToolDescription({ text }) {
  const [expanded, setExpanded] = useState(false)
  if (!text) return null

  const isLong = text.length > TOOL_DESC_SUMMARY_LIMIT
  if (!isLong) return <div className="toggle-desc">{text}</div>

  const summary = text.slice(0, TOOL_DESC_SUMMARY_LIMIT).trimEnd() + '…'
  const handleClick = (e) => {
    e.stopPropagation()
    setExpanded((v) => !v)
  }
  return (
    <div className={`toggle-desc tool-desc-collapsible ${expanded ? 'expanded' : ''}`}>
      <span className="tool-desc-text">{expanded ? text : summary}</span>
      <button
        type="button"
        className="tool-desc-toggle"
        onClick={handleClick}
        title={expanded ? 'Show less' : 'Show full description'}
      >
        {expanded ? 'less' : 'more'}
      </button>
    </div>
  )
}

function AccordionSection({ label, count, isOpen, onToggle, children }) {
  const Chevron = isOpen ? ChevronDown : ChevronRight

  return (
    <div className={`accordion-section ${isOpen ? 'accordion-section-open' : ''}`}>
      <button
        className="collapsible-header"
        onClick={onToggle}
      >
        <Chevron size={13} className="collapsible-chevron" />
        <span className="sidebar-section-label" style={{ marginBottom: 0 }}>
          {label}
        </span>
        {count > 0 && (
          <span className="collapsible-count">{count}</span>
        )}
      </button>
      {isOpen && (
        <div className="accordion-body">
          {children}
        </div>
      )}
    </div>
  )
}

function CollapsibleSection({ label, count, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)
  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <div className="collapsible-section">
      <button
        className="collapsible-header"
        onClick={() => setOpen(!open)}
      >
        <Chevron size={13} className="collapsible-chevron" />
        <span className="sidebar-section-label" style={{ marginBottom: 0 }}>
          {label}
        </span>
        {count > 0 && (
          <span className="collapsible-count">{count}</span>
        )}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  )
}

export default function Sidebar({
  models,
  selectedModel,
  onModelChange,
  tools,
  selectedTools,
  onToggleTool,
  mcpServers,
  onReconnectMCP,
  files,
  selectedFiles,
  onToggleFile,
  onUpload,
  onDeleteFile,
  uploadProgress,
  conversations,
  activeConversationId,
  onSelectConversation,
  onDeleteConversation,
  onNewChat,
  isOpen,
  onClose,
}) {
  const fileInputRef = React.useRef(null)

  // Accordion state: only one of 'tools', 'mcp', 'rag' open at a time
  const [openSection, setOpenSection] = useState('tools')
  const toggleSection = (key) => setOpenSection((prev) => prev === key ? null : key)

  const handleUploadClick = () => fileInputRef.current?.click()

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0]
    if (file) {
      await onUpload(file)
      e.target.value = ''
    }
  }

  // Group models by provider
  const grouped = {}
  models.forEach((m) => {
    const p = m.provider || 'unknown'
    if (!grouped[p]) grouped[p] = []
    grouped[p].push(m)
  })

  const builtinTools = tools.filter((t) => t.source !== 'mcp')
  const activeBuiltinCount = builtinTools.filter((t) => selectedTools.includes(t.id)).length
  const mcpToolCount = tools.filter((t) => t.source === 'mcp' && selectedTools.includes(t.id)).length
  const activeFileCount = selectedFiles.length

  return (
    <>
      {isOpen && <div className="sidebar-overlay" onClick={onClose} />}
      <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
        <div className="sidebar-header">
          <div className="sidebar-logo">
            <NexusLogoIcon size={28} />
          </div>
          <span className="sidebar-title">Nexus Chat</span>
        </div>

        <button className="new-chat-btn" onClick={onNewChat}>
          <MessageSquarePlus size={15} />
          New Conversation
        </button>

        {/* Conversation threads */}
        {conversations && conversations.length > 0 && (
          <CollapsibleSection
            label="Conversations"
            count={conversations.length}
            defaultOpen
          >
            <div className="thread-list">
              {conversations.map((conv) => {
                const isActive = conv.id === activeConversationId
                return (
                  <div
                    key={conv.id}
                    className={`thread-item ${isActive ? 'active' : ''}`}
                    onClick={() => onSelectConversation(conv.id)}
                  >
                    <MessageCircle size={13} className="thread-icon" />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="thread-title">{conv.title}</div>
                      <div className="thread-meta">
                        {conv.message_count} messages
                      </div>
                    </div>
                    <button
                      className="thread-delete"
                      onClick={(e) => {
                        e.stopPropagation()
                        onDeleteConversation(conv.id)
                      }}
                      title="Delete conversation"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                )
              })}
            </div>
          </CollapsibleSection>
        )}

        {/* Model selector */}
        <div className="sidebar-section">
          <div className="sidebar-section-label">Model</div>
        </div>
        <div className="model-selector">
          <select
            className="model-select"
            value={selectedModel}
            onChange={(e) => onModelChange(e.target.value)}
          >
            {Object.entries(grouped).map(([provider, provModels]) => (
              <optgroup key={provider} label={provider.charAt(0).toUpperCase() + provider.slice(1)}>
                {provModels.map((m) => {
                  // remote_available === null  → no probe info, treat as available
                  // remote_available === false → known not on the remote
                  // remote_available === true  → confirmed on the remote
                  const remoteUnknown = m.remote_available === null || m.remote_available === undefined
                  const onRemote = remoteUnknown ? true : !!m.remote_available
                  const usable = m.available && onRemote
                  const suffix = !m.available
                    ? ' (not configured)'
                    : !onRemote
                    ? ' • unavailable'
                    : m.thinking
                    ? ' • thinking'
                    : ''
                  return (
                    <option key={m.id} value={m.id} disabled={!usable}>
                      {usable ? '● ' : '○ '}{m.name}{suffix}
                    </option>
                  )
                })}
              </optgroup>
            ))}
          </select>
        </div>

        <div className="sidebar-body">
          {/* Built-in Tools */}
          <AccordionSection
            label="Tools"
            count={activeBuiltinCount}
            isOpen={openSection === 'tools'}
            onToggle={() => toggleSection('tools')}
          >
            {builtinTools.map((tool) => {
              const active = selectedTools.includes(tool.id)
              const Icon = ICON_MAP[tool.icon] || Wrench
              return (
                <div
                  key={tool.id}
                  className="toggle-item"
                  onClick={() => onToggleTool(tool.id)}
                >
                  <div className={`toggle-check ${active ? 'active' : ''}`}>
                    {active && <Check size={11} color="#fff" strokeWidth={3} />}
                  </div>
                  <Icon size={15} className="toggle-icon" />
                  <div className="toggle-text">
                    <div className="toggle-label">{tool.name}</div>
                    <ToolDescription text={tool.description} />
                  </div>
                </div>
              )
            })}
          </AccordionSection>

          {/* MCP Servers */}
          {mcpServers && mcpServers.length > 0 && (
            <AccordionSection
              label="MCP Servers"
              count={mcpToolCount}
              isOpen={openSection === 'mcp'}
              onToggle={() => toggleSection('mcp')}
            >
              {mcpServers.map((srv) => {
                const mcpTools = tools.filter(
                  (t) => t.source === 'mcp' && t.server === srv.id
                )
                const allSelected = mcpTools.length > 0 && mcpTools.every((t) => selectedTools.includes(t.id))
                const someSelected = mcpTools.some((t) => selectedTools.includes(t.id))
                return (
                  <div key={srv.id} style={{ marginBottom: 4 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '4px 16px',
                        fontSize: 12,
                      }}
                    >
                      <div
                        className={`toggle-check ${allSelected ? 'active' : ''} ${someSelected && !allSelected ? 'partial' : ''}`}
                        style={{ cursor: 'pointer' }}
                        onClick={() => {
                          const toolIds = mcpTools.map((t) => t.id)
                          if (allSelected) {
                            // Deselect all tools from this server
                            toolIds.forEach((id) => onToggleTool(id))
                          } else {
                            // Select all tools from this server
                            toolIds.forEach((id) => {
                              if (!selectedTools.includes(id)) onToggleTool(id)
                            })
                          }
                        }}
                        title={allSelected ? 'Deselect all' : 'Select all'}
                      >
                        {allSelected && <Check size={11} color="#fff" strokeWidth={3} />}
                        {someSelected && !allSelected && <span style={{ color: '#fff', fontSize: 10, fontWeight: 700 }}>–</span>}
                      </div>
                      <Server size={13} style={{ opacity: 0.6 }} />
                      <span style={{ fontWeight: 500, flex: 1 }}>{srv.name}</span>
                      <span
                        style={{
                          width: 7,
                          height: 7,
                          borderRadius: '50%',
                          background: srv.connected ? 'var(--success)' : 'var(--error)',
                          display: 'inline-block',
                        }}
                        title={srv.connected ? 'Connected' : 'Disconnected'}
                      />
                      {!srv.connected && (
                        <button
                          style={{
                            background: 'none',
                            border: 'none',
                            cursor: 'pointer',
                            color: 'var(--text-tertiary)',
                            padding: 2,
                          }}
                          onClick={() => onReconnectMCP(srv.id)}
                          title="Reconnect"
                        >
                          <RefreshCw size={12} />
                        </button>
                      )}
                    </div>
                    {mcpTools.map((tool) => {
                      const active = selectedTools.includes(tool.id)
                      const Icon = ICON_MAP[tool.icon] || Database
                      return (
                        <div
                          key={tool.id}
                          className="toggle-item"
                          onClick={() => onToggleTool(tool.id)}
                        >
                          <div className={`toggle-check ${active ? 'active' : ''}`}>
                            {active && <Check size={11} color="#fff" strokeWidth={3} />}
                          </div>
                          <Icon size={15} className="toggle-icon" />
                          <div className="toggle-text">
                            <div className="toggle-label">{tool.name}</div>
                            <ToolDescription text={tool.description} />
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )
              })}
            </AccordionSection>
          )}

          {/* Files */}
          <AccordionSection
            label="Files (RAG)"
            count={activeFileCount}
            isOpen={openSection === 'rag'}
            onToggle={() => toggleSection('rag')}
          >
            {files.length === 0 && (
              <div style={{ padding: '4px 16px', fontSize: 12.5, color: 'var(--text-tertiary)' }}>
                No files uploaded yet
              </div>
            )}
            {files.map((file) => {
              const active = selectedFiles.includes(file.name)
              return (
                <div
                  key={file.name}
                  className="toggle-item"
                  onClick={() => onToggleFile(file.name)}
                >
                  <div className={`toggle-check ${active ? 'active' : ''}`}>
                    {active && <Check size={11} color="#fff" strokeWidth={3} />}
                  </div>
                  <FileText size={15} className="toggle-icon" />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="toggle-label" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {file.name}
                    </div>
                    <div className="toggle-desc">
                      {(file.size / 1024).toFixed(1)} KB
                    </div>
                  </div>
                  <button
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: 'var(--text-tertiary)', padding: 2,
                    }}
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteFile(file.name)
                    }}
                    title="Delete file"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              )
            })}

            {/* Upload progress indicator: shows both upload and embedding stages */}
            {uploadProgress && (
              <div className="upload-progress-item">
                <FileText size={15} className="toggle-icon" />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="toggle-label" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {uploadProgress.filename}
                  </div>
                  <div className="upload-progress-bar-track">
                    <div
                      className={`upload-progress-bar-fill ${uploadProgress.stage === 'embedding' ? 'embedding' : ''}`}
                      style={{ width: `${uploadProgress.percent || 0}%` }}
                    />
                  </div>
                  <div className="toggle-desc" style={{ marginTop: 2 }}>
                    {uploadProgress.percent || 0}%{' '}
                    {uploadProgress.stage === 'embedding'
                      ? 'embedding…'
                      : uploadProgress.stage === 'error'
                      ? 'failed'
                      : 'uploading…'}
                  </div>
                </div>
              </div>
            )}

            <button
              className="new-chat-btn"
              style={{ margin: '8px 16px' }}
              onClick={handleUploadClick}
            >
              <Paperclip size={14} />
              Upload File
            </button>
            <input
              ref={fileInputRef}
              type="file"
              hidden
              onChange={handleFileChange}
            />
          </AccordionSection>
        </div>
      </aside>
    </>
  )
}
