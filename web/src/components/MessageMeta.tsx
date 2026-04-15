import type { Message } from '../types'

interface Props {
  message: Message
}

export default function MessageMeta({ message }: Props) {
  const { tokens, cost, agent, modelId } = message
  if (!tokens && !cost) return null

  const parts: string[] = []
  if (tokens?.input) parts.push(`${tokens.input} in`)
  if (tokens?.output) parts.push(`${tokens.output} out`)
  if (tokens?.reasoning) parts.push(`${tokens.reasoning} reason`)
  if (tokens?.cacheRead) parts.push(`${tokens.cacheRead} cache`)
  if (cost != null) parts.push(`$${cost.toFixed(4)}`)
  if (agent) parts.push(agent)
  if (modelId) parts.push(modelId.split('/').pop() ?? modelId)

  return (
    <div className="mt-1 px-1 text-[10px] text-gray-500 flex flex-wrap gap-x-2">
      {parts.map((p, i) => (
        <span key={i}>{p}</span>
      ))}
    </div>
  )
}
