import type { Message } from '../types'

interface Props {
  message: Message
}

export default function MessageMeta({ message }: Props) {
  const { tokens, cost, agent, modelId } = message
  if (!tokens && !cost) return null

  const parts: string[] = []
  const cacheHitRate =
    tokens?.input && tokens?.cacheRead != null && tokens.input > 0
      ? (100 * tokens.cacheRead) / tokens.input
      : null

  if (modelId) parts.push(modelId.split('/').pop() ?? modelId)
  if (agent) parts.push(agent)
  if (tokens?.input) parts.push(`${tokens.input.toLocaleString()} in`)
  if (tokens?.output) parts.push(`${tokens.output.toLocaleString()} out`)
  if (tokens?.reasoning) parts.push(`${tokens.reasoning.toLocaleString()} reasoning`)
  if (tokens?.cacheRead) {
    const cacheText = cacheHitRate != null
      ? `${tokens.cacheRead.toLocaleString()} cache (${cacheHitRate.toFixed(1)}%)`
      : `${tokens.cacheRead.toLocaleString()} cache`
    parts.push(cacheText)
  }
  if (cost != null) parts.push(`$${cost.toFixed(4)}`)

  return (
    <div className="mt-2.5 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-2 text-xxs font-mono text-ink-muted">
      {parts.join(' · ')}
    </div>
  )
}
