"""CLI 入口。"""

from pathlib import Path
from typing import Optional

import typer

from .pending_loader import (
    default_pending_path,
    load_completed_prs,
    load_pending_prs,
    record_pr_completed,
)
from .pipeline import run_pipeline

app = typer.Typer(
    name="patch-pipeline",
    help="补丁回合自动化流水线：本地或 GitCode PR commits 合入 + OpenCode patch/review agents",
)


@app.command()
def main(
    to_repo: str = typer.Option(
        ...,
        "--to",
        "-t",
        help="目标仓库（openEuler-Kernel-kunpeng），patch 合入到此",
    ),
    gitcode_url: Optional[str] = typer.Option(
        None,
        "--gitcode",
        "-g",
        help="GitCode PR URL（如 https://gitcode.com/openeuler/kernel/pull/18031）",
    ),
    gitee_url: Optional[str] = typer.Option(
        None,
        "--gitee",
        help="Gitee PR URL（如 https://gitee.com/openeuler/kernel/pulls/18031）",
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        "-T",
        help="GitCode/Gitee 个人访问令牌（Gitee 必填，GitCode 可选）",
    ),
    from_repo: Optional[str] = typer.Option(
        None,
        "--from",
        "-f",
        help="源仓库（openEuler-Kernel-origin），git show 在此执行",
    ),
    pr: Optional[int] = typer.Option(
        None,
        "--pr",
        "-p",
        help="PR 编号（本地模式，用于解析 pull/{id}/head、pr-{id} 等）",
    ),
    show_patch: bool = typer.Option(
        False,
        "--show-patch/--no-show-patch",
        help="是否打印每个 commit 的完整 patch 内容",
    ),
    pending_list: Optional[str] = typer.Option(
        None,
        "--pending-list",
        "-l",
        help="待合入 PR 列表文件路径，每行一个 PR 编号。不指定路径时使用 pending_list/pending_pr.txt",
    ),
    batch: bool = typer.Option(
        False,
        "--batch",
        "-b",
        help="批量模式，从 pending_list/pending_pr.txt 读取 PR 列表（等同于 -l pending_list/pending_pr.txt）",
    ),
    gitee_base: Optional[str] = typer.Option(
        None,
        "--gitee-base",
        help="Gitee PR 基础 URL（批量模式必填），如 https://gitee.com/openeuler/kernel/pulls",
    ),
) -> None:
    """
    执行补丁回合流水线。

    四种模式：
    1. 批量模式：--pending-list 文件 --from --to --token --gitee-base，按顺序逐个合入
    2. Gitee 模式：--gitee URL --token 令牌 --from origin --to kunpeng
    3. GitCode 模式：--gitcode URL --from origin --to kunpeng
    4. 本地模式：--from origin --to kunpeng --pr 编号
    运行前请手动切换到目标分支，to 仓库当前分支用于 merge-base 计算
    """
    to_path = Path(to_repo).resolve()
    if not to_path.is_dir():
        typer.echo(f"错误：目标仓库不存在 {to_path}", err=True)
        raise typer.Exit(1)

    # 批量模式：从 pending_list 读取 PR，按顺序逐个合入
    use_batch = batch or pending_list is not None
    if use_batch:
        if not from_repo or not token:
            typer.echo(
                "错误：批量模式需要 --from、--to、--token、--gitee-base",
                err=True,
            )
            raise typer.Exit(1)
        base = (gitee_base or "").rstrip("/")
        if not base:
            typer.echo("错误：批量模式需要 --gitee-base", err=True)
            raise typer.Exit(1)
        pl_path = (
            Path(pending_list).resolve()
            if pending_list
            else default_pending_path()
        )
        if not pl_path.exists():
            typer.echo(f"错误：pending 文件不存在 {pl_path}", err=True)
            raise typer.Exit(1)
        from_path = Path(from_repo).resolve()
        if not from_path.is_dir():
            typer.echo(f"错误：源仓库不存在 {from_path}", err=True)
            raise typer.Exit(1)
        all_pr_ids = load_pending_prs(pl_path)
        completed = load_completed_prs(pl_path)
        pr_ids = [p for p in all_pr_ids if p not in completed]
        if completed:
            typer.echo(f"已跳过 {len(completed)} 个已完成 PR")
        if not pr_ids:
            typer.echo("无待合入 PR，退出。")
            return
        typer.echo(f"共 {len(pr_ids)} 个 PR 待合入：{pr_ids[:5]}{'...' if len(pr_ids) > 5 else ''}")
        for i, pr_id in enumerate(pr_ids, 1):
            typer.echo(f"\n{'#'*60}\n[{i}/{len(pr_ids)}] PR #{pr_id}\n{'#'*60}")
            try:
                run_pipeline(
                    from_repo=str(from_path),
                    to_repo=str(to_path),
                    gitee_url=f"{base}/{pr_id}",
                    token=token,
                    show_patch=show_patch,
                    batch_pr_id=pr_id,
                    require_review_confirmation=True,
                )
            except SystemExit:
                typer.echo("用户中断，退出。")
                raise typer.Exit(1)
            record_pr_completed(pr_id, pl_path)
        return

    if gitee_url and token:
        # Gitee 模式：用 token 从网站 API 抓取 commit 列表
        if not from_repo:
            typer.echo(
                "错误：Gitee 模式需要 --from（源仓库）和 --to（目标仓库）",
                err=True,
            )
            raise typer.Exit(1)
        from_path = Path(from_repo).resolve()
        if not from_path.is_dir():
            typer.echo(f"错误：源仓库不存在 {from_path}", err=True)
            raise typer.Exit(1)
        run_pipeline(
            from_repo=str(from_path),
            to_repo=str(to_path),
            gitee_url=gitee_url,
            token=token,
            show_patch=show_patch,
        )
    elif gitcode_url:
        # GitCode 模式：可选 --token 用 API 抓取
        if not from_repo:
            typer.echo(
                "错误：GitCode 模式需要 --from（源仓库 origin）和 --to（目标仓库 kunpeng）",
                err=True,
            )
            raise typer.Exit(1)
        from_path = Path(from_repo).resolve()
        if not from_path.is_dir():
            typer.echo(f"错误：源仓库不存在 {from_path}", err=True)
            raise typer.Exit(1)
        run_pipeline(
            from_repo=str(from_path),
            to_repo=str(to_path),
            gitcode_url=gitcode_url,
            token=token,
            show_patch=show_patch,
        )
    else:
        # 本地模式
        if not from_repo or pr is None:
            typer.echo(
                "错误：本地模式需要 --from 和 --pr，或使用 --gitee/--gitcode 指定 PR",
                err=True,
            )
            raise typer.Exit(1)
        from_path = Path(from_repo).resolve()
        if not from_path.is_dir():
            typer.echo(f"错误：源仓库不存在 {from_path}", err=True)
            raise typer.Exit(1)
        run_pipeline(
            from_repo=str(from_path),
            to_repo=str(to_path),
            pr_id=pr,
            show_patch=show_patch,
        )


if __name__ == "__main__":
    app()
