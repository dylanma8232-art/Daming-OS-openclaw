# 大明天子 OS

<p align="center">
  <img src="assets/banner.png" alt="Daming OS Banner" width="800">
</p>

<p align="center">
  <b>一个为智能体量身打造的记忆成长系统</b>
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/Language-English-lightgrey" alt="English"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/语言-简体中文-blue" alt="简体中文"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/许可证-CC_BY--NC--SA_4.0-orange" alt="开源许可证: CC BY-NC-SA 4.0"></a>
</p>

## 项目简介

在以大语言模型为核心的自主智能体开发中，传统的裸奔运行智能体正面临着严峻的生存挑战。尤其是在 OpenClaw 等高权限自主智能体生态中，智能体在缺乏底层保护的情况下直接运行，极易触发以下三大致命痛点：

> ### 智能体裸奔运行的三大致命痛点
>
> - **资金黑洞**：上下文不断膨胀导致爆仓，API 费用开销呈指数级上升。
> - **越权炸弹**：沙箱内执行自演化代码时缺乏审计，极易遭受黑客攻击或提权破坏。
> - **死循环风暴**：运行报错或逻辑异常导致智能体陷入自我纠错死循环，瞬间榨干 API 配额。

**Daming OS** 是面向所有智能体运行时的工业级记忆与成长底座。它不依赖 OpenClaw 或任何特定 Agent 框架：只要宿主实现标准生命周期、审批与部署协议，便可接入 Codex、自研 Agent、LangGraph 等任意运行时。

## Daming OS Flow

Daming OS 中的记忆系统与成长系统并非孤立运行，而是通过事件总线与日志通道实现了深度的双向反馈与闭环流转。

<p align="center">
  <img src="assets/openclaw_flow.png" alt="Daming OS System Flow" width="800">
</p>

### 核心特性

- **极速冷温热三层语义缓存与搜索**：基于文件锁与两级缓存实现热数据微秒级响应，整合密集向量与全文稀疏检索，实现语义相似度与文本匹配的多维定位。
- **防爆仓自适应网关与物理截断**：按需检索，写入记忆库前自动清洗输入中的历史提示词标签；在额度超限时自适应精简历史，并在返回前物理截断长度。
- **安全沙箱与静态安检门**：在隔离沙箱中运行编译级安全检测、静态分析与冒烟测试，拦截高危导入与文件系统反射修改。
- **异常捕获与多智能体博弈自愈**：实时监听运行报错日志，利用指数衰退滑动窗口计算成长值积分，并可触发红蓝白三方多智能体辩论，生成修复补丁与最佳实践。
- **闭环反思与毫秒级原子部署**：将运行报错沉淀为负反馈记忆；部署前创建冷备以支持安全回滚，部署后刷新缓存。
- **通用生命周期契约**：统一记录 `turn`、`tool`、`model`、`policy` 等宿主事件，携带租户、Agent、会话与追踪标识，便于审计和观测。
- **记忆治理**：写入前执行敏感凭据脱敏、长度限制、租户/Agent/会话作用域绑定和可配置保留期；租户检索默认拒绝返回旧的无作用域数据。
- **可恢复演化工作流**：演化按 `proposed → validated → approved → deployed → verified/rolled_back` 推进。Daming OS 只管理状态与审计，实际验证、审批、部署和回滚由宿主适配器提供。
- **生产记忆闭环**：每轮以文件锁追加热记忆（工具调用、状态变更和 token 量），超窗后生成进度快照；事件流自动聚类重复故障，形成可审阅的成长提案。
- **经验到能力**：经验具有 `pending → verified → deprecated` 生命周期与应用计数。仅已验证经验可蒸馏为待人工审核的通用技能候选。
- **质量门**：高风险任务完成后必须通过独立 review，调用方可在交付或部署前查询阻断项。

## 通用 Agent 接入方式

```python
from daming_os import AgentContext, DamingAdapter

adapter = DamingAdapter()
context = AgentContext(
    agent_id="research-agent",
    session_id="session-42",
    tenant_id="team-a",
    metadata={"trace_id": "trace-123"},
)
memories = adapter.before_turn("检索上次的架构决策", context)
# 宿主执行自己的模型/工具调用
adapter.after_turn("检索上次的架构决策", "架构决策如下…", context)
```

接入方可使用 `EvolutionWorkflow` 注入自己的 `validator`、`approvals`、`deployer` 和 `verifier`，避免将聊天平台、Webhook 或代码部署方式硬编码到 Daming OS。

### 无 OpenClaw 的运行时与 Hook 接入

独立运行时将自动召回/捕获、生命周期 Hook、维护调度、两级语义缓存、上下文压缩和技能懒加载接回 Daming OS。任意 Agent 只需把自身 Hook 注册器传给 `runtime.hooks.install`；常驻 Agent 可显式启动后台调度器。

```python
from daming_os import DamingRuntime

runtime = DamingRuntime("./.daming", skill_dirs=["./skills"])
runtime.hooks.install(agent.register_hook)  # 注册 before_turn / after_turn / error
runtime.start_scheduler(poll_seconds=30)  # 可选：仅用于常驻宿主
```

Hook 的 `before_turn` payload 使用 `input`、`agent_id`、`session_id`、`tenant_id` 和可选 `metadata.messages`；它会返回 `daming_memories`、`daming_compacted_messages` 与 `daming_skill_context`，由宿主决定如何注入。`after_turn` 会写热记忆、投递成长事件，并通过 `tick()` 执行到期维护任务。

默认调度保持精简：

- `daily-maintenance` 每天 02:30 执行深度睡眠记忆整理、记忆/Agent 健康检查和条件式审批提醒。
- `weekly-governance` 每周日 23:30 执行。
- `daily-digest` 每天 23:00 执行，但必须通过 `runtime.daily_digest_enabled` 主动开启；它取代了原先重复的“每日总结”和“日记报告”。
- `watchdog` 按需开启，只检查长期未结束的会话。它和每日简报都不是核心记忆所必需。

审批提醒也会在交互结束后检查，只在确实超时的情况下触发，并有 24 小时冷却时间。Agent 运行质量正常时只返回精简状态，检测到异常时才保存详细报告。

如需语义向量召回，传入任何 OpenAI-compatible 的 embedding endpoint：

```python
from daming_os import OpenAICompatibleEmbeddingProvider
from daming_os.memory.core import MemorySystem

embeddings = OpenAICompatibleEmbeddingProvider(model="your-embedding-model", base_url="https://your-endpoint/v1")
memory = MemorySystem(embedding_provider=embeddings)
```

---

## 一键安装

核心插件不依赖第三方运行库。安装后直接在 Agent 项目中生成通用桥接入口：

```bash
pip install "daming-os @ git+https://github.com/dylanma8232-art/Daming-OS-openclaw.git"
daming-os install --host-dir .
```

安装器会生成 `daming_bootstrap.py`、隔离的 `.daming/` 状态、稳定的宿主专属 Agent ID，以及保护记忆数据不被提交到 Git 的 `.daming/.gitignore`。随后会在隔离目录执行真实的“写入 → 整理 → 召回”冒烟测试。安装过程不会修改宿主自己的指令和密钥文件，再次执行会安全升级由 Daming OS 生成的文件。

宿主可以直接使用统一入口，或一次注册三个生命周期 Hook：

```python
from daming_bootstrap import daming

session_id = daming.new_session_id()
context = daming.before_turn("你好", session_id=session_id)
# 宿主执行模型/工具，可注入 context["daming_memories"]。
daming.after_turn("你好", "完成", session_id=session_id)

# 暴露 register(name, callback) 的宿主可一次安装全部 Hook。
daming.install_hooks(agent.register_hook)
```

生成的桥接入口采用延迟初始化，默认故障降级：如果大明 OS 的存储或配置出现问题，会通过 `daming_degraded` 返回错误，但不会拖垮宿主 Agent。需要严格失败模式的宿主可使用 `DamingPlugin(strict=True)`。

本地成长审批不依赖任何聊天平台：

```bash
daming-os approvals --dir ./.daming list
daming-os approvals --dir ./.daming issue <proposal-id>
daming-os approvals --dir ./.daming approve <proposal-id> <otp>
```

OTP 只在终端显示一次，不会写入大明日志。工作区数据库迁移具有版本记录、自动备份并在初始化时自动执行，也可以手动运行 `daming-os migrate --dir ./.daming`。

向量、LLM 等能力保持可选，需要时再安装 `[vector]`、`[llm]` 或 `[full]`。可用 `daming-os doctor --dir ./.daming` 和 `daming-os status --dir ./.daming` 诊断与查看状态。错过的每日/每周任务会在下次交互补执行，失败任务则按有上限的指数退避自动重试。

---

## 安全声明

Daming OS 秉持防御优先理念，在安全门控中默认封锁高级提权和文件系统反射修改。如智能体确实需要执行高权限系统操作，请在策略配置中进行明确授权，或谨慎调整安检白名单以保证合规。

---

## 开源许可证

本项目采用 [Creative Commons 署名-非商业性使用-相同方式共享 4.0 国际许可协议 (CC BY-NC-SA 4.0)](LICENSE) 开源。

