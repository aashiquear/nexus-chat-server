import React, { useState, useEffect } from 'react'
import { X, Plus, Trash2, Shield, Users, KeyRound, HardDrive } from 'lucide-react'
import { adminListUsers, adminCreateUser, adminDeleteUser, adminResetPassword } from '../hooks/api'

function fmtBytes(n) {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`
}

export default function AdminPanel({ onClose, currentUser }) {
  const [users, setUsers] = useState([])
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newIsAdmin, setNewIsAdmin] = useState(false)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const loadUsers = async () => {
    try {
      setUsers(await adminListUsers())
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    loadUsers()
  }, [])

  const handleCreate = async (e) => {
    e.preventDefault()
    setError('')
    setIsLoading(true)
    try {
      await adminCreateUser(newUsername.trim(), newPassword, newIsAdmin)
      setNewUsername('')
      setNewPassword('')
      setNewIsAdmin(false)
      await loadUsers()
    } catch (err) {
      setError(err.message)
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (username) => {
    if (!window.confirm(`Delete user "${username}" and their whole workspace (conversations, uploads, generated files)?`)) return
    try {
      await adminDeleteUser(username)
      await loadUsers()
    } catch (err) {
      alert(err.message)
    }
  }

  const handleReset = async (username) => {
    const pw = window.prompt(`New password for "${username}" (min 4 characters):`)
    if (!pw) return
    try {
      await adminResetPassword(username, pw)
      alert(`Password for ${username} updated`)
    } catch (err) {
      alert(err.message)
    }
  }

  const totalUsage = users.reduce((s, u) => s + (u.usage_bytes || 0), 0)

  return (
    <div className="mcp-modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="mcp-modal" style={{ width: 620 }}>
        <div className="mcp-modal-header">
          <h3>
            <Users size={16} /> Admin — users & workspaces
          </h3>
          <button className="gen-icon-btn" onClick={onClose} title="Close">
            <X size={18} />
          </button>
        </div>
        <div className="mcp-modal-body">
          <form onSubmit={handleCreate} className="gen-row" style={{ flexWrap: 'wrap' }}>
            <input
              type="text"
              placeholder="Username (letters, digits, . _ -)"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              className="gen-input"
              style={{ flex: 2, minWidth: 140 }}
            />
            <input
              type="password"
              placeholder="Password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="gen-input"
              style={{ flex: 1, minWidth: 120 }}
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, cursor: 'pointer' }}>
              <input type="checkbox" checked={newIsAdmin} onChange={(e) => setNewIsAdmin(e.target.checked)} />
              Admin
            </label>
            <button type="submit" className="gen-btn primary" disabled={isLoading || !newUsername || !newPassword}>
              <Plus size={13} /> Create user
            </button>
          </form>
          {error && <div className="mcp-error" style={{ padding: 0 }}>{error}</div>}

          <div className="mcp-help">
            Each user gets a private workspace under <code>data/users/&lt;name&gt;/</code> (uploads, generated files, plots,
            conversations, MCP servers, settings). RAG documents are filtered per owner. Total usage: {fmtBytes(totalUsage)}.
          </div>

          <div className="mcp-import-results">
            {users.map((u) => (
              <div key={u.username} className="mcp-import-row" style={{ color: 'inherit' }}>
                {u.is_admin ? <Shield size={13} style={{ color: 'var(--accent)' }} /> : <Users size={13} style={{ opacity: 0.5 }} />}
                <span className="mcp-import-id" style={{ flex: 1 }}>
                  {u.username}
                  {u.is_admin && <span className="mcp-badge" style={{ marginLeft: 6 }}>admin</span>}
                  {u.username === currentUser?.username && <span className="mcp-badge" style={{ marginLeft: 6 }}>you</span>}
                </span>
                <span className="mcp-result-desc" title="Workspace usage">
                  <HardDrive size={11} style={{ verticalAlign: '-1px' }} /> {fmtBytes(u.usage_bytes)} · {u.conversations} chats
                </span>
                <button className="gen-icon-btn" onClick={() => handleReset(u.username)} title="Reset password">
                  <KeyRound size={13} />
                </button>
                {u.username !== currentUser?.username && (
                  <button className="gen-icon-btn" style={{ color: 'var(--error)' }} onClick={() => handleDelete(u.username)} title="Delete user and workspace">
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            ))}
            {users.length === 0 && <div className="mcp-result-desc">No users found</div>}
          </div>
        </div>
      </div>
    </div>
  )
}
