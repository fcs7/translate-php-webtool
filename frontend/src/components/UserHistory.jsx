import { useState, useEffect, useCallback } from 'react'
import { getHistory, getActivity, getQuota, getDownloadUrl, getVoipnowDownloadUrl, deleteHistoryJob, deleteHistoryBulk } from '../services/api'
import { timeAgo, expiresIn, ACTION_LABELS } from '../utils/formatters'

function QuotaBar({ quota }) {
  if (!quota) return null
  const pct = quota.percent || 0
  const color = pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-green-500'
  const textColor = pct >= 90 ? 'text-red-400' : pct >= 70 ? 'text-yellow-400' : 'text-gray-400'

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-400">Armazenamento</span>
        <span className={textColor}>
          {quota.used_mb} MB / {quota.limit_mb} MB ({pct}%)
        </span>
      </div>
      <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  )
}

export default function UserHistory({ onBack }) {
  const [tab, setTab] = useState('jobs')
  const [jobs, setJobs] = useState([])
  const [activity, setActivity] = useState([])
  const [quota, setQuota] = useState(null)
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(null)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [confirmBulk, setConfirmBulk] = useState(false)
  const [error, setError] = useState(null)

  const loadData = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([getHistory(), getActivity(), getQuota()])
      .then(([j, a, q]) => { setJobs(j); setActivity(a); setQuota(q) })
      .catch((err) => {
        console.error('Erro ao carregar dados:', err)
        setError('Erro ao carregar dados. Tente recarregar a pagina.')
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadData() }, [loadData])

  const handleDeleteJob = async (jobId) => {
    if (deleting) return
    setDeleting(jobId)
    setError(null)
    try {
      const result = await deleteHistoryJob(jobId)
      setQuota(result.quota)
      setJobs(prev => prev.filter(j => j.job_id !== jobId))
    } catch (err) {
      console.error('Erro ao deletar job:', err)
      setError(`Erro ao remover traducao: ${err.message}`)
    } finally {
      setDeleting(null)
    }
  }

  const handleBulkDelete = async (expiredOnly) => {
    setBulkDeleting(true)
    setConfirmBulk(false)
    setError(null)
    try {
      const result = await deleteHistoryBulk(expiredOnly)
      setQuota(result.quota)
      loadData()
    } catch (err) {
      console.error('Erro ao deletar em massa:', err)
      setError(`Erro ao liberar espaco: ${err.message}`)
    } finally {
      setBulkDeleting(false)
    }
  }

  if (loading) {
    return (
      <div className="text-center py-8">
        <div className="inline-block w-6 h-6 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-gray-400 text-sm mt-3">Carregando historico...</p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button
          onClick={onBack}
          className="text-gray-500 hover:text-gray-300 text-sm flex items-center gap-1.5 transition-colors"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12" />
            <polyline points="12 19 5 12 12 5" />
          </svg>
          Voltar
        </button>
        <h2 className="text-lg font-semibold text-gradient">Minha Conta</h2>
      </div>

      {/* Quota Bar */}
      <QuotaBar quota={quota} />

      {/* Error Banner */}
      {error && (
        <div className="glass-light border border-red-500/20 rounded-lg px-4 py-2.5 text-xs text-red-400 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-gray-500 hover:text-gray-300 ml-2">&times;</button>
        </div>
      )}

      {/* Botao liberar espaco */}
      {quota && quota.percent >= 80 && jobs.length > 0 && (
        <div className="relative">
          <button
            onClick={() => setConfirmBulk(!confirmBulk)}
            disabled={bulkDeleting}
            className="w-full text-sm py-2 px-3 rounded-lg bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-50"
          >
            {bulkDeleting ? 'Liberando espaco...' : 'Liberar espaco'}
          </button>
          {confirmBulk && (
            <div className="absolute top-full left-0 right-0 mt-1 glass-light rounded-lg p-3 z-10 space-y-2">
              <p className="text-xs text-gray-400">O que deseja remover?</p>
              <button
                onClick={() => handleBulkDelete(true)}
                className="w-full text-xs py-1.5 px-3 rounded bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/20 transition-colors"
              >
                Apenas expirados
              </button>
              <button
                onClick={() => handleBulkDelete(false)}
                className="w-full text-xs py-1.5 px-3 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
              >
                Todos os arquivos
              </button>
              <button
                onClick={() => setConfirmBulk(false)}
                className="w-full text-xs py-1.5 px-3 rounded text-gray-500 hover:text-gray-300 transition-colors"
              >
                Cancelar
              </button>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 glass-light rounded-lg p-1">
        <button
          onClick={() => setTab('jobs')}
          className={`flex-1 text-sm py-2 rounded-md transition-all ${
            tab === 'jobs' ? 'bg-accent-500/20 text-accent-400 font-medium' : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          Traducoes ({jobs.length})
        </button>
        <button
          onClick={() => setTab('activity')}
          className={`flex-1 text-sm py-2 rounded-md transition-all ${
            tab === 'activity' ? 'bg-accent-500/20 text-accent-400 font-medium' : 'text-gray-500 hover:text-gray-300'
          }`}
        >
          Atividade ({activity.length})
        </button>
      </div>

      {/* Job History */}
      {tab === 'jobs' && (
        <div className="space-y-2">
          {jobs.length === 0 ? (
            <p className="text-gray-500 text-sm text-center py-6">Nenhuma traducao ainda.</p>
          ) : jobs.map((j) => {
            const expired = new Date(j.expires_at) < new Date()
            const sizeMb = j.file_size_bytes ? (j.file_size_bytes / (1024 * 1024)).toFixed(1) : null
            return (
              <div key={j.job_id} className="glass-light rounded-lg p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${
                      j.status === 'completed' ? 'bg-green-400' :
                      j.status === 'failed' ? 'bg-red-400' :
                      j.status === 'cancelled' ? 'bg-yellow-400' : 'bg-gray-400'
                    }`} />
                    <span className="text-sm text-gray-200 font-mono">#{j.job_id}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      j.status === 'completed' ? 'bg-green-500/10 text-green-400' :
                      j.status === 'failed' ? 'bg-red-500/10 text-red-400' :
                      'bg-yellow-500/10 text-yellow-400'
                    }`}>
                      {j.status === 'completed' ? 'Concluido' : j.status === 'failed' ? 'Falhou' : 'Cancelado'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-500">{timeAgo(j.created_at)}</span>
                    <button
                      onClick={() => handleDeleteJob(j.job_id)}
                      disabled={deleting === j.job_id}
                      className="text-gray-600 hover:text-red-400 transition-colors disabled:opacity-50"
                      title="Remover arquivos"
                    >
                      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  </div>
                </div>

                <div className="flex items-center gap-4 text-xs text-gray-400">
                  <span>{j.total_files} arquivo{j.total_files !== 1 ? 's' : ''}</span>
                  <span>{j.translated_strings}/{j.total_strings} strings</span>
                  {sizeMb && <span>{sizeMb} MB</span>}
                  <span className={expired ? 'text-red-400' : 'text-gray-500'}>
                    {expired ? 'Expirado' : `Expira em ${expiresIn(j.expires_at)}`}
                  </span>
                </div>

                {j.status === 'completed' && !expired && (
                  <div className="flex items-center gap-3">
                    <a
                      href={getDownloadUrl(j.job_id)}
                      className="inline-flex items-center gap-1.5 text-xs text-accent-400 hover:text-accent-300 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                      Baixar ZIP
                    </a>
                    <a
                      href={getVoipnowDownloadUrl(j.job_id)}
                      className="inline-flex items-center gap-1.5 text-xs text-purple-400 hover:text-purple-300 transition-colors"
                    >
                      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                        <polyline points="7 10 12 15 17 10" />
                        <line x1="12" y1="15" x2="12" y2="3" />
                      </svg>
                      Baixar VoipNow
                    </a>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Activity Log */}
      {tab === 'activity' && (
        <div className="space-y-1">
          {activity.length === 0 ? (
            <p className="text-gray-500 text-sm text-center py-6">Nenhuma atividade registrada.</p>
          ) : activity.map((a, i) => (
            <div key={i} className="glass-light rounded-lg px-4 py-2.5 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-200 font-medium min-w-[80px]">
                  {ACTION_LABELS[a.action] || a.action}
                </span>
                {a.details && (
                  <span className="text-xs text-gray-500 truncate max-w-[180px]">{a.details}</span>
                )}
              </div>
              <span className="text-xs text-gray-600 shrink-0">{timeAgo(a.created_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
