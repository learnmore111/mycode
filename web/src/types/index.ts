// ---- Time ----
export interface TimeInfo {
  created: number
  updated?: number
  completed?: number
  compacting?: number
  archived?: number
}

// ---- Session ----
export interface Session {
  id: string
  slug: string
  projectID: string
  directory: string
  title: string
  version: string
  parentID?: string
  summary?: string
  share?: string
  time: TimeInfo
}

// ---- Part ----
export type PartType = 'text' | 'tool' | 'reasoning' | 'file' | 'step'

export interface ToolState {
  status?: string
  input?: unknown
  output?: string
  error?: string
  [key: string]: unknown
}

export interface Part {
  id: string
  type: PartType
  content?: string
  tool?: string
  toolCallId?: string
  state?: ToolState
  time: { created: number; completed?: number }
}

// ---- Message ----
export interface TokenInfo {
  input?: number
  output?: number
  reasoning?: number
  cacheRead?: number
  cacheWrite?: number
}

export interface Message {
  id: string
  sessionId: string
  role: 'user' | 'assistant'
  parentId?: string
  modelId?: string
  providerId?: string
  agent?: string
  tokens?: TokenInfo
  cost?: number
  error?: unknown
  parts: Part[]
  time: { created: number; completed?: number }
}

// ---- Provider / Model ----
export interface ModelInfo {
  id: string
  name: string
}

export interface ProviderInfo {
  id: string
  name: string
  source: string
  models: Record<string, ModelInfo>
}

// ---- Agent ----
export interface AgentInfo {
  name: string
  description: string
  mode: string
  hidden: boolean
}

// ---- Permission ----
export interface PermissionRequest {
  id: string
  session_id: string
  permission: string
  patterns: string[]
  metadata: Record<string, unknown>
}

// ---- SSE Events ----
export type SSEEventType =
  | 'started'
  | 'text_delta'
  | 'tool_start'
  | 'tool_running'
  | 'tool_done'
  | 'error'
  | 'compact'
  | 'guard_warn'
  | 'guard_stop'
  | 'done'

export interface SSEEvent {
  type: SSEEventType
  data: Record<string, unknown>
}

// ---- Streaming state ----
export interface StreamingPart {
  id: string
  type: PartType
  content: string
  tool?: string
  toolCallId?: string
  state?: ToolState
}

export interface StreamingState {
  active: boolean
  text: string
  parts: StreamingPart[]
  sessionId?: string
}
