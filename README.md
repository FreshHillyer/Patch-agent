# Patch Pipeline

补丁回合自动化流水线：从本地或 GitCode PR 获取 commits，用 `git apply -F 0` 尝试合入，冲突时调用 OpenCode patch agent 解决，合入后调用 review agent 校验，连续 2 次失败则 CLI 暂停等待用户处理。

## 安装

```bash
pip install .
```

需要已安装 [OpenCode CLI](https://opencode.ai/docs/cli/)。

## 使用

### Gitee 模式（用令牌从网站 API 抓取）

```bash
patch-pipeline --gitee https://gitee.com/openeuler/kernel/pulls/18031 \
               --token YOUR_GITEE_TOKEN \
               --from /path/to/openEuler-Kernel-origin \
               --to /path/to/openEuler-Kernel-kunpeng
```

- `--gitee`：Gitee PR 页面 URL
- `--token`：Gitee 个人访问令牌（设置 → 开发者设置 → 个人访问令牌）。若返回 401，公开仓库会自动尝试无 token 访问
- `--from`：源仓库，在此执行 `git fetch`（带 token）和 `git show` 获取 patch
- `--to`：目标仓库，patch 合入到此

### GitCode 模式

```bash
patch-pipeline --gitcode https://gitcode.com/openeuler/kernel/pull/18031 \
               --token YOUR_GITCODE_TOKEN \
               --from /path/to/openEuler-Kernel-origin \
               --to /path/to/openEuler-Kernel-kunpeng
```

- `--token`：GitCode 个人访问令牌（可选，https://gitcode.com/setting/token-classic 创建）。有 token 时从 API 抓取 commit 列表
- `--from`（origin）：源仓库，在此执行 `git fetch` 和 `git show` 获取 patch
- `--to`（kunpeng）：目标仓库，patch 合入到此

### 本地模式

```bash
patch-pipeline --from /path/to/openEuler-Kernel-origin \
               --to /path/to/openEuler-Kernel-kunpeng \
               --pr 123
```

- `--from`（origin）：源仓库，PR 在此，`git show` 在此执行
- `--to`（kunpeng）：目标仓库，patch 合入到此

运行前请手动切换到目标分支，`--to` 仓库当前分支用于 merge-base 计算。

### 参数

| 参数 | 说明 |
|------|------|
| `--from` / `-f` | 源仓库（origin），git show 在此执行 |
| `--to` / `-t` | 目标仓库（kunpeng），patch 合入到此 |
| `--gitee` | Gitee PR URL（Gitee 模式） |
| `--token` / `-T` | GitCode/Gitee 个人访问令牌（GitCode 可选，Gitee 必填） |
| `--gitcode` / `-g` | GitCode PR URL（GitCode 模式） |
| `--pr` / `-p` | PR 编号（本地模式） |
| `--show-patch` / `--no-show-patch` | 是否打印每个 commit 的完整 patch（默认开启） |

## 流程

1. 获取 commits：GitCode API 或本地 `merge-base..HEAD`
2. 对每个 commit：`git apply -F 0` 尝试合入
3. 若冲突：调用 OpenCode patch agent 解决
4. 合入成功后：调用 OpenCode review agent 审查
5. 连续 2 次失败：CLI 暂停，等待用户手动解决后按 Enter 继续

## 跳过的 commit

以下情况会跳过 commit：
1. **patch 为空**：API 或本地 git 返回空（如 merge commit、空提交）
2. **已合入过**：`git apply -R --check` 成功，说明 --to 仓库中该 patch 的修改已存在，视为曾合入过

若某个 commit 被跳过，可开启 `--show-patch` 查看实际获取到的 patch。
