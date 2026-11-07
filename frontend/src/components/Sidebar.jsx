import React, { useState } from 'react'
import {
  Check, Calculator, Search, Code2, Clock, FileText,
  Wrench, Trash2, Paperclip, MessageSquarePlus,
  Database, Server, RefreshCw, ChevronDown, ChevronRight, Image,
  MessageCircle, BarChart3, Eye, Globe, Table, Plus, Store, BookOpen,
  Brain, Cpu, Sparkles, Hand, Pin, Sigma, Ruler, Calendar, Coins, Type, Braces, FileSearch,
  GraduationCap, GitCompare, ListChecks, Scale,
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
  globe: Globe,
  table: Table,
  sigma: Sigma,
  ruler: Ruler,
  calendar: Calendar,
  coins: Coins,
  type: Type,
  braces: Braces,
  'file-search': FileSearch,
  'graduation-cap': GraduationCap,
  'git-compare': GitCompare,
  brain: Brain,
  'list-checks': ListChecks,
  scale: Scale,
}

const CATEGORY_ORDER = ['Utilities', 'Research', 'Files', 'Data', 'Create', 'Personal', 'Other']

const PREVIEWABLE_EXT = new Set([
  '.csv', '.tsv', '.json', '.md', '.txt', '.html', '.htm', '.pdf', '.png', '.jpg', '.jpeg', '.gif',
  '.webp', '.svg', '.py', '.js', '.ts', '.java', '.c', '.cpp', '.go', '.rs', '.sh', '.yaml', '.yml',
  '.xml', '.css', '.sql', '.log', '.mp4', '.webm', '.mp3', '.wav',
])

// Compact capability badges for the model selector / header.
export function ModelBadges({ model }) {
  if (!model) return null
  const caps = model.capabilities || {}
  return (
    <span className="model-badges">
      {caps.vision && <span className="model-badge vision" title="Accepts images">vision</span>}
      {(caps.thinking || model.thinking) && <span className="model-badge thinking" title="Reasoning / thinking model">thinking</span>}
      {caps.tools === false && <span className="model-badge warn" title="Does not support tool calling">no tools</span>}
      {model.quantization && <span className="model-badge" title="Quantization">{model.quantization}</span>}
      {model.parameter_size && <span className="model-badge" title="Parameters">{model.parameter_size}</span>}
      {model.context_length && <span className="model-badge" title="Max context">{model.context_length >= 1000 ? `${Math.round(model.context_length / 1024)}k ctx` : `${model.context_length} ctx`}</span>}
    </span>
  )
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
  onOpenMarketplace,
  onRemoveMCP,
  onOpenMCPResources,
  files,
  selectedFiles,
  onToggleFile,
  onUpload,
  onDeleteFile,
  onPreviewFile,
  uploadProgress,
  conversations,
  activeConversationId,
  onSelectConversation,
  onDeleteConversation,
  onNewChat,
  isOpen,
  onClose,
  toolMode,
  onToolModeChange,
  lastToolSelection,
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
  const isAuto = toolMode !== 'manual'
  const autoAttached = new Set([...(lastToolSelection?.core || []), ...((lastToolSelection?.matched || []).map((m) => m.id))])
  const toolGroups = {}
  builtinTools.forEach((t) => {
    const cat = t.category || 'Other'
    if (!toolGroups[cat]) toolGroups[cat] = []
    toolGroups[cat].push(t)
  })
  const categories = Object.keys(toolGroups).sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a)
    const ib = CATEGORY_ORDER.indexOf(b)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
  const [openCats, setOpenCats] = useState({})
  const catOpen = (c) => openCats[c] ?? (isAuto ? false : true)
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
                  const caps = m.capabilities || {}
                  const tags = []
                  if (caps.vision) tags.push('vision')
                  if (caps.thinking || m.thinking) tags.push('thinking')
                  if (m.quantization) tags.push(m.quantization)
                  const suffix = !m.available
                    ? ' (not configured)'
                    : !onRemote
                    ? ' • not pulled'
                    : tags.length
                    ? ' • ' + tags.join(' · ')
                    : ''
                  return (
                    <option key={m.id} value={m.id} disabled={!usable}>
                      {usable ? '● ' : '○ '}{m.name}{m.discovered ? ' (on server)' : ''}{suffix}
                    </option>
                  )
                })}
              </optgroup>
            ))}
          </select>
          {(() => {
            const cur = models.find((m) => m.id === selectedModel)
            return cur ? (
              <div className="model-meta">
                {cur.kind === 'local' ? <Cpu size={11} /> : <Globe size={11} />}
                <span>{cur.provider}{cur.family ? ` · ${cur.family}` : ''}</span>
                <ModelBadges model={cur} />
              </div>
            ) : null
          })()}
        </div>

        <div className="sidebar-body">
          {/* Built-in Tools */}
          <AccordionSection
            label="Tools"
            count={isAuto ? autoAttached.size + activeBuiltinCount : activeBuiltinCount}
            isOpen={openSection === 'tools'}
            onToggle={() => toggleSection('tools')}
          >
            <div className="tool-mode-switch" role="radiogroup" aria-label="Tool selection mode">
              <button className={isAuto ? 'active' : ''} onClick={() => onToolModeChange && onToolModeChange('auto')} title="The router attaches the tools relevant to each message; tick a tool to pin it">
                <Sparkles size={12} /> Auto
              </button>
              <button className={!isAuto ? 'active' : ''} onClick={() => onToolModeChange && onToolModeChange('manual')} title="Only ticked tools are sent to the model">
                <Hand size={12} /> Manual
              </button>
            </div>
            <div className="tool-mode-hint">
              {isAuto
                ? `${builtinTools.length} tools available · relevant ones are attached per message${lastToolSelection?.matched?.length ? ` (last: ${lastToolSelection.matched.map((m) => m.name || m.id).slice(0, 4).join(', ')})` : ''}. Tick to pin.`
                : 'Only ticked tools are sent to the model.'}
            </div>
            {categories.map((cat) => {
              const list = toolGroups[cat]
              const open = catOpen(cat)
              const pinned = list.filter((t) => selectedTools.includes(t.id)).length
              const attached = list.filter((t) => autoAttached.has(t.id)).length
              return (
                <div key={cat} className="tool-category">
                  <button className="tool-category-header" onClick={() => setOpenCats((p) => ({ ...p, [cat]: !open }))}>
                    {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    <span>{cat}</span>
                    <span className="tool-category-count">
                      {pinned > 0 && <Pin size={10} />} {pinned > 0 ? pinned : ''}{isAuto && attached > 0 ? ` · ${attached} active` : ''} {!isAuto && pinned === 0 ? '' : ''}
                      <span className="tool-category-total">{list.length}</span>
                    </span>
                  </button>
                  {open && list.map((tool) => {
                    const active = selectedTools.includes(tool.id)
                    const auto = isAuto && autoAttached.has(tool.id)
                    const Icon = ICON_MAP[tool.icon] || Wrench
                    return (
                      <div
                        key={tool.id}
                        className={`toggle-item ${auto && !active ? 'auto-attached' : ''}`}
                        onClick={() => onToggleTool(tool.id)}
                        title={isAuto ? (active ? 'Pinned — always attached' : auto ? 'Auto-attached for the last message' : 'Click to pin') : ''}
                      >
                        <div className={`toggle-check ${active ? 'active' : ''} ${auto && !active ? 'auto' : ''}`}>
                          {active && (isAuto ? <Pin size={10} color="#fff" strokeWidth={3} /> : <Check size={11} color="#fff" strokeWidth={3} />)}
                          {!active && auto && <Sparkles size={9} />}
                        </div>
                        <Icon size={15} className="toggle-icon" />
                        <div className="toggle-text">
                          <div className="toggle-label">
                            {tool.name}
                            {tool.core && <span className="tool-badge core" title="Always attached in auto mode">core</span>}
                            {tool.risk === 'high' && <span className="tool-badge warn" title="Modifies state or spends money">caution</span>}
                            {tool.offline === false && <span className="tool-badge" title="Needs internet">online</span>}
                          </div>
                          <ToolDescription text={tool.description} />
                        </div>
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </AccordionSection>

          {/* MCP Servers */}
          <AccordionSection
              label="MCP Servers"
              count={mcpToolCount}
              isOpen={openSection === 'mcp'}
              onToggle={() => toggleSection('mcp')}
            >
              <button
                className="new-chat-btn mcp-market-btn"
                onClick={onOpenMarketplace}
                title="Install MCP servers from the registry, import a Claude/ChatGPT config, or add by URL"
              >
                <Store size={14} />
                MCP Marketplace
              </button>
              {(!mcpServers || mcpServers.length === 0) && (
                <div style={{ padding: '4px 16px', fontSize: 12.5, color: 'var(--text-tertiary)' }}>
                  No MCP servers connected yet
                </div>
              )}
              {(mcpServers || []).map((srv) => {
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
                      <span style={{ fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={`${srv.url || ''} (${srv.transport || ''})${srv.error ? `\n${srv.error}` : ''}`}>{srv.name}</span>
                      {srv.resources > 0 && (
                        <button
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 2 }}
                          onClick={() => onOpenMCPResources && onOpenMCPResources(srv)}
                          title={`${srv.resources} resource(s) — add to RAG`}
                        >
                          <BookOpen size={12} />
                        </button>
                      )}
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
                          title={srv.error ? `Reconnect — ${srv.error}` : 'Reconnect'}
                        >
                          <RefreshCw size={12} />
                        </button>
                      )}
                      {srv.source !== 'config' && onRemoveMCP && (
                        <button
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 2 }}
                          onClick={(e) => { e.stopPropagation(); onRemoveMCP(srv.id) }}
                          title="Remove server"
                        >
                          <Trash2 size={12} />
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
                  {onPreviewFile && PREVIEWABLE_EXT.has((file.extension || '').toLowerCase()) && (
                    <button
                      style={{
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: 'var(--text-tertiary)', padding: 2,
                      }}
                      onClick={(e) => {
                        e.stopPropagation()
                        onPreviewFile(file)
                      }}
                      title="Preview in canvas"
                    >
                      <Eye size={13} />
                    </button>
                  )}
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
