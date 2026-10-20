import datetime
import sqlite3
import json
import re
from pathlib import Path
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base, sessionmaker
from mathbank.paths import DATABASE_FILE, sqlite_url

# 题末可分离插图的尺寸枚举：与 mathbank/image_layout.SIZE_CM 一一对应。
# auto 表示沿用导出侧既有默认宽度，绝不允许把任意 CSS/TeX 写进库里。
FIGURE_SIZE_VALUES = frozenset({"auto", "small", "medium", "large"})
FIGURE_ALIGN_VALUES = frozenset({"right", "center", "bottom_right"})


def normalize_figure_size(value) -> str:
    """把外部传入的插图尺寸收敛到枚举，非法值一律退回 auto。"""

    normalized = str(value or "").strip()
    return normalized if normalized in FIGURE_SIZE_VALUES else "auto"


# SQLite Database URL
SQLALCHEMY_DATABASE_URL = sqlite_url(DATABASE_FILE)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):
    """Apply relational safety settings to every SQLite connection."""

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _utcnow_naive():
    """Return UTC without tzinfo for the existing SQLite DateTime columns."""

    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def configure_sqlite_wal(database_engine: Engine) -> str | None:
    """Prefer WAL, but keep a local single-user database usable with DELETE."""

    database_name = database_engine.url.database
    if not database_name or database_name == ":memory:":
        return None
    wal_error = None
    try:
        with database_engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            mode = str(
                connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
            ).lower()
            if mode == "wal":
                connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
                connection.exec_driver_sql("PRAGMA wal_autocheckpoint=1000")
            else:
                wal_error = f"journal_mode returned {mode}"
    except SQLAlchemyError as exc:
        mode = ""
        wal_error = f"{type(exc).__name__}: {exc}"

    if mode != "wal":
        try:
            with database_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                mode = str(
                    connection.exec_driver_sql("PRAGMA journal_mode=DELETE").scalar_one()
                ).lower()
                if mode != "delete":
                    raise RuntimeError(
                        f"SQLite 无法启用 WAL 或 DELETE 日志模式，当前模式: {mode}"
                    )
                connection.exec_driver_sql("PRAGMA synchronous=FULL")
        except SQLAlchemyError as exc:
            raise RuntimeError("SQLite WAL 降级至 DELETE 模式失败") from exc
        print(
            "[Database Warning] WAL mode is unavailable; using the safer "
            f"single-user DELETE journal fallback ({wal_error}).",
            flush=True,
        )
    try:
        Path(database_name).resolve().chmod(0o600)
    except OSError:
        pass
    return mode

_PREVIEW_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\([^)]*\)|\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}"
)
_PREVIEW_DELIM_RE = re.compile(r"\$\$?|\\[\(\)\[\]]")
_PREVIEW_SPACING_RE = re.compile(
    r"\\(?:quad|qquad|enspace|thinspace|medspace|thickspace|[,;!:])(?![A-Za-z])|~"
)
_PREVIEW_SPACE_RE = re.compile(r"\s+")


def _plain_text_preview(value, limit: int = 140) -> str:
    """把 LaTeX/Markdown 混排的题干压成一行纯文本预览（列表接口专用）。

    只剥「不影响阅读的包装」：插图折成〔图〕占位（组卷时看得出这道题有没有配图
    就够），数学定界符与间距宏去掉，**公式本体原样保留**——教师日常就写 LaTeX，
    `\\frac{1}{2}` 比硬压成 `12` 更好认，也不会把预览骗成「题干很短」。
    """

    text = _PREVIEW_IMAGE_RE.sub("〔图〕", str(value or ""))
    text = _PREVIEW_DELIM_RE.sub(" ", text)
    text = _PREVIEW_SPACING_RE.sub(" ", text)
    text = _PREVIEW_SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


class Question(Base):
    __tablename__ = "questions"

    # 列表接口默认按「录入时间 + 物理 ID」排序。缺这条复合索引时，SQLite 对
    # ORDER BY created_at DESC, id DESC 只能 SCAN 全表再建 TEMP B-TREE 重排，
    # 十万题规模下每翻一页都要重排整表。单条 (created_at, id) 索引即可同时
    # 服务升/降序——SQLite 会反向扫描索引，不必为每个方向各建一条。
    __table_args__ = (
        Index("ix_questions_created_at_id", "created_at", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)  # 题干 (LaTeX + markdown)
    question_type = Column(String(50), default="single_choice", index=True)  # single_choice, multi_choice, fill_in_blank, detailed_answer
    category_compulsory = Column(String(100), default="", index=True)  # 必修/选修/选择性必修
    category_chapter = Column(String(100), default="", index=True)  # 章节
    category_knowledge = Column(String(100), default="", index=True)  # 知识点（小节）
    applicable_grade = Column(String(100), default="", index=True)  # 适用年级（g1 高一 / g2 高二 / g3 高三）
    topic_id = Column(String(100), default="", index=True)  # 考点 ID（挂在小节下，稳定不变；显示名由大纲映射翻译）
    difficulty = Column(String(50), default="L2", index=True)  # L1概念/L2运算/L3应用/L4综合
    source = Column(String(200), default="")  # 来源（具体试卷名，如"2025全国甲卷"）
    source_school_id = Column(String(20))  # 主来源学校 ID（schools.json 的 sch_xxx；可空=未建档/手输地区）
    paper_category = Column(String(50), default="", index=True)  # 试卷分类：gaokao/liankao/moni/xiaokao/qiangji/foreign
    paper_question_no = Column(String(50), default="")  # 试卷原题号
    answer_markdown = Column(Text, default="")  # 答案与解析 (LaTeX + markdown)
    review = Column(Text, default="")  # 评述 (允许空白)
    association_group_id = Column(String(100), default="", index=True)  # 关联题目分组ID (支持传递关系)
    _image_paths = Column(Text, default="[]", name="image_paths")  # 以JSON字符串形式存储相对路径列表
    tikz_code = Column(Text, default="")  # TikZ 几何绘图源代码
    tikz_reference_image_path = Column(Text, default="")  # 题干 TikZ 自动重绘时的原题参考图
    _content_tikz_assets = Column(Text, default="[]", name="content_tikz_assets")  # 题干多图 TikZ 源码与渲染图映射
    _answer_tikz_assets = Column(Text, default="[]", name="answer_tikz_assets")  # 解答多图 TikZ 源码与渲染图映射
    figure_align = Column(String(50), default="right")  # 插图排版位置: right (题干右侧), center (下方居中), bottom_right (下方居右)
    figure_align_custom = Column(
        Boolean, nullable=False, default=False, server_default="0"
    )  # 用户是否明确挑选过插图位置；没挑过时导出的默认排版仍可随卷型变化
    figure_size = Column(
        String(20), nullable=False, default="auto", server_default="auto"
    )  # 题末可分离插图尺寸: auto, small, medium, large
    # 逐图排版（align/size）以 JSON 存在 image_layouts 列。属性名与列名刻意不同，
    # 这样 SQLAlchemy 存的是校验后的 JSON 原文，读出来的是布局字典。
    _image_layouts = Column(
        Text, nullable=False, default="{}", server_default="{}", name="image_layouts"
    )
    tags = Column(Text, default="")  # 自定义标签 (逗号分隔或字符串)
    # 知识点（细粒度多对多，独立于考向树的小节/考向，参考库 knowledge_points 范式）
    knowledge_points = Column(Text, default="")  # 知识点缓存串（逗号分隔，便于列表/搜索）
    # 题目考试元数据（移植自参考库 questions 表，原库全缺）
    exam_year = Column(Integer, nullable=True, index=True)  # 考试年份（如 2025）
    exam_region = Column(String(100), default="", index=True)  # 卷区/地区（如 全国甲卷/新高考I卷）
    exam_stage = Column(String(20), default="", index=True)  # 考试阶段（校考题细分：期中/期末/月考/其他）
    video_url = Column(Text, default="")  # 视频讲解链接
    option_columns = Column(Integer, default=0)  # 选择题选项列数：0=自适应，1/2/4=强制列数
    options = Column(Text, default="[]")  # 选项级结构化（JSON 数组，如 [{"label":"A","content":"..."}]）
    analysis_methods = Column(Text, default="[]")  # 解法方法（JSON 数组，如 ["几何法","代数法"]）
    # 教育测量维度（v17，借鉴主流题库/教育测量学）
    difficulty_index = Column(Float, nullable=True, index=True)  # 难度系数 0.0~1.0（与 L1~L5 并存）
    cognitive_level = Column(String(20), default="", index=True)  # 认知层次：识记/理解/应用/分析/综合/评价
    core_competency = Column(Text, default="")  # 核心素养（逗号串，如 数学抽象,逻辑推理）
    error_prone_points = Column(Text, default="[]")  # 易错点（JSON 数组）
    usage_count = Column(Integer, default=0, index=True)  # 组卷引用次数
    created_at = Column(DateTime, default=_utcnow_naive)

    @property
    def image_layouts(self):
        from .image_layout import normalize_image_layouts

        try:
            return normalize_image_layouts(self._image_layouts or "{}", self.content)
        except ValueError:
            # 布局只是可选装饰：脏数据不该让整条列表或整份导出 500，退回默认即可
            return {}

    @image_layouts.setter
    def image_layouts(self, value):
        from .image_layout import normalize_image_layouts

        self._image_layouts = json.dumps(
            normalize_image_layouts(value, self.content), ensure_ascii=False
        )

    @property
    def image_paths(self):
        try:
            value = json.loads(self._image_paths)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @image_paths.setter
    def image_paths(self, value):
        if isinstance(value, list):
            self._image_paths = json.dumps(value)
        else:
            self._image_paths = "[]"

    @property
    def answer_tikz_assets(self):
        try:
            value = json.loads(self._answer_tikz_assets or "[]")
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @answer_tikz_assets.setter
    def answer_tikz_assets(self, value):
        if isinstance(value, list):
            self._answer_tikz_assets = json.dumps(value, ensure_ascii=False)
        else:
            self._answer_tikz_assets = "[]"

    @property
    def content_tikz_assets(self):
        try:
            value = json.loads(self._content_tikz_assets or "[]")
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @content_tikz_assets.setter
    def content_tikz_assets(self, value):
        if isinstance(value, list):
            self._content_tikz_assets = json.dumps(value, ensure_ascii=False)
        else:
            self._content_tikz_assets = "[]"

    @property
    def display_image_paths(self):
        """Return question figures while keeping AI-only references hidden."""

        hidden_references = {
            str(self.tikz_reference_image_path or "").strip()
        }
        for asset in [*self.content_tikz_assets, *self.answer_tikz_assets]:
            if not isinstance(asset, dict):
                continue
            reference = str(asset.get("reference_image_path") or "").strip()
            if reference:
                hidden_references.add(reference)
        hidden_references.discard("")
        return [path for path in self.image_paths if path not in hidden_references]

    @property
    def export_image_paths(self):
        r"""导出侧的**物理**资源清单：显示清单 + 被 display 口径藏起来的 TikZ 参考图。

        ``display_image_paths`` 是 UI 语义（已重绘成 TikZ 就不该在面板里再显示原参考图），
        但导出必须按物理口径收图：正文里的 ``\includegraphics`` 仍可能指向那些原图
        —— ``\begin{choices}`` 内的选项图就不会被改写成 TikZ。拿 display 口径去凑编译
        清单会把它们整批藏掉，XeLaTeX 于是撞上不存在的文件，``Unable to load picture or
        PDF file`` 叠加 ``-halt-on-error`` 直接炸掉整卷（2026-10-18 事故，题库 id=15）。
        """
        collected: list = []

        def add(reference) -> None:
            value = str(reference or "").strip()
            if value and value not in collected:
                collected.append(value)

        for path in self.image_paths:
            add(path)
        add(self.tikz_reference_image_path)
        for asset in [*self.content_tikz_assets, *self.answer_tikz_assets]:
            if not isinstance(asset, dict):
                continue
            add(asset.get("image_path"))
            add(asset.get("reference_image_path"))
        return collected

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "applicable_grade": self.applicable_grade or "",
            "topic_id": self.topic_id or "",
            "difficulty": self.difficulty,
            "source": self.source,
            "source_school_id": self.source_school_id or "",
            "answer_markdown": self.answer_markdown,
            "has_answer": bool((self.answer_markdown or "").strip()),
            "review": self.review,
            "association_group_id": self.association_group_id,
            "paper_category": self.paper_category or "",
            "paper_question_no": self.paper_question_no or "",
            "image_paths": self.display_image_paths,
            "tikz_code": self.tikz_code,
            "tikz_reference_image_path": self.tikz_reference_image_path or "",
            "content_tikz_assets": self.content_tikz_assets,
            "answer_tikz_assets": self.answer_tikz_assets,
            "figure_align": self.figure_align or "right",
            "figure_align_custom": bool(self.figure_align_custom),
            "figure_size": normalize_figure_size(self.figure_size),
            "image_layouts": self.image_layouts,
            "tags": self.tags,
            "knowledge_points": self.knowledge_points,
            "exam_year": self.exam_year,
            "exam_region": self.exam_region or "",
            "exam_stage": self.exam_stage or "",
            "video_url": self.video_url or "",
            "option_columns": self.option_columns or 0,
            "options": _parse_json_list(self.options),
            "analysis_methods": _parse_json_list(self.analysis_methods),
            "difficulty_index": self.difficulty_index,
            "cognitive_level": self.cognitive_level or "",
            "core_competency": self.core_competency or "",
            "error_prone_points": _parse_json_list(self.error_prone_points),
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

    def to_summary_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "applicable_grade": self.applicable_grade or "",
            "topic_id": self.topic_id or "",
            "difficulty": self.difficulty,
            "source": self.source,
            "source_school_id": self.source_school_id or "",
            "has_answer": bool((self.answer_markdown or "").strip()),
            "association_group_id": self.association_group_id,
            "paper_category": self.paper_category or "",
            "paper_question_no": self.paper_question_no or "",
            "image_paths": self.display_image_paths,
            "tikz_code": self.tikz_code,
            "has_tikz_assets": bool(
                self.content_tikz_assets or self.answer_tikz_assets or self.tikz_code
            ),
            "figure_align": self.figure_align or "right",
            "figure_align_custom": bool(self.figure_align_custom),
            "figure_size": normalize_figure_size(self.figure_size),
            "image_layouts": self.image_layouts,
            "tags": self.tags,
            "knowledge_points": self.knowledge_points,
            "exam_year": self.exam_year,
            "exam_region": self.exam_region or "",
            "exam_stage": self.exam_stage or "",
            "video_url": self.video_url or "",
            "option_columns": self.option_columns or 0,
            "options": _parse_json_list(self.options),
            "analysis_methods": _parse_json_list(self.analysis_methods),
            "difficulty_index": self.difficulty_index,
            "cognitive_level": self.cognitive_level or "",
            "core_competency": self.core_competency or "",
            "error_prone_points": _parse_json_list(self.error_prone_points),
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

    def to_lean_dict(self):
        """列表/组卷流的轻量摘要：只带卡片渲染必需的元信息与题干预览。

        与 ``to_summary_dict`` 的差别是**刻意的**：列表接口不再下发完整
        ``content``、``options``、``tikz_code`` 与任何答案正文。十万题规模下，
        「一页 JSON 把整题干拖回来」正是性能崩塌的根源；需要完整题面时走
        ``/api/questions/{id}``（展开答案）或 ``/api/paper/questions?ids=``
        （加入试卷 / 卷面渲染）按需取。
        """

        return {
            "id": self.id,
            "lean": True,
            "preview": _plain_text_preview(self.content),
            "question_type": self.question_type,
            "difficulty": self.difficulty,
            "source": self.source,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "applicable_grade": self.applicable_grade or "",
            "topic_id": self.topic_id or "",
            "paper_category": self.paper_category or "",
            "paper_question_no": self.paper_question_no or "",
            "has_answer": bool((self.answer_markdown or "").strip()),
            "image_count": len(self.display_image_paths),
            "has_tikz_assets": bool(
                self.content_tikz_assets or self.answer_tikz_assets or self.tikz_code
            ),
            "usage_count": self.usage_count or 0,
        }

class LessonPlan(Base):
    __tablename__ = "lesson_plans"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False, index=True)  # 教案标题
    grade = Column(String(50), default="", index=True)  # g1/g2/g3
    compulsory = Column(String(100), default="", index=True)  # 必修一/必修二/选修一/选修二/选修三/一轮/二轮/三轮
    chapter = Column(String(100), default="")  # 章节
    knowledge = Column(String(100), default="")  # 小节
    topic_ids = Column(Text, default="[]")  # 关联考点ID（JSON数组）
    objectives = Column(Text, default="")  # 教学目标
    key_points = Column(Text, default="")  # 教学重点
    difficult_points = Column(Text, default="")  # 教学难点
    teaching_process = Column(Text, default="{}")  # 教学过程（JSON：导入/讲解/例题/练习/小结/作业）
    board_design = Column(Text, default="")  # 板书设计
    reflection = Column(Text, default="")  # 教学反思
    question_refs = Column(Text, default="{}")  # 关联题目（JSON：{"items":[{question_id, content, stage, note}]}）
    knowledge_content = Column(Text, default="{}")  # 知识内容（JSON：{"blocks":[{title, content}]}）
    mindmap = Column(Text, default="{}")  # 思维导图（JSON：{"root":{text, children:[...]}}）
    status = Column(String(20), default="draft", index=True)  # draft/published
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "grade": self.grade,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge,
            "topic_ids": json.loads(self.topic_ids or "[]"),
            "objectives": self.objectives or "",
            "key_points": self.key_points or "",
            "difficult_points": self.difficult_points or "",
            "teaching_process": json.loads(self.teaching_process or "{}"),
            "board_design": self.board_design or "",
            "reflection": self.reflection or "",
            "question_refs": json.loads(self.question_refs or "{}"),
            "knowledge_content": json.loads(self.knowledge_content or "{}"),
            "mindmap": json.loads(self.mindmap or "{}"),
            "status": self.status or "draft",
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
            "updated_at": (self.updated_at.isoformat() + "Z") if self.updated_at else None,
        }



class QuestionSource(Base):
    """一题多来源：一道题可被多份试卷/多次考试收录。

    每条来源 = 年份 + 学校/地区简称 + 考试类型 + 整卷出处。讲义中
    学生版只展示主来源（排序后第一条），教师版展示全部。
    """

    __tablename__ = "question_sources"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, nullable=False, index=True)
    year = Column(String(20), default="")  # 年份，如 2026
    region = Column(String(100), default="")  # 学校/地区简称：哈三中、省实验、全国甲卷
    province = Column(String(50), default="")  # 省：黑龙江省
    city = Column(String(50), default="")  # 市：哈尔滨市
    grade = Column(String(10), default="")  # 考试年级：g1/g2/g3
    semester = Column(String(10), default="")  # 学期：上/下
    exam_type = Column(String(50), default="")  # 考试类型显示名：三模、期末、高考、月考…
    paper_title = Column(String(200), default="")  # 整卷出处（试卷标题）
    question_no = Column(String(50), default="")  # 该题在这份原卷中的题号
    school_id = Column(String(20))  # 学校词库 ID（sch_xxx；可空=手输地区/未建档）
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        from mathbank.school_library import resolve_school_label
        region_display = resolve_school_label(self.school_id, self.region)
        return {
            "id": self.id,
            "question_id": self.question_id,
            "year": self.year or "",
            "region": region_display,
            "province": self.province or "",
            "city": self.city or "",
            "grade": self.grade or "",
            "semester": self.semester or "",
            "exam_type": self.exam_type or "",
            "paper_title": self.paper_title or "",
            "question_no": self.question_no or "",
            "text": format_source_text(
                self.year, region_display, self.exam_type,
                province=self.province, city=self.city,
                grade=self.grade, semester=self.semester,
            ),
            "school_id": self.school_id or "",
            "sort_order": self.sort_order,
        }


_GRADE_LABELS = {"g1": "高一", "g2": "高二", "g3": "高三"}


def format_source_text(year: str, region: str, exam_type: str,
                       province: str = "", city: str = "",
                       grade: str = "", semester: str = "") -> str:
    """拼接来源显示文本：2026年黑龙江省哈尔滨市哈三中高二下期末。"""
    parts = []
    if year:
        parts.append(str(year) + "年")
    if province:
        parts.append(str(province))
    if city:
        parts.append(str(city))
    if region:
        parts.append(str(region))
    g = _GRADE_LABELS.get(str(grade or "").strip(), "")
    if g:
        parts.append(g)
    if semester:
        parts.append(str(semester))
    if exam_type:
        parts.append(str(exam_type))
    return "".join(parts)


class LectureSetting(Base):
    """讲义全局设置（单行）：封面信息 / 名言池 / 页面模式。"""

    __tablename__ = "lecture_settings"

    id = Column(Integer, primary_key=True)
    cover_title = Column(String(200), default="")  # 封面主标题（缺省按章节名）
    cover_subtitle = Column(String(200), default="同步讲义")  # 封面副标题
    cover_brand = Column(String(200), default="MT-Blaze 的数学空间")  # 封面品牌角标
    cover_author = Column(String(200), default="")  # 封面编著署名
    quote_pool = Column(Text, default="[]")  # 页眉名言池（JSON 数组）
    bound_mode = Column(String(20), default="loose")  # loose 散页 / bound 装订成册
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def to_dict(self):
        try:
            quotes = json.loads(self.quote_pool or "[]")
        except (TypeError, ValueError):
            quotes = []
        return {
            "cover_title": self.cover_title or "",
            "cover_subtitle": self.cover_subtitle or "同步讲义",
            "cover_brand": self.cover_brand or "",
            "cover_author": self.cover_author or "",
            "quote_pool": quotes if isinstance(quotes, list) else [],
            "bound_mode": self.bound_mode or "loose",
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LectureNote(Base):
    """讲义知识块修订稿：知识梳理(小节维度) / 易错点·解题方法(考向维度)。

    用户手动改过的内容存这里；生成讲义时优先取修订稿，无修订则用大纲默认。
    """

    __tablename__ = "lecture_notes"

    id = Column(Integer, primary_key=True, index=True)
    note_key = Column(String(300), nullable=False, unique=True, index=True)
    note_type = Column(String(20), default="knowledge", index=True)  # knowledge/pit/method
    content = Column(Text, default="")  # HTML 源码（含 LaTeX）
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def to_dict(self):
        return {
            "note_key": self.note_key,
            "note_type": self.note_type,
            "content": self.content or "",
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LectureDraft(Base):
    """讲义草稿：一次生成的编排结果（选题 + 角色 + 知识块），可保存续编。"""

    __tablename__ = "lecture_drafts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False, default="未命名讲义")
    grade = Column(String(50), default="", index=True)
    compulsory = Column(String(100), default="", index=True)
    chapter = Column(String(100), default="")
    knowledge = Column(String(100), default="")
    config = Column(Text, default="{}")  # JSON：{topics:[{topic_id,name,items:[{question_id,role}]}]}
    knowledge_src = Column(Text, default="")  # 知识梳理源码（覆盖默认）
    status = Column(String(20), default="draft", index=True)
    created_at = Column(DateTime, default=_utcnow_naive)
    updated_at = Column(DateTime, default=_utcnow_naive, onupdate=_utcnow_naive)

    def to_dict(self):
        try:
            config = json.loads(self.config or "{}")
        except (TypeError, ValueError):
            config = {}
        return {
            "id": self.id,
            "title": self.title,
            "grade": self.grade,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge,
            "config": config if isinstance(config, dict) else {},
            "knowledge_src": self.knowledge_src or "",
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class QuestionCurriculum(Base):
    __tablename__ = "question_curriculums"

    __table_args__ = (
        UniqueConstraint(
            "question_id",
            "version_code",
            name="uq_question_curriculum_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_code = Column(String(50), index=True, nullable=False)  # 'A', 'B', 'S'
    compulsory = Column(String(100), default="", index=True)
    chapter = Column(String(100), default="", index=True)
    knowledge = Column(String(100), default="", index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "question_id": self.question_id,
            "version_code": self.version_code,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge
        }


class QuestionTopic(Base):
    """Multi-kaoxiang slots for one question (一题多考点).

    The row with sort_order == 0 mirrors the question's primary classification
    fields (category_compulsory / category_chapter / category_knowledge /
    topic). Additional rows (sort_order 1..N) are extra kaoxiang attached to
    the same question, enabling AND/OR topic filters when building lessons.
    """

    __tablename__ = "question_topics"

    __table_args__ = (
        UniqueConstraint(
            "question_id",
            "version_code",
            "compulsory",
            "chapter",
            "knowledge",
            "topic_id",
            name="uq_question_topic_slot",
        ),
        Index(
            "idx_question_topics_lookup",
            "version_code",
            "compulsory",
            "chapter",
            "knowledge",
            "topic_id",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_code = Column(String(50), index=True, nullable=False, default="")  # 'A', 'B', 'S', 'G'
    compulsory = Column(String(100), default="", index=True)
    chapter = Column(String(100), default="", index=True)
    knowledge = Column(String(100), default="", index=True)
    topic_id = Column(String(100), default="", index=True)
    sort_order = Column(Integer, default=0, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "question_id": self.question_id,
            "version_code": self.version_code,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge,
            "topic_id": self.topic_id or "",
            "sort_order": self.sort_order or 0,
        }


class QuestionFingerprint(Base):
    """Rebuildable duplicate-detection indexes derived from question content."""

    __tablename__ = "question_fingerprints"

    __table_args__ = (
        Index(
            "idx_question_fingerprints_exact",
            "fingerprint_version",
            "exact_hash",
        ),
        Index(
            "idx_question_fingerprints_band0",
            "fingerprint_version",
            "band0",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band1",
            "fingerprint_version",
            "band1",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band2",
            "fingerprint_version",
            "band2",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band3",
            "fingerprint_version",
            "band3",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band4",
            "fingerprint_version",
            "band4",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band5",
            "fingerprint_version",
            "band5",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band6",
            "fingerprint_version",
            "band6",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_band7",
            "fingerprint_version",
            "band7",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band0",
            "fingerprint_version",
            "text_band0",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band1",
            "fingerprint_version",
            "text_band1",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band2",
            "fingerprint_version",
            "text_band2",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band3",
            "fingerprint_version",
            "text_band3",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band4",
            "fingerprint_version",
            "text_band4",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band5",
            "fingerprint_version",
            "text_band5",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band6",
            "fingerprint_version",
            "text_band6",
            "token_count",
        ),
        Index(
            "idx_question_fingerprints_text_band7",
            "fingerprint_version",
            "text_band7",
            "token_count",
        ),
    )

    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    fingerprint_version = Column(Integer, primary_key=True, nullable=False)
    content_revision_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    exact_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    critical_math_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    answer_hash = Column(
        String(64), default="", server_default=text("''"), nullable=False
    )
    simhash_hex = Column(
        String(32), default="", server_default=text("''"), nullable=False
    )
    token_count = Column(Integer, default=0, server_default=text("0"), nullable=False)
    choice_count = Column(Integer, default=0, server_default=text("0"), nullable=False)
    figure_count = Column(Integer, default=0, server_default=text("0"), nullable=False)
    visible_image_hashes = Column(
        Text, default="[]", server_default=text("'[]'"), nullable=False
    )
    tikz_hashes = Column(
        Text, default="[]", server_default=text("'[]'"), nullable=False
    )
    text_band0 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band1 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band2 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band3 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band4 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band5 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band6 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    text_band7 = Column(
        String(16), default="", server_default=text("''"), nullable=False
    )
    band0 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band1 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band2 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band3 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band4 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band5 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band6 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    band7 = Column(Integer, default=0, server_default=text("0"), nullable=False)
    status = Column(
        String(20),
        default="pending",
        server_default=text("'pending'"),
        nullable=False,
    )
    updated_at = Column(
        DateTime,
        default=_utcnow_naive,
        onupdate=_utcnow_naive,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

class Tag(Base):
    """Normalized custom-tag dictionary (replaces the comma-joined string)."""

    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True, index=True)


class QuestionTag(Base):
    """Many-to-many link between a question and its tags."""

    __tablename__ = "question_tags"

    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    tag_id = Column(
        Integer,
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )


def normalize_tag_string(tag_string):
    """Split a comma/、 separated tag payload into a clean, de-duplicated list."""

    if not tag_string:
        return []
    parts = str(tag_string).replace("，", ",").split(",")
    seen = set()
    clean = []
    for raw in parts:
        name = raw.strip()
        if not name or name in seen or len(name) > 64:
            continue
        seen.add(name)
        clean.append(name)
    return clean


def sync_question_tags(db, question, tag_string):
    """Replace a question's tag associations from a string payload.

    Upserts tag names into ``tags`` and rewrites the ``question_tags`` junction,
    keeping the denormalized ``Question.tags`` cache column in sync so existing
    read paths (to_dict, search) keep working unchanged.
    """

    names = normalize_tag_string(tag_string)
    tag_ids = []
    for name in names:
        tag = db.query(Tag).filter(Tag.name == name).first()
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        tag_ids.append(tag.id)
    db.query(QuestionTag).filter(QuestionTag.question_id == question.id).delete()
    for tag_id in tag_ids:
        db.add(QuestionTag(question_id=question.id, tag_id=tag_id))
    question.tags = ",".join(names)
    return names


def _parse_json_list(value):
    """Safely parse a JSON array column into a Python list (fallback to [])."""

    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


class KnowledgePoint(Base):
    """Normalized fine-grained knowledge-point dictionary.

    Distinct from ``category_knowledge`` (the 小节 bound to the 考向树): a teacher
    can attach arbitrary cross-curriculum KPs (e.g. 二次函数最值) to a question,
    enabling multi-KP stats/filtering like the reference library's
    ``knowledge_points`` + ``question_knowledge``.
    """

    __tablename__ = "knowledge_points"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True, index=True)


class QuestionKnowledge(Base):
    """Many-to-many link between a question and its knowledge points."""

    __tablename__ = "question_knowledge"

    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    kp_id = Column(
        Integer,
        ForeignKey("knowledge_points.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )


def sync_question_knowledge(db, question, kp_string):
    """Replace a question's knowledge-point associations from a string payload.

    Mirrors :func:`sync_question_tags`: upserts KP names into ``knowledge_points``
    and rewrites the ``question_knowledge`` junction while keeping the
    denormalized ``Question.knowledge_points`` cache column in sync.
    """

    names = normalize_tag_string(kp_string)
    kp_ids = []
    for name in names:
        kp = db.query(KnowledgePoint).filter(KnowledgePoint.name == name).first()
        if kp is None:
            kp = KnowledgePoint(name=name)
            db.add(kp)
            db.flush()
        kp_ids.append(kp.id)
    db.query(QuestionKnowledge).filter(
        QuestionKnowledge.question_id == question.id
    ).delete()
    for kp_id in kp_ids:
        db.add(QuestionKnowledge(question_id=question.id, kp_id=kp_id))
    question.knowledge_points = ",".join(names)
    return names


class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    subtitle = Column(String(200), default="")
    paper_type = Column(String(50), default="exam")  # exam, quiz, handout
    total_score = Column(Integer, default=150)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        meta = {}
        try:
            parsed_meta = json.loads(self.metadata_json or "{}")
            meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            meta = {}
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "paper_type": self.paper_type,
            "total_score": self.total_score,
            "show_secret": meta.get("show_secret", True),
            "show_notice": meta.get("show_notice", True),
            "metadata_json": self.metadata_json,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class PaperQuestion(Base):
    __tablename__ = "paper_questions"

    __table_args__ = (
        UniqueConstraint("paper_id", "order_index", name="uq_paper_question_order"),
        CheckConstraint("score >= 0", name="ck_paper_question_score_nonnegative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(
        Integer,
        ForeignKey("papers.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    order_index = Column(Integer, default=0)
    score = Column(Integer, default=5)
    snapshot_json = Column(Text, default="{}", server_default=text("'{}'"))

    def to_dict(self):
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "question_id": self.question_id,
            "order_index": self.order_index,
            "score": self.score
        }


class ImportBatch(Base):
    """One导入批次（一次导入任务落地的题目集合），支持整体回滚。"""

    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), default="", unique=True, index=True)  # 客户端批次令牌（幂等键）
    name = Column(String(200), default="")  # 批次名（如试卷标题或文件名）
    source_name = Column(String(300), default="")  # 来源文件/说明
    question_ids = Column(Text, default="[]")  # 本批次创建的题目 id（JSON 数组）
    question_count = Column(Integer, default=0)  # 冗余计数，便于列表展示
    rolled_back = Column(Integer, default=0)  # 0/1，是否已回滚
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        return {
            "id": self.id,
            "token": self.token or "",
            "name": self.name or "",
            "source_name": self.source_name or "",
            "question_ids": _parse_json_list(self.question_ids),
            "question_count": self.question_count or 0,
            "rolled_back": bool(self.rolled_back),
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
        }


class TikzVersion(Base):
    """TikZ 绘图版本树：同一 (question_id, slot) 下的历次源码快照。"""

    __tablename__ = "tikz_versions"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    slot = Column(String(50), default="", index=True)  # content / answer / 资产索引位
    version = Column(Integer, default=1)  # 该 slot 内的自增版本号
    parent_version_id = Column(Integer, nullable=True)  # 上一版本（可追溯派生链）
    code = Column(Text, default="")  # TikZ 源码
    source_hash = Column(String(64), default="", index=True)  # 源码 sha256（去重/比对）
    renderer_version = Column(String(50), default="")  # 渲染器/模型版本，便于复现
    model = Column(String(100), default="")  # 生成模型（如 deepseek-chat）
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        return {
            "id": self.id,
            "question_id": self.question_id,
            "slot": self.slot or "",
            "version": self.version or 1,
            "parent_version_id": self.parent_version_id,
            "code": self.code or "",
            "source_hash": self.source_hash or "",
            "renderer_version": self.renderer_version or "",
            "model": self.model or "",
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None,
        }


# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables
def init_db():
    from mathbank.db_migrations import (
        LEGACY_REQUIRED_TABLES,
        LATEST_SCHEMA_VERSION,
        REQUIRED_TABLES,
        create_pre_migration_backup,
        ensure_late_tables,
        migrate_database,
        schema_version,
    )

    # Refuse a future schema before create_all or any legacy ALTER can mutate it.
    current_version = schema_version(engine)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于程序支持版本 "
            f"{LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    if current_version >= LATEST_SCHEMA_VERSION:
        # A database stamped as the latest version can still be missing a table
        # introduced in that version: create_all never adds tables to an
        # existing database.  Heal it instead of refusing to boot.
        with engine.begin() as repair_connection:
            ensure_late_tables(repair_connection)
    with engine.connect() as connection:
        existing_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        core_tables = existing_tables & LEGACY_REQUIRED_TABLES
        if existing_tables:
            upgradeable_layouts = (
                {"questions"},
                {"questions", "question_curriculums"},
                LEGACY_REQUIRED_TABLES,
            )
            if current_version != 0:
                upgradeable_layouts = (LEGACY_REQUIRED_TABLES,)
            if core_tables not in upgradeable_layouts:
                missing = ", ".join(sorted(LEGACY_REQUIRED_TABLES - core_tables))
                raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")
            if current_version >= LATEST_SCHEMA_VERSION:
                missing = REQUIRED_TABLES - existing_tables
                if missing:
                    raise RuntimeError(
                        "数据库结构不完整，缺少必要数据表: "
                        + ", ".join(sorted(missing))
                    )

            # Old releases legitimately had only the question tables.  Accept
            # those known layouts, but reject a similarly named damaged table
            # before create_all can disguise the missing core columns.
            required_columns = {
                "questions": {
                    "id", "content", "question_type", "category_compulsory",
                    "category_chapter", "category_knowledge", "difficulty",
                    "source", "answer_markdown", "image_paths", "created_at",
                },
                "question_curriculums": {
                    "id", "question_id", "version_code", "compulsory",
                    "chapter", "knowledge",
                },
                "papers": {
                    "id", "title", "subtitle", "paper_type", "total_score",
                    "metadata_json", "created_at",
                },
                "paper_questions": {
                    "id", "paper_id", "question_id", "order_index", "score",
                },
            }
            if current_version >= 3:
                required_columns["questions"].add("answer_tikz_assets")
            if current_version >= 4:
                required_columns["questions"].add("tikz_reference_image_path")
            if current_version >= 5:
                required_columns["questions"].add("content_tikz_assets")
            if current_version >= 9:
                required_columns["questions"].add("applicable_grade")
                required_columns["questions"].add("topic")
            if current_version >= 11:
                # v11 replaces the topic name column with a stable topic_id.
                required_columns["questions"].discard("topic")
                required_columns["questions"].add("topic_id")
            if current_version >= 26:
                # v26 起题末插图支持逐图尺寸/位置：已定版却缺列说明结构被截断过
                required_columns["questions"].add("image_layouts")
                required_columns["questions"].add("figure_size")
                required_columns["questions"].add("figure_align_custom")
            for table_name in core_tables:
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                }
                missing_columns = required_columns[table_name] - columns
                if missing_columns:
                    missing = ", ".join(sorted(missing_columns))
                    raise RuntimeError(
                        f"数据库表 {table_name} 缺少核心字段: {missing}"
                    )
    pre_migration_backup = None
    if current_version < LATEST_SCHEMA_VERSION and existing_tables:
        pre_migration_backup = create_pre_migration_backup(
            engine,
            from_version=current_version,
            to_version=LATEST_SCHEMA_VERSION,
        )

    if existing_tables:
        # Existing databases must leave fingerprint table/index creation or
        # replacement to the explicit migration transaction below.  create_all
        # remains responsible only for filling the known legacy core layout
        # when upgrading very old question-only databases.
        Base.metadata.create_all(
            bind=engine,
            tables=[
                Question.__table__,
                QuestionCurriculum.__table__,
                Paper.__table__,
                PaperQuestion.__table__,
            ],
        )
    else:
        Base.metadata.create_all(bind=engine)
    # Create indexes manually and execute automatic migrations for SQLite databases to ensure maximum performance at scale
    try:
        with engine.begin() as conn:
            # Check column existence
            cursor = conn.execute(text("PRAGMA table_info(questions)"))
            columns = [row[1] for row in cursor.fetchall()]
            
            if "review" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN review TEXT DEFAULT ''"))
                print("Added column 'review' to questions table successfully.")
                
            if "association_group_id" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN association_group_id VARCHAR(100) DEFAULT ''"))
                print("Added column 'association_group_id' to questions table successfully.")
                
            if "tikz_code" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tikz_code TEXT DEFAULT ''"))
                print("Added column 'tikz_code' to questions table successfully.")

            if "figure_align" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN figure_align VARCHAR(50) DEFAULT 'right'"))
                print("Added column 'figure_align' to questions table successfully.")
                
            if "tags" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tags TEXT DEFAULT ''"))
                print("Added column 'tags' to questions table successfully.")
                
            if "usage_count" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN usage_count INTEGER DEFAULT 0"))
                print("Added column 'usage_count' to questions table successfully.")
                
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_compulsory ON questions (category_compulsory)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_chapter ON questions (category_chapter)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_knowledge ON questions (category_knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_question_type ON questions (question_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions (difficulty)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_association_group_id ON questions (association_group_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (tags)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_usage_count ON questions (usage_count)"))

            # Create indexes on question_curriculums
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_lookup ON question_curriculums (version_code, compulsory, chapter, knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_qid ON question_curriculums (question_id)"))

            # Auto-migrate legacy data to A-version question_curriculums
            cursor = conn.execute(text("SELECT COUNT(*) FROM question_curriculums"))
            count = cursor.fetchone()[0]
            if count == 0:
                conn.execute(text("""
                    INSERT INTO question_curriculums (question_id, version_code, compulsory, chapter, knowledge)
                    SELECT id, 'A', category_compulsory, category_chapter, category_knowledge
                    FROM questions
                """))
                print("Successfully auto-migrated legacy question categories to A-version question_curriculums mapping.")
    except Exception as e:
        raise RuntimeError("数据库旧字段或索引迁移失败，服务已停止启动") from e

    migration_result = migrate_database(
        engine, pre_migration_backup=pre_migration_backup
    )
    configure_sqlite_wal(engine)
    if migration_result.get("from_version") != migration_result.get("to_version"):
        print(f"[Database] Schema migration complete: {migration_result}")
