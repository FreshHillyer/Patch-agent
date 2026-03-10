"""OpenCode CLI 调用：Patch Agent 与 Review Agent。"""

import json
import shutil
import subprocess
from pathlib import Path

from .git_apply import ConflictInfo, read_file_safe


def _ensure_agents_in_repo(repo_path: str) -> None:
    """将 agent 配置复制到目标仓库的 .opencode/agent/。"""
    proj_root = Path(__file__).parent.parent.parent
    agents_dst = Path(repo_path) / ".opencode" / "agent"
    agents_dst.mkdir(parents=True, exist_ok=True)
    # 包内 agents (patch, review) + 项目 .opencode/agent (os-merge-expert)
    for agents_src in [
        Path(__file__).parent / "agents",
        proj_root / ".opencode" / "agent",
    ]:
        if agents_src.exists():
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
    repo_abs = str(Path(repo_path).resolve())
    cmd_preview = f"opencode run --agent {agent} --format json --dir {repo_abs}"
    print("运行命令：", cmd_preview, f'"{prompt}"')

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

    # 解析 JSONL：仅提取 type=text 的 part.text，拼接后返回给用户
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
            part = obj.get("part") or {}
            text = part.get("text", "") if isinstance(part, dict) else ""
            if text:
                text_parts.append(text)

    result = "\n".join(text_parts)
    if result:
        print("--- OpenCode 输出 ---")
        print(result)
        print("---")
        fwafwauihuih
    
    return not has_error, result


def run_patch_agent(
    repo_path: str,
    conflict: ConflictInfo,
    patch_label: str,
) -> tuple[bool, str]:
    """
    调用 os-merge-expert Agent 解决冲突。
    patch 使用绝对路径引用 patch_files/<label>.patch，目标仓库为 --to 仓库。

    Returns:
        (成功, 消息)
    """
    patch_path = (Path(repo_path) / "patch_files" / f"{patch_label}.patch").resolve()
    if not patch_path.exists():
        return False, f"patch 文件不存在: {patch_path}"

    # 构建 prompt：patch 用绝对路径，rej 内容、目标文件内容
    parts = [
        "请解决以下 git apply 冲突，将 patch 的意图正确合入到目标文件中。",
        "不要改变原 commit 的修改目的。",
        "",
        f"=== patch 文件（绝对路径）===",
        str(patch_path),
        "",
        "=== git apply 错误输出 ===",
        conflict.apply_stderr or "(无)",
    ]
    
    # for rej_path, rej_content in conflict.rej_contents.items():
    #     parts.append(f"=== 拒绝块 {rej_path} ===")
    #     parts.append(rej_content)
    #     target_path = rej_path[:-4] if rej_path.endswith(".rej") else rej_path
    #     file_content = read_file_safe(repo_path, target_path)
    #     parts.append(f"=== 当前目标文件 {target_path} ===")
    #     parts.append(file_content or "(空或不存在)")
    #     parts.append("")

    prompt = "\n".join(parts)
    print(prompt)
    return _run_opencode(repo_path, prompt, "os-merge-expert")


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

原分支补丁Commit 信息：{commit_message}

=== 当前适配后diff（待审查）===
{diff_content}

请检查，并输出你的review结论。"""

    ok, output = _run_opencode(repo_path, prompt, "patch-review-expert")

    if not ok:
        return False, output

    # 解析第一行判断通过与否
    first_line = (output.split("\n")[0] or "").strip()
    passed = "通过" in first_line and "不通过" not in first_line[:10]
    return passed, output
