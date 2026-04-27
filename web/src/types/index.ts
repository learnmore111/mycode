// ---- Time ----
export interface TimeInfo {
  created: number
  updated?: number
  completed?: number
  compacting?: number
  archived?: number
}

// ---- Session ----
export interface SessionSummaryDiffItem {
  file?: string
  path?: string
  label?: string
}

export type SessionSummaryDiff = string | SessionSummaryDiffItem

export interface SessionSummary {
  additions?: number
  deletions?: number
  files?: number
  diffs?: SessionSummaryDiff[]
}

export interface Session {
  id: string
  slug: string
  projectID: string
  directory: string
  title: string
  version: string
  parentID?: string
  summary?: SessionSummary
  share?: string
  visible?: boolean
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
  turnNumber?: number | null
  snapshotRef?: string | null
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
  | 'reasoning_delta'
  | 'text_delta'
  | 'tool_start'
  | 'tool_running'
  | 'tool_done'
  | 'error'
  | 'compact'
  | 'guard_warn'
  | 'guard_stop'
  | 'context_snapshot'
  | 'done'

export interface SSEEvent {
  type: SSEEventType
  data: Record<string, unknown>
}

// ---- Context Snapshot ----
export interface ContextMessageInfo {
  index: number
  role: string
  content?: string
  cache_status: 'cached' | 'new'
  estimated_tokens: number
  is_compaction_summary?: boolean
  is_system_reminder?: boolean
  tool_call_id?: string
  tool_name?: string
  tool_calls?: Array<{ id: string; tool: string; args_preview: string }>
  content_truncated?: boolean
  full_length?: number
}

export interface ContextSnapshot {
  system: {
    content: string
    estimated_tokens: number
    cache_status: string
  }
  tools: {
    count: number
    names: string[]
    estimated_tokens: number
    cache_status: string
  }
  messages: ContextMessageInfo[]
  compaction: {
    has_boundary: boolean
    boundary_index: number | null
  }
  summary: {
    total_estimated_tokens: number
    cached_estimated_tokens: number
    new_estimated_tokens: number
    context_limit: number
    usage_percent: number
  }
  actual_usage?: {
    input_tokens: number
    output_tokens: number
    cache_read_tokens: number
    cache_write_tokens: number
    reasoning_tokens: number
    total_cost: number
  } | null
  iteration: number
  model: string
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

export interface PausedRun {
  sessionId: string
  lastUserText: string
  partialText?: string
  pausedAt: number
  model?: string
  agent?: string
}

export interface SessionCodeChange {
  id: string
  tool: string
  filePath: string | null
  time: number
  preview?: string
}

export type GitFileStatus = 'modified' | 'added' | 'deleted' | 'renamed' | 'untracked' | 'conflicted'

export interface GitChangedFile {
  path: string
  oldPath?: string | null
  indexStatus: string
  worktreeStatus: string
  status: GitFileStatus
  staged: boolean
  unstaged: boolean
}

export interface GitStatusSummary {
  changed: number
  staged: number
  unstaged: number
  untracked: number
  conflicted: number
  modified: number
  added: number
  deleted: number
  renamed: number
}

export interface GitStatus {
  available: boolean
  reason?: string | null
  worktree?: string
  branch?: string | null
  upstream?: string | null
  head?: string | null
  ahead: number
  behind: number
  clean: boolean
  summary: GitStatusSummary
  files: GitChangedFile[]
  lastUpdated: number
}

export interface GitDiffStats {
  additions: number
  deletions: number
  isBinary?: boolean
}

export interface GitDiffDetail {
  available: boolean
  path: string
  oldPath?: string | null
  status: GitFileStatus
  staged: boolean
  unstaged: boolean
  branch?: string | null
  head?: string | null
  diff: string
  tooLarge: boolean
  stats: GitDiffStats
  lastUpdated: number
}

// ---- Compaction Events ----
export interface CompactionEvent {
  id: string
  session_id: string
  iteration: number
  old_message_count: number
  old_message_tokens: number
  summary_length: number
  removed_turn_count: number
  old_messages: Array<{
    role: string
    content?: string
  }>
  summary: string
  time_created: number
}
