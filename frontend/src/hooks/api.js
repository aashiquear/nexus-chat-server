const BASE = ''

function getToken() {
  return localStorage.getItem('nexus_token')
}

function handleAuth(res) {
  if (res.status === 401) {
    localStorage.removeItem('nexus_token')
    localStorage.removeItem('nexus_user')
    window.location.reload()
  }
}

async function authFetch(url, options = {}) {
  const token = getToken()
  const headers = {
    ...(options.headers || {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  const res = await fetch(`${BASE}${url}`, { ...options, headers })
  handleAuth(res)
  return res
}

export async function fetchModels() {
  const res = await authFetch(`${BASE}/api/models`)
  const data = await res.json()
  return data.models || []
}

export async function fetchTools() {
  const res = await authFetch(`${BASE}/api/tools`)
  const data = await res.json()
  return data.tools || []
}

export async function fetchFiles() {
  const res = await authFetch(`${BASE}/api/files`)
  const data = await res.json()
  return data.files || []
}

export async function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}/api/upload`)
    const token = getToken()
    if (token) {
      xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status === 401) {
        handleAuth({ status: 401 })
        reject(new Error('Unauthorized'))
        return
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText))
      } else {
        try {
          const err = JSON.parse(xhr.responseText)
          reject(new Error(err.detail || 'Upload failed'))
        } catch (_e) {
          reject(new Error('Upload failed'))
        }
      }
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
    xhr.send(form)
  })
}

export async function fetchUploadProgress(filename) {
  const res = await authFetch(
    `${BASE}/api/upload/progress/${encodeURIComponent(filename)}`
  )
  if (!res.ok) return null
  return res.json()
}

export async function deleteFile(filename) {
  const res = await authFetch(`${BASE}/api/files/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  })
  return res.json()
}

export async function fetchMCPServers() {
  const res = await authFetch(`${BASE}/api/mcp/servers`)
  const data = await res.json()
  return data.servers || []
}

export async function reconnectMCPServer(serverId) {
  const res = await authFetch(`${BASE}/api/mcp/servers/${encodeURIComponent(serverId)}/reconnect`, {
    method: 'POST',
  })
  return res.json()
}

// ---- Auth ----

export async function fetchAuthMe() {
  const res = await authFetch(`${BASE}/api/auth/me`)
  if (!res.ok) return null
  return res.json()
}

// ---- Conversations ----

export async function fetchConversations() {
  const res = await authFetch(`${BASE}/api/conversations`)
  const data = await res.json()
  return data.conversations || []
}

export async function fetchConversation(id) {
  const res = await authFetch(`${BASE}/api/conversations/${encodeURIComponent(id)}`)
  if (!res.ok) return null
  return res.json()
}

export async function saveConversation({ id, messages, model, token_usage }) {
  const res = await authFetch(`${BASE}/api/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, messages, model, token_usage }),
  })
  return res.json()
}

export async function deleteConversation(id) {
  const res = await authFetch(`${BASE}/api/conversations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  return res.json()
}
