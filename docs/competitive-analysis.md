# IssuePilot 同类项目分析与产品取舍

> 调研快照：2026-09-02。Star 和项目方向会变化，链接到各项目 GitHub 首页作为后续复核入口。

IssuePilot 的目标不是单纯“让 AI 写一段代码”，而是把一次负责任的开源贡献串起来：发现候选、核验是否有人处理、解释风险、在本地完成可审查修复，最后由人决定是否创建 Draft PR。

## 结论

完整覆盖上述链路的成熟开源产品很少。高 Star 项目通常擅长执行指定任务，却不负责从外部开源社区中挑选值得贡献的 Issue；直接覆盖“发现 + 修复 + PR”的项目又普遍较新。IssuePilot 因此应把差异化放在贡献机会判断和社区礼仪上，而不是宣称拥有独有的代码生成能力。

## 值得参考的项目

### ContriFlow

- 项目：https://github.com/Sunder-Kumar/contriflow
- 可取之处：用语言、Star、Label 等条件搜索项目和 Issue；将发现、方案建议、仓库准备和贡献流程放在一个入口中。
- 局限：更像命令行新手助手；对关联 PR、关联 Commit、认领留言和长期积压原因的判断不足。
- IssuePilot 采用：多维筛选，并在前端把筛选条件直接暴露给普通用户。

### Sweep

- 项目：https://github.com/sweepai/sweep
- 调研时约 7.7k Star；项目首页显示当前主方向已转为 JetBrains AI 编码助手。
- 可取之处：早期 Issue 到 PR 的交互非常直接；会利用测试和 GitHub Actions 的失败反馈继续修正。
- 局限：当前不再以开源贡献发现为核心；依赖远程 CI 的闭环也会增加权限和外部副作用。
- IssuePilot 采用：要求代理运行相关测试并清楚报告结果。
- 暂缓：自动读取 PR 上的远程 CI 后持续推送。它需要额外发布授权、费用与防止无限重试的预算机制。

### hive-mind

- 项目：https://github.com/link-assistant/hive-mind
- 可取之处：Issue URL 可直接进入自动 Fork、分支、PR 流程；支持 Codex 等多种执行代理；有任务队列和跳过已有 PR 的选项。
- 局限：强项在执行编排，不负责判断某个外部 Issue 是否适合打扰维护者。
- IssuePilot 采用：处理前、发布前两次检查占用状态；开放 PR、近期关联 Commit、近期自然语言认领，以及“已实现/可关闭/重复”留言都会阻止运行。

### aixgo Code

- 项目：https://github.com/aixgo-dev/code
- 可取之处：由人施加标签才开始；必须通过项目自己的质量门槛；Review 意见可触发后续修改；最终只由人合并。
- 局限：面向有仓库写权限的内部团队，不是外部开源贡献者。
- IssuePilot 采用：人负责发布决定、代理负责最小修复和测试、结果默认停在本地待审核状态，PR 永远先建为 Draft。
- 下一阶段：为准备记录增加“根据 Review 继续同一任务”的受控入口。

### Jiffy

- 项目：https://github.com/Jiffy-Agnet/gateway
- 调研时为非常早期项目。
- 可取之处：输入适配器、任务队列、隔离容器、执行器、状态记录相互分离；GitHub、GitLab、Gitea 可以共用核心流程。
- 局限：部署较重，当前成熟度和社区验证有限。
- IssuePilot 采用：Web、CLI、GitHub 客户端、Codex 执行器和本地状态分别实现；准备记录支持恢复。
- 暂缓：多平台和多 worker 队列。现阶段先把单用户 GitHub 流程做可靠。

### Arbiter

- 项目：https://github.com/git-arbiter
- 可取之处：本地优先、标签触发、自动监控、完成后交给人 Review。
- 局限：面向用户管理的仓库，并带产品授权路线；不做外部项目发现。
- IssuePilot 采用：本地优先和明确触发。
- 暂缓：后台持续监听。外部开源贡献不适合在无人查看时批量生成 PR。

### GitAuto

- 项目：https://github.com/guibranco/gitauto
- 可取之处：AI 先解释理解和方案，人同意后才创建 PR；PR Review 后可以继续修改。
- 局限：需要安装 GitHub App 到目标仓库，适用于自有仓库而非任意外部项目。
- IssuePilot 采用：连接 GitHub 不等于授权发布；每个准备结果都需要单独确认。

### SWE-agent 与 OpenHands

- 项目：https://github.com/SWE-agent/SWE-agent、https://github.com/OpenHands/OpenHands
- 调研时分别约 20.2k 和 75k Star。
- 可取之处：成熟的代码代理、工具使用、隔离执行和任务结果结构化；可以作为未来可插拔执行后端。
- 局限：它们解决“如何完成指定任务”，而不是“这个外部 Issue 值不值得贡献”。
- IssuePilot 采用：把执行器和 GitHub 编排分开；目标仓库按不可信输入处理；结构化返回状态、测试和风险。
- 下一阶段：把 CodexAgent 抽象成可选执行后端，但不复制这些大型代理平台。

## 已落地的组合方案

| 能力 | 来源启发 | IssuePilot 当前做法 |
| --- | --- | --- |
| 多维发现 | ContriFlow | Stars 上下限、语言、Issue 标签、仓库活跃天数、Issue 存在/更新天数、候选数量和多种排序模式；单次 GraphQL 实时核验、有限并发、候选不足自动扩展，并在连续搜索间轮换仓库批次 |
| 防止撞车和重复劳动 | hive-mind + 实际误报经验 | 检查时间线、仓库开放 PR、近期 Commit、自然语言认领和已解决留言；发现信号即跳过 |
| 可解释筛选 | IssuePilot 差异化 | 展示适合原因、风险原因、Issue 年龄与长期积压的启发式解释 |
| 本地质量门槛 | Sweep + aixgo | 代理运行相关测试；限制 diff、敏感文件、Git hooks 和历史改写 |
| 隔离执行 | SWE-agent + OpenHands + Jiffy | 不可信项目、受限沙箱、无 GitHub 凭证、默认无网络写入 |
| 人工发布门槛 | aixgo + GitAuto + Arbiter | 默认只准备；发布锁 + 单次确认；仅创建 Draft PR |
| 可恢复任务 | Jiffy + hive-mind | 工作区和 manifest 分离，失败后可以检查、重试或可恢复归档 |

## 明确不照搬的做法

- 不以“全自动大量发 PR”作为成功指标。候选质量和维护者接受度比 PR 数量重要。
- 不把 GitHub 登录等同于发布授权。
- 不在首次版本中自动循环远程 CI 和 Review 推送，避免无限费用、噪声和越权。
- 不把启发式解释包装成确定事实。前端明确把长期未解决原因作为辅助判断，最终仍需阅读原讨论。
- 不为了兼容更多模型而复制大型代理框架；优先保持编排层简单、可审计。

## 后续优先级

1. 在准备结果中显示完整 diff、测试日志摘要和风险，减少用户跳到终端审核的成本。
2. 增加仓库贡献规则检查：是否要求先留言认领、是否拒绝 AI 生成内容、是否有指定测试命令。
3. 增加受控的 Review 续跑：只针对同一个 Draft PR、同一工作区和明确的人类反馈。
4. 记录候选被跳过的原因和最终 PR 是否合并，用真实数据校准排序，而不是盲目调分。
5. 执行后端接口化，让 Codex 保持默认，同时允许未来接入 SWE-agent/OpenHands。
