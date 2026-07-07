# Changelog

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
