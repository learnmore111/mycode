import { useEffect } from 'react'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'
import PermissionModal from './components/PermissionModal'
import GitDiffViewer from './components/GitDiffViewer'
import { useSession } from './hooks/useSession'
import { useChat } from './hooks/useChat'
import { useGit } from './hooks/useGit'
import { usePermission } from './hooks/usePermission'
import { useProviders } from './hooks/useProviders'

export default function App() {
  const session = useSession()
  const chat = useChat(session.activeId)
  const git = useGit()
  const permission = usePermission()
  const providerState = useProviders()

  useEffect(() => {
    chat.loadHistory()
  }, [session.activeId]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-screen bg-surface-1 text-ink font-sans">
      <Sidebar
        sessions={session.sessions}
        deletedSessions={session.deletedSessions}
        activeId={session.activeId}
        onSelect={session.setActiveId}
        onCreate={session.create}
        onDelete={session.remove}
        onRestore={session.restore}
        loading={session.loading}
        gitStatus={git.status}
        gitLoading={git.loading}
        gitError={git.error}
        selectedGitPath={git.selectedPath}
        onSelectGitFile={git.openDiff}
        onRefreshGit={git.refresh}
      />
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
      />
      {permission.pending.length > 0 && (
        <PermissionModal request={permission.pending[0]} onReply={permission.reply} />
      )}
      <GitDiffViewer
        diff={git.diff}
        loading={git.diffLoading}
        error={git.diffError}
        onClose={git.closeDiff}
      />
    </div>
  )
}
