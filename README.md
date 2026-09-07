# IssuePilot

IssuePilot 是一个面向开源贡献的本地 AI 工作流：它用你的 GitHub 身份寻找高 stars 项目中的开放 issue，让 Codex 在本地仓库里分析、修改和测试，然后在通过安全检查后创建 **Draft Pull Request**。

它不是“看到 issue 就盲目发 PR”的机器人。默认只在本地准备修改；只有显式运行 `publish`（或传入 `--publish`）并解锁发布，才会 fork、推送分支和创建草稿 PR。

[快速开始](#安装) · [完整工作流](docs/workflow.md) · [安全边界](#安全边界) · [竞品分析](docs/competitive-analysis.md)

## 能做什么

1. 按 Stars 上下限、编程语言、Issue 标签、仓库活跃度、Issue 创建/更新时间和排序模式搜索非归档、非 fork 的项目；网络查询使用有限并发以缩短等待。
2. 收集未分配的候选，检查开放 PR、近期关联 Commit、自然语言认领，以及“已实现/可关闭/重复”留言，并按可执行性打分。每次扫描会将候选和筛选条件记在本地，刷新页面仍可恢复最近结果。
3. 解释每个候选为什么适合、有哪些风险，以及长期未解决的可能原因；这些解释是透明的启发式提示，不冒充维护者结论。
4. 拉取 issue 正文和讨论，交给已登录的 Codex 检查代码、实现最小修复并运行相关测试。
5. 审查改动文件数、diff 大小、敏感凭证、Git 元数据和受保护路径。
6. 保存可校验的准备清单，确保审核后发布的还是 AI 测试过的同一份 diff。
7. 使用你的 GitHub 账号 fork 项目、提交 commit，并向上游创建 Draft PR。
8. 由 Codex 定时任务跟进已发布 PR 的讨论、Review、CI、冲突和合并状态；明确的新修改先在本地准备并验证，仍需人工确认才能推送。

IssuePilot 会区分“发现候选”和“准备成功”：候选通过元数据与空闲状态检查，并不代表需求已经形成共识、具备本地验证条件，或一定适合自动修改。完整状态模型和推荐的每日任务见[工作流说明](docs/workflow.md)。

每次发现会记住仓库搜索位置：只要候选数量未达到目标，就会自动继续向后扫描，直到找满、GitHub 当前条件下的可搜索范围确实耗尽，或用户在网页中点击“停止扫描”。Stars 升序或降序模式会在遇到 GitHub 单次搜索 1000 条结果上限时自动切换 Stars 区间，继续发现新仓库。IssuePilot 会读取 GitHub Search、Core 和 GraphQL API 返回的剩余配额；接近任一上限时会自动等待下一个配额窗口后继续，而不是把一次完整扫描中断成错误。找满后会立即停止；全部扫完仍不足时会如实返回已找到的数量。下次搜索会从上次结束后的新批次继续。连接 GitHub 后，每个候选的实时状态、最近讨论、时间线和开放 PR 搜索会合并为一次 GraphQL 核验；未登录时仍使用兼容的 REST 只读流程。

## 安装

需要：

- Python 3.9+
- Git
- 已安装并登录的 Codex CLI（Codex 桌面版已包含）
- 发布 PR 时需要 GitHub token，或者已经登录的 GitHub CLI `gh`

在本目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

不配置 token 也可以匿名搜索公开仓库并在本地准备修复，但 GitHub 的匿名 API 限额较低。发布 PR 时，在 `.env` 中填入 GitHub token；它需要 fork、写入 fork 内容和创建 pull request 的权限。请使用专用、最小权限、可随时撤销的 token，不要把 `.env` 提交到 Git。

如果已经安装并登录 `gh`，可以不写 token；IssuePilot 会读取 `gh auth token` 的登录态。

## 使用

### 网页控制台（推荐）

安装完成后，可以直接打开本机控制台：

```bash
issuepilot web
```

浏览器会打开 `http://127.0.0.1:8765/`。在这里可以检查 GitHub / Codex 状态、寻找候选 Issue、启动 AI 修复、查看待审核记录，并在二次确认后创建 Draft PR。控制台只监听本机，不会把 token、工作区或 AI 操作暴露到公网。

准备修复时，IssuePilot 会先使用标准 Git 浅克隆；如果网络连接失败且本机已连接 GitHub CLI，会自动改用 GitHub CLI 的认证下载通道。

如果前端源码有改动，先在 `web/` 目录运行 `pnpm local:build` 生成本地控制台资源；普通使用不需要这一步。

### 命令行

先检查环境：

```bash
issuepilot doctor
```

首次使用时，可以运行一次深度自检。它会在临时目录创建一个极小仓库，让真实 Codex 修复一个故意设置的错误，再独立运行测试；不会访问或写入 GitHub：

```bash
issuepilot selftest
```

只搜索和排序候选 issue，不修改 GitHub：

```bash
issuepilot discover --limit 20
```

也可以临时覆盖常用筛选条件：

```bash
issuepilot discover --limit 10 --min-stars 5000 --max-stars 30000 \
  --language TypeScript --language Python \
  --label "good first issue" --label bug \
  --created-within-days 180 --updated-within-days 90 \
  --repo-active-within-days 30 --sort stars_asc
```

针对一个明确的 issue，让 AI 在 `.oss-agent/workspaces/` 中准备修复：

```bash
issuepilot run --issue https://github.com/OWNER/REPO/issues/123
```

每个工作区根目录都会生成 `ISSUEPILOT_REVIEW.zh-CN.md`，其中包含 Issue 全文翻译、所有讨论翻译、可处理性分析、实际改动和验证结果。该文件仅供本地审核，已被 Git 本地忽略，不会进入 commit 或 PR。再在该目录执行 `git diff` 检查真实代码改动。确认无误后，发布已经准备好的同一份修改：

```bash
export ISSUEPILOT_ALLOW_PUBLISH=I_UNDERSTAND
issuepilot publish --issue https://github.com/OWNER/REPO/issues/123
```

`publish` 不会再次运行 AI。它会核对 diff 指纹；如果审核期间文件被改动，会阻止发布并要求重新准备。

查看所有准备记录和发布状态：

```bash
issuepilot prepared
```

### 每日扫描与 PR 跟进

IssuePilot 本身提供确定性的发现、准备、发布和归档命令；每日调度及 PR 跟进由 Codex 定时任务编排。任务会先处理已发布 PR 的新增讨论与 CI，再继续候选发现，并把去重状态保存在本地 `.oss-agent/` 中。可直接复用的任务模板、状态解释和人工审批边界见 [docs/workflow.md](docs/workflow.md)。

如果一次尝试留下了不需要的本地改动，先把它可恢复地归档，再重新运行：

```bash
issuepilot archive --issue https://github.com/OWNER/REPO/issues/123
```

归档内容保存在 `.oss-agent/archive/`，不会直接删除。

如果明确需要一步完成准备和发布，也可以使用：

```bash
export ISSUEPILOT_ALLOW_PUBLISH=I_UNDERSTAND
issuepilot run --issue https://github.com/OWNER/REPO/issues/123 --publish
```

从排名最高的候选中自动尝试一个：

```bash
issuepilot autopilot --max-issues 1
```

自动尝试并发布 Draft PR：

```bash
export ISSUEPILOT_ALLOW_PUBLISH=I_UNDERSTAND
issuepilot autopilot --max-issues 1 --publish
```

建议从 `--max-issues 1` 开始。大量、低质量、重复或未经维护者确认的自动 PR 会给开源社区制造负担。

## 配置

配置既可以写进当前目录的 `.env`，也可以作为环境变量传入：

| 变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `GITHUB_TOKEN` / `GH_TOKEN` | 无 | 发布所需的 GitHub 身份凭证；只读流程可匿名 |
| `ISSUEPILOT_MIN_STARS` | `5000` | 候选仓库最低 stars |
| `ISSUEPILOT_MAX_STARS` | `1000000` | 候选仓库最高 stars，必须不低于最低值 |
| `ISSUEPILOT_LANGUAGES` | `Python,TypeScript,JavaScript,Go,Rust` | 逗号分隔语言 |
| `ISSUEPILOT_REPO_LIMIT` | `20` | 每轮检查的仓库上限 |
| `ISSUEPILOT_ISSUES_PER_REPO` | `5` | 每仓库候选上限 |
| `ISSUEPILOT_MAX_REPO_IDLE_DAYS` | `180` | 排除超过此天数没有推送的仓库 |
| `ISSUEPILOT_MAX_ISSUE_IDLE_DAYS` | `365` | 排除超过此天数没有更新的 Issue |
| `ISSUEPILOT_MAX_ISSUE_AGE_DAYS` | `180` | 排除从创建至今超过此天数的 Issue |
| `ISSUEPILOT_ISSUE_LABELS` | `good first issue,help wanted` | 满足任一标签即可进入候选池 |
| `ISSUEPILOT_ACTIVE_WORK_DAYS` | `45` | 检测近期关联 Commit 或明确认领留言的时间窗口 |
| `ISSUEPILOT_DISCOVERY_CONCURRENCY` | `6` | GitHub 只读检查的最大并发数（1–8） |
| `ISSUEPILOT_DISCOVERY_SCAN_PAGES` | `10` | 找满目标候选前最多扫描的仓库批次数（1–10） |
| `ISSUEPILOT_DISCOVERY_SORT` | `recommended` | 排序模式：综合推荐、Stars 升/降序、Issue 最新创建或最近更新 |
| `ISSUEPILOT_EXCLUDED_TOPICS` | 见 `.env.example` | 排除书单、awesome-list、面试资源等非代码仓库主题；留空可关闭 |
| `ISSUEPILOT_CODEX_MODEL` | Codex 默认 | 可选模型覆盖 |
| `ISSUEPILOT_CODEX_TIMEOUT_SECONDS` | `1800` | 单个 AI 修复的超时秒数（60–7200） |
| `ISSUEPILOT_MAX_CHANGED_FILES` | `20` | 自动接受的改动文件数上限 |
| `ISSUEPILOT_MAX_DIFF_BYTES` | `200000` | 自动接受的 diff 大小上限 |
| `ISSUEPILOT_WORKSPACE` | `.oss-agent/workspaces` | 临时仓库位置 |
| `ISSUEPILOT_ALLOW_PUBLISH` | 未设置 | 必须精确为 `I_UNDERSTAND` 才允许发布 |

加 `--json` 可获得适合脚本消费的 JSON 输出，例如：

```bash
issuepilot --json discover --limit 5
```

## 安全边界

- issue、评论、仓库文件和测试输出都被视为不可信输入。
- 目标仓库被标记为不可信项目，Codex 跳过其项目级配置、hooks 和执行规则，并使用 `workspace-write` 沙箱；提示词明确禁止访问凭证和执行外部写入。
- Codex 的测试命令不继承 GitHub token 等秘密环境变量，且默认禁用沙箱内网络访问。
- AI 不负责 commit、push 或开 PR；这些动作由确定性的本地流程在检查后完成。
- `.github/workflows/`、`.github/actions/`、`CODEOWNERS`、`SECURITY.md` 等高风险路径不能自动发布。
- AI 若改写 Git 历史、本地 Git 配置或创建活动 hook，流程会阻止发布。
- commit 和 push 都禁用仓库内 Git hooks，避免不可信仓库在发布阶段执行额外代码。
- 新增内容会扫描常见 token、私钥和密码模式。
- 所有 PR 默认是 Draft，并带有 AI 生成和人工复核提示。
- commit、push 或创建 PR 中途失败时，准备清单会保存阶段状态；再次执行 `publish` 可安全续跑，并会复用已有 PR。

`workspace-write` 重点限制写入，并不等价于机密文件的完全读取隔离。对蓄意对抗或来源可疑的仓库，请在专用系统账号、容器或虚拟机中运行。沙箱和正则扫描不能证明代码绝对安全。发布前仍应阅读完整 diff、确认测试结果、检查项目的 `CONTRIBUTING` 指南，并确认没有其他贡献者正在处理同一个 issue。

## 开发与测试

项目只使用 Python 标准库。运行测试：

```bash
PYTHONPATH=src python3 -m unittest discover -v
```

当前实现针对公开仓库和 GitHub.com。组织 SSO、企业 GitHub、自定义 fork 目标、需要先认领的 issue，以及复杂的多仓库构建系统，仍可能需要人工处理。

同类项目的逐项分析、采用理由和暂缓项见 [竞品分析与产品取舍](docs/competitive-analysis.md)。
