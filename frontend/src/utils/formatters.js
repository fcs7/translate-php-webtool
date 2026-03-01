/**
 * Formata data relativa (ex: "3h atras", "2d atras").
 * @param {string} dateStr - ISO date string
 * @returns {string}
 */
export function timeAgo(dateStr) {
  const d = new Date(dateStr)
  const now = new Date()
  const diff = (now - d) / 1000
  if (diff < 60) return 'agora'
  if (diff < 3600) return `${Math.floor(diff / 60)}min atras`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h atras`
  return `${Math.floor(diff / 86400)}d atras`
}

/**
 * Formata tempo restante ate expirar (ex: "2d 5h", "3h").
 * @param {string} dateStr - ISO date string
 * @returns {string}
 */
export function expiresIn(dateStr) {
  const d = new Date(dateStr)
  const now = new Date()
  const diff = (d - now) / 1000
  if (diff <= 0) return 'Expirado'
  const days = Math.floor(diff / 86400)
  const hours = Math.floor((diff % 86400) / 3600)
  if (days > 0) return `${days}d ${hours}h`
  return `${hours}h`
}

/** Mapeamento de action codes para labels em portugues. */
export const ACTION_LABELS = {
  login: 'Login',
  login_otp: 'Login (codigo)',
  register: 'Cadastro',
  logout: 'Logout',
  upload: 'Upload',
  download: 'Download',
  cancel_job: 'Cancelou job',
  delete_job: 'Deletou job',
  delete_history: 'Limpou arquivos',
  delete_history_bulk: 'Limpeza em massa',
  admin_delete_user: 'Deletou usuario',
  admin_reconcile: 'Reconciliou storage',
  checkout: 'Checkout Pix',
  plan_activated: 'Plano ativado',
  plan_expired: 'Plano expirado',
  plan_refunded: 'Estorno',
}
