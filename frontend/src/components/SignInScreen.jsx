import React, { useState } from 'react'
import { ArrowRight, LogIn } from 'lucide-react'
import NexusLogoIcon from './NexusLogoIcon'

export default function SignInScreen({ onSignIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setIsLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Login failed')
        return
      }
      localStorage.setItem('nexus_token', data.token)
      localStorage.setItem('nexus_user', JSON.stringify(data.user))
      onSignIn(data.user)
    } catch (err) {
      setError('Network error')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="splash-screen">
      <div className="splash-content">
        <div className="sidebar-logo" style={{ marginBottom: 24 }}>
          <NexusLogoIcon size={48} />
        </div>
        <div className="splash-logo-shine" style={{ marginBottom: 16 }}>
          <h1 style={{ fontSize: 32, fontWeight: 600, color: '#3a675a', letterSpacing: 4 }}>
            NEXUS CHAT
          </h1>
          <p style={{ fontSize: 14, color: '#6b9d8c', letterSpacing: 2, marginTop: 4 }}>
            SERVER
          </p>
        </div>

        <form onSubmit={handleSubmit} style={{ width: '100%', maxWidth: 320 }}>
          <div style={{ marginBottom: 12 }}>
            <input
              type="text"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="chat-input-field"
              style={{ width: '100%', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)' }}
              autoFocus
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="chat-input-field"
              style={{ width: '100%', padding: '10px 14px', borderRadius: 8, border: '1px solid var(--border)' }}
            />
          </div>
          {error && (
            <div style={{ color: 'var(--error)', fontSize: 13, marginBottom: 12, textAlign: 'center' }}>
              {error}
            </div>
          )}
          <button
            type="submit"
            className="splash-start-btn"
            disabled={isLoading || !username || !password}
            style={{ width: '100%' }}
          >
            <LogIn size={16} />
            <span>{isLoading ? 'Signing in…' : 'Sign In'}</span>
            <ArrowRight size={16} />
          </button>
        </form>
      </div>
    </div>
  )
}
