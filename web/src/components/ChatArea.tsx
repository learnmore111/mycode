import { useState } from 'react'
import { Code2, Sparkles, Scan, Wrench, Bug, RefreshCw } from 'lucide-react'
import type { Session, Message, StreamingPart, AgentInfo, ContextSnapshot } from '../types'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import MessageInput from './MessageInput'
import ContextViewer from './ContextViewer'

interface Props {
  session: Session | null
  messages: Message[]
  streaming: boolean
  streamText: string
  streamParts: StreamingPart[]
  error: string | null
  loadingHistory: boolean
  onSend: (text: string) => void
  onAbort: () => void
  onCreate: () => Promise<Session>
  models: { id: string; name: string; provider: string }[]
  agents: AgentInfo[]
  selectedModel?: string
  selectedAgent?: string
  onModelChange: (m: string | undefined) => void
  onAgentChange: (a: string | undefined) => void
  contextSnapshot?: ContextSnapshot | null
}

const SUGGESTIONS = [
  { icon: Scan, text: '分析当前项目的架构和技术栈', color: 'text-accent-blue' },
  { icon: Wrench, text: '帮我写一个 REST API 接口', color: 'text-accent-green' },
  { icon: Bug, text: '查找并修复代码中的 bug', color: 'text-accent-amber' },
  { icon: RefreshCw, text: '重构这段代码，提升可读性', color: 'text-accent-purple' },
]

export default function ChatArea({
  session,
  messages,
  streaming,
  streamText,
  streamParts,
  error,
  loadingHistory,
  onSend,
  onAbort,
  onCreate,
  models,
  agents,
  selectedModel,
  selectedAgent,
  onModelChange,
  onAgentChange,
  contextSnapshot,
}: Props) {
  const [showContext, setShowContext] = useState(false)

  // Welcome screen — no active session
  if (!session) {
    return (
      <div className="flex-1 flex flex-col">
        {/* Center content */}
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          {/* Logo + Title */}
          <div className="mb-4">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center shadow-elevated">
              <Code2 size={24} className="text-white" />
            </div>
          </div>
          <h1 className="text-xl font-semibold text-text-primary mb-2 tracking-tight">你说，MyCode 来创造</h1>
          <p className="text-text-tertiary text-sm mb-8">智慧捕捉分析需求，构建全新完整应用，持续迭代业务存量</p>

          {/* Suggestions */}
          <div className="max-w-lg w-full space-y-2">
            <div className="flex items-center gap-2 text-text-muted text-xs mb-3">
              <Sparkles size={13} />
              <span>不知道从哪里开始？试试这些</span>
            </div>
            {SUGGESTIONS.map((s, i) => (
              <button
                key={i}
                onClick={async () => {
                  const sess = await onCreate()
                  if (sess) {
                    setTimeout(() => onSend(s.text), 100)
                  }
                }}
                className="flex items-center gap-3 w-full px-4 py-3 rounded-lg text-sm text-text-secondary bg-surface-1 border border-border-subtle hover:bg-surface-2 hover:border-border hover:text-text-primary transition-all text-left"
              >
                <s.icon size={16} className={s.color} />
                {s.text}
              </button>
            ))}
          </div>
        </div>

        {/* Bottom input bar */}
        <div className="px-6 pb-6">
          <MessageInput
            onSend={async (text) => {
              const sess = await onCreate()
              if (sess) {
                setTimeout(() => onSend(text), 100)
              }
            }}
            onAbort={onAbort}
            streaming={streaming}
            models={models}
            agents={agents}
            selectedModel={selectedModel}
            selectedAgent={selectedAgent}
            onModelChange={onModelChange}
            onAgentChange={onAgentChange}
          />
        </div>
      </div>
    )
  }

  // Active session — conversation view
  return (
    <div className="flex-1 flex flex-col min-w-0">
      <ChatHeader
        session={session}
        models={models}
        agents={agents}
        selectedModel={selectedModel}
        selectedAgent={selectedAgent}
        onModelChange={onModelChange}
        onAgentChange={onAgentChange}
        contextSnapshot={contextSnapshot}
        onViewContext={() => setShowContext(true)}
      />
      <MessageList
        messages={messages}
        streaming={streaming}
        streamText={streamText}
        streamParts={streamParts}
        loadingHistory={loadingHistory}
      />
      {error && (
        <div className="mx-4 mb-2 px-3 py-2 bg-accent-red/10 border border-accent-red/20 rounded-lg text-accent-red text-sm">
          {error}
        </div>
      )}
      <div className="px-4 pb-4">
        <MessageInput
          onSend={onSend}
          onAbort={onAbort}
          streaming={streaming}
          models={models}
          agents={agents}
          selectedModel={selectedModel}
          selectedAgent={selectedAgent}
          onModelChange={onModelChange}
          onAgentChange={onAgentChange}
        />
      </div>
      {showContext && contextSnapshot && (
        <ContextViewer snapshot={contextSnapshot} sessionId={session.id} onClose={() => setShowContext(false)} />
      )}
    </div>
  )
}
