import React, { useState, useEffect, useRef } from 'react'
import { X, Plus, Server, Wrench, Loader2, AlertCircle } from 'lucide-react'
import { discoverMCPServer, addMCPServer } from '../hooks/api'

export default function MCPDiscoveryPanel({ onClose, onAdded }) {
  const [url, setUrl] = useState('')
  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discovered, setDiscovered] = useState(null)
  const [error, setError] = useState('')
  const [isAdding, setIsAdding] = useState(false)
  const panelRef = useRef(null)

  useEffect(() => {
    const handleEsc = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [onClose])

  const handleDiscover = async (e) => {
    e.preventDefault()
    setError('')
    setDiscovered(null)
    if (!url.trim()) {
      setError('Please enter a server URL')
      return
    }
    setIsDiscovering(true)
    try {
      const data = await discoverMCPServer(url.trim())
      setDiscovered(data)
    } catch (err) {
      setError(err.message || 'Discovery failed')
    } finally {
      setIsDiscovering(false)
    }
  }

  const handleAdd = async () => {
    if (!discovered) return
    setIsAdding(true)
    try {
      const cfg = {
        url: discovered.server.url,
        name: discovered.server.name,
        description: discovered.server.description,
        icon: discovered.server.icon,
        timeout: 30,
      }
      await addMCPServer(cfg)
      onAdded()
      onClose()
    } catch (err) {
      setError(err.message || 'Failed to add server')
    } finally {
      setIsAdding(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 300,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={panelRef}
        style={{
          width: 420,
          maxWidth: '90vw',
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
          padding: 20,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Add MCP Server</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}>
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleDiscover}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <input
              type="text"
              placeholder="http://192.168.x.x:port_number"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="chat-input-field"
              style={{ flex: 1, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
            />
            <button
              type="submit"
              className="splash-start-btn"
              disabled={isDiscovering}
              style={{ fontSize: 13, padding: '8px 12px' }}
            >
              {isDiscovering ? <Loader2 size={14} className="spin" /> : <Server size={14} />}
              {isDiscovering ? 'Discovering…' : 'Discover'}
            </button>
          </div>
        </form>

        {error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--error)', fontSize: 13, marginBottom: 12 }}>
            <AlertCircle size={14} />
            {error}
          </div>
        )}

        {discovered && (
          <div style={{ marginTop: 8 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '10px 12px',
                background: 'var(--bg-subtle)',
                borderRadius: 8,
                marginBottom: 12,
              }}
            >
              <Server size={16} style={{ opacity: 0.7 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 500, fontSize: 13 }}>{discovered.server.name}</div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{discovered.server.description || discovered.server.url}</div>
              </div>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: 'var(--success)',
                  display: 'inline-block',
                }}
                title="Connected"
              />
            </div>

            {discovered.tools.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  Discovered Tools
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {discovered.tools.map((tool) => (
                    <div
                      key={tool.name}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '4px 8px',
                        fontSize: 12,
                        background: 'var(--bg-subtle)',
                        borderRadius: 4,
                      }}
                    >
                      <Wrench size={12} style={{ opacity: 0.5 }} />
                      <span style={{ fontWeight: 500 }}>{tool.name}</span>
                      {tool.description && (
                        <span style={{ color: 'var(--text-secondary)', marginLeft: 4 }}>{tool.description}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <button
              className="splash-start-btn"
              onClick={handleAdd}
              disabled={isAdding}
              style={{ width: '100%', fontSize: 13 }}
            >
              <Plus size={14} />
              {isAdding ? 'Adding…' : 'Add Server'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
