# Changelog

## [1.3.1] - 2026-07-27

### Added

- 通用维护、回顾、归档恢复、知识图谱、工作区迁移和健康报告能力。
- 成长治理：结构化反思、五维巡检、GEP 策略、审计/OTP 审批、部署验证、自动回滚与版本审计。
- 通用 Heartbeat、调度协议和配置漂移检测。

## [1.3.0] - 2026-07-27

### Added

- 通用 Agent 生命周期适配器与持久化事件记录，支持跨宿主接入。
- 追加式热记忆日志、会话折叠、作用域/保留期/敏感信息治理。
- 通用 Embedding Provider 接口与 OpenAI-compatible 实现。
- 主动事件巡检、重复问题聚类、经验生命周期与 Skill 候选蒸馏。
- 高风险任务质量门，以及可恢复的成长 Proposal 工作流。

### Changed

- 记忆 consolidation 持久化作用域元数据；租户查询拒绝返回未隔离或已过期记录。
- 更新 README，明确 Daming OS 不依赖 OpenClaw，可服务任意 Agent 运行时。

All notable changes to this project will be documented in this file.

## [1.2.0] - 2026-07-07

### Added
- **图激活扩散算法修复与语义加权增强**：修复了 `db.py` 中 `wiki_edges` 列名不匹配（`source_id` / `target_id` vs `source_node` / `target_node`）导致检索静默瘫痪的 Bug；新增了基于边关系 `link_type` 的动态扩散权重赋值机制（`depends_on` = 1.0, `causes` = 0.9, `extends` = 0.8, 其他 = 0.5）。
- **对话多轮合并预处理支持**：在 `MemorySystem.query` 接口中，新增了 `messages` 历史会话列表参数支持，原生以启发式滑动窗口拼接最近 3 轮的 user 发言，防止短提问下因上下文指代丢失而引起的检索失效。
- **YAML 格式安全卡片转义**：在冷记忆生成机制中强化了卡片标题 `title` 双引号转义的防灾机制，规避 YAML 解析崩溃。

## [1.1.0] - 2026-06-24

### Fixed
- **OpenClaw Context Overflow Bug** (#1): Resolved the critical issue where multi-turn agent sessions exploded and exceeded context window limits after 2-3 dialog turns.
  - Added a heuristic `MessageCompactor` to filter out low-density historical traces.
  - Implemented physical markdown truncation (`max_chars=1000`) on memory recall.
  - Cleansed old `<MemoryHint>` tags dynamically in the memory wrapper to eliminate recursive memory-nesting.

### Added
- **On-Demand Memory Retrieval**: Modified `@attach_memory` to default to `auto_recall=False` (aligned with OpenClaw's `"autoRecall": false` mode), giving agents control to query the memory engine manually only when errors or complex logic occur.
- **Async Memory Hint prefetching**: Added support for asynchronous shadow prefetching.

### Removed
- Removed the Github Star prompt during workspace initialization to keep the CLI experience clean and professional.
