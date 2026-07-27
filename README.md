# 大明天子 OS

<p align="center">
  <img src="assets/banner.png" alt="Daming OS Banner" width="800">
</p>

<p align="center">
  <b>一个为智能体量身打造的记忆成长系统</b>
</p>

## 💡 项目简介

在以大语言模型为核心的自主智能体开发中，传统的裸奔运行智能体正面临着严峻的生存挑战。尤其是在 OpenClaw 等高权限自主智能体生态中，智能体在缺乏底层保护的情况下直接运行，极易触发以下三大致命痛点：

> ### 🚨 智能体裸奔运行的三大致命痛点
> 
> * **资金黑洞**：上下文不断膨胀导致爆仓，API 费用开销呈指数级上升。
> * **越权炸弹**：沙箱内执行自演化代码时缺乏审计，极易遭受黑客攻击或提权破坏。
> * **死循环风暴**：运行报错或逻辑异常导致智能体陷入自我纠错死循环，瞬间榨干 API 配额。

**Daming OS** 是面向所有智能体运行时的工业级记忆与成长底座。它不依赖 OpenClaw 或任何特定 Agent 框架：只要宿主实现标准生命周期、审批与部署协议，便可接入 Codex、自研 Agent、LangGraph 等任意运行时。

## 🔄 Daming OS Flow

Daming OS 中的记忆系统与成长系统并非孤立运行，而是通过事件总线与日志通道实现了深度的双向反馈与闭环流转。整体架构与代码流转一目了然：

<p align="center">
  <img src="assets/openclaw_flow.png" alt="Daming OS System Flow" width="800">
</p>

### ✨ 核心特性

Daming OS 实现了记忆系统与成长系统的高效闭环流转，极限融合出五大黄金核心特性：

* **极速冷温热三层语义缓存与搜索**：基于文件锁与两级缓存实现热数据微秒级响应，避免频繁读取数据库。深度整合密集向量与全文稀疏检索，实现基于语义相似度与文本匹配的多维极速定位。
* **防爆仓自适应网关与物理截断**：响应全局配置以按需检索，在存入记忆库前自动清洗输入中的历史提示词标签，避免循环记忆的逻辑死结。在额度超限时自适应精简历史，并在返回前进行物理长度截断，强制字符切片以誓死防爆仓。
* **安全沙箱与静态安检门**：在隔离沙箱中运行编译级安全检测、静态分析与冒烟测试，强制拦截并封锁高危导入与文件系统反射修改，严防越权与提权，确保代码无毒。
* **异常捕获与多智能体博弈自愈**：实时监听运行报错日志，利用指数衰退滑动窗口计算成长值积分，积满即触发红蓝白三方多智能体博弈辩论，全自动生成高质量代码修复补丁与最佳实践。
* **闭环反思与毫秒级原子部署**：将运行中的所有报错拦截并作为负反馈记忆沉淀至底层，部署前进行物理冷备以支持一键安全回滚，部署完成后刷新缓存使全新行为即刻生效。
* **通用生命周期契约**：统一记录 `turn`、`tool`、`model`、`policy` 等宿主事件，携带租户、Agent、会话与追踪标识，便于任何运行时接入审计和观测。
* **记忆治理**：写入前执行敏感凭据脱敏、长度限制、租户/Agent/会话作用域绑定和可配置保留期；租户检索默认拒绝返回旧的无作用域数据。
* **可恢复演化工作流**：演化按 `proposed → validated → approved → deployed → verified/rolled_back` 推进。Daming OS 只管理状态与审计，实际验证、审批、部署和回滚均由宿主适配器明确提供。
* **生产记忆闭环**：每轮以文件锁追加热记忆（工具调用、状态变更和 token 量），超窗后生成进度快照；事件流自动聚类重复故障，形成可审阅的成长提案。
* **经验到能力**：经验具有 `pending → verified → deprecated` 生命周期与应用计数。仅已验证经验可蒸馏为待人工审核的通用技能候选，避免未经验证的“自我改写”。
* **质量门**：高风险任务完成后必须通过独立 review，调用方可在交付或部署前查询阻断项。

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

接入方可使用 `EvolutionWorkflow` 注入自己的 `validator`、`approvals`、`deployer` 和 `verifier`。这避免将任何聊天平台、Webhook 或代码部署方式硬编码到 Daming OS。

### 无 OpenClaw 的运行时与 Hook 接入

独立运行时将白皮书中原先由 OpenClaw 代管的自动召回/捕获、生命周期 Hook、维护调度、两级语义缓存、上下文压缩和技能懒加载接回 Daming OS。任意 Agent 只需把自身 Hook 注册器传给 `runtime.hooks.install`；没有常驻 Hook 的长运行 Agent 可调用 `runtime.start()` 启动内置持久化调度器。

```python
from daming_os import DamingRuntime

runtime = DamingRuntime("./my-agent-workspace", skill_dirs=["./skills"])
runtime.hooks.install(agent.register_hook)  # 注册 before_turn / after_turn / error
# 或：runtime.start()  # 让无 Hook 的常驻进程运行维护任务
```

Hook 的 `before_turn` payload 使用 `input`、`agent_id`、`session_id`、`tenant_id` 和可选 `metadata.messages`；它会返回 `daming_memories` 与压缩后的 `messages`。`after_turn` 会自动写热记忆、投递成长事件并执行到期维护任务。

如需语义向量召回，传入任何 OpenAI-compatible 的 embedding endpoint：

```python
from daming_os import OpenAICompatibleEmbeddingProvider
from daming_os.memory.core import MemorySystem

embeddings = OpenAICompatibleEmbeddingProvider(model="your-embedding-model", base_url="https://your-endpoint/v1")
memory = MemorySystem(embedding_provider=embeddings)
```

---

## 🚀 一键极速安装

在您的终端中执行以下一键安装命令，即可直接从远程 GitHub 仓库拉取并配置包及其全部依赖，无需繁琐的手动克隆和配置：

```bash
pip install git+https://github.com/dylanma8232-art/Daming-OS.git
```

### 脚手架一键生成工作区

安装完成后，在您自己的智能体项目根目录下，运行命令行工具一键生成配置骨架：

```bash
daming-os init --dir ./my-agent-workspace
```

这将在目标目录下自动生成 AGENTS.md 指令规范文件、USER.md 用户授权配置文件以及 .env 环境变量配置文件等。

---

## 🔐 极限安全声明

Daming OS 秉持极端防御主义理念，在安全门控中默认封锁一切高级提权和文件系统反射修改。如开发者的智能体确实需要执行高权限系统操作，请在配置中进行策略授权，或自行微调安检名单以保证合规。
