# 白皮书流程融合审计

本文件以两份白皮书的 ASCII 图为能力边界，而不是以 88 个脚本文件名为边界。文件名别名、旧版脚本和同一能力的多种实现只计为一个能力。

## 记忆系统 3.0

```text
Agent Hook
  → 自动召回（向量 + FTS + 图扩散）
  → RRF 融合 → 作用域过滤 → 上下文压缩 → 注入 Agent
  → 自动捕获 → Hot JSONL/会话日志
  → 深睡眠 Consolidation → Warm 向量、SQLite FTS、关系边、Wiki
  → Wiki/Bitable 同步 → Glacier 归档 / 会话清理 / 健康检查
```

横切能力：L1/L2 语义缓存、技能懒加载、文件跟踪、质量门、迁移、版本/配置审计。

## 成长系统 2.0

```text
生命周期事件 / 错误 / 用户反馈
  → GEP 衰减与五维巡检
  → 同类事件聚类与经验提炼
  → 技能结晶（learning-to-skill）和提案
  → 双子对抗审计 → OTP
  → 审批 Provider / 卡片 Provider
  → AST + 沙箱 + 原子部署 + 验证/回滚
  → 经验、Wiki、图谱、版本和事件日志回写
```

横切能力：Meta-Prompt 演化、`/xuexi` 指令监听、插件隔离（由 SandboxGate 统一承载）、黄金路径、Agent-as-a-Judge、SICA 安全门禁、清理与会话看门狗。

## 去重规则

| 白皮书称呼 | 统一能力 | Daming OS 入口 |
|---|---|---|
| 动态知识图谱 / 图扩散拓扑检索 | 检索种子沿 `wiki_edges` 扩散后参与 RRF | `SpreadingActivationTraverser` |
| 工作流蒸馏 / 技能结晶 | 已验证经验生成 Skill 候选 | `WorkflowDistillation` + `SkillDistiller` |
| 多方会审 / 双子对抗审计 | Builder/Reviewer/Judge 审计与共识门控 | `ThreePartyCouncil` |
| 插件舱 / 隔离插件执行 | AST 门控、沙箱冒烟、部署前隔离 | `SandboxGate` / `WorkspaceProposalValidator` |
| 运行时缓存 / 语义缓存 | L1 精确缓存 + L2 向量缓存 | `HardenedSemanticCache` |
| 会话压缩 / 消息压缩 / 中间件 | Hook 中的上下文压缩与注入 | `DamingHookBridge` + `MessageCompactor` |

## 当前审计原则

只有同时满足“有实现、被 `DamingRuntime` 默认构造、由 Hook 或 Scheduler 触发、具备端到端测试”的能力，才能标记为已完成。其余能力在完整矩阵完成前保持审计中。

## 可执行验收

白皮书的脚本按行为去重归并在 [daming_os/blueprint.py](daming_os/blueprint.py)，但该清单只用于追溯名称，**不能**作为接通证明。唯一的完成证据是运行时实际执行：

- `test_memory_whitepaper_hot_to_warm_to_fts_to_graph_flow`：Hook 捕获 → 队列 → SQLite/FTS/本地向量回退 → Wiki/关系边 → 图扩散 → Bitable 导出；
- `test_gep_default_chain_builds_a_skill_then_requires_otp_before_deploy`：GEP → 技能构建 → 三方会审 → OTP → 原子部署 → 验证 → 经验/版本回写；
- `test_every_default_whitepaper_job_executes_in_an_empty_workspace`：每一个默认 Scheduler 任务在无 OpenClaw 的空工作区均可执行。

默认运行时会自行启动持久化 Scheduler；Hook 型 Agent 也会在每次 `after_turn` 触发一次轻量 tick。所有五类 GEP 信号可由标准 Hook 的 `growth_signals`，或 `feedback`、`discovery`、`rule_violation`、`system_error` 字段输入。OTP 可由任意 Hook 用 `/daming-approve <proposal_id> <otp>` 回调；外部聊天卡片/身份系统仍可替换通知 Provider。

最终补链：Bitable 同步先拉取再合并并回写可检索 Warm 数据；Wiki 链接按 `item_id` 写入同一 `wiki_edges` 图；Glacier 保留 Wiki 目录层级；文件变更会生成 discovery 信号；已验证进化会立即整合为 Memory/Wiki/图谱，而不是等待夜间任务。

`DamingRuntime.blueprint_gaps()` 仅是安装时的提示，不得用于宣称白皮书已 100% 落地。

这避免了以下历史重复：

- `event-log-writer.py` 与 `event_log_writer.py` 是同一个事件写入能力；
- `hybrid_search.py --graph` 与“动态知识图谱”是同一图扩散检索能力；
- `learning-to-skill.py` 与“工作流蒸馏”是同一技能结晶能力；
- `audit_engine.py` 与“双子/多方会审”是同一审计门控能力；
- 两个 `version_manager.py` 是同一版本审计能力。
