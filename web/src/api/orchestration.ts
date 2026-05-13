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
  disallowed_tools?: string[]
  tools?: string[]
  permission?: Array<{ permission: string; pattern: string; action: string }>
  model?: string
  temperature?: number
  top_p?: number
  isolation?: string
  max_turns?: number
  background?: boolean
  omit_claudemd?: boolean
}

export interface FlowStageSpawn {
  agent: string
  task: string
  vars?: Record<string, unknown>
  timeout_seconds?: number
}

export interface FlowStage {
  id: string
  description?: string
  parallel: boolean
  max_concurrency?: number
  runs_on?: string
  fan_out_from?: string
  depends_on: string[]
  inputs: string[]
  prompt?: string
  spawns: FlowStageSpawn[]
}

export interface FlowDetail {
  name: string
  description?: string
  mode: 'coordinator' | 'swarm' | 'hybrid'
  extends?: string
  model?: string
  max_depth?: number
  /** Preferred field: the initial task receiver for swarm/collaboration flows. */
  entry?: string
  /** @deprecated Alias of {@link entry}; kept for backward compatibility. */
  lead?: string
  /** The coordinating/facilitating agent in orchestration/collaboration flows. */
  coordinator?: string
  agents: FlowAgent[]
  stages: FlowStage[]
  vars: Record<string, string>
  backend?: Record<string, unknown>
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

export interface OrchestrationAgentDetail extends AgentCreateParams {
  source: string
}

export interface SwarmPeerSummary {
  name: string
  agent: string
  is_error: boolean
  turns: number
  tool_calls: number
  output?: string
  output_preview: string
  task?: string
  title?: string
  metadata?: Record<string, unknown>
  sent_count?: number
  received_count?: number
  recent_activity_direction?: 'sent' | 'received' | 'output' | 'none'
  recent_activity_partner?: string
  recent_activity_preview?: string
}

export interface SwarmMessageRoute {
  sender: string
  recipient: string
  count: number
}

export interface SwarmRecentMessage {
  seq: number
  kind: string
  sender: string
  recipient: string
  summary?: string
  content?: string
  preview: string
  timestamp?: number
}

export interface SwarmRunResult {
  kind: 'swarm' | 'hybrid'
  /** Preferred: the entry/supervisor agent name. */
  entry: string
  /** @deprecated Alias of {@link entry}. */
  lead: string
  peer_count: number
  terminated_reason: string
  message_count: number
  collaboration_count?: number
  active_peer_count?: number
  has_errors: boolean
  /** Preferred: full final output from the entry/supervisor agent. */
  entry_output?: string
  /** @deprecated Alias of {@link entry_output}. */
  lead_output?: string
  /** Preferred: preview of the entry/supervisor agent's final output. */
  entry_output_preview: string
  /** @deprecated Alias of {@link entry_output_preview}. */
  lead_output_preview: string
  message_routes?: SwarmMessageRoute[]
  transcript?: SwarmRecentMessage[]
  recent_messages?: SwarmRecentMessage[]
  peers: SwarmPeerSummary[]
}

export interface CoordinatorSpawnSummary {
  agent: string
  task: string
  title?: string
  is_error: boolean
  turns: number
  tool_calls: number
  output: string
  output_preview: string
  metadata?: Record<string, unknown>
}

export interface CoordinatorStageSummary {
  stage_id: string
  is_error: boolean
  spawn_count: number
  ok_count: number
  error_count: number
  coordinator_agent?: string
  coordinator_output?: string
  output?: string
  output_preview: string
  spawns?: CoordinatorSpawnSummary[]
}

export interface CoordinatorRunResult {
  kind: 'coordinator'
  stage_count: number
  stage_order: string[]
  total_spawn_count: number
  total_error_count: number
  has_errors: boolean
  last_stage_id?: string | null
  last_output?: string
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

export interface AgentLiveMessageEvent {
  role: string
  kind: string
  turn: number
  content_preview: string
  recipient?: string
  stage_id?: string | null
  spawn_index?: number | null
  time: number
}

export interface AgentLiveToolEvent {
  tool_name: string
  turn: number
  args_preview: string
  output_preview: string
  stage_id?: string | null
  spawn_index?: number | null
  time: number
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

export interface DeleteRunResponse {
  ok: boolean
  run_id: string
  deleted: boolean
}

// --- Agent CRUD types ---
export interface AgentCreateParams {
  name: string
  description?: string
  extends?: string
  role?: string
  mode?: string
  hidden?: boolean
  tools?: string[]
  prompt?: string
  model?: string
  temperature?: number
  top_p?: number
  color?: string
  variant?: string
  options?: Record<string, unknown>
  steps?: number
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
  extends?: string
  model?: string
  max_depth?: number
  /** Preferred field for the initial task receiver in swarm/collaboration flows. */
  entry?: string
  /** @deprecated Alias of {@link entry}; still accepted by the backend. */
  lead?: string
  /** The coordinating/facilitating agent in orchestration/collaboration flows. */
  coordinator?: string
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

export async function getOrchestrationAgent(name: string): Promise<OrchestrationAgentDetail> {
  return apiFetch<OrchestrationAgentDetail>(`/orchestration/agent/${encodeURIComponent(name)}`)
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

export async function deleteRun(runId: string): Promise<DeleteRunResponse> {
  return apiFetch<DeleteRunResponse>(`/orchestration/run/${encodeURIComponent(runId)}`, {
    method: 'DELETE',
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
