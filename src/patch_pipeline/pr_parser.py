"""PR 解析：支持本地 Git、GitCode 或 Gitee API 获取 commits。"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import requests


@dataclass
class CommitInfo:
    """单个 commit 的信息。"""

    sha: str
    message: str
    patch_content: str
    changed_files: List[str]  # 有实际修改的文件列表


def _parse_patch_files(patch_content: str) -> List[str]:
    """从 patch 内容解析出有实际修改的文件路径。"""
    if not patch_content or not patch_content.strip():
        return []
    files: List[str] = []
    for line in patch_content.split("\n"):
        line = line.rstrip()
        if line.startswith("diff --git "):
            # diff --git a/path b/path
            parts = line.split()
            if len(parts) >= 4:
                p = parts[2]  # a/path
                if p.startswith("a/"):
                    files.append(p[2:])
                elif p == "/dev/null" and len(parts) >= 5:
                    # diff --git a/x b/y 或 a/dev/null b/newfile
                    p2 = parts[3]
                    if p2.startswith("b/"):
                        files.append(p2[2:])
        elif line.startswith("--- "):
            # --- a/path 或 --- /dev/null
            path = line[4:].split("\t")[0].strip()
            if path.startswith("a/") and path != "a/dev/null":
                files.append(path[2:])
        elif line.startswith("+++ "):
            # +++ b/path 或 +++ /dev/null
            path = line[4:].split("\t")[0].strip()
            if path.startswith("b/") and path != "b/dev/null":
                if path[2:] not in files:
                    files.append(path[2:])
    return list(dict.fromkeys(files))  # 去重保序


def _resolve_pr_ref(from_repo: str, pr_id: int) -> str:
    """
    将 PR id 解析为可用的 git ref。
    尝试顺序：refs/pull/{id}/head, pr-{id}, pr/{id}
    """
    candidates = [
        f"refs/pull/{pr_id}/head",
        f"pull/{pr_id}/head",
        f"pr-{pr_id}",
        f"pr/{pr_id}",
    ]
    for ref in candidates:
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", ref],
                cwd=from_repo,
                capture_output=True,
                check=True,
            )
            return ref
        except subprocess.CalledProcessError:
            continue
    raise ValueError(
        f"无法解析 PR {pr_id} 对应的 ref，请确保 from 仓库中存在 "
        f"pull/{pr_id}/head、pr-{pr_id} 或 pr/{pr_id} 分支"
    )


def fetch_commits_from_local(
    from_repo: str,
    to_repo: str,
    pr_id: int,
    to_branch: str,
) -> List[CommitInfo]:
    """
    使用本地 git 获取待合入的 commits（不访问网络）。

    from_repo: 源仓库（openEuler-Kernel-origin），PR 在此，git show 在此执行
    to_repo: 目标仓库（openEuler-Kernel-kunpeng），patch 合入到此
    pr_id: PR 编号，用于解析 ref（pull/{id}/head、pr-{id}、pr/{id}）
    to_branch: 目标分支（如 main）

    Returns:
        按 git 拓扑序（父→子）排列的 commit 列表。
    """
    from_repo = str(Path(from_repo).resolve())
    to_repo = str(Path(to_repo).resolve())
    from_ref = _resolve_pr_ref(from_repo, pr_id)

    # 在 from_repo 中临时添加 to_repo 为 remote，用于计算 merge-base
    remote_name = "_patch_pipeline_to"
    try:
        subprocess.run(
            ["git", "remote", "add", remote_name, to_repo],
            cwd=from_repo,
            capture_output=True,
            check=False,  # 可能已存在
        )
        # 若已存在则更新 url
        subprocess.run(
            ["git", "remote", "set-url", remote_name, to_repo],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", remote_name, to_branch],
            cwd=from_repo,
            capture_output=True,
            check=True,
        )
        to_ref = "FETCH_HEAD"

        # 在 from_repo 中计算 merge-base 和 commit 列表
        base_result = subprocess.run(
            ["git", "merge-base", to_ref, from_ref],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        base_sha = base_result.stdout.strip()

        log_result = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", f"{base_sha}..{from_ref}"],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        shas = [s.strip() for s in log_result.stdout.strip().split("\n") if s.strip()]
    finally:
        subprocess.run(
            ["git", "remote", "remove", remote_name],
            cwd=from_repo,
            capture_output=True,
        )

    if not shas:
        return []

    result: List[CommitInfo] = []
    for sha in shas:
        msg_result = subprocess.run(
            ["git", "log", "-1", "--format=%s%n%b", sha],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        message = msg_result.stdout.strip()

        # 在 from_repo（origin）中 git show 获取 patch
        patch_result = subprocess.run(
            ["git", "show", sha, "--format=", "-p"],
            cwd=from_repo,
            capture_output=True,
            text=True,
        )
        patch_content = patch_result.stdout if patch_result.returncode == 0 else ""
        changed_files = _parse_patch_files(patch_content)

        result.append(
            CommitInfo(
                sha=sha,
                message=message,
                patch_content=patch_content,
                changed_files=changed_files,
            )
        )

    # 仅过滤 patch 完全为空的 commit（merge commit 等）
    # 注意：有 diff 的 commit 若 API 返回空，会保留并打印警告
    filtered = [c for c in result if c.patch_content.strip()]
    if len(filtered) < len(result):
        for c in result:
            if not c.patch_content.strip():
                print(f"  跳过 {c.sha[:7]} (patch 为空，可能是 merge commit)")
    return filtered


def _parse_gitcode_url(url: str) -> tuple[str, str, int]:
    """
    解析 GitCode PR URL，提取 owner、repo、pr_number。
    支持: https://gitcode.com/openeuler/kernel/pull/18031
    """
    # 匹配 /owner/repo/pull/number 或 /owner/repo/pull/number/...
    m = re.search(
        r"gitcode\.com/([^/]+)/([^/]+)/pull/(\d+)",
        url,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError(
            f"无法解析 GitCode URL，期望格式: https://gitcode.com/owner/repo/pull/123，当前: {url}"
        )
    return m.group(1), m.group(2), int(m.group(3))


def _parse_gitee_url(url: str) -> tuple[str, str, int]:
    """
    解析 Gitee PR URL，提取 owner、repo、pr_number。
    支持: https://gitee.com/openeuler/kernel/pulls/18031 或 /pull/18031
    """
    m = re.search(
        r"gitee\.com/([^/]+)/([^/]+)/pull[s]?/(\d+)",
        url,
        re.IGNORECASE,
    )
    if not m:
        raise ValueError(
            f"无法解析 Gitee URL，期望格式: https://gitee.com/owner/repo/pulls/123，当前: {url}"
        )
    return m.group(1), m.group(2), int(m.group(3))


def fetch_commits_from_gitee(
    pr_url: str,
    token: str,
    from_repo: str,
    to_repo: str,
    to_branch: str,
) -> List[CommitInfo]:
    """
    通过 Gitee API + token 从网站获取 PR 的 commit 列表，在 from_repo 中 git fetch（带 token）
    并 git show 获取 patch，最后合入到 to_repo。

    pr_url: PR 页面 URL，如 https://gitee.com/openeuler/kernel/pulls/18031
    token: Gitee 个人访问令牌
    from_repo: 源仓库，fetch 和 git show 在此执行
    to_repo: 目标仓库，patch 合入到此
    to_branch: 目标分支（如 main）

    Returns:
        按 git 拓扑序（父→子）排列的 commit 列表，保证 patch 按依赖顺序合入。
    """
    owner, repo, pr_number = _parse_gitee_url(pr_url)
    from_repo = str(Path(from_repo).resolve())
    to_repo = str(Path(to_repo).resolve())

    # 1. 用 Gitee API 从网站抓取 PR 的 commit 列表
    api_base = "https://gitee.com/api/v5"
    url = f"{api_base}/repos/{owner}/{repo}/pulls/{pr_number}/commits"
    headers = {"User-Agent": "patch-pipeline/1.0"}

    def _fetch_page(page: int, use_token: bool) -> requests.Response:
        params: dict = {"page": page, "per_page": 100}
        if use_token:
            params["access_token"] = token
        return requests.get(url, params=params, headers=headers, timeout=30)

    commits_data: List[dict] = []
    page = 1
    seen_shas: set[str] = set()
    use_token = True
    while True:
        r = _fetch_page(page, use_token)
        if r.status_code == 401 and use_token:
            # token 无效或过期，公开仓库可尝试无 token
            print("  提示：token 认证失败 (401)，尝试无 token 访问（仅公开仓库）")
            use_token = False
            r = _fetch_page(page, use_token)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for c in batch:
            sha = c.get("sha")
            if sha and sha not in seen_shas:
                seen_shas.add(sha)
                commits_data.append(c)
        if len(batch) < 100:
            break
        page += 1

    if not commits_data:
        return []

    # 2. 在 from_repo 中 git fetch 拉取 PR 内容（公开仓库可不用 token）
    if use_token:
        git_url = f"https://oauth2:{token}@gitee.com/{owner}/{repo}.git"
    else:
        git_url = f"https://gitee.com/{owner}/{repo}.git"
    remote_gitee = "_patch_pipeline_gitee"
    remote_to = "_patch_pipeline_to"

    try:
        subprocess.run(
            ["git", "remote", "add", remote_gitee, git_url],
            cwd=from_repo,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "remote", "set-url", remote_gitee, git_url],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", remote_gitee, f"pull/{pr_number}/head"],
            cwd=from_repo,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "update-ref", "refs/remotes/_patch_pr/head", "FETCH_HEAD"],
            cwd=from_repo,
            capture_output=True,
        )
        fetch_ref = "refs/remotes/_patch_pr/head"

        subprocess.run(
            ["git", "remote", "add", remote_to, to_repo],
            cwd=from_repo,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "remote", "set-url", remote_to, to_repo],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", remote_to, to_branch],
            cwd=from_repo,
            capture_output=True,
            check=True,
        )
        to_ref = "FETCH_HEAD"

        base_result = subprocess.run(
            ["git", "merge-base", to_ref, fetch_ref],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        base_sha = base_result.stdout.strip()

        # 用 merge-base 过滤，只保留 PR 相对于 target 的新 commit（与 API 顺序结合）
        log_result = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", f"{base_sha}..{fetch_ref}"],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        pr_shas = [s.strip() for s in log_result.stdout.strip().split("\n") if s.strip()]
    finally:
        subprocess.run(
            ["git", "remote", "remove", remote_gitee],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "remove", remote_to],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "update-ref", "-d", "refs/remotes/_patch_pr/head"],
            cwd=from_repo,
            capture_output=True,
        )

    # 顺序：始终用 git 拓扑序（git log --reverse），保证 patch 按依赖顺序合入
    # API 顺序可能因分页等问题不可靠，git 拓扑序是父→子的正确顺序
    ordered_shas = pr_shas

    result: List[CommitInfo] = []
    for sha in ordered_shas:
        commit_obj = next((c for c in commits_data if c.get("sha") == sha), None)
        if commit_obj:
            msg = (commit_obj.get("commit") or {}).get("message", "")
        else:
            msg_result = subprocess.run(
                ["git", "log", "-1", "--format=%s%n%b", sha],
                cwd=from_repo,
                capture_output=True,
                text=True,
                check=True,
            )
            msg = msg_result.stdout.strip()

        patch_result = subprocess.run(
            ["git", "show", sha, "--format=", "-p"],
            cwd=from_repo,
            capture_output=True,
            text=True,
        )
        patch_content = patch_result.stdout if patch_result.returncode == 0 else ""
        changed_files = _parse_patch_files(patch_content)

        result.append(
            CommitInfo(
                sha=sha,
                message=msg,
                patch_content=patch_content,
                changed_files=changed_files,
            )
        )

    filtered = [c for c in result if c.patch_content.strip()]
    if len(filtered) < len(result):
        for c in result:
            if not c.patch_content.strip():
                print(f"  跳过 {c.sha[:7]} (patch 为空，可能是 merge commit)")
    return filtered


def fetch_commits_from_gitcode(
    pr_url: str,
    from_repo: str,
    to_repo: str,
    to_branch: str,
    token: Optional[str] = None,
) -> List[CommitInfo]:
    """
    通过本地 git 或 GitCode API 获取 PR 的 commits 及 patch。

    有 token 时：用 GitCode API (api.gitcode.com) 抓取 commit 列表，git fetch 带 token 拉取。
    无 token 时：仅 git fetch + git log 获取。

    pr_url: PR 页面 URL，如 https://gitcode.com/openeuler/kernel/pull/18031
    from_repo: 源仓库，fetch 和 git show 在此执行
    to_repo: 目标仓库，patch 合入到此
    to_branch: 目标分支（如 main）
    token: GitCode 个人访问令牌（可选，https://gitcode.com/setting/token-classic 创建）

    Returns:
        按 git 拓扑序（父→子）排列的 commit 列表，保证 patch 按依赖顺序合入。
    """
    owner, repo, pr_number = _parse_gitcode_url(pr_url)
    from_repo = str(Path(from_repo).resolve())
    to_repo = str(Path(to_repo).resolve())

    # GitCode token 用法：Authorization: Bearer / PRIVATE-TOKEN / access_token 查询参数
    use_api = token is not None
    commits_data: List[dict] = []
    if use_api:
        api_base = "https://api.gitcode.com/api/v5"
        url = f"{api_base}/repos/{owner}/{repo}/pulls/{pr_number}/commits"
        headers = {
            "User-Agent": "patch-pipeline/1.0",
            "Authorization": f"Bearer {token}",
        }
        page = 1
        seen_shas: set[str] = set()
        while True:
            r = requests.get(
                url,
                params={"page": page, "per_page": 100},
                headers=headers,
                timeout=30,
            )
            if r.status_code == 401:
                # 尝试 PRIVATE-TOKEN（GitCode/GitLab 风格）
                r = requests.get(
                    url,
                    params={"page": page, "per_page": 100},
                    headers={
                        "User-Agent": "patch-pipeline/1.0",
                        "PRIVATE-TOKEN": token,
                    },
                    timeout=30,
                )
            if r.status_code == 401:
                # 尝试 access_token 查询参数
                r = requests.get(
                    url,
                    params={"page": page, "per_page": 100, "access_token": token},
                    headers={"User-Agent": "patch-pipeline/1.0"},
                    timeout=30,
                )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            for c in batch:
                sha = c.get("sha")
                if sha and sha not in seen_shas:
                    seen_shas.add(sha)
                    commits_data.append(c)
            if len(batch) < 100:
                break
            page += 1

    if use_api:
        git_url = f"https://oauth2:{token}@gitcode.com/{owner}/{repo}.git"
    else:
        git_url = f"https://gitcode.com/{owner}/{repo}.git"
    remote_gitcode = "_patch_pipeline_gitcode"
    remote_to = "_patch_pipeline_to"

    try:
        # 在 from_repo 中添加 GitCode 和 to_repo 为 remote
        subprocess.run(
            ["git", "remote", "add", remote_gitcode, git_url],
            cwd=from_repo,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "remote", "set-url", remote_gitcode, git_url],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", remote_gitcode, f"pull/{pr_number}/head"],
            cwd=from_repo,
            capture_output=True,
            check=True,
        )
        # 保存 PR ref，因后续 fetch 会覆盖 FETCH_HEAD
        subprocess.run(
            ["git", "update-ref", "refs/remotes/_patch_pr/head", "FETCH_HEAD"],
            cwd=from_repo,
            capture_output=True,
        )
        fetch_ref = "refs/remotes/_patch_pr/head"

        # 在 from_repo 中 fetch to_repo 的 target 分支，用于 merge-base
        subprocess.run(
            ["git", "remote", "add", remote_to, to_repo],
            cwd=from_repo,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "remote", "set-url", remote_to, to_repo],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", remote_to, to_branch],
            cwd=from_repo,
            capture_output=True,
            check=True,
        )
        to_ref = "FETCH_HEAD"

        # 计算 merge-base 和 commit 列表（在 from_repo 中）
        base_result = subprocess.run(
            ["git", "merge-base", to_ref, fetch_ref],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        base_sha = base_result.stdout.strip()

        log_result = subprocess.run(
            ["git", "log", "--reverse", "--format=%H", f"{base_sha}..{fetch_ref}"],
            cwd=from_repo,
            capture_output=True,
            text=True,
            check=True,
        )
        shas = [s.strip() for s in log_result.stdout.strip().split("\n") if s.strip()]
    finally:
        subprocess.run(
            ["git", "remote", "remove", remote_gitcode],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "remote", "remove", remote_to],
            cwd=from_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "update-ref", "-d", "refs/remotes/_patch_pr/head"],
            cwd=from_repo,
            capture_output=True,
        )

    if not shas:
        return []

    # 顺序：始终用 git 拓扑序（git log --reverse base..head），保证 patch 按依赖顺序合入
    # API 顺序可能因分页等问题不可靠，git 拓扑序是父→子的正确顺序
    ordered_shas = shas

    result: List[CommitInfo] = []
    for sha in ordered_shas:
        commit_obj = next((c for c in commits_data if c.get("sha") == sha), None)
        if commit_obj:
            message = (commit_obj.get("commit") or {}).get("message", "")
        else:
            msg_result = subprocess.run(
                ["git", "log", "-1", "--format=%s%n%b", sha],
                cwd=from_repo,
                capture_output=True,
                text=True,
                check=True,
            )
            message = msg_result.stdout.strip()

        patch_result = subprocess.run(
            ["git", "show", sha, "--format=", "-p"],
            cwd=from_repo,
            capture_output=True,
            text=True,
        )
        patch_content = patch_result.stdout if patch_result.returncode == 0 else ""
        changed_files = _parse_patch_files(patch_content)

        result.append(
            CommitInfo(
                sha=sha,
                message=message,
                patch_content=patch_content,
                changed_files=changed_files,
            )
        )

    filtered = [c for c in result if c.patch_content.strip()]
    if len(filtered) < len(result):
        for c in result:
            if not c.patch_content.strip():
                print(f"  跳过 {c.sha[:7]} (patch 为空，可能是 merge commit)")
    return filtered
