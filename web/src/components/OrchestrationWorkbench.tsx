import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeft,
  Bot,
  Plus,
  Trash2,
  Save,
  RefreshCcw,
  ChevronDown,
  ChevronRight,
  Layers,
  Activity,
  Play,
  X,
  AlertCircle,
  Check,
  GitBranch,
  Network,
  Users,
  Workflow,
  Zap,
  Settings2,
  GripVertical,
  Edit3,
  Shield,
  Cpu,
  Hash,
  Circle,
  LayoutGrid,
  List,
} from 'lucide-react'
import {
  listFlows, getFlow, listOrchestrationAgents,
  getOrchestrationAgent,
  createAgent, updateAgent, deleteAgent,
  createFlow, updateFlow, deleteFlow,
  startRun, listRuns, getRun, cancelRun, deleteRun,
} from '../api/orchestration'
import type {
  AgentLiveMessageEvent,
  AgentLiveToolEvent,
  CoordinatorRunResult,
  FlowInfo,
  FlowDetail,
  OrchestrationAgent,
  OrchestrationAgentDetail,
  AgentCreateParams,
  FlowCreateParams,
  RunDetail,
  RunStatus,
  StartRunParams,
  SwarmPeerSummary,
  SwarmRunResult,
} from '../api/orchestration'
import FlowDAGEditor, { type StageData } from './FlowDAGEditor'

interface Props { onBack: () => void }
type Tab = 'agents' | 'flows' | 'runs'
type RunDetailTab = 'overview' | 'board' | 'transcripts' | 'result' | 'events'
type AgentTranscriptItem =
  | ({ type: 'message' } & AgentLiveMessageEvent)
  | ({ type: 'tool' } & AgentLiveToolEvent)
type AgentBoardStatus = 'waiting' | 'active' | 'collaborating' | 'review' | 'done' | 'error'
type AgentBoardCard = {
  agent: string
  role?: string
  description?: string
  status: AgentBoardStatus
  label: string
  eventCount: number
  turnCount: number
  toolCount: number
  sentCount: number
  receivedCount: number
  stage?: string
  lastPreview: string
  updatedAt?: number
  isEntry?: boolean
}

// ── Shared styles ──
const inputBase = 'w-full bg-[#F4F3F0] rounded-lg px-3 py-2 text-[13px] text-[#1A1A1A] placeholder:text-[#ABABAB] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/40 focus:ring-2 focus:ring-[#3D3BF3]/8 transition-all'
const inputMono = `${inputBase} font-[JetBrains_Mono,ui-monospace,monospace]`
const labelStyle = 'text-[10px] font-semibold text-[#8A8A85] uppercase tracking-[0.08em] mb-1.5 block'
const cardStyle = 'rounded-2xl border border-[#E5E4E0] bg-white shadow-[0_1px_3px_rgba(0,0,0,0.04)]'
const pillActive = 'bg-[#3D3BF3]/8 text-[#3D3BF3] border-[#3D3BF3]/15'
const pillInactive = 'bg-[#F4F3F0] text-[#8A8A85] border-transparent hover:border-[#E5E4E0] hover:text-[#5C5C5C]'
const btnPrimary = 'flex items-center gap-1.5 px-4 py-2 rounded-xl bg-[#3D3BF3] text-white text-[12px] font-semibold hover:bg-[#3230D8] disabled:opacity-35 disabled:cursor-not-allowed transition-all shadow-[0_1px_3px_rgba(61,59,243,0.3)]'
const btnGhost = 'px-3 py-1.5 rounded-lg text-[12px] font-medium text-[#8A8A85] hover:bg-[#F4F3F0] hover:text-[#5C5C5C] transition-colors'
const sectionTitle = 'flex items-center gap-2 text-[11px] font-bold text-[#0F0F0F] uppercase tracking-[0.06em]'

type FlowModeKey = 'coordinator' | 'hybrid' | 'swarm'
const FLOW_MODE_CONFIG: Record<FlowModeKey, {
  label: string
  shortLabel: string
  description: string
  icon: React.ElementType
  badge: string
  darkBadge: string
}> = {
  coordinator: {
    label: '工作流式',
    shortLabel: '工作流',
    description: '阶段化 DAG，由协调者按流程分派并汇总。',
    icon: Workflow,
    badge: 'bg-[#3D3BF3]/8 text-[#3D3BF3]',
    darkBadge: 'bg-white/10 text-white',
  },
  hybrid: {
    label: '主管协作式',
    shortLabel: '主管协作',
    description: '主管 Agent 接收任务、组织专家消息协作并综合输出。',
    icon: Users,
    badge: 'bg-[#16a34a]/8 text-[#16a34a]',
    darkBadge: 'bg-[#16a34a]/15 text-[#8AE6A6]',
  },
  swarm: {
    label: 'Swarm 式',
    shortLabel: 'Swarm',
    description: '去中心化 peer-to-peer，成员通过消息自由推进。',
    icon: Network,
    badge: 'bg-[#d97706]/8 text-[#d97706]',
    darkBadge: 'bg-[#d97706]/15 text-[#F4B467]',
  },
}

function getFlowModeConfig(mode?: string) {
  return FLOW_MODE_CONFIG[(mode as FlowModeKey) in FLOW_MODE_CONFIG ? mode as FlowModeKey : 'coordinator']
}

function usesStages(mode: string): boolean {
  return mode === 'coordinator'
}

function usesEntry(mode: string): boolean {
  return mode === 'swarm' || mode === 'hybrid'
}

function usesCoordinator(mode: string): boolean {
  return mode === 'coordinator' || mode === 'hybrid'
}

const STATIC_TOOLS = [
  'read', 'write', 'edit', 'grep', 'glob', 'listdir', 'bash',
  'webfetch', 'websearch', 'task', 'apply_patch',
]
const RUNTIME_ONLY_TOOLS = ['send_message']

// ── Custom Select ──
function Select({ value, onChange, options, placeholder, className = '', mono = false }: {
  value: string; onChange: (v: string) => void
  options: Array<{ value: string; label: string }>
  placeholder?: string; className?: string; mono?: boolean
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  const selected = options.find((o) => o.value === value)
  const font = mono ? 'font-[JetBrains_Mono,ui-monospace,monospace]' : ''

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button type="button" onClick={() => setOpen(!open)}
        className={`w-full flex items-center justify-between gap-2 bg-[#F4F3F0] rounded-lg px-3 py-2 text-[13px] text-left outline-none border transition-all ${
          open ? 'border-[#3D3BF3]/40 ring-2 ring-[#3D3BF3]/8' : 'border-[#E5E4E0] hover:border-[#D4D3CF]'
        } ${font}`}>
        <span className={selected ? 'text-[#1A1A1A]' : 'text-[#ABABAB]'}>
          {selected ? selected.label : placeholder || '选择...'}
        </span>
        <ChevronDown size={12} className={`text-[#ABABAB] transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-white rounded-xl border border-[#E5E4E0] shadow-xl overflow-hidden animate-slide-up">
          <div className="max-h-52 overflow-y-auto py-1">
            {placeholder && (
              <button type="button" onClick={() => { onChange(''); setOpen(false) }}
                className={`w-full text-left px-3 py-2 text-[12px] transition-colors ${!value ? 'text-[#3D3BF3] bg-[#3D3BF3]/5 font-medium' : 'text-[#ABABAB] hover:bg-[#FAFAF8]'}`}>
                {placeholder}
              </button>
            )}
            {options.map((opt) => (
              <button key={opt.value} type="button" onClick={() => { onChange(opt.value); setOpen(false) }}
                className={`w-full text-left px-3 py-2 text-[12px] transition-colors flex items-center justify-between ${font} ${
                  opt.value === value ? 'text-[#3D3BF3] bg-[#3D3BF3]/5 font-medium' : 'text-[#5C5C5C] hover:bg-[#FAFAF8]'
                }`}>
                <span>{opt.label}</span>
                {opt.value === value && <Check size={12} className="text-[#3D3BF3]" />}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Toast ──
function Toast({ t, onClose }: { t: { type: 'success' | 'error'; msg: string } | null; onClose: () => void }) {
  if (!t) return null
  return (
    <div className="fixed top-5 right-5 z-[200] animate-slide-up">
      <div className={`flex items-center gap-2.5 pl-4 pr-3 py-3 rounded-2xl shadow-xl text-[13px] font-medium backdrop-blur-md ${
        t.type === 'success' ? 'bg-[#16a34a] text-white' : 'bg-[#dc2626] text-white'
      }`}>
        {t.type === 'success' ? <Check size={15} strokeWidth={2.5} /> : <AlertCircle size={15} />}
        <span>{t.msg}</span>
        <button onClick={onClose} className="ml-1 p-1 rounded-lg hover:bg-white/20 transition-colors"><X size={13} /></button>
      </div>
    </div>
  )
}

// ── Source badge ──
function SourceBadge({ source }: { source: string }) {
  const cfg: Record<string, string> = {
    builtin: 'bg-[#3D3BF3]/8 text-[#3D3BF3]',
    project: 'bg-[#16a34a]/8 text-[#16a34a]',
    global: 'bg-[#d97706]/8 text-[#d97706]',
    config: 'bg-[#F4F3F0] text-[#8A8A85]',
  }
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-md text-[9px] font-bold uppercase tracking-wider ${cfg[source] || cfg.config}`}>
      {source}
    </span>
  )
}

// ══════════════════════════════════════════════════════════════
// AGENT EDITOR
// ══════════════════════════════════════════════════════════════
function AgentEditor({ initial, allAgents, onSave, onCancel, saving }: {
  initial?: AgentCreateParams; allAgents: OrchestrationAgent[]
  onSave: (p: AgentCreateParams, isNew: boolean) => void; onCancel: () => void; saving: boolean
}) {
  const isNew = !initial
  const [name, setName] = useState(initial?.name ?? '')
  const [desc, setDesc] = useState(initial?.description ?? '')
  const [ext, setExt] = useState(initial?.extends ?? '')
  const [role, setRole] = useState(initial?.role ?? '')
  const [mode, setMode] = useState(initial?.mode ?? 'all')
  const [hidden, setHidden] = useState(initial?.hidden ?? false)
  const [tools, setTools] = useState<string[]>(initial?.tools ?? [])
  const [prompt, setPrompt] = useState(initial?.prompt ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [temp, setTemp] = useState<string>(initial?.temperature != null ? String(initial.temperature) : '')
  const [topP, setTopP] = useState<string>(initial?.top_p != null ? String(initial.top_p) : '')
  const [color, setColor] = useState(initial?.color ?? '')
  const [variant, setVariant] = useState(initial?.variant ?? '')
  const [steps, setSteps] = useState<string>(initial?.steps != null ? String(initial.steps) : '')
  const [maxTurns, setMaxTurns] = useState<string>(initial?.max_turns != null ? String(initial.max_turns) : '')
  const [isolation, setIsolation] = useState(initial?.isolation ?? 'none')
  const [omitClaude, setOmitClaude] = useState(initial?.omit_claudemd ?? false)
  const [scope, setScope] = useState(initial?.scope ?? 'project')

  const toggleTool = (t: string) => setTools((p) => p.includes(t) ? p.filter((x) => x !== t) : [...p, t])

  const handleSave = () => {
    onSave({
      name: name.trim(), description: desc || undefined, extends: ext || undefined,
      role: role || undefined, mode, hidden, tools: tools.length > 0 ? tools : undefined,
      prompt: prompt || undefined, model: model || undefined,
      temperature: temp ? parseFloat(temp) : undefined,
      top_p: topP ? parseFloat(topP) : undefined,
      color: color || undefined,
      variant: variant || undefined,
      steps: steps ? parseInt(steps) : undefined,
      max_turns: maxTurns ? parseInt(maxTurns) : undefined,
      isolation, omit_claudemd: omitClaude || undefined, scope,
    }, isNew)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header bar */}
      <div className="flex items-center justify-between px-6 py-3.5 border-b border-[#E5E4E0] bg-[#0F0F0F]">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[#3D3BF3] flex items-center justify-center">
            <Bot size={14} className="text-white" />
          </div>
          <div>
            <h3 className="text-[13px] font-bold text-white">{isNew ? '创建 Agent' : `编辑 · ${name}`}</h3>
            <p className="text-[10px] text-[#8A8A85] mt-0.5">配置自定义 AI 代理的行为和权限</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={onCancel} className={btnGhost + ' !text-[#8A8A85] hover:!bg-white/10 hover:!text-white'}>取消</button>
          <button onClick={handleSave} disabled={saving || !name.trim()} className={btnPrimary}>
            {saving ? <RefreshCcw size={12} className="animate-spin" /> : <Save size={12} />}
            <span>保存</span>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
          {/* Identity section */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-4'}><Hash size={12} className="text-[#3D3BF3]" />基本信息</div>
            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <label className={labelStyle}>名称 *</label>
                <input value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} placeholder="my-agent" className={inputMono + ' disabled:opacity-40'} />
              </div>
              <div>
                <label className={labelStyle}>作用域</label>
                <div className="flex gap-2">
                  {(['project', 'global'] as const).map((s) => (
                    <button key={s} onClick={() => setScope(s)} className={`flex-1 px-3 py-2 rounded-lg text-[12px] font-semibold transition-all border ${scope === s ? pillActive : pillInactive}`}>
                      {s === 'project' ? '项目级' : '全局'}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div>
              <label className={labelStyle}>描述</label>
              <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Agent 的用途描述" className={inputBase} />
            </div>
          </div>

          {/* Inheritance & role */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-4'}><GitBranch size={12} className="text-[#3D3BF3]" />继承与角色</div>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className={labelStyle}>继承自</label>
                <Select value={ext} onChange={setExt} placeholder="无"
                  options={allAgents.filter((a) => a.name !== name).map((a) => ({ value: a.name, label: a.name }))} />
              </div>
              <div>
                <label className={labelStyle}>角色</label>
                <Select value={role} onChange={setRole} placeholder="无"
                  options={['coordinator', 'worker', 'teammate', 'entry', 'lead', 'fork'].map((r) => ({ value: r, label: r }))} mono />
              </div>
              <div>
                <label className={labelStyle}>模式</label>
                <Select value={mode} onChange={setMode}
                  options={['all', 'primary', 'subagent'].map((m) => ({ value: m, label: m }))} mono />
              </div>
            </div>
            <div className="mt-4 flex items-center gap-6 pt-4 border-t border-[#F4F3F0]">
              <label className="flex items-center gap-2.5 text-[12px] text-[#5C5C5C] cursor-pointer select-none">
                <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)}
                  className="w-4 h-4 rounded border-[#D4D3CF] text-[#3D3BF3] focus:ring-[#3D3BF3]/20" />
                在列表中隐藏
              </label>
            </div>
          </div>

          {/* Tools */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-3'}><Shield size={12} className="text-[#3D3BF3]" />工具白名单</div>
            <div className="flex flex-wrap gap-1.5">
              {STATIC_TOOLS.map((t) => (
                <button key={t} onClick={() => toggleTool(t)}
                  className={`px-3 py-1.5 rounded-lg text-[11px] font-[JetBrains_Mono,monospace] font-semibold transition-all border ${
                    tools.includes(t) ? pillActive : pillInactive
                  }`}>{t}</button>
              ))}
            </div>
            <p className="text-[10px] text-[#ABABAB] mt-2">不选则使用默认工具集。选中的工具构成 Agent 的能力边界。</p>
            <div className="mt-3 rounded-xl border border-dashed border-[#3D3BF3]/18 bg-[#3D3BF3]/[0.04] px-3 py-2 text-[10px] leading-relaxed text-[#6B6A78]">
              <span className="font-semibold text-[#3D3BF3]">运行时专属工具：</span>{' '}
              {RUNTIME_ONLY_TOOLS.join(', ')} 会在 `swarm` 运行中按上下文自动注入，不需要在静态白名单里手动配置。
            </div>
          </div>

          {/* Prompt */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-3'}><Edit3 size={12} className="text-[#3D3BF3]" />系统提示词</div>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={10}
              placeholder="你是一个专门的 AI agent，擅长..."
              className={inputMono + ' !text-[12px] resize-y min-h-[120px] leading-relaxed'} />
          </div>

          {/* Model & params */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-4'}><Cpu size={12} className="text-[#3D3BF3]" />模型与参数</div>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <label className={labelStyle}>模型</label>
                <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="provider/model-id" className={inputMono} />
              </div>
              <div>
                <label className={labelStyle}>Temperature</label>
                <input value={temp} onChange={(e) => setTemp(e.target.value)} type="number" step="0.1" min="0" max="2" placeholder="0.7" className={inputMono} />
              </div>
              <div>
                <label className={labelStyle}>Top P</label>
                <input value={topP} onChange={(e) => setTopP(e.target.value)} type="number" step="0.1" min="0" max="1" placeholder="1.0" className={inputMono} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <label className={labelStyle}>颜色</label>
                <input value={color} onChange={(e) => setColor(e.target.value)} placeholder="yellow" className={inputMono} />
              </div>
              <div>
                <label className={labelStyle}>Variant</label>
                <input value={variant} onChange={(e) => setVariant(e.target.value)} placeholder="provider preset" className={inputMono} />
              </div>
              <div>
                <label className={labelStyle}>Steps</label>
                <input value={steps} onChange={(e) => setSteps(e.target.value)} type="number" min="1" placeholder="8" className={inputMono} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <label className={labelStyle}>最大轮数</label>
                <input value={maxTurns} onChange={(e) => setMaxTurns(e.target.value)} type="number" min="1" max="50" placeholder="8" className={inputMono} />
              </div>
            </div>
            <div className="flex items-center gap-6 pt-2 border-t border-[#F4F3F0]">
              <label className="flex items-center gap-2.5 text-[12px] text-[#5C5C5C] cursor-pointer select-none">
                <input type="checkbox" checked={omitClaude} onChange={(e) => setOmitClaude(e.target.checked)}
                  className="w-4 h-4 rounded border-[#D4D3CF] text-[#3D3BF3] focus:ring-[#3D3BF3]/20" />
                跳过项目上下文
              </label>
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-[#8A8A85] font-medium">隔离:</span>
                {(['none', 'worktree', 'container'] as const).map((v) => (
                  <button key={v} onClick={() => setIsolation(v)}
                    className={`px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all border ${isolation === v ? pillActive : pillInactive}`}>{v}</button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

type FlowDraftAgent = { name: string; extends: string; role: string; prompt: string }

function getManagedFlowRole(mode: string, agentName: string, coordinator: string, entry: string): string | null {
  const trimmedName = agentName.trim()
  if (!trimmedName) return null
  if (usesCoordinator(mode) && coordinator.trim() === trimmedName) return 'coordinator'
  if (usesEntry(mode) && entry.trim() === trimmedName) return 'entry'
  return null
}

function normalizeFlowAgentsForMode(
  agents: FlowDraftAgent[],
  mode: string,
  coordinator: string,
  entry: string,
): FlowDraftAgent[] {
  return agents.map((agent) => {
    const managedRole = getManagedFlowRole(mode, agent.name, coordinator, entry)
    if (managedRole) {
      return agent.role === managedRole ? agent : { ...agent, role: managedRole }
    }

    if (usesCoordinator(mode)) {
      return agent.role === 'worker' ? agent : { ...agent, role: '' }
    }

  if (mode === 'swarm') {
    return agent.role === 'teammate' ? agent : { ...agent, role: '' }
  }

    return agent
  })
}

function getRoleOptionsForFlowAgent(
  mode: string,
  agentName: string,
  coordinator: string,
  entry: string,
): { placeholder: string; options: Array<{ value: string; label: string }> } {
  const managedRole = getManagedFlowRole(mode, agentName, coordinator, entry)
  if (managedRole === 'coordinator') {
    return { placeholder: '由上方协调 Agent 指定', options: [{ value: 'coordinator', label: 'coordinator' }] }
  }
  if (managedRole === 'entry') {
    return { placeholder: '由上方起始 Agent 指定', options: [{ value: 'entry', label: 'entry' }] }
  }
  if (usesCoordinator(mode)) {
    return { placeholder: '无角色', options: [{ value: 'worker', label: 'worker' }] }
  }
  return { placeholder: '无角色', options: [{ value: 'teammate', label: 'teammate' }] }
}

// ══════════════════════════════════════════════════════════════
// FLOW EDITOR
// ══════════════════════════════════════════════════════════════
function FlowEditor({ initial, allAgents, onSave, onCancel, saving }: {
  initial?: FlowDetail & { scope?: string }; allAgents: OrchestrationAgent[]
  onSave: (p: FlowCreateParams, isNew: boolean) => void; onCancel: () => void; saving: boolean
}) {
  const isNew = !initial
  const [name, setName] = useState(initial?.name ?? '')
  const [desc, setDesc] = useState(initial?.description ?? '')
  const [mode, setMode] = useState<string>(initial?.mode ?? 'coordinator')
  const [flowExtends, setFlowExtends] = useState(initial?.extends ?? '')
  const [flowModel, setFlowModel] = useState(initial?.model ?? '')
  const [maxDepth, setMaxDepth] = useState<string>(initial?.max_depth != null ? String(initial.max_depth) : '')
  // Initial task receiver. Swarm-style runs use it directly; collaboration
  // mode keeps it as the visible starting point while still using stages.
  const [entry, setEntry] = useState(initial?.entry ?? initial?.lead ?? '')
  // Coordinator / facilitator agent for orchestration and collaboration modes.
  // When empty the backend can derive it from the single role=coordinator agent.
  const [coordinator, setCoordinator] = useState(initial?.coordinator ?? '')
  const [scope, setScope] = useState(initial?.scope ?? 'project')
  const [vars, setVars] = useState<Array<{ key: string; value: string }>>(
    initial?.vars ? Object.entries(initial.vars).map(([key, value]) => ({ key, value })) : []
  )
  const [agents, setAgents] = useState<Array<{ name: string; extends: string; role: string; prompt: string }>>(
    initial?.agents.map((a) => ({ name: a.name, extends: a.extends ?? '', role: a.role ?? '', prompt: a.prompt ?? '' })) ?? []
  )
  const [stages, setStages] = useState<Array<{
    id: string; parallel: boolean; runs_on: string; depends_on: string;
    inputs: string; spawns: Array<{ agent: string; task: string }>
  }>>(
    initial?.stages.map((s) => ({
      id: s.id, parallel: s.parallel, runs_on: s.runs_on ?? '',
      depends_on: s.depends_on.join(', '), inputs: s.inputs.join(', '),
      spawns: s.spawns.map((sp) => ({ agent: sp.agent, task: sp.task })),
    })) ?? []
  )
  // Toggle between form-based and visual DAG editor
  const [dagViewMode, setDagViewMode] = useState(false)

  const addAgent = () => setAgents((p) => [...p, { name: '', extends: '', role: '', prompt: '' }])
  const removeAgent = (i: number) => setAgents((p) => {
    const removed = p[i]
    if (removed) {
      if (removed.name.trim() === coordinator.trim()) setCoordinator('')
      if (removed.name.trim() === entry.trim()) setEntry('')
    }
    return p.filter((_, idx) => idx !== i)
  })
  const updateAgentField = (i: number, field: string, val: string) =>
    setAgents((p) => p.map((a, idx) => {
      if (idx !== i) return a
      if (field === 'name') {
        if (a.name.trim() === coordinator.trim()) setCoordinator(val)
        if (a.name.trim() === entry.trim()) setEntry(val)
      }
      return { ...a, [field]: val }
    }))
  const addStage = () => setStages((p) => [...p, { id: '', parallel: false, runs_on: '', depends_on: '', inputs: '', spawns: [{ agent: '', task: '' }] }])
  const removeStage = (i: number) => setStages((p) => p.filter((_, idx) => idx !== i))
  const addSpawn = (si: number) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: [...s.spawns, { agent: '', task: '' }] } : s))
  const removeSpawn = (si: number, spi: number) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: s.spawns.filter((_, j) => j !== spi) } : s))
  const updateSpawnTask = (si: number, spi: number, task: string) =>
    setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: s.spawns.map((x, j) => j === spi ? { ...x, task } : x) } : s))

  useEffect(() => {
    setAgents((prev) => normalizeFlowAgentsForMode(prev, mode, coordinator, entry))
  }, [mode, coordinator, entry])

  const agentNames = agents.filter((a) => a.name.trim()).map((a) => a.name.trim())

  // Dropdown for Lead / Entry / Spawn agents should also show agents
  // discovered from the registry (builtin / global / project) — otherwise a
  // freshly-created flow has nothing to pick from until the user manually
  // adds participants.  Registry agents are surfaced with a "(注册表)" hint.
  const registryOnlyNames = allAgents
    .map((a) => a.name)
    .filter((name) => !agentNames.includes(name))
  const agentSelectOptions = [
    ...agentNames.map((name) => ({ value: name, label: name })),
    ...registryOnlyNames.map((name) => ({ value: name, label: `${name} (注册表)` })),
  ]

  // Auto-append a registry agent into the flow's agent list when the user
  // picks one from a Lead/Entry/Spawn dropdown.  Keeps the backend happy —
  // the validator requires the referenced agent to exist inside the flow.
  const ensureAgent = (selected: string, defaultRole: string) => {
    if (!selected) return
    if (agentNames.includes(selected)) return
    setAgents((prev) => [
      ...prev,
      { name: selected, extends: selected, role: defaultRole, prompt: '' },
    ])
  }

  const handleSelectCoordinator = (v: string) => {
    setCoordinator(v)
    ensureAgent(v, 'coordinator')
  }

  const handleSelectEntry = (v: string) => {
    setEntry(v)
    ensureAgent(v, 'entry')
  }

  const handleSelectSpawnAgent = (stageIdx: number, spawnIdx: number, v: string) => {
    setStages((p) =>
      p.map((s, idx) =>
        idx === stageIdx
          ? { ...s, spawns: s.spawns.map((x, j) => (j === spawnIdx ? { ...x, agent: v } : x)) }
          : s,
      ),
    )
    ensureAgent(v, 'worker')
  }

  const handleSave = () => {
    const varsObj: Record<string, string> = {}
    vars.forEach((v) => { if (v.key.trim()) varsObj[v.key.trim()] = v.value })
    const normalizedAgents = normalizeFlowAgentsForMode(agents, mode, coordinator, entry)
    const agentSpecs = normalizedAgents.filter((a) => a.name.trim()).map((a) => {
      const spec: Record<string, unknown> = { name: a.name.trim() }
      if (a.extends) spec.extends = a.extends
      if (a.role) spec.role = a.role
      if (a.prompt) spec.prompt = a.prompt
      return spec
    })
    const stageSpecs = stages.filter((s) => s.id.trim()).map((s) => {
      const spec: Record<string, unknown> = { id: s.id.trim() }
      if (s.parallel) spec.parallel = true
      if (s.runs_on) spec.runs_on = s.runs_on
      if (s.depends_on) spec.depends_on = s.depends_on.split(',').map((x) => x.trim()).filter(Boolean)
      if (s.inputs) spec.inputs = s.inputs.split(',').map((x) => x.trim()).filter(Boolean)
      if (s.spawns.length > 0) spec.spawn = s.spawns.filter((sp) => sp.agent.trim()).map((sp) => ({ agent: sp.agent.trim(), task: sp.task }))
      return spec
    })
    onSave({ name: name.trim(), description: desc || undefined, mode,
      extends: flowExtends || undefined,
      model: flowModel || undefined,
      max_depth: maxDepth ? parseInt(maxDepth) : undefined,
      entry: usesEntry(mode) ? entry || undefined : undefined,
      coordinator: usesCoordinator(mode) ? coordinator || undefined : undefined,
      agents: agentSpecs.length > 0 ? agentSpecs : undefined,
      stages: usesStages(mode) && stageSpecs.length > 0 ? stageSpecs : undefined,
      vars: Object.keys(varsObj).length > 0 ? varsObj : undefined, scope }, isNew)
  }

  const modeConfig = getFlowModeConfig(mode)
  const ModeIcon = modeConfig.icon

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-6 py-3.5 border-b border-[#E5E4E0] bg-[#0F0F0F]">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded-lg bg-[#3D3BF3] flex items-center justify-center">
            <Workflow size={14} className="text-white" />
          </div>
          <div>
            <h3 className="text-[13px] font-bold text-white">{isNew ? '创建编排流' : `编辑 · ${name}`}</h3>
            <p className="text-[10px] text-[#8A8A85] mt-0.5">设计多 Agent 协作流程</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={onCancel} className={btnGhost + ' !text-[#8A8A85] hover:!bg-white/10 hover:!text-white'}>取消</button>
          <button onClick={handleSave} disabled={saving || !name.trim() || agents.length === 0 || (usesCoordinator(mode) && !coordinator.trim())} className={btnPrimary}>
            {saving ? <RefreshCcw size={12} className="animate-spin" /> : <Save size={12} />}
            <span>保存</span>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
          {/* Basic */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-4'}><Hash size={12} className="text-[#3D3BF3]" />基本信息</div>
            <div className="grid gap-4 grid-cols-3">
              <div>
                <label className={labelStyle}>名称 *</label>
                <input value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} placeholder="my-flow" className={inputMono + ' disabled:opacity-40'} />
              </div>
              <div>
                <label className={labelStyle}>模式 *</label>
                <div className="grid grid-cols-3 gap-1.5">
                  {(['coordinator', 'hybrid', 'swarm'] as const).map((v) => {
                    const cfg = getFlowModeConfig(v)
                    const I = cfg.icon
                    return (
                    <button key={v} type="button" onClick={() => setMode(v)}
                      className={`flex-1 flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg text-[11px] font-semibold transition-all border whitespace-nowrap focus:outline-none ${mode === v ? pillActive : pillInactive}`}>
                      <I size={12} />{cfg.label}
                    </button>
                    )
                  })}
                </div>
              </div>
              <div>
                <label className={labelStyle}>作用域</label>
                <div className="flex gap-2">
                  {(['project', 'global'] as const).map((s) => (
                    <button key={s} type="button" onClick={() => setScope(s)} className={`flex-1 px-3 py-2 rounded-lg text-[12px] font-semibold transition-all border focus:outline-none ${scope === s ? pillActive : pillInactive}`}>
                      {s === 'project' ? '项目级' : '全局'}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-4 rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[10px] font-bold ${modeConfig.badge}`}>
                  <ModeIcon size={11} />{modeConfig.label}
                </span>
                <span className="text-[11px] leading-5 text-[#6B6A78]">{modeConfig.description}</span>
              </div>
            </div>
            {/* Facilitator / starting agent (mode-specific) */}
            <div className="mt-4 grid gap-4 grid-cols-2">
              {usesCoordinator(mode) && (
                <div>
                  <label className={labelStyle} title="工作流式中负责阶段汇总；主管协作式中负责接收任务、组织专家并综合结果">协调 Agent *</label>
                  <Select value={coordinator} onChange={handleSelectCoordinator} placeholder="选择协调 Agent（必填）"
                    options={agentSelectOptions} mono />
                </div>
              )}
              {usesEntry(mode) && (
                <div>
                  <label className={labelStyle} title="Swarm 式的初始任务接收者；主管协作式未指定协调 Agent 时作为主管回退">起始 Agent</label>
                  <Select value={entry} onChange={handleSelectEntry} placeholder="选择起始 Agent（可选）"
                    options={agentSelectOptions} mono />
                </div>
              )}
              <div>
                <label className={labelStyle}>描述</label>
                <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="编排流描述" className={inputBase} />
              </div>
            </div>
            <div className="mt-4 grid gap-4 grid-cols-3">
              <div>
                <label className={labelStyle}>继承自</label>
                <input value={flowExtends} onChange={(e) => setFlowExtends(e.target.value)} placeholder="base-flow" className={inputMono} />
              </div>
              <div>
                <label className={labelStyle}>模型</label>
                <input value={flowModel} onChange={(e) => setFlowModel(e.target.value)} placeholder="provider/model-id" className={inputMono} />
              </div>
              <div>
                <label className={labelStyle}>最大深度</label>
                <input value={maxDepth} onChange={(e) => setMaxDepth(e.target.value)} type="number" min="1" placeholder="3" className={inputMono} />
              </div>
            </div>
          </div>

          {/* Agents */}
          <div className={cardStyle + ' p-5'}>
            <div className="flex items-center justify-between mb-4">
              <div className={sectionTitle}><Bot size={12} className="text-[#3D3BF3]" />参与 Agent<span className="text-[#ABABAB] font-normal ml-1">({agents.length})</span></div>
              <button onClick={addAgent} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#3D3BF3]/8 text-[#3D3BF3] text-[11px] font-semibold hover:bg-[#3D3BF3]/15 transition-colors">
                <Plus size={11} />添加
              </button>
            </div>
            <div className="space-y-2.5">
              {agents.map((agent, i) => (
                <div key={i} className="rounded-xl border border-[#E5E4E0] bg-[#FAFAF8] p-3.5">
                  {(() => {
                    const roleConfig = getRoleOptionsForFlowAgent(mode, agent.name, coordinator, entry)
                    const helperText = usesCoordinator(mode)
                      ? agent.name.trim() && agent.name.trim() === coordinator.trim()
                        ? '该 Agent 的协调者身份由上方统一指定。'
                        : `${modeConfig.label}下，其他参与者通常作为 worker 参与。`
                      : agent.name.trim() && agent.name.trim() === entry.trim()
                        ? '该 Agent 的 entry 身份由上方起始 Agent 统一指定。'
                        : 'Swarm 式下，其他参与者通常作为 teammate 参与。'

                    return (
                      <>
                  <div className="flex items-center gap-2 mb-2.5">
                    <GripVertical size={12} className="text-[#D4D3CF] cursor-grab" />
                    <input value={agent.name} onChange={(e) => updateAgentField(i, 'name', e.target.value)} placeholder="agent-name"
                      className="flex-1 bg-white rounded-lg px-2.5 py-1.5 text-[12px] text-[#1A1A1A] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace] transition-all" />
                    <Select value={agent.extends} onChange={(v) => updateAgentField(i, 'extends', v)} placeholder="无继承" className="w-28"
                      options={[...allAgents.map((a) => a.name), ...agentNames.filter((n) => n !== agent.name)].filter((v, j, arr) => arr.indexOf(v) === j).map((n) => ({ value: n, label: n }))} mono />
                    <Select value={agent.role} onChange={(v) => updateAgentField(i, 'role', v)} placeholder={roleConfig.placeholder} className="w-32"
                      options={roleConfig.options} mono />
                    <button onClick={() => removeAgent(i)} className="p-1.5 rounded-lg text-[#ABABAB] hover:text-[#dc2626] hover:bg-[#dc2626]/8 transition-colors">
                      <Trash2 size={12} />
                    </button>
                  </div>
                  <div className="mb-2 text-[10px] text-[#8A8A85]">{helperText}</div>
                  <textarea value={agent.prompt} onChange={(e) => updateAgentField(i, 'prompt', e.target.value)}
                    placeholder="Agent 提示词（可选）…" rows={2}
                    className="w-full bg-white rounded-lg px-2.5 py-1.5 text-[11px] text-[#1A1A1A] placeholder:text-[#ABABAB] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 resize-none font-[JetBrains_Mono,monospace] transition-all" />
                      </>
                    )
                  })()}
                </div>
              ))}
              {agents.length === 0 && <div className="text-center py-8 text-[13px] text-[#ABABAB]">点击 "添加" 按钮来添加参与的 Agent</div>}
            </div>
          </div>

          {/* Stages (orchestration/collaboration) */}
          {usesStages(mode) && (
            <div className={cardStyle + ' p-5'}>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={sectionTitle}><Layers size={12} className="text-[#3D3BF3]" />执行阶段<span className="text-[#ABABAB] font-normal ml-1">({stages.length})</span></div>
                  {/* View toggle */}
                  <div className="flex rounded-lg border border-[#E5E4E0] p-0.5">
                    <button
                      type="button"
                      onClick={() => setDagViewMode(false)}
                      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold transition-all ${
                        !dagViewMode ? 'bg-[#0F0F0F] text-white shadow-sm' : 'text-[#8A8A85] hover:text-[#5C5C5C]'
                      }`}
                    >
                      <List size={11} />表单
                    </button>
                    <button
                      type="button"
                      onClick={() => setDagViewMode(true)}
                      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold transition-all ${
                        dagViewMode ? 'bg-[#0F0F0F] text-white shadow-sm' : 'text-[#8A8A85] hover:text-[#5C5C5C]'
                      }`}
                    >
                      <LayoutGrid size={11} />DAG 图
                    </button>
                  </div>
                </div>
                <button onClick={addStage} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#3D3BF3]/8 text-[#3D3BF3] text-[11px] font-semibold hover:bg-[#3D3BF3]/15 transition-colors">
                  <Plus size={11} />添加
                </button>
              </div>

              {/* DAG Visual Editor */}
              {dagViewMode ? (
                <div className="h-[520px] -mx-2">
                  <FlowDAGEditor
                    stages={stages as StageData[]}
                    agents={agents.map((a) => ({ name: a.name, role: a.role || '' }))}
                    onChange={(newStages) => setStages(newStages as typeof stages)}
                    onAddStage={addStage}
                    agentSelectOptions={agentSelectOptions}
                    onSelectSpawnAgent={handleSelectSpawnAgent}
                    onUpdateSpawnTask={updateSpawnTask}
                    onUpdateStageField={(idx, field, val) =>
                      setStages((p) => p.map((s, i) => i === idx ? { ...s, [field]: val } : s))
                    }
                    onRemoveStage={removeStage}
                    onAddSpawn={addSpawn}
                    onRemoveSpawn={removeSpawn}
                  />
                </div>
              ) : (
              /* Form-based stage list */
              <div className="space-y-3">
                {stages.map((stage, si) => (
                  <div key={si} className="rounded-xl border border-[#E5E4E0] bg-[#FAFAF8] p-3.5 space-y-2.5">
                    <div className="flex items-center gap-2">
                      <input value={stage.id} onChange={(e) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, id: e.target.value } : s))}
                        placeholder="stage-id" className="flex-1 bg-white rounded-lg px-2.5 py-1.5 text-[12px] text-[#1A1A1A] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace] transition-all" />
                      <label className="flex items-center gap-1.5 text-[11px] text-[#5C5C5C] cursor-pointer select-none">
                        <input type="checkbox" checked={stage.parallel} onChange={(e) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, parallel: e.target.checked } : s))} className="w-3.5 h-3.5 rounded border-[#D4D3CF] text-[#3D3BF3]" />
                        <Zap size={10} className="text-[#d97706]" />并行
                      </label>
                      <input value={stage.runs_on} onChange={(e) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, runs_on: e.target.value } : s))}
                        placeholder="runs_on" className="w-24 bg-white rounded-lg px-2 py-1.5 text-[10px] text-[#5C5C5C] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace] transition-all" />
                      <button onClick={() => removeStage(si)} className="p-1.5 rounded-lg text-[#ABABAB] hover:text-[#dc2626] hover:bg-[#dc2626]/8 transition-colors"><Trash2 size={12} /></button>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <input value={stage.depends_on} onChange={(e) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, depends_on: e.target.value } : s))}
                        placeholder="depends_on (逗号分隔)" className="bg-white rounded-lg px-2.5 py-1.5 text-[11px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]" />
                      <input value={stage.inputs} onChange={(e) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, inputs: e.target.value } : s))}
                        placeholder="inputs (如 research.*)" className="bg-white rounded-lg px-2.5 py-1.5 text-[11px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]" />
                    </div>
                    <div className="pl-3.5 border-l-2 border-[#3D3BF3]/20 space-y-1.5">
                      {stage.spawns.map((sp, spi) => (
                        <div key={spi} className="flex items-center gap-2">
                          <Select value={sp.agent} onChange={(v) => handleSelectSpawnAgent(si, spi, v)}
                            placeholder="Agent" className="w-28" mono
                            options={agentSelectOptions} />
                          <input value={sp.task} onChange={(e) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: s.spawns.map((x, j) => j === spi ? { ...x, task: e.target.value } : x) } : s))}
                            placeholder="任务 / {{ vars.key }}" className="flex-1 bg-white rounded-lg px-2.5 py-1.5 text-[11px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]" />
                          <button onClick={() => removeSpawn(si, spi)} className="p-0.5 text-[#D4D3CF] hover:text-[#dc2626] transition-colors"><X size={11} /></button>
                        </div>
                      ))}
                      <button onClick={() => addSpawn(si)} className="text-[10px] text-[#3D3BF3] font-semibold hover:underline">+ Spawn</button>
                    </div>
                  </div>
                ))}
              </div>
              )}
            </div>
          )}

          {/* Vars */}
          <div className={cardStyle + ' p-5'}>
            <div className="flex items-center justify-between mb-3">
              <div className={sectionTitle}><Settings2 size={12} className="text-[#3D3BF3]" />变量</div>
              <button onClick={() => setVars((p) => [...p, { key: '', value: '' }])} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#3D3BF3]/8 text-[#3D3BF3] text-[11px] font-semibold hover:bg-[#3D3BF3]/15 transition-colors">
                <Plus size={11} />添加
              </button>
            </div>
            <div className="space-y-1.5">
              {vars.map((v, i) => (
                <div key={i} className="flex items-center gap-2">
                  <input value={v.key} onChange={(e) => setVars((p) => p.map((x, idx) => idx === i ? { ...x, key: e.target.value } : x))}
                    placeholder="key" className="w-36 bg-[#F4F3F0] rounded-lg px-2.5 py-1.5 text-[12px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]" />
                  <span className="text-[#D4D3CF] text-[12px]">=</span>
                  <input value={v.value} onChange={(e) => setVars((p) => p.map((x, idx) => idx === i ? { ...x, value: e.target.value } : x))}
                    placeholder="value" className="flex-1 bg-[#F4F3F0] rounded-lg px-2.5 py-1.5 text-[12px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]" />
                  <button onClick={() => setVars((p) => p.filter((_, idx) => idx !== i))} className="p-0.5 text-[#D4D3CF] hover:text-[#dc2626] transition-colors"><X size={11} /></button>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════
// MAIN WORKBENCH
// ══════════════════════════════════════════════════════════════
type RunEventItem = { event: string; data: Record<string, unknown>; time: number }

type RunDialogState = {
  flowName: string
  detail: FlowDetail
  task: string
  vars: Record<string, string>
}

function isBuiltinDemoFlow(flowName: string): flowName is 'research' | 'supervised-review' | 'pair-review' {
  return flowName === 'research' || flowName === 'supervised-review' || flowName === 'pair-review'
}

function DemoFlowBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-lg bg-[#3D3BF3]/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.08em] text-[#3D3BF3]">
      {label}
    </span>
  )
}

function ResearchRunPreset({ runDialog, setRunDialog }: {
  runDialog: RunDialogState
  setRunDialog: React.Dispatch<React.SetStateAction<RunDialogState | null>>
}) {
  const updateVar = (key: string, value: string) => {
    setRunDialog((prev) => prev ? { ...prev, vars: { ...prev.vars, [key]: value } } : prev)
  }

  return (
    <>
      <div className={cardStyle + ' p-4'}>
        <div className="flex items-center gap-2 mb-3">
          <DemoFlowBadge label="Builtin Demo" />
          <span className="text-[11px] font-semibold text-[#0F0F0F]">Research 启动面板</span>
        </div>
        <p className="text-[12px] leading-6 text-[#6B6A78]">
          这个示例会先并行做两轮初探，再基于结果自动 fan-out 深挖，最后由协调 Agent 输出结构化简报。
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <label className={cardStyle + ' block p-4'}>
          <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">研究问题 A</div>
          <textarea
            value={runDialog.vars.q1 ?? ''}
            onChange={(e) => updateVar('q1', e.target.value)}
            rows={4}
            placeholder="例如：描述认证与权限相关模块的结构和边界"
            className={inputBase + ' mt-3 min-h-[120px] resize-y leading-6'}
          />
        </label>
        <label className={cardStyle + ' block p-4'}>
          <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">研究问题 B</div>
          <textarea
            value={runDialog.vars.q2 ?? ''}
            onChange={(e) => updateVar('q2', e.target.value)}
            rows={4}
            placeholder="例如：识别当前代码库里最值得优先处理的风险或维护热点"
            className={inputBase + ' mt-3 min-h-[120px] resize-y leading-6'}
          />
        </label>
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Layers size={12} className="text-[#3D3BF3]" />执行说明</div>
        <div className="grid gap-3 md:grid-cols-3 text-[11px] text-[#5C5C5C]">
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">Stage 1</div>
            <div className="mt-1 leading-5">两个 explorer 并行回答两个研究问题。</div>
          </div>
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">Stage 2</div>
            <div className="mt-1 leading-5">自动从每个初步发现中生成一个进一步深挖点。</div>
          </div>
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">Stage 3</div>
            <div className="mt-1 leading-5">协调 Agent 汇总成系统概览、风险、未知项与建议行动。</div>
          </div>
        </div>
      </div>
    </>
  )
}

function PairReviewRunPreset({ runDialog, setRunDialog }: {
  runDialog: RunDialogState
  setRunDialog: React.Dispatch<React.SetStateAction<RunDialogState | null>>
}) {
  const usePrompt = (text: string) => {
    setRunDialog((prev) => prev ? { ...prev, task: text } : prev)
  }

  const promptCards = [
    '请以多人 code review 的方式审查 `src/auth/`，分别从安全、性能与整体可维护性给出发现，并按严重级别汇总。',
    '请对最近的多 Agent 相关改动做 Swarm review，重点关注运行时约束、并发安全和用户可见回归。',
    '请审查一个 FastAPI + SQLite 模块，分别关注鉴权边界、查询效率、异常处理和测试缺口。',
  ]

  return (
    <>
      <div className={cardStyle + ' p-4'}>
        <div className="flex items-center gap-2 mb-3">
          <DemoFlowBadge label="Builtin Demo" />
          <span className="text-[11px] font-semibold text-[#0F0F0F]">Pair Review 启动面板</span>
        </div>
        <p className="text-[12px] leading-6 text-[#6B6A78]">
          这个示例会先由起始 agent 分工，再让安全和性能 reviewer 回报初判、互相校验关键点，
          并且必须发生 reviewer 之间的直接消息往来，最后由 reviewer-starter 汇总成带引用的评审结论。
        </p>
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Users size={12} className="text-[#d97706]" />常用审查模板</div>
        <div className="space-y-2">
          {promptCards.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => usePrompt(prompt)}
              className="w-full rounded-xl border border-[#E5E4E0] bg-[#FAFAF8] px-3.5 py-3 text-left text-[12px] leading-6 text-[#5C5C5C] transition-colors hover:border-[#3D3BF3]/25 hover:bg-white"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Activity size={12} className="text-[#3D3BF3]" />任务描述</div>
        <textarea
          value={runDialog.task}
          onChange={(e) => setRunDialog((prev) => prev ? { ...prev, task: e.target.value } : prev)}
          rows={5}
          placeholder="描述你希望这个 Swarm review 团队完成的审查任务..."
          className={inputBase + ' min-h-[140px] resize-y leading-6'}
        />
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Bot size={12} className="text-[#3D3BF3]" />协作方式</div>
        <div className="grid gap-3 md:grid-cols-3 text-[11px] text-[#5C5C5C]">
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">reviewer-starter</div>
            <div className="mt-1 leading-5">拆分任务、催收进度、强制专家互相校验，并把结论按严重级别汇总。</div>
          </div>
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">security-reviewer</div>
            <div className="mt-1 leading-5">关注 auth、secrets、输入校验与权限边界，并主动找 perf-reviewer 交叉确认。</div>
          </div>
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">perf-reviewer</div>
            <div className="mt-1 leading-5">关注 I/O、热循环、并发瓶颈，并主动找 security-reviewer 校验假设。</div>
          </div>
        </div>
      </div>
    </>
  )
}

function SupervisedReviewRunPreset({ runDialog, setRunDialog }: {
  runDialog: RunDialogState
  setRunDialog: React.Dispatch<React.SetStateAction<RunDialogState | null>>
}) {
  const usePrompt = (text: string) => {
    setRunDialog((prev) => prev ? { ...prev, task: text } : prev)
  }

  const promptCards = [
    '请由主管组织架构与风险专家评审这次多 Agent 编排改动，判断是否可以合入，并列出必须补齐的测试。',
    '请评估一个新功能方案：架构专家关注模块边界与扩展性，风险专家关注回归、权限和发布风险，最后由主管给出决策。',
    '请对最近的后端 API 改动做主管协作式 review，分别检查接口契约、数据一致性、异常路径和上线风险。',
  ]

  return (
    <>
      <div className={cardStyle + ' p-4'}>
        <div className="flex items-center gap-2 mb-3">
          <DemoFlowBadge label="Builtin Demo" />
          <span className="text-[11px] font-semibold text-[#0F0F0F]">Supervisor Review 启动面板</span>
        </div>
        <p className="text-[12px] leading-6 text-[#6B6A78]">
          这个示例体现主管协作式：review-supervisor 拥有最终判断权，通过消息组织架构与风险专家分别检查，
          专家用 main 回到主管，主管在专家反馈基础上给出 ship / follow-up / block 决策。
        </p>
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Users size={12} className="text-[#16a34a]" />主管任务模板</div>
        <div className="space-y-2">
          {promptCards.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => usePrompt(prompt)}
              className="w-full rounded-xl border border-[#E5E4E0] bg-[#FAFAF8] px-3.5 py-3 text-left text-[12px] leading-6 text-[#5C5C5C] transition-colors hover:border-[#16a34a]/25 hover:bg-white"
            >
              {prompt}
            </button>
          ))}
        </div>
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Activity size={12} className="text-[#3D3BF3]" />任务描述</div>
        <textarea
          value={runDialog.task}
          onChange={(e) => setRunDialog((prev) => prev ? { ...prev, task: e.target.value } : prev)}
          rows={5}
          placeholder="描述你希望主管组织专家完成的评审任务..."
          className={inputBase + ' min-h-[140px] resize-y leading-6'}
        />
      </div>

      <div className={cardStyle + ' p-4'}>
        <div className={sectionTitle + ' mb-3'}><Bot size={12} className="text-[#3D3BF3]" />协作方式</div>
        <div className="grid gap-3 md:grid-cols-3 text-[11px] text-[#5C5C5C]">
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">review-supervisor</div>
            <div className="mt-1 leading-5">接收任务、分派专家、追问不清晰反馈，并负责最终决策与综合输出。</div>
          </div>
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">architecture-reviewer</div>
            <div className="mt-1 leading-5">检查模块边界、耦合、数据流和方案是否贴合产品目标。</div>
          </div>
          <div className="rounded-xl bg-[#FAFAF8] px-3 py-3">
            <div className="font-semibold text-[#0F0F0F]">risk-reviewer</div>
            <div className="mt-1 leading-5">检查回归风险、缺失测试、权限边界、异常路径和发布风险。</div>
          </div>
        </div>
      </div>
    </>
  )
}

const LIVE_EVENT_TYPES = [
  'orchestration.flow.started',
  'orchestration.flow.finished',
  'orchestration.stage.started',
  'orchestration.stage.finished',
  'orchestration.spawn.started',
  'orchestration.spawn.finished',
  'orchestration.agent.message',
  'orchestration.agent.tool',
  'orchestration.message.sent',
  'orchestration.swarm.started',
  'orchestration.swarm.finished',
] as const

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function formatRunTime(timestamp?: number | null): string {
  if (!timestamp) return '—'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatRunElapsed(run: Pick<RunStatus, 'started_at' | 'finished_at'>): string {
  const started = run.started_at
  if (!started) return '—'
  const finished = run.finished_at ?? Date.now() / 1000
  const seconds = Math.max(0, finished - started)
  if (seconds < 1) return '<1s'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`
}

function getRunStatusLabel(run: Pick<RunStatus, 'status' | 'cancel_requested'>): string {
  switch (run.status) {
    case 'completed':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    case 'cancelling':
      return '取消中'
    default:
      return run.cancel_requested ? '准备取消' : '运行中'
  }
}

function getRunTone(run: Pick<RunStatus, 'status' | 'cancel_requested'>): { dot: string; badge: string } {
  switch (run.status) {
    case 'completed':
      return { dot: 'bg-[#16a34a]', badge: 'bg-[#16a34a]/10 text-[#16a34a]' }
    case 'failed':
      return { dot: 'bg-[#dc2626]', badge: 'bg-[#dc2626]/10 text-[#dc2626]' }
    case 'cancelled':
      return { dot: 'bg-[#b42318]', badge: 'bg-[#b42318]/10 text-[#b42318]' }
    case 'cancelling':
      return { dot: 'bg-[#d97706] animate-pulse', badge: 'bg-[#d97706]/10 text-[#d97706]' }
    default:
      return run.cancel_requested
        ? { dot: 'bg-[#d97706] animate-pulse', badge: 'bg-[#d97706]/10 text-[#d97706]' }
        : { dot: 'bg-[#3D3BF3] animate-pulse', badge: 'bg-[#3D3BF3]/10 text-[#3D3BF3]' }
  }
}

function getEventTone(event: string): string {
  if (event.includes('message')) return 'bg-[#d97706]/10 text-[#d97706]'
  if (event.includes('finished')) return 'bg-[#16a34a]/10 text-[#16a34a]'
  if (event.includes('started')) return 'bg-[#3D3BF3]/10 text-[#3D3BF3]'
  return 'bg-[#F4F3F0] text-[#8A8A85]'
}

function getEventLabel(event: string): string {
  return event.replace('orchestration.', '').split('.').join(' · ')
}

function getEventHeadline(event: string, data: Record<string, unknown>): string {
  const agent = asText(data.agent)
  const stageId = asText(data.stage_id)
  const sender = asText(data.sender)
  const recipient = asText(data.recipient)
  // Event payloads historically use ``lead``; prefer ``entry`` when the
  // backend has been updated.
  const entry = asText(data.entry) || asText(data.lead)
  switch (event) {
    case 'orchestration.flow.started':
      return `Flow 启动 · ${asText(data.mode) || 'unknown'}`
    case 'orchestration.flow.finished':
      return data.ok === false ? 'Flow 结束（失败）' : 'Flow 结束'
    case 'orchestration.stage.started':
      return `阶段 ${stageId || 'unknown'} 开始`
    case 'orchestration.stage.finished':
      return `阶段 ${stageId || 'unknown'} 完成`
    case 'orchestration.spawn.started':
      return `${agent || 'agent'} 开始执行`
    case 'orchestration.spawn.finished':
      return `${agent || 'agent'} 完成执行`
    case 'orchestration.agent.message':
      return `${agent || 'agent'} · ${asText(data.role) || 'message'}`
    case 'orchestration.agent.tool':
      return `${agent || 'agent'} · ${asText(data.tool_name) || 'tool'}`
    case 'orchestration.message.sent':
      return `${sender || 'unknown'} → ${recipient || 'unknown'}`
    case 'orchestration.swarm.started':
      return `Swarm 式启动 · 起始 Agent ${entry || 'unknown'}`
    case 'orchestration.swarm.finished':
      return `Swarm 式完成 · ${asText(data.terminated_reason) || 'unknown'}`
    default:
      return event
  }
}

function getEventDetail(event: string, data: Record<string, unknown>): string {
  const taskPreview = asText(data.task_preview)
  const outputPreview = asText(data.output_preview)
  const coordinatorPreview = asText(data.coordinator_preview)
  const summary = asText(data.summary)
  const contentPreview = asText(data.content_preview)
  const terminatedReason = asText(data.terminated_reason)
  const duration = asNumber(data.duration_seconds)
  const peers = asStringArray(data.peers)
  switch (event) {
    case 'orchestration.flow.started':
      return `参与 agent：${asStringArray(data.agents).join(', ') || '—'}`
    case 'orchestration.flow.finished':
      return duration != null ? `总耗时 ${duration.toFixed(2)}s` : 'Flow 已结束。'
    case 'orchestration.stage.started':
      return '等待该阶段下的 spawn 或协调任务推进。'
    case 'orchestration.stage.finished':
      return coordinatorPreview || outputPreview || `spawn 数：${asNumber(data.spawn_count) ?? 0}`
    case 'orchestration.spawn.started':
      return taskPreview || '已收到任务，等待输出。'
    case 'orchestration.spawn.finished':
      return outputPreview || (duration != null ? `执行耗时 ${duration.toFixed(2)}s` : '执行完成。')
    case 'orchestration.agent.message':
      return contentPreview || 'Agent 产生了一条新的对话内容。'
    case 'orchestration.agent.tool':
      return asText(data.output_preview) || asText(data.args_preview) || 'Agent 调用了一个工具。'
    case 'orchestration.message.sent':
      return summary || contentPreview || '已发送一条团队消息。'
    case 'orchestration.swarm.started':
      return `Peer：${peers.join(', ') || '—'}`
    case 'orchestration.swarm.finished':
      return terminatedReason || (duration != null ? `总耗时 ${duration.toFixed(2)}s` : 'Swarm 式运行已完成。')
    default:
      return contentPreview || outputPreview || taskPreview || '暂无更多细节。'
  }
}

function getAgentCardPreview(item?: AgentTranscriptItem): string {
  if (!item) return '已加入编排，等待分配任务或事件更新。'
  if (item.type === 'tool') {
    return item.output_preview || item.args_preview
      ? `${item.tool_name} · ${item.output_preview || item.args_preview}`
      : `${item.tool_name} 工具调用中`
  }
  if (item.recipient) return `发给 ${item.recipient}：${item.content_preview || '已发送协作消息。'}`
  return item.content_preview || '产生了一条新的对话内容。'
}

function getAgentStatusLabel(status: AgentBoardStatus): string {
  switch (status) {
    case 'waiting':
      return '待命'
    case 'active':
      return '执行中'
    case 'collaborating':
      return '协作中'
    case 'review':
      return '收尾中'
    case 'done':
      return '已完成'
    case 'error':
      return '异常'
    default:
      return status
  }
}

function getAgentStatusTone(status: AgentBoardStatus): { column: string; badge: string; dot: string; icon: React.ElementType } {
  switch (status) {
    case 'active':
      return { column: 'bg-[#FFF9EC]', badge: 'bg-[#d97706] text-white', dot: 'bg-[#d97706] animate-pulse', icon: Zap }
    case 'collaborating':
      return { column: 'bg-[#F0F8F3]', badge: 'bg-[#16a34a] text-white', dot: 'bg-[#16a34a] animate-pulse', icon: Users }
    case 'review':
      return { column: 'bg-[#F7F4FF]', badge: 'bg-[#7C3AED] text-white', dot: 'bg-[#7C3AED]', icon: Shield }
    case 'done':
      return { column: 'bg-[#F0F6FD]', badge: 'bg-[#1473D1] text-white', dot: 'bg-[#1473D1]', icon: Check }
    case 'error':
      return { column: 'bg-[#FFF1F1]', badge: 'bg-[#dc2626] text-white', dot: 'bg-[#dc2626]', icon: AlertCircle }
    default:
      return { column: 'bg-[#FAFAF8]', badge: 'bg-white text-[#8A8A85] border border-[#D4D3CF]', dot: 'bg-[#D4D3CF]', icon: Circle }
  }
}

function formatAgentUpdatedAt(timestamp?: number): string {
  if (!timestamp) return '—'
  return new Date(timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function buildAgentBoardCards(
  activeRun: RunDetail,
  flowDetail: FlowDetail | null,
  agentPanels: Record<string, AgentTranscriptItem[]>,
  runEvents: RunEventItem[],
): AgentBoardCard[] {
  const cards = new Map<string, AgentBoardCard>()
  const entryAgent = flowDetail?.entry || flowDetail?.lead
  const ensure = (name: string, seed?: Partial<AgentBoardCard>) => {
    const trimmed = name.trim()
    if (!trimmed) return null
    const existing = cards.get(trimmed)
    if (existing) {
      cards.set(trimmed, { ...existing, ...seed, agent: trimmed })
      return cards.get(trimmed)!
    }
    const created: AgentBoardCard = {
      agent: trimmed,
      role: seed?.role,
      description: seed?.description,
      status: seed?.status ?? 'waiting',
      label: seed?.label ?? '待命',
      eventCount: seed?.eventCount ?? 0,
      turnCount: seed?.turnCount ?? 0,
      toolCount: seed?.toolCount ?? 0,
      sentCount: seed?.sentCount ?? 0,
      receivedCount: seed?.receivedCount ?? 0,
      stage: seed?.stage,
      lastPreview: seed?.lastPreview ?? '已加入编排，等待分配任务或事件更新。',
      updatedAt: seed?.updatedAt,
      isEntry: seed?.isEntry ?? trimmed === entryAgent,
    }
    cards.set(trimmed, created)
    return created
  }

  flowDetail?.agents.forEach((agent) => {
    ensure(agent.name, {
      role: agent.role || agent.extends,
      description: agent.description || agent.prompt,
      isEntry: agent.name === entryAgent,
    })
  })

  Object.entries(agentPanels).forEach(([agentName, items]) => {
    const lastItem = items[items.length - 1]
    const toolCount = items.filter((item) => item.type === 'tool').length
    const messageItems = items.filter((item): item is Extract<AgentTranscriptItem, { type: 'message' }> => item.type === 'message')
    const card = ensure(agentName)
    if (!card) return
    card.eventCount = items.length
    card.turnCount = Math.max(0, ...items.map((item) => item.turn ?? 0))
    card.toolCount = toolCount
    card.sentCount = messageItems.filter((item) => Boolean(item.recipient)).length
    card.stage = lastItem?.stage_id || card.stage
    card.lastPreview = getAgentCardPreview(lastItem)
    card.updatedAt = lastItem?.time || card.updatedAt
    if (!activeRun.done) {
      card.status = card.sentCount > 0 ? 'collaborating' : 'active'
      card.label = getAgentStatusLabel(card.status)
    }
  })

  runEvents.forEach((ev) => {
    if (ev.event === 'orchestration.spawn.started' || ev.event === 'orchestration.spawn.finished' || ev.event === 'orchestration.agent.tool' || ev.event === 'orchestration.agent.message') {
      const agent = asText(ev.data.agent)
      const card = ensure(agent)
      if (!card) return
      card.updatedAt = Math.max(card.updatedAt ?? 0, ev.time)
      card.stage = asText(ev.data.stage_id) || card.stage
      if (!activeRun.done) {
        if (ev.event === 'orchestration.spawn.finished') {
          card.status = 'review'
          card.label = '等待汇总'
        } else if (ev.event === 'orchestration.agent.message' && asText(ev.data.recipient)) {
          card.status = 'collaborating'
          card.label = '协作中'
        } else {
          card.status = 'active'
          card.label = '执行中'
        }
      }
    }
    if (ev.event === 'orchestration.message.sent') {
      const sender = asText(ev.data.sender)
      const recipient = asText(ev.data.recipient)
      const isBroadcastSummary = recipient === '*'
      const senderCard = ensure(sender)
      if (senderCard) {
        senderCard.sentCount += 1
        senderCard.status = activeRun.done ? senderCard.status : 'collaborating'
        senderCard.label = activeRun.done ? senderCard.label : '协作中'
        senderCard.updatedAt = Math.max(senderCard.updatedAt ?? 0, ev.time)
        senderCard.lastPreview = asText(ev.data.summary) || asText(ev.data.content_preview) || `发给 ${recipient || '队友'} 的协作消息`
      }
      if (!isBroadcastSummary) {
        const recipientCard = ensure(recipient)
        if (recipientCard) {
          recipientCard.receivedCount += 1
          recipientCard.updatedAt = Math.max(recipientCard.updatedAt ?? 0, ev.time)
        }
      }
    }
  })

  const result = activeRun.result
  if (isSwarmRunResult(result)) {
    result.peers.forEach((peer) => {
      const card = ensure(peer.name, { role: peer.agent })
      if (!card) return
      card.status = peer.is_error ? 'error' : 'done'
      card.label = peer.is_error ? '异常' : '已完成'
      card.turnCount = Math.max(card.turnCount, getEffectivePeerTurns(peer))
      card.toolCount = Math.max(card.toolCount, peer.tool_calls)
      card.sentCount = Math.max(card.sentCount, peer.sent_count ?? 0)
      card.receivedCount = Math.max(card.receivedCount, peer.received_count ?? 0)
      card.lastPreview = getSwarmPeerNarrative(peer)
      card.isEntry = card.isEntry || peer.name === result.entry || peer.name === result.lead
    })
  } else if (isCoordinatorRunResult(result)) {
    result.stages.forEach((stage) => {
      const agent = stage.coordinator_agent
      if (!agent) return
      const card = ensure(agent, { stage: stage.stage_id })
      if (!card) return
      card.status = stage.is_error ? 'error' : 'done'
      card.label = stage.is_error ? '异常' : '已完成'
      card.lastPreview = stage.output_preview || card.lastPreview
      card.eventCount = Math.max(card.eventCount, stage.spawn_count)
    })
  }

  if (activeRun.done) {
    cards.forEach((card) => {
      if (card.status === 'waiting' || !isSwarmRunResult(result)) {
        card.status = activeRun.status === 'failed' ? 'error' : 'done'
        card.label = activeRun.status === 'failed' ? '异常' : getRunStatusLabel(activeRun)
      }
    })
  }

  return Array.from(cards.values()).sort((a, b) => {
    if (a.isEntry !== b.isEntry) return a.isEntry ? -1 : 1
    return a.agent.localeCompare(b.agent)
  })
}

function AgentOrchestrationBoard({ cards, selectedAgent, onSelectAgent, runDone = false }: {
  cards: AgentBoardCard[]
  selectedAgent: string | null
  onSelectAgent: (agent: string) => void
  runDone?: boolean
}) {
  const columns: Array<{ status: AgentBoardStatus; title: string }> = [
    { status: 'waiting', title: 'Backlog' },
    { status: 'active', title: 'In Progress' },
    { status: 'collaborating', title: 'Messaging' },
    { status: 'review', title: 'In Review' },
    { status: 'done', title: 'Done' },
    { status: 'error', title: 'Blocked' },
  ]
  const persistentColumns = runDone
    ? new Set<AgentBoardStatus>([])
    : new Set<AgentBoardStatus>(['active', 'collaborating', 'review'])
  const activeColumns = columns
    .map((column) => ({ ...column, cards: cards.filter((card) => card.status === column.status) }))
    .filter((column) => column.cards.length > 0 || persistentColumns.has(column.status))

  if (cards.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-[#E5E4E0] bg-[#FAFAF8] px-4 py-12 text-center text-[12px] text-[#ABABAB]">
        运行开始后，这里会按状态展示每一个成员 agent。
      </div>
    )
  }

  return (
    <div className="overflow-x-auto pb-2">
      <div
        className="grid gap-3"
        style={{
          gridTemplateColumns: `repeat(${activeColumns.length}, minmax(180px, 1fr))`,
          minWidth: `${Math.max(activeColumns.length * 180, 360)}px`,
        }}
      >
        {activeColumns.map((column) => {
          const tone = getAgentStatusTone(column.status)
          const Icon = tone.icon
          return (
            <div key={column.status} className={`min-h-[360px] rounded-2xl border border-[#E5E4E0]/80 ${tone.column} p-3`}>
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-bold ${tone.badge}`}>
                    <Icon size={10} />
                    {column.title}
                  </span>
                  <span className="text-[11px] font-semibold text-[#8A8A85]">{column.cards.length}</span>
                </div>
                <span className="text-[14px] leading-none text-[#ABABAB]">...</span>
              </div>
              <div className="space-y-2.5">
                {column.cards.length === 0 ? (
                  <div className="rounded-2xl border border-dashed border-[#E5E4E0] bg-white/45 px-3 py-8 text-center text-[11px] text-[#ABABAB]">
                    暂无成员
                  </div>
                ) : column.cards.map((card) => (
                  <button
                    key={card.agent}
                    type="button"
                    onClick={() => onSelectAgent(card.agent)}
                    className={`w-full rounded-2xl border bg-white px-3.5 py-3 text-left shadow-[0_1px_2px_rgba(15,15,15,0.04)] transition-all hover:-translate-y-0.5 hover:shadow-md ${
                      selectedAgent === card.agent ? 'border-[#3D3BF3]/35 ring-2 ring-[#3D3BF3]/10' : 'border-[#E5E4E0]'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="font-[JetBrains_Mono,monospace] text-[10px] text-[#8A8A85]">{card.agent}</div>
                        <div className="mt-1 line-clamp-2 text-[12px] font-bold leading-5 text-[#0F0F0F]">{card.role || card.description || (card.isEntry ? '起始 Agent' : '成员 Agent')}</div>
                      </div>
                      <span className={`mt-0.5 h-2 w-2 rounded-full ${tone.dot}`} />
                    </div>
                    <div className="mt-2 line-clamp-2 text-[11px] leading-5 text-[#6B6A78]">{card.lastPreview}</div>
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                      {card.isEntry && <span className="rounded-md bg-[#0F0F0F] px-1.5 py-0.5 text-[9px] font-bold text-white">ENTRY</span>}
                      {card.stage && <span className="rounded-md bg-[#F4F3F0] px-1.5 py-0.5 text-[9px] font-semibold text-[#8A8A85]">{card.stage}</span>}
                      <span className="rounded-md bg-[#F4F3F0] px-1.5 py-0.5 text-[9px] font-semibold text-[#8A8A85]">{card.eventCount} events</span>
                      {card.toolCount > 0 && <span className="rounded-md bg-[#d97706]/10 px-1.5 py-0.5 text-[9px] font-semibold text-[#d97706]">{card.toolCount} tools</span>}
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-1.5 text-center">
                      <div className="rounded-lg bg-[#FAFAF8] px-2 py-1">
                        <div className="text-[10px] font-bold text-[#0F0F0F]">{card.turnCount}</div>
                        <div className="text-[8px] uppercase tracking-[0.08em] text-[#ABABAB]">Turns</div>
                      </div>
                      <div className="rounded-lg bg-[#FAFAF8] px-2 py-1">
                        <div className="text-[10px] font-bold text-[#0F0F0F]">{card.sentCount}</div>
                        <div className="text-[8px] uppercase tracking-[0.08em] text-[#ABABAB]">Sent</div>
                      </div>
                      <div className="rounded-lg bg-[#FAFAF8] px-2 py-1">
                        <div className="text-[10px] font-bold text-[#0F0F0F]">{formatAgentUpdatedAt(card.updatedAt)}</div>
                        <div className="text-[8px] uppercase tracking-[0.08em] text-[#ABABAB]">Updated</div>
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function isSwarmRunResult(result: RunDetail['result']): result is SwarmRunResult {
  return Boolean(result)
    && typeof result === 'object'
    && !Array.isArray(result)
    && ((result as { kind?: unknown }).kind === 'swarm' || (result as { kind?: unknown }).kind === 'hybrid')
    && Array.isArray((result as { peers?: unknown }).peers)
}

function isCoordinatorRunResult(result: RunDetail['result']): result is CoordinatorRunResult {
  return Boolean(result)
    && typeof result === 'object'
    && !Array.isArray(result)
    && (result as { kind?: unknown }).kind === 'coordinator'
    && Array.isArray((result as { stages?: unknown }).stages)
}

function isGenericCancelledResult(result: RunDetail['result']): boolean {
  return Boolean(result)
    && typeof result === 'object'
    && !Array.isArray(result)
    && (result as { cancelled?: unknown }).cancelled === true
}

function formatSwarmTerminationReason(reason: string): string {
  switch (reason) {
    case 'lead-quiet':
      return '起始 Agent 结束轮询'
    case 'shutdown':
      return '团队协商收尾'
    case 'walltime':
      return '达到时限'
    case 'turn-budget':
      return '达到轮数预算'
    default:
      return reason || '—'
  }
}

function getSwarmPeerNarrative(peer: SwarmPeerSummary): string {
  const preview = peer.recent_activity_preview || peer.output_preview
  switch (peer.recent_activity_direction) {
    case 'sent':
      return preview ? `最近发给 ${peer.recent_activity_partner || '队友'}：${preview}` : '最近主动向队友同步了进展。'
    case 'received':
      return preview ? `最近收到 ${peer.recent_activity_partner || '队友'}：${preview}` : '最近在等待或消化队友反馈。'
    case 'output':
      return preview || '留下了独立输出。'
    default:
      if ((peer.sent_count ?? 0) > 0 || (peer.received_count ?? 0) > 0) {
        return '主要通过团队消息参与，没有留下单独的最终文本。'
      }
      return peer.output_preview || '没有留下可见的文本或消息痕迹。'
  }
}

function hasMeaningfulPeerOutput(peer: SwarmPeerSummary): boolean {
  const preview = (peer.output_preview || '').trim()
  return preview !== '' && preview !== '(no output)' && preview !== '_(no output)_'
}

function getEffectivePeerTurns(peer: SwarmPeerSummary): number {
  if (
    (peer.sent_count ?? 0) === 0 &&
    (peer.received_count ?? 0) === 0 &&
    peer.tool_calls === 0 &&
    !hasMeaningfulPeerOutput(peer)
  ) {
    return 0
  }
  return peer.turns
}

export default function OrchestrationWorkbench({ onBack }: Props) {
  const [tab, setTab] = useState<Tab>('agents')
  const [agents, setAgents] = useState<OrchestrationAgent[]>([])
  const [flows, setFlows] = useState<FlowInfo[]>([])
  const [runs, setRuns] = useState<RunStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const [saving, setSaving] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentCreateParams | null | 'new'>(null)
  const [editingFlow, setEditingFlow] = useState<(FlowDetail & { scope?: string }) | null | 'new'>(null)
  const [expandedFlow, setExpandedFlow] = useState<string | null>(null)
  const [flowDetail, setFlowDetail] = useState<FlowDetail | null>(null)
  const [runDialog, setRunDialog] = useState<RunDialogState | null>(null)
  const [runDetailTab, setRunDetailTab] = useState<RunDetailTab>('overview')
  const [startingRun, setStartingRun] = useState(false)
  const [runEvents, setRunEvents] = useState<RunEventItem[]>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [activeRun, setActiveRun] = useState<RunDetail | null>(null)
  const [activeRunFlowDetail, setActiveRunFlowDetail] = useState<FlowDetail | null>(null)
  const [runDetailLoading, setRunDetailLoading] = useState(false)
  const [cancellingRun, setCancellingRun] = useState(false)
  const [deletingRunId, setDeletingRunId] = useState<string | null>(null)
  const [agentPanels, setAgentPanels] = useState<Record<string, AgentTranscriptItem[]>>({})
  const [selectedAgentPanel, setSelectedAgentPanel] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  const fire = useCallback((type: 'success' | 'error', msg: string) => {
    setToast({ type, msg })
    window.setTimeout(() => setToast(null), 3000)
  }, [])

  const refresh = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) setLoading(true)
    try {
      const [agentList, flowList, runList] = await Promise.all([
        listOrchestrationAgents(),
        listFlows(),
        listRuns(),
      ])
      setAgents(agentList)
      setFlows(flowList)
      setRuns(runList)
    } catch {
      // ignore refresh noise in the UI loop
    } finally {
      if (!options?.silent) setLoading(false)
    }
  }, [])

  const refreshRunDetail = useCallback(async (runId: string, silent: boolean = false) => {
    if (!silent) setRunDetailLoading(true)
    try {
      const detail = await getRun(runId)
      setActiveRun(detail)
      try {
        setActiveRunFlowDetail(await getFlow(detail.flow))
      } catch {
        setActiveRunFlowDetail(null)
      }
    } catch (err) {
      if (!silent) fire('error', `加载运行详情失败: ${err}`)
    } finally {
      if (!silent) setRunDetailLoading(false)
    }
  }, [fire])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    if (tab !== 'runs') return undefined
    const id = window.setInterval(() => { void refresh({ silent: true }) }, 3000)
    return () => window.clearInterval(id)
  }, [refresh, tab])

  useEffect(() => {
    if (!activeRunId) {
      setActiveRun(null)
      setActiveRunFlowDetail(null)
      setAgentPanels({})
      setSelectedAgentPanel(null)
      setRunDetailTab('overview')
      return
    }
    void refreshRunDetail(activeRunId)
  }, [activeRunId, refreshRunDetail])

  useEffect(() => {
    if (tab !== 'runs' || !activeRunId || activeRun?.done) return undefined
    const id = window.setInterval(() => { void refreshRunDetail(activeRunId, true) }, 2500)
    return () => window.clearInterval(id)
  }, [activeRun?.done, activeRunId, refreshRunDetail, tab])

  const activeAgentCards = useMemo(
    () => activeRun ? buildAgentBoardCards(activeRun, activeRunFlowDetail, agentPanels, runEvents) : [],
    [activeRun, activeRunFlowDetail, agentPanels, runEvents],
  )
  const activeAgentStats = useMemo(() => ({
    members: activeAgentCards.length,
    waiting: activeAgentCards.filter((card) => card.status === 'waiting').length,
    active: activeAgentCards.filter((card) => card.status === 'active' || card.status === 'collaborating' || card.status === 'review').length,
    done: activeAgentCards.filter((card) => card.status === 'done').length,
    blocked: activeAgentCards.filter((card) => card.status === 'error').length,
    tools: activeAgentCards.reduce((sum, card) => sum + card.toolCount, 0),
    messages: activeAgentCards.reduce((sum, card) => sum + card.sentCount, 0),
  }), [activeAgentCards])

  useEffect(() => {
    if (!activeRunId) return undefined
    const es = new EventSource(`/orchestration/events?run_id=${activeRunId}`)
    eventSourceRef.current = es

    const appendEvent = (eventName: string, payload: string) => {
      try {
        const data = JSON.parse(payload) as Record<string, unknown>
        setRunEvents((previous) => [...previous, { event: eventName, data, time: Date.now() }])
        if (eventName === 'orchestration.agent.message') {
          const agent = asText(data.agent)
          if (agent) {
            const item: AgentTranscriptItem = {
              type: 'message',
              role: asText(data.role),
              kind: asText(data.kind),
              turn: asNumber(data.turn) ?? 0,
              content_preview: asText(data.content_preview),
              recipient: asText(data.recipient) || undefined,
              stage_id: asText(data.stage_id) || null,
              spawn_index: asNumber(data.spawn_index),
              time: Date.now(),
            }
            setAgentPanels((previous) => ({
              ...previous,
              [agent]: [...(previous[agent] ?? []), item],
            }))
            setSelectedAgentPanel((previous) => previous ?? agent)
          }
        }
        if (eventName === 'orchestration.agent.tool') {
          const agent = asText(data.agent)
          if (agent) {
            const item: AgentTranscriptItem = {
              type: 'tool',
              tool_name: asText(data.tool_name),
              turn: asNumber(data.turn) ?? 0,
              args_preview: asText(data.args_preview),
              output_preview: asText(data.output_preview),
              stage_id: asText(data.stage_id) || null,
              spawn_index: asNumber(data.spawn_index),
              time: Date.now(),
            }
            setAgentPanels((previous) => ({
              ...previous,
              [agent]: [...(previous[agent] ?? []), item],
            }))
            setSelectedAgentPanel((previous) => previous ?? agent)
          }
        }
        if (eventName.includes('finished') || eventName.includes('started')) {
          void refresh({ silent: true })
          void refreshRunDetail(activeRunId, true)
        }
      } catch {
        // ignore malformed payloads
      }
    }

    LIVE_EVENT_TYPES.forEach((eventName) => {
      es.addEventListener(eventName, (ev) => {
        appendEvent(eventName, (ev as MessageEvent).data)
      })
    })

    return () => {
      es.close()
      eventSourceRef.current = null
    }
  }, [activeRunId, refresh, refreshRunDetail])

  const handleSaveAgent = async (params: AgentCreateParams, isNew: boolean) => {
    setSaving(true)
    try {
      if (isNew) await createAgent(params)
      else await updateAgent(params.name, params)
      fire('success', isNew ? `Agent "${params.name}" 已创建` : '已更新')
      setEditingAgent(null)
      await refresh({ silent: true })
      // Notify the sidebar (which maintains its own agent/flow state)
      // to re-fetch so the newly-created agent shows up immediately.
      window.dispatchEvent(new CustomEvent('orchestration:refresh'))
    } catch (err) {
      fire('error', `保存失败: ${err}`)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteAgent = async (name: string, source: string) => {
    if (!confirm(`确定删除 Agent "${name}"？`)) return
    try {
      await deleteAgent(name, source === 'global' ? 'global' : 'project')
      fire('success', '已删除')
      await refresh({ silent: true })
      window.dispatchEvent(new CustomEvent('orchestration:refresh'))
    } catch (err) {
      fire('error', `删除失败: ${err}`)
    }
  }

  const handleSaveFlow = async (params: FlowCreateParams, isNew: boolean) => {
    setSaving(true)
    try {
      if (isNew) await createFlow(params)
      else await updateFlow(params.name, params)
      fire('success', isNew ? `编排流 "${params.name}" 已创建` : '已更新')
      setEditingFlow(null)
      await refresh({ silent: true })
      window.dispatchEvent(new CustomEvent('orchestration:refresh'))
    } catch (err) {
      fire('error', `保存失败: ${err}`)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteFlow = async (name: string, source: string) => {
    if (!confirm(`确定删除编排流 "${name}"？`)) return
    try {
      await deleteFlow(name, source === 'global' ? 'global' : 'project')
      fire('success', '已删除')
      await refresh({ silent: true })
      window.dispatchEvent(new CustomEvent('orchestration:refresh'))
    } catch (err) {
      fire('error', `删除失败: ${err}`)
    }
  }

  const openRunDialog = useCallback(async (flow: FlowInfo) => {
    try {
      const detail = expandedFlow === flow.name && flowDetail ? flowDetail : await getFlow(flow.name)
      setRunDialog({
        flowName: flow.name,
        detail,
        task: '',
        vars: { ...detail.vars },
      })
    } catch (err) {
      fire('error', `加载运行配置失败: ${err}`)
    }
  }, [expandedFlow, fire, flowDetail])

  const handleStartRun = async () => {
    if (!runDialog) return
    if ((runDialog.detail.mode === 'swarm' || runDialog.detail.mode === 'hybrid') && !runDialog.task.trim()) {
      fire('error', `请输入 ${getFlowModeConfig(runDialog.detail.mode).label} 任务描述`)
      return
    }

    setStartingRun(true)
    try {
      const params: StartRunParams = { flow: runDialog.flowName }
      const vars = Object.fromEntries(
        Object.entries(runDialog.vars)
          .filter(([key]) => key.trim())
          .map(([key, value]) => [key, value ?? '']),
      )
      if (Object.keys(vars).length > 0) params.vars = vars
      if (runDialog.detail.mode === 'swarm' || runDialog.detail.mode === 'hybrid') params.task = runDialog.task.trim()

      const result = await startRun(params)
      fire('success', `运行 ${result.run_id.slice(0, 8)}… 已启动`)
      setRunDialog(null)
      setRunEvents([])
      setAgentPanels({})
      setSelectedAgentPanel(null)
      setActiveRunId(result.run_id)
      setTab('runs')
      await Promise.all([
        refresh({ silent: true }),
        refreshRunDetail(result.run_id, true),
      ])
    } catch (err) {
      fire('error', `启动失败: ${err}`)
    } finally {
      setStartingRun(false)
    }
  }

  const handleCancelActiveRun = async () => {
    if (!activeRunId) return
    setCancellingRun(true)
    try {
      const result = await cancelRun(activeRunId)
      fire('success', result.already_finished ? `运行已结束，当前状态：${result.status}` : '已发送取消请求')
      await Promise.all([
        refresh({ silent: true }),
        refreshRunDetail(activeRunId, true),
      ])
    } catch (err) {
      fire('error', `取消失败: ${err}`)
    } finally {
      setCancellingRun(false)
    }
  }

  const handleDeleteRun = async (runId: string) => {
    const target = runs.find((run) => run.run_id === runId) ?? activeRun
    if (target && !target.done) {
      fire('error', '运行仍在进行中，请先取消或等待结束')
      return
    }
    if (!confirm(`确定删除运行记录 "${runId}"？`)) return
    setDeletingRunId(runId)
    try {
      await deleteRun(runId)
      fire('success', '运行记录已删除')
      setRuns((items) => items.filter((item) => item.run_id !== runId))
      if (activeRunId === runId) {
        setActiveRunId(null)
        setActiveRun(null)
        setRunEvents([])
        setAgentPanels({})
        setSelectedAgentPanel(null)
      }
      await refresh({ silent: true })
    } catch (err) {
      fire('error', `删除失败: ${err}`)
    } finally {
      setDeletingRunId(null)
    }
  }

  const handleViewFlow = async (name: string) => {
    if (expandedFlow === name) {
      setExpandedFlow(null)
      setFlowDetail(null)
      return
    }
    setExpandedFlow(name)
    try {
      setFlowDetail(await getFlow(name))
    } catch (err) {
      setFlowDetail(null)
      fire('error', `加载 flow 失败: ${err}`)
    }
  }

  const handleEditAgent = useCallback(async (name: string) => {
    try {
      const detail: OrchestrationAgentDetail = await getOrchestrationAgent(name)
      setEditingAgent(detail)
    } catch (err) {
      fire('error', `加载 Agent 失败: ${err}`)
    }
  }, [fire])

  if (editingAgent !== null) return (
    <div className="flex-1 flex flex-col bg-[#FAFAF8] min-h-0">
      <Toast t={toast} onClose={() => setToast(null)} />
      <AgentEditor initial={editingAgent === 'new' ? undefined : editingAgent} allAgents={agents} onSave={handleSaveAgent} onCancel={() => setEditingAgent(null)} saving={saving} />
    </div>
  )

  if (editingFlow !== null) return (
    <div className="flex-1 flex flex-col bg-[#FAFAF8] min-h-0">
      <Toast t={toast} onClose={() => setToast(null)} />
      <FlowEditor initial={editingFlow === 'new' ? undefined : editingFlow} allAgents={agents} onSave={handleSaveFlow} onCancel={() => setEditingFlow(null)} saving={saving} />
    </div>
  )

  return (
    <div className="flex-1 flex flex-col bg-[#FAFAF8] min-h-0">
      <Toast t={toast} onClose={() => setToast(null)} />

      <div className="flex items-center gap-4 px-5 py-3 bg-[#0F0F0F] border-b border-[#2A2A2A]">
        <button onClick={onBack} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium text-[#8A8A85] hover:bg-white/8 hover:text-white transition-colors">
          <ArrowLeft size={13} /><span>返回对话</span>
        </button>
        <div className="w-px h-5 bg-[#2A2A2A]" />
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-md bg-[#3D3BF3] flex items-center justify-center">
            <Network size={12} className="text-white" />
          </div>
          <h2 className="text-[13px] font-bold text-white tracking-tight">编排工作台</h2>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-4 text-[11px]">
          <span className="text-[#8A8A85]"><span className="font-bold text-white">{agents.length}</span> Agents</span>
          <span className="text-[#8A8A85]"><span className="font-bold text-white">{flows.length}</span> Flows</span>
          {runs.filter((run) => !run.done).length > 0 && (
            <span className="flex items-center gap-1.5 text-[#16a34a]">
              <Circle size={6} fill="currentColor" className="animate-pulse" />
              <span className="font-bold">{runs.filter((run) => !run.done).length}</span> 运行中
            </span>
          )}
        </div>
        <button onClick={() => void refresh()} className="p-1.5 rounded-lg text-[#8A8A85] hover:bg-white/8 hover:text-white transition-colors" title="刷新">
          <RefreshCcw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="px-5 py-3 bg-white border-b border-[#E5E4E0]">
        <div className="flex gap-1">
          {([
            { key: 'agents' as const, icon: Bot, label: 'Agent 管理' },
            { key: 'flows' as const, icon: Workflow, label: '流程设计' },
            { key: 'runs' as const, icon: Activity, label: '运行监控' },
          ]).map(({ key, icon: Icon, label }) => (
            <button key={key} onClick={() => setTab(key)}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-[12px] font-semibold transition-all ${
                tab === key
                  ? 'bg-[#0F0F0F] text-white shadow-[0_1px_3px_rgba(0,0,0,0.2)]'
                  : 'text-[#8A8A85] hover:bg-[#F4F3F0] hover:text-[#5C5C5C]'
              }`}>
              <Icon size={13} />{label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className={tab === 'runs' ? 'w-full px-5 py-5 xl:px-6 2xl:px-8' : 'max-w-5xl mx-auto px-5 py-5'}>
          {tab === 'agents' && (
            <div>
              <div className="flex items-center justify-between mb-5">
                <p className="text-[12px] text-[#8A8A85]">{agents.length} 个 Agent — 内置 + 全局 + 项目</p>
                <button onClick={() => setEditingAgent('new')} className={btnPrimary}><Plus size={13} />创建 Agent</button>
              </div>
              <div className="grid gap-2.5">
                {agents.map((agent) => {
                  const canEdit = agent.source === 'project' || agent.source === 'global'
                  return (
                    <div key={agent.name} className={cardStyle + ' flex items-center gap-4 px-5 py-3.5 group hover:shadow-md transition-shadow'}>
                      <div className="w-9 h-9 rounded-xl bg-[#3D3BF3]/8 flex items-center justify-center flex-shrink-0">
                        <Bot size={16} className="text-[#3D3BF3]" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2.5">
                          <span className="text-[13px] font-semibold text-[#0F0F0F]">{agent.name}</span>
                          <SourceBadge source={agent.source} />
                          {agent.extends && <span className="text-[10px] text-[#ABABAB] flex items-center gap-0.5"><GitBranch size={9} />{agent.extends}</span>}
                        </div>
                        {agent.description && <div className="text-[12px] text-[#8A8A85] mt-0.5 truncate">{agent.description}</div>}
                        {agent.error && <div className="text-[11px] text-[#dc2626] mt-0.5 flex items-center gap-1"><AlertCircle size={10} />{agent.error}</div>}
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        {canEdit && (
                          <>
                            <button onClick={() => void handleEditAgent(agent.name)}
                              className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#F4F3F0] hover:text-[#5C5C5C] transition-colors" title="编辑"><Edit3 size={13} /></button>
                            <button onClick={() => void handleDeleteAgent(agent.name, agent.source)}
                              className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#dc2626]/8 hover:text-[#dc2626] transition-colors" title="删除"><Trash2 size={13} /></button>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {tab === 'flows' && (
            <div>
              <div className="flex items-center justify-between mb-5">
                <p className="text-[12px] text-[#8A8A85]">{flows.length} 个编排流</p>
                <button onClick={() => setEditingFlow('new')} className={btnPrimary}><Plus size={13} />创建编排流</button>
              </div>
              <div className="grid gap-2.5">
                {flows.map((flow) => {
                  const isExp = expandedFlow === flow.name
                  const canEdit = flow.source === 'project' || flow.source === 'global'
                  const detailMode = flowDetail && isExp ? getFlowModeConfig(flowDetail.mode) : null
                  return (
                    <div key={flow.name} className={cardStyle + ` overflow-hidden ${isExp ? 'shadow-md' : 'hover:shadow-md'} transition-shadow`}>
                      <div className="flex items-center gap-4 px-5 py-3.5 cursor-pointer group" onClick={() => void handleViewFlow(flow.name)}>
                        <div className="flex-shrink-0">{isExp ? <ChevronDown size={14} className="text-[#8A8A85]" /> : <ChevronRight size={14} className="text-[#D4D3CF]" />}</div>
                        <div className="w-9 h-9 rounded-xl bg-[#3D3BF3]/8 flex items-center justify-center flex-shrink-0"><Workflow size={16} className="text-[#3D3BF3]" /></div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2.5"><span className="text-[13px] font-semibold text-[#0F0F0F]">{flow.name}</span><SourceBadge source={flow.source} /></div>
                        </div>
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
                          <button onClick={() => void openRunDialog(flow)}
                            className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#16a34a]/8 hover:text-[#16a34a] transition-colors" title="运行"><Play size={13} /></button>
                          {canEdit && (
                            <>
                              <button onClick={async () => {
                                const detail = isExp && flowDetail ? flowDetail : await getFlow(flow.name)
                                setEditingFlow({ ...detail, scope: flow.source })
                              }}
                                className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#F4F3F0] hover:text-[#5C5C5C] transition-colors" title="编辑"><Edit3 size={13} /></button>
                              <button onClick={() => void handleDeleteFlow(flow.name, flow.source)}
                                className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#dc2626]/8 hover:text-[#dc2626] transition-colors" title="删除"><Trash2 size={13} /></button>
                            </>
                          )}
                        </div>
                      </div>
                      {isExp && flowDetail && (
                        <div className="px-5 pb-4 border-t border-[#F4F3F0]">
                          <div className="mt-3.5 space-y-3">
                            <div className="flex items-center gap-2.5 flex-wrap">
                              {(() => {
                                const cfg = detailMode ?? getFlowModeConfig(flowDetail.mode)
                                const Icon = cfg.icon
                                return <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-bold ${cfg.badge}`}><Icon size={10} />{cfg.label}</span>
                              })()}
                              {isBuiltinDemoFlow(flow.name) && <DemoFlowBadge label="Builtin Demo" />}
                              {usesCoordinator(flowDetail.mode) && flowDetail.coordinator && <span className="text-[11px] text-[#8A8A85]">协调 Agent: <span className="font-[JetBrains_Mono,monospace] font-semibold text-[#5C5C5C]">{flowDetail.coordinator}</span></span>}
                              {usesEntry(flowDetail.mode) && (flowDetail.entry || flowDetail.lead) && <span className="text-[11px] text-[#8A8A85]">起始 Agent: <span className="font-[JetBrains_Mono,monospace] font-semibold text-[#5C5C5C]">{flowDetail.entry || flowDetail.lead}</span></span>}
                              <span className="text-[11px] text-[#ABABAB]">{flowDetail.agents.length} agents · {flowDetail.stages.length} stages</span>
                              <button onClick={() => void openRunDialog(flow)} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-[#16a34a]/20 bg-[#16a34a]/8 px-3 py-1.5 text-[11px] font-semibold text-[#16a34a] transition-colors hover:bg-[#16a34a]/12">
                                <Play size={11} />配置并运行
                              </button>
                            </div>
                            {flow.name === 'research' && (
                              <div className="rounded-2xl border border-[#3D3BF3]/12 bg-[#3D3BF3]/[0.04] px-4 py-3 text-[12px] leading-6 text-[#5C5C5C]">
                                这个内置示例用于演示工作流式的完整链路：并行探索、基于结果的 fan-out 深挖，以及最后由协调 Agent 汇总为结构化简报。
                              </div>
                            )}
                            {flow.name === 'pair-review' && (
                              <div className="rounded-2xl border border-[#d97706]/15 bg-[#d97706]/[0.04] px-4 py-3 text-[12px] leading-6 text-[#6B4D1F]">
                                这个内置示例用于演示 Swarm 式的核心差异：不仅有起始 agent 发起任务，还要求安全与性能 reviewer 彼此直接发消息做交叉校验，再由发起该轮协作的 peer 汇总最终报告。
                              </div>
                            )}
                            {flow.name === 'supervised-review' && (
                              <div className="rounded-2xl border border-[#16a34a]/15 bg-[#16a34a]/[0.04] px-4 py-3 text-[12px] leading-6 text-[#355C3B]">
                                这个内置示例用于演示主管协作式：主管 Agent 接收任务、分派架构与风险专家，并以专家反馈为输入产出最终决策。
                              </div>
                            )}
                            {flowDetail.agents.length > 0 && (
                              <div className="flex flex-wrap gap-1.5">
                                {flowDetail.agents.map((agent) => (
                                  <span key={agent.name} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#F4F3F0] border border-[#E5E4E0] text-[11px] text-[#5C5C5C] font-medium">
                                    <Bot size={10} className="text-[#3D3BF3]" />{agent.name}
                                    {agent.extends && <span className="text-[#ABABAB]">← {agent.extends}</span>}
                                  </span>
                                ))}
                              </div>
                            )}
                            {flowDetail.stages.length > 0 && (
                              <div className="space-y-1">
                                {flowDetail.stages.map((stage) => (
                                  <div key={stage.id} className="flex items-center gap-2.5 px-3.5 py-2 rounded-lg bg-[#F4F3F0] text-[11px]">
                                    {stage.parallel ? <Zap size={10} className="text-[#d97706]" /> : <Layers size={10} className="text-[#D4D3CF]" />}
                                    <span className="font-[JetBrains_Mono,monospace] font-semibold text-[#5C5C5C]">{stage.id}</span>
                                    {stage.runs_on && <span className="px-1.5 py-0.5 rounded bg-[#3D3BF3]/8 text-[#3D3BF3] text-[9px] font-bold">{stage.runs_on}</span>}
                                    {stage.spawns.length > 0 && <span className="text-[#ABABAB]">{stage.spawns.length} spawn{stage.spawns.length > 1 ? 's' : ''}</span>}
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {tab === 'runs' && (
            <div className="space-y-4">
              <div className={cardStyle + ' px-5 py-4'}>
                <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                  <div>
                    <div className="text-[13px] font-bold text-[#0F0F0F]">运行监控</div>
                    <div className="mt-1 text-[11px] leading-5 text-[#8A8A85]">查看最近运行、进入成员看板，并跟踪实时事件流。</div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-3 xl:min-w-[520px]">
                    <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
                      <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">总运行数</div>
                      <div className="mt-1.5 text-[24px] font-semibold text-[#0F0F0F]">{runs.length}</div>
                    </div>
                    <div className="rounded-2xl border border-[#E5E4E0] bg-[#FFF7ED] px-4 py-3">
                      <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#C27A19]">进行中</div>
                      <div className="mt-1.5 text-[24px] font-semibold text-[#d97706]">{runs.filter((run) => !run.done).length}</div>
                    </div>
                    <div className="rounded-2xl border border-[#E5E4E0] bg-[#FEF2F2] px-4 py-3">
                      <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#B75A5A]">失败 / 取消</div>
                      <div className="mt-1.5 text-[24px] font-semibold text-[#dc2626]">{runs.filter((run) => run.status === 'failed' || run.status === 'cancelled').length}</div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1.25fr)] 2xl:grid-cols-[300px_minmax(0,1.45fr)]">
                <div className={cardStyle + ' overflow-hidden h-fit'}>
                  <div className="flex items-center justify-between px-4 py-3 border-b border-[#E5E4E0] bg-white">
                    <div>
                      <div className="text-[12px] font-bold text-[#0F0F0F]">运行记录</div>
                      <div className="text-[10px] text-[#8A8A85] mt-0.5">选择一条 run 查看详情与实时事件</div>
                    </div>
                    {activeRunId && <button onClick={() => { setActiveRunId(null); setRunEvents([]) }} className={btnGhost}>清空选择</button>}
                  </div>
                  <div className="max-h-[680px] overflow-y-auto p-3 space-y-2.5">
                    {runs.length === 0 ? (
                      <div className="rounded-2xl border border-dashed border-[#E5E4E0] bg-[#FAFAF8] px-4 py-12 text-center text-[12px] text-[#ABABAB]">
                        暂无运行记录 — 从“流程设计”发起一条运行吧。
                      </div>
                    ) : runs.map((run) => {
                      const tone = getRunTone(run)
                      const modeCfg = getFlowModeConfig(run.mode)
                      const ModeIcon = modeCfg.icon
                      return (
                        <button key={run.run_id} type="button" onClick={() => { setActiveRunId(run.run_id); setRunEvents([]) }}
                          className={cardStyle + ` w-full text-left px-4 py-3 transition-all ${
                            activeRunId === run.run_id ? '!border-[#3D3BF3]/35 ring-2 ring-[#3D3BF3]/10 shadow-md' : 'hover:shadow-md'
                          }`}>
                          <div className="flex items-start gap-3">
                            <span className={`mt-1 h-2.5 w-2.5 rounded-full ${tone.dot}`} />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-[12px] font-semibold text-[#0F0F0F] truncate">{run.flow}</span>
                                <span className={`inline-flex items-center gap-1 rounded-lg px-2 py-0.5 text-[9px] font-bold ${modeCfg.badge}`}>
                                  <ModeIcon size={9} />
                                  {modeCfg.label}
                                </span>
                              </div>
                              <div className="mt-1 font-[JetBrains_Mono,monospace] text-[10px] text-[#8A8A85] truncate">{run.run_id}</div>
                              <div className="mt-2 flex items-center justify-between gap-3">
                                <span className={`inline-flex rounded-lg px-2 py-0.5 text-[9px] font-bold ${tone.badge}`}>{getRunStatusLabel(run)}</span>
                                <span className="text-[10px] text-[#ABABAB] whitespace-nowrap">{formatRunTime(run.started_at)}</span>
                              </div>
                              <div className="mt-1 flex items-center justify-between gap-2">
                                <span className="text-[10px] text-[#ABABAB]">{formatRunElapsed(run)}</span>
                                {run.done && (
                                  <span
                                    role="button"
                                    tabIndex={0}
                                    onClick={(event) => {
                                      event.stopPropagation()
                                      void handleDeleteRun(run.run_id)
                                    }}
                                    onKeyDown={(event) => {
                                      if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault()
                                        event.stopPropagation()
                                        void handleDeleteRun(run.run_id)
                                      }
                                    }}
                                    className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-semibold text-[#ABABAB] transition-colors hover:bg-[#dc2626]/8 hover:text-[#dc2626]"
                                    title="删除运行记录"
                                  >
                                    {deletingRunId === run.run_id ? <RefreshCcw size={10} className="animate-spin" /> : <Trash2 size={10} />}
                                    删除
                                  </span>
                                )}
                              </div>
                              {run.error && <div className="mt-2 text-[10px] text-[#dc2626] line-clamp-2">{run.error}</div>}
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <div className="space-y-4">
                  {activeRun ? (
                    <>
                      <div className={cardStyle + ' overflow-hidden'}>
                        <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4 border-b border-[#E5E4E0] bg-[#0F0F0F]">
                          {(() => {
                            const modeCfg = getFlowModeConfig(activeRun.mode)
                            const ModeIcon = modeCfg.icon
                            return (
                          <div>
                            <div className="flex items-center gap-2.5 flex-wrap">
                              <h3 className="text-[14px] font-bold text-white">{activeRun.flow}</h3>
                              <span className={`inline-flex items-center gap-1 rounded-lg px-2 py-0.5 text-[9px] font-bold ${modeCfg.darkBadge}`}>
                                <ModeIcon size={9} />
                                {modeCfg.label}
                              </span>
                              <span className={`inline-flex rounded-lg px-2 py-0.5 text-[9px] font-bold ${getRunTone(activeRun).badge}`}>{getRunStatusLabel(activeRun)}</span>
                            </div>
                            <div className="mt-2 font-[JetBrains_Mono,monospace] text-[10px] text-[#8A8A85]">{activeRun.run_id}</div>
                          </div>
                            )
                          })()}
                          <div className="flex items-center gap-2">
                            <button onClick={() => activeRunId && void refreshRunDetail(activeRunId)} className={btnGhost + ' !text-[#8A8A85] hover:!bg-white/10 hover:!text-white'}>
                              {runDetailLoading ? '刷新中…' : '刷新详情'}
                            </button>
                            {!activeRun.done && (
                              <button onClick={() => void handleCancelActiveRun()} disabled={cancellingRun}
                                className="inline-flex items-center gap-1.5 rounded-xl border border-[#dc2626]/25 bg-[#dc2626]/10 px-3 py-2 text-[12px] font-semibold text-[#dc2626] transition-all hover:bg-[#dc2626]/14 disabled:cursor-not-allowed disabled:opacity-50">
                                {cancellingRun ? <RefreshCcw size={12} className="animate-spin" /> : <X size={12} />}
                                取消运行
                              </button>
                            )}
                            {activeRun.done && (
                              <button onClick={() => void handleDeleteRun(activeRun.run_id)} disabled={deletingRunId === activeRun.run_id}
                                className="inline-flex items-center gap-1.5 rounded-xl border border-[#dc2626]/25 bg-[#dc2626]/10 px-3 py-2 text-[12px] font-semibold text-[#dc2626] transition-all hover:bg-[#dc2626]/14 disabled:cursor-not-allowed disabled:opacity-50">
                                {deletingRunId === activeRun.run_id ? <RefreshCcw size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                删除记录
                              </button>
                            )}
                          </div>
                        </div>

                        <div className="border-b border-[#E5E4E0] bg-white px-5 py-3">
                          <div className="flex gap-1 overflow-x-auto">
                            {([
                              { key: 'overview' as const, icon: Activity, label: '总览' },
                              { key: 'board' as const, icon: LayoutGrid, label: '看板' },
                              { key: 'transcripts' as const, icon: Bot, label: '对话' },
                              { key: 'result' as const, icon: Layers, label: '结果' },
                              { key: 'events' as const, icon: List, label: '事件' },
                            ]).map(({ key, icon: Icon, label }) => (
                              <button
                                key={key}
                                type="button"
                                onClick={() => setRunDetailTab(key)}
                                className={`flex shrink-0 items-center gap-1.5 rounded-xl px-3 py-2 text-[12px] font-semibold transition-all ${
                                  runDetailTab === key
                                    ? 'bg-[#0F0F0F] text-white shadow-[0_1px_3px_rgba(0,0,0,0.16)]'
                                    : 'text-[#8A8A85] hover:bg-[#F4F3F0] hover:text-[#5C5C5C]'
                                }`}
                              >
                                <Icon size={13} />{label}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="p-5">
                          {runDetailTab === 'overview' && (
                            <div className="space-y-4">
                              <div className="grid gap-3 md:grid-cols-4">
                                <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">开始时间</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{formatRunTime(activeRun.started_at)}</div>
                                </div>
                                <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">耗时</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{formatRunElapsed(activeRun)}</div>
                                </div>
                                <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">最大轮数</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.max_turns || '默认'}</div>
                                </div>
                                <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">Walltime</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.walltime_seconds ? `${activeRun.walltime_seconds}s` : '未限制'}</div>
                                </div>
                              </div>

                              <div className="grid gap-3 md:grid-cols-4">
                                <button type="button" onClick={() => setRunDetailTab('board')} className="rounded-2xl border border-[#E5E4E0] bg-white px-4 py-3 text-left transition-all hover:border-[#3D3BF3]/25 hover:shadow-md">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">成员 Agent</div>
                                  <div className="mt-2 text-[22px] font-semibold text-[#0F0F0F]">{activeAgentStats.members}</div>
                                  <div className="mt-1 text-[10px] text-[#8A8A85]">{activeAgentStats.active} active · {activeAgentStats.done} done</div>
                                </button>
                                <button type="button" onClick={() => setRunDetailTab('board')} className="rounded-2xl border border-[#E5E4E0] bg-white px-4 py-3 text-left transition-all hover:border-[#d97706]/25 hover:shadow-md">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">工具调用</div>
                                  <div className="mt-2 text-[22px] font-semibold text-[#d97706]">{activeAgentStats.tools}</div>
                                  <div className="mt-1 text-[10px] text-[#8A8A85]">来自成员看板</div>
                                </button>
                                <button type="button" onClick={() => setRunDetailTab('events')} className="rounded-2xl border border-[#E5E4E0] bg-white px-4 py-3 text-left transition-all hover:border-[#16a34a]/25 hover:shadow-md">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">协作消息</div>
                                  <div className="mt-2 text-[22px] font-semibold text-[#16a34a]">{activeAgentStats.messages}</div>
                                  <div className="mt-1 text-[10px] text-[#8A8A85]">{runEvents.length} live events</div>
                                </button>
                                <button type="button" onClick={() => activeAgentStats.blocked > 0 ? setRunDetailTab('board') : setRunDetailTab('result')} className="rounded-2xl border border-[#E5E4E0] bg-white px-4 py-3 text-left transition-all hover:border-[#dc2626]/25 hover:shadow-md">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">异常</div>
                                  <div className={`mt-2 text-[22px] font-semibold ${activeAgentStats.blocked > 0 ? 'text-[#dc2626]' : 'text-[#0F0F0F]'}`}>{activeAgentStats.blocked}</div>
                                  <div className="mt-1 text-[10px] text-[#8A8A85]">{activeRun.error ? '运行失败' : '当前状态'}</div>
                                </button>
                              </div>

                              {(activeRun.task_preview || Object.keys(activeRun.vars).length > 0) && (
                            <div className="grid gap-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(260px,0.85fr)]">
                              <div className="rounded-2xl border border-[#E5E4E0] bg-white p-4">
                                <div className={sectionTitle + ' mb-3'}><Activity size={12} className="text-[#3D3BF3]" />任务摘要</div>
                                <p className="text-[12px] leading-6 text-[#5C5C5C]">{activeRun.task_preview || '该运行未提供额外任务描述。'}</p>
                              </div>
                              <div className="rounded-2xl border border-[#E5E4E0] bg-white p-4">
                                <div className={sectionTitle + ' mb-3'}><Settings2 size={12} className="text-[#3D3BF3]" />变量覆盖</div>
                                {Object.keys(activeRun.vars).length === 0 ? (
                                  <p className="text-[12px] text-[#ABABAB]">本次运行未覆盖变量。</p>
                                ) : (
                                  <div className="space-y-2">
                                    {Object.entries(activeRun.vars).map(([key, value]) => (
                                      <div key={key} className="flex items-start gap-3 rounded-xl bg-[#FAFAF8] px-3 py-2.5">
                                        <span className="font-[JetBrains_Mono,monospace] text-[10px] font-semibold text-[#3D3BF3]">{key}</span>
                                        <span className="min-w-0 flex-1 text-[11px] leading-5 text-[#5C5C5C] break-all">{value || '∅'}</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          )}

                              {activeRun.error && (
                            <div className="rounded-2xl border border-[#dc2626]/20 bg-[#dc2626]/6 p-4 text-[12px] text-[#b42318]">
                              <div className="flex items-center gap-2 font-semibold"><AlertCircle size={14} />运行失败</div>
                              <div className="mt-2 whitespace-pre-wrap break-words leading-6">{activeRun.error}</div>
                            </div>
                          )}

                              <div className="grid gap-3 md:grid-cols-3">
                                <button type="button" onClick={() => setRunDetailTab('board')} className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3 text-left transition-colors hover:bg-white">
                                  <div className={sectionTitle}><LayoutGrid size={12} className="text-[#3D3BF3]" />打开看板</div>
                                  <p className="mt-2 text-[11px] leading-5 text-[#8A8A85]">按状态查看每个成员 agent 的执行位置。</p>
                                </button>
                                <button type="button" onClick={() => setRunDetailTab('transcripts')} className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3 text-left transition-colors hover:bg-white">
                                  <div className={sectionTitle}><Bot size={12} className="text-[#3D3BF3]" />查看对话</div>
                                  <p className="mt-2 text-[11px] leading-5 text-[#8A8A85]">钻进单个 agent 的消息、收件内容和工具调用。</p>
                                </button>
                                <button type="button" onClick={() => setRunDetailTab('result')} className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3 text-left transition-colors hover:bg-white">
                                  <div className={sectionTitle}><Layers size={12} className="text-[#3D3BF3]" />查看结果</div>
                                  <p className="mt-2 text-[11px] leading-5 text-[#8A8A85]">查看多 Agent 运行的最终摘要。</p>
                                </button>
                              </div>
                            </div>
                          )}

                          {runDetailTab === 'board' && (
                            <div className="space-y-4">
                              <div className="flex flex-wrap items-center justify-between gap-3">
                                <div className={sectionTitle}><LayoutGrid size={12} className="text-[#3D3BF3]" />成员 Agent 状态看板</div>
                                <div className="flex items-center gap-3 text-[10px] text-[#8A8A85]">
                                  <span>{activeAgentStats.members} members</span>
                                  <span>{activeAgentStats.tools} tools</span>
                                  <span>{activeAgentStats.messages} messages</span>
                                </div>
                              </div>
                              <AgentOrchestrationBoard
                                cards={activeAgentCards}
                                selectedAgent={selectedAgentPanel}
                                runDone={Boolean(activeRun.done)}
                                onSelectAgent={(agent) => {
                                  setSelectedAgentPanel(agent)
                                  if (agentPanels[agent]) setRunDetailTab('transcripts')
                                }}
                              />
                            </div>
                          )}

                          {runDetailTab === 'transcripts' && (
                            <div className="space-y-4">
                              <div className="flex items-center justify-between gap-3">
                                <div className={sectionTitle}><Bot size={12} className="text-[#3D3BF3]" />子 Agent 运行窗口</div>
                                <button type="button" onClick={() => setRunDetailTab('board')} className={btnGhost}>返回看板</button>
                              </div>
                            <div className="grid gap-4 xl:grid-cols-[240px_minmax(0,1.35fr)] 2xl:grid-cols-[260px_minmax(0,1.5fr)]">
                              <div className="space-y-2">
                                {Object.keys(agentPanels).length > 0 ? Object.entries(agentPanels).map(([agentName, items]) => (
                                  (() => {
                                    const lastItem = items[items.length - 1]
                                    const lastPreview = !lastItem
                                      ? '等待输出…'
                                      : lastItem.type === 'tool'
                                        ? `${lastItem.tool_name} · ${lastItem.output_preview || lastItem.args_preview}`
                                        : lastItem.content_preview || '等待输出…'
                                    return (
                                  <button
                                    key={agentName}
                                    type="button"
                                    onClick={() => setSelectedAgentPanel(agentName)}
                                    className={`w-full rounded-2xl border px-3 py-3 text-left transition-all ${
                                      selectedAgentPanel === agentName
                                        ? 'border-[#3D3BF3]/30 bg-[#3D3BF3]/[0.05]'
                                        : 'border-[#E5E4E0] bg-[#FAFAF8] hover:bg-white'
                                    }`}
                                  >
                                    <div className="text-[12px] font-semibold text-[#0F0F0F]">{agentName}</div>
                                    <div className="mt-1 text-[10px] text-[#8A8A85]">{items.length} 条运行事件</div>
                                    <div className="mt-2 text-[11px] leading-5 text-[#5C5C5C] line-clamp-2">
                                      {lastPreview}
                                    </div>
                                  </button>
                                    )
                                  })()
                                )) : (
                                  <div className="rounded-2xl border border-dashed border-[#E5E4E0] bg-[#FAFAF8] px-4 py-10 text-center text-[12px] text-[#ABABAB]">
                                    运行开始后，这里会实时出现每个子 agent 的对话。
                                  </div>
                                )}
                              </div>
                              <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] p-4 min-h-[360px]">
                                {selectedAgentPanel && agentPanels[selectedAgentPanel] ? (
                                  <div className="space-y-3">
                                    <div className="flex items-center justify-between gap-3">
                                      <div>
                                        <div className="text-[13px] font-semibold text-[#0F0F0F]">{selectedAgentPanel}</div>
                                        <div className="text-[10px] text-[#8A8A85] mt-1">实时查看当前对话、收件内容和工具调用</div>
                                      </div>
                                      <span className="rounded-lg bg-white px-2 py-0.5 text-[9px] font-bold text-[#3D3BF3] border border-[#E5E4E0]">
                                        {agentPanels[selectedAgentPanel].length} events
                                      </span>
                                    </div>
                                    <div className="max-h-[520px] overflow-y-auto space-y-2 pr-1">
                                      {agentPanels[selectedAgentPanel].map((item, index) => (
                                        <div key={`${selectedAgentPanel}-${index}`} className={`rounded-2xl border px-3.5 py-3 ${
                                          item.type === 'tool'
                                            ? 'border-[#d97706]/20 bg-[#d97706]/[0.05]'
                                            : item.role === 'assistant'
                                              ? 'border-[#3D3BF3]/18 bg-white'
                                              : 'border-[#E5E4E0] bg-[#F4F3F0]'
                                        }`}>
                                          <div className="flex items-center gap-2 flex-wrap text-[10px]">
                                            <span className={`rounded-lg px-2 py-0.5 font-bold uppercase tracking-[0.08em] ${
                                              item.type === 'tool'
                                                ? 'bg-[#d97706]/10 text-[#d97706]'
                                                : item.role === 'assistant'
                                                  ? 'bg-[#3D3BF3]/10 text-[#3D3BF3]'
                                                  : 'bg-white text-[#8A8A85]'
                                            }`}>
                                              {item.type === 'tool' ? 'tool' : (item.kind || item.role)}
                                            </span>
                                            <span className="text-[#8A8A85]">turn {item.turn}</span>
                                            {item.stage_id && <span className="text-[#ABABAB]">{item.stage_id}</span>}
                                            {'spawn_index' in item && item.spawn_index != null && <span className="text-[#ABABAB]">#{item.spawn_index}</span>}
                                          </div>
                                          {item.type === 'tool' ? (
                                            <div className="mt-2 space-y-2">
                                              <div className="text-[11px] font-semibold text-[#0F0F0F]">{item.tool_name}</div>
                                              {item.args_preview && <div className="rounded-xl bg-white px-3 py-2 text-[11px] leading-5 text-[#5C5C5C] border border-[#E5E4E0] whitespace-pre-wrap break-words">{item.args_preview}</div>}
                                              {item.output_preview && <div className="text-[11px] leading-6 text-[#8A5A12] whitespace-pre-wrap break-words">{item.output_preview}</div>}
                                            </div>
                                          ) : (
                                            <div className="mt-2">
                                              {item.recipient && <div className="text-[10px] text-[#8A8A85] mb-1">关联对象：{item.recipient}</div>}
                                              <div className="text-[11px] leading-6 text-[#5C5C5C] whitespace-pre-wrap break-words">{item.content_preview || '（空内容）'}</div>
                                            </div>
                                          )}
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                ) : (
                                  <div className="h-full min-h-[320px] flex items-center justify-center text-[12px] text-[#ABABAB]">
                                    选择左侧一个子 agent，即可查看它当前的详细运行对话。
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                          )}

                          {runDetailTab === 'result' && isSwarmRunResult(activeRun.result) && (
                            <div className="space-y-4">
                              <div className={sectionTitle}><Users size={12} className="text-[#d97706]" />{activeRun.result.kind === 'hybrid' ? '主管协作式结果' : 'Swarm 式结果'}</div>
                              <div className="grid gap-3 md:grid-cols-5">
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">{activeRun.result.kind === 'hybrid' ? '主管 Agent' : '起始 Agent'}</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.entry || activeRun.result.lead || '—'}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">Peer 数</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.peer_count}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">协作消息</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.collaboration_count ?? activeRun.result.message_count}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">活跃 Peer</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.active_peer_count ?? activeRun.result.peers.length}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">终止原因</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{formatSwarmTerminationReason(activeRun.result.terminated_reason)}</div>
                                </div>
                              </div>
                              <div className="rounded-2xl border border-[#d97706]/15 bg-[#d97706]/[0.04] p-4">
                                <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#b45309]">最终汇总</div>
                                <p className="mt-2 whitespace-pre-wrap text-[12px] leading-6 text-[#6B4D1F]">{activeRun.result.entry_output || activeRun.result.lead_output || activeRun.result.entry_output_preview || activeRun.result.lead_output_preview || '起始 Agent 还没有留下最终汇总文本。'}</p>
                              </div>
                              <div className="grid gap-3 md:grid-cols-2">
                                <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] p-4">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">协作路径</div>
                                  <div className="mt-3 flex flex-wrap gap-2">
                                    {(activeRun.result.message_routes ?? []).length > 0 ? (
                                      (activeRun.result.message_routes ?? []).map((route) => (
                                        <span key={`${route.sender}-${route.recipient}`} className="rounded-xl border border-[#E5E4E0] bg-white px-2.5 py-1 text-[10px] font-medium text-[#5C5C5C]">
                                          {route.sender} → {route.recipient} · {route.count}
                                        </span>
                                      ))
                                    ) : (
                                      <span className="text-[11px] text-[#ABABAB]">还没有可见的消息往来。</span>
                                    )}
                                  </div>
                                </div>
                                <div className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] p-4">
                                  <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">最近协作</div>
                                  <div className="mt-3 space-y-2">
                                    {((activeRun.result.transcript ?? activeRun.result.recent_messages) ?? []).length > 0 ? (
                                      ((activeRun.result.transcript ?? activeRun.result.recent_messages) ?? []).map((message) => (
                                        <div key={message.seq} className="rounded-xl border border-[#E5E4E0] bg-white px-3 py-2">
                                          <div className="text-[10px] font-semibold text-[#0F0F0F]">{message.sender} → {message.recipient}</div>
                                          <div className="mt-1 whitespace-pre-wrap text-[11px] leading-5 text-[#5C5C5C]">{message.content || message.preview || '已发送一条团队消息。'}</div>
                                        </div>
                                      ))
                                    ) : (
                                      <div className="text-[11px] text-[#ABABAB]">这次运行没有留下可见的近期协作摘要。</div>
                                    )}
                                  </div>
                                </div>
                              </div>
                              <div className="grid gap-3 md:grid-cols-2">
                                {activeRun.result.peers.map((peer) => (
                                  <div key={peer.name} className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] p-4">
                                    <div className="flex items-center justify-between gap-3">
                                      <div>
                                        <div className="text-[12px] font-semibold text-[#0F0F0F]">{peer.name}</div>
                                        <div className="text-[10px] text-[#8A8A85] font-[JetBrains_Mono,monospace]">{peer.agent}</div>
                                      </div>
                                      <span className={`inline-flex rounded-lg px-2 py-0.5 text-[9px] font-bold ${peer.is_error ? 'bg-[#dc2626]/10 text-[#dc2626]' : 'bg-[#16a34a]/10 text-[#16a34a]'}`}>{peer.is_error ? '异常' : '完成'}</span>
                                    </div>
                                    <div className="mt-3 flex items-center gap-3 text-[10px] text-[#8A8A85]">
                                      <span>{getEffectivePeerTurns(peer)} turns</span>
                                      <span>{peer.tool_calls} tools</span>
                                      <span>{peer.sent_count ?? 0} sent</span>
                                      <span>{peer.received_count ?? 0} recv</span>
                                    </div>
                                    <p className="mt-3 whitespace-pre-wrap text-[11px] leading-6 text-[#5C5C5C]">{peer.output || getSwarmPeerNarrative(peer)}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {runDetailTab === 'result' && isCoordinatorRunResult(activeRun.result) && (
                            <div className="space-y-4">
                              <div className={sectionTitle}><Layers size={12} className="text-[#3D3BF3]" />编排/协作结果</div>
                              <div className="grid gap-3 md:grid-cols-4">
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">阶段数</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.stage_count}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">Spawn 总数</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.total_spawn_count}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">错误数</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.total_error_count}</div>
                                </div>
                                <div className="rounded-2xl bg-[#FAFAF8] px-4 py-3">
                                  <div className="text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">最后阶段</div>
                                  <div className="mt-2 text-[12px] font-semibold text-[#0F0F0F]">{activeRun.result.last_stage_id || '—'}</div>
                                </div>
                              </div>
                              <div className="rounded-2xl border border-[#3D3BF3]/12 bg-[#3D3BF3]/[0.04] p-4">
                                <div className="text-[10px] font-bold uppercase tracking-[0.08em] text-[#3D3BF3]">最终输出</div>
                                <p className="mt-2 whitespace-pre-wrap text-[12px] leading-6 text-[#5C5C5C]">{activeRun.result.last_output || activeRun.result.last_output_preview || '暂无最终输出摘要。'}</p>
                              </div>
                              <div className="space-y-2.5">
                                {activeRun.result.stages.map((stage) => (
                                  <div key={stage.stage_id} className="rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-4 py-3">
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <span className="font-[JetBrains_Mono,monospace] text-[11px] font-semibold text-[#0F0F0F]">{stage.stage_id}</span>
                                      {stage.coordinator_agent && <span className="rounded-lg bg-[#3D3BF3]/8 px-2 py-0.5 text-[9px] font-bold text-[#3D3BF3]">{stage.coordinator_agent}</span>}
                                      <span className={`rounded-lg px-2 py-0.5 text-[9px] font-bold ${stage.is_error ? 'bg-[#dc2626]/10 text-[#dc2626]' : 'bg-[#16a34a]/10 text-[#16a34a]'}`}>{stage.is_error ? '异常' : '正常'}</span>
                                    </div>
                                    <div className="mt-2 flex flex-wrap items-center gap-3 text-[10px] text-[#8A8A85]">
                                      <span>{stage.spawn_count} spawns</span>
                                      <span>{stage.ok_count} ok</span>
                                      <span>{stage.error_count} errors</span>
                                    </div>
                                    <p className="mt-2 whitespace-pre-wrap text-[11px] leading-6 text-[#5C5C5C]">{stage.output || stage.output_preview || '暂无阶段输出摘要。'}</p>
                                    {(stage.spawns ?? []).length > 0 && (
                                      <div className="mt-3 space-y-2">
                                        {(stage.spawns ?? []).map((spawn, index) => (
                                          <div key={`${stage.stage_id}-${spawn.agent}-${index}`} className="rounded-xl border border-[#E5E4E0] bg-white px-3 py-2">
                                            <div className="flex items-center justify-between gap-3">
                                              <div className="font-[JetBrains_Mono,monospace] text-[10px] font-semibold text-[#0F0F0F]">{spawn.agent}</div>
                                              <span className={`rounded-lg px-2 py-0.5 text-[9px] font-bold ${spawn.is_error ? 'bg-[#dc2626]/10 text-[#dc2626]' : 'bg-[#16a34a]/10 text-[#16a34a]'}`}>{spawn.is_error ? '异常' : '完成'}</span>
                                            </div>
                                            <div className="mt-1 text-[10px] leading-5 text-[#8A8A85]">{spawn.task}</div>
                                            <div className="mt-2 whitespace-pre-wrap text-[11px] leading-6 text-[#5C5C5C]">{spawn.output || spawn.output_preview || '暂无输出。'}</div>
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {runDetailTab === 'result' && !isSwarmRunResult(activeRun.result) && !isCoordinatorRunResult(activeRun.result) && !isGenericCancelledResult(activeRun.result) && (
                            <div className="rounded-2xl border border-dashed border-[#E5E4E0] bg-[#FAFAF8] px-5 py-12 text-center text-[12px] text-[#ABABAB]">
                              当前运行还没有可展示的结构化结果。
                            </div>
                          )}

                          {runDetailTab === 'result' && isGenericCancelledResult(activeRun.result) && (
                            <div className="rounded-2xl border border-[#d97706]/15 bg-[#d97706]/[0.04] p-4 text-[12px] text-[#8A8A85]">
                              该运行已被取消，当前只保留基础状态摘要，细粒度历史时间线仍依赖实时事件流。
                            </div>
                          )}

                          {runDetailTab === 'events' && (
                            <div className="space-y-3">
                              <div className="flex items-center gap-2.5">
                                <Activity size={13} className="text-[#3D3BF3]" />
                                <span className="text-[12px] font-bold text-[#0F0F0F]">事件时间线</span>
                                <span className="text-[10px] text-[#ABABAB] font-[JetBrains_Mono,monospace]">{activeRun.run_id.slice(0, 12)}</span>
                                {!activeRun.done && <Circle size={6} fill="currentColor" className="text-[#16a34a] animate-pulse" />}
                              </div>
                              {runEvents.length === 0 ? (
                                <div className="rounded-2xl border border-dashed border-[#E5E4E0] bg-[#FAFAF8] px-5 py-10 text-center text-[12px] text-[#ABABAB]">
                                  {activeRun.done
                                    ? '当前没有可回放的历史 SSE 事件。后续补齐持久化事件历史后，这里可以展示完整回放。'
                                    : '已连接事件流，等待新的编排事件到达…'}
                                </div>
                              ) : (
                                <div className="max-h-[620px] overflow-y-auto rounded-2xl border border-[#E5E4E0] bg-white divide-y divide-[#F4F3F0]">
                                  {runEvents.map((ev, index) => (
                                    <div key={`${ev.event}-${index}`} className="px-5 py-3.5 hover:bg-[#FAFAF8] transition-colors">
                                      <div className="flex items-start gap-3">
                                        <div className="mt-0.5 text-[10px] font-[JetBrains_Mono,monospace] text-[#ABABAB] tabular-nums whitespace-nowrap">
                                          {new Date(ev.time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                          <div className="flex flex-wrap items-center gap-2">
                                            <span className={`inline-flex rounded-md px-2 py-0.5 text-[9px] font-bold uppercase tracking-[0.08em] ${getEventTone(ev.event)}`}>{getEventLabel(ev.event)}</span>
                                            <span className="text-[12px] font-semibold text-[#0F0F0F]">{getEventHeadline(ev.event, ev.data)}</span>
                                          </div>
                                          <div className="mt-1 text-[11px] leading-6 text-[#6B6A78]">{getEventDetail(ev.event, ev.data)}</div>
                                        </div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className={cardStyle + ' flex min-h-[420px] items-center justify-center px-6 py-10 text-center'}>
                      <div className="max-w-md">
                        <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-[#3D3BF3]/8 text-[#3D3BF3]">
                          <Activity size={22} />
                        </div>
                        <h3 className="text-[15px] font-semibold text-[#0F0F0F]">选择一条运行记录</h3>
                        <p className="mt-2 text-[12px] leading-6 text-[#8A8A85]">右侧会展示 run 的总览、成员看板、结果和实时事件流。</p>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {runDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4 backdrop-blur-sm animate-fade-in">
          <div className="w-full max-w-2xl overflow-hidden rounded-[28px] border border-[#E5E4E0] bg-white shadow-[0_24px_80px_rgba(15,15,15,0.18)] animate-slide-up">
            <div className="border-b border-[#E5E4E0] bg-[#0F0F0F] px-6 py-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    {(() => {
                      const cfg = getFlowModeConfig(runDialog.detail.mode)
                      const Icon = cfg.icon
                      return (
                        <>
                          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-white/10 text-white">
                            <Icon size={14} />
                          </div>
                          <div>
                            <div className="text-[13px] font-bold text-white">启动 {runDialog.flowName}</div>
                            <div className="mt-1 text-[10px] uppercase tracking-[0.08em] text-[#8A8A85]">{cfg.label} · {runDialog.detail.agents.length} agents · {runDialog.detail.stages.length} stages</div>
                          </div>
                        </>
                      )
                    })()}
                  </div>
                  {runDialog.detail.mode === 'hybrid' && (
                    <p className="mt-3 text-[11px] leading-6 text-[#ABABAB]">主管协作式会把任务交给协调 Agent，由它通过 <code>send_message</code> 组织专家协同，最终优先展示主管输出。</p>
                  )}
                  {runDialog.detail.mode === 'swarm' && (
                    <p className="mt-3 text-[11px] leading-6 text-[#ABABAB]">Swarm 式是去中心化的点对点协作。起始 Agent（如已指定）只负责接收初始任务；其他 peer 通过 <code>send_message</code> 在运行时自由 handoff。</p>
                  )}
                </div>
                <button onClick={() => setRunDialog(null)} className="rounded-xl p-2 text-[#8A8A85] transition-colors hover:bg-white/10 hover:text-white">
                  <X size={14} />
                </button>
              </div>
            </div>

            <div className="max-h-[70vh] overflow-y-auto px-6 py-5 space-y-5 bg-[#FAFAF8]">
              {runDialog.flowName === 'research' ? (
                <ResearchRunPreset runDialog={runDialog} setRunDialog={setRunDialog} />
              ) : runDialog.flowName === 'supervised-review' ? (
                <SupervisedReviewRunPreset runDialog={runDialog} setRunDialog={setRunDialog} />
              ) : runDialog.flowName === 'pair-review' ? (
                <PairReviewRunPreset runDialog={runDialog} setRunDialog={setRunDialog} />
              ) : (
                <>
                  {(runDialog.detail.mode === 'swarm' || runDialog.detail.mode === 'hybrid') && (
                    <div className={cardStyle + ' p-4'}>
                      <div className={sectionTitle + ' mb-3'}><Users size={12} className="text-[#d97706]" />任务描述</div>
                      <textarea
                        value={runDialog.task}
                        onChange={(e) => setRunDialog((prev) => prev ? { ...prev, task: e.target.value } : prev)}
                        rows={4}
                        placeholder={runDialog.detail.mode === 'hybrid'
                          ? '例如：请由主管组织安全、性能和可维护性专家协作审查最近改动，并综合风险、建议与优先级。'
                          : '例如：请以 code review 团队的方式审查最近的 orchestration 持久化改动，输出风险、建议与优先级。'}
                        className={inputBase + ' min-h-[120px] resize-y leading-6'}
                      />
                    </div>
                  )}

                  <div className="grid gap-4 md:grid-cols-[minmax(0,1.1fr)_minmax(240px,0.9fr)]">
                    <div className={cardStyle + ' p-4'}>
                      <div className={sectionTitle + ' mb-3'}><Bot size={12} className="text-[#3D3BF3]" />参与 Agent</div>
                      <div className="flex flex-wrap gap-1.5">
                        {runDialog.detail.agents.map((agent) => (
                          <span key={agent.name} className="inline-flex items-center gap-1.5 rounded-lg border border-[#E5E4E0] bg-[#FAFAF8] px-2.5 py-1 text-[11px] text-[#5C5C5C]">
                            <Bot size={10} className="text-[#3D3BF3]" />
                            {agent.name}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div className={cardStyle + ' p-4'}>
                      <div className={sectionTitle + ' mb-3'}><Layers size={12} className="text-[#3D3BF3]" />执行结构</div>
                      <div className="space-y-2 text-[11px] text-[#5C5C5C]">
                        <div className="flex items-center justify-between"><span>模式</span><span className="font-semibold text-[#0F0F0F]">{runDialog.detail.mode}</span></div>
                        <div className="flex items-center justify-between"><span>{runDialog.detail.mode === 'hybrid' ? '主管 Agent' : '起始 Agent'}</span><span className="font-semibold text-[#0F0F0F]">{runDialog.detail.mode === 'hybrid' ? runDialog.detail.coordinator || runDialog.detail.entry || runDialog.detail.lead || '—' : runDialog.detail.entry || runDialog.detail.lead || '—'}</span></div>
                        <div className="flex items-center justify-between"><span>Stages</span><span className="font-semibold text-[#0F0F0F]">{runDialog.detail.stages.length}</span></div>
                        <div className="flex items-center justify-between"><span>默认变量</span><span className="font-semibold text-[#0F0F0F]">{Object.keys(runDialog.detail.vars).length}</span></div>
                      </div>
                    </div>
                  </div>

                  <div className={cardStyle + ' p-4'}>
                    <div className={sectionTitle + ' mb-3'}><Settings2 size={12} className="text-[#3D3BF3]" />变量覆盖</div>
                    {Object.keys(runDialog.detail.vars).length === 0 ? (
                      <p className="text-[12px] text-[#ABABAB]">该 flow 没有声明可覆盖变量，启动时会直接使用默认配置。</p>
                    ) : (
                      <div className="grid gap-3 md:grid-cols-2">
                        {Object.entries(runDialog.detail.vars).map(([key, defaultValue]) => (
                          <label key={key} className="block rounded-2xl border border-[#E5E4E0] bg-[#FAFAF8] px-3.5 py-3">
                            <span className="mb-2 block text-[10px] font-bold uppercase tracking-[0.08em] text-[#8A8A85]">{key}</span>
                            <input
                              value={runDialog.vars[key] ?? defaultValue}
                              onChange={(e) => setRunDialog((prev) => prev ? {
                                ...prev,
                                vars: { ...prev.vars, [key]: e.target.value },
                              } : prev)}
                              className={inputMono}
                            />
                          </label>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-[#E5E4E0] bg-white px-6 py-4">
              <button onClick={() => setRunDialog(null)} className={btnGhost}>取消</button>
              <button onClick={() => void handleStartRun()} disabled={startingRun || ((runDialog.detail.mode === 'swarm' || runDialog.detail.mode === 'hybrid') && !runDialog.task.trim())} className={btnPrimary}>
                {startingRun ? <RefreshCcw size={12} className="animate-spin" /> : <Play size={12} />}
                <span>{startingRun ? '启动中…' : '启动运行'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
