"""从 pending_pr.txt 读取 PR 编号列表。"""

from pathlib import Path
from typing import Union


def load_pending_prs(path: Union[str, Path]) -> list[int]:
    """
    从文件读取 PR 编号，每行一个，按顺序返回。
    跳过空行和 # 开头的注释。
    """
    p = Path(path)
    if not p.exists():
        return []
    prs: list[int] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            prs.append(int(line))
        except ValueError:
            continue
    return prs


def load_completed_prs(pending_list_path: Union[str, Path]) -> set[int]:
    """从 completed_pr.txt 读取已完成的 PR 编号集合。"""
    p = Path(pending_list_path)
    completed_path = p.parent / "completed_pr.txt"
    if not completed_path.exists():
        return set()
    return set(load_pending_prs(completed_path))


def default_pending_path() -> Path:
    """默认 pending_pr.txt 路径（src/pending_list/pending_pr.txt）。"""
    # patch_pipeline 在 src/patch_pipeline，pending_list 在 src/pending_list
    pkg_dir = Path(__file__).parent.parent  # src
    return pkg_dir / "pending_list" / "pending_pr.txt"


def record_pr_completed(pr_id: int, pending_list_path: Path) -> None:
    """将 PR 记录为已完成，追加到 completed_pr.txt。"""
    completed_path = pending_list_path.parent / "completed_pr.txt"
    with open(completed_path, "a", encoding="utf-8") as f:
        f.write(f"{pr_id}\n")
