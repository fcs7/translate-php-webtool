const API_BASE = '/api'

// ─── Autenticacao ─────────────────────────────────────────────────────────────

export async function register(email, password) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Erro ao cadastrar')
  return data
}

export async function login(email, password) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'E-mail ou senha incorretos')
  return data
}

export async function requestOtp(email) {
  const res = await fetch(`${API_BASE}/auth/request-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Erro ao enviar codigo')
  return data
}

export async function verifyOtp(email, code) {
  const res = await fetch(`${API_BASE}/auth/verify-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, code }),
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Codigo invalido')
  return data
}

export async function logout() {
  await fetch(`${API_BASE}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
}

export async function getMe() {
  const res = await fetch(`${API_BASE}/auth/me`, { credentials: 'include' })
  if (!res.ok) throw new Error('Nao autenticado')
  return res.json()
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export async function uploadZip(file, delay = 0.2) {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('delay', delay.toString())

  const res = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  })

  if (!res.ok) {
    const data = await res.json()
    throw new Error(data.error || 'Erro ao enviar arquivo')
  }

  return res.json()
}

export async function uploadFiles(files, delay = 0.2) {
  const formData = new FormData()
  formData.append('delay', delay.toString())

  for (const f of files) {
    formData.append('files', f)
    // Preservar caminho relativo (pasta) quando disponivel
    const relPath = f.webkitRelativePath || f.name
    formData.append('paths', relPath)
  }

  const res = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  })

  if (!res.ok) {
    const data = await res.json()
    throw new Error(data.error || 'Erro ao enviar arquivos')
  }

  return res.json()
}

export async function getJobStatus(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, { credentials: 'include' })
  if (!res.ok) throw new Error('Job nao encontrado')
  return res.json()
}

export async function getJobs() {
  const res = await fetch(`${API_BASE}/jobs`, { credentials: 'include' })
  return res.json()
}

export async function cancelJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
    method: 'POST',
    credentials: 'include',
  })
  return res.json()
}

export async function deleteJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  return res.json()
}

export function getDownloadUrl(jobId) {
  return `${API_BASE}/jobs/${jobId}/download`
}

export function getVoipnowDownloadUrl(jobId) {
  return `${API_BASE}/jobs/${jobId}/download/voipnow`
}

// ─── Historico do usuario ────────────────────────────────────────────────────

export async function getHistory() {
  const res = await fetch(`${API_BASE}/history`, { credentials: 'include' })
  if (!res.ok) throw new Error('Erro ao carregar historico')
  return res.json()
}

export async function getActivity(limit = 50) {
  const res = await fetch(`${API_BASE}/activity?limit=${limit}`, { credentials: 'include' })
  if (!res.ok) throw new Error('Erro ao carregar atividades')
  return res.json()
}

// ─── Quota e gerenciamento de historico ──────────────────────────────────────

export async function getQuota() {
  const res = await fetch(`${API_BASE}/quota`, { credentials: 'include' })
  if (!res.ok) throw new Error('Erro ao carregar quota')
  return res.json()
}

export async function deleteHistoryJob(jobId) {
  const res = await fetch(`${API_BASE}/history/${jobId}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Erro ao deletar job')
  return data
}

export async function deleteHistoryBulk(expiredOnly = false) {
  const qs = expiredOnly ? '?expired_only=true' : ''
  const res = await fetch(`${API_BASE}/history${qs}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Erro ao deletar jobs')
  return data
}

// ─── Admin ──────────────────────────────────────────────────────────────────

export async function adminGetUsers(token) {
  const res = await fetch(`${API_BASE}/admin/users`, {
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Erro ao carregar usuarios')
  return res.json()
}

export async function adminGetStats(token) {
  const res = await fetch(`${API_BASE}/admin/stats`, {
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Erro ao carregar estatisticas')
  return res.json()
}

export async function adminGetActivity(token, limit = 100) {
  const res = await fetch(`${API_BASE}/admin/activity?limit=${limit}`, {
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Erro ao carregar atividades')
  return res.json()
}

export async function adminGetJobHistory(token, limit = 100) {
  const res = await fetch(`${API_BASE}/admin/job-history?limit=${limit}`, {
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error('Erro ao carregar historico')
  return res.json()
}

export async function adminToggleAdmin(token, userId) {
  const res = await fetch(`${API_BASE}/admin/users/${userId}/toggle-admin`, {
    method: 'POST',
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  return res.json()
}

export async function adminDeleteUser(token, userId) {
  const res = await fetch(`${API_BASE}/admin/users/${userId}`, {
    method: 'DELETE',
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  return res.json()
}

export async function adminLogin(token) {
  const res = await fetch(`${API_BASE}/admin/login`, {
    method: 'POST',
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!res.ok) {
    const data = await res.json()
    throw new Error(data.error || 'Acesso negado')
  }
  return res.json()
}

export async function adminReconcileStorage(token) {
  const res = await fetch(`${API_BASE}/admin/reconcile-storage`, {
    method: 'POST',
    credentials: 'include',
    headers: { Authorization: `Bearer ${token}` },
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Erro ao reconciliar storage')
  return data
}

// ─── Cache ───────────────────────────────────────────────────────────────────

export async function clearUntranslatedCache() {
  const res = await fetch(`${API_BASE}/cache/clear-untranslated`, {
    method: 'POST',
    credentials: 'include',
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || 'Erro ao limpar cache')
  return data
}
