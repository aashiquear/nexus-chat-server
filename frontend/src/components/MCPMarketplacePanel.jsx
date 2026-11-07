import React, { useEffect, useState } from 'react'
import { X, Search, Plus, Server, Wrench, Loader2, AlertCircle, Terminal, Globe, FileJson, ExternalLink, CheckCircle2 } from 'lucide-react'
import { searchMCPRegistry, discoverMCPServer, addMCPServer, importMCPConfig } from '../hooks/api'

const EXAMPLE_IMPORT = `{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/app/data/uploads"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_..." }
    },
    "remote-example": { "url": "https://mcp.example.com/mcp" }
  }
}`

// Marketplace / installer for MCP servers:
//   • Registry  — search the official MCP Registry (same catalog the
//                 GitHub, Claude and ChatGPT marketplaces use) and install
//                 npm / PyPI / Docker / remote servers in one click.
//   • Import    — paste a Claude Desktop / ChatGPT / Cursor "mcpServers"
//                 JSON block (the de-facto plugin format).
//   • Manual    — connect by URL (streamable-http / sse / legacy) or a
//                 stdio command.
export default function MCPMarketplacePanel({ onClose, onAdded }) {
  const [tab, setTab] = useState('registry')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // registry
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [nextCursor, setNextCursor] = useState(null)
  const [selected, setSelected] = useState(null) // {candidate, option}
  const [envValues, setEnvValues] = useState({})

  // manual
  const [manualUrl, setManualUrl] = useState('')
  const [manualCommand, setManualCommand] = useState('')
  const [manualName, setManualName] = useState('')
  const [discovered, setDiscovered] = useState(null)

  // import
  const [importText, setImportText] = useState('')
  const [importResult, setImportResult] = useState(null)

  useEffect(() => {
    const handleEsc = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [onClose])

  useEffect(() => {
    if (tab === 'registry' && results.length === 0) doSearch('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  const doSearch = async (q, cursor) => {
    setBusy(true)
    setError('')
    try {
      const data = await searchMCPRegistry(q, cursor)
      setResults((prev) => (cursor ? [...prev, ...data.results] : data.results))
      setNextCursor(data.next_cursor || null)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const installSelected = async () => {
    if (!selected) return
    setBusy(true)
    setError('')
    try {
      const { candidate, option } = selected
      const cfg = { ...option.config }
      if (cfg.env) cfg.env = { ...cfg.env, ...envValues }
      await addMCPServer({
        ...cfg,
        id: candidate.id.split('/').pop(),
        name: candidate.name,
        description: candidate.description,
        source: 'registry',
        registry_id: candidate.id,
        homepage: candidate.homepage,
      })
      onAdded && onAdded()
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const manualPayload = () => {
    const payload = { name: manualName.trim() || undefined }
    if (manualCommand.trim()) {
      const parts = manualCommand.trim().match(/(?:[^\s"]+|"[^"]*")+/g) || []
      payload.command = parts[0].replace(/"/g, '')
      payload.args = parts.slice(1).map((p) => p.replace(/"/g, ''))
    } else {
      payload.url = manualUrl.trim()
    }
    return payload
  }

  const doDiscover = async (e) => {
    e.preventDefault()
    setError('')
    setDiscovered(null)
    if (!manualUrl.trim() && !manualCommand.trim()) {
      setError('Enter a server URL or a stdio command')
      return
    }
    setBusy(true)
    try {
      const data = await discoverMCPServer(manualPayload())
      setDiscovered(data)
      if (!manualName) setManualName(data.server.name || '')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const addManual = async () => {
    setBusy(true)
    setError('')
    try {
      await addMCPServer({ ...manualPayload(), name: manualName.trim() || discovered?.server?.name, source: 'user' })
      onAdded && onAdded()
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const doImport = async () => {
    setBusy(true)
    setError('')
    setImportResult(null)
    try {
      const data = await importMCPConfig(importText)
      setImportResult(data.imported)
      onAdded && onAdded()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mcp-modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mcp-modal">
        <div className="mcp-modal-header">
          <h3>
            <Server size={16} /> MCP Marketplace
          </h3>
          <button className="gen-icon-btn" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>
        <div className="mcp-tabs">
          <button className={tab === 'registry' ? 'active' : ''} onClick={() => setTab('registry')}>
            <Search size={13} /> Registry
          </button>
          <button className={tab === 'import' ? 'active' : ''} onClick={() => setTab('import')}>
            <FileJson size={13} /> Import config
          </button>
          <button className={tab === 'manual' ? 'active' : ''} onClick={() => setTab('manual')}>
            <Globe size={13} /> URL / command
          </button>
        </div>

        {error && (
          <div className="mcp-error">
            <AlertCircle size={14} /> {error}
          </div>
        )}

        <div className="mcp-modal-body">
          {tab === 'registry' && (
            <>
              <form
                className="mcp-search-row"
                onSubmit={(e) => {
                  e.preventDefault()
                  setSelected(null)
                  doSearch(query)
                }}
              >
                <input type="text" placeholder="Search the official MCP registry (github, postgres, slack, browser…)" value={query} onChange={(e) => setQuery(e.target.value)} />
                <button type="submit" className="gen-btn primary" disabled={busy}>
                  {busy ? <Loader2 size={13} className="spin" /> : <Search size={13} />} Search
                </button>
              </form>
              <div className="mcp-results">
                {results.map((c) => (
                  <div key={c.id} className={`mcp-result ${selected?.candidate.id === c.id ? 'selected' : ''}`}>
                    <div className="mcp-result-head">
                      <div className="mcp-result-title">
                        <Server size={14} />
                        <span>{c.name}</span>
                        {c.version && <span className="mcp-badge">{c.version}</span>}
                      </div>
                      {c.homepage && (
                        <a href={c.homepage} target="_blank" rel="noreferrer" title="Open homepage" className="gen-icon-btn">
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>
                    <div className="mcp-result-id">{c.id}</div>
                    {c.description && <div className="mcp-result-desc">{c.description}</div>}
                    <div className="mcp-result-options">
                      {c.options.map((o, i) => (
                        <button
                          key={i}
                          className={`mcp-option ${selected?.candidate.id === c.id && selected?.option === o ? 'active' : ''}`}
                          onClick={() => {
                            setSelected({ candidate: c, option: o })
                            setEnvValues({})
                          }}
                          title={o.kind === 'remote' ? o.config.url : `${o.config.command} ${o.config.args.join(' ')}`}
                        >
                          {o.kind === 'remote' ? <Globe size={12} /> : <Terminal size={12} />}
                          {o.label}
                        </button>
                      ))}
                      {!c.installable && <span className="mcp-result-desc">No installable package or remote endpoint published.</span>}
                    </div>
                    {selected?.candidate.id === c.id && selected.option.env_vars?.length > 0 && (
                      <div className="mcp-env">
                        <div className="gen-section-label">Environment variables</div>
                        {selected.option.env_vars.map((ev) => (
                          <label key={ev.name} className="gen-field">
                            <span className="gen-field-label">
                              {ev.name}
                              {ev.required && <span className="gen-field-hint">required</span>}
                              {ev.description && <span className="gen-field-hint">{ev.description}</span>}
                            </span>
                            <input
                              type={ev.secret ? 'password' : 'text'}
                              className="gen-input"
                              value={envValues[ev.name] ?? ''}
                              onChange={(e) => setEnvValues((p) => ({ ...p, [ev.name]: e.target.value }))}
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {results.length === 0 && !busy && <div className="mcp-result-desc" style={{ padding: 12 }}>No servers found.</div>}
                {nextCursor && (
                  <button className="gen-btn" style={{ alignSelf: 'center' }} onClick={() => doSearch(query, nextCursor)} disabled={busy}>
                    Load more
                  </button>
                )}
              </div>
            </>
          )}

          {tab === 'import' && (
            <>
              <p className="mcp-help">
                Paste the <code>mcpServers</code> block from Claude Desktop, ChatGPT, Cursor or VS Code. Stdio servers
                (<code>command</code> + <code>args</code>) run inside the Nexus container via <code>npx</code> / <code>uvx</code> /{' '}
                <code>docker</code>; remote servers connect by URL.
              </p>
              <textarea className="mcp-textarea" rows={12} value={importText} placeholder={EXAMPLE_IMPORT} onChange={(e) => setImportText(e.target.value)} spellCheck={false} />
              <div className="gen-row" style={{ justifyContent: 'flex-end' }}>
                <button className="gen-btn" onClick={() => setImportText(EXAMPLE_IMPORT)}>
                  Use example
                </button>
                <button className="gen-btn primary" onClick={doImport} disabled={busy || !importText.trim()}>
                  {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Import & connect
                </button>
              </div>
              {importResult && (
                <div className="mcp-import-results">
                  {importResult.map((r) => (
                    <div key={r.id} className={`mcp-import-row ${r.connected ? 'ok' : 'fail'}`}>
                      {r.connected ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                      <span className="mcp-import-id">{r.id}</span>
                      <span className="mcp-result-desc">{r.connected ? `${r.tools.length} tool(s)` : r.error}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {tab === 'manual' && (
            <form onSubmit={doDiscover} className="mcp-manual">
              <label className="gen-field">
                <span className="gen-field-label">
                  Server URL <span className="gen-field-hint">streamable-http, /sse or legacy /rpc — auto-detected</span>
                </span>
                <input type="text" className="gen-input" placeholder="http://host:port/mcp" value={manualUrl} onChange={(e) => setManualUrl(e.target.value)} />
              </label>
              <label className="gen-field">
                <span className="gen-field-label">
                  …or stdio command <span className="gen-field-hint">runs inside the Nexus container</span>
                </span>
                <input
                  type="text"
                  className="gen-input"
                  placeholder="npx -y @modelcontextprotocol/server-memory"
                  value={manualCommand}
                  onChange={(e) => setManualCommand(e.target.value)}
                />
              </label>
              <label className="gen-field">
                <span className="gen-field-label">Display name</span>
                <input type="text" className="gen-input" value={manualName} onChange={(e) => setManualName(e.target.value)} placeholder="My server" />
              </label>
              <div className="gen-row" style={{ justifyContent: 'flex-end' }}>
                <button type="submit" className="gen-btn" disabled={busy}>
                  {busy ? <Loader2 size={13} className="spin" /> : <Search size={13} />} Test connection
                </button>
                <button type="button" className="gen-btn primary" onClick={addManual} disabled={busy || (!manualUrl.trim() && !manualCommand.trim())}>
                  <Plus size={13} /> Add server
                </button>
              </div>
              {discovered && (
                <div className="mcp-discovered">
                  <div className="mcp-result-title">
                    <Server size={14} /> {discovered.server.name}
                    <span className="mcp-badge">{discovered.server.transport}</span>
                  </div>
                  <div className="mcp-tool-list">
                    {discovered.tools.map((t) => (
                      <div key={t.name} className="mcp-tool-row">
                        <Wrench size={12} />
                        <span>
                          <strong>{t.name}</strong>
                          {t.description ? ` — ${t.description}` : ''}
                        </span>
                      </div>
                    ))}
                    {discovered.resources?.length > 0 && (
                      <div className="mcp-result-desc">{discovered.resources.length} resource(s) available for RAG ingestion.</div>
                    )}
                  </div>
                </div>
              )}
            </form>
          )}
        </div>

        {tab === 'registry' && selected && (
          <div className="mcp-modal-footer">
            <span className="mcp-result-desc">
              {selected.option.kind === 'remote'
                ? selected.option.config.url
                : `${selected.option.config.command} ${selected.option.config.args.join(' ')}`}
            </span>
            <button className="gen-btn primary" onClick={installSelected} disabled={busy}>
              {busy ? <Loader2 size={13} className="spin" /> : <Plus size={13} />} Install {selected.candidate.name}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
