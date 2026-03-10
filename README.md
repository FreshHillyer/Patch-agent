# Patch Pipeline

补丁回合自动化流水线：从 Gitee/GitCode PR 获取 commits，用 `git apply -C 3 --reject` 尝试合入，冲突时调用 OpenCode os-merge-expert 解决，合入后调用 patch-review-expert 校验。支持批量模式，按 `pending_pr.txt` 顺序逐个合入 PR。

## 安装

```bash
pip install .
```

需要已安装 [OpenCode CLI](https://opencode.ai/docs/cli/)。

## 使用

### 批量模式（推荐）

从 `pending_list/pending_pr.txt` 读取 PR 列表，按顺序逐个合入。已完成的 PR（记录在 `completed_pr.txt`）会自动跳过。

```bash
patch-pipeline --batch \
  --to /path/to/openEuler-Kernel-kunpeng \
  --from /path/to/openEuler-Kernel-origin \
  --token YOUR_GITEE_TOKEN \
  --gitee-base https://gitee.com/openeuler/kernel/pulls
```

- `--batch` / `-b`：批量模式，使用默认 `pending_list/pending_pr.txt`
- `--pending-list` / `-l`：指定 PR 列表文件路径（每行一个 PR 编号）
- `--gitee-base`：Gitee PR 基础 URL（如 `https://gitee.com/openeuler/kernel/pulls`）
- `--from`：源仓库
- `--to`：目标仓库，patch 合入到此
- `--token`：Gitee 个人访问令牌

**批量模式流程**：
1. 读取 `pending_pr.txt`，过滤掉 `completed_pr.txt` 中已完成的 PR
2. 按顺序逐个 PR 合入
3. 每个 PR 的 commits 全部完成后：`git commit -m "PR {id} 自动合入"`，并追加到 `completed_pr.txt`
4. 当 os-merge-expert 或 patch-review-expert 被调用时，Review 输出后需用户确认 `(y/n)`，拒绝则中断程序

### Gitee 模式（单 PR）

```bash
patch-pipeline --gitee https://gitee.com/openeuler/kernel/pulls/18031 \
               --token YOUR_GITEE_TOKEN \
               --from /path/to/openEuler-Kernel-origin \
               --to /path/to/openEuler-Kernel-kunpeng
```

### GitCode 模式

```bash
patch-pipeline --gitcode https://gitcode.com/openeuler/kernel/pull/18031 \
               --from /path/to/openEuler-Kernel-origin \
               --to /path/to/openEuler-Kernel-kunpeng
```

- `--token`：可选，有 token 时从 API 抓取 commit 列表

### 本地模式

```bash
patch-pipeline --from /path/to/openEuler-Kernel-origin \
               --to /path/to/openEuler-Kernel-kunpeng \
               --pr 123
```

运行前请手动切换到目标分支，`--to` 仓库当前分支用于 merge-base 计算。

### 参数

| 参数 | 说明 |
|------|------|
| `--to` / `-t` | 目标仓库，patch 合入到此（必填） |
| `--from` / `-f` | 源仓库，git show 在此执行 |
| `--gitee` | Gitee PR URL（Gitee 模式） |
| `--gitee-base` | Gitee PR 基础 URL（批量模式必填） |
| `--token` / `-T` | Gitee 个人访问令牌（Gitee 必填，GitCode 可选） |
| `--gitcode` / `-g` | GitCode PR URL（GitCode 模式） |
| `--pr` / `-p` | PR 编号（本地模式） |
| `--batch` / `-b` | 批量模式，从 pending_list/pending_pr.txt 读取 |
| `--pending-list` / `-l` | PR 列表文件路径（批量模式） |
| `--show-patch` / `--no-show-patch` | 是否打印每个 commit 的完整 patch（默认关闭） |

## 文件结构

```
pending_list/
├── pending_pr.txt    # 待合入 PR 列表，每行一个编号
└── completed_pr.txt  # 已完成的 PR（自动追加，用于跳过）
```

目标仓库中会生成 `patch_files/<sha>.patch`，每个 commit 的 patch 会写入对应文件便于调试。

## 流程

1. 获取 commits：Gitee/GitCode API 或本地 `merge-base..HEAD`，按拓扑顺序排列
2. 对每个 commit：将 patch 写入 `patch_files/<sha>.patch`，`git apply -C 3 --reject` 尝试合入
3. 若冲突：调用 OpenCode **os-merge-expert** 解决
4. os-merge-expert 成功后：调用 **patch-review-expert** 审查
5. 批量模式下：Review 输出后需用户确认 `(y/n)`，拒绝则退出
6. 连续 2 次失败：CLI 暂停，等待用户手动解决后按 Enter 继续
7. PR 全部完成（批量模式）：`git commit -m "PR {id} 自动合入"`，并记录到 `completed_pr.txt`

## 跳过的 commit

- **patch 为空**：merge commit、空提交
- **已合入过**：`git apply -R --check` 成功，说明目标仓库中该修改已存在
