# Codex 终端接管 · 环境探测报告

探测时间：2026-10-19 15:47｜执行者：全自动终端执行代理

---

## 一、结论

**可以用终端驱动 Codex，链路已实测贯通（读取 + 写入均验证通过）。**

但有一个必须先讲的前提：**它不是 OpenAI 的账号体系，而是接了百炼中转**，所以行为特征和"官方 Codex"不同，成本也不低（见第四节）。

---

## 二、可执行文件位置

`codex` **没有**注册进 PATH，`where codex` 零结果。真实路径：

```
C:\Users\15972\AppData\Local\OpenAI\Codex\bin\896a0166b774037a\codex.exe
```

同目录另有三个内部程序，**不要当 CLI 用**：

| 文件 | 用途 |
|---|---|
| `codex-command-runner.exe` | 内部管道程序，直接跑会报 `runner: no pipe-in provided` |
| `codex-code-mode-host.exe` | 代码模式宿主 |
| `codex-windows-sandbox-setup.exe` | 沙箱安装器 |

另有 `~/.codex/.sandbox-bin\codex-command-runner-*.exe` —— 同样是内部组件，不是入口。

当前有 `codex.exe`（PID 15888）在跑，命令行是 `app-server`，说明**桌面 App 正在使用同一个 CLI 二进制**。这意味着：如果我在终端发起任务，它会与 App 共享 `~/.codex` 下的 session/queue 状态，可能互相干扰（见第五节）。

---

## 三、调用语法（已实测）

核心是 `exec` 子命令（非交互模式）：

```bash
"/c/Users/15972/AppData/Local/OpenAI/Codex/bin/896a0166b774037a/codex.exe" \
  exec -s workspace-write -C "E:/math-question-bank-main" "<prompt>" < /dev/null
```

**四个必须注意的点：**

1. **`< /dev/null` 不能省。** 不加会把 stdin 当 prompt 读，命令直接挂住。实测日志里那句 `Reading additional input from stdin...` 就是它想读 stdin。
2. **`-s workspace-write`** 才有写权限；`read-only` 只能读（我的第一次测试用的就是 read-only）。可选值：`read-only | workspace-write | danger-full-access`。
3. **`-C <dir>`** 指定工作根目录，等价于 `--cd`。
4. 长任务会被环境 40 分钟判 failed，但进程可能仍存活——**必须用 `-o <FILE>` 把结果落盘**，不要靠 stdout 抓。

**自动化取结果的三个开关：**

| 开关 | 用途 |
|---|---|
| `-o, --output-last-message <FILE>` | 把 Codex 的末条消息写入文件 —— **自动化首选** |
| `--json` | stdout 输出 JSONL 事件流，可程序化解析 |
| `--output-schema <FILE>` | 用 JSON Schema 约束返回结构 |

**退出码**：成功 0。失败/异常的具体语义需再测。

---

## 四、当前配置与实测开销

`~/.codex/config.toml` 关键项：

```toml
model_provider = "custom"
model = "qwen3.8-flash"
model_reasoning_effort = "high"

[model_providers.custom]
name = "bailian"
base_url = "https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1"
wire_api = "responses"
experimental_bearer_token = "sk-sp-..."   # 内联明文
```

**这意味着：**

- `codex login status` 返回 **Not logged in**，但 `exec` 照常工作——它根本不走 OpenAI 账号，走的是百炼。所以"未登录"不是问题，**不要试图去 login**。
- 启动会告警：`Model metadata for qwen3.8-flash not found. Defaulting to fallback metadata; this can degrade performance`。这是正常的，因为百炼模型不在 OpenAI 的元数据表里。
- 另有两项被忽略的配置告警：`disable_response_storage`、`mcp_servers.cua_repl.type`（无害，但说明配置有陈旧项）。

**实测 token 消耗（重要）**

| 任务 | 消耗 |
|---|---|
| 一句话回复"链路正常" | **18,365 tokens** |
| 写一个单行文件 | **9,936 tokens** |

**每轮都自带 ~10k~18k 的固定开销**（system prompt + 环境快照 + AGENTS.md 注入）。这意味着：
- 全自动循环跑任务时，成本要按"每轮至少 1 万 token 起"估。
- 让我在同一个 session 里连续下发多步（用 `codex exec resume`），比每步新开 session 省钱。

⚠️ 附带发现：`config.toml` 里有**明文 bearer token**。它在用户目录下、且 `.codex` 不是 git 仓库，所以没有外泄风险；但如果将来要备份或分享配置，这是敏感项。

---

## 五、两个必须提前说清的风险

**风险 1：桌面 App 与 CLI 会抢同一状态**

`codex.exe` 已在跑 `app-server`（PID 15888），且 `~/.codex` 下有 `queue_1.sqlite`、`thread-writer-locks`、`sessions/`、`sqlite` 等共享状态。我通过 CLI 发起的 session 会写进同一套存储。**如果我发起的任务正好和你 App 里打开的那个会话撞上，可能出现锁冲突或上下文串台。**

建议：要么关掉 App 再用 CLI 接管，要么我给每次调用加 `--ephemeral`（不持久化 session）——但那样就不能 `resume` 续跑。

**风险 2：`model_reasoning_effort = "high"` + 18k 固定开销**

同一件事在官方 Codex 上可能几千 token，这里要 1.8 万。如果要做"全自动循环"，建议评估是否把 effort 降到 medium 来省成本——但**这会改变 Codex 的解题质量**，需要你决定。

---

## 六、关于第二步：我不建议照原样执行

你给的示例指令是：

> 请立刻 commit，message 写...，同时删除 outputs 里的遗留草稿

**这两件事我都不能照做，理由在上一轮审核里：**

1. **不能立刻 commit。** 当前代码状态是：
   - `routes_paper.py:921` 默认值仍是 `"1"`（你 15:36 的裁决是默认 `0`）
   - `tests/test_pdf_fast_path_ab.py` 一字未改（`_OLD_READ_TIMEOUT=420` 镜像仍在、零断言）
   - `ai_ok` / `PDF_FASTPATH_STRICT` 三件守卫零命中
   - `1654 passed` 说明**没有新增任何测试**
   
   现在提交，等于把"未证明的默认开启"一起固化进历史。

2. **不能删 outputs 里的草稿。** 那四份（不是两份）是本次的决策记录：为什么默认关、为什么 harness 必须走真身、为什么不能用相似度判成功。删掉等于把"为什么"一起删了。

**我建议的执行方案**（等你点头再发）：

- **第一笔 commit**：只含两个根因修复（thinking 关闭 + read_timeout）+ 给 `enable_thinking=False` 补回归锁 + 开关回滚为默认 0 + 两条开关测试。预期 `1656 passed`。
- **第二笔**：等守卫和 27 卷数据齐了再提。
- **outputs 四份 md**：**入库**（可挪到 `docs/adr/` 加日期前缀），不删。
- **R3**：暂缓，先做一次 `prompt_tokens / completion_tokens / 首 token 时间` 三元测量——按 59 tok/s 反推，30s 基本都是 1813 个 output token 的生成时间，砍输入 prompt 到不了 15s。

---

## 七、待你确认

1. 是否同意"拆两笔提交、outputs 不删"的方案？还是你坚持按原示例执行？
2. 接管期间**要不要先关掉 Codex 桌面 App**，避免会话状态互撞？
3. 成本敏感度：`reasoning_effort` 保持 `high`（贵、质量好），还是降到 medium？