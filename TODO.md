# TODO

## 待实现功能

### 对话轮次回滚（高优先级）

支持用户选择回退到之前任意一轮对话的状态，包括消息历史和文件状态的恢复。

**后端：**
- [ ] MessageTable 添加 turn_number/sequence 字段，标识每轮对话
- [ ] 每轮对话（assistant 回复完成）后自动创建文件快照，关联 turn_number
- [ ] 新增 API：`POST /session/{id}/rollback` — 接受 turn_number，删除该轮之后的所有消息，恢复对应的文件快照
- [ ] 利用已有的 `snapshot/` 模块（shadow git）实现文件状态保存与恢复
- [ ] 利用 MessageTable 已预留的 `parent_id` 字段建立消息链

**前端：**
- [ ] 消息列表中每轮对话旁显示回滚按钮（如时间线上的节点）
- [ ] 点击后弹出确认，调用 rollback API
- [ ] 回滚成功后刷新消息列表和 Git 状态
- [ ] 移除当前的"返回上一会话"按钮（被此功能替代）

**参考：**
- `mycode/snapshot/snapshot.py` — 文件快照（track/diff/patch/restore）
- `mycode/storage/models.py` — SessionTable.revert（预留 JSON 字段）、MessageTable.parent_id（预留）
- `mycode/server/routes/session.py` — 需新增 rollback 端点
- `mycode/session/message.py` — 消息持久化逻辑
