import type { Message } from '../types'

interface Props {
  message: Message
}

export default function MessageMeta({ message }: Props) {
  const { tokens, cost, agent, modelId } = message
  if (!tokens && !cost) return null

  const parts: string[] = []
  if (modelId) parts.push(modelId.split('/').pop() ?? modelId)
  if (agent) parts.push(agent)
  if (tokens?.input) parts.push(`${tokens.input.toLocaleString()} in`)
  if (tokens?.output) parts.push(`${tokens.output.toLocaleString()} out`)
  if (tokens?.reasoning) parts.push(`${tokens.reasoning.toLocaleString()} reasoning`)
  if (tokens?.cacheRead) parts.push(`${tokens.cacheRead.toLocaleString()} cached`)
  if (cost != null) parts.push(`$${cost.toFixed(4)}`)

  return (
    <div className="mt-2.5 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-2 text-xxs font-mono text-ink-muted">
      {parts.join(' · ')}
    </div>
  )
}
