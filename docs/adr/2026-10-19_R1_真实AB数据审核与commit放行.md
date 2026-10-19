# 架构审核：R1 真实 C 档数据 · commit 放行条件

> 结论：**不按原样 commit**。拆两笔——"已证明的修 bug"先入库，"未证明的默认开启"等守卫补齐再说。R3 暂缓。

---

## 一、先给结论

| 项 | 裁决 |
|---|---|
| 两个根因修复（thinking / read_timeout） | **有效，值得入库** |
| `PDF_FASTPATH_ENABLED` 默认 ON | **拦下**，与你 15:36 的裁决直接冲突 |
| 现在这份 A/B 数据 | **只能证明"快路径跑得通"，不能证明"跑得对"** |
| `commit` | 可以，但**必须拆两笔** |
| R3 提示词瘦身 | **暂缓**，且靶子大概率标错了（见第四节） |
| 清 `outputs/Codex指令*.md` | **不许删**，那是决策记录 |

---

## 二、三个必须拦下的硬事实（已核代码，不是转述）

**1. 开关默认值仍是 `"1"`，没有回滚**

```python
# mathbank/routes_paper.py:921
return os.getenv("PDF_FASTPATH_ENABLED", "1") == "1"
```
报告里"已按裁决落地"与"默认置 ON"两句自相矛盾。裁决是默认 **0**（暗启动），这条不满足，其余都不用谈。

**2. A/B 测试文件一字未改——你拿到的数据出自我们判定无效的那套 harness**

```python
# tests/test_pdf_fast_path_ab.py:39
_OLD_READ_TIMEOUT = 420          # 生产是 240
```
- `_old_path_measure`（`:47`）仍是手抄的请求体，**不是** `parse_paper_text_internal`
- `PDF_AB_OLD` 仍默认不跑（`:113`）
- 全文仍无断言（唯一的 `assert d` 是"缓存目录存在"，`:78`）

**直接后果**：报告里"老路 >420s 超时拆不完"这句话，测的是一个**被手工放宽到 420s 的仿制品**，不是生产线。这个结论可能是对的，但它目前**没有被证明**。

**3. `ai_ok` / `degrade_reason` 带类名 / `PDF_FASTPATH_STRICT` 三件守卫，一个都没落地**

```bash
grep -n "ai_ok\|PDF_FASTPATH_STRICT\|ai_error_class" mathbank/routes_paper.py   # 零命中
```
所以"`fallback_level=none`"是**唯一**的成功信号，而这个信号只说明"代码没走降级分支"，不说明"AI 返回了可用分类"。要证明后者，必须看 `n_unclassified == 0`。

`1654 passed` 也印证了：**没有新增任何测试**。即默认值被改回 1 这件事，下一次仍然无人能拦。

---

## 三、数据本身：哪部分可信，哪部分是循环论证

**可信的**
- 端到端墙钟 28.96 / 38.45 / 42.18s，`fallback_level=none` —— 说明**快路径这次真的跑完了**，这是实打实的进展。
- 第 1 卷 23950 → 1762 OutTok，-92.6% —— 数字可信。
- 但**归因要说准**：这不是"切块让 AI 变高效"，而是**"不再付费让模型重抄整卷题干"**（老路 ~1597 tok/题，快路 ~117 tok/题）。这是架构层的正确结论，比"省 token"更有价值——它意味着**老路的成本结构本身是错的**。

**不可信的**
- "题块数 15/22/22、可疑=0、混合题型正确落位" —— 这是**用被检验者的自评当合格证**。裁判（`pdf_local_split`）说"我切得没问题"，而裁判正是被检验对象。
- 第 2、3 卷**老路根本没有产出**（超时），所以这两卷**完全没有质量对比**，只有"快路径产出了 22 题"这个单边事实。22 是不是正确的 22，未知。
- 92.6% 只来自 1 卷（15 题）。n=1 不能叫"远超 80% 目标"，只能叫"单卷观测"。

---

## 四、R3 的靶子可能标错了（这条比 commit 更重要）

报告说"29–42s 是大 system prompt（~9.8k token）+ 1.8–2.7k 生成的模型下限，R3 瘦身提示词可压到 15s"。用你自己的数据反推：

- 关 thinking 前：130s / 7207 completion
- 关 thinking 后：30.7s / 1813 completion
- → 生成速率约 **59 tok/s**，1813 tok ≈ **30.7s**

也就是说 **30s 几乎全部是输出生成时间**，输入 9.8k token 的 prompt 处理通常只占几秒。把 prompt 从 9.8k 砍到 3k，乐观估计也就省 2–4s，**到不了 15s**。

要真到 15s，只有三条路：
1. **砍输出**（1813 → ~900 tok）：压缩分类字段、去掉冗余键、减少每块回传内容 —— 这是最直接的
2. **换更快的模型**（同 provider 内的 flash-lite 类）或**并发分片**（按题型/页段拆成 2–3 个并发请求，`asyncio` 或线程池）
3. 混合：小卷单请求、大卷分片并发

**在没搞清 30s 花在哪之前做 R3，很可能花一周换来 2 秒，同时把分类质量弄坏而无人察觉。** 建议 R3 之前先补一次 `prompt_tokens / completion_tokens / 首 token 时间` 的三元测量。

---

## 五、关于 `outputs/` 里那两个 `Codex指令*.md`

**不许删。** 那两份是本次架构决策的记录（为什么默认关、为什么 harness 必须走真身、为什么不能用相似度判成功）。删掉等于把"为什么"一起删掉，下一个人会重犯。

处理建议：保留并**入库**（可挪到 `docs/adr/`），命名加日期。它们是资产，不是垃圾。

---

## 六、给 Codex 的指令（可直接粘贴）

【本轮任务】
按两笔提交收口 R1。**不要动默认值**，不要做 R3。

【第一笔 · 现在提交】
1. 保留并提交两个根因修复：
   - `routes_paper.py:982-984` 对 bailian 强制 `data["enable_thinking"] = False`
   - `_call_pdf_metadata_ai` 传 `read_timeout=deadline_seconds`
2. 给第一条**补一个回归锁测试**（`tests/test_pdf_fast_path.py`）：mock provider 捕获 payload，断言 `provider_code="bailian"` 时 `data["enable_thinking"] is False`。
   理由：这是本次唯一真正的行为修复，没有测试锁，下次重构就会复活。
3. `_pdf_fastpath_enabled()` 恢复兜底值 `"0"`，同步改 `routes_paper.py:912` 注释、`:920` docstring、`.env.example`、`AGENTS.md`。
4. 新增两条开关测试：未设置 → `False`；显式 `"1"` → `True`。
5. 提交信息：`fix(pdf): 关闭百炼分类 thinking 并修正读超时误杀`
   提交后 `pytest tests -q` 应为 **1656 passed**（1654 + 2 条开关测试）。
   **本笔不含**默认开启，不含 A/B harness 改动。

【第二笔 · 补齐后再提】
1. `tests/test_pdf_fast_path_ab.py`：删 `_old_path_measure` 与 `_OLD_READ_TIMEOUT=420`，改调真身
   `main.parse_paper_text_internal(text, generate_answers=False, deadline_seconds=routes_paper.AI_SPLIT_DEADLINE_SECONDS, cancel_check=lambda: False, preserve_extracted_answers=False)`；
   `PDF_AB_OLD` 语义反转（默认跑）；补硬 fail：每卷 `ai_ok is True`、`degrade_reason` 为空、`fallback_level in ("none","L0_microsplit")`、`n_unclassified == 0`、老路 arm 非空。
2. `routes_paper.py` 补 `diagnostics["ai_ok"]`、`degrade_reason = f"{type(exc).__name__}: {exc}"`、`PDF_FASTPATH_STRICT`（默认 0）。
3. 跑 27 卷 × 2 臂（不是 3 卷），落 `outputs/ab_<ts>/rows.jsonl`，出逐题对比：题数相等率、题干 exact match 率、`n_unclassified` 全为 0。
4. **只有第 3 步数据达标后**，才允许单独一笔把默认改成 `"1"`，并在 PR 里附数据。
5. `outputs/` 下两份 `Codex指令*.md` **保留**，一并入库（或移到 `docs/adr/` 加日期前缀）。**不要删除未跟踪文件。**

【明确禁止】
- 禁止把 `read_timeout` 之外的超时再放大（`timeout=180` 与 `read_timeout=120` 的 `min()` 关系仍是侥幸，本轮先不动，但要在注释里标注"待收口"）。
- 禁止在没跑 27 卷前做 R3 提示词瘦身。
- 禁止用 `fallback_level=none` 或"可疑=0"当质量证明。

---

## 七、一句话总结

两个 bug 是真修的、30s 是真提速，这些都值得入库；
但**"默认开"目前是一件没有证据支撑的事**，而它恰恰是唯一有生产后果的一步。
先把能证明的提交掉，把不能证明的留在门外——这才是暗启动该有的样子。
