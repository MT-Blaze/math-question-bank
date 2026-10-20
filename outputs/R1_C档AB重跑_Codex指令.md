# R1「C 档 A/B 重跑」——给 Codex 的指令包

> 签发：架构师（2026-10-19）｜对象：Codex｜目标：拿到可信的 27 卷 A/B 数据，再决定是否开默认
> **先别急着跑。** 现在的 A/B 跑出来的数一定是"好看但假"的，原因不在 30s。

---

## 0. 结论先行

`read_timeout=30` 只是**触发器**，不是根因。若只修超时就去重跑，你会得到一轮"看起来通过"的数据，因为：

| 层 | 事实 | 后果 |
|---|---|---|
| 机制层 | `ai_http.py:131` 用单一 scalar `timeout` 同时表达 connect 与 read，且 `min()` 语义**只能收紧不能放宽** | 修复靠 `180 > 120` 侥幸成立，下一次调小 `timeout` 就静默失效 |
| 结构层 | `_run_fast_path` 用 `except Exception` 把所有 AI 异常兜成"可用返回值" | **降级 = 成功**，任务状态、UI、调用方全都认为没问题 |
| 度量层 | `tests/test_pdf_fast_path_ab.py` **零个 assert**，只 `print` | 100% 降级也不会让任何东西变红 |
| 基线层 | 老路 arm 是**手工镜像**的请求体，且 `PDF_AB_OLD != 1` 时根本不跑 | 对照可能不存在；存在的那个也不是生产线 |
| 覆盖层 | A 档只跑 `pdf_local_split`，完全不碰 AI 腿 | 基础设施类回归天生测不出来 |

**所以重跑分三步：P0 改代码让失败可见 → P1 修 harness 让它会红 → P2 才跑数。**

---

## 1. 三处必须先对齐的事实（跑之前确认，别猜）

### 1.1 开关默认值：报告与代码互相打脸

- 报告：`PDF_FASTPATH_ENABLED` **默认 0（暗启动）**
- 代码 `routes_paper.py:921`：`return os.getenv("PDF_FASTPATH_ENABLED", "1") == "1"` ← **默认开**
- 同文件 `:912` 注释：`本地切块极速路径默认开启`；`:920` docstring：`C 档 A/B 达标后默认开`

三处说法不一致。请 Codex 在 PR 描述里**一句话确认事实**，并统一注释。
A/B 期间**一律显式传值**，绝不依赖默认值——否则"对照组"可能悄悄不存在。

### 1.2 `read_timeout` 只能收紧，不能放宽（隐形耦合）

```python
# mathbank/ai_http.py:131
kwargs["timeout"] = min(requested, float(read_timeout) if read_timeout else 30.0)
```

`_call_pdf_metadata_ai` 现在传 `timeout=180, read_timeout=deadline_seconds(120)` → 生效 120。
**这个修复之所以成立，纯粹因为 180 > 120。** 把 `timeout=180` 改成 90，修复立刻回退成 90，且无任何测试报警。

### 1.3 同一处修改把 connect 超时从 30s 放大到 120s（新引入）

`timeout` 是 scalar，同时作用于 connect 与 read：

- 修复前：`min(180, 30)` = 30 → connect 30s、read 30s
- 修复后：`min(180, 120)` = 120 → connect 120s、read 120s

而 `robust_request_post`（`ai_http.py:37-49`）对 `ConnectTimeout` 会**无代理重试一次** → 最坏 240s。
且 `_post_bounded` 的 deadline 只在 chunk 循环里检查（`:141`），**连接阶段完全不受 deadline 约束**。
`:116-121` docstring 承诺 "guarantees the call always comes back" 不成立。

### 1.4 附带发现（低优先级，但别留）

`pdf_local_split.plan_pdf_local_split(source_text, page_texts=...)`（`:203`）接受 `page_texts`，**全文从未使用**。
说明还有一道基于页级的闸门没落地。要么接上，要么删参数——签名不该撒谎。

---

## 2. P0：三处代码改动（不改这三处，数据不可信）

### P0-1 拆分超时语义：connect / read / deadline 三个独立量

**背景**
`_post_bounded` 目前用一个 scalar 同时表达 connect 与 read，`min()` 让 `read_timeout` 永远无法超过 `timeout`，两个概念互相污染；且 connect 阶段不受 deadline 约束。R1 的 30s 事故就是这套语义的直接产物。

**改前原文**（`mathbank/ai_http.py:128-135`）
```python
    kwargs = dict(request_kwargs)
    kwargs["stream"] = True
    requested = float(kwargs.get("timeout") or 60)
    kwargs["timeout"] = min(requested, float(read_timeout) if read_timeout else 30.0)
    started = time.monotonic()
    deadline = started + deadline_seconds if deadline_seconds else None

    response = robust_request_post(url, **kwargs)
```

**改后目标**（行为契约，非实现）
1. 新增模块级常量：`DEFAULT_CONNECT_TIMEOUT_SECONDS = 30.0`、`DEFAULT_READ_TIMEOUT_SECONDS = 30.0`。
2. `kwargs["timeout"]` 一律归一化为 `(connect, read)` 二元组：
   - `connect = min(调用方给的 connect 或 60, DEFAULT_CONNECT_TIMEOUT_SECONDS, 剩余 deadline)`
   - `read    = min(调用方给的 read 或 60, read_timeout if read_timeout else DEFAULT_READ_TIMEOUT_SECONDS)`
3. `read_timeout` 从此**可以放宽**（不再被 `timeout` 天花板压死）；connect 不会被 `read_timeout` 放大。
4. 保留 `robust_request_post` 现有的"仅 ProxyError/ConnectTimeout 重试一次"语义，不动。

**严格边界**
- 现有所有 scalar `timeout=` 调用者：connect 上限从"等于 timeout"收紧到 30s。这是**行为变化**，必须在 PR 描述里显式列出，并说明为什么是改善（快速失败 + 可重试）。
- 不得在 `_post_bounded` 内新增任何业务判断（不认 provider、不认 task 类型）。
- 不得改 `post_chat_completion` 的公开签名；`stream=True` 分支（`:187-192`）不受影响。
- 全量 `pytest tests/ -q` 必须仍然 1654 passed。

### P0-2 让"降级"变成可判红的事实

**背景**
AI 失败被 `except Exception` 兜成可用返回值，唯一痕迹是 `diagnostics["degrade_reason"]` 里 200 字符的 `str(exc)`（`ReadTimeout(...)` 的字符串里根本没有异常类名）。任何自动化都无法区分"正常降级"与"基础设施全挂"。

**改前原文**（`mathbank/routes_paper.py:1033-1036`）
```python
    except (TaskCancelled,):
        raise
    except Exception as exc:  # 超时/网络/坏JSON/条数不符 → L2 保正文降级
        return _degrade_clean_only(plan, exc, diagnostics)
```
以及 `:1013-1015` 的 `_degrade_clean_only`。

**改后目标**
1. 成功路径写入 `diagnostics["ai_ok"] = True`；降级路径写 `False`。
2. `degrade_reason` 改为 `f"{type(exc).__name__}: {exc}"[:200]`（**类名必须进字符串**）。
3. 新增 `diagnostics["ai_error_class"] = type(exc).__name__`。
4. 新增开关 `PDF_FASTPATH_STRICT`（默认 `0`）：置 `1` 时，`_run_fast_path` 的 AI 异常**直接向上抛**，不做 L2 降级。

**严格边界**
- `PDF_FASTPATH_STRICT` **只**作用于 `_run_fast_path` 的 AI 异常分支。
- **不得**让它影响 L1 整卷回退（`plan.trustworthy=False` 是切块器判定，是合法路径，不是失败）。
- `TaskCancelled` 仍然原样 `raise`，strict 与否都不许吞。
- 生产默认保持 `0`，全量测试不得因新增开关改变行为。

### P0-3 让 A/B 超时预算对齐生产线

**背景**
快路径 `deadline=120s`，老路 `AI_SPLIT_READ_TIMEOUT_SECONDS=240s` / `AI_SPLIT_DEADLINE_SECONDS=480s`。
快路径天生比老路更容易超时。一旦有 2-3 卷落在 120~240s 区间，你会得出"快路径更差"的**假结论**——那是超时策略的锅，不是切块的锅。

**改后目标**
A/B 期间把快路径预算**显式对齐**老路：`PDF_METADATA_DEADLINE_SECONDS = 240`，`read_timeout = 240`。
测完拿到真实 `ai_ms` 分布后，再**单独一次**提交把它收紧到 p99 之上的值（附分布数据），不要和 A/B 混在一起。

**严格边界**
- 这只是 A/B 期的临时对齐，PR 里必须写明"R1 定稿时会按实测分布收紧"。
- 不许借机调整 `AI_SPLIT_DEADLINE_SECONDS` 或老路任何预算。

---

## 3. P1：修 harness 的五个硬伤（`tests/test_pdf_fast_path_ab.py`）

| # | 硬伤 | 现状 | 改后要求 |
|---|---|---|---|
| 1 | 零断言 | `test_pdf_fast_path_ab` 全篇只 `print` | 必须带 fail 条件（见下） |
| 2 | 老路是镜像复制 | `_old_path_measure` 手抄请求体 | 必须调**真身** `main.parse_paper_text_internal(...)` |
| 3 | 老路默认不跑 | `PDF_AB_OLD=1` 才跑 | 默认跑；要省额度显式 `PDF_AB_OLD=0` |
| 4 | 只测 3 卷 | `PICKS` 三条 | 默认 A 档同一批全 27 卷，保留 `--picks` 做 smoke |
| 5 | 基线超时失真 | 老路读超时 420s ≠ 生产 240s | 用生产常量，不许私自放宽 |

**为什么必须用真身**
镜像已经漂移了：420s vs 生产 240s、缺 `preserve_extracted_answers=bool(split_info)`、缺页标清理与 `normalize_for_storage`。用它当基线，比的是"两套手写实现的差异"，不是"两条流水线的差异"。

**必须 fail 的条件（任一命中即非零退出）**
1. `PDF_FASTPATH_STRICT=1` 下任何 AI 异常被抛出（strict 模式的意义就在此）
2. 任一行 `degrade_reason` 含 `Timeout` / `ReadTimeout`
3. 任一行 `fallback_level == "L1_fullpaper_exception"`
4. fast 臂 `n_unclassified > 0` 或出现 `review` 标记
5. 老路 arm 缺失（除非显式 `PDF_AB_OLD=0`）

**报告头必须记录**（否则数据不可复现）
`git commit`、时间戳、`PDF_AB_RUN`、provider_code、model_name、api_base 域名（不含 key）、
`AI_SPLIT_READ_TIMEOUT_SECONDS`、`PDF_METADATA_DEADLINE_SECONDS`、卷数。

**输出**
`outputs/ab_<timestamp>/rows.jsonl`，一行一卷一臂，字段固定：

```
paper_id, arm(legacy|fast), ok, wall_ms, ai_ms, ai_ok, ai_error_class,
ai_usage{prompt_tokens,completion_tokens,total_tokens},
fallback_level, degrade_reason, n_questions, q_types{},
n_images, q_fingerprints[sha1(...)], n_unclassified, review_tagged
```

---

## 4. 判据：五组指标 + 阈值

### D1 正文保真（可自动判，硬指标）
- 题数逐卷完全相等：**27/27**
- 题干 `normalize_for_storage` 后 exact match：**≥ 99%**
- `answer_markdown` exact match：**≥ 99%**
- `referenced_images` 数量相等：**27/27**

### D2 分类完整（不可自动等值，只判"不空"）
- `n_unclassified == 0`（27 卷全部）
- `review_tagged == False`
- `question_type` 分布与老路差 ≤ 2 卷（记录，不强判）

### D3 成本
- `completion_tokens` 降幅、`prompt_tokens` 降幅（快路 `max_tokens=8192` vs 老路 65536；clean 块只送 150 字摘录）

### D4 时延
- `ai_ms` p50 / p95 / max（每臂）
- `wall_ms` p50 / p95（每臂）
- 纯切块耗时沿用 A 档：p50 3.8ms / p95 6.1ms

### D5 稳定性
- fast 臂连跑 **2 轮**，两轮 `ai_ok_rate` 都必须 100%，`degrade` 次数都必须 0

### ⚠️ 一个会骗人的陷阱，必须写进报告结论
**不要用题干相似度当"快路径成功"的唯一证据。**
clean 块正文是本地原样保留的（设计使然），AI 只拿到 150 字摘录做分类。
所以"正文 100% 一致 + 分类全空"是完全可能的组合——它会显示成漂亮的相似度，实际是 100% 降级。
**真正的证据是 `ai_ok == True` 且 `n_unclassified == 0`。**

---

## 5. 执行顺序（三步，别一把梭）

**Step 1｜3 卷 smoke（目的：验 120s 够不够）**
`PDF_AB_RUN=1 PDF_AB_OLD=1 PDF_FASTPATH_STRICT=1`，只跑 `PICKS` 三条。
通过条件：3/3 `ai_ok`、0 degrade、打印出真实 `ai_ms`。
**若任一卷 `ai_ms > 90s` → 停，先回 P0-3 调预算，别继续。**

**Step 2｜27 卷 × 2 臂 × 1 轮**
`PDF_AB_RUN=1 PDF_AB_OLD=1 PDF_FASTPATH_STRICT=1`。
产出 `rows.jsonl` + 五组指标汇总表。这是主数据。

**Step 3｜fast 臂第 2 轮（只验稳定性）**
`PDF_AB_RUN=1 PDF_AB_OLD=0 PDF_FASTPATH_STRICT=1`。
对比两轮 degrade 次数与 `ai_ms` 抖动。

---

## 6. 交付物清单（跑完一起给我）

1. `rows.jsonl`（54 行：27 卷 × 2 臂）
2. 一张汇总表：D1~D5 五组指标，逐项打勾/打叉
3. **3 张不信任卷的清单**（A 档 24/27 之外那 3 卷）及各自回退原因
   —— 它们在 fast 臂里本来就走 L1，是**天然对照组**，请顺带验证"L1 回退 ≡ 老路"（同一份源码、同一次调用，逐字相等）
4. `ai_ms` 分布图（或 p50/p95/max 三个数），用它决定 R1 定稿的 deadline
5. 一句明确结论：**默认开关能开 / 不能开**，以及理由
