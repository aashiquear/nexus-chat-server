import React, { useState, useEffect, useRef } from 'react'
import { X, KeyRound, Trash2, AlertTriangle, LogOut } from 'lucide-react'

export default function AccountPopup({ user, onClose, onLogout }) {
  const [tab, setTab] = useState('info')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [deletePassword, setDeletePassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const popupRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (popupRef.current && !popupRef.current.contains(e.target)) {
        onClose()
      }
    }
    const handleEsc = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [onClose])

  const handleChangePassword = async (e) => {
    e.preventDefault()
    setError('')
    setSuccess('')
    if (newPassword !== confirmPassword) {
      setError('New passwords do not match')
      return
    }
    if (newPassword.length < 4) {
      setError('Password must be at least 4 characters')
      return
    }
    setIsLoading(true)
    try {
      const token = localStorage.getItem('nexus_token')
      const res = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      })
      if (!res.ok) {
        const data = await res.json()
        setError(data.detail || 'Failed to change password')
      } else {
        setSuccess('Password changed successfully')
        setCurrentPassword('')
        setNewPassword('')
        setConfirmPassword('')
      }
    } catch {
      setError('Network error')
    } finally {
      setIsLoading(false)
    }
  }

  const handleDeleteAccount = async (e) => {
    e.preventDefault()
    setError('')
    if (!window.confirm('Are you sure you want to permanently delete your account and all conversations?')) {
      return
    }
    setIsLoading(true)
    try {
      const token = localStorage.getItem('nexus_token')
      const res = await fetch('/api/auth/account', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ password: deletePassword }),
      })
      if (!res.ok) {
        const data = await res.json()
        setError(data.detail || 'Failed to delete account')
      } else {
        onLogout()
      }
    } catch {
      setError('Network error')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div
      ref={popupRef}
      style={{
        position: 'absolute',
        top: 52,
        right: 12,
        width: 320,
        background: 'var(--bg-panel)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
        zIndex: 200,
        padding: 16,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Account</h3>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}>
          <X size={16} />
        </button>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button
          className={`thread-item ${tab === 'info' ? 'active' : ''}`}
          onClick={() => { setTab('info'); setError(''); setSuccess('') }}
          style={{ flex: 1, justifyContent: 'center', fontSize: 12 }}
        >
          Info
        </button>
        <button
          className={`thread-item ${tab === 'password' ? 'active' : ''}`}
          onClick={() => { setTab('password'); setError(''); setSuccess('') }}
          style={{ flex: 1, justifyContent: 'center', fontSize: 12 }}
        >
          <KeyRound size={12} style={{ marginRight: 4 }} />
          Password
        </button>
        <button
          className={`thread-item ${tab === 'delete' ? 'active' : ''}`}
          onClick={() => { setTab('delete'); setError(''); setSuccess('') }}
          style={{ flex: 1, justifyContent: 'center', fontSize: 12, color: 'var(--error)' }}
        >
          <Trash2 size={12} style={{ marginRight: 4 }} />
          Delete
        </button>
      </div>

      {tab === 'info' && (
        <div>
          <div style={{ fontSize: 14, marginBottom: 4 }}>
            <strong>Username:</strong> {user.username}
          </div>
          <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginBottom: 12 }}>
            <strong>Role:</strong> {user.is_admin ? 'Administrator' : 'User'}
          </div>
          <button
            className="splash-start-btn"
            style={{ width: '100%', fontSize: 13 }}
            onClick={onLogout}
          >
            <LogOut size={14} />
            Log Out
          </button>
        </div>
      )}

      {tab === 'password' && (
        <form onSubmit={handleChangePassword}>
          <input
            type="password"
            placeholder="Current password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            className="chat-input-field"
            style={{ width: '100%', marginBottom: 8, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          />
          <input
            type="password"
            placeholder="New password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="chat-input-field"
            style={{ width: '100%', marginBottom: 8, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          />
          <input
            type="password"
            placeholder="Confirm new password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="chat-input-field"
            style={{ width: '100%', marginBottom: 8, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          />
          {error && <div style={{ color: 'var(--error)', fontSize: 12, marginBottom: 8 }}>{error}</div>}
          {success && <div style={{ color: 'var(--success)', fontSize: 12, marginBottom: 8 }}>{success}</div>}
          <button type="submit" className="splash-start-btn" disabled={isLoading} style={{ width: '100%', fontSize: 13 }}>
            <KeyRound size={14} />
            {isLoading ? 'Updating…' : 'Change Password'}
          </button>
        </form>
      )}

      {tab === 'delete' && (
        <form onSubmit={handleDeleteAccount}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10, color: 'var(--error)', fontSize: 13 }}>
            <AlertTriangle size={14} />
            This action cannot be undone.
          </div>
          <input
            type="password"
            placeholder="Enter your password to confirm"
            value={deletePassword}
            onChange={(e) => setDeletePassword(e.target.value)}
            className="chat-input-field"
            style={{ width: '100%', marginBottom: 8, padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
          />
          {error && <div style={{ color: 'var(--error)', fontSize: 12, marginBottom: 8 }}>{error}</div>}
          <button type="submit" className="splash-start-btn" disabled={isLoading} style={{ width: '100%', fontSize: 13, background: 'var(--error)', borderColor: 'var(--error)' }}>
            <Trash2 size={14} />
            {isLoading ? 'Deleting…' : 'Delete My Account'}
          </button>
        </form>
      )}
    </div>
  )
}
