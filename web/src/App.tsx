import { useEffect } from 'react'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'
import PermissionModal from './components/PermissionModal'
import { useSession } from './hooks/useSession'
import { useChat } from './hooks/useChat'
import { usePermission } from './hooks/usePermission'
import { useProviders } from './hooks/useProviders'

export default function App() {
  const session = useSession()
  const chat = useChat(session.activeId)
  const permission = usePermission()
  const providerState = useProviders()

  // Load messages when active session changes
  useEffect(() => {
    chat.loadHistory()
  }, [session.activeId]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-screen bg-[#0c0d12] text-text-primary">
      <Sidebar
        sessions={session.sessions}
        deletedSessions={session.deletedSessions}
        activeId={session.activeId}
        onSelect={session.setActiveId}
        onCreate={session.create}
        onDelete={session.remove}
        onRestore={session.restore}
        loading={session.loading}
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
        onCreate={session.create}
        models={providerState.models}
        agents={providerState.agents}
        selectedModel={providerState.selectedModel}
        selectedAgent={providerState.selectedAgent}
        onModelChange={providerState.setSelectedModel}
        onAgentChange={providerState.setSelectedAgent}
        contextSnapshot={chat.contextSnapshot}
      />
      {permission.pending.length > 0 && (
        <PermissionModal request={permission.pending[0]} onReply={permission.reply} />
      )}
    </div>
  )
}
