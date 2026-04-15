import { Code2, Sparkles } from 'lucide-react'
import type { Session, Message, StreamingPart, AgentInfo } from '../types'
import ChatHeader from './ChatHeader'
import MessageList from './MessageList'
import MessageInput from './MessageInput'

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
}

const SUGGESTIONS = [
  '分析当前项目的架构和技术栈',
  '帮我写一个 REST API 接口',
  '查找并修复代码中的 bug',
  '重构这段代码，提升可读性',
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
}: Props) {
  // Welcome screen — no active session
  if (!session) {
    return (
      <div className="flex-1 flex flex-col">
        {/* Center content */}
        <div className="flex-1 flex flex-col items-center justify-center px-6">
          {/* Logo + Title */}
          <div className="mb-3">
            <div className="w-14 h-14 rounded-2xl bg-white/10 backdrop-blur flex items-center justify-center border border-white/10">
              <Code2 size={28} className="text-blue-300" />
            </div>
          </div>
          <h1 className="text-3xl font-bold text-white mb-2 tracking-tight">你说，MyCode 来创造</h1>
          <p className="text-white/45 text-sm mb-10">智慧捕捉分析需求，构建全新完整应用，持续迭代业务存量</p>

          {/* Suggestions */}
          <div className="glass-card rounded-2xl p-5 max-w-lg w-full">
            <div className="flex items-center gap-2 text-white/50 text-xs mb-3">
              <Sparkles size={13} />
              <span>不知道从哪里开始？试试这些：</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={async () => {
                    const sess = await onCreate()
                    if (sess) {
                      // Small delay to let session activate
                      setTimeout(() => onSend(s), 100)
                    }
                  }}
                  className="px-3 py-1.5 rounded-lg text-xs text-white/60 bg-white/5 hover:bg-white/10 hover:text-white/80 border border-white/8 transition-all"
                >
                  {s}
                </button>
              ))}
            </div>
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
      />
      <MessageList
        messages={messages}
        streaming={streaming}
        streamText={streamText}
        streamParts={streamParts}
        loadingHistory={loadingHistory}
      />
      {error && (
        <div className="mx-4 mb-2 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-red-300 text-sm">
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
    </div>
  )
}
