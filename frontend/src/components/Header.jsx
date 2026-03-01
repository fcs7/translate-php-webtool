import PlanBadge from './PlanBadge'

export default function Header({ user, onLogout, onHistory, onAdmin, onPricing }) {
  const quota = user?.quota

  const quotaColor = !quota ? 'bg-accent-500'
    : quota.percent > 95 ? 'bg-red-500'
    : quota.percent > 80 ? 'bg-yellow-500'
    : 'bg-accent-500'

  return (
    <header className="glass border-b border-white/5">
      <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-4">
        {/* Logo */}
        <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-accent-500 to-glow-gold flex items-center justify-center shadow-lg shadow-accent-500/25">
          <svg className="w-6 h-6 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M5 7h14M12 7v12M8 7V5M16 7V5" />
          </svg>
        </div>

        {/* Title block */}
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-bold text-gradient leading-tight tracking-tight">Traducao</h1>
          <p className="text-xs text-gray-500 mt-0.5 tracking-wide">Tradutor PHP  ·  EN  →  PT-BR</p>
        </div>

        {/* User info & actions */}
        {user && onLogout && (
          <div className="flex items-center gap-2">
            {/* Quota */}
            {quota && (
              <div className="hidden sm:flex items-center gap-2">
                <div className="w-20 bg-surface-800/60 rounded-full h-1.5 overflow-hidden border border-white/5">
                  <div
                    className={`h-full rounded-full transition-all ${quotaColor}`}
                    style={{ width: `${Math.min(quota.percent, 100)}%` }}
                  />
                </div>
                <span className="text-xs text-gray-500 whitespace-nowrap">
                  {quota.used_mb} / {quota.limit_mb} MB
                </span>
              </div>
            )}
            <PlanBadge plan={user?.plan} onClick={onPricing} />
            <span className="hidden sm:inline-flex items-center gap-2 text-xs text-gray-400 bg-surface-800/60 border border-white/5 rounded-full px-3.5 py-1.5">
              <svg className="w-3.5 h-3.5 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                <circle cx="12" cy="7" r="4" />
              </svg>
              {user.email}
            </span>
            {/* Historico */}
            <button
              onClick={onHistory}
              className="glass-light text-xs text-gray-400 hover:text-white px-2.5 py-1.5 rounded-lg transition-colors"
              title="Minha conta"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <polyline points="12 6 12 12 16 14" />
              </svg>
            </button>
            {/* Admin */}
            {user.is_admin ? (
              <button
                onClick={onAdmin}
                className="glass-light text-xs text-accent-400 hover:text-white px-2.5 py-1.5 rounded-lg transition-colors"
                title="Painel admin"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/>
                </svg>
              </button>
            ) : null}
            {/* Logout */}
            <button
              onClick={onLogout}
              className="glass-light text-xs text-gray-400 hover:text-white px-3 py-1.5 rounded-lg transition-colors flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              Sair
            </button>
          </div>
        )}
      </div>
    </header>
  )
}
