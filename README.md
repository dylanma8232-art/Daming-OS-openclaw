# 大明天子 OS (Daming OS)

<p align="center">
  <b>一个为自主智能体（Autonomous Agents）量身打造的工业级防弹底座。</b>
</p>

## 💡 项目简介 (Introduction)

在当前大模型（LLM）开发中，让智能体拥有长期的记忆，并能自己写代码进化是极具挑战的。如果不加以限制，上下文（Context）极易爆仓，沙箱执行极易遭受恶意代码攻击，错误循环更是会导致无限的 API 开销风暴。

**Daming OS (大明天子 OS)** 就是为了终结这些乱象而诞生的。它脱胎于高度复杂的闭源企业级生产环境，将其最核心、最底层的**防爆仓记忆引擎**与**防黑客成长沙箱**完全抽离解耦，打造为了一个轻量、即插即用的 Python 开源包。

任何套壳的 AI 智能体，只要接入了 Daming OS，就仿佛穿上了一件工业级的防弹衣，瞬间获得**冷温热三层级联的永久记忆力**与**自带物理安检门的无限进化能力**。

---

## 🌟 核心特性 (Features)

### 🧠 大明记忆系统 v1.0
*告别简单的向量拼接，真正具备遗忘、衰减与物理防爆仓的记忆引擎。*

- **三层记忆级联 (Hot/Warm/Cold)**：
  - **Hot (热层)**：采用 `os.replace` 原子锁写入临时 Session，绝对防御高频写入导致的进程撕裂。
  - **Warm (温层)**：内置 LanceDB 密集向量检索。
  - **Cold (冷层)**：内置 SQLite FTS5 稀疏文本检索。
- **3-Way RRF 检索融合**：基于向量相似度、图谱激活扩散与文本稀疏性的 RRF 综合打分。
- **物理衰减扩散公式**：图激活扩散算法支持深度 BFS 队列与真实的 $\beta$ 传递、$\gamma$ 耗散物理数学模型。
- **防爆仓信息密度压缩器**：采用独创的 $ID(i) = \frac{\text{Score}_{\text{final}}(i)}{\text{Len}(i)}$ 公式，残酷裁剪长篇大论，誓死捍卫 LLM 的上下文。
- **文件级死锁缓存**：基于 `fcntl.flock` 的高并发持久化 JSON 语义缓存。

### 🧬 大明成长系统 v1.0
*不止于执行代码，而是在安全的沙箱中自我辩论、自我测试、自我愈合。*

- **AST 物理安检门**：沙箱代码执行前触发深度抽象语法树 (AST) 安检，无情拦截 `subprocess`、`os.system` 等任何尝试越权提权的恶意代码。
- **沙箱闭环自愈与后台 GC**：烟雾测试失败后立即捕获 stderr 回传 LLM 进行自动重试修复，并自带后台守护线程静默清理废弃沙箱，防止硬盘泄漏。
- **内阁辩论协议 (Cabinet Swarm)**：经验提取抛弃单体盲目思考，采用多线程并发拉起“红方黑客挑刺、蓝方防守修复、白方裁判总结”，三方模型激烈博弈后才产出最高质量经验。
- **防事件风暴**：引入 5 分钟滑动窗口与 SHA256 哈希去重机制，防御死循环引发的无底洞积分风暴。
- **$<1ms$ 物理回滚**：部署新经验前，自动使用 `shutil.copy2` 进行瞬时的原子级物理冷备。

---

## 🚀 快速上手 (Quick Start)

### 1. 安装 Daming OS
请确保您的环境是 Python 3.9+，在项目根目录下执行：
```bash
git clone git@github.com:dylanma8232-art/Daming-OS.git
cd Daming-OS
pip install -e .
```

### 2. 一键初始化脚手架
在您自己的 Agent 项目目录中，使用自带的命令行工具生成骨架：
```bash
daming-os init --dir ./my-agent-workspace
```
这将在该目录下自动生成：
- `.env` (环境变量配置文件)
- `AGENTS.md` (系统规范红线模板)
- `session_status.md` (心跳检查文件)

### 3. 配置环境变量
打开生成的 `.env` 文件，填入您的 API 密钥：
```env
DAMING_OS_WORKSPACE="/path/to/my-agent-workspace"
# 填入您使用的大模型 API KEY (支持 OpenAI, Anthropic, DashScope 等)
OPENAI_API_KEY="<YOUR_OPENAI_API_KEY_HERE>"
```

### 4. 极简代码接入
```python
import os
from daming_os.middleware import DamingMiddleware

# 1. 挂载中间件
middleware = DamingMiddleware()

user_input = "帮我写一个 Python 脚本"
# 2. 拦截并检索相关祖传经验
context = middleware.before_llm(user_input)
print("提取到的防御与经验指引:", context)

# 3. LLM 处理完毕后，反写本次反思与新成长经验
middleware.after_llm(user_input, "这是我写出的脚本...", success=True)
```

## 🔐 极限安全声明
Daming OS 秉持**极端防御主义理念**，在 AST 安检门中默认封锁一切 OS 级系统调用。如开发者确实需要赋予 Agent 执行高级命令的权限，请自行前往 `daming_os/growth/sandbox.py` 修改 `SafetyVisitor` 黑名单。
