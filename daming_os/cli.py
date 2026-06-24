import os
import argparse
import textwrap

def init_workspace(target_dir: str):
    """Initialize a new Daming OS workspace with necessary directories and templates."""
    os.makedirs(target_dir, exist_ok=True)
    
    # 1. Create Directories
    dirs = [
        "memory/lancedb",
        "memory/evolution-proposals",
        "wiki/main/concepts",
        ".daming-os" # hidden meta directory
    ]
    for d in dirs:
        os.makedirs(os.path.join(target_dir, d), exist_ok=True)
        
    # 2. Generate AGENTS.md
    agents_md_path = os.path.join(target_dir, "AGENTS.md")
    if not os.path.exists(agents_md_path):
        with open(agents_md_path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                # 智能体系统指令规范 (AGENTS.md)
                
                ## 🔴 记忆保存铁律
                每次对话结束前，必须调用记忆存储工具保存重要信息：
                - 用户偏好/决策
                - 新发现的经验
                - 错误教训
                
                ## 🧬 成长系统审批处理
                当收到演化提案时，必须等待人类（管理员）的明确批准后才能部署代码。
                
                ## 📚 知识库
                - 遇到不确定的架构问题，请先检索本地 `wiki/main/` 目录。
            """))

    # 3. Generate USER.md
    user_md_path = os.path.join(target_dir, "USER.md")
    if not os.path.exists(user_md_path):
        with open(user_md_path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                # 身份验证配置文件
                
                > **管理员验证**：此处填入您的唯一身份标识或指纹，确保只有您可以触发系统核心演化。
            """))

    # 4. Generate session_status.md (Heartbeat)
    session_md_path = os.path.join(target_dir, "session_status.md")
    if not os.path.exists(session_md_path):
        with open(session_md_path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent("""\
                # 运行状态 (Session Status)
                - 当前系统状态：✅ 正常运行
                - OS 引擎版本：Daming OS v1.1
            """))

    # 5. Generate .env template
    env_path = os.path.join(target_dir, ".env")
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(f"""\
                DAMING_OS_WORKSPACE="{os.path.abspath(target_dir)}"
                # 填入您使用的大模型 API KEY (支持 OpenAI, Anthropic, DashScope 等)
                OPENAI_API_KEY="<YOUR_OPENAI_API_KEY_HERE>"
            """))

    print(textwrap.dedent(f"""\n        ================================================================\n        [系统提示] 欢迎接入 Daming OS 核心中枢。\n        [系统提示] 骨架已在 {os.path.abspath(target_dir)} 部署完毕。\n        ================================================================\n        👉 下一步：请修改 .env 文件中的 API KEY，唤醒您的智能体。\n        ================================================================\n    """))

def main():
    parser = argparse.ArgumentParser(description="Daming OS Command Line Interface")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    init_parser = subparsers.add_parser("init", help="Initialize a new Daming OS workspace")
    init_parser.add_argument("--dir", default=".", help="Target directory to initialize")
    
    args = parser.parse_args()
    
    if args.command == "init":
        init_workspace(args.dir)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
