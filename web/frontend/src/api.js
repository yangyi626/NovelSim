// 后端 API 封装。所有 fetch 走相对路径 /api，dev 下由 Vite 代理到 FastAPI。

const AUTH_TOKEN_KEY = 'novelsim_creator_token'

export function getAuthToken() {
  return window.localStorage.getItem(AUTH_TOKEN_KEY) || ''
}

export function clearAuthToken() {
  window.localStorage.removeItem(AUTH_TOKEN_KEY)
}

function authHeaders({ json = true } = {}) {
  const headers = {}
  if (json) headers['Content-Type'] = 'application/json'
  const token = getAuthToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

async function parseResponse(resp) {
  // 统一处理：HTTP 层错误也包装成 { status: 'error', error }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    let body = {}
    try {
      body = await resp.json()
      detail = body.detail || body.error || detail
    } catch (_) {
      /* 非 JSON 错误体，保留默认 */
    }
    return { ...body, status: body.status || 'error', error: detail }
  }
  return resp.json()
}

export async function startSession(packageId = 'huarong_lane') {
  const resp = await fetch('/api/start', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ package_id: packageId }),
  })
  return parseResponse(resp)
}

export async function resumeSession(sessionId) {
  const resp = await fetch(`/api/session?session=${encodeURIComponent(sessionId)}`, {
    headers: authHeaders({ json: false }),
  })
  return parseResponse(resp)
}

export async function listSaves() {
  const resp = await fetch('/api/saves', { headers: authHeaders({ json: false }) })
  return parseResponse(resp)
}

export async function renameSave(sessionId, name) {
  const resp = await fetch(`/api/saves/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: authHeaders(),
    body: JSON.stringify({ name }),
  })
  return parseResponse(resp)
}

export async function deleteSave(sessionId) {
  const resp = await fetch(`/api/saves/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
    headers: authHeaders({ json: false }),
  })
  return parseResponse(resp)
}

export async function exportSave(sessionId) {
  const resp = await fetch(`/api/saves/${encodeURIComponent(sessionId)}/export`, {
    headers: authHeaders({ json: false }),
  })
  if (!resp.ok) return parseResponse(resp)
  const blob = await resp.blob()
  const disposition = resp.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  return {
    status: 'ok',
    blob,
    filename: match?.[1] || `world-save-${sessionId}.json`,
  }
}

export async function importSave(backup) {
  const resp = await fetch('/api/saves/import', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ backup }),
  })
  return parseResponse(resp)
}

export async function listWorldPackages() {
  const resp = await fetch('/api/creator/packages', {
    headers: authHeaders({ json: false }),
  })
  return parseResponse(resp)
}

export async function getWorldPackage(packageId) {
  const resp = await fetch(`/api/creator/packages/${encodeURIComponent(packageId)}`, {
    headers: authHeaders({ json: false }),
  })
  return parseResponse(resp)
}

export async function cloneWorldPackage(packageId) {
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}/clone`,
    { method: 'POST', headers: authHeaders({ json: false }) },
  )
  return parseResponse(resp)
}

export async function validateWorldPackage(pkg) {
  const resp = await fetch('/api/creator/packages/validate', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ package: pkg }),
  })
  return parseResponse(resp)
}

export async function saveWorldPackage(packageId, pkg, expectedRevision) {
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}`,
    {
      method: 'PUT',
      headers: authHeaders(),
      body: JSON.stringify({
        package: pkg,
        expected_revision: expectedRevision,
      }),
    },
  )
  return parseResponse(resp)
}

export async function listWorldPackageRevisions(packageId) {
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}/revisions`,
    { headers: authHeaders({ json: false }) },
  )
  return parseResponse(resp)
}

export async function diffWorldPackageRevisions(
  packageId,
  fromRevision,
  toRevision = null,
) {
  const params = new URLSearchParams({
    from_revision: String(fromRevision),
  })
  if (toRevision != null) params.set('to_revision', String(toRevision))
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}/diff?${params}`,
    { headers: authHeaders({ json: false }) },
  )
  return parseResponse(resp)
}

export async function transitionWorldPackageReview(
  packageId,
  targetStatus,
  expectedRevision,
  note = '',
) {
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}/review`,
    {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        target_status: targetStatus,
        expected_revision: expectedRevision,
        note,
      }),
    },
  )
  return parseResponse(resp)
}

export async function listCompilationJobs(limit = 100) {
  const resp = await fetch(
    `/api/creator/compiler/jobs?limit=${encodeURIComponent(limit)}`,
    { headers: authHeaders({ json: false }) },
  )
  return parseResponse(resp)
}

export async function getCompilationJob(jobId) {
  const resp = await fetch(
    `/api/creator/compiler/jobs/${encodeURIComponent(jobId)}`,
    { headers: authHeaders({ json: false }) },
  )
  return parseResponse(resp)
}

export async function createCompilationJob(payload) {
  const resp = await fetch('/api/creator/compiler/jobs', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  })
  return parseResponse(resp)
}

export async function controlCompilationJob(jobId, action) {
  const resp = await fetch(
    `/api/creator/compiler/jobs/${encodeURIComponent(jobId)}/actions`,
    {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ action }),
    },
  )
  return parseResponse(resp)
}

export async function submitTurn(sessionId, text, useNpcAgents) {
  const resp = await fetch('/api/turn', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      session_id: sessionId,
      text,
      use_npc_agents: useNpcAgents,
    }),
  })
  return parseResponse(resp)
}

export async function bootstrapAdmin(username, password) {
  const resp = await fetch('/api/auth/bootstrap', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ username, password }),
  })
  const data = await parseResponse(resp)
  if (data.status === 'ok' && data.token) {
    window.localStorage.setItem(AUTH_TOKEN_KEY, data.token)
  }
  return data
}

export async function loginCreator(username, password) {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ username, password }),
  })
  const data = await parseResponse(resp)
  if (data.status === 'ok' && data.token) {
    window.localStorage.setItem(AUTH_TOKEN_KEY, data.token)
  }
  return data
}

export async function getCurrentUser() {
  const resp = await fetch('/api/auth/me', {
    headers: authHeaders({ json: false }),
  })
  return parseResponse(resp)
}

export async function listAuditEvents(limit = 100) {
  const resp = await fetch(
    `/api/creator/audit?limit=${encodeURIComponent(limit)}`,
    { headers: authHeaders({ json: false }) },
  )
  return parseResponse(resp)
}
