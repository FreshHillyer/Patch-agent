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


def default_pending_path() -> Path:
    """默认 pending_pr.txt 路径（src/pending_list/pending_pr.txt）。"""
    # patch_pipeline 在 src/patch_pipeline，pending_list 在 src/pending_list
    pkg_dir = Path(__file__).parent.parent  # src
    return pkg_dir / "pending_list" / "pending_pr.txt"
