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

export interface SwarmPeerSummary {
  name: string
  agent: string
  is_error: boolean
  turns: number
  tool_calls: number
  output_preview: string
}

export interface SwarmRunResult {
  kind: 'swarm'
  lead: string
  peer_count: number
  terminated_reason: string
  message_count: number
  has_errors: boolean
  lead_output_preview: string
  peers: SwarmPeerSummary[]
}

export interface CoordinatorStageSummary {
  stage_id: string
  is_error: boolean
  spawn_count: number
  ok_count: number
  error_count: number
  coordinator_agent?: string
  output_preview: string
}

export interface CoordinatorRunResult {
  kind: 'coordinator'
  stage_count: number
  stage_order: string[]
  total_spawn_count: number
  total_error_count: number
  has_errors: boolean
  last_stage_id?: string | null
  last_output_preview: string
  stages: CoordinatorStageSummary[]
}

export type RunResult = SwarmRunResult | CoordinatorRunResult | Record<string, unknown> | null

export interface RunInfo {
  run_id: string
  flow: string
  mode: string
  status: string
}

export interface RunStatus {
  run_id: string
  flow: string
  mode: string
  status: string
  done: boolean
  cancelled: boolean
  cancel_requested: boolean
  started_at: number
  finished_at?: number | null
  has_result: boolean
  error?: string | null
}

export interface RunDetail extends RunStatus {
  directory?: string | null
  task_preview: string
  vars: Record<string, string>
  max_turns: number
  walltime_seconds: number
  result?: RunResult
}

export interface StartRunParams {
  flow: string
  task?: string
  vars?: Record<string, string>
  max_turns?: number
  walltime_seconds?: number
}

export interface CancelRunResponse {
  ok: boolean
  run_id: string
  status: string
  already_finished?: boolean
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

export async function startRun(params: StartRunParams): Promise<RunInfo> {
  return apiFetch<RunInfo>('/orchestration/run', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function listRuns(): Promise<RunStatus[]> {
  return apiFetch<RunStatus[]>('/orchestration/run')
}

export async function getRun(runId: string): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/orchestration/run/${encodeURIComponent(runId)}`)
}

export async function cancelRun(runId: string): Promise<CancelRunResponse> {
  return apiFetch<CancelRunResponse>(`/orchestration/run/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  })
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
