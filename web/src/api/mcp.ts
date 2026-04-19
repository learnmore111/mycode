import { apiFetch } from './client'

export interface McpStatus {
  servers: Record<string, string>
  tools: string[]
}

export async function getMcpStatus(): Promise<McpStatus> {
  return apiFetch<McpStatus>('/mcp')
}

export async function connectMcp(name: string): Promise<void> {
  await apiFetch(`/mcp/${encodeURIComponent(name)}/connect`, { method: 'POST' })
}

export async function disconnectMcp(name: string): Promise<void> {
  await apiFetch(`/mcp/${encodeURIComponent(name)}/disconnect`, { method: 'POST' })
}
