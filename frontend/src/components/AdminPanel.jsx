import React, { useState, useEffect } from 'react'
import { X, Plus, Trash2, Shield, Users, LogOut } from 'lucide-react'

export default function AdminPanel({ onClose, onLogout }) {
  const [users, setUsers] = useState([])
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newIsAdmin, setNewIsAdmin] = useState(false)
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const token = localStorage.getItem('nexus_token')

  const loadUsers = async () => {
    try {
      const res = await fetch('/api/admin/users', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Failed to load users')
      const data = await res.json()
      setUsers(data.users || [])
    } catch (err) {
      console.error(err)
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
      const res = await fetch('/api/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ username: newUsername, password: newPassword, is_admin: newIsAdmin }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Failed to create user')
      } else {
        setNewUsername('')
        setNewPassword('')
        setNewIsAdmin(false)
        await loadUsers()
      }
    } catch {
      setError('Network error')
    } finally {
      setIsLoading(false)
    }
  }

  const handleDelete = async (username) => {
    if (!window.confirm(`Delete user "${username}" and all their conversations?`)) return
    try {
      const res = await fetch(`/api/admin/users/${encodeURIComponent(username)}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (!res.ok) {
        const data = await res.json()
        alert(data.detail || 'Failed to delete user')
      } else {
        await loadUsers()
      }
    } catch {
      alert('Network error')
    }
  }

  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Users size={16} />
          Admin Panel
        </h3>
        <div style={{ display: 'flex', gap: 6 }}>
          <button
            className="splash-start-btn"
            style={{ fontSize: 12, padding: '4px 8px' }}
            onClick={onLogout}
          >
            <LogOut size={12} />
            Log Out
          </button>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}>
            <X size={16} />
          </button>
        </div>
      </div>

      <form onSubmit={handleCreate} style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="Username"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            className="chat-input-field"
            style={{ flex: 1, minWidth: 100, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          />
          <input
            type="password"
            placeholder="Password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="chat-input-field"
            style={{ flex: 1, minWidth: 100, padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          />
          <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, cursor: 'pointer' }}>
            <input type="checkbox" checked={newIsAdmin} onChange={(e) => setNewIsAdmin(e.target.checked)} />
            Admin
          </label>
          <button type="submit" className="splash-start-btn" disabled={isLoading || !newUsername || !newPassword} style={{ fontSize: 12, padding: '6px 10px' }}>
            <Plus size={13} />
            Create
          </button>
        </div>
        {error && <div style={{ color: 'var(--error)', fontSize: 12 }}>{error}</div>}
      </form>

      <div style={{ maxHeight: 240, overflowY: 'auto' }}>
        {users.map((u) => (
          <div
            key={u.username}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '6px 8px',
              borderRadius: 6,
              fontSize: 13,
              borderBottom: '1px solid var(--border-subtle)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {u.is_admin && <Shield size={12} style={{ color: 'var(--accent)' }} />}
              <span style={{ fontWeight: 500 }}>{u.username}</span>
              {u.is_admin && <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>(admin)</span>}
            </div>
            <button
              onClick={() => handleDelete(u.username)}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--error)', padding: 2 }}
              title="Delete user"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        {users.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '8px 0' }}>No users found</div>
        )}
      </div>
    </div>
  )
}
