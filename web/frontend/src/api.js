// 后端 API 封装。所有 fetch 走相对路径 /api，dev 下由 Vite 代理到 FastAPI。

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function parseResponse(resp) {
  // 统一处理：HTTP 层错误也包装成 { status: 'error', error }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      detail = body.detail || body.error || detail
    } catch (_) {
      /* 非 JSON 错误体，保留默认 */
    }
    return { status: 'error', error: detail }
  }
  return resp.json()
}

export async function startSession() {
  const resp = await fetch('/api/start', { method: 'POST' })
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
