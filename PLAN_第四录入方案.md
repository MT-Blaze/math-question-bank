# PLAN：移植望悠录入链路，作为「第四录入方案」

> **状态：待批准。截至目前未改动任何业务代码。**
> 参考资料：`E:\math-wangyou\question-bank\resources\app-next`（Next.js 生产构建，非源码）

---

## 一、为什么要做

我们现有三条录入路线（`native_preferred` / `force_ocr` / `mineru_cloud`）都是**面向单份试卷**的：传一个文件 → 识别 → 卡片预览 → 入库。

望悠那套是**面向批量/整册**的：多文件会话 → 先出计划 → 识别 → **人工风控** → 整批提交 → 可回滚。它补的是我们缺的「**批量 + 强质量控制 + 可追溯**」。

而且它**同时支持试卷**（关键词命中：试卷 67 / 大题 36 / 分值 12 / 答题卡 10 / 满分 7），不是只能录教材。

---

## 二、望悠流水线的六个阶段（已确认）

| 阶段 | 路由 | 契约 |
|---|---|---|
| 1 会话 | `POST batch/session` | 上传源文件，**单文件 300MB 上限**，建立批次会话 |
| 2 计划 | `POST batch/plan` | 生成导入计划（把源文件切成待识别项），可先预览"将要识别什么" |
| 3 识别 | `POST batch/recognize` | 逐项调用**视觉大模型**（vision）识别 |
| 4 风控 | `POST batch/risk` | `{acceptances:[{riskId, reason}], expectedRevision}` — **riskId 与 reason 都必须非空**才准入；`expectedRevision` 做乐观并发控制 |
| 5 提交 | `POST batch/commit` | 必须带 `previewHash`，校验预览未被改动，防陈旧提交 |
| 6 回滚 | `POST batch/rollback` | 带 `rollbackHash` 整批回滚；另有 `detail` / `list` 查看批次 |

另有 `/api/import/recognition-key` `recognition-status` `recognition-test`：识别服务密钥的配置与连通性自检。

---

## 三、可移植的原料（已归档）

- **470 条中文串 / LaTeX 模板** → `.system_generated/scratch/2026-09-20/wangyou_prompts.json`
- 其中真正有价值的三类：
  1. **视觉转录提示词**：扫描页 → LaTeX 正文（逐字转录，保留【例】【定理】【习题】等条目标记）
  2. **结构化提示词**：LaTeX → 题目条目（按【例】【题】【定理】【命题】【注】【习题】切分，输出 sections/kind）
  3. **公式纪律规则**（最有价值）：防止 `\\` 被渲染器当换行、花括号被当多余删掉等**静默错误**；违反则整批判失真退回
- 还有 `[[FIG:n]]` 插图占位符规则、以及试卷/答题卡的 LaTeX 版式模板

**限制**：望悠只有编译产物，逻辑靠反推，与原源码可能有出入；作者源码路径为 macOS 上的 `/Volumes/language/free号池/question-bank`，我们拿不到。

---

## 四、与我们现有能力的衔接（关键，避免重复造轮子）

| 望悠的阶段 | 我们的对接方式 |
|---|---|
| 批次 + 回滚 | **复用现有 `import_batches` 表**（已有 `token`/`name`/`question_ids`/`rolled_back`），不新建表 |
| 视觉识别 | **复用现有 provider**（`resolve_text_provider` / 视觉 provider），不新增供应商判断 |
| 入库 | **必须过 `normalize_for_storage` 归一化链**，保证与三条老路线源码字节一致 |
| 插图 | 沿用 `image_paths` + 望悠的 `[[FIG:n]]` 占位规则 |
| 查重 | risk 阶段并入现有 fingerprint 查重结果（与"跳过/独立/审查"三选衔接） |
| 卡片预览 | 复用现有卡片流，不另起一套 |

---

## 五、分阶段实施

- **Phase 0 · 原料归档** ✅ 已完成（470 条提取入库）
- **Phase 1 · 后端骨架**：新增 `mathbank/routes_import_batch.py`，落 6 个接口骨架 + 会话/计划的数据结构
- **Phase 2 · 识别接入**：接现有视觉 provider，按 plan 项逐项识别
- **Phase 3 · 风控闸门**：`riskId + reason` 强制校验 + `expectedRevision` 乐观并发
- **Phase 4 · 提交与回滚**：`previewHash` / `rollbackHash` 校验，整批入库与回滚（复用 import_batches）
- **Phase 5 · 提示词本地化**：把提取的提示词改成适配我们 LaTeX 归一化链的版本
- **Phase 6 · 前端**：录入页加"第四方案"入口 + 风控界面（风险项列表 + 逐项填理由）

每阶段结束跑一次全量 `pytest`（当前基线 1087 passed / 2 skipped）。

---

## 六、风险与待定

1. **规模大**：六阶段跨前后端，需分阶段验收，不宜一次做完。
2. **与现有卡片流的边界**：两套录入体验并存会让页面变复杂，需在 Phase 6 前定清入口形态。
3. **编译产物反推**：望悠的实现细节可能有偏差，Phase 2/5 需以实测结果为准。
4. **`expectedRevision` / `previewHash`**：需确认是否与现有 `duplicate_snapshot_hash` 机制冲突（两者都是防陈旧，可能可合并）。
