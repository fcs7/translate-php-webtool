import { useState, useRef, useEffect, useCallback } from 'react'
import { login, register, requestOtp, verifyOtp } from '../services/api'

export default function LoginPage({ onSuccess }) {
  // 'login' | 'otp-request' | 'otp-verify'
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showCreatePrompt, setShowCreatePrompt] = useState(false)

  // OTP state
  const [digits, setDigits] = useState(['', '', '', '', '', ''])
  const [resendCountdown, setResendCountdown] = useState(0)
  const digitRefs = useRef([])
  const countdownRef = useRef(null)
  const emailRef = useRef(null)

  useEffect(() => {
    emailRef.current?.focus()
  }, [])

  useEffect(() => () => clearInterval(countdownRef.current), [])

  // Timer de reenvio OTP
  const startCountdown = useCallback((seconds = 60) => {
    setResendCountdown(seconds)
    clearInterval(countdownRef.current)
    countdownRef.current = setInterval(() => {
      setResendCountdown(prev => {
        if (prev <= 1) {
          clearInterval(countdownRef.current)
          return 0
        }
        return prev - 1
      })
    }, 1000)
  }, [])

  function isValidEmail(v) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)
  }

  // ─── Login com senha ────────────────────────────────────────────────────────

  async function handleLogin(e) {
    e.preventDefault()
    if (showCreatePrompt) return
    setError('')

    if (!isValidEmail(email)) {
      setError('Digite um e-mail valido.')
      return
    }
    if (!password) {
      setError('Digite sua senha.')
      return
    }

    setLoading(true)
    try {
      const data = await login(email, password)
      onSuccess(data.user)
    } catch (err) {
      if (err.code === 'user_not_found') {
        setShowCreatePrompt(true)
      } else {
        setError(err.message)
      }
    } finally {
      setLoading(false)
    }
  }

  // ─── Auto-cadastro (confirmacao) ────────────────────────────────────────────

  async function handleConfirmRegister() {
    setError('')

    if (password.length < 6) {
      setError('Senha deve ter pelo menos 6 caracteres.')
      return
    }

    setLoading(true)
    try {
      const data = await register(email, password)
      onSuccess(data.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  // ─── OTP ────────────────────────────────────────────────────────────────────

  async function handleRequestOtp(e) {
    e.preventDefault()
    setError('')

    if (!isValidEmail(email)) {
      setError('Digite um e-mail valido.')
      return
    }

    setLoading(true)
    try {
      await requestOtp(email)
      setMode('otp-verify')
      startCountdown(60)
      setTimeout(() => digitRefs.current[0]?.focus(), 50)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function handleDigitChange(index, value) {
    const digit = value.replace(/\D/g, '').slice(-1)
    const next = [...digits]
    next[index] = digit
    setDigits(next)
    setError('')
    if (digit && index < 5) {
      digitRefs.current[index + 1]?.focus()
    }
  }

  function handleDigitKeyDown(index, e) {
    if (e.key === 'Backspace') {
      if (digits[index]) {
        const next = [...digits]
        next[index] = ''
        setDigits(next)
      } else if (index > 0) {
        digitRefs.current[index - 1]?.focus()
      }
    } else if (e.key === 'ArrowLeft' && index > 0) {
      digitRefs.current[index - 1]?.focus()
    } else if (e.key === 'ArrowRight' && index < 5) {
      digitRefs.current[index + 1]?.focus()
    }
  }

  function handlePaste(e) {
    e.preventDefault()
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6)
    const next = ['', '', '', '', '', '']
    for (let i = 0; i < pasted.length; i++) next[i] = pasted[i]
    setDigits(next)
    setError('')
    const focusIdx = Math.min(pasted.length, 5)
    digitRefs.current[focusIdx]?.focus()
  }

  async function handleVerifyOtp(e) {
    e.preventDefault()
    setError('')
    const code = digits.join('')
    if (code.length < 6) {
      setError('Digite todos os 6 digitos.')
      return
    }
    setLoading(true)
    try {
      const data = await verifyOtp(email, code)
      onSuccess(data.user)
    } catch (err) {
      setError(err.message)
      setDigits(['', '', '', '', '', ''])
      setTimeout(() => digitRefs.current[0]?.focus(), 50)
    } finally {
      setLoading(false)
    }
  }

  async function handleResend() {
    if (resendCountdown > 0) return
    setError('')
    setDigits(['', '', '', '', '', ''])
    setLoading(true)
    try {
      await requestOtp(email)
      startCountdown(60)
      setTimeout(() => digitRefs.current[0]?.focus(), 50)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function switchMode(newMode) {
    setMode(newMode)
    setError('')
    setPassword('')
    setShowCreatePrompt(false)
    setDigits(['', '', '', '', '', ''])
  }

  // ─── Componentes reutilizaveis ──────────────────────────────────────────────

  const ErrorBox = () => error && (
    <div className="glass-light border border-red-500/20 rounded-lg px-3 py-2 flex items-center gap-2">
      <svg className="w-4 h-4 text-red-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      <p className="text-red-400 text-sm">{error}</p>
    </div>
  )

  const Spinner = () => (
    <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  )

  const EmailInput = () => (
    <div className="space-y-2">
      <label className="text-sm text-gray-400 flex items-center gap-2">
        <svg className="w-4 h-4 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
          <polyline points="22,6 12,13 2,6" />
        </svg>
        E-mail
      </label>
      <input
        ref={emailRef}
        type="email"
        value={email}
        onChange={e => { setEmail(e.target.value); setError(''); setShowCreatePrompt(false) }}
        placeholder="Digite seu e-mail"
        autoComplete="email"
        className="input-glow w-full bg-surface-800/60 border border-white/10 rounded-lg px-4 py-2.5
                   text-white placeholder-gray-600 text-sm outline-none
                   focus:border-accent-500 focus:ring-1 focus:ring-accent-500/30
                   transition-all"
      />
    </div>
  )

  const PasswordInput = () => (
    <div className="space-y-2">
      <label className="text-sm text-gray-400 flex items-center gap-2">
        <svg className="w-4 h-4 text-gray-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
          <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        </svg>
        Senha
      </label>
      <div className="relative">
        <input
          type={showPassword ? 'text' : 'password'}
          value={password}
          onChange={e => { setPassword(e.target.value); setError('') }}
          placeholder="Digite sua senha"
          autoComplete="current-password"
          className="input-glow w-full bg-surface-800/60 border border-white/10 rounded-lg px-4 py-2.5 pr-10
                     text-white placeholder-gray-600 text-sm outline-none
                     focus:border-accent-500 focus:ring-1 focus:ring-accent-500/30
                     transition-all"
        />
        <button
          type="button"
          onClick={() => setShowPassword(!showPassword)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
          tabIndex={-1}
        >
          {showPassword ? (
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
              <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
              <line x1="1" y1="1" x2="23" y2="23" />
            </svg>
          ) : (
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          )}
        </button>
      </div>
    </div>
  )

  // ─── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-surface-950 flex flex-col items-center justify-center px-4 relative overflow-hidden">

      {/* Decorative background orbs */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="orb w-96 h-96 bg-accent-500 -top-48 -left-48" />
        <div className="orb w-80 h-80 bg-accent-400 top-1/2 -right-40" style={{ animationDelay: '4s' }} />
        <div className="orb w-64 h-64 bg-glow-gold bottom-0 left-1/3" style={{ animationDelay: '2s' }} />
      </div>

      {/* Logo */}
      <div className="flex items-center gap-4 mb-10 fade-in relative z-10">
        <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-accent-500 to-glow-gold flex items-center justify-center shadow-lg shadow-accent-500/30">
          <svg className="w-8 h-8 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <path d="M5 7h14M12 7v12M8 7V5M16 7V5" />
          </svg>
        </div>
        <div>
          <p className="text-gradient text-2xl font-bold leading-none">Traducao</p>
          <p className="text-gray-500 text-sm mt-1">Tradutor PHP · EN → PT-BR</p>
        </div>
      </div>

      {/* Card */}
      <div className="w-full max-w-sm glass rounded-2xl shadow-2xl overflow-hidden fade-in relative z-10">

        {/* ── Login (com auto-cadastro) ─────────────────────────── */}
        {mode === 'login' && (
          <form onSubmit={handleLogin} className="p-8 space-y-5">
            <div>
              <h1 className="text-xl font-semibold text-gradient">Entrar</h1>
              <p className="text-sm text-gray-500 mt-1">Acesse ou crie sua conta</p>
            </div>

            {EmailInput()}
            {PasswordInput()}
            {ErrorBox()}

            {/* Prompt de criacao de conta */}
            {showCreatePrompt && (
              <div className="space-y-3">
                <div className="glass-light border border-accent-500/20 rounded-lg px-3 py-2 flex items-center gap-2">
                  <svg className="w-4 h-4 text-accent-400 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" />
                    <line x1="12" y1="8" x2="12" y2="12" />
                    <line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                  <p className="text-accent-300 text-sm">Nenhuma conta encontrada com este e-mail. Deseja criar uma conta?</p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={handleConfirmRegister}
                    disabled={loading}
                    className="btn-glow flex-1 py-2.5 rounded-lg font-medium text-sm transition-all
                               text-white disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {loading ? (
                      <span className="flex items-center justify-center gap-2">{Spinner()} Criando...</span>
                    ) : (
                      <span className="flex items-center justify-center gap-2">
                        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                          <circle cx="8.5" cy="7" r="4" />
                          <line x1="20" y1="8" x2="20" y2="14" />
                          <line x1="23" y1="11" x2="17" y2="11" />
                        </svg>
                        Criar conta e entrar
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowCreatePrompt(false)}
                    className="px-4 py-2.5 rounded-lg text-sm text-gray-400 hover:text-white
                               border border-white/10 hover:border-white/20 transition-all"
                  >
                    Voltar
                  </button>
                </div>
              </div>
            )}

            {!showCreatePrompt && (
              <button
                type="submit"
                disabled={loading || !email || !password}
                className="btn-glow w-full py-2.5 rounded-lg font-medium text-sm transition-all
                           text-white disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <span className="flex items-center justify-center gap-2">{Spinner()} Entrando...</span>
                ) : (
                  <span className="flex items-center justify-center gap-2">
                    Entrar
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="5" y1="12" x2="19" y2="12" />
                      <polyline points="12 5 19 12 12 19" />
                    </svg>
                  </span>
                )}
              </button>
            )}

            <div className="text-center pt-1">
              <button
                type="button"
                onClick={() => switchMode('otp-request')}
                className="text-xs text-gray-600 hover:text-gray-400 transition-colors"
              >
                Esqueci a senha
              </button>
            </div>
          </form>
        )}

        {/* ── OTP: Solicitar codigo ───────────────────────────────── */}
        {mode === 'otp-request' && (
          <form onSubmit={handleRequestOtp} className="p-8 space-y-5">
            <div>
              <button
                type="button"
                onClick={() => switchMode('login')}
                className="text-gray-500 hover:text-gray-300 text-sm mb-4 flex items-center gap-1.5 transition-colors"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="19" y1="12" x2="5" y2="12" />
                  <polyline points="12 19 5 12 12 5" />
                </svg>
                Voltar
              </button>
              <h1 className="text-xl font-semibold text-gradient">Esqueci a senha</h1>
              <p className="text-sm text-gray-500 mt-1">Enviaremos um codigo para acessar sua conta</p>
            </div>

            {EmailInput()}
            {ErrorBox()}

            <button
              type="submit"
              disabled={loading || !email}
              className="btn-glow w-full py-2.5 rounded-lg font-medium text-sm transition-all
                         text-white disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">{Spinner()} Enviando...</span>
              ) : (
                <span className="flex items-center justify-center gap-2">
                  Enviar codigo
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="5" y1="12" x2="19" y2="12" />
                    <polyline points="12 5 19 12 12 19" />
                  </svg>
                </span>
              )}
            </button>
          </form>
        )}

        {/* ── OTP: Verificar codigo ───────────────────────────────── */}
        {mode === 'otp-verify' && (
          <form onSubmit={handleVerifyOtp} className="p-8 space-y-6">
            <div>
              <button
                type="button"
                onClick={() => { switchMode('otp-request'); setDigits(['','','','','','']) }}
                className="text-gray-500 hover:text-gray-300 text-sm mb-4 flex items-center gap-1.5 transition-colors"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="19" y1="12" x2="5" y2="12" />
                  <polyline points="12 19 5 12 12 5" />
                </svg>
                Voltar
              </button>
              <h1 className="text-xl font-semibold text-gradient">Verifique seu e-mail</h1>
              <p className="text-sm text-gray-400 mt-2 flex items-start gap-2">
                <svg className="w-4 h-4 text-accent-400 mt-0.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                  <polyline points="22,6 12,13 2,6" />
                </svg>
                <span>
                  Enviamos um codigo para{' '}
                  <span className="text-white font-medium">{email}</span>
                </span>
              </p>
            </div>

            {/* Inputs de 6 digitos */}
            <div className="flex gap-2 justify-center" onPaste={handlePaste}>
              {digits.map((d, i) => (
                <input
                  key={i}
                  ref={el => (digitRefs.current[i] = el)}
                  type="text"
                  inputMode="numeric"
                  maxLength={2}
                  value={d}
                  onChange={e => handleDigitChange(i, e.target.value)}
                  onKeyDown={e => handleDigitKeyDown(i, e)}
                  className={`
                    input-glow text-center text-xl font-bold rounded-lg border
                    bg-surface-800/60 text-white outline-none transition-all
                    ${d
                      ? 'border-accent-500 ring-1 ring-accent-500/30 shadow-sm shadow-accent-500/20'
                      : 'border-white/10 focus:border-accent-500 focus:ring-1 focus:ring-accent-500/30'
                    }
                    ${i === 2 ? 'mr-3' : ''}
                  `}
                  style={{ width: '2.75rem', height: '3.25rem' }}
                />
              ))}
            </div>

            {ErrorBox()}

            <button
              type="submit"
              disabled={loading || digits.join('').length < 6}
              className="btn-glow w-full py-2.5 rounded-lg font-medium text-sm transition-all
                         text-white disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">{Spinner()} Verificando...</span>
              ) : (
                <span className="flex items-center justify-center gap-2">
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                  Verificar
                </span>
              )}
            </button>

            {/* Reenviar */}
            <div className="text-center">
              <button
                type="button"
                onClick={handleResend}
                disabled={resendCountdown > 0 || loading}
                className="text-sm text-gray-500 hover:text-accent-400 transition-colors
                           disabled:cursor-default disabled:hover:text-gray-500 flex items-center gap-1.5 mx-auto"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="23 4 23 10 17 10" />
                  <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
                </svg>
                {resendCountdown > 0
                  ? `Reenviar codigo (${String(Math.floor(resendCountdown / 60)).padStart(2, '0')}:${String(resendCountdown % 60).padStart(2, '0')})`
                  : 'Reenviar codigo'}
              </button>
            </div>
          </form>
        )}

      </div>

    </div>
  )
}
