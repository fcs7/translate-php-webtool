import { useState, useEffect, useCallback } from 'react'
import {
  adminLogin, adminGetUsers, adminGetStats, adminGetActivity,
  adminGetJobHistory, adminToggleAdmin, adminDeleteUser,
  adminReconcileStorage,
} from '../services/api'
import { timeAgo, ACTION_LABELS } from '../utils/formatters'

export default function AdminPanel({ onBack }) {
  const [token, setToken] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const [tab, setTab] = useState('stats')
  const [stats, setStats] = useState(null)
  const [users, setUsers] = useState([])
  const [activity, setActivity] = useState([])
  const [jobHistory, setJobHistory] = useState([])
  const [confirmDelete, setConfirmDelete] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [actionLoading, setActionLoading] = useState(null)
  const [actionError, setActionError] = useState('')
  const [reconciling, setReconciling] = useState(false)
  const [reconcileResult, setReconcileResult] = useState(null)

  // Auth admin
  useEffect(() => {
    adminLogin()
      .then(data => setToken(data.token))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  // Cada request independente — falha de um nao afeta os outros
  const loadData = useCallback(async () => {
    if (!token) return
    setRefreshing(true)

    try { setStats(await adminGetStats(token)) } catch (e) { console.error('[Admin] stats:', e.message) }
    try { setUsers(await adminGetUsers(token)) } catch (e) { console.error('[Admin] users:', e.message) }
    try { setActivity(await adminGetActivity(token)) } catch (e) { console.error('[Admin] activity:', e.message) }
    try { setJobHistory(await adminGetJobHistory(token)) } catch (e) { console.error('[Admin] jobs:', e.message) }

    setRefreshing(false)
  }, [token])

  useEffect(() => { loadData() }, [loadData])

  // Limpa erro de acao apos 5s
  useEffect(() => {
    if (!actionError) return
    const t = setTimeout(() => setActionError(''), 5000)
    return () => clearTimeout(t)
  }, [actionError])

  async function handleToggleAdmin(userId) {
    setActionLoading(`toggle-${userId}`)
    try {
      await adminToggleAdmin(token, userId)
      await loadData()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  async function handleDeleteUser(userId) {
    setActionLoading(`delete-${userId}`)
    try {
      await adminDeleteUser(token, userId)
      setConfirmDelete(null)
      await loadData()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  async function handleReconcile() {
    setReconciling(true)
    setReconcileResult(null)
    try {
      const result = await adminReconcileStorage(token)
      setReconcileResult(result)
      await loadData()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setReconciling(false)
    }
  }

  if (loading) {
    return (
      <div className="text-center py-8">
        <div className="inline-block w-6 h-6 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-gray-400 text-sm mt-3">Autenticando admin...</p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="text-gray-500 hover:text-gray-300 text-sm flex items-center gap-1.5 transition-colors">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
          Voltar
        </button>
        <div className="glass-light border border-red-500/20 rounded-lg px-4 py-3 text-red-400 text-sm">{error}</div>
      </div>
    )
  }

  const tabs = [
    { id: 'stats', label: 'Painel' },
    { id: 'users', label: users.length > 0 ? `Usuarios (${users.length})` : 'Usuarios' },
    { id: 'activity', label: 'Atividade' },
    { id: 'jobs', label: 'Jobs' },
  ]

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={onBack} className="text-gray-500 hover:text-gray-300 text-sm flex items-center gap-1.5 transition-colors">
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>
          Voltar
        </button>
        <h2 className="text-lg font-semibold text-gradient flex items-center gap-2">
          <svg className="w-5 h-5 text-accent-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
          </svg>
          Admin
        </h2>
      </div>

      {/* Erro temporario de acao */}
      {actionError && (
        <div className="glass-light border border-red-500/20 rounded-lg px-4 py-2 text-red-400 text-sm flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError('')} className="text-red-400/60 hover:text-red-400 ml-2">&times;</button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 glass-light rounded-lg p-1">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 text-xs py-2 rounded-md transition-all ${
              tab === t.id ? 'bg-accent-500/20 text-accent-400 font-medium' : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Stats ────────────────────────────────────────── */}
      {tab === 'stats' && !stats && (
        <div className="text-center py-8">
          {refreshing ? (
            <>
              <div className="inline-block w-5 h-5 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-gray-500 text-sm mt-3">Carregando dados...</p>
            </>
          ) : (
            <p className="text-gray-500 text-sm">Nenhum dado disponivel.</p>
          )}
        </div>
      )}
      {tab === 'stats' && stats && (
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: 'Usuarios', value: stats.users, color: 'accent' },
            { label: 'Admins', value: stats.admins, color: 'accent' },
            { label: 'Jobs rodando', value: `${stats.running_jobs}/${stats.max_concurrent_jobs}`, color: 'green' },
            { label: 'Sessoes admin', value: stats.active_admin_sessions, color: 'yellow' },
            { label: 'Cache (entradas)', value: stats.cache_entries?.toLocaleString(), color: 'cyan' },
            { label: 'Cache (hits)', value: stats.cache_total_hits?.toLocaleString(), color: 'cyan' },
          ].map((s, i) => (
            <div key={i} className="glass-light rounded-lg p-3">
              <p className="text-xs text-gray-500 mb-1">{s.label}</p>
              <p className={`text-xl font-bold font-mono text-${s.color === 'accent' ? 'accent-400' : s.color + '-400'}`}>
                {s.value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* ── Reconcile Storage (dentro de stats) ──────────── */}
      {tab === 'stats' && stats && (
        <div className="space-y-2">
          <button
            onClick={handleReconcile}
            disabled={reconciling}
            className="w-full glass-light text-xs text-gray-400 hover:text-accent-400 disabled:opacity-50 px-4 py-2.5 rounded-lg transition-all flex items-center justify-center gap-2"
          >
            {reconciling ? (
              <div className="w-3.5 h-3.5 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.3"/>
              </svg>
            )}
            {reconciling ? 'Reconciliando storage...' : 'Reconciliar Storage'}
          </button>
          {reconcileResult && (
            <div className="glass-light border border-green-500/20 rounded-lg px-4 py-2.5 text-xs text-green-400 space-y-1">
              <p>Reconciliacao concluida: {reconcileResult.users_updated} usuario(s) atualizado(s)</p>
              {reconcileResult.details && reconcileResult.details.length > 0 && (
                <ul className="text-gray-500 space-y-0.5">
                  {reconcileResult.details.map((d, i) => (
                    <li key={i}>{d.email}: {(d.old_bytes / (1024*1024)).toFixed(1)}MB → {(d.new_bytes / (1024*1024)).toFixed(1)}MB</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Users ────────────────────────────────────────── */}
      {tab === 'users' && (
        <div className="space-y-2">
          {users.map(u => (
            <div key={u.id} className="glass-light rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm text-gray-200 truncate">{u.email}</span>
                  {u.is_admin ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-accent-500/20 text-accent-400 shrink-0">admin</span>
                  ) : null}
                </div>
                <span className="text-xs text-gray-600 shrink-0">#{u.id}</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-gray-500">Desde {new Date(u.created_at).toLocaleDateString('pt-BR')}</span>
                <div className="flex-1" />
                <button
                  onClick={() => handleToggleAdmin(u.id)}
                  disabled={!!actionLoading}
                  className="text-gray-500 hover:text-accent-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors px-2 py-1 rounded flex items-center gap-1"
                >
                  {actionLoading === `toggle-${u.id}` && (
                    <div className="w-3 h-3 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
                  )}
                  {u.is_admin ? 'Remover admin' : 'Tornar admin'}
                </button>
                {confirmDelete === u.id ? (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => handleDeleteUser(u.id)}
                      disabled={!!actionLoading}
                      className="text-red-400 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed px-2 py-1 rounded text-xs font-medium flex items-center gap-1"
                    >
                      {actionLoading === `delete-${u.id}` && (
                        <div className="w-3 h-3 border-2 border-red-400 border-t-transparent rounded-full animate-spin" />
                      )}
                      Confirmar
                    </button>
                    <button onClick={() => setConfirmDelete(null)} disabled={!!actionLoading} className="text-gray-500 hover:text-gray-300 disabled:opacity-50 px-2 py-1 rounded">
                      Cancelar
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirmDelete(u.id)}
                    disabled={!!actionLoading}
                    className="text-gray-600 hover:text-red-400 disabled:opacity-50 disabled:cursor-not-allowed transition-colors px-2 py-1 rounded"
                  >
                    Deletar
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Activity ─────────────────────────────────────── */}
      {tab === 'activity' && (
        <div className="space-y-1 max-h-[60vh] overflow-y-auto">
          {activity.length === 0 ? (
            <p className="text-gray-500 text-sm text-center py-6">Nenhuma atividade.</p>
          ) : activity.map((a, i) => (
            <div key={i} className="glass-light rounded-lg px-3 py-2 flex items-center gap-3 text-xs">
              <span className="text-gray-200 font-medium min-w-[70px]">{ACTION_LABELS[a.action] || a.action}</span>
              <span className="text-accent-400 truncate min-w-[120px]">{a.user_email}</span>
              <span className="text-gray-600 truncate flex-1">{a.details || ''}</span>
              <span className="text-gray-600 shrink-0">{a.ip_address}</span>
              <span className="text-gray-600 shrink-0">{timeAgo(a.created_at)}</span>
            </div>
          ))}
        </div>
      )}

      {/* ── Job History ──────────────────────────────────── */}
      {tab === 'jobs' && (
        <div className="space-y-2 max-h-[60vh] overflow-y-auto">
          {jobHistory.length === 0 ? (
            <p className="text-gray-500 text-sm text-center py-6">Nenhum job no historico.</p>
          ) : jobHistory.map((j, i) => {
            const expired = new Date(j.expires_at) < new Date()
            return (
              <div key={i} className="glass-light rounded-lg p-3">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${
                      j.status === 'completed' ? 'bg-green-400' : j.status === 'failed' ? 'bg-red-400' : 'bg-yellow-400'
                    }`} />
                    <span className="text-xs font-mono text-gray-300">#{j.job_id}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      j.status === 'completed' ? 'bg-green-500/10 text-green-400' :
                      j.status === 'failed' ? 'bg-red-500/10 text-red-400' : 'bg-yellow-500/10 text-yellow-400'
                    }`}>{j.status}</span>
                  </div>
                  <span className="text-xs text-gray-500">{timeAgo(j.created_at)}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500">
                  <span className="text-accent-400">{j.user_email}</span>
                  <span>{j.total_files} arq.</span>
                  <span>{j.translated_strings}/{j.total_strings} str</span>
                  <span className={expired ? 'text-red-400' : ''}>{expired ? 'Expirado' : `Exp. ${timeAgo(j.expires_at)}`}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Refresh */}
      <div className="flex justify-center">
        <button
          onClick={loadData}
          disabled={refreshing}
          className="glass-light text-xs text-gray-500 hover:text-gray-300 disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2 rounded-lg transition-all flex items-center gap-1.5"
        >
          {refreshing ? (
            <div className="w-3.5 h-3.5 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
          ) : (
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
          )}
          {refreshing ? 'Atualizando...' : 'Atualizar'}
        </button>
      </div>
    </div>
  )
}
