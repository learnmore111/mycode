import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  addEdge,
  MarkerType,
  Handle,
  Position,
  NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Zap, Layers, Trash2, Plus, Bot, GitBranch } from 'lucide-react'

// ── Types ──
export interface StageData {
  id: string
  parallel: boolean
  runs_on: string
  depends_on: string
  inputs: string
  spawns: Array<{ agent: string; task: string }>
}

interface FlowDAGEditorProps {
  stages: StageData[]
  agents: Array<{ name: string; role: string }>
  onChange: (stages: StageData[]) => void
  onAddStage: () => void
  agentSelectOptions: Array<{ value: string; label: string }>
  onSelectSpawnAgent: (stageIdx: number, spawnIdx: number, v: string) => void
  onUpdateStageField: (idx: number, field: keyof StageData, val: string | boolean) => void
  onRemoveStage: (idx: number) => void
  onAddSpawn: (si: number) => void
  onRemoveSpawn: (si: number, spi: number) => void
}

// ── Stage Node Component ──
function StageNode({ data, id }: NodeProps<Node<{ stageData: StageData; index: number; agentSelectOptions: Array<{ value: string; label: string }>; onSelectSpawnAgent: (stageIdx: number, spawnIdx: number, v: string) => void; onUpdateStageField: (idx: number, field: keyof StageData, val: string | boolean) => void; onRemoveStage: (idx: number) => void; onAddSpawn: (si: number) => void; onRemoveSpawn: (si: number, spi: number) => void }>>) {
  const [expanded, setExpanded] = useState(false)
  const s = data.stageData

  return (
    <div className="group relative min-w-[220px] max-w-[280px] rounded-xl border border-[#E5E4E0] bg-white shadow-[0_2px_8px_rgba(0,0,0,0.06)] hover:shadow-md transition-shadow">
      {/* Handles for connections */}
      <Handle type="target" position={Position.Top} className="!w-3 !h-3 !bg-[#3D3BF3] !border-2 !border-white !-top-[6px]" />
      <Handle type="source" position={Position.Bottom} className="!w-3 !h-3 !bg-[#3D3BF3] !border-2 !border-white !-bottom-[6px]" />

      {/* Header */}
      <div className={`flex items-center gap-2 px-3 py-2.5 rounded-t-xl ${s.parallel ? 'bg-[#d97706]/8' : 'bg-[#3D3BF3]/8'}`}>
        {s.parallel ? <Zap size={13} className="text-[#d97706]" /> : <Layers size={13} className="text-[#3D3BF3]" />}
        <input
          value={s.id}
          onChange={(e) => data.onUpdateStageField(data.index, 'id', e.target.value)}
          placeholder="stage-id"
          className="flex-1 bg-transparent text-[12px] font-bold text-[#0F0F0F] outline-none font-[JetBrains_Mono,monospace]"
        />
        {s.parallel && (
          <span className="px-1.5 py-0.5 rounded bg-[#d97706]/15 text-[9px] font-bold text-[#d97706]">并行</span>
        )}
        <button
          onClick={() => data.onRemoveStage(data.index)}
          className="opacity-0 group-hover:opacity-100 p-1 rounded text-[#ABABAB] hover:text-[#dc2626] transition-all"
        >
          <Trash2 size={11} />
        </button>
      </div>

      {/* Body - collapsible */}
      <div className={`overflow-hidden transition-all duration-200 ${expanded ? 'max-h-[400px]' : 'max-h-0'}`}>
        <div className="px-3 py-2.5 space-y-2 border-t border-[#F4F3F0]">
          {/* runs_on */}
          <input
            value={s.runs_on}
            onChange={(e) => data.onUpdateStageField(data.index, 'runs_on', e.target.value)}
            placeholder="runs_on"
            className="w-full bg-[#FAFAF8] rounded-lg px-2.5 py-1.5 text-[11px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]"
          />
          {/* depends_on */}
          <input
            value={s.depends_on}
            onChange={(e) => data.onUpdateStageField(data.index, 'depends_on', e.target.value)}
            placeholder="depends_on (逗号分隔)"
            className="w-full bg-[#FAFAF8] rounded-lg px-2.5 py-1.5 text-[11px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]"
          />
          {/* inputs */}
          <input
            value={s.inputs}
            onChange={(e) => data.onUpdateStageField(data.index, 'inputs', e.target.value)}
            placeholder="inputs (如 research.*)"
            className="w-full bg-[#FAFAF8] rounded-lg px-2.5 py-1.5 text-[11px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]"
          />
          {/* parallel toggle */}
          <label className="flex items-center gap-1.5 text-[11px] text-[#5C5C5C] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={s.parallel}
              onChange={(e) => data.onUpdateStageField(data.index, 'parallel', e.target.checked)}
              className="w-3.5 h-3.5 rounded border-[#D4D3CF] text-[#3D3BF3]"
            />
            <Zap size={10} className="text-[#d97706]" />并行执行
          </label>
          {/* Spawns */}
          <div className="pl-2 border-l-2 border-[#3D3BF3]/20 space-y-1.5 mt-1">
            {s.spawns.map((sp, spi) => (
              <div key={spi} className="flex items-center gap-1.5">
                <select
                  value={sp.agent}
                  onChange={(e) => data.onSelectSpawnAgent(data.index, spi, e.target.value)}
                  className="w-24 bg-white rounded px-1.5 py-1 text-[10px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]"
                >
                  <option value="">Agent</option>
                  {data.agentSelectOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
                <input
                  value={sp.task}
                  onChange={(e) => {
                    const newSpawns = [...s.spawns]
                    newSpawns[spi] = { ...newSpawns[spi], task: e.target.value }
                    // We need to update through a different mechanism since we don't have direct setStages
                    // Use a hidden input approach or callback
                    const event = new CustomEvent('dag:spawn-update', {
                      detail: { stageIdx: data.index, spawnIdx: spi, task: e.target.value },
                    })
                    window.dispatchEvent(event)
                  }}
                  placeholder="任务描述"
                  className="flex-1 bg-white rounded px-1.5 py-1 text-[10px] outline-none border border-[#E5E4E0] focus:border-[#3D3BF3]/30 font-[JetBrains_Mono,monospace]"
                />
                <button
                  onClick={() => data.onRemoveSpawn(data.index, spi)}
                  className="p-0.5 text-[#D4D3CF] hover:text-[#dc2626]"
                >
                  <Trash2 size={10} />
                </button>
              </div>
            ))}
            <button
              onClick={() => data.onAddSpawn(data.index)}
              className="text-[10px] text-[#3D3BF3] font-semibold hover:underline"
            >+ Spawn</button>
          </div>
        </div>
      </div>

      {/* Footer / Expand toggle + spawn count */}
      <div className="px-3 py-2 flex items-center justify-between border-t border-[#F4F3F0]">
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-[10px] text-[#3D3BF3] font-medium hover:underline flex items-center gap-1"
        >
          {expanded ? '收起详情' : '展开编辑'}
          <GitBranch size={9} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </button>
        <div className="flex items-center gap-1.5 text-[10px] text-[#ABABAB]">
          {s.spawns.length > 0 && (
            <>
              <Bot size={9} />
              <span>{s.spawns.length} spawn{s.spawns.length > 1 ? 's' : ''}</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const nodeTypes = { stageNode: StageNode }

// ── Auto-layout using topological sort (Kahn-like layering) ──
function autoLayout(stages: StageData[]): Array<{ x: number; y: number; stageId: string }> {
  if (stages.length === 0) return []

  const stageIdSet = new Set(stages.filter((s) => s.id.trim()).map((s) => s.id.trim()))

  // Build adjacency: depends_on -> stage (edge from dependency to dependent)
  const inDegree = new Map<string, number>()
  const dependents = new Map<string, string[]>()

  for (const s of stages) {
    const sid = s.id.trim()
    if (!sid) continue
    if (!inDegree.has(sid)) inDegree.set(sid, 0)
    if (!dependents.has(sid)) dependents.set(sid, [])

    const deps = s.depends_on.split(',').map((d) => d.trim()).filter((d) => d && stageIdSet.has(d))
    inDegree.set(sid, deps.length)

    for (const dep of deps) {
      const lst = dependents.get(dep) || []
      lst.push(sid)
      dependents.set(dep, lst)
    }
  }

  // Topological layers
  const layers: string[][] = []
  const remaining = new Set(inDegree.keys())
  const processed = new Set<string>()

  while (remaining.size > 0) {
    // Find all nodes with in-degree 0 (or whose deps are all processed)
    const layer: string[] = []
    for (const sid of remaining) {
      const s = stages.find((x) => x.id.trim() === sid)
      if (!s) continue
      const deps = s.depends_on.split(',').map((d) => d.trim()).filter(Boolean)
      if (deps.every((d) => processed.has(d) || !stageIdSet.has(d))) {
        layer.push(sid)
      }
    }

    if (layer.length === 0) {
      // Break cycles — push remaining as next layer
      layer.push(...Array.from(remaining))
    }

    layers.push(layer)
    for (const sid of layer) {
      processed.add(sid)
      remaining.delete(sid)
    }
  }

  // Calculate positions
  const NODE_WIDTH = 260
  const NODE_HEIGHT = 120
  const H_GAP = 80
  const V_GAP = 100

  const positions: Array<{ x: number; y: number; stageId: string }> = []

  for (let li = 0; li < layers.length; li++) {
    const layer = layers[li]
    const y = li * (NODE_HEIGHT + V_GAP)
    const totalWidth = layer.length * NODE_WIDTH + (layer.length - 1) * H_GAP
    let startX = -(totalWidth / 2)

    for (let ni = 0; ni < layer.length; ni++) {
      positions.push({
        x: startX + ni * (NODE_WIDTH + H_GAP),
        y,
        stageId: layer[ni],
      })
    }
  }

  // Place any unplaced stages (no id yet)
  const placedIds = new Set(positions.map((p) => p.stageId))
  let fallbackY = layers.length * (NODE_HEIGHT + V_GAP) + 50
  for (const s of stages) {
    if (!placedIds.has(s.id.trim())) {
      positions.push({
        x: (positions.length % 3) * (NODE_WIDTH + H_GAP),
        y: fallbackY,
        stageId: s.id.trim() || `new-${positions.length}`,
      })
      if (positions.filter((p) => p.y === fallbackY).length >= 3) fallbackY += NODE_HEIGHT + V_GAP
    }
  }

  return positions
}

// ── Main DAG Editor ──
export default function FlowDAGEditor({
  stages,
  agents,
  onChange,
  onAddStage,
  agentSelectOptions,
  onSelectSpawnAgent,
  onUpdateStageField,
  onRemoveStage,
  onAddSpawn,
  onRemoveSpawn,
}: FlowDAGEditorProps) {
  // Build initial nodes from stages
  const buildNodes = useCallback(
    (stg: StageData[]): Node[] => {
      const layout = autoLayout(stg)
      return stg.map((s, i) => {
        const pos = layout.find((p) => p.stageId === s.id.trim())
        return {
          id: `stage-${i}`,
          type: 'stageNode',
          position: pos ? { x: pos.x, y: pos.y } : { x: i * 300, y: i * 140 },
          data: {
            stageData: s,
            index: i,
            agentSelectOptions,
            onSelectSpawnAgent,
            onUpdateStageField,
            onRemoveStage,
            onAddSpawn,
            onRemoveSpawn,
          },
        }
      })
    },
    [agentSelectOptions, onSelectSpawnAgent, onUpdateStageField, onRemoveStage, onAddSpawn, onRemoveSpawn],
  )

  const buildEdges = useCallback(
    (stg: StageData[]): Edge[] => {
      const edges: Edge[] = []
      const stageIndexMap = new Map(stg.map((s, i) => [s.id.trim(), i]))

      stg.forEach((s, si) => {
        const deps = s.depends_on.split(',').map((d) => d.trim()).filter(Boolean)
        deps.forEach((dep) => {
          const sourceIdx = stageIndexMap.get(dep)
          if (sourceIdx !== undefined && dep !== s.id.trim()) {
            edges.push({
              id: `edge-${dep}-${s.id.trim()}-${si}`,
              source: `stage-${sourceIdx}`,
              target: `stage-${si}`,
              animated: s.parallel,
              style: { stroke: s.parallel ? '#d97706' : '#3D3BF3', strokeWidth: 2 },
              markerEnd: {
                type: MarkerType.ArrowClosed,
                color: s.parallel ? '#d97706' : '#3D3BF3',
                width: 16,
                height: 16,
              },
              type: 'smoothstep',
            })
          }
        })
      })

      return edges
    },
    [],
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(buildNodes(stages))
  const [edges, setEdges, onEdgesChange] = useEdgesState(buildEdges(stages))

  // Sync when external stages change
  useEffect(() => {
    const newNodes = buildNodes(stages)
    const newEdges = buildEdges(stages)
    setNodes(newNodes)
    setEdges(newEdges)
  }, [stages, buildNodes, buildEdges, setNodes, setEdges])

  // Listen for spawn updates from inside node components
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { stageIdx: number; spawnIdx: number; task: string }
      const updated = [...stages]
      if (updated[detail.stageIdx]?.spawns[detail.spawnIdx]) {
        updated[detail.stageIdx].spawns[detail.spawnIdx] = {
          ...updated[detail.stageIdx].spawns[detail.spawnIdx],
          task: detail.task,
        }
        onChange(updated)
      }
    }
    window.addEventListener('dag:spawn-update', handler)
    return () => window.removeEventListener('dag:spawn-update', handler)
  }, [stages, onChange])

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return
      const sourceIdx = parseInt(connection.source.replace('stage-', ''))
      const targetIdx = parseInt(connection.target.replace('stage-', ''))

      if (isNaN(sourceIdx) || isNaN(targetIdx)) return
      if (sourceIdx === targetIdx) return

      const updated = [...stages]
      const targetStage = updated[targetIdx]
      const sourceStage = updated[sourceIdx]
      if (!targetStage || !sourceStage) return

      // Add source stage's id to target's depends_on
      const srcId = sourceStage.id.trim()
      if (!srcId) return
      const currentDeps = targetStage.depends_on.split(',').map((d) => d.trim()).filter(Boolean)
      if (!currentDeps.includes(srcId)) {
        currentDeps.push(srcId)
        updated[targetIdx] = { ...targetStage, depends_on: currentDeps.join(', ') }
        onChange(updated)
      }
    },
    [stages, onChange],
  )

  const handleAddNode = useCallback(() => {
    onAddStage()
  }, [onAddStage])

  return (
    <div className="relative w-full h-full min-h-[500px] rounded-xl overflow-hidden border border-[#E5E4E0] bg-[#FAFAF8]">
      {/* Floating toolbar */}
      <div className="absolute top-4 left-4 z-10 flex items-center gap-2">
        <button
          onClick={handleAddNode}
          className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[#3D3BF3] text-white text-[11px] font-bold shadow-lg shadow-[#3D3BF3]/20 hover:bg-[#3230D8] transition-colors"
        >
          <Plus size={13} />添加阶段
        </button>
        <div className="px-3.5 py-2 rounded-xl bg-white/90 backdrop-blur-sm border border-[#E5E4E0] text-[11px] text-[#8A8A85] font-medium shadow-sm">
          {stages.filter((s) => s.id.trim()).length} 个节点 · 拖拽连线建立依赖
        </div>
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        defaultEdgeOptions={{
          type: 'smoothstep',
          style: { strokeWidth: 2, stroke: '#3D3BF3' },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#3D3BF3', width: 16, height: 16 },
        }}
        proOptions={{ hideAttribution: true }}
        className="!bg-[#FAFAF8]"
      >
        <Background gap={18} size={1} color="#E5E4E0" />
        <Controls
          showInteractive={false}
          className="!rounded-xl !shadow-lg !border-[#E5E4E0] !overflow-hidden"
        />
        <MiniMap
          nodeColor={() => '#3D3BF3'}
          maskColor="rgba(61,59,243,0.06)"
          className="!rounded-xl !shadow-lg !border-[#E5E4E0]"
          style={{ display: stages.length <= 2 ? 'none' : undefined }}
        />
      </ReactFlow>

      {stages.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-0">
          <div className="text-center space-y-3 pointer-events-auto">
            <div className="mx-auto w-14 h-14 rounded-2xl bg-[#3D3BF3]/8 flex items-center justify-center">
              <Layers size={24} className="text-[#3D3BF3]" />
            </div>
            <div>
              <p className="text-[14px] font-semibold text-[#0F0F0F]">可视化 DAG 编排</p>
              <p className="text-[12px] text-[#ABABAB] mt-1">点击「添加阶段」创建节点，从节点底部拖拽到另一节点顶部建立依赖关系</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
