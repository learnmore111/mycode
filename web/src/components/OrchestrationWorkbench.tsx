import { useCallback, useEffect, useRef, useState } from 'react'
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
} from 'lucide-react'
import {
  listFlows, getFlow, listOrchestrationAgents,
  createAgent, updateAgent, deleteAgent,
  createFlow, updateFlow, deleteFlow,
  startRun, listRuns,
} from '../api/orchestration'
import type {
  FlowInfo, FlowDetail, OrchestrationAgent,
  AgentCreateParams, FlowCreateParams, RunStatus,
} from '../api/orchestration'

interface Props { onBack: () => void }
type Tab = 'agents' | 'flows' | 'runs'

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

const COMMON_TOOLS = [
  'read', 'write', 'edit', 'grep', 'glob', 'listdir', 'bash',
  'webfetch', 'websearch', 'task', 'send_message', 'apply_patch',
]

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
  const [tools, setTools] = useState<string[]>(initial?.tools ?? [])
  const [prompt, setPrompt] = useState(initial?.prompt ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [temp, setTemp] = useState<string>(initial?.temperature != null ? String(initial.temperature) : '')
  const [maxTurns, setMaxTurns] = useState<string>(initial?.max_turns != null ? String(initial.max_turns) : '')
  const [isolation, setIsolation] = useState(initial?.isolation ?? 'none')
  const [omitClaude, setOmitClaude] = useState(initial?.omit_claudemd ?? false)
  const [scope, setScope] = useState(initial?.scope ?? 'project')

  const toggleTool = (t: string) => setTools((p) => p.includes(t) ? p.filter((x) => x !== t) : [...p, t])

  const handleSave = () => {
    onSave({
      name: name.trim(), description: desc || undefined, extends: ext || undefined,
      role: role || undefined, mode, tools: tools.length > 0 ? tools : undefined,
      prompt: prompt || undefined, model: model || undefined,
      temperature: temp ? parseFloat(temp) : undefined,
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
                  options={['coordinator', 'worker', 'teammate', 'lead', 'fork'].map((r) => ({ value: r, label: r }))} mono />
              </div>
              <div>
                <label className={labelStyle}>模式</label>
                <Select value={mode} onChange={setMode}
                  options={['all', 'primary', 'subagent'].map((m) => ({ value: m, label: m }))} mono />
              </div>
            </div>
          </div>

          {/* Tools */}
          <div className={cardStyle + ' p-5'}>
            <div className={sectionTitle + ' mb-3'}><Shield size={12} className="text-[#3D3BF3]" />工具白名单</div>
            <div className="flex flex-wrap gap-1.5">
              {COMMON_TOOLS.map((t) => (
                <button key={t} onClick={() => toggleTool(t)}
                  className={`px-3 py-1.5 rounded-lg text-[11px] font-[JetBrains_Mono,monospace] font-semibold transition-all border ${
                    tools.includes(t) ? pillActive : pillInactive
                  }`}>{t}</button>
              ))}
            </div>
            <p className="text-[10px] text-[#ABABAB] mt-2">不选则使用默认工具集。选中的工具构成 Agent 的能力边界。</p>
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

// ══════════════════════════════════════════════════════════════
// FLOW EDITOR
// ══════════════════════════════════════════════════════════════
function FlowEditor({ initial, allAgents, onSave, onCancel, saving }: {
  initial?: FlowDetail & { scope?: string }; allAgents: OrchestrationAgent[]
  onSave: (p: FlowCreateParams, isNew: boolean) => void; onCancel: () => void; saving: boolean
}) {
  const isNew = !initial
  const [name, setName] = useState(initial?.name ?? '')
  const [desc, setDesc] = useState('')
  const [mode, setMode] = useState<string>(initial?.mode ?? 'coordinator')
  const [lead, setLead] = useState(initial?.lead ?? '')
  const [scope, setScope] = useState('project')
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

  const addAgent = () => setAgents((p) => [...p, { name: '', extends: '', role: '', prompt: '' }])
  const removeAgent = (i: number) => setAgents((p) => p.filter((_, idx) => idx !== i))
  const updateAgentField = (i: number, field: string, val: string) =>
    setAgents((p) => p.map((a, idx) => idx === i ? { ...a, [field]: val } : a))
  const addStage = () => setStages((p) => [...p, { id: '', parallel: false, runs_on: '', depends_on: '', inputs: '', spawns: [{ agent: '', task: '' }] }])
  const removeStage = (i: number) => setStages((p) => p.filter((_, idx) => idx !== i))
  const addSpawn = (si: number) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: [...s.spawns, { agent: '', task: '' }] } : s))
  const removeSpawn = (si: number, spi: number) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: s.spawns.filter((_, j) => j !== spi) } : s))

  const agentNames = agents.filter((a) => a.name.trim()).map((a) => a.name.trim())

  const handleSave = () => {
    const varsObj: Record<string, string> = {}
    vars.forEach((v) => { if (v.key.trim()) varsObj[v.key.trim()] = v.value })
    const agentSpecs = agents.filter((a) => a.name.trim()).map((a) => {
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
    onSave({ name: name.trim(), description: desc || undefined, mode, lead: lead || undefined,
      agents: agentSpecs.length > 0 ? agentSpecs : undefined, stages: stageSpecs.length > 0 ? stageSpecs : undefined,
      vars: Object.keys(varsObj).length > 0 ? varsObj : undefined, scope }, isNew)
  }

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
          <button onClick={handleSave} disabled={saving || !name.trim() || agents.length === 0} className={btnPrimary}>
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
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className={labelStyle}>名称 *</label>
                <input value={name} onChange={(e) => setName(e.target.value)} disabled={!isNew} placeholder="my-flow" className={inputMono + ' disabled:opacity-40'} />
              </div>
              <div>
                <label className={labelStyle}>模式 *</label>
                <div className="flex gap-1.5">
                  {([{ v: 'coordinator', l: 'Coordinator', i: Network }, { v: 'swarm', l: 'Swarm', i: Users }] as const).map(({ v, l, i: I }) => (
                    <button key={v} onClick={() => setMode(v)}
                      className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[11px] font-semibold transition-all border ${mode === v ? pillActive : pillInactive}`}>
                      <I size={12} />{l}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className={labelStyle}>{mode === 'swarm' ? 'Lead Agent' : '作用域'}</label>
                {mode === 'swarm' ? (
                  <Select value={lead} onChange={setLead} placeholder="选择 Lead..."
                    options={agentNames.map((n) => ({ value: n, label: n }))} mono />
                ) : (
                  <div className="flex gap-2">
                    {(['project', 'global'] as const).map((s) => (
                      <button key={s} onClick={() => setScope(s)} className={`flex-1 px-3 py-2 rounded-lg text-[12px] font-semibold transition-all border ${scope === s ? pillActive : pillInactive}`}>
                        {s === 'project' ? '项目级' : '全局'}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {/* Description */}
            <div className="mt-4">
              <label className={labelStyle}>描述</label>
              <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="编排流描述" className={inputBase} />
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
                  <div className="flex items-center gap-2 mb-2.5">
                    <GripVertical size={12} className="text-[#D4D3CF] cursor-grab" />
                    <input value={agent.name} onChange={(e) => updateAgentField(i, 'name', e.target.value)} placeholder="agent-name"
                      className="flex-1 bg-white rounded-lg px-2.5 py-1.5 text-[12px] text-[#1A1A1A] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace] transition-all" />
                    <Select value={agent.extends} onChange={(v) => updateAgentField(i, 'extends', v)} placeholder="无继承" className="w-28"
                      options={[...allAgents.map((a) => a.name), ...agentNames.filter((n) => n !== agent.name)].filter((v, j, arr) => arr.indexOf(v) === j).map((n) => ({ value: n, label: n }))} mono />
                    <Select value={agent.role} onChange={(v) => updateAgentField(i, 'role', v)} placeholder="无角色" className="w-24"
                      options={['coordinator', 'worker', 'teammate', 'lead'].map((r) => ({ value: r, label: r }))} mono />
                    <button onClick={() => removeAgent(i)} className="p-1.5 rounded-lg text-[#ABABAB] hover:text-[#dc2626] hover:bg-[#dc2626]/8 transition-colors">
                      <Trash2 size={12} />
                    </button>
                  </div>
                  <textarea value={agent.prompt} onChange={(e) => updateAgentField(i, 'prompt', e.target.value)}
                    placeholder="Agent 提示词（可选）…" rows={2}
                    className="w-full bg-white rounded-lg px-2.5 py-1.5 text-[11px] text-[#1A1A1A] placeholder:text-[#ABABAB] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 resize-none font-[JetBrains_Mono,monospace] transition-all" />
                </div>
              ))}
              {agents.length === 0 && <div className="text-center py-8 text-[13px] text-[#ABABAB]">点击 "添加" 按钮来添加参与的 Agent</div>}
            </div>
          </div>

          {/* Stages (coordinator) */}
          {mode === 'coordinator' && (
            <div className={cardStyle + ' p-5'}>
              <div className="flex items-center justify-between mb-4">
                <div className={sectionTitle}><Layers size={12} className="text-[#3D3BF3]" />执行阶段<span className="text-[#ABABAB] font-normal ml-1">({stages.length})</span></div>
                <button onClick={addStage} className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#3D3BF3]/8 text-[#3D3BF3] text-[11px] font-semibold hover:bg-[#3D3BF3]/15 transition-colors">
                  <Plus size={11} />添加
                </button>
              </div>
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
                          <Select value={sp.agent} onChange={(v) => setStages((p) => p.map((s, idx) => idx === si ? { ...s, spawns: s.spawns.map((x, j) => j === spi ? { ...x, agent: v } : x) } : s))}
                            placeholder="Agent" className="w-28" mono
                            options={agentNames.map((n) => ({ value: n, label: n }))} />
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
  const [runEvents, setRunEvents] = useState<Array<{ event: string; data: Record<string, unknown>; time: number }>>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  const fire = (type: 'success' | 'error', msg: string) => { setToast({ type, msg }); setTimeout(() => setToast(null), 3000) }

  const refresh = useCallback(async () => {
    setLoading(true)
    try { const [a, f, r] = await Promise.all([listOrchestrationAgents(), listFlows(), listRuns()]); setAgents(a); setFlows(f); setRuns(r) }
    catch { /* */ } finally { setLoading(false) }
  }, [])

  useEffect(() => { refresh() }, [refresh])
  useEffect(() => { if (tab !== 'runs') return; const id = setInterval(async () => { try { setRuns(await listRuns()) } catch {} }, 3000); return () => clearInterval(id) }, [tab])

  useEffect(() => {
    if (!activeRunId) return
    const es = new EventSource(`/orchestration/events?run_id=${activeRunId}`)
    eventSourceRef.current = es
    const handler = (e: MessageEvent) => { try { const d = JSON.parse(e.data); setRunEvents((p) => [...p, { event: e.type || 'event', data: d, time: Date.now() }]) } catch {} }
    es.onmessage = handler
    const types = ['orchestration.flow.started','orchestration.flow.finished','orchestration.stage.started','orchestration.stage.finished','orchestration.spawn.started','orchestration.spawn.finished','orchestration.message.sent','orchestration.swarm.started','orchestration.swarm.finished']
    types.forEach((t) => es.addEventListener(t, (ev) => { const me = ev as MessageEvent; try { const d = JSON.parse(me.data); setRunEvents((p) => [...p, { event: t, data: d, time: Date.now() }]) } catch {} }))
    return () => { es.close(); eventSourceRef.current = null }
  }, [activeRunId])

  const handleSaveAgent = async (params: AgentCreateParams, isNew: boolean) => {
    setSaving(true)
    try { if (isNew) await createAgent(params); else await updateAgent(params.name, params); fire('success', isNew ? `Agent "${params.name}" 已创建` : `已更新`); setEditingAgent(null); refresh() }
    catch (err) { fire('error', `保存失败: ${err}`) } finally { setSaving(false) }
  }
  const handleDeleteAgent = async (name: string, source: string) => {
    if (!confirm(`确定删除 Agent "${name}"？`)) return
    try { await deleteAgent(name, source === 'global' ? 'global' : 'project'); fire('success', `已删除`); refresh() }
    catch (err) { fire('error', `删除失败: ${err}`) }
  }
  const handleSaveFlow = async (params: FlowCreateParams, isNew: boolean) => {
    setSaving(true)
    try { if (isNew) await createFlow(params); else await updateFlow(params.name, params); fire('success', isNew ? `编排流 "${params.name}" 已创建` : `已更新`); setEditingFlow(null); refresh() }
    catch (err) { fire('error', `保存失败: ${err}`) } finally { setSaving(false) }
  }
  const handleDeleteFlow = async (name: string, source: string) => {
    if (!confirm(`确定删除编排流 "${name}"？`)) return
    try { await deleteFlow(name, source === 'global' ? 'global' : 'project'); fire('success', `已删除`); refresh() }
    catch (err) { fire('error', `删除失败: ${err}`) }
  }
  const handleRunFlow = async (flowName: string, flowMode: string) => {
    try {
      const params: Parameters<typeof startRun>[0] = { flow: flowName }
      if (flowMode === 'swarm') { fire('error', 'Swarm 需要任务描述（TODO: 弹窗输入）'); return }
      const result = await startRun(params)
      fire('success', `运行 ${result.run_id.slice(0, 8)}… 已启动`)
      setActiveRunId(result.run_id); setRunEvents([]); setTab('runs'); refresh()
    } catch (err) { fire('error', `启动失败: ${err}`) }
  }
  const handleViewFlow = async (name: string) => {
    if (expandedFlow === name) { setExpandedFlow(null); setFlowDetail(null); return }
    setExpandedFlow(name); try { setFlowDetail(await getFlow(name)) } catch { setFlowDetail(null) }
  }

  // ── Editor modes ──
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

  // ── Main dashboard ──
  return (
    <div className="flex-1 flex flex-col bg-[#FAFAF8] min-h-0">
      <Toast t={toast} onClose={() => setToast(null)} />

      {/* Command bar — dark header */}
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
        {/* Stats */}
        <div className="flex items-center gap-4 text-[11px]">
          <span className="text-[#8A8A85]"><span className="font-bold text-white">{agents.length}</span> Agents</span>
          <span className="text-[#8A8A85]"><span className="font-bold text-white">{flows.length}</span> Flows</span>
          {runs.filter((r) => !r.done).length > 0 && (
            <span className="flex items-center gap-1.5 text-[#16a34a]">
              <Circle size={6} fill="currentColor" className="animate-pulse" />
              <span className="font-bold">{runs.filter((r) => !r.done).length}</span> 运行中
            </span>
          )}
        </div>
        <button onClick={refresh} className="p-1.5 rounded-lg text-[#8A8A85] hover:bg-white/8 hover:text-white transition-colors" title="刷新">
          <RefreshCcw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Tab bar */}
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

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-4xl mx-auto px-5 py-5">

          {/* ── Agents ── */}
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
                            <button onClick={() => setEditingAgent({ name: agent.name, description: agent.description, extends: agent.extends, mode: agent.mode, scope: agent.source })}
                              className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#F4F3F0] hover:text-[#5C5C5C] transition-colors" title="编辑"><Edit3 size={13} /></button>
                            <button onClick={() => handleDeleteAgent(agent.name, agent.source)}
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

          {/* ── Flows ── */}
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
                  return (
                    <div key={flow.name} className={cardStyle + ` overflow-hidden ${isExp ? 'shadow-md' : 'hover:shadow-md'} transition-shadow`}>
                      <div className="flex items-center gap-4 px-5 py-3.5 cursor-pointer group" onClick={() => handleViewFlow(flow.name)}>
                        <div className="flex-shrink-0">{isExp ? <ChevronDown size={14} className="text-[#8A8A85]" /> : <ChevronRight size={14} className="text-[#D4D3CF]" />}</div>
                        <div className="w-9 h-9 rounded-xl bg-[#3D3BF3]/8 flex items-center justify-center flex-shrink-0"><Workflow size={16} className="text-[#3D3BF3]" /></div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2.5"><span className="text-[13px] font-semibold text-[#0F0F0F]">{flow.name}</span><SourceBadge source={flow.source} /></div>
                        </div>
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
                          <button onClick={() => { if (flowDetail && isExp) handleRunFlow(flow.name, flowDetail.mode) }}
                            className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#16a34a]/8 hover:text-[#16a34a] transition-colors" title="运行"><Play size={13} /></button>
                          {canEdit && (
                            <>
                              <button onClick={async () => { const d = isExp && flowDetail ? flowDetail : await getFlow(flow.name); setEditingFlow({ ...d, scope: flow.source }) }}
                                className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#F4F3F0] hover:text-[#5C5C5C] transition-colors" title="编辑"><Edit3 size={13} /></button>
                              <button onClick={() => handleDeleteFlow(flow.name, flow.source)}
                                className="p-2 rounded-lg text-[#ABABAB] hover:bg-[#dc2626]/8 hover:text-[#dc2626] transition-colors" title="删除"><Trash2 size={13} /></button>
                            </>
                          )}
                        </div>
                      </div>
                      {isExp && flowDetail && (
                        <div className="px-5 pb-4 border-t border-[#F4F3F0]">
                          <div className="mt-3.5 space-y-3">
                            <div className="flex items-center gap-2.5 flex-wrap">
                              <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase tracking-wider ${
                                flowDetail.mode === 'coordinator' ? 'bg-[#3D3BF3]/8 text-[#3D3BF3]' : 'bg-[#d97706]/8 text-[#d97706]'
                              }`}>{flowDetail.mode === 'coordinator' ? <Network size={10} /> : <Users size={10} />}{flowDetail.mode}</span>
                              {flowDetail.lead && <span className="text-[11px] text-[#8A8A85]">Lead: <span className="font-[JetBrains_Mono,monospace] font-semibold text-[#5C5C5C]">{flowDetail.lead}</span></span>}
                              <span className="text-[11px] text-[#ABABAB]">{flowDetail.agents.length} agents · {flowDetail.stages.length} stages</span>
                            </div>
                            {flowDetail.agents.length > 0 && (
                              <div className="flex flex-wrap gap-1.5">
                                {flowDetail.agents.map((a) => (
                                  <span key={a.name} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#F4F3F0] border border-[#E5E4E0] text-[11px] text-[#5C5C5C] font-medium">
                                    <Bot size={10} className="text-[#3D3BF3]" />{a.name}
                                    {a.extends && <span className="text-[#ABABAB]">← {a.extends}</span>}
                                  </span>
                                ))}
                              </div>
                            )}
                            {flowDetail.stages.length > 0 && (
                              <div className="space-y-1">
                                {flowDetail.stages.map((s) => (
                                  <div key={s.id} className="flex items-center gap-2.5 px-3.5 py-2 rounded-lg bg-[#F4F3F0] text-[11px]">
                                    {s.parallel ? <Zap size={10} className="text-[#d97706]" /> : <Layers size={10} className="text-[#D4D3CF]" />}
                                    <span className="font-[JetBrains_Mono,monospace] font-semibold text-[#5C5C5C]">{s.id}</span>
                                    {s.runs_on && <span className="px-1.5 py-0.5 rounded bg-[#3D3BF3]/8 text-[#3D3BF3] text-[9px] font-bold">{s.runs_on}</span>}
                                    {s.spawns.length > 0 && <span className="text-[#ABABAB]">{s.spawns.length} spawn{s.spawns.length > 1 ? 's' : ''}</span>}
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

          {/* ── Runs ── */}
          {tab === 'runs' && (
            <div>
              <div className="flex items-center justify-between mb-5">
                <p className="text-[12px] text-[#8A8A85]">{runs.length} 个运行记录</p>
                {activeRunId && <button onClick={() => { setActiveRunId(null); setRunEvents([]) }} className="text-[11px] text-[#8A8A85] hover:text-[#5C5C5C] transition-colors">清除事件流</button>}
              </div>

              {/* Live events */}
              {activeRunId && runEvents.length > 0 && (
                <div className={cardStyle + ' overflow-hidden mb-5'}>
                  <div className="px-5 py-3 border-b border-[#E5E4E0] bg-[#0F0F0F] flex items-center gap-2.5">
                    <Activity size={13} className="text-[#3D3BF3]" />
                    <span className="text-[12px] font-bold text-white">实时事件</span>
                    <span className="text-[10px] text-[#8A8A85] font-[JetBrains_Mono,monospace]">{activeRunId.slice(0, 12)}</span>
                    <Circle size={6} fill="#16a34a" className="text-[#16a34a] animate-pulse" />
                  </div>
                  <div className="max-h-72 overflow-y-auto divide-y divide-[#F4F3F0]">
                    {runEvents.map((ev, i) => (
                      <div key={i} className="px-5 py-2.5 flex items-start gap-3.5 text-[12px] hover:bg-[#FAFAF8] transition-colors">
                        <span className="text-[10px] text-[#ABABAB] font-[JetBrains_Mono,monospace] mt-0.5 flex-shrink-0 tabular-nums">
                          {new Date(ev.time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                        </span>
                        <span className={`px-2 py-0.5 rounded-md text-[9px] font-bold uppercase tracking-wider flex-shrink-0 ${
                          ev.event.includes('started') ? 'bg-[#3D3BF3]/8 text-[#3D3BF3]' :
                          ev.event.includes('finished') ? 'bg-[#16a34a]/8 text-[#16a34a]' :
                          ev.event.includes('message') ? 'bg-[#d97706]/8 text-[#d97706]' :
                          'bg-[#F4F3F0] text-[#8A8A85]'
                        }`}>{ev.event.replace('orchestration.', '')}</span>
                        <div className="flex-1 min-w-0 text-[#5C5C5C]">
                          {ev.data.agent && <span className="font-[JetBrains_Mono,monospace] font-semibold text-[#0F0F0F]">{String(ev.data.agent)} </span>}
                          {ev.data.stage_id && <span className="text-[#8A8A85]">stage:{String(ev.data.stage_id)} </span>}
                          {ev.data.task_preview && <span className="text-[#ABABAB] truncate">{String(ev.data.task_preview).slice(0, 80)}</span>}
                          {ev.data.output_preview && <span className="text-[#ABABAB] truncate">{String(ev.data.output_preview).slice(0, 80)}</span>}
                          {ev.data.terminated_reason && <span className="text-[#d97706]">终止: {String(ev.data.terminated_reason)}</span>}
                          {ev.data.ok === false && <span className="text-[#dc2626] font-semibold">失败</span>}
                          {ev.data.duration_seconds != null && <span className="text-[#ABABAB] ml-1">{Number(ev.data.duration_seconds).toFixed(1)}s</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid gap-2.5">
                {runs.length === 0 ? (
                  <div className="text-center py-16 text-[13px] text-[#ABABAB]">暂无运行记录 — 从 "流程设计" 启动运行</div>
                ) : runs.map((run) => (
                  <div key={run.run_id} onClick={() => { setActiveRunId(run.run_id); setRunEvents([]) }}
                    className={cardStyle + ` flex items-center gap-4 px-5 py-3.5 cursor-pointer transition-all ${
                      activeRunId === run.run_id ? '!border-[#3D3BF3]/30 !shadow-md ring-2 ring-[#3D3BF3]/8' : 'hover:shadow-md'
                    }`}>
                    <div className={`w-3 h-3 rounded-full flex-shrink-0 ${
                      run.cancelled ? 'bg-[#dc2626]' : run.done ? 'bg-[#16a34a]' : 'bg-[#d97706] animate-pulse'
                    }`} />
                    <span className="text-[13px] font-[JetBrains_Mono,monospace] font-semibold text-[#0F0F0F] flex-1">{run.run_id}</span>
                    <span className={`px-2.5 py-1 rounded-lg text-[10px] font-bold ${
                      run.cancelled ? 'bg-[#dc2626]/8 text-[#dc2626]' : run.done ? 'bg-[#16a34a]/8 text-[#16a34a]' : 'bg-[#d97706]/8 text-[#d97706]'
                    }`}>{run.cancelled ? '已取消' : run.done ? '已完成' : '运行中'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
