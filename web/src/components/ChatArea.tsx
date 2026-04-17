import { useState } from 'react'
import { Sparkles, ArrowRight } from 'lucide-react'
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
  { text: '分析当前项目的架构和技术栈', icon: '🏗️' },
  { text: '帮我写一个 REST API 接口', icon: '⚡' },
  { text: '查找并修复代码中的 bug', icon: '🔍' },
  { text: '重构这段代码，提升可读性', icon: '✨' },
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
      <div className="flex-1 flex flex-col bg-surface-1">
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          {/* Logo area */}
          <div className="mb-8 flex flex-col items-center gap-3">
            <div className="w-12 h-12 rounded-xl bg-accent flex items-center justify-center shadow-sm">
              <Sparkles size={22} className="text-white" />
            </div>
            <h1 className="text-xl font-semibold text-ink-strong tracking-tight">有什么可以帮到你？</h1>
            <p className="text-sm text-ink-tertiary">选择一个常用指令或直接输入你的问题</p>
          </div>

          {/* Suggestion cards */}
          <div className="max-w-lg w-full grid grid-cols-2 gap-2.5">
            {SUGGESTIONS.map((item, i) => (
              <button
                key={i}
                onClick={async () => {
                  const sess = await onCreate()
                  if (sess) {
                    setTimeout(() => onSend(item.text), 100)
                  }
                }}
                className="group flex items-start gap-3 p-4 rounded-xl text-left bg-surface-0 border border-line-subtle hover:border-accent hover:shadow-sm transition-all"
              >
                <span className="text-lg flex-shrink-0 mt-0.5">{item.icon}</span>
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-ink-secondary group-hover:text-ink transition-colors leading-snug block">{item.text}</span>
                </div>
                <ArrowRight size={14} className="text-ink-faint group-hover:text-accent transition-colors flex-shrink-0 mt-0.5" />
              </button>
            ))}
          </div>
        </div>

        {/* Bottom input */}
        <div className="px-6 pb-8">
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
    <div className="flex-1 flex flex-col min-w-0 bg-surface-1">
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
        <div className="mx-4 mb-2 px-4 py-2.5 bg-status-error-light border border-status-error/15 rounded-lg text-status-error text-sm">
          {error}
        </div>
      )}
      <div className="px-4 pb-5">
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
