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
  if (tokens?.reasoning) parts.push(`${tokens.reasoning.toLocaleString()} reason`)
  if (tokens?.cacheRead) parts.push(`${tokens.cacheRead.toLocaleString()} cache`)
  if (cost != null) parts.push(`$${cost.toFixed(4)}`)

  return (
    <div className="mt-2 pt-2 border-t border-border text-xs text-text-muted">
      {parts.join(' · ')}
    </div>
  )
}
