"""CLI 入口。"""

from pathlib import Path
from typing import Optional

import typer

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
        True,
        "--show-patch/--no-show-patch",
        help="是否打印每个 commit 的完整 patch 内容",
    ),
) -> None:
    """
    执行补丁回合流水线。

    三种模式：
    1. Gitee 模式：--gitee URL --token 令牌 --from origin --to kunpeng
    2. GitCode 模式：--gitcode URL --from origin --to kunpeng
    3. 本地模式：--from origin --to kunpeng --pr 编号
    运行前请手动切换到目标分支，to 仓库当前分支用于 merge-base 计算
    """
    to_path = Path(to_repo).resolve()
    if not to_path.is_dir():
        typer.echo(f"错误：目标仓库不存在 {to_path}", err=True)
        raise typer.Exit(1)

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
