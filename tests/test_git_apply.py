"""测试 git_apply 模块，重点验证 patch_files 目录下会生成 .patch 文件。"""

from pathlib import Path

import pytest

from patch_pipeline.git_apply import apply_patch


# 参考 Gitee PR 18031 的典型 kernel patch 格式
SAMPLE_PATCH = """diff --git a/drivers/fwctl/core.c b/drivers/fwctl/core.c
new file mode 100644
index 0000000000000..1234567890abc
--- /dev/null
+++ b/drivers/fwctl/core.c
@@ -0,0 +1,5 @@
+// SPDX-License-Identifier: GPL-2.0-only
+/*
+ * fwctl core driver
+ */
+/* test patch for bd47441 */
"""


def test_apply_patch_writes_to_patch_files(tmp_path: Path) -> None:
    """
    验证 apply_patch 会将 patch 内容写入 patch_files/<label>.patch，
    且文件保留不删除。patch 写入发生在 git apply 之前，故无需真实 git 仓库。
    """
    repo = str(tmp_path)

    apply_patch(
        repo,
        patch_content=SAMPLE_PATCH,
        fuzz=0,
        patch_label="bd47441",
    )

    patch_file = tmp_path / "patch_files" / "bd47441.patch"
    assert patch_file.exists(), f"patch 文件应生成在 patch_files/bd47441.patch，路径: {tmp_path}"
    assert patch_file.read_text() == SAMPLE_PATCH
