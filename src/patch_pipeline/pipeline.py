"""主流程编排：PR commits 合入 + 冲突处理 + Review + 连续失败暂停。"""

import subprocess
from typing import Optional

from .git_apply import (
    apply_patch,
    cleanup_rejects,
    is_patch_already_applied,
    remove_reject_files,
)
from .opencode_client import run_patch_agent, run_review_agent
from .pr_parser import (
    CommitInfo,
    fetch_commits_from_gitee,
    fetch_commits_from_gitcode,
    fetch_commits_from_local,
)


def _run_apply_loop(
    to_repo: str,
    commits: list,
    show_patch: bool = False,
) -> None:
    """对 commit 列表执行合入循环。"""
    consecutive_errors = 0

    for i, commit in enumerate(commits, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(commits)}] commit {commit.sha[:7]}: {commit.message[:80]}")
        print("-" * 60)
        if commit.changed_files:
            print("修改文件:")
            for f in commit.changed_files:
                print(f"  - {f}")
        else:
            print("修改文件: (从 patch 未能解析，见下方内容)")
        print("-" * 60)
        if show_patch:
            print("patch 内容:")
            if commit.patch_content:
                for line in commit.patch_content.rstrip().split("\n"):
                    print(f"  {line}")
            else:
                print("  (空 - 该 commit 将被跳过)")
        print("-" * 60)

        if consecutive_errors >= 2:
            print("\n--- 连续 2 次失败，暂停 ---")
            print(f"当前 commit: {commit.sha} - {commit.message}")
            input("请手动解决后按 Enter 继续...")
            consecutive_errors = 0

        if is_patch_already_applied(to_repo, commit.patch_content):
            print("  ⊘ 已合入过（--to 仓库中修改已存在），跳过")
            consecutive_errors = 0
            continue

        ok, conflict = apply_patch(
            to_repo,
            commit.patch_content,
            fuzz=0,
            patch_label=commit.sha[:7],
        )

        patch_agent_used = False
        if not ok and conflict:
            # apply 失败时再检查：可能内容已存在但上下文略有差异（如 ub_fwctl）
            if is_patch_already_applied(to_repo, commit.patch_content):
                print("  ⊘ 已合入过（apply 失败但反向检查通过），跳过")
                consecutive_errors = 0
                continue
            print("  git apply 冲突，调用 Patch Agent...")
            patch_ok, patch_msg = run_patch_agent(
                to_repo, conflict, patch_label=commit.sha[:7]
            )
            if not patch_ok:
                print(f"  Patch Agent 失败: {patch_msg}")
                consecutive_errors += 1
                cleanup_rejects(to_repo)
                continue
            remove_reject_files(to_repo)
            ok = True
            patch_agent_used = True

        if not ok:
            consecutive_errors += 1
            continue

        # Review 仅在 os-merge-expert 调用成功后才执行
        if patch_agent_used and commit.patch_content.strip():
            print("  合入成功，调用 Review Agent...")
            review_ok, review_msg = run_review_agent(to_repo, commit.message)
            if not review_ok:
                print(f"  Review 不通过: {review_msg[:200]}...")
                consecutive_errors += 1
                continue

        consecutive_errors = 0
        print(f"  ✓ commit {commit.sha[:7]} 完成")

    print("\n流水线结束。")


def _get_current_branch(repo_path: str) -> str:
    """获取仓库当前分支。"""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def run_pipeline(
    to_repo: str,
    from_repo: Optional[str] = None,
    pr_id: Optional[int] = None,
    gitcode_url: Optional[str] = None,
    gitee_url: Optional[str] = None,
    token: Optional[str] = None,
    show_patch: bool = True,
) -> None:
    """
    执行补丁回合流水线。

    支持三种模式：
    1. 本地模式：from_repo + pr_id，使用本地 git
    2. GitCode 模式：gitcode_url + from_repo，git fetch 获取
    3. Gitee 模式：gitee_url + token + from_repo，API 抓取 commit 列表 + git fetch 获取 patch

    运行前请手动切换到目标分支，to 仓库当前分支用于 merge-base 计算。
    """
    to_branch = _get_current_branch(to_repo)
    if gitee_url and token:
        if from_repo is None:
            raise ValueError("Gitee 模式需要 --from（源仓库）")
        commits = fetch_commits_from_gitee(
            pr_url=gitee_url,
            token=token,
            from_repo=from_repo,
            to_repo=to_repo,
            to_branch=to_branch,
        )
    elif gitcode_url:
        if from_repo is None:
            raise ValueError("GitCode 模式需要 --from（源仓库 origin）")
        commits = fetch_commits_from_gitcode(
            pr_url=gitcode_url,
            from_repo=from_repo,
            to_repo=to_repo,
            to_branch=to_branch,
            token=token,
        )
    elif from_repo is not None and pr_id is not None:
        commits = fetch_commits_from_local(
            from_repo=from_repo,
            to_repo=to_repo,
            pr_id=pr_id,
            to_branch=to_branch,
        )
    else:
        raise ValueError(
            "需指定 (from_repo + pr_id)、gitcode_url 或 (gitee_url + token)"
        )

    if not commits:
        print("无待合入 commits，退出。")
        return

    print(f"共 {len(commits)} 个 commits 待合入（目标分支: {to_branch}）")
    print("说明：仅当 API/本地返回的 patch 内容为空时才会跳过（如 merge commit）")
    _run_apply_loop(to_repo, commits, show_patch)
