import { apiFetch } from './client'
import type { ProviderInfo, AgentInfo } from '../types'

export async function listProviders(): Promise<Record<string, ProviderInfo>> {
  return apiFetch<Record<string, ProviderInfo>>('/provider')
}

export async function listAgents(): Promise<AgentInfo[]> {
  return apiFetch<AgentInfo[]>('/agent')
}
