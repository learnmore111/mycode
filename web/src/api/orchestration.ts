import { apiFetch } from './client'

// --- Types ---

export interface FlowInfo {
  name: string
  source: string
  path: string
}

export interface FlowAgent {
  name: string
  extends?: string
  prompt?: string
  role?: string
  description?: string
  tools?: string[]
  model?: string
  temperature?: number
  max_turns?: number
}

export interface FlowStageSpawn {
  agent: string
  task: string
}

export interface FlowStage {
  id: string
  parallel: boolean
  runs_on?: string
  fan_out_from?: string
  depends_on: string[]
  inputs: string[]
  spawns: FlowStageSpawn[]
}

export interface FlowDetail {
  name: string
  mode: 'coordinator' | 'swarm' | 'hybrid'
  lead?: string
  agents: FlowAgent[]
  stages: FlowStage[]
  vars: Record<string, string>
}

export interface OrchestrationAgent {
  name: string
  source: string
  description?: string
  extends?: string
  tools?: string
  mode?: string
  error?: string
}

export interface RunInfo {
  run_id: string
  flow: string
  mode: string
}

export interface RunStatus {
  run_id: string
  done: boolean
  cancelled: boolean
}

// --- Agent CRUD types ---
export interface AgentCreateParams {
  name: string
  description?: string
  extends?: string
  role?: string
  mode?: string
  tools?: string[]
  prompt?: string
  model?: string
  temperature?: number
  top_p?: number
  max_turns?: number
  isolation?: string
  omit_claudemd?: boolean
  permission?: Array<{ permission: string; pattern: string; action: string }>
  scope?: string
}

// --- Flow CRUD types ---
export interface FlowCreateParams {
  name: string
  description?: string
  mode?: string
  lead?: string
  agents?: Array<Record<string, unknown>>
  stages?: Array<Record<string, unknown>>
  vars?: Record<string, string>
  backend?: Record<string, string>
  scope?: string
}

// --- Read API ---

export async function listFlows(): Promise<FlowInfo[]> {
  return apiFetch<FlowInfo[]>('/orchestration/flow')
}

export async function getFlow(name: string): Promise<FlowDetail> {
  return apiFetch<FlowDetail>(`/orchestration/flow/${encodeURIComponent(name)}`)
}

export async function listOrchestrationAgents(): Promise<OrchestrationAgent[]> {
  return apiFetch<OrchestrationAgent[]>('/orchestration/agent')
}

export async function startRun(params: {
  flow: string
  task?: string
  vars?: Record<string, string>
  max_turns?: number
  walltime_seconds?: number
}): Promise<RunInfo> {
  return apiFetch<RunInfo>('/orchestration/run', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function listRuns(): Promise<RunStatus[]> {
  return apiFetch<RunStatus[]>('/orchestration/run')
}

// --- Agent CRUD ---

export async function createAgent(params: AgentCreateParams): Promise<void> {
  await apiFetch('/orchestration/agent', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function updateAgent(name: string, params: AgentCreateParams): Promise<void> {
  await apiFetch(`/orchestration/agent/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(params),
  })
}

export async function deleteAgent(name: string, scope: string = 'project'): Promise<void> {
  await apiFetch(`/orchestration/agent/${encodeURIComponent(name)}?scope=${scope}`, {
    method: 'DELETE',
  })
}

// --- Flow CRUD ---

export async function createFlow(params: FlowCreateParams): Promise<void> {
  await apiFetch('/orchestration/flow', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function updateFlow(name: string, params: FlowCreateParams): Promise<void> {
  await apiFetch(`/orchestration/flow/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(params),
  })
}

export async function deleteFlow(name: string, scope: string = 'project'): Promise<void> {
  await apiFetch(`/orchestration/flow/${encodeURIComponent(name)}?scope=${scope}`, {
    method: 'DELETE',
  })
}
