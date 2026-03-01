import { useState, useCallback, useEffect, useRef } from 'react'
import Header from './components/Header'
import FileUpload from './components/FileUpload'
import TranslationProgress from './components/TranslationProgress'
import UserHistory from './components/UserHistory'
import LoginPage from './pages/LoginPage'
import AdminPanel from './pages/AdminPanel'
import { useSocket } from './hooks/useSocket'
import { useAuth } from './hooks/useAuth'
import { uploadZip, uploadFiles, cancelJob, deleteJob, getJobs, getJobStatus } from './services/api'

export default function App() {
  const { user, loading, isAuthenticated, logout, refetch } = useAuth()
  const [currentJobId, setCurrentJobId] = useState(null)
  const [page, setPage] = useState('main') // 'main' | 'history' | 'admin'
  const { jobData, setJobData, connected, joinJob } = useSocket()
  const hasRestoredRef = useRef(false)

  // ─── Restaurar job ativo ao autenticar ────────────────────────────────────
  useEffect(() => {
    if (!isAuthenticated) {
      hasRestoredRef.current = false
      return
    }
    if (hasRestoredRef.current) return
    hasRestoredRef.current = true

    getJobs()
      .then(jobs => {
        if (!Array.isArray(jobs) || !jobs.length) return
        const sorted = [...jobs].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
        // Prioriza job em execucao/pendente, depois o mais recente
        const active =
          sorted.find(j => j.status === 'running' || j.status === 'pending') ||
          sorted[0]
        if (active) {
          setCurrentJobId(active.job_id)
          setJobData(active)
          joinJob(active.job_id)
        }
      })
      .catch(() => {})
  }, [isAuthenticated, joinJob, setJobData])

  // ─── Polling de status como fallback ao reconectar ───────────────────────
  // - Job em estado final: polling desnecessário, para completamente.
  // - WebSocket conectado: polling de segurança a cada 30s.
  // - WebSocket desconectado: polling ativo a cada 5s.
  useEffect(() => {
    if (!currentJobId) return

    const TERMINAL = ['completed', 'failed', 'cancelled']
    if (TERMINAL.includes(jobData?.status)) return

    const poll = async () => {
      try {
        const data = await getJobStatus(currentJobId)
        setJobData(data)
      } catch {
        // ignora erros de rede
      }
    }

    poll() // busca imediata ao montar/reconectar
    const intervalId = setInterval(poll, connected ? 30_000 : 5_000)
    return () => clearInterval(intervalId)
  }, [currentJobId, connected, jobData?.status, setJobData])

  // ─── Recarregar quota apos job completar/falhar ─────────────────────────
  useEffect(() => {
    if (jobData?.status === 'completed' || jobData?.status === 'failed') {
      refetch()
    }
  }, [jobData?.status, refetch])

  // ─── Handlers ────────────────────────────────────────────────────────────

  const handleUpload = useCallback(async (fileOrFiles, delay) => {
    const isMultiple = Array.isArray(fileOrFiles)
    const { job_id } = isMultiple
      ? await uploadFiles(fileOrFiles, delay)
      : await uploadZip(fileOrFiles, delay)
    setCurrentJobId(job_id)
    joinJob(job_id)
  }, [joinJob])

  const handleCancel = useCallback(async () => {
    if (currentJobId) {
      await cancelJob(currentJobId)
    }
  }, [currentJobId])

  const handleDelete = useCallback(async () => {
    if (currentJobId) {
      await deleteJob(currentJobId)
      setCurrentJobId(null)
      setJobData(null)
      refetch() // Atualizar quota apos devolver storage
    }
  }, [currentJobId, setJobData, refetch])

  const handleNewTranslation = useCallback(() => {
    setCurrentJobId(null)
    setJobData(null)
  }, [setJobData])

  // ─── Carregando sessao ───────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="min-h-screen bg-surface-950 flex items-center justify-center">
        <div className="shimmer w-8 h-8 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  // ─── Nao autenticado → tela de login ────────────────────────────────────
  if (!isAuthenticated) {
    return <LoginPage onSuccess={() => refetch()} />
  }

  // ─── App principal ───────────────────────────────────────────────────────
  const showUpload = !currentJobId
  const showProgress = currentJobId && jobData

  return (
    <div className="min-h-screen bg-surface-950 flex flex-col relative">
      {/* Decorative background orbs */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="orb w-96 h-96 bg-accent-500 -top-48 -left-48" />
        <div className="orb w-80 h-80 bg-accent-400 top-1/2 -right-40" style={{ animationDelay: '4s' }} />
        <div className="orb w-64 h-64 bg-glow-gold bottom-0 left-1/3" style={{ animationDelay: '2s' }} />
      </div>

      <Header
        user={user}
        onLogout={logout}
        onHistory={() => setPage('history')}
        onAdmin={() => setPage('admin')}
      />

      <main className="flex-1 flex items-start justify-center p-6 relative z-10">
        <div className="w-full max-w-xl space-y-6 mt-8 fade-in">

          {/* ── Historico ─────────────────────────────────── */}
          {page === 'history' && (
            <div className="glass rounded-2xl p-6 shadow-2xl slide-up">
              <UserHistory onBack={() => setPage('main')} />
            </div>
          )}

          {/* ── Admin ─────────────────────────────────────── */}
          {page === 'admin' && (
            <div className="glass rounded-2xl p-6 shadow-2xl slide-up">
              <AdminPanel onBack={() => setPage('main')} />
            </div>
          )}

          {/* ── Traducao (pagina principal) ────────────────── */}
          {page === 'main' && (
            <>
              <div className="glass rounded-2xl p-6 shadow-2xl slide-up">
                <div className="mb-6">
                  <h2 className="text-lg font-semibold text-gradient flex items-center gap-2">
                    {showUpload ? (
                      <>
                        <svg className="w-5 h-5 text-accent-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                          <polyline points="17 8 12 3 7 8" />
                          <line x1="12" y1="3" x2="12" y2="15" />
                        </svg>
                        Enviar arquivos
                      </>
                    ) : (
                      <>
                        <svg className="w-5 h-5 text-accent-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <line x1="12" y1="20" x2="12" y2="10" />
                          <line x1="18" y1="20" x2="18" y2="4" />
                          <line x1="6" y1="20" x2="6" y2="16" />
                        </svg>
                        Progresso
                      </>
                    )}
                  </h2>
                  <p className="text-sm text-gray-400 mt-1">
                    {showUpload
                      ? 'Envie seus arquivos PHP para traduzir'
                      : 'Acompanhe a traducao em tempo real'
                    }
                  </p>
                </div>

                {showUpload && user?.quota?.percent >= 100 ? (
                  <div className="glass-light border border-red-500/20 rounded-lg p-4 space-y-2 text-center">
                    <p className="text-red-400 text-sm font-medium">Armazenamento cheio</p>
                    <p className="text-gray-500 text-xs">
                      Voce atingiu {user.quota.used_mb} MB / {user.quota.limit_mb} MB.
                      Libere espaco no seu historico para enviar novos arquivos.
                    </p>
                    <button
                      onClick={() => setPage('history')}
                      className="text-xs text-accent-400 hover:text-accent-300 transition-colors"
                    >
                      Ir para Minha Conta
                    </button>
                  </div>
                ) : showUpload && (
                  <FileUpload onUpload={handleUpload} disabled={false} />
                )}

                {showProgress && (
                  <TranslationProgress
                    job={jobData}
                    onCancel={handleCancel}
                    onDelete={handleDelete}
                    onNewTranslation={handleNewTranslation}
                  />
                )}

                {currentJobId && !jobData && (
                  <div className="text-center py-8">
                    <div className="inline-block shimmer w-6 h-6 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
                    <p className="text-gray-400 text-sm mt-3">Carregando...</p>
                  </div>
                )}
              </div>

            </>
          )}

        </div>
      </main>
    </div>
  )
}
