"""OpenCode CLI 调用：Patch Agent 与 Review Agent。"""

import json
import shutil
import subprocess
from pathlib import Path

from .git_apply import ConflictInfo, read_file_safe


def _ensure_agents_in_repo(repo_path: str) -> None:
    """将 agent 配置复制到目标仓库的 .opencode/agents/。"""
    # 优先从包内 agents 目录复制（pip 安装后可用）
    agents_src = Path(__file__).parent / "agents"
    if not agents_src.exists():
        agents_src = Path(__file__).parent.parent.parent / ".opencode" / "agents"
    agents_dst = Path(repo_path) / ".opencode" / "agents"
    if not agents_src.exists():
        return
    agents_dst.mkdir(parents=True, exist_ok=True)
    for f in agents_src.glob("*.md"):
        shutil.copy2(f, agents_dst / f.name)


def _run_opencode(
    repo_path: str,
    prompt: str,
    agent: str,
) -> tuple[bool, str]:
    """
    调用 opencode run，解析 JSONL 输出。

    Returns:
        (成功, 模型输出的文本摘要)
    """
    _ensure_agents_in_repo(repo_path)

    cmd = [
        "opencode",
        "run",
        "--agent",
        agent,
        "--format",
        "json",
        "--dir",
        repo_path,
        prompt,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=repo_path,
        )
        stdout, stderr = proc.communicate(timeout=600)  # 10 min
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return False, "OpenCode 执行超时"
    except FileNotFoundError:
        return False, "未找到 opencode 命令，请先安装 OpenCode CLI"

    if proc.returncode != 0:
        return False, stderr or stdout or f"opencode 退出码 {proc.returncode}"

    # 解析 JSONL
    has_error = False
    text_parts: list[str] = []

    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = obj.get("type")
        if t == "error":
            has_error = True
            err = obj.get("error", {})
            data = err.get("data", {})
            msg = data.get("message", str(err))
            return False, msg
        if t == "text":
            part = obj.get("part", {})
            text_parts.append(part.get("text", ""))
        if t == "step_finish":
            reason = obj.get("part", {}).get("reason")
            if reason == "stop" and has_error:
                pass  # 已在 error 处理
            elif reason == "stop":
                pass  # 正常结束

    return not has_error, "\n".join(text_parts)


def run_patch_agent(repo_path: str, conflict: ConflictInfo) -> tuple[bool, str]:
    """
    调用 Patch Agent 解决冲突。

    Returns:
        (成功, 消息)
    """
    # 构建 prompt：包含 patch、rej 内容、目标文件内容
    parts = [
        "请解决以下 git apply 冲突，将 patch 的意图正确合入到目标文件中。",
        "不要改变原 commit 的修改目的。",
        "",
        "=== 原始 patch ===",
        conflict.patch_content,
        "",
        "=== git apply 错误输出 ===",
        conflict.apply_stderr or "(无)",
    ]

    for rej_path, rej_content in conflict.rej_contents.items():
        parts.append(f"=== 拒绝块 {rej_path} ===")
        parts.append(rej_content)
        target_path = rej_path[:-4] if rej_path.endswith(".rej") else rej_path
        file_content = read_file_safe(repo_path, target_path)
        parts.append(f"=== 当前目标文件 {target_path} ===")
        parts.append(file_content or "(空或不存在)")
        parts.append("")

    prompt = "\n".join(parts)
    return _run_opencode(repo_path, prompt, "patch")


def run_review_agent(repo_path: str, commit_message: str) -> tuple[bool, str]:
    """
    调用 Review Agent 审查合入结果。

    检查：修改目的是否正确、代码变更量是否过大、是否存在语法错误。
    要求输出格式：第一行包含 "通过" 或 "不通过"，后续为原因。

    Returns:
        (通过, 审查说明)
    """
    # 获取当前未提交的变更作为 diff
    result = subprocess.run(
        ["git", "diff", "--no-color"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    diff_content = result.stdout or "(无变更)"

    prompt = f"""请审查以下合入变更。

Commit 信息：{commit_message}

=== 当前 diff（待审查）===
{diff_content}

请检查：
1. 修改目的是否正确
2. 代码变更量是否过大
3. 是否存在语法错误

第一行必须为「通过」或「不通过」，随后简要说明原因。"""

    ok, output = _run_opencode(repo_path, prompt, "review")

    if not ok:
        return False, output

    # 解析第一行判断通过与否
    first_line = (output.split("\n")[0] or "").strip()
    passed = "通过" in first_line and "不通过" not in first_line[:10]
    return passed, output
