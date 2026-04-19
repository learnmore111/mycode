import { useState } from 'react'
import { RefreshCcw, Plug, PlugZap, Power, PowerOff, ChevronDown, ChevronRight, Wrench, Plus, X, Trash2 } from 'lucide-react'
import type { McpStatus } from '../api/mcp'
import { connectMcp, disconnectMcp, addMcpServer, removeMcpServer } from '../api/mcp'

interface Props {
  status: McpStatus | null
  loading: boolean
  onRefresh: () => void
}

const STATUS_STYLES: Record<string, { color: string; label: string }> = {
  connected: { color: 'text-status-success', label: '已连接' },
  disabled: { color: 'text-ink-muted', label: '未启用' },
  failed: { color: 'text-status-error', label: '连接失败' },
  needs_auth: { color: 'text-status-warning', label: '需认证' },
}

export default function McpSidebar({ status, loading, onRefresh }: Props) {
  const [expandedServer, setExpandedServer] = useState<string | null>(null)
  const [busyServer, setBusyServer] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [addName, setAddName] = useState('')
  const [addType, setAddType] = useState<'local' | 'remote'>('local')
  const [addCommand, setAddCommand] = useState('')
  const [addUrl, setAddUrl] = useState('')
  const [adding, setAdding] = useState(false)

  const servers = status ? Object.entries(status.servers) : []
  const allTools = status?.tools ?? []

  const handleConnect = async (name: string) => {
    setBusyServer(name)
    try {
      await connectMcp(name)
      onRefresh()
    } catch (err) {
      console.error('Connect MCP failed', err)
    } finally {
      setBusyServer(null)
    }
  }

  const handleDisconnect = async (name: string) => {
    setBusyServer(name)
    try {
      await disconnectMcp(name)
      onRefresh()
    } catch (err) {
      console.error('Disconnect MCP failed', err)
    } finally {
      setBusyServer(null)
    }
  }

  const handleAdd = async () => {
    if (!addName.trim()) return
    setAdding(true)
    try {
      if (addType === 'local') {
        const parts = addCommand.trim().split(/\s+/)
        if (parts.length === 0 || !parts[0]) return
        await addMcpServer({ name: addName.trim(), type: 'local', command: parts })
      } else {
        if (!addUrl.trim()) return
        await addMcpServer({ name: addName.trim(), type: 'remote', url: addUrl.trim() })
      }
      onRefresh()
      setShowAdd(false)
      setAddName('')
      setAddCommand('')
      setAddUrl('')
    } catch (err) {
      console.error('Add MCP server failed', err)
    } finally {
      setAdding(false)
    }
  }

  const handleRemove = async (name: string) => {
    if (!confirm(`确定要移除 MCP 服务器 "${name}"？`)) return
    setBusyServer(name)
    try {
      await removeMcpServer(name)
      onRefresh()
    } catch (err) {
      console.error('Remove MCP server failed', err)
    } finally {
      setBusyServer(null)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 px-4 py-10">
        <RefreshCcw size={16} className="animate-spin text-accent" />
        <span className="text-xs text-ink-muted">加载 MCP 状态...</span>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-3 pb-3">
        <div className="rounded-2xl border border-line bg-surface-1 px-3.5 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink-strong">
              <PlugZap size={14} className="text-accent" />
              <span>MCP 服务器</span>
            </div>
            <button
              onClick={onRefresh}
              className="p-2 rounded-xl text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors"
              title="刷新"
            >
              <RefreshCcw size={13} />
            </button>
          </div>
          <div className="mt-1 text-[11px] text-ink-muted">
            {servers.length} 个服务器 · {allTools.length} 个工具
          </div>
        </div>

        {/* Add button */}
        <button
          onClick={() => setShowAdd(!showAdd)}
          className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
            showAdd
              ? 'bg-surface-2 text-ink-secondary'
              : 'bg-accent text-white hover:bg-accent-hover shadow-xs'
          }`}
        >
          {showAdd ? <X size={12} /> : <Plus size={12} />}
          <span>{showAdd ? '取消' : '添加服务器'}</span>
        </button>

        {/* Add form */}
        {showAdd && (
          <div className="rounded-xl border border-line bg-surface-1 p-3 space-y-2.5 animate-slide-up">
            <input
              type="text"
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              placeholder="服务器名称"
              className="w-full bg-surface-2 rounded-lg px-3 py-2 text-xs text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 transition-colors"
            />
            <div className="flex items-center gap-2">
              <button
                onClick={() => setAddType('local')}
                className={`px-2.5 py-1 rounded-lg text-xxs font-medium transition-colors ${
                  addType === 'local' ? 'bg-accent text-white' : 'bg-surface-2 text-ink-muted hover:text-ink-secondary'
                }`}
              >
                本地 (stdio)
              </button>
              <button
                onClick={() => setAddType('remote')}
                className={`px-2.5 py-1 rounded-lg text-xxs font-medium transition-colors ${
                  addType === 'remote' ? 'bg-accent text-white' : 'bg-surface-2 text-ink-muted hover:text-ink-secondary'
                }`}
              >
                远程 (HTTP)
              </button>
            </div>
            {addType === 'local' ? (
              <input
                type="text"
                value={addCommand}
                onChange={(e) => setAddCommand(e.target.value)}
                placeholder="启动命令（如 python -m mcp.server）"
                className="w-full bg-surface-2 rounded-lg px-3 py-2 text-xs text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 font-mono transition-colors"
              />
            ) : (
              <input
                type="text"
                value={addUrl}
                onChange={(e) => setAddUrl(e.target.value)}
                placeholder="服务器 URL（如 http://localhost:9000）"
                className="w-full bg-surface-2 rounded-lg px-3 py-2 text-xs text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 font-mono transition-colors"
              />
            )}
            <div className="flex justify-end">
              <button
                onClick={handleAdd}
                disabled={adding || !addName.trim() || (addType === 'local' ? !addCommand.trim() : !addUrl.trim())}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-white text-xxs font-medium hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {adding ? <RefreshCcw size={10} className="animate-spin" /> : <Plus size={10} />}
                <span>添加并连接</span>
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {servers.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
            <div className="w-12 h-12 rounded-2xl bg-surface-2 flex items-center justify-center">
              <Plug size={20} className="text-ink-faint" />
            </div>
            <div>
              <div className="text-sm text-ink-secondary">未配置 MCP 服务器</div>
              <div className="text-xs text-ink-faint mt-1">在 mycode.json 中配置 mcp 字段</div>
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            {servers.map(([name, serverStatus]) => {
              const style = STATUS_STYLES[serverStatus] || STATUS_STYLES.disabled
              const isExpanded = expandedServer === name
              const isBusy = busyServer === name
              const isConnected = serverStatus === 'connected'
              // Find tools belonging to this server
              const serverTools = allTools.filter((t) => t.startsWith(`${name}_`))

              return (
                <div key={name} className="rounded-xl border border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover transition-all overflow-hidden">
                  <div className="flex items-center gap-2 px-3 py-2.5">
                    <button onClick={() => setExpandedServer(isExpanded ? null : name)} className="flex-shrink-0">
                      {isExpanded ? (
                        <ChevronDown size={12} className="text-ink-muted" />
                      ) : (
                        <ChevronRight size={12} className="text-ink-muted" />
                      )}
                    </button>

                    <div
                      className="flex-1 min-w-0 cursor-pointer"
                      onClick={() => setExpandedServer(isExpanded ? null : name)}
                    >
                      <div className="flex items-center gap-2">
                        <Plug size={12} className={style.color} />
                        <span className="text-xs font-medium text-ink-strong truncate">{name}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5 ml-5">
                        <span className={`text-xxs font-medium ${style.color}`}>{style.label}</span>
                        {serverTools.length > 0 && (
                          <span className="text-xxs text-ink-faint">{serverTools.length} 工具</span>
                        )}
                      </div>
                    </div>

                    {isBusy ? (
                      <RefreshCcw size={12} className="animate-spin text-ink-muted flex-shrink-0" />
                    ) : (
                      <div className="flex items-center gap-0.5 flex-shrink-0">
                        {isConnected ? (
                          <button
                            onClick={() => handleDisconnect(name)}
                            className="p-1.5 rounded-lg text-ink-muted hover:bg-status-error-light hover:text-status-error transition-colors"
                            title="断开连接"
                          >
                            <PowerOff size={12} />
                          </button>
                        ) : (
                          <button
                            onClick={() => handleConnect(name)}
                            className="p-1.5 rounded-lg text-ink-muted hover:bg-status-success-light hover:text-status-success transition-colors"
                            title="连接"
                          >
                            <Power size={12} />
                          </button>
                        )}
                        <button
                          onClick={() => handleRemove(name)}
                          className="p-1.5 rounded-lg text-ink-muted hover:bg-status-error-light hover:text-status-error transition-colors"
                          title="移除"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    )}
                  </div>

                  {isExpanded && serverTools.length > 0 && (
                    <div className="px-3 pb-2.5 border-t border-line-subtle">
                      <div className="mt-2 space-y-1">
                        {serverTools.map((tool) => (
                          <div key={tool} className="flex items-center gap-2 px-2 py-1 rounded-lg bg-surface-2">
                            <Wrench size={10} className="text-ink-faint flex-shrink-0" />
                            <span className="text-xxs font-mono text-ink-secondary truncate">
                              {tool.replace(`${name}_`, '')}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
