# Daming OS

<p align="center">
  <img src="assets/banner.png" alt="Daming OS Banner" width="800">
</p>

<p align="center">
  <b>A memory and growth system built for intelligent agents.</b>
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/Language-English-blue" alt="English"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/语言-简体中文-lightgrey" alt="简体中文"></a>
</p>

## About

Autonomous agents built on large language models face real operational risks when they run without a protective foundation—especially in high-privilege environments such as OpenClaw. Daming OS addresses three common failure modes:

> ### Key risks of unprotected agents
>
> - **Runaway cost:** ever-growing context windows can drive API spending sharply upward.
> - **Privilege escalation:** self-evolving code executed without auditing can expose an agent to attacks or unauthorized system changes.
> - **Error loops:** runtime failures or faulty logic can trap an agent in repeated self-correction and exhaust its API quota.

**Daming OS** is an industrial-grade memory and growth foundation for any agent runtime. It is not tied to OpenClaw or a particular agent framework: any host that implements the standard lifecycle, approval, and deployment contracts can integrate Codex, custom agents, LangGraph, and more.

## Daming OS Flow

The memory and growth systems are connected through an event bus and log channels, creating a closed feedback loop rather than operating in isolation.

<p align="center">
  <img src="assets/openclaw_flow.png" alt="Daming OS System Flow" width="800">
</p>

### Core capabilities

- **Three-tier semantic memory and search:** File locks and two-level caching deliver microsecond-scale hot-data responses, while dense vectors and sparse full-text retrieval provide multi-dimensional lookup.
- **Adaptive context gateway:** Cleans historical prompt tags before memory writes, retrieves context on demand, adapts history under quota pressure, and physically truncates output to prevent context overruns.
- **Secure sandbox and static safety gate:** Runs compile-level checks, static analysis, and smoke tests in isolation; blocks dangerous imports and filesystem reflection changes.
- **Exception capture and multi-agent self-healing:** Watches runtime errors, calculates growth scores with an exponentially decayed sliding window, and can trigger red/blue/white multi-agent deliberation to produce repair patches and practices.
- **Closed-loop reflection and atomic deployment:** Persists intercepted errors as negative-feedback memories, creates cold backups before deployment for safe rollback, and refreshes caches after release.
- **Universal lifecycle contract:** Records host events such as `turn`, `tool`, `model`, and `policy`, with tenant, agent, session, and trace identifiers for auditing and observability.
- **Memory governance:** Redacts sensitive credentials, enforces size limits and tenant/agent/session scopes, supports configurable retention, and rejects old unscoped data in tenant retrieval by default.
- **Recoverable evolution workflows:** Evolves through `proposed → validated → approved → deployed → verified/rolled_back`. Daming OS manages state and audit records; the host adapter explicitly supplies validation, approval, deployment, and rollback.
- **Production memory loop:** Appends hot memory for tool calls, state changes, and token use with file locks; creates progress snapshots after a window expires; clusters recurring failures into reviewable growth proposals.
- **Experience-to-skill pipeline:** Tracks experience through `pending → verified → deprecated` and only distills verified experience into human-reviewed skill candidates.
- **Quality gates:** High-risk tasks must pass an independent review; callers can query blockers before delivery or deployment.

## Integrate any agent

```python
from daming_os import AgentContext, DamingAdapter

adapter = DamingAdapter()
context = AgentContext(
    agent_id="research-agent",
    session_id="session-42",
    tenant_id="team-a",
    metadata={"trace_id": "trace-123"},
)
memories = adapter.before_turn("Retrieve the previous architecture decision", context)
# Run your host's model and tool calls here.
adapter.after_turn("Retrieve the previous architecture decision", "The architecture decision is…", context)
```

Use `EvolutionWorkflow` to inject your own `validator`, `approvals`, `deployer`, and `verifier`. This keeps chat platforms, webhooks, and deployment mechanisms out of Daming OS itself.

### Runtime and hook integration without OpenClaw

Independent runtimes bring the whitepaper's automatic recall/capture, lifecycle hooks, maintenance scheduling, two-tier semantic cache, context compression, and lazy skill loading directly into Daming OS. Any agent can pass its hook registry to `runtime.hooks.install`; long-running agents without persistent hooks can call `runtime.start()` to enable the built-in scheduler.

```python
from daming_os import DamingRuntime

runtime = DamingRuntime("./my-agent-workspace", skill_dirs=["./skills"])
runtime.hooks.install(agent.register_hook)  # Registers before_turn / after_turn / error.
# Or: runtime.start()  # Runs maintenance tasks for a persistent process without hooks.
```

The `before_turn` hook payload uses `input`, `agent_id`, `session_id`, `tenant_id`, and optional `metadata.messages`; it returns `daming_memories` and compressed `messages`. `after_turn` writes hot memory, publishes growth events, and runs due maintenance automatically.

For semantic vector retrieval, provide any OpenAI-compatible embedding endpoint:

```python
from daming_os import OpenAICompatibleEmbeddingProvider
from daming_os.memory.core import MemorySystem

embeddings = OpenAICompatibleEmbeddingProvider(
    model="your-embedding-model", base_url="https://your-endpoint/v1"
)
memory = MemorySystem(embedding_provider=embeddings)
```

---

## Quick installation

Install directly from GitHub:

```bash
pip install git+https://github.com/dylanma8232-art/Daming-OS-openclaw.git
```

### Scaffold a workspace

After installation, run this command from your agent project's root directory to generate a configuration skeleton:

```bash
daming-os init --dir ./my-agent-workspace
```

It creates an `AGENTS.md` instruction file, a `USER.md` authorization configuration file, and a `.env` environment configuration file in the target directory.

---

## Security statement

Daming OS follows a defense-first approach. Its security gate blocks advanced privilege-escalation patterns and filesystem reflection changes by default. If an agent genuinely needs high-privilege system access, grant it explicitly through policy configuration or carefully tailor the safety allowlist.
