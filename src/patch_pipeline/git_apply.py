"""Git 合入逻辑：git apply -F 0 及冲突信息收集。"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ConflictInfo:
    """git apply 失败时的冲突信息，供 Patch Agent 使用。"""

    patch_content: str
    apply_stderr: str
    rej_files: list[str] = field(default_factory=list)
    rej_contents: dict[str, str] = field(default_factory=dict)
    target_files: list[str] = field(default_factory=list)


def ensure_on_branch(repo_path: str, target_branch: str) -> None:
    """切换到目标分支。"""
    subprocess.run(
        ["git", "checkout", target_branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )


def is_patch_already_applied(repo_path: str, patch_content: str) -> bool:
    """
    检测 patch 是否已在目标仓库中合入过。
    使用 git apply -R --check：若反向应用成功，说明修改已存在，视为已合入。
    """
    if not patch_content.strip():
        return True  # 空 patch 视为已处理
    result = subprocess.run(
        ["git", "apply", "-R", "--check", "--"],
        cwd=repo_path,
        input=patch_content,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def apply_patch(
    repo_path: str,
    patch_content: str,
    fuzz: int = 0,
    patch_label: Optional[str] = None,
) -> tuple[bool, Optional[ConflictInfo]]:
    """
    使用 git apply --reject 应用 patch。
    git apply 无 -F 选项（fuzz 属于 GNU patch），使用 -C<n> 控制上下文行数，fuzz=0 时用 -C 3 严格匹配。
    先将 patch 写入 patch_files/<label>.patch，再 apply，便于调试和明确正在合入的内容。文件保留不删除。

    patch_label: 用于命名 patch 文件，如 commit sha 短格式，便于识别。

    Returns:
        (成功, None) 或 (失败, ConflictInfo)
    """
    if not patch_content.strip():
        return True, None

    repo = Path(repo_path)
    patch_dir = repo / "patch_files"
    patch_dir.mkdir(parents=True, exist_ok=True)
    label = patch_label or "current"
    patch_path = patch_dir / f"{label}.patch"
    patch_path.write_text(patch_content, encoding="utf-8")
    patch_rel = f"patch_files/{label}.patch"

    # git apply 无 -F，用 -C<n> 控制上下文；fuzz=0 时 -C 3 严格匹配
    context = max(0, 3 - fuzz)
    cmd = ["git", "apply", "-C", str(context), "--reject", patch_rel]
    print("运行命令：", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return True, None

    # 收集 .rej 文件
    repo = Path(repo_path)
    rej_files: list[str] = []
    rej_contents: dict[str, str] = {}
    target_files: list[str] = []

    for rej in repo.rglob("*.rej"):
        rel = str(rej.relative_to(repo))
        rej_files.append(rel)
        rej_contents[rel] = rej.read_text(encoding="utf-8", errors="replace")
        # 对应源文件去掉 .rej
        target = rel[:-4] if rel.endswith(".rej") else rel
        target_files.append(target)

    conflict = ConflictInfo(
        patch_content=patch_content,
        apply_stderr=result.stderr or "",
        rej_files=rej_files,
        rej_contents=rej_contents,
        target_files=target_files,
    )
    return False, conflict


def read_file_safe(repo_path: str, rel_path: str) -> str:
    """安全读取仓库内文件内容。"""
    p = Path(repo_path) / rel_path
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def remove_reject_files(repo_path: str) -> None:
    """仅删除所有 .rej 文件（保留工作区修改，用于 Patch Agent 成功后）。"""
    repo = Path(repo_path)
    for rej in repo.rglob("*.rej"):
        rej.unlink(missing_ok=True)


def cleanup_rejects(repo_path: str) -> None:
    """删除所有 .rej 文件并恢复工作区（用于放弃当前 apply 时）。"""
    repo = Path(repo_path)
    for rej in repo.rglob("*.rej"):
        rej.unlink(missing_ok=True)
    subprocess.run(
        ["git", "checkout", "--", "."],
        cwd=repo_path,
        capture_output=True,
    )
