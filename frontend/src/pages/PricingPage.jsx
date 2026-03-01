import { useState, useEffect, useRef, useCallback } from 'react'
import { getBillingPlans, createPixCheckout, getPaymentStatus } from '../services/api'

const PLAN_COLORS = {
  free: { border: 'border-gray-600', badge: 'bg-gray-500/20 text-gray-400', accent: 'text-gray-400' },
  pro: { border: 'border-accent-500', badge: 'bg-accent-500/20 text-accent-400', accent: 'text-accent-400' },
  business: { border: 'border-purple-500', badge: 'bg-purple-500/20 text-purple-400', accent: 'text-purple-400' },
}

const PLAN_FEATURES = {
  free: ['500 MB de armazenamento', 'Traducoes ilimitadas', 'Todos os providers'],
  pro: ['2 GB de armazenamento', 'Traducoes ilimitadas', 'Todos os providers', 'Prioridade no suporte'],
  business: ['10 GB de armazenamento', 'Traducoes ilimitadas', 'Todos os providers', 'Suporte prioritario', 'Ideal para equipes'],
}

function QrCodeModal({ checkout, onClose, onConfirmed }) {
  const [status, setStatus] = useState('PENDING')
  const [copied, setCopied] = useState(false)
  const intervalRef = useRef(null)

  useEffect(() => {
    if (!checkout?.payment_id) return

    const poll = async () => {
      try {
        const data = await getPaymentStatus(checkout.payment_id)
        setStatus(data.status)
        if (data.status === 'RECEIVED' || data.status === 'CONFIRMED') {
          clearInterval(intervalRef.current)
          onConfirmed()
        }
      } catch {
        // ignora erros de polling
      }
    }

    poll()
    intervalRef.current = setInterval(poll, 5000)
    return () => clearInterval(intervalRef.current)
  }, [checkout?.payment_id, onConfirmed])

  const handleCopy = async () => {
    if (!checkout?.pix_payload) return
    try {
      await navigator.clipboard.writeText(checkout.pix_payload)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // fallback
      setCopied(false)
    }
  }

  if (status === 'RECEIVED' || status === 'CONFIRMED') {
    return (
      <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
        <div className="glass rounded-2xl p-8 max-w-sm w-full text-center space-y-4 slide-up" onClick={e => e.stopPropagation()}>
          <div className="w-16 h-16 mx-auto rounded-full bg-green-500/20 flex items-center justify-center">
            <svg className="w-8 h-8 text-green-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-white">Pagamento confirmado!</h3>
          <p className="text-sm text-gray-400">Seu plano foi ativado com sucesso.</p>
          <button onClick={onClose} className="w-full py-2.5 rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 transition-colors text-sm font-medium">
            Fechar
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="glass rounded-2xl p-6 max-w-sm w-full space-y-4 slide-up" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white">Pagar com Pix</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
            <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="text-center space-y-3">
          <p className="text-2xl font-bold text-white">
            R$ {checkout.value?.toFixed(2)}
          </p>
          <p className="text-xs text-gray-500">
            Plano {checkout.plan?.charAt(0).toUpperCase() + checkout.plan?.slice(1)} — 30 dias
          </p>
        </div>

        {/* QR Code */}
        {checkout.qrcode_base64 && (
          <div className="flex justify-center">
            <div className="bg-white rounded-xl p-3">
              <img
                src={`data:image/png;base64,${checkout.qrcode_base64}`}
                alt="QR Code Pix"
                className="w-48 h-48"
              />
            </div>
          </div>
        )}

        {/* Pix copia e cola */}
        {checkout.pix_payload && (
          <div className="space-y-2">
            <p className="text-xs text-gray-500 text-center">Ou copie o codigo Pix:</p>
            <div className="glass-light rounded-lg p-3 flex items-center gap-2">
              <code className="text-xs text-gray-300 flex-1 break-all line-clamp-2">
                {checkout.pix_payload}
              </code>
              <button
                onClick={handleCopy}
                className="shrink-0 text-xs px-3 py-1.5 rounded-md bg-accent-500/20 text-accent-400 hover:bg-accent-500/30 transition-colors"
              >
                {copied ? 'Copiado!' : 'Copiar'}
              </button>
            </div>
          </div>
        )}

        {/* Status */}
        <div className="flex items-center justify-center gap-2 text-xs text-gray-500">
          <div className="w-3 h-3 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
          Aguardando pagamento...
        </div>

        {checkout.expiration && (
          <p className="text-xs text-gray-600 text-center">
            Expira em: {new Date(checkout.expiration).toLocaleString('pt-BR')}
          </p>
        )}
      </div>
    </div>
  )
}

export default function PricingPage({ onBack, currentPlan, onPlanUpdated }) {
  const [plans, setPlans] = useState([])
  const [loading, setLoading] = useState(true)
  const [checkoutLoading, setCheckoutLoading] = useState(null)
  const [checkout, setCheckout] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getBillingPlans()
      .then(setPlans)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  const handleCheckout = async (planId) => {
    setCheckoutLoading(planId)
    setError(null)
    try {
      const result = await createPixCheckout(planId)
      setCheckout(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setCheckoutLoading(null)
    }
  }

  const handleConfirmed = useCallback(() => {
    setTimeout(() => {
      setCheckout(null)
      if (onPlanUpdated) onPlanUpdated()
    }, 2000)
  }, [onPlanUpdated])

  if (loading) {
    return (
      <div className="text-center py-8">
        <div className="inline-block w-6 h-6 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-gray-400 text-sm mt-3">Carregando planos...</p>
      </div>
    )
  }

  const userPlan = currentPlan?.plan || 'free'

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
        <h2 className="text-lg font-semibold text-gradient">Planos</h2>
      </div>

      {error && (
        <div className="glass-light border border-red-500/20 rounded-lg px-4 py-2.5 text-xs text-red-400 flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-gray-500 hover:text-gray-300 ml-2">&times;</button>
        </div>
      )}

      {/* Plan Cards */}
      <div className="space-y-3">
        {plans.map(plan => {
          const colors = PLAN_COLORS[plan.id] || PLAN_COLORS.free
          const features = PLAN_FEATURES[plan.id] || []
          const isCurrent = userPlan === plan.id
          const isActive = isCurrent && currentPlan?.status === 'active'

          return (
            <div
              key={plan.id}
              className={`glass-light rounded-xl p-5 space-y-3 border transition-all ${
                isCurrent ? colors.border + ' border-opacity-50' : 'border-transparent'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className={`text-base font-semibold ${colors.accent}`}>
                    {plan.name}
                  </h3>
                  {isCurrent && (
                    <span className={`text-xs px-2 py-0.5 rounded-full ${colors.badge}`}>
                      Atual
                    </span>
                  )}
                </div>
                <div className="text-right">
                  {plan.price > 0 ? (
                    <>
                      <span className="text-xl font-bold text-white">R${plan.price}</span>
                      <span className="text-xs text-gray-500 ml-1">/{plan.duration_days}d</span>
                    </>
                  ) : (
                    <span className="text-lg font-semibold text-gray-400">Gratis</span>
                  )}
                </div>
              </div>

              <div className="text-sm text-gray-400">
                {plan.storage_gb >= 1
                  ? `${plan.storage_gb} GB`
                  : `${plan.storage_mb} MB`
                } de armazenamento
              </div>

              <ul className="space-y-1.5">
                {features.map((f, i) => (
                  <li key={i} className="flex items-center gap-2 text-xs text-gray-400">
                    <svg className={`w-3.5 h-3.5 ${colors.accent} shrink-0`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                    {f}
                  </li>
                ))}
              </ul>

              {plan.price > 0 && !isCurrent && (
                <button
                  onClick={() => handleCheckout(plan.id)}
                  disabled={!!checkoutLoading}
                  className={`w-full py-2.5 rounded-lg text-sm font-medium transition-all disabled:opacity-50 ${
                    plan.id === 'pro'
                      ? 'bg-accent-500/20 text-accent-400 hover:bg-accent-500/30'
                      : 'bg-purple-500/20 text-purple-400 hover:bg-purple-500/30'
                  }`}
                >
                  {checkoutLoading === plan.id ? (
                    <span className="flex items-center justify-center gap-2">
                      <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
                      Gerando Pix...
                    </span>
                  ) : (
                    `Pagar com Pix — R$${plan.price}`
                  )}
                </button>
              )}

              {plan.price > 0 && isCurrent && isActive && currentPlan?.days_remaining != null && (
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">
                    {currentPlan.days_remaining > 0
                      ? `${currentPlan.days_remaining} dia(s) restante(s)`
                      : 'Expira hoje'}
                  </span>
                  {currentPlan.days_remaining <= 5 && (
                    <button
                      onClick={() => handleCheckout(plan.id)}
                      disabled={!!checkoutLoading}
                      className="text-yellow-400 hover:text-yellow-300 transition-colors disabled:opacity-50"
                    >
                      Renovar
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* QR Code Modal */}
      {checkout && (
        <QrCodeModal
          checkout={checkout}
          onClose={() => setCheckout(null)}
          onConfirmed={handleConfirmed}
        />
      )}
    </div>
  )
}
