const BASE = ''

// ---------------------------------------------------------------------
// Request plumbing. ``authHeaders`` is a hook point: the single-user
// build sends nothing, the multi-user server edition injects a bearer
// token here (and nowhere else), which keeps the two code bases aligned.
// ---------------------------------------------------------------------
export function authHeaders() {
  return {}
}

export function onUnauthorized(_res) {}

export async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}), ...authHeaders() }
  const res = await fetch(`${BASE}${url}`, { ...options, headers })
  if (res.status === 401) onUnauthorized(res)
  return res
}

async function jsonOrThrow(res, fallback) {
  if (!res.ok) {
    let detail = fallback || `Request failed: ${res.status}`
    try {
      const err = await res.json()
      detail = err.detail || detail
    } catch (_e) {}
    throw new Error(detail)
  }
  return res.json()
}

// Append the auth/scope query suffix (server edition) to a file URL so
// <img>/<iframe>/<embed> tags, which cannot send headers, still work.
export function fileUrl(path) {
  return path
}

// ---- Models / tools / settings ----

export async function fetchModels() {
  const res = await apiFetch(`/api/models`)
  const data = await res.json()
  return data.models || []
}

export async function fetchModelInfo(modelId) {
  const res = await apiFetch(`/api/models/${encodeURIComponent(modelId)}/info`)
  if (!res.ok) return null
  return res.json()
}

export async function fetchTools() {
  const res = await apiFetch(`/api/tools`)
  const data = await res.json()
  return data.tools || []
}

// Tools plus the server's tool-selection policy ({mode, max_tools, …}).
export async function fetchToolsWithPolicy() {
  const res = await apiFetch(`/api/tools`)
  const data = await res.json()
  return { tools: data.tools || [], selection: data.selection || { mode: 'auto' } }
}

export async function discoverTools(query, limit = 8) {
  const res = await apiFetch(`/api/tools/discover?q=${encodeURIComponent(query)}&limit=${limit}`)
  if (!res.ok) return []
  return (await res.json()).matches || []
}

export async function fetchGenerationSettings() {
  const res = await apiFetch(`/api/settings/generation`)
  if (!res.ok) return null
  return res.json()
}

// Stream Ollama pull progress. ``onProgress`` receives each JSON event.
export async function pullModel(model, onProgress) {
  const res = await apiFetch(`/api/models/pull`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  if (!res.ok) throw new Error(`Pull failed: ${res.status}`)
  await readSSE(res, onProgress)
}

// ---- Files ----

export async function fetchFiles() {
  const res = await apiFetch(`/api/files`)
  const data = await res.json()
  return data.files || []
}

export async function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}/api/upload`)
    Object.entries(authHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    }
    xhr.onload = () => {
      if (xhr.status === 401) {
        onUnauthorized({ status: 401 })
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
  const res = await apiFetch(`/api/upload/progress/${encodeURIComponent(filename)}`)
  if (!res.ok) return null
  return res.json()
}

export async function deleteFile(filename) {
  const res = await apiFetch(`/api/files/${encodeURIComponent(filename)}`, { method: 'DELETE' })
  return res.json()
}

export async function fetchPreview(filename) {
  const res = await apiFetch(`/api/preview/${encodeURIComponent(filename)}`)
  return jsonOrThrow(res, 'Preview failed')
}

// ---- Sandbox ----

export async function fetchSandboxLanguages() {
  const res = await apiFetch(`/api/sandbox/languages`)
  if (!res.ok) return { languages: [], sandbox: false }
  return res.json()
}

// Run a code block directly. ``onChunk(line)`` streams output; resolves
// with the final result {output, return_code, artifacts, downloadable}.
export async function runCode({ code, language, packages, stdin, filename, input_files }, onChunk) {
  const res = await apiFetch(`/api/sandbox/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, language, packages, stdin, filename, input_files }),
  })
  if (!res.ok) {
    let detail = `Run failed: ${res.status}`
    try { detail = (await res.json()).detail || detail } catch (_e) {}
    throw new Error(detail)
  }
  let result = null
  await readSSE(res, (ev) => {
    if (ev.type === 'chunk') onChunk && onChunk(ev.data)
    else if (ev.type === 'result') result = ev
    else if (ev.type === 'error') throw new Error(ev.error)
  })
  return result
}

// Minimal SSE reader over fetch (works for POST requests, unlike EventSource).
export async function readSSE(res, onEvent) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const dataLines = raw.split('\n').filter((l) => l.startsWith('data:'))
      if (!dataLines.length) continue
      const payload = dataLines.map((l) => l.slice(5).trim()).join('\n')
      try {
        onEvent(JSON.parse(payload))
      } catch (e) {
        if (e instanceof SyntaxError) continue
        throw e
      }
    }
  }
}

// ---- MCP ----

export async function fetchMCPServers() {
  const res = await apiFetch(`/api/mcp/servers`)
  const data = await res.json()
  return data.servers || []
}

export async function reconnectMCPServer(serverId) {
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/reconnect`, { method: 'POST' })
  return jsonOrThrow(res, 'Reconnect failed')
}

export async function discoverMCPServer(cfg) {
  const res = await apiFetch(`/api/mcp/servers/discover`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  return jsonOrThrow(res, 'Discovery failed')
}

export async function addMCPServer(cfg) {
  const res = await apiFetch(`/api/mcp/servers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  })
  return jsonOrThrow(res, 'Failed to add server')
}

export async function removeMCPServer(serverId) {
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(serverId)}`, { method: 'DELETE' })
  return jsonOrThrow(res, 'Failed to remove server')
}

export async function searchMCPRegistry(query, cursor) {
  const params = new URLSearchParams()
  if (query) params.set('q', query)
  if (cursor) params.set('cursor', cursor)
  const res = await apiFetch(`/api/mcp/registry/search?${params.toString()}`)
  return jsonOrThrow(res, 'Registry search failed')
}

export async function importMCPConfig(configText) {
  const res = await apiFetch(`/api/mcp/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ config: configText }),
  })
  return jsonOrThrow(res, 'Import failed')
}

export async function fetchMCPResources(serverId) {
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/resources`)
  if (!res.ok) return []
  return (await res.json()).resources || []
}

export async function ingestMCPResource(serverId, uri) {
  const res = await apiFetch(`/api/mcp/servers/${encodeURIComponent(serverId)}/resources/ingest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ uri }),
  })
  return jsonOrThrow(res, 'Ingest failed')
}

// ---- Conversations ----

export async function fetchConversations() {
  const res = await apiFetch(`/api/conversations`)
  const data = await res.json()
  return data.conversations || []
}

export async function fetchConversation(id) {
  const res = await apiFetch(`/api/conversations/${encodeURIComponent(id)}`)
  if (!res.ok) return null
  return res.json()
}

export async function saveConversation({ id, messages, model, token_usage, compaction_state, generation, selected_tools, selected_files }) {
  const res = await apiFetch(`/api/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, messages, model, token_usage, compaction_state, generation, selected_tools, selected_files }),
  })
  return res.json()
}

export async function deleteConversation(id) {
  const res = await apiFetch(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
  return res.json()
}
