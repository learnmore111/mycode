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

export interface AddMcpServerParams {
  name: string
  type: 'local' | 'remote'
  command?: string[]
  url?: string
  environment?: Record<string, string>
  headers?: Record<string, string>
}

export async function addMcpServer(params: AddMcpServerParams): Promise<void> {
  await apiFetch('/mcp', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function removeMcpServer(name: string): Promise<void> {
  await apiFetch(`/mcp/${encodeURIComponent(name)}`, { method: 'DELETE' })
}
