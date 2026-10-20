import ast
import asyncio
import re

import pytest

from mathbank.curriculums import get_curriculum_preset, load_curriculum
from mathbank.paths import (
    CURRICULUMS_DIR,
    DATABASE_FILE,
    PROJECT_ROOT,
    STATIC_DIR,
    TEMPLATES_DIR,
)
from mathbank.prompts import build_ai_solve_prompts


STATIC_JS_DIR = PROJECT_ROOT / "static" / "js"


def test_python_sources_do_not_use_deprecated_fitz_import():
    source_paths = [PROJECT_ROOT / "main.py"]
    for directory_name in ("mathbank", "scripts", "tests"):
        source_paths.extend((PROJECT_ROOT / directory_name).rglob("*.py"))

    violations = []
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "fitz" for alias in node.names
            ):
                violations.append(f"{source_path.relative_to(PROJECT_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module == "fitz":
                violations.append(f"{source_path.relative_to(PROJECT_ROOT)}:{node.lineno}")

    assert violations == []


def test_shared_paths_are_absolute_and_project_anchored():
    for path in (DATABASE_FILE, STATIC_DIR, TEMPLATES_DIR, CURRICULUMS_DIR):
        assert path.is_absolute()
    assert DATABASE_FILE.parent == PROJECT_ROOT
    assert STATIC_DIR.parent == PROJECT_ROOT


def test_all_curriculum_presets_load_from_resources():
    for version in ("A", "B", "S", "H"):
        preset = get_curriculum_preset(version)
        assert preset["version"] == version
        assert preset["metadata"]["curriculum"] == load_curriculum(version)
        assert preset["metadata"]["question_types"]
        assert preset["metadata"]["difficulties"]


def test_solve_prompt_builder_preserves_required_structure():
    system_prompt, user_prompt = build_ai_solve_prompts(
        "single_choice",
        "若 $x=1$，求函数值。",
        ocr_result="草稿",
        custom_prompt="简化步骤",
    )
    assert "【答案】" in system_prompt
    assert "【解析】" in system_prompt
    assert "严禁超纲" in system_prompt
    assert "草稿" in user_prompt
    assert "简化步骤" in user_prompt
    assert "若 $x=1$" in user_prompt


@pytest.mark.parametrize("question_type", ["single_choice", "multi_choice", "fill_in_blank", "detailed_answer"])
def test_solve_prompt_keeps_exactly_answer_and_analysis_blocks(question_type):
    """AI 解题输出必须只有【答案】+【解析】两个板块，不得再出现旧板块。"""

    system_prompt, _ = build_ai_solve_prompts(question_type, "若 $x=1$，求函数值。")

    # 只保留这两个板块；首个板块必须是【答案】（first_block_header 判据与之一致）
    assert "【答案】" in system_prompt
    assert "【解析】" in system_prompt
    for removed_header in ("【参考答案】", "【解析过程】", "【解析思路】", "【核心知识点】", "【规范解答】"):
        assert removed_header not in system_prompt

    # 落进 system_instructions 的首个板块判据必须写成新名，避免提示词改了
    # 而 "第一个字符必须是..." 仍指向旧名。
    assert "第一个字符必须是“\\textbf{【答案】}”" in system_prompt


def test_extract_choice_answer_letter_reads_new_answer_block():
    """新格式（【答案】+答案）必须仍能被答案字母提取链路命中。"""

    from mathbank.paper_helper import extract_choice_answer_letter

    # 提示词要求的形态：`\textbf{【答案】}` + 答案内容（无冒号）
    assert extract_choice_answer_letter("\\textbf{【答案】}D\n\\textbf{【解析】}由 $x=1$ 得。") == "D"
    # 带冒号的写法同样兼容
    assert extract_choice_answer_letter("\\textbf{【答案】}：D\n\\textbf{【解析】}由 $x=1$ 得。") == "D"
    # 多选题多字母
    assert extract_choice_answer_letter("\\textbf{【答案】}ACD\n\\textbf{【解析】}略。") == "ACD"


def test_editor_identity_and_meta_preview_have_single_sources():
    api_source = (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")
    editor_source = (STATIC_JS_DIR / "editor.js").read_text(encoding="utf-8")
    import_source = (STATIC_JS_DIR / "import.js").read_text(encoding="utf-8")
    combined_source = "\n".join((api_source, editor_source, import_source))

    assert "const EditorState =" in api_source
    assert "window.EditorState = EditorState" in api_source
    for legacy_name in (
        "currentQuestionId",
        "currentSeqNum",
        "currentCreatedAt",
        "currentDraftId",
    ):
        assert legacy_name not in combined_source

    assert combined_source.count("getElementById('paperBadges')") == 1
    assert "window.renderEditorPaperMeta = renderEditorPaperMeta" in editor_source
    assert "renderEditorPaperMeta();" in import_source
    select_start = import_source.index("function selectQuestion(item)")
    select_end = import_source.index("window.reloadCurrentQuestionSilently", select_start)
    select_source = import_source[select_start:select_end]
    assert "EditorState.useQuestion(item)" not in select_source
    assert select_source.index(".then(fullItem =>") < select_source.index("EditorState.useQuestion(fullItem)")
    assert "loadSequence !== questionDetailLoadSequence" in select_source


def test_multimodal_ai_routes_use_shared_provider_resolvers():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    # AI 域实现已下沉到 mathbank.routes_ai / mathbank.routes_paper（2026-09-17），
    # 源码计数断言需合并统计。
    routes_ai_source = (PROJECT_ROOT / "mathbank" / "routes_ai.py").read_text(
        encoding="utf-8"
    )
    routes_paper_source = (PROJECT_ROOT / "mathbank" / "routes_paper.py").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((main_source, routes_ai_source, routes_paper_source))

    for legacy_helper in (
        "def ocr_via_siliconflow",
        "def ocr_via_ali_bailian",
        "def ocr_via_zhongzhan",
    ):
        assert legacy_helper not in combined

    assert "def ocr_via_provider" in combined
    assert "resolve_ocr_provider(engine)" in combined
    assert "resolve_ocr_fallbacks(prefer_engine)" in combined
    assert combined.count("resolve_draw_provider(prefer_draw)") == 2


def test_all_text_ai_routes_parse_models_with_the_shared_effort_rules():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    routes_ai_source = (PROJECT_ROOT / "mathbank" / "routes_ai.py").read_text(
        encoding="utf-8"
    )
    routes_paper_source = (PROJECT_ROOT / "mathbank" / "routes_paper.py").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((main_source, routes_ai_source, routes_paper_source))

    assert "parse_effort=False" not in combined
    assert "robust_request_post" not in combined
    assert "inject_reasoning_effort" in combined


def test_bailian_model_presets_are_current_and_task_specific():
    api_source = (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")

    assert "const BAILIAN_MODEL_PRESETS_BY_TASK" in api_source
    assert "const BAILIAN_MODEL_DEFAULTS_BY_TASK" in api_source
    for task_key in ("solve", "parse", "classify", "ocr", "draw"):
        assert f"{task_key}:" in api_source
    for current_model in ("qwen3.7-flash", "qwen3.7-plus", "qwen3.8-max"):
        assert current_model in api_source
    for removed_preset in (
        '"qwen3-vl-flash"',
        '"qwen-vl-plus"',
        '"qwen-vl-max"',
        '"qwen-max"',
        '"qwen-plus"',
        '"qwen3.5-ocr"',
    ):
        assert removed_preset not in api_source
    assert "models = [selectedValue, ...models]" in api_source


def test_deepseek_catalog_tracks_the_current_v41_flash_model():
    """DeepSeek 目录与默认值必须跟随官方当前主推模型（deepseek-flash）。"""

    api_source = (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")
    ocr_source = (STATIC_JS_DIR / "ocr.js").read_text(encoding="utf-8")
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    # 解析流水线与 AI/试卷域的实现已下沉到 mathbank，站点统计需一并覆盖。
    parsing_source = (PROJECT_ROOT / "mathbank" / "parsing_pipeline.py").read_text(
        encoding="utf-8"
    )
    routes_ai_source = (PROJECT_ROOT / "mathbank" / "routes_ai.py").read_text(
        encoding="utf-8"
    )
    routes_paper_source = (PROJECT_ROOT / "mathbank" / "routes_paper.py").read_text(
        encoding="utf-8"
    )
    settings_source = (PROJECT_ROOT / "mathbank" / "routes_settings.py").read_text(
        encoding="utf-8"
    )
    combined_main = "\n".join((main_source, routes_ai_source, routes_paper_source))

    # 预设列表提供官方主名，并保留仍在服务的 V4 Pro
    assert "deepseek: [" in api_source
    assert '"deepseek-flash"' in api_source
    assert '"deepseek-v4-pro"' in api_source
    # 只对 MODEL_PRESETS.deepseek 数组本体做下线名检查：中转站（zhongzhan_*）会
    # 真实代理这些模型 ID（智元 token.sensenova.cn 的 /models 就返回 deepseek-v4-flash），
    # 整文件 grep 会把合法的代理目录误判成 DeepSeek 目录回归。
    _ds_match = re.search(r"deepseek:\s*\[(?P<body>.*?)\]", api_source, re.S)
    deepseek_block = _ds_match.group("body") if _ds_match else ""
    # 已下线的旧 Flash 名不得再作为 DeepSeek 预设或默认值出现
    for removed in ('"deepseek-v4-flash"', "'deepseek-v4-flash'", "DeepSeek-V4-Flash"):
        assert removed not in deepseek_block, removed
        assert removed not in ocr_source, removed
    # 前后端默认值统一指向 deepseek-flash（单引号形式 = 默认值，而不是预设清单项）
    for source in (deepseek_block, ocr_source, main_source, settings_source):
        assert "'deepseek-v4-pro'" not in source
        assert "'deepseek-v4-flash'" not in source
        assert "deepseek-v4-flash" not in source
    for source in (combined_main, settings_source):
        assert '"deepseek-v4-pro"' not in source
    assert '"deepseek-chat"' not in combined_main, "不再以旧模型名作为缺省模型"

    # 思考参数统一由共享策略生成，禁止各处手搓 thinking 字段
    # 六个站点：solve / classify / parse×2 / latex_diagnostic / pdf_metadata（R1 快路径元数据批分类）
    assert (combined_main + parsing_source).count("apply_deepseek_thinking_policy(") == 6
    assert 'data["thinking"] = {' not in combined_main
    assert 'payload["thinking"] = {' not in combined_main


def test_settings_can_refresh_models_from_the_provider_api():
    """设置页支持「从官网拉取」模型列表，且密钥不回显。"""

    api_source = (STATIC_JS_DIR / "api.js").read_text(encoding="utf-8")
    settings_source = (PROJECT_ROOT / "mathbank" / "routes_settings.py").read_text(
        encoding="utf-8"
    )

    # 后端端点：用提供商真实 /models 接口取列表
    assert '@router.post("/api/ai/models")' in settings_source
    assert "build_models_url" in settings_source
    assert "parse_model_ids" in settings_source
    assert "resolve_provider_endpoint" in settings_source

    # 前端：下拉模式与中转站输入模式都要有刷新按钮
    assert api_source.count('onclick="refreshModelsFromProvider(') == 2
    assert "window.refreshModelsFromProvider = refreshModelsFromProvider" in api_source
    assert "'/api/ai/models'" in api_source
    # 拉回来的模型并入 localStorage 自定义列表，刷新后仍在
    assert "custom_models_${provider}" in api_source
    # 未保存的 Key / Base 也能用于拉取
    assert "providerCredentialInputs" in api_source


def test_blocking_upload_and_ai_handlers_run_in_fastapi_worker_threads():
    import inspect
    import main

    for handler_name in (
        "upload_image",
        "upload_tex_source",
        "upload_batch_images",
        "upload_pdf_task",
        "upload_docx_task",
        "ai_solve",
        "ai_classify",
        "ai_parse_paper",
    ):
        assert not inspect.iscoroutinefunction(getattr(main, handler_name))


def test_paper_parsers_use_defensive_ai_json_parser():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    # 试卷解析实现已下沉到 mathbank.parsing_pipeline 与 mathbank.routes_paper，
    # 三处源码合并统计。
    parsing_source = (PROJECT_ROOT / "mathbank" / "parsing_pipeline.py").read_text(
        encoding="utf-8"
    )
    routes_paper_source = (PROJECT_ROOT / "mathbank" / "routes_paper.py").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((main_source, parsing_source, routes_paper_source))

    assert combined.count("parse_ai_json(raw_ai_text, raw_markdown=latex_content)") == 1
    assert combined.count("parse_ai_json(raw_ai_text, raw_markdown=model_source)") == 1


def test_backend_modules_and_cli_tools_live_in_packages():
    for module_name in (
        "asset_security.py",
        "backup.py",
        "database.py",
        "db_migrations.py",
        "health.py",
        "paper_helper.py",
        "sync_helper.py",
        "task_manager.py",
    ):
        assert not (PROJECT_ROOT / module_name).exists()
        assert (PROJECT_ROOT / "mathbank" / module_name).is_file()

    for script_name in (
        "search_questions.py",
        "backup.py",
        "restore.py",
        "migrate_fillin.py",
        "migrate_choice_parentheses.py",
        "build_release.py",
    ):
        assert not (PROJECT_ROOT / script_name).exists()
        assert (PROJECT_ROOT / "scripts" / script_name).is_file()


def test_server_holds_restore_runtime_lock_before_database_initialization():
    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")

    lock_call = main_source.index(
        "_RUNTIME_LOCK = None if IS_TESTING else acquire_runtime_lock()"
    )
    database_init = main_source.index("\ninit_db()", lock_call)
    assert lock_call < database_init
    assert "atexit.register(_RUNTIME_LOCK.close)" in main_source[lock_call:database_init]


def test_lifespan_owns_post_startup_maintenance(monkeypatch):
    import main

    calls = []

    class FakeTasks:
        def start_maintenance(self, *, interval_seconds):
            calls.append(("task_maintenance", interval_seconds))

        def shutdown(self, *, wait):
            calls.append(("task_shutdown", wait))

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            calls.append(("thread_created", target.__name__, name, daemon))

        def start(self):
            calls.append(("thread_started",))

    monkeypatch.setattr(main, "IS_TESTING", False)
    monkeypatch.setattr(main, "DOCUMENT_TASKS", FakeTasks())
    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    async def exercise_lifespan():
        async with main.app_lifespan(main.app):
            calls.append(("app_ready",))

    asyncio.run(exercise_lifespan())

    assert calls == [
        ("task_maintenance", 60.0),
        (
            "thread_created",
            "start_startup_cleanup",
            "mathbank-post-startup-maintenance",
            True,
        ),
        ("thread_started",),
        ("app_ready",),
        ("task_shutdown", False),
    ]


def test_post_startup_maintenance_backs_up_before_mutating(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: calls.append("sleep"))
    monkeypatch.setattr(
        main, "create_full_backup_if_due", lambda: calls.append("backup")
    )
    monkeypatch.setattr(
        main, "heal_database_curriculum_names", lambda: calls.append("heal")
    )
    monkeypatch.setattr(main, "clean_orphaned_images", lambda: calls.append("clean"))
    monkeypatch.setattr(
        main, "recalibrate_usage_counts", lambda: calls.append("recalibrate")
    )
    monkeypatch.setattr(
        main,
        "rebuild_all_missing_fingerprints",
        lambda *_args, **_kwargs: calls.append("fingerprints") or {"backfilled": 0},
    )
    monkeypatch.setattr(
        main, "print_optional_tool_diagnostics", lambda: calls.append("tools")
    )

    main.start_startup_cleanup()

    assert calls == [
        "sleep",
        "backup",
        "heal",
        "clean",
        "recalibrate",
        "fingerprints",
        "tools",
    ]


def test_post_startup_maintenance_stops_mutation_when_backup_fails(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: calls.append("sleep"))

    def fail_backup():
        calls.append("backup")
        raise OSError("backup unavailable")

    monkeypatch.setattr(main, "create_full_backup_if_due", fail_backup)
    monkeypatch.setattr(
        main, "heal_database_curriculum_names", lambda: calls.append("heal")
    )
    monkeypatch.setattr(main, "clean_orphaned_images", lambda: calls.append("clean"))
    monkeypatch.setattr(
        main, "recalibrate_usage_counts", lambda: calls.append("recalibrate")
    )
    monkeypatch.setattr(
        main, "print_optional_tool_diagnostics", lambda: calls.append("tools")
    )

    main.start_startup_cleanup()

    assert calls == ["sleep", "backup", "tools"]


def test_metadata_load_does_not_run_full_database_heal_before_ready():
    metadata_source = (PROJECT_ROOT / "mathbank" / "metadata.py").read_text(encoding="utf-8")
    metadata_start = metadata_source.index("def load_or_init_metadata(")
    metadata_end = metadata_source.index("def get_active_version_code(", metadata_start)
    assert "heal_database_curriculum_names()" not in metadata_source[
        metadata_start:metadata_end
    ]


def test_question_routes_live_in_dedicated_router_and_main_stays_thin():
    import main
    import mathbank.routes_questions as routes_questions

    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    router_source = (
        PROJECT_ROOT / "mathbank" / "routes_questions.py"
    ).read_text(encoding="utf-8")

    # Thin-main invariant: question handlers are NOT defined on main.app.
    assert '@app.get("/api/questions")' not in main_source
    assert "def list_questions(" not in main_source

    # The dedicated router owns the question domain.
    assert '@router.get("/api/questions")' in router_source
    assert "def list_questions(" in router_source
    assert "APIRouter" in router_source

    # The router is wired into the running application. FastAPI nests an included
    # router as a single `_IncludedRouter` object (its real APIRoutes live inside
    # `original_router`), so we verify the nest points at `questions_router`.
    included = [
        route
        for route in main.app.routes
        if type(route).__name__ == "_IncludedRouter"
        and getattr(route, "original_router", None) is main.questions_router
    ]
    assert included, "questions_router was not included in main.app"

    # The canonical list endpoint resolves to the extracted handler.
    list_route = next(
        route
        for route in routes_questions.router.routes
        if getattr(route, "path", None) == "/api/questions"
        and "GET" in getattr(route, "methods", set())
    )
    assert list_route.endpoint.__module__ == "mathbank.routes_questions"
    assert list_route.endpoint.__name__ == "list_questions"

    # main.py stays a thin entry point.
    assert len(main_source.splitlines()) < 4500


def test_ai_and_paper_routes_live_in_dedicated_routers_and_main_stays_thin():
    """AI 域与试卷域下沉护栏（2026-09-17 拆分后锁定）。"""
    import main
    import mathbank.routes_ai as routes_ai
    import mathbank.routes_paper as routes_paper

    main_source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    ai_source = (PROJECT_ROOT / "mathbank" / "routes_ai.py").read_text(encoding="utf-8")
    paper_source = (PROJECT_ROOT / "mathbank" / "routes_paper.py").read_text(
        encoding="utf-8"
    )

    # 领域路由不再定义在 main 上。
    for route_dec in (
        '@app.post("/api/ocr")',
        '@app.post("/api/ai/solve")',
        '@app.post("/api/ai/classify")',
        '@app.post("/api/ai/parse-paper")',
        '@app.post("/api/paper/save")',
        '@app.post("/api/paper/export/word")',
        '@app.post("/api/upload/pdf-task")',
    ):
        assert route_dec not in main_source

    assert '@router.post("/api/ocr")' in ai_source
    assert '@router.post("/api/ai/solve")' in ai_source
    assert '@router.post("/api/ai/classify")' in ai_source
    assert '@router.post("/api/ai/parse-paper")' in paper_source
    assert '@router.post("/api/paper/save")' in paper_source
    assert '@router.post("/api/paper/export/word")' in paper_source
    assert '@router.post("/api/upload/pdf-task")' in paper_source

    # 路由已挂载到运行中的应用。
    for router_attr, module in (
        ("ai_router", routes_ai),
        ("paper_router", routes_paper),
    ):
        included = [
            route
            for route in main.app.routes
            if type(route).__name__ == "_IncludedRouter"
            and getattr(route, "original_router", None) is getattr(main, router_attr)
        ]
        assert included, f"{router_attr} was not included in main.app"
        assert module.router is getattr(main, router_attr)

    # main.py 保持薄入口（拆分前约 4,400 行）。
    assert len(main_source.splitlines()) < 2000


def test_redundant_main_reexports_removed():
    import main
    import mathbank.routes_questions as routes_questions

    # These symbols were previously re-exported by main.py only to forward them to
    # domain routers/tests, but are unused by main.py itself. They must not be
    # re-introduced as main-module attributes (routers/tests import them from their
    # real source modules instead).
    removed_reexports = [
        "LessonPlan",
        "normalize_topic_list",
        "topic_id_for",
        "topic_id_to_name",
        "PANDOC_INSTALL_MANAGER",
        "pandoc_status",
        "export_database_to_files",
        "load_or_create_local_token",
        "require_local_token",
        "METADATA_FILE",
        "set_metadata_cache",
        "normalize_metadata_curriculum",
        "parse_version_tuple",
        "DEFAULT_APPLICABLE_GRADES",
        "DEFAULT_DIFFICULTIES",
        "build_default_metadata",
        "get_curriculum_preset",
        "set_current_curriculum",
        "DATA_BACKUP_DIR",
        "SYSTEM_GENERATED_DIR",
        "write_private_text_atomic",
    ]
    for name in removed_reexports:
        assert not hasattr(main, name), f"redundant re-export {name!r} still on main"

    # deepcopy was an unused import in main.py.
    assert not hasattr(main, "deepcopy")

    # routes_questions no longer carries unused `app` / `mathbank` imports.
    assert not hasattr(routes_questions, "app")
    assert not hasattr(routes_questions, "mathbank")


def test_routes_questions_imports_helpers_from_source_modules():
    import ast

    import mathbank.routes_questions as routes_questions

    src = open(routes_questions.__file__, encoding="utf-8").read()
    tree = ast.parse(src)

    main_imports = set()
    mathbank_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = {alias.asname or alias.name for alias in node.names}
            if mod == "main":
                main_imports |= names
            elif mod.startswith("mathbank"):
                mathbank_imports |= names

    # Only symbols genuinely defined on the app entrypoint (main) may be imported
    # from `main`; every shared helper must come from its owning mathbank.* module.
    allowed_from_main = {
        "TMP_UPLOAD_DIR",
        "UPLOAD_DIR",
        "UPLOAD_DIR_REL",
        "normalize_fillin_macro",
    }
    assert main_imports == allowed_from_main, (
        f"routes_questions imports {sorted(main_imports - allowed_from_main)} "
        f"from main; they should be imported from their source module instead"
    )

    # Sanity: the shared helpers really are reachable from mathbank.* (not main).
    for name in (
        "Question",
        "get_db",
        "get_metadata_cache",
        "build_question_fingerprint",
        "load_question_topics",
        "resolve_upload_asset",
    ):
        assert name in mathbank_imports, f"{name} should be imported from mathbank.*"

