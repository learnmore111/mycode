import { useCallback, useEffect, useState } from 'react'
import {
  RefreshCcw,
  Network,
  Play,
  ChevronDown,
  ChevronRight,
  Users,
  Layers,
  Bot,
  ArrowRight,
  GitBranch,
  Workflow,
  Zap,
  CircleDot,
  Check,
  AlertCircle,
  X,
  Settings2,
  Activity,
} from 'lucide-react'
import {
  listFlows,
  getFlow,
  listOrchestrationAgents,
  startRun,
  listRuns,
} from '../api/orchestration'
import type {
  FlowInfo,
  FlowDetail,
  OrchestrationAgent,
  RunInfo,
  RunStatus,
} from '../api/orchestration'

interface Props {
  loading: boolean
  onRefresh: () => void
  /** Bumped by the parent whenever an external action (e.g. a Workbench
   * create/update/delete) mutates flows or agents.  The sidebar listens
   * and re-fetches its own lists so the UI stays in sync. */
  refreshToken?: number
}

type ActiveView = 'flows' | 'agents' | 'runs'

const MODE_CONFIG: Record<string, { icon: typeof Network; label: string; color: string }> = {
  coordinator: { icon: Network, label: 'Coordinator', color: 'text-accent' },
  swarm: { icon: Users, label: 'Swarm', color: 'text-status-warning' },
  hybrid: { icon: Workflow, label: 'Hybrid', color: 'text-status-success' },
}

const SOURCE_BADGE: Record<string, { bg: string; text: string }> = {
  builtin: { bg: 'bg-accent/10', text: 'text-accent' },
  global: { bg: 'bg-status-warning/10', text: 'text-status-warning' },
  project: { bg: 'bg-status-success/10', text: 'text-status-success' },
  config: { bg: 'bg-surface-2', text: 'text-ink-muted' },
}

export default function OrchestrationSidebar({ loading: _parentLoading, onRefresh, refreshToken = 0 }: Props) {
  const [activeView, setActiveView] = useState<ActiveView>('flows')
  const [flows, setFlows] = useState<FlowInfo[]>([])
  const [agents, setAgents] = useState<OrchestrationAgent[]>([])
  const [runs, setRuns] = useState<RunStatus[]>([])
  const [flowsLoading, setFlowsLoading] = useState(true)
  const [agentsLoading, setAgentsLoading] = useState(false)
  const [expandedFlow, setExpandedFlow] = useState<string | null>(null)
  const [flowDetail, setFlowDetail] = useState<FlowDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [showRunDialog, setShowRunDialog] = useState<string | null>(null)
  const [runTask, setRunTask] = useState('')
  const [runVars, setRunVars] = useState<Record<string, string>>({})
  const [running, setRunning] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const showToast = (type: 'success' | 'error', message: string) => {
    setToast({ type, message })
    setTimeout(() => setToast(null), 4000)
  }

  const refreshFlows = useCallback(async () => {
    setFlowsLoading(true)
    try {
      setFlows(await listFlows())
    } catch {
      /* ignore */
    } finally {
      setFlowsLoading(false)
    }
  }, [])

  const refreshAgents = useCallback(async () => {
    setAgentsLoading(true)
    try {
      setAgents(await listOrchestrationAgents())
    } catch {
      /* ignore */
    } finally {
      setAgentsLoading(false)
    }
  }, [])

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await listRuns())
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    refreshFlows()
  }, [refreshFlows])

  useEffect(() => {
    // Always refresh on tab switch — a freshly created / deleted agent
    // or flow would otherwise stay cached and not appear until the tab
    // loses its state.  Cheap GETs; fine to re-run.
    if (activeView === 'agents') refreshAgents()
    if (activeView === 'runs') refreshRuns()
    if (activeView === 'flows') refreshFlows()
  }, [activeView, refreshAgents, refreshRuns, refreshFlows])

  // External refresh trigger: parent (Workbench) bumps ``refreshToken``
  // after a mutation.  Pull whatever the current tab shows so the user
  // sees their new agent / flow immediately without manual tab toggle.
  useEffect(() => {
    if (refreshToken === 0) return  // initial mount; handled above
    if (activeView === 'agents') refreshAgents()
    if (activeView === 'runs') refreshRuns()
    if (activeView === 'flows') refreshFlows()
  }, [refreshToken, activeView, refreshAgents, refreshRuns, refreshFlows])

  // Loose-coupling channel: the Workbench dispatches a
  // ``orchestration:refresh`` window event after create/update/delete
  // mutations.  Listening here keeps the two components in sync without
  // threading extra props through App.tsx.
  useEffect(() => {
    const handler = () => {
      if (activeView === 'agents') refreshAgents()
      if (activeView === 'runs') refreshRuns()
      if (activeView === 'flows') refreshFlows()
    }
    window.addEventListener('orchestration:refresh', handler)
    return () => window.removeEventListener('orchestration:refresh', handler)
  }, [activeView, refreshAgents, refreshRuns, refreshFlows])

  // Poll runs every 3s when viewing runs tab
  useEffect(() => {
    if (activeView !== 'runs') return
    const interval = setInterval(refreshRuns, 3000)
    return () => clearInterval(interval)
  }, [activeView, refreshRuns])

  const toggleFlow = async (name: string) => {
    if (expandedFlow === name) {
      setExpandedFlow(null)
      setFlowDetail(null)
      return
    }
    setExpandedFlow(name)
    setDetailLoading(true)
    try {
      const detail = await getFlow(name)
      setFlowDetail(detail)
      // Pre-fill vars for run dialog
      setRunVars({ ...detail.vars })
    } catch {
      setFlowDetail(null)
      showToast('error', `加载 ${name} 失败`)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleStartRun = async (flowName: string) => {
    setRunning(true)
    try {
      const detail = flowDetail
      const params: Parameters<typeof startRun>[0] = { flow: flowName }
      if (detail?.mode === 'swarm' && runTask.trim()) {
        params.task = runTask.trim()
      }
      if (Object.keys(runVars).length > 0) {
        params.vars = runVars
      }
      const result: RunInfo = await startRun(params)
      showToast('success', `已启动运行 ${result.run_id.slice(0, 8)}...`)
      setShowRunDialog(null)
      setRunTask('')
      setActiveView('runs')
      refreshRuns()
      onRefresh()
    } catch (err) {
      console.error('Start run failed', err)
      showToast('error', '启动编排运行失败')
    } finally {
      setRunning(false)
    }
  }

  const isLoading = flowsLoading || _parentLoading

  if (isLoading && flows.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4 py-10">
        <RefreshCcw size={16} className="animate-spin text-accent" />
        <span className="text-xs text-ink-muted">加载编排数据...</span>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <div className="px-3 pb-2">
        <div className="rounded-2xl border border-line bg-surface-1 px-3.5 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-strong">
              <Network size={14} className="text-accent" />
              <span>多 Agent 编排</span>
            </div>
            <button
              onClick={() => {
                refreshFlows()
                refreshAgents()
                refreshRuns()
              }}
              className="p-2 rounded-xl text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
              title="刷新"
            >
              <RefreshCcw size={13} />
            </button>
          </div>
          <div className="mt-2 flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-[10px]">
              <Layers size={10} className="text-ink-faint" />
              <span className="text-ink-muted">
                <span className="font-semibold text-ink-secondary">{flows.length}</span> 编排流
              </span>
            </div>
            <div className="w-px h-3 bg-line-subtle" />
            <div className="flex items-center gap-1.5 text-[10px]">
              <Bot size={10} className="text-ink-faint" />
              <span className="text-ink-muted">
                <span className="font-semibold text-ink-secondary">{agents.length}</span> Agent
              </span>
            </div>
            {runs.filter((r) => !r.done).length > 0 && (
              <>
                <div className="w-px h-3 bg-line-subtle" />
                <div className="flex items-center gap-1.5 text-[10px]">
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse" />
                  <span className="text-status-success font-medium">
                    {runs.filter((r) => !r.done).length} 运行中
                  </span>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* View selector tabs */}
      <div className="px-3 pb-2">
        <div className="grid grid-cols-3 gap-1 rounded-xl bg-surface-2 p-1">
          {([
            { key: 'flows' as const, icon: Layers, label: '编排流' },
            { key: 'agents' as const, icon: Bot, label: 'Agents' },
            { key: 'runs' as const, icon: Activity, label: '运行' },
          ]).map(({ key, icon: Icon, label }) => (
            <button
              key={key}
              onClick={() => setActiveView(key)}
              className={`flex items-center justify-center gap-1 rounded-lg px-2 py-2 text-xxs font-medium transition-all ${
                activeView === key
                  ? 'bg-surface-0 text-accent shadow-xs'
                  : 'text-ink-muted hover:text-ink-secondary'
              }`}
            >
              <Icon size={11} />
              <span>{label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div className="px-3 pb-2 animate-slide-up">
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] font-medium ${
              toast.type === 'success'
                ? 'bg-status-success/10 text-status-success border border-status-success/20'
                : 'bg-status-error/10 text-status-error border border-status-error/20'
            }`}
          >
            {toast.type === 'success' ? <Check size={12} /> : <AlertCircle size={12} />}
            <span>{toast.message}</span>
          </div>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {/* ===== FLOWS VIEW ===== */}
        {activeView === 'flows' && (
          <>
            {flows.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
                <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
                  <Network size={20} className="text-ink-faint" />
                </div>
                <div>
                  <div className="text-sm text-ink-secondary">暂无编排流</div>
                  <div className="text-xs text-ink-faint mt-1">
                    在 .mycode/orchestrations/ 下创建 YAML 文件
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-1.5">
                {flows.map((flow) => {
                  const isExpanded = expandedFlow === flow.name
                  const src = SOURCE_BADGE[flow.source] || SOURCE_BADGE.config

                  return (
                    <div
                      key={flow.name}
                      className={`rounded-xl border transition-all overflow-hidden ${
                        isExpanded
                          ? 'border-line bg-surface-1 shadow-xs'
                          : 'border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover'
                      }`}
                    >
                      {/* Flow header */}
                      <div
                        className="flex items-center gap-2 px-3 py-2.5 cursor-pointer"
                        onClick={() => toggleFlow(flow.name)}
                      >
                        <div className="flex-shrink-0 p-0.5">
                          {isExpanded ? (
                            <ChevronDown size={12} className="text-ink-muted" />
                          ) : (
                            <ChevronRight size={12} className="text-ink-muted" />
                          )}
                        </div>
                        <Workflow size={13} className="text-accent flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium text-ink-strong truncate">
                            {flow.name}
                          </div>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span
                              className={`inline-flex items-center px-1.5 py-0.5 rounded-md text-[9px] font-medium ${src.bg} ${src.text}`}
                            >
                              {flow.source}
                            </span>
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            if (!isExpanded) toggleFlow(flow.name)
                            setShowRunDialog(flow.name)
                          }}
                          className="p-1.5 rounded-lg text-ink-muted hover:bg-status-success/10 hover:text-status-success transition-colors"
                          title="运行"
                        >
                          <Play size={12} />
                        </button>
                      </div>

                      {/* Flow detail (expanded) */}
                      {isExpanded && (
                        <div className="border-t border-line-subtle">
                          {detailLoading ? (
                            <div className="flex items-center gap-2 px-4 py-4 text-xs text-ink-muted">
                              <RefreshCcw size={11} className="animate-spin" />
                              <span>加载中...</span>
                            </div>
                          ) : flowDetail ? (
                            <div className="px-3 py-2.5 space-y-3">
                              {/* Mode badge */}
                              <div className="flex items-center gap-2">
                                {(() => {
                                  const cfg = MODE_CONFIG[flowDetail.mode] || MODE_CONFIG.coordinator
                                  const ModeIcon = cfg.icon
                                  return (
                                    <span
                                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold ${cfg.color} bg-current/10`}
                                      style={{ backgroundColor: 'color-mix(in srgb, currentColor 10%, transparent)' }}
                                    >
                                      <ModeIcon size={10} />
                                      {cfg.label}
                                    </span>
                                  )
                                })()}
                                {flowDetail.mode === 'coordinator' && flowDetail.coordinator && (
                                  <span className="text-[10px] text-ink-muted">
                                    Lead: <span className="font-mono font-medium text-ink-secondary">{flowDetail.coordinator}</span>
                                  </span>
                                )}
                                {flowDetail.mode !== 'coordinator' && (flowDetail.entry || flowDetail.lead) && (
                                  <span className="text-[10px] text-ink-muted">
                                    入口: <span className="font-mono font-medium text-ink-secondary">{flowDetail.entry || flowDetail.lead}</span>
                                  </span>
                                )}
                              </div>

                              {/* Agents section */}
                              <div>
                                <div className="flex items-center gap-1.5 mb-1.5">
                                  <Bot size={10} className="text-ink-faint" />
                                  <span className="text-[10px] font-medium text-ink-muted uppercase tracking-wider">
                                    参与 Agent ({flowDetail.agents.length})
                                  </span>
                                </div>
                                <div className="space-y-0.5">
                                  {flowDetail.agents.map((agent) => (
                                    <div
                                      key={agent.name}
                                      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-surface-2"
                                    >
                                      <CircleDot size={8} className="text-accent flex-shrink-0" />
                                      <span className="text-[11px] font-medium text-ink-secondary truncate">
                                        {agent.name}
                                      </span>
                                      {agent.extends && (
                                        <span className="text-[9px] text-ink-faint flex items-center gap-0.5">
                                          <GitBranch size={8} />
                                          {agent.extends}
                                        </span>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              </div>

                              {/* Stages section (coordinator mode) */}
                              {flowDetail.stages.length > 0 && (
                                <div>
                                  <div className="flex items-center gap-1.5 mb-1.5">
                                    <Layers size={10} className="text-ink-faint" />
                                    <span className="text-[10px] font-medium text-ink-muted uppercase tracking-wider">
                                      执行阶段 ({flowDetail.stages.length})
                                    </span>
                                  </div>
                                  <div className="space-y-1">
                                    {flowDetail.stages.map((stage, idx) => (
                                      <div key={stage.id} className="relative">
                                        {idx > 0 && (
                                          <div className="absolute -top-1 left-3 w-px h-2 bg-line-subtle" />
                                        )}
                                        <div className="flex items-start gap-2 px-2.5 py-2 rounded-lg bg-surface-2">
                                          <div className="flex-shrink-0 mt-0.5">
                                            {stage.parallel ? (
                                              <Zap size={10} className="text-status-warning" />
                                            ) : stage.runs_on ? (
                                              <Settings2 size={10} className="text-accent" />
                                            ) : (
                                              <ArrowRight size={10} className="text-ink-faint" />
                                            )}
                                          </div>
                                          <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1.5">
                                              <span className="text-[11px] font-mono font-semibold text-ink-secondary">
                                                {stage.id}
                                              </span>
                                              {stage.parallel && (
                                                <span className="text-[9px] px-1 py-0.5 rounded bg-status-warning/10 text-status-warning font-medium">
                                                  并行
                                                </span>
                                              )}
                                              {stage.runs_on && (
                                                <span className="text-[9px] px-1 py-0.5 rounded bg-accent/10 text-accent font-medium">
                                                  {stage.runs_on}
                                                </span>
                                              )}
                                            </div>
                                            {stage.spawns.length > 0 && (
                                              <div className="mt-1 space-y-0.5">
                                                {stage.spawns.map((sp, si) => (
                                                  <div
                                                    key={si}
                                                    className="text-[10px] text-ink-faint truncate"
                                                  >
                                                    <span className="font-mono text-ink-muted">{sp.agent}</span>
                                                    <span className="mx-1">→</span>
                                                    <span className="italic">{sp.task.slice(0, 50)}{sp.task.length > 50 ? '...' : ''}</span>
                                                  </div>
                                                ))}
                                              </div>
                                            )}
                                            {stage.depends_on.length > 0 && (
                                              <div className="mt-1 text-[9px] text-ink-faint">
                                                依赖: {stage.depends_on.join(', ')}
                                              </div>
                                            )}
                                          </div>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Vars section */}
                              {Object.keys(flowDetail.vars).length > 0 && (
                                <div>
                                  <div className="flex items-center gap-1.5 mb-1.5">
                                    <Settings2 size={10} className="text-ink-faint" />
                                    <span className="text-[10px] font-medium text-ink-muted uppercase tracking-wider">
                                      变量
                                    </span>
                                  </div>
                                  <div className="space-y-0.5">
                                    {Object.entries(flowDetail.vars).map(([k, v]) => (
                                      <div
                                        key={k}
                                        className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-surface-2"
                                      >
                                        <span className="text-[10px] font-mono font-medium text-accent">{k}</span>
                                        <span className="text-[10px] text-ink-faint">=</span>
                                        <span className="text-[10px] text-ink-secondary truncate flex-1 font-mono">
                                          {String(v)}
                                        </span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          ) : (
                            <div className="px-4 py-4 text-xs text-ink-faint text-center">
                              加载失败
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}

        {/* ===== AGENTS VIEW ===== */}
        {activeView === 'agents' && (
          <>
            {agentsLoading ? (
              <div className="flex flex-col items-center justify-center gap-3 px-5 py-10">
                <RefreshCcw size={16} className="animate-spin text-accent" />
                <span className="text-xs text-ink-muted">加载 Agent 列表...</span>
              </div>
            ) : agents.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
                <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
                  <Bot size={20} className="text-ink-faint" />
                </div>
                <div>
                  <div className="text-sm text-ink-secondary">暂无 Agent</div>
                  <div className="text-xs text-ink-faint mt-1">
                    在 .mycode/agents/ 下创建 .md 文件
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-1">
                {agents.map((agent) => {
                  const src = SOURCE_BADGE[agent.source] || SOURCE_BADGE.config
                  return (
                    <div
                      key={agent.name}
                      className="group rounded-xl border border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover transition-all px-3 py-2.5"
                    >
                      <div className="flex items-center gap-2">
                        <Bot size={12} className="text-accent flex-shrink-0" />
                        <span className="text-xs font-medium text-ink-strong truncate flex-1">
                          {agent.name}
                        </span>
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded-md text-[9px] font-medium ${src.bg} ${src.text}`}
                        >
                          {agent.source}
                        </span>
                      </div>
                      {agent.description && (
                        <div className="text-[10px] text-ink-muted mt-1 ml-5 truncate">
                          {agent.description}
                        </div>
                      )}
                      <div className="flex items-center gap-2 mt-1 ml-5 flex-wrap">
                        {agent.extends && (
                          <span className="text-[9px] text-ink-faint flex items-center gap-0.5">
                            <GitBranch size={8} />
                            {agent.extends}
                          </span>
                        )}
                        {agent.mode && (
                          <span className="text-[9px] text-ink-faint px-1 py-0.5 rounded bg-surface-2">
                            {agent.mode}
                          </span>
                        )}
                        {agent.tools && (
                          <span className="text-[9px] text-ink-faint px-1 py-0.5 rounded bg-surface-2 truncate max-w-[120px]">
                            {agent.tools}
                          </span>
                        )}
                      </div>
                      {agent.error && (
                        <div className="text-[10px] text-status-error mt-1 ml-5 flex items-center gap-1">
                          <AlertCircle size={9} />
                          {agent.error}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </>
        )}

        {/* ===== RUNS VIEW ===== */}
        {activeView === 'runs' && (
          <>
            {runs.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
                <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
                  <Activity size={20} className="text-ink-faint" />
                </div>
                <div>
                  <div className="text-sm text-ink-secondary">暂无运行记录</div>
                  <div className="text-xs text-ink-faint mt-1">
                    从编排流 Tab 启动运行
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-1">
                {runs.map((run) => (
                  <div
                    key={run.run_id}
                    className="rounded-xl border border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover transition-all px-3 py-2.5"
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className={`w-2 h-2 rounded-full flex-shrink-0 ${
                          run.cancelled
                            ? 'bg-status-error'
                            : run.done
                            ? 'bg-status-success'
                            : 'bg-status-warning animate-pulse'
                        }`}
                      />
                      <span className="text-xs font-mono font-medium text-ink-strong truncate flex-1">
                        {run.run_id}
                      </span>
                      <span
                        className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${
                          run.cancelled
                            ? 'bg-status-error/10 text-status-error'
                            : run.done
                            ? 'bg-status-success/10 text-status-success'
                            : 'bg-status-warning/10 text-status-warning'
                        }`}
                      >
                        {run.cancelled ? '已取消' : run.done ? '已完成' : '运行中'}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Run dialog overlay */}
      {showRunDialog && flowDetail && (
        <div className="absolute inset-0 z-50 bg-black/20 flex items-end justify-center backdrop-blur-[2px]">
          <div className="w-full mx-2 mb-2 rounded-xl border border-line bg-surface-0 shadow-lg overflow-hidden animate-slide-up">
            {/* Dialog header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-line-subtle bg-surface-1">
              <div className="flex items-center gap-2">
                <Play size={13} className="text-accent" />
                <span className="text-xs font-semibold text-ink-strong">
                  运行 {showRunDialog}
                </span>
                {(() => {
                  const cfg = MODE_CONFIG[flowDetail.mode] || MODE_CONFIG.coordinator
                  return (
                    <span className={`text-[10px] font-medium ${cfg.color}`}>
                      {cfg.label}
                    </span>
                  )
                })()}
              </div>
              <button
                onClick={() => setShowRunDialog(null)}
                className="p-1 rounded-lg hover:bg-surface-hover text-ink-muted transition-colors"
              >
                <X size={14} />
              </button>
            </div>

            <div className="px-4 py-3 space-y-3 max-h-60 overflow-y-auto">
              {/* Task input (swarm mode) */}
              {flowDetail.mode === 'swarm' && (
                <div>
                  <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider mb-1.5 block">
                    任务描述 (必填)
                  </label>
                  <textarea
                    value={runTask}
                    onChange={(e) => setRunTask(e.target.value)}
                    placeholder="描述要让 Agent 团队完成的任务..."
                    rows={2}
                    className="w-full bg-surface-2 rounded-lg px-3 py-2 text-xs text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 focus:bg-surface-0 resize-none transition-all"
                  />
                </div>
              )}

              {/* Vars editing */}
              {Object.keys(flowDetail.vars).length > 0 && (
                <div>
                  <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider mb-1.5 block">
                    变量覆盖
                  </label>
                  <div className="space-y-1.5">
                    {Object.entries(flowDetail.vars).map(([key, defaultVal]) => (
                      <div key={key} className="flex items-center gap-2">
                        <span className="text-[10px] font-mono text-accent w-16 truncate flex-shrink-0">
                          {key}
                        </span>
                        <input
                          type="text"
                          value={runVars[key] ?? defaultVal}
                          onChange={(e) =>
                            setRunVars((prev) => ({ ...prev, [key]: e.target.value }))
                          }
                          className="flex-1 bg-surface-2 rounded-lg px-2.5 py-1.5 text-[11px] text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 focus:bg-surface-0 font-mono transition-all"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Dialog footer */}
            <div className="px-4 py-3 border-t border-line-subtle bg-surface-1 flex justify-end gap-2">
              <button
                onClick={() => setShowRunDialog(null)}
                className="px-3 py-1.5 rounded-lg text-[11px] font-medium text-ink-muted hover:bg-surface-hover transition-colors"
              >
                取消
              </button>
              <button
                onClick={() => handleStartRun(showRunDialog)}
                disabled={running || (flowDetail.mode === 'swarm' && !runTask.trim())}
                className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-accent text-white text-[11px] font-medium hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-xs"
              >
                {running ? (
                  <RefreshCcw size={10} className="animate-spin" />
                ) : (
                  <Play size={10} />
                )}
                <span>启动运行</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
