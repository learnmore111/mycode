import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'
import OrchestrationWorkbench from './components/OrchestrationWorkbench'
import PermissionModal from './components/PermissionModal'
import GitDiffViewer from './components/GitDiffViewer'
import CommandPalette from './components/CommandPalette'
import { useSession } from './hooks/useSession'
import { useChat } from './hooks/useChat'
import { useGit } from './hooks/useGit'
import { usePermission } from './hooks/usePermission'
import { useProviders } from './hooks/useProviders'
import { listSkills } from './api/skills'
import { getMcpStatus } from './api/mcp'
import type { SkillInfo } from './api/skills'
import type { McpStatus } from './api/mcp'

type MainView = 'chat' | 'orchestration'

export default function App() {
  const session = useSession()
  const chat = useChat(session.activeId)
  const git = useGit()
  const permission = usePermission()
  const providerState = useProviders()

  // --- Main view state (chat vs orchestration workbench) ---
  const [mainView, setMainView] = useState<MainView>('chat')

  // --- Skills state ---
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [skillsLoading, setSkillsLoading] = useState(true)
  const refreshSkills = useCallback(async () => {
    setSkillsLoading(true)
    try { setSkills(await listSkills()) } catch { /* ignore */ }
    finally { setSkillsLoading(false) }
  }, [])
  useEffect(() => { refreshSkills() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // --- MCP state ---
  const [mcpStatus, setMcpStatus] = useState<McpStatus | null>(null)
  const [mcpLoading, setMcpLoading] = useState(true)
  const refreshMcp = useCallback(async () => {
    setMcpLoading(true)
    try { setMcpStatus(await getMcpStatus()) } catch { /* ignore */ }
    finally { setMcpLoading(false) }
  }, [])
  useEffect(() => { refreshMcp() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Sidebar resizable width ---
  const SIDEBAR_MIN = 180
  const SIDEBAR_MAX = 480
  const SIDEBAR_DEFAULT = 256
  const STORAGE_KEY = 'sidebar-width'

  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const n = parseInt(saved, 10)
      if (!isNaN(n) && n >= SIDEBAR_MIN && n <= SIDEBAR_MAX) return n
    }
    return SIDEBAR_DEFAULT
  })
  const dragging = useRef(false)

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    document.body.classList.add('select-none', 'cursor-col-resize')
  }, [])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const next = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, e.clientX))
      setSidebarWidth(next)
    }
    const onUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.classList.remove('select-none', 'cursor-col-resize')
      setSidebarWidth((w) => {
        localStorage.setItem(STORAGE_KEY, String(w))
        return w
      })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  // Auto-refresh git status when streaming ends
  const wasStreaming = useRef(false)
  useEffect(() => {
    if (chat.streaming) {
      wasStreaming.current = true
    } else if (wasStreaming.current) {
      wasStreaming.current = false
      git.refresh()
    }
  }, [chat.streaming]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    chat.loadHistory()
  }, [session.activeId]) // eslint-disable-line react-hooks/exhaustive-deps

  // --- Command palette (Cmd/Ctrl+K) ---
  const [paletteOpen, setPaletteOpen] = useState(false)

  const gitChangedPaths = useMemo(
    () => new Set((git.status?.files ?? []).map((f) => f.path)),
    [git.status?.files],
  )
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toLowerCase().includes('mac')
      const mod = isMac ? e.metaKey : e.ctrlKey
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((v) => !v)
      }
      if (e.key === 'Escape' && paletteOpen) {
        setPaletteOpen(false)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [paletteOpen])

  // When clicking a session in sidebar, switch back to chat view
  const handleSelectSession = useCallback((id: string) => {
    session.setActiveId(id)
    setMainView('chat')
  }, [session])

  return (
    <div className="flex h-screen bg-surface-1 text-ink font-sans">
      <Sidebar
        sessions={session.sessions}
        deletedSessions={session.deletedSessions}
        activeId={session.activeId}
        onSelect={handleSelectSession}
        onCreate={() => { session.create(); setMainView('chat') }}
        onDelete={session.remove}
        onRestore={session.restore}
        loading={session.loading}
        gitStatus={git.status}
        gitLoading={git.loading}
        gitError={git.error}
        selectedGitPath={git.selectedPath}
        onSelectGitFile={git.openDiff}
        onRefreshGit={git.refresh}
        width={sidebarWidth}
        skills={skills}
        skillsLoading={skillsLoading}
        onRefreshSkills={refreshSkills}
        mcpStatus={mcpStatus}
        mcpLoading={mcpLoading}
        onRefreshMcp={refreshMcp}
        onRefreshOrchestration={() => {}}
        onOpenOrchestration={() => setMainView('orchestration')}
      />
      {/* Drag handle */}
      <div
        onMouseDown={onDragStart}
        className="w-1 flex-shrink-0 cursor-col-resize hover:bg-accent/30 active:bg-accent/50 transition-colors"
      />

      {/*
        Main content area: ChatArea stays mounted (hidden via CSS) so it
        doesn't lose state. OrchestrationWorkbench mounts/unmounts on demand.
      */}
      <div className={mainView === 'chat' ? 'flex-1 flex flex-col min-w-0' : 'hidden'}>
        <ChatArea
          session={session.active}
          messages={chat.messages}
          streaming={chat.streaming}
          streamText={chat.streamText}
          streamParts={chat.streamParts}
          error={chat.error}
          loadingHistory={chat.loadingHistory}
          onSend={(text) => chat.send(text, { model: providerState.selectedModel, agent: providerState.selectedAgent })}
          onAbort={chat.abort}
          onResume={chat.resume}
          onDismissPausedRun={chat.dismissPausedRun}
          pausedRun={chat.pausedRun}
          codeChanges={chat.codeChanges}
          chatStatus={chat.status}
          onCreate={session.create}
          models={providerState.models}
          agents={providerState.agents}
          selectedModel={providerState.selectedModel}
          selectedAgent={providerState.selectedAgent}
          onModelChange={providerState.setSelectedModel}
          onAgentChange={providerState.setSelectedAgent}
          contextSnapshot={chat.contextSnapshot}
          canReturnToLastSession={session.canReturnToLastSession}
          onReturnToLastSession={session.returnToLastSession}
          onSelectGitFile={git.openDiff}
          onRefreshGit={git.refresh}
          gitChangedPaths={gitChangedPaths}
          onRollback={async (turn, options) => {
            const result = await chat.rollbackToTurn(turn, options)
            git.refresh()
            return result
          }}
        />
      </div>

      {mainView === 'orchestration' && (
        <OrchestrationWorkbench onBack={() => setMainView('chat')} />
      )}

      {permission.pending.length > 0 && (
        <PermissionModal request={permission.pending[0]} onReply={permission.reply} />
      )}
      <GitDiffViewer
        diff={git.diff}
        loading={git.diffLoading}
        error={git.diffError}
        onClose={git.closeDiff}
      />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        sessions={session.sessions}
        activeId={session.activeId}
        onSelect={(id) => { session.setActiveId(id); setMainView('chat') }}
      />
    </div>
  )
}
