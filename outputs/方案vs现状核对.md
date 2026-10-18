# 方案 vs 现状 核对报告

> 对象：共享方案《搭建本地数学题库的智能体方案》（20 条规划对话）
> 现状：`math-question-bank-main` 项目（端口 27182，FastAPI + SQLite + 纯前端）
> 核对方式：读代码实证（非凭记忆）
> 生成时间：2026-09-26

## 一、方案核心结论回顾

| # | 方案结论 | 现状状态 |
|---|---|---|
| 1 | 高中数学、教学讲义、跨库随机抽题组卷 | ✅ 已实现 |
| 2 | 载体用 SQLite 单文件库（约 40 万题量级） | ✅ 已实现（含索引） |
| 3 | Web 前端（用户点界面刷题/出卷） | ⚠️ 偏差（自研零部署，非"现成开源"） |
| 4 | PDF 为主 / Word 为辅 自动录入 | ✅ 已实现 |
| 5 | WorkBuddy 自主录题（不手动一条条录） | ✅ 已实现（MinerU+AI 自动拆题） |
| 6 | 跨库按知识点/难度**随机**抽题组卷 | ⚠️ 部分实现（能筛选+手动组卷，缺一键随机抽 N 道） |
| 7 | 出讲义 / 组卷 XeLaTeX 排版导出 | ✅ 已实现 |
| 8 | 查重（去重） | ✅ 已实现（含 simhash 近重复） |
| 9 | 选择题/填空/括号归一化（choices/`\fillin`/`\paren`） | ✅ 已实现 |
| 10 | 双操作台：A 对话 + B Web 前端 | ⚠️ 部分实现（Web 完整；对话驱动无专门接线） |
| 11 | 调用 Scale / 无人值守自动录题 | ❌ 缺失（仅手动上传触发） |

---

## 二、逐项核对

### 1. 载体：SQLite 单文件库 ✅
- `mathbank/database.py`：题目表 `questions` 含 `question_type / category_compulsory / category_chapter / category_knowledge / difficulty / topic_id / exam_year / exam_region` 等字段，全部建了 `index=True`。
- 启动时有手动建索引逻辑（`idx_questions_category_knowledge`、`idx_questions_difficulty`、`idx_questions_question_type`、`idx_questions_tags` 等），并为 `question_curriculums` 建复合索引——**知识点/难度筛选有索引支撑，40 万题量级下跨库过滤可行**。
- `db_migrations.py` 提供版本化、备份优先的迁移。

### 2. Web 前端 ⚠️ 偏差（实现更好）
- 方案原话"现成 Web 前端"，实际是 **WorkBuddy 自研的零部署纯前端**：`static/js/{api,editor,paper,import,filter-panel,ocr,lesson}.js` + KaTeX 实时渲染 + Tailwind 本地化。
- 评价：比"装一个开源题库系统"更贴合"不写代码"诉求（WorkBuddy 代写，你零代码），且无外部依赖、离线可用。属**正向偏差**。

### 3. PDF/Word 自动录入 ✅
- `mathbank/parsing_pipeline.py`：统一承载整卷后处理 + AI 拆题，源文支持 **MinerU markdown / Word markdown / TeX 源码**。
- `mathbank/omml_helper.py`：Word 公式（OMML）转 LaTeX，印证 **Word 试卷为辅**已落地。
- `mathbank/mineru_cache.py`：按内容哈希整包缓存，命中即零云端请求。

### 4. WorkBuddy 自主录题 ✅（对话遗留问题的实际答案）
- 用户原问"你可以自己去调用 Scale 呗，去录题嘛"——**不需要接外部标注服务**。当前流水线：上传 PDF/Word → MinerU 云端解析 → AI 拆题 → 归一化 → 入库，全程自动化，你只做最后审核。
- 这比"调用 Scale 外包"更省、更可控。

### 5. 跨库抽题 + 随机组卷 ⚠️ 部分实现
- ✅ 跨库筛选：前端 `filter-panel.js` + 后端索引，可按知识点/难度/题型/来源/年份等条件过滤（满足"跨全库按知识点/难度抽题"）。
- ✅ 手动组卷：`paper.js` 组卷工作台，勾选题目→生成试卷→XeLaTeX 导出。
- ❌ **一键"随机抽 N 道满足条件题"未实现**：全代码库检索 `ORDER BY RANDOM` / `random()` / `shuffle` / `随机抽`，仅在 `import.js`/`lesson.js` 里用于生成 token/随机金句，**没有任何"按条件随机抽题"逻辑**。当前只能先筛出集合、再人工勾选。

### 6. 出讲义 / XeLaTeX 导出 ✅
- `mathbank/paper_helper.py` 多处使用 **XeLaTeX** 编译（含中文宏包、TikZ 函数图渲染、`choices` 环境、圈号分级缩进、留白调控）。
- `lecture_helper.py` + `lesson.js`：支持教案/讲义编辑与导出，比方案仅提"出讲义"更进一步。

### 7. 查重 ✅（超出方案预期）
- `mathbank/question_duplicates.py` + `question_duplicate_service.py`：保守型确定性查重，含 **simhash 近重复**相似度计算、指纹索引、分批扫描。
- 接口：`/api/questions/check-duplicates`、`/api/questions/scan-duplicates`；保存时强制查重快照比对，重复则要求人工确认。
- 方案只说"去重"，实际做了精确+近似双轨。

### 8. 题型归一化 ✅
- `mathbank/latex_normalize.py`：选择题 → `choices` 环境、填空 → `\fillin`、括号 → `\paren`，统一为入库唯一归一化链（`normalize_for_storage`），多录入路径汇合、幂等。与 AGENTS.md 规范一致。

### 9. 双操作台 ⚠️ 部分实现
- ✅ B 操作台（Web 前端）：完整可用。
- ⚠️ A 操作台（对话驱动）：后端 API 齐全，但**没有把"对话→录入/组卷/出讲义"封装成可一键调用的智能体技能/工作流**。目前我（WorkBuddy）可以直接调 API 干活，但没有固化成"说一句就办"的入口。

### 10. 无人值守自动录题 ❌ 缺失
- 全代码库检索 `watch / folder / monitor / scale / 调度 / 定时扫描`，**无任何文件夹监控或定时扫描入库逻辑**。
- 即：录题必须你手动上传触发，没有"丢进文件夹就自动解析入库"的能力。这正是规划对话**最后一句没答完的问题**的落点。

---

## 三、待补清单（按优先级）

| 优先级 | 缺口 | 说明 |
|---|---|---|
| 🔴 高 | 一键随机抽题组卷 | 加一个 `/api/questions/random-sample`（按条件 + LIMIT + `ORDER BY RANDOM`），前端加"随机抽 N 道"按钮 |
| 🔴 高 | 无人值守录题 | 加文件夹监控自动化（或直接用 WorkBuddy automation 定时扫描目录→调上传接口），实现"丢文件即入库" |
| 🟡 中 | 对话操作台封装 | 把录入/组卷/出讲义封装成可复用 skill 或工作流，让"A 对话操作台"真正开箱即用 |
| 🟢 低 | 40 万题压测 | 现有索引足够，但建议灌入大批量数据做一次真实查询延迟/编译并发验证 |

---

## 四、结论

方案 11 项核心结论中，**7 项已完整实现，2 项正向偏差/部分实现，2 项待补**。你最关心的两件事——

- **"不想手动录题、交给 WorkBuddy"**：✅ 已满足（MinerU+AI 自动拆题，比接 Scale 更省）。
- **"既出讲义、又跨库随机抽题组卷"**：讲义✅、跨库筛选✅、但**随机抽题**和**无人值守录题**这两个自动化收尾项还没做，正是规划对话断在最后一句的遗留。

下一步若要补，建议从 🔴 两项（随机抽题 + 无人值守录题）入手，约 1–2 天可落地。
