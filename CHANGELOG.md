# Changelog

All notable changes to this project will be documented in this file.

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
