// 后端 API 封装。所有 fetch 走相对路径 /api，dev 下由 Vite 代理到 FastAPI。

const JSON_HEADERS = { 'Content-Type': 'application/json' }

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
    headers: JSON_HEADERS,
    body: JSON.stringify({ package_id: packageId }),
  })
  return parseResponse(resp)
}

export async function resumeSession(sessionId) {
  const resp = await fetch(`/api/session?session=${encodeURIComponent(sessionId)}`)
  return parseResponse(resp)
}

export async function listSaves() {
  const resp = await fetch('/api/saves')
  return parseResponse(resp)
}

export async function renameSave(sessionId, name) {
  const resp = await fetch(`/api/saves/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ name }),
  })
  return parseResponse(resp)
}

export async function deleteSave(sessionId) {
  const resp = await fetch(`/api/saves/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  return parseResponse(resp)
}

export async function exportSave(sessionId) {
  const resp = await fetch(`/api/saves/${encodeURIComponent(sessionId)}/export`)
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
    headers: JSON_HEADERS,
    body: JSON.stringify({ backup }),
  })
  return parseResponse(resp)
}

export async function listWorldPackages() {
  const resp = await fetch('/api/creator/packages')
  return parseResponse(resp)
}

export async function getWorldPackage(packageId) {
  const resp = await fetch(`/api/creator/packages/${encodeURIComponent(packageId)}`)
  return parseResponse(resp)
}

export async function cloneWorldPackage(packageId) {
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}/clone`,
    { method: 'POST' },
  )
  return parseResponse(resp)
}

export async function validateWorldPackage(pkg) {
  const resp = await fetch('/api/creator/packages/validate', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ package: pkg }),
  })
  return parseResponse(resp)
}

export async function saveWorldPackage(packageId, pkg, expectedRevision) {
  const resp = await fetch(
    `/api/creator/packages/${encodeURIComponent(packageId)}`,
    {
      method: 'PUT',
      headers: JSON_HEADERS,
      body: JSON.stringify({
        package: pkg,
        expected_revision: expectedRevision,
      }),
    },
  )
  return parseResponse(resp)
}

export async function submitTurn(sessionId, text, useNpcAgents) {
  const resp = await fetch('/api/turn', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      session_id: sessionId,
      text,
      use_npc_agents: useNpcAgents,
    }),
  })
  return parseResponse(resp)
}
