# Changelog

## [Unreleased]

## [1.4.0] - 2026-07-27

### Added

- 独立 `DamingRuntime`：内置持久化 Scheduler 默认启动，任何支持 Hook 的 Agent 可直接接入，不再依赖 OpenClaw 的 Cron、Hook、记忆插件或技能加载器。
- 记忆系统 3.0 全链路：Hot 捕获、SQLite/FTS、向量检索本地回退、结构化 Wiki、`wiki_edges` 图扩散、Bitable 双向同步、Glacier 无损归档、质量门与文件变更发现。
- 成长系统 2.0 全链路：五类 GEP 信号、事件补算、经验聚类、技能结晶、三方审计、OTP Hook 回调、安全部署/回滚、版本与知识回写。
- `/xuexi` 即时经验结晶、Meta-Prompt（灵魂自我改写）提案/审批/部署，以及部署后自动注入 Agent 上下文。

### Changed

- 统一动态图谱与检索图为同一 `wiki_edges` 存储，消除“构图成功但召回不可见”的重复实现。
- Bitable 改为先拉取、再合并、再推送，并将远端知识导入可检索的 Warm Memory。
- 已验证成长结果立即回写 Memory、Wiki、图谱、Release Ledger 与质量门，不再等待夜间整理。
- 默认技能蒸馏输出标准 `SKILL.md`，由懒加载器发现并记录实际注入使用。

### Changed

- 将白皮书中曾由 OpenClaw 原生运行时代管的 Hook、调度、L2 语义缓存、Warm 向量写入、上下文压缩和技能懒加载接入 Daming OS 独立运行时。
- 默认 GEP 门限调整为白皮书规定的 3.0；标准 Adapter 会订阅自身生命周期事件驱动成长提案。

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
