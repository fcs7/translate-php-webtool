const BADGE_STYLES = {
  free: 'bg-gray-500/20 text-gray-400 border-gray-600/30',
  pro: 'bg-accent-500/20 text-accent-400 border-accent-500/30',
  business: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
}

export default function PlanBadge({ plan, onClick }) {
  if (!plan) return null

  const planId = plan.plan || 'free'
  const style = BADGE_STYLES[planId] || BADGE_STYLES.free
  const label = planId.charAt(0).toUpperCase() + planId.slice(1)

  const daysText = plan.days_remaining != null && planId !== 'free'
    ? ` (${plan.days_remaining}d)`
    : ''

  return (
    <button
      onClick={onClick}
      className={`text-xs px-2.5 py-1 rounded-full border transition-colors hover:opacity-80 ${style}`}
      title="Ver planos"
    >
      {label}{daysText}
    </button>
  )
}
