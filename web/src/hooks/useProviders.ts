import { useState, useEffect } from 'react'
import type { ProviderInfo, AgentInfo } from '../types'
import { listProviders, listAgents } from '../api/providers'

export function useProviders() {
  const [providers, setProviders] = useState<Record<string, ProviderInfo>>({})
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [selectedModel, setSelectedModel] = useState<string | undefined>()
  const [selectedAgent, setSelectedAgent] = useState<string | undefined>()

  useEffect(() => {
    listProviders().then(setProviders).catch(console.error)
    listAgents().then(setAgents).catch(console.error)
  }, [])

  // Flatten models for selection
  const models: { id: string; name: string; provider: string }[] = []
  for (const [pid, p] of Object.entries(providers)) {
    for (const [mid, m] of Object.entries(p.models)) {
      models.push({ id: `${pid}/${mid}`, name: `${p.name} / ${m.name}`, provider: pid })
    }
  }

  const visibleAgents = agents.filter((a) => !a.hidden)

  return {
    providers,
    models,
    agents: visibleAgents,
    selectedModel,
    setSelectedModel,
    selectedAgent,
    setSelectedAgent,
  }
}
