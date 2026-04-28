import { useState } from 'react'
import {
  RefreshCcw,
  Plug,
  PlugZap,
  Power,
  PowerOff,
  ChevronDown,
  ChevronRight,
  Wrench,
  Plus,
  X,
  Trash2,
  Globe,
  Terminal,
  Server,
  Check,
  AlertCircle,
  Settings,
  Key,
  Variable,
} from 'lucide-react'
import type { McpStatus } from '../api/mcp'
import { connectMcp, disconnectMcp, addMcpServer, removeMcpServer } from '../api/mcp'

interface Props {
  status: McpStatus | null
  loading: boolean
  onRefresh: () => void
}

const STATUS_CONFIG: Record<string, { color: string; bg: string; label: string; dot: string }> = {
  connected: { color: 'text-status-success', bg: 'bg-status-success/10', label: '已连接', dot: 'bg-status-success' },
  disabled: { color: 'text-ink-muted', bg: 'bg-surface-2', label: '未启用', dot: 'bg-ink-faint' },
  failed: { color: 'text-status-error', bg: 'bg-status-error/10', label: '连接失败', dot: 'bg-status-error' },
  needs_auth: { color: 'text-status-warning', bg: 'bg-status-warning/10', label: '需认证', dot: 'bg-status-warning' },
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
  const [toast, setToast] = useState<{ type: 'success' | 'error'; message: string } | null>(null)
  // Advanced params
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([])
  const [headerVars, setHeaderVars] = useState<Array<{ key: string; value: string }>>([])

  const servers = status ? Object.entries(status.servers) : []
  const allTools = status?.tools ?? []
  const connectedCount = servers.filter(([, s]) => s === 'connected').length

  const showToast = (type: 'success' | 'error', message: string) => {
    setToast({ type, message })
    setTimeout(() => setToast(null), 3000)
  }

  const handleConnect = async (name: string) => {
    setBusyServer(name)
    try { await connectMcp(name); onRefresh(); showToast('success', `已连接 ${name}`) }
    catch { showToast('error', `连接 ${name} 失败`) }
    finally { setBusyServer(null) }
  }

  const handleDisconnect = async (name: string) => {
    setBusyServer(name)
    try { await disconnectMcp(name); onRefresh(); showToast('success', `已断开 ${name}`) }
    catch { showToast('error', `断开 ${name} 失败`) }
    finally { setBusyServer(null) }
  }

  const resetForm = () => {
    setAddName(''); setAddCommand(''); setAddUrl('')
    setEnvVars([]); setHeaderVars([]); setShowAdvanced(false)
  }

  const handleAdd = async () => {
    if (!addName.trim()) return
    setAdding(true)
    try {
      const env: Record<string, string> = {}
      envVars.forEach((v) => { if (v.key.trim()) env[v.key.trim()] = v.value })
      const hdrs: Record<string, string> = {}
      headerVars.forEach((v) => { if (v.key.trim()) hdrs[v.key.trim()] = v.value })

      if (addType === 'local') {
        const parts = addCommand.trim().split(/\s+/)
        if (parts.length === 0 || !parts[0]) return
        await addMcpServer({
          name: addName.trim(), type: 'local', command: parts,
          environment: Object.keys(env).length > 0 ? env : undefined,
        })
      } else {
        if (!addUrl.trim()) return
        await addMcpServer({
          name: addName.trim(), type: 'remote', url: addUrl.trim(),
          headers: Object.keys(hdrs).length > 0 ? hdrs : undefined,
          environment: Object.keys(env).length > 0 ? env : undefined,
        })
      }
      onRefresh(); setShowAdd(false); resetForm()
      showToast('success', `已添加服务器 ${addName.trim()}`)
    } catch { showToast('error', '添加服务器失败') }
    finally { setAdding(false) }
  }

  const handleRemove = async (name: string) => {
    if (!confirm(`确定要移除 MCP 服务器 "${name}"？`)) return
    setBusyServer(name)
    try { await removeMcpServer(name); onRefresh(); showToast('success', `已移除 ${name}`) }
    catch { showToast('error', `移除 ${name} 失败`) }
    finally { setBusyServer(null) }
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
      {/* Header */}
      <div className="px-3 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-[10px]">
              <Server size={10} className="text-ink-faint" />
              <span className="font-semibold text-ink-secondary">{servers.length}</span>
              <span className="text-ink-muted">服务器</span>
            </div>
            <div className="w-px h-3 bg-line-subtle" />
            <div className="flex items-center gap-1.5 text-[10px]">
              <Wrench size={10} className="text-ink-faint" />
              <span className="font-semibold text-ink-secondary">{allTools.length}</span>
              <span className="text-ink-muted">工具</span>
            </div>
            {connectedCount > 0 && (
              <>
                <div className="w-px h-3 bg-line-subtle" />
                <div className="flex items-center gap-1 text-[10px]">
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse" />
                  <span className="text-status-success font-medium">{connectedCount}</span>
                </div>
              </>
            )}
          </div>
          <button onClick={onRefresh} className="p-1.5 rounded-lg text-ink-muted hover:bg-surface-hover hover:text-ink transition-colors" title="刷新">
            <RefreshCcw size={12} />
          </button>
        </div>
      </div>

      {/* Add button */}
      <div className="px-3 pb-2">
        <button onClick={() => { setShowAdd(!showAdd); if (showAdd) resetForm() }}
          className={`w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
            showAdd ? 'bg-surface-2 text-ink-secondary border border-line' : 'bg-accent text-white hover:bg-accent-hover shadow-xs'
          }`}>
          {showAdd ? <X size={11} /> : <Plus size={11} />}
          <span>{showAdd ? '取消' : '添加服务器'}</span>
        </button>
      </div>

      {/* Add form */}
      {showAdd && (
        <div className="px-3 pb-3 animate-slide-up">
          <div className="rounded-xl border border-line bg-surface-1 overflow-hidden">
            {/* Name */}
            <div className="px-3 pt-3 pb-2">
              <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider mb-1 block">服务器名称</label>
              <input type="text" value={addName} onChange={(e) => setAddName(e.target.value)} placeholder="my-server"
                className="w-full bg-surface-2 rounded-lg px-3 py-1.5 text-xs text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 focus:bg-surface-0 transition-all" />
            </div>

            {/* Type */}
            <div className="px-3 pb-2">
              <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider mb-1 block">连接方式</label>
              <div className="grid grid-cols-2 gap-1.5">
                {([
                  { v: 'local' as const, icon: Terminal, label: '本地 (stdio)' },
                  { v: 'remote' as const, icon: Globe, label: '远程 (HTTP)' },
                ]).map(({ v, icon: Icon, label }) => (
                  <button key={v} onClick={() => setAddType(v)}
                    className={`flex items-center justify-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all border ${
                      addType === v ? 'bg-accent/10 text-accent border-accent/20' : 'bg-surface-2 text-ink-muted border-transparent hover:border-line'
                    }`}>
                    <Icon size={11} /><span>{label}</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Command / URL */}
            <div className="px-3 pb-2">
              <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider mb-1 block">
                {addType === 'local' ? '启动命令' : '服务器 URL'}
              </label>
              <input type="text"
                value={addType === 'local' ? addCommand : addUrl}
                onChange={(e) => addType === 'local' ? setAddCommand(e.target.value) : setAddUrl(e.target.value)}
                placeholder={addType === 'local' ? 'python -m mcp.server' : 'http://localhost:9000/sse'}
                className="w-full bg-surface-2 rounded-lg px-3 py-1.5 text-xs text-ink placeholder:text-ink-muted outline-none border border-line-subtle focus:border-accent/30 focus:bg-surface-0 font-mono transition-all" />
            </div>

            {/* Advanced toggle */}
            <div className="px-3 pb-1">
              <button onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center gap-1.5 text-[10px] text-ink-muted hover:text-ink-secondary transition-colors">
                <Settings size={10} />
                <span>{showAdvanced ? '收起高级设置' : '高级设置'}</span>
                {showAdvanced ? <ChevronDown size={9} /> : <ChevronRight size={9} />}
              </button>
            </div>

            {/* Advanced: Environment variables */}
            {showAdvanced && (
              <div className="px-3 pb-2 space-y-2 animate-slide-up">
                {/* Environment */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider flex items-center gap-1">
                      <Variable size={9} />环境变量
                    </label>
                    <button onClick={() => setEnvVars((p) => [...p, { key: '', value: '' }])}
                      className="text-[9px] text-accent hover:underline">+ 添加</button>
                  </div>
                  {envVars.length === 0 ? (
                    <div className="text-[10px] text-ink-faint px-1">无</div>
                  ) : (
                    <div className="space-y-1">
                      {envVars.map((v, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <input value={v.key} onChange={(e) => setEnvVars((p) => p.map((x, idx) => idx === i ? { ...x, key: e.target.value } : x))}
                            placeholder="KEY" className="w-[40%] bg-surface-2 rounded px-2 py-1 text-[10px] text-ink outline-none border border-line-subtle focus:border-accent/30 font-mono" />
                          <span className="text-ink-faint text-[10px]">=</span>
                          <input value={v.value} onChange={(e) => setEnvVars((p) => p.map((x, idx) => idx === i ? { ...x, value: e.target.value } : x))}
                            placeholder="value" className="flex-1 bg-surface-2 rounded px-2 py-1 text-[10px] text-ink outline-none border border-line-subtle focus:border-accent/30 font-mono" />
                          <button onClick={() => setEnvVars((p) => p.filter((_, idx) => idx !== i))}
                            className="p-0.5 text-ink-faint hover:text-status-error transition-colors"><X size={9} /></button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Headers (remote only) */}
                {addType === 'remote' && (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-[10px] font-medium text-ink-muted uppercase tracking-wider flex items-center gap-1">
                        <Key size={9} />HTTP Headers
                      </label>
                      <button onClick={() => setHeaderVars((p) => [...p, { key: '', value: '' }])}
                        className="text-[9px] text-accent hover:underline">+ 添加</button>
                    </div>
                    {headerVars.length === 0 ? (
                      <div className="text-[10px] text-ink-faint px-1">无</div>
                    ) : (
                      <div className="space-y-1">
                        {headerVars.map((v, i) => (
                          <div key={i} className="flex items-center gap-1.5">
                            <input value={v.key} onChange={(e) => setHeaderVars((p) => p.map((x, idx) => idx === i ? { ...x, key: e.target.value } : x))}
                              placeholder="Header-Name" className="w-[40%] bg-surface-2 rounded px-2 py-1 text-[10px] text-ink outline-none border border-line-subtle focus:border-accent/30 font-mono" />
                            <span className="text-ink-faint text-[10px]">:</span>
                            <input value={v.value} onChange={(e) => setHeaderVars((p) => p.map((x, idx) => idx === i ? { ...x, value: e.target.value } : x))}
                              placeholder="value" className="flex-1 bg-surface-2 rounded px-2 py-1 text-[10px] text-ink outline-none border border-line-subtle focus:border-accent/30 font-mono" />
                            <button onClick={() => setHeaderVars((p) => p.filter((_, idx) => idx !== i))}
                              className="p-0.5 text-ink-faint hover:text-status-error transition-colors"><X size={9} /></button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Submit */}
            <div className="px-3 py-2 flex justify-end border-t border-line-subtle bg-surface-0">
              <button onClick={handleAdd}
                disabled={adding || !addName.trim() || (addType === 'local' ? !addCommand.trim() : !addUrl.trim())}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-accent text-white text-[11px] font-medium hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-xs">
                {adding ? <RefreshCcw size={10} className="animate-spin" /> : <Plus size={10} />}
                <span>添加并连接</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div className="px-3 pb-2 animate-slide-up">
          <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-[11px] font-medium ${
            toast.type === 'success' ? 'bg-status-success/10 text-status-success border border-status-success/20' : 'bg-status-error/10 text-status-error border border-status-error/20'
          }`}>
            {toast.type === 'success' ? <Check size={11} /> : <AlertCircle size={11} />}
            <span>{toast.message}</span>
          </div>
        </div>
      )}

      {/* Server list */}
      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {servers.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 px-4 py-10 text-center">
            <div className="w-10 h-10 rounded-2xl bg-surface-2 flex items-center justify-center">
              <Plug size={18} className="text-ink-faint" />
            </div>
            <div>
              <div className="text-xs text-ink-secondary">未配置 MCP 服务器</div>
              <div className="text-xxs text-ink-faint mt-0.5">点击上方按钮添加</div>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {servers.map(([name, serverStatus]) => {
              const cfg = STATUS_CONFIG[serverStatus] || STATUS_CONFIG.disabled
              const isExpanded = expandedServer === name
              const isBusy = busyServer === name
              const isConnected = serverStatus === 'connected'
              const serverTools = allTools.filter((t) => t.startsWith(`${name}_`))

              return (
                <div key={name} className={`rounded-xl border transition-all overflow-hidden ${
                  isExpanded ? 'border-line bg-surface-1 shadow-xs' : 'border-transparent hover:border-line bg-surface-1 hover:bg-surface-hover'
                }`}>
                  <div className="flex items-center gap-2 px-3 py-2">
                    <button onClick={() => setExpandedServer(isExpanded ? null : name)} className="flex-shrink-0">
                      {isExpanded ? <ChevronDown size={11} className="text-ink-muted" /> : <ChevronRight size={11} className="text-ink-muted" />}
                    </button>
                    <div className="flex-1 min-w-0 cursor-pointer" onClick={() => setExpandedServer(isExpanded ? null : name)}>
                      <div className="flex items-center gap-1.5">
                        <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${cfg.dot}`} />
                        <span className="text-xs font-medium text-ink-strong truncate">{name}</span>
                      </div>
                      <div className="flex items-center gap-2 mt-0.5 ml-3">
                        <span className={`inline-flex items-center px-1.5 py-0.5 rounded-md text-[9px] font-medium ${cfg.bg} ${cfg.color}`}>{cfg.label}</span>
                        {serverTools.length > 0 && <span className="text-[9px] text-ink-faint flex items-center gap-0.5"><Wrench size={8} />{serverTools.length}</span>}
                      </div>
                    </div>
                    {isBusy ? (
                      <RefreshCcw size={11} className="animate-spin text-accent flex-shrink-0" />
                    ) : (
                      <div className="flex items-center gap-0.5 flex-shrink-0">
                        {isConnected ? (
                          <button onClick={() => handleDisconnect(name)} className="p-1 rounded-lg text-ink-muted hover:bg-status-error/10 hover:text-status-error transition-colors" title="断开">
                            <PowerOff size={11} />
                          </button>
                        ) : (
                          <button onClick={() => handleConnect(name)} className="p-1 rounded-lg text-ink-muted hover:bg-status-success/10 hover:text-status-success transition-colors" title="连接">
                            <Power size={11} />
                          </button>
                        )}
                        <button onClick={() => handleRemove(name)} className="p-1 rounded-lg text-ink-muted hover:bg-status-error/10 hover:text-status-error transition-colors" title="移除">
                          <Trash2 size={10} />
                        </button>
                      </div>
                    )}
                  </div>

                  {isExpanded && (
                    <div className="border-t border-line-subtle">
                      {serverTools.length > 0 ? (
                        <div className="px-3 py-2">
                          <div className="flex items-center gap-1.5 mb-1.5">
                            <Wrench size={9} className="text-ink-faint" />
                            <span className="text-[9px] font-medium text-ink-muted uppercase tracking-wider">工具 ({serverTools.length})</span>
                          </div>
                          <div className="space-y-0.5">
                            {serverTools.map((tool) => (
                              <div key={tool} className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-surface-2 hover:bg-surface-hover transition-colors">
                                <div className="w-1 h-1 rounded-full bg-accent/40 flex-shrink-0" />
                                <span className="text-[10px] font-mono text-ink-secondary truncate">{tool.replace(`${name}_`, '')}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : (
                        <div className="px-3 py-3 text-center">
                          <span className="text-[10px] text-ink-faint">{isConnected ? '无工具' : '连接后查看工具'}</span>
                        </div>
                      )}
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
