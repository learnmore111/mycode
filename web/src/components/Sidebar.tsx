import { useState, useMemo } from 'react'
import {
  Plus,
  Trash2,
  RotateCcw,
  MessageSquare,
  Loader2,
  PanelLeftClose,
  PanelLeft,
  ChevronDown,
  ChevronRight,
  Search,
  X,
  Clock,
  CalendarDays,
  GitBranch,
  Wand2,
  Plug,
  Network,
  Bot,
  Layers,
  Activity,
} from 'lucide-react'
import GitSidebar from './GitSidebar'
import SkillsSidebar from './SkillsSidebar'
import McpSidebar from './McpSidebar'
import type { GitStatus, Session } from '../types'
import type { SkillInfo } from '../api/skills'
import type { McpStatus } from '../api/mcp'
import { getSessionSearchText, getSessionSummaryBadges } from '../utils/sessionInsights'

interface Props {
  sessions: Session[]
  deletedSessions: Session[]
  activeId: string | null
  onSelect: (id: string) => void
  onCreate: () => void
  onDelete: (id: string) => void
  onRestore: (id: string) => void
  loading: boolean
  gitStatus: GitStatus | null
  gitLoading: boolean
  gitError: string | null
  selectedGitPath: string | null
  onSelectGitFile: (path: string) => void
  onRefreshGit: () => void
  width?: number
  skills: SkillInfo[]
  skillsLoading: boolean
  onRefreshSkills: () => void
  mcpStatus: McpStatus | null
  mcpLoading: boolean
  onRefreshMcp: () => void
  onRefreshOrchestration: () => void
  onOpenOrchestration?: () => void
}

type TabKey = 'sessions' | 'git' | 'skills' | 'mcp' | 'orchestration'

const TAB_ITEMS: Array<{ key: TabKey; icon: typeof MessageSquare; label: string; tip: string }> = [
  { key: 'sessions', icon: MessageSquare, label: '会话', tip: '会话列表' },
  { key: 'git', icon: GitBranch, label: 'Git', tip: 'Git 状态' },
  { key: 'skills', icon: Wand2, label: '技能', tip: '技能管理' },
  { key: 'mcp', icon: Plug, label: 'MCP', tip: 'MCP 服务器' },
  { key: 'orchestration', icon: Network, label: '编排', tip: '多 Agent 编排' },
]

function getTimeGroup(timestamp: number): string {
  const now = new Date()
  const ts = timestamp < 1e12 ? timestamp * 1000 : timestamp
  const date = new Date(ts)
  const diffDays = Math.floor((now.getTime() - date.getTime()) / 86400000)
  if (diffDays === 0) return '今天'
  if (diffDays === 1) return '昨天'
  if (diffDays <= 7) return '最近 7 天'
  if (diffDays <= 30) return '最近 30 天'
  return '更早'
}

function groupSessions(sessions: Session[]) {
  const groups: Record<string, Session[]> = {}
  const order = ['今天', '昨天', '最近 7 天', '最近 30 天', '更早']
  for (const s of sessions) {
    const group = getTimeGroup(s.time.created)
    if (!groups[group]) groups[group] = []
    groups[group].push(s)
  }
  return order.filter((g) => groups[g]?.length).map((g) => ({ label: g, sessions: groups[g] }))
}

function formatSessionTime(timestamp: number): string {
  const ts = timestamp < 1e12 ? timestamp * 1000 : timestamp
  const date = new Date(ts)
  const now = new Date()
  const diffDays = Math.floor((now.getTime() - date.getTime()) / 86400000)
  if (diffDays === 0) return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

function SessionItem({ session, isActive, onSelect, onDelete }: {
  session: Session; isActive: boolean; onSelect: () => void; onDelete: () => void
}) {
  const summaryBadges = getSessionSummaryBadges(session.summary)
  return (
    <div
      onClick={onSelect}
      className={`group flex items-center gap-2.5 px-3 py-2.5 rounded-xl cursor-pointer transition-all relative ${
        isActive ? 'bg-accent-light text-accent shadow-xs' : 'text-ink-secondary hover:bg-surface-hover hover:text-ink'
      }`}
    >
      {isActive && <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-accent" />}
      <MessageSquare size={14} className={`flex-shrink-0 ${isActive ? 'text-accent' : 'text-ink-muted'}`} />
      <div className="flex-1 min-w-0">
        <div className={`text-sm truncate ${isActive ? 'font-semibold' : ''}`}>{session.title || '未命名会话'}</div>
        <div className="text-xxs text-ink-muted mt-0.5 flex items-center gap-1 flex-wrap">
          <span className="flex items-center gap-1"><Clock size={9} /><span>{formatSessionTime(session.time.created)}</span></span>
          {summaryBadges.map((badge) => (
            <span key={badge} className={`px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${
              isActive ? 'border-accent/20 bg-white/70 text-accent' : 'border-line-subtle bg-surface-1 text-ink-muted'
            }`}>{badge}</span>
          ))}
        </div>
      </div>
      <button onClick={(e) => { e.stopPropagation(); onDelete() }}
        className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg hover:bg-status-error-light text-ink-muted hover:text-status-error transition-all" title="删除">
        <Trash2 size={12} />
      </button>
    </div>
  )
}

export default function Sidebar({
  sessions, deletedSessions, activeId, onSelect, onCreate, onDelete, onRestore, loading,
  gitStatus, gitLoading, gitError, selectedGitPath, onSelectGitFile, onRefreshGit,
  width = 256, skills, skillsLoading, onRefreshSkills,
  mcpStatus, mcpLoading, onRefreshMcp, onRefreshOrchestration, onOpenOrchestration,
}: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const [showDeleted, setShowDeleted] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [isSearching, setIsSearching] = useState(false)
  const [activeTab, setActiveTab] = useState<TabKey>('sessions')

  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions
    const q = searchQuery.toLowerCase()
    return sessions.filter((s) => {
      const titleText = (s.title || '').toLowerCase()
      const summaryText = getSessionSearchText(s.summary).toLowerCase()
      return titleText.includes(q) || summaryText.includes(q)
    })
  }, [sessions, searchQuery])

  const grouped = useMemo(() => groupSessions(filteredSessions), [filteredSessions])

  // ── Collapsed: just icons ──
  if (collapsed) {
    return (
      <div className="flex flex-col items-center py-3 px-1.5 gap-1 bg-surface-0 border-r border-line">
        <button onClick={() => setCollapsed(false)}
          className="p-2 rounded-lg hover:bg-surface-hover text-ink-muted hover:text-ink-secondary transition-colors mb-1" title="展开侧边栏">
          <PanelLeft size={16} />
        </button>
        {TAB_ITEMS.map(({ key, icon: Icon, tip }) => (
          <button key={key} onClick={() => { setActiveTab(key); setCollapsed(false) }}
            className={`p-2 rounded-lg transition-all ${
              activeTab === key ? 'bg-accent-light text-accent' : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'
            }`} title={tip}>
            <Icon size={15} />
          </button>
        ))}
        <div className="flex-1" />
        <button onClick={onCreate}
          className="p-2 rounded-lg bg-accent text-white hover:bg-accent-hover shadow-xs transition-all" title="新建会话">
          <Plus size={15} />
        </button>
      </div>
    )
  }

  // ── Expanded: icon rail (left) + content panel (right) ──
  return (
    <div style={{ width }} className="flex-shrink-0 flex border-r border-line bg-surface-0">
      {/* Left icon rail */}
      <div className="flex flex-col items-center py-3 px-1.5 gap-0.5 border-r border-line-subtle bg-surface-0 flex-shrink-0">
        {/* Logo */}
        <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center shadow-xs mb-2">
          <span className="text-white text-xs font-bold font-mono">M</span>
        </div>

        {TAB_ITEMS.map(({ key, icon: Icon, tip }) => (
          <button key={key} onClick={() => setActiveTab(key)}
            className={`relative p-2 rounded-lg transition-all group ${
              activeTab === key
                ? 'bg-accent-light text-accent'
                : 'text-ink-muted hover:bg-surface-hover hover:text-ink-secondary'
            }`} title={tip}>
            <Icon size={15} />
            {/* Active indicator */}
            {activeTab === key && (
              <div className="absolute left-0 top-1/2 -translate-y-1/2 -translate-x-[3px] w-[3px] h-4 rounded-r-full bg-accent" />
            )}
          </button>
        ))}

        <div className="flex-1" />

        {/* Quick actions */}
        <button onClick={onCreate}
          className="p-2 rounded-lg bg-accent text-white hover:bg-accent-hover shadow-xs transition-all mb-1" title="新建会话">
          <Plus size={14} />
        </button>
        <button onClick={() => setCollapsed(true)}
          className="p-1.5 rounded-lg hover:bg-surface-hover text-ink-faint hover:text-ink-muted transition-colors" title="折叠">
          <PanelLeftClose size={13} />
        </button>
      </div>

      {/* Right content panel */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Content header */}
        <div className="px-3 py-3 flex items-center justify-between border-b border-line-subtle">
          <span className="text-xs font-semibold text-ink-strong tracking-tight">
            {TAB_ITEMS.find((t) => t.key === activeTab)?.label}
          </span>
          <div className="flex items-center gap-0.5">
            {activeTab === 'sessions' && (
              <button onClick={() => setIsSearching(!isSearching)}
                className={`p-1.5 rounded-lg transition-colors ${isSearching ? 'bg-accent-light text-accent' : 'hover:bg-surface-hover text-ink-muted hover:text-ink-secondary'}`} title="搜索会话">
                <Search size={13} />
              </button>
            )}
          </div>
        </div>

        {/* Tab content */}
        {activeTab === 'sessions' ? (
          <>
            {isSearching && (
              <div className="px-3 py-2 animate-slide-up border-b border-line-subtle">
                <div className="flex items-center gap-2 bg-surface-2 rounded-lg px-3 py-1.5 border border-line-subtle focus-within:border-accent/30 focus-within:bg-surface-0 transition-all">
                  <Search size={11} className="text-ink-muted flex-shrink-0" />
                  <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="搜索标题或改动文件..."
                    className="flex-1 bg-transparent text-xs text-ink placeholder:text-ink-muted outline-none" autoFocus />
                  {searchQuery && (
                    <button onClick={() => setSearchQuery('')} className="p-0.5 rounded hover:bg-surface-3 text-ink-muted">
                      <X size={10} />
                    </button>
                  )}
                </div>
                {searchQuery && <div className="mt-1 text-xxs text-ink-muted px-0.5">找到 {filteredSessions.length} 个</div>}
              </div>
            )}

            <div className="px-2 py-2">
              <button onClick={onCreate}
                className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-accent text-white text-xs font-medium hover:bg-accent-hover shadow-xs transition-all">
                <Plus size={13} /><span>新建会话</span>
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-1.5 pb-2">
              {loading ? (
                <div className="flex flex-col items-center justify-center py-10 gap-3">
                  <Loader2 size={16} className="animate-spin text-accent" />
                  <span className="text-xs text-ink-muted">加载中...</span>
                </div>
              ) : sessions.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 gap-3">
                  <div className="w-10 h-10 rounded-2xl bg-surface-2 flex items-center justify-center">
                    <MessageSquare size={18} className="text-ink-faint" />
                  </div>
                  <div className="text-center">
                    <span className="text-xs text-ink-muted block">暂无会话</span>
                    <span className="text-xxs text-ink-faint mt-0.5 block">点击上方按钮创建</span>
                  </div>
                </div>
              ) : filteredSessions.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-8 gap-2">
                  <Search size={16} className="text-ink-faint" />
                  <span className="text-xxs text-ink-muted">未找到匹配的会话</span>
                </div>
              ) : (
                <div className="space-y-3">
                  {grouped.map((group) => (
                    <div key={group.label}>
                      <div className="flex items-center gap-2 px-2 py-1 mb-0.5">
                        <CalendarDays size={9} className="text-ink-faint" />
                        <span className="text-xxs font-semibold text-ink-muted uppercase tracking-wider">{group.label}</span>
                        <div className="flex-1 h-px bg-line-subtle" />
                        <span className="text-xxs text-ink-faint font-mono">{group.sessions.length}</span>
                      </div>
                      <div className="space-y-0.5">
                        {group.sessions.map((s) => (
                          <SessionItem key={s.id} session={s} isActive={s.id === activeId}
                            onSelect={() => onSelect(s.id)} onDelete={() => onDelete(s.id)} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {deletedSessions.length > 0 && (
                <div className="mt-3 pt-2 border-t border-line-subtle">
                  <button onClick={() => setShowDeleted(!showDeleted)}
                    className="flex items-center gap-2 px-2 py-1.5 w-full text-left rounded-lg hover:bg-surface-hover transition-colors">
                    {showDeleted ? <ChevronDown size={11} className="text-ink-muted" /> : <ChevronRight size={11} className="text-ink-muted" />}
                    <Trash2 size={11} className="text-ink-muted" />
                    <span className="text-xxs text-ink-muted font-medium flex-1">已删除</span>
                    <span className="text-xxs text-ink-faint bg-surface-2 px-1.5 py-0.5 rounded-md font-mono">{deletedSessions.length}</span>
                  </button>
                  {showDeleted && (
                    <div className="mt-1 space-y-0.5 animate-slide-up">
                      {deletedSessions.map((s) => (
                        <div key={s.id} className="group flex items-center gap-2 px-2 py-1.5 rounded-lg text-ink-muted hover:bg-surface-hover transition-colors">
                          <MessageSquare size={11} className="flex-shrink-0 opacity-30" />
                          <span className="text-xxs opacity-60 truncate flex-1">{s.title || '未命名会话'}</span>
                          <button onClick={(e) => { e.stopPropagation(); onRestore(s.id) }}
                            className="opacity-0 group-hover:opacity-100 p-1 rounded-lg hover:bg-status-success-light text-ink-muted hover:text-status-success transition-all" title="恢复">
                            <RotateCcw size={11} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="px-3 py-2 border-t border-line-subtle">
              <div className="flex items-center justify-between text-xxs text-ink-faint">
                <span>{sessions.length} 个会话</span>
                <span className="font-mono">v0.1</span>
              </div>
            </div>
          </>
        ) : activeTab === 'git' ? (
          <GitSidebar status={gitStatus} loading={gitLoading} error={gitError}
            selectedPath={selectedGitPath} onSelectFile={onSelectGitFile} onRefresh={onRefreshGit} />
        ) : activeTab === 'skills' ? (
          <SkillsSidebar skills={skills} loading={skillsLoading} onRefresh={onRefreshSkills} />
        ) : activeTab === 'mcp' ? (
          <McpSidebar status={mcpStatus} loading={mcpLoading} onRefresh={onRefreshMcp} />
        ) : activeTab === 'orchestration' ? (
          <div className="flex-1 flex flex-col min-h-0 px-3 py-3">
            <div className="rounded-2xl border border-line bg-surface-1 px-3.5 py-3 mb-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-ink-strong">
                <Network size={14} className="text-accent" />
                <span>多 Agent 编排</span>
              </div>
              <div className="mt-1 text-[11px] text-ink-muted">创建和管理 Agent、设计编排流程、监控运行状态</div>
            </div>
            <button onClick={onOpenOrchestration}
              className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl bg-accent text-white text-xs font-medium hover:bg-accent-hover shadow-xs transition-all">
              <Network size={13} /><span>打开编排工作台</span>
            </button>
            <div className="mt-4 space-y-2.5 text-[11px] text-ink-muted px-0.5">
              <div className="flex items-center gap-2.5">
                <span className="w-5 h-5 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0"><Bot size={10} className="text-accent" /></span>
                <span>Agent 管理 — 创建/编辑自定义 Agent</span>
              </div>
              <div className="flex items-center gap-2.5">
                <span className="w-5 h-5 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0"><Layers size={10} className="text-accent" /></span>
                <span>流程设计 — Coordinator / Swarm 编排</span>
              </div>
              <div className="flex items-center gap-2.5">
                <span className="w-5 h-5 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0"><Activity size={10} className="text-accent" /></span>
                <span>运行监控 — 实时事件流</span>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
