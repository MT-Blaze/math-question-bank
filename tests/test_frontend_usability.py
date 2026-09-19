import os
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
INDEX_PATH = STATIC_DIR / "index.html"
CSS_PATH = STATIC_DIR / "css" / "app.css"
JS_FILES = tuple(
    STATIC_JS_DIR / name
    for name in ("api.js", "editor.js", "ocr.js", "import.js", "paper.js")
)


def _run_node_script(script: str) -> subprocess.CompletedProcess:
    """Run a generated Node script through a temporary file.

    Windows caps the command line at roughly 32k characters, so handing a large
    extracted JS payload to ``node -e`` fails with WinError 206 (filename or
    extension too long).  Writing the script to a temp file removes that limit
    without weakening the regression in any way.

    ``encoding="utf-8"`` is not cosmetic: node prints UTF-8, and on a CP936
    console the reader thread dies with UnicodeDecodeError, leaving
    ``result.stdout`` as ``None`` — the assertion then reads like a broken test
    instead of a broken frontend.
    """

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    )
    try:
        with handle:
            handle.write(script)
        return subprocess.run(
            [node, handle.name],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _escape_html_helper_source() -> str:
    """Shared escapeHtml helper that api.js provides before editor.js loads."""
    api_source = _read(STATIC_JS_DIR / "api.js")
    start = api_source.index("function escapeHtml(str)")
    end = api_source.index("window.escapeHtml = escapeHtml;", start)
    return api_source[start:end]


_ESCAPE_TEXT_STUB = (
    "    escapeText(value) { return String(value)"
    ".replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')"
    ".replace(/\"/g, '&quot;').replace(/'/g, '&#039;'); },\n"
)


class _ElementIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, dict[str, str | None]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.by_id[element_id] = attributes


def _index_elements() -> dict[str, dict[str, str | None]]:
    parser = _ElementIndex()
    parser.feed(_read(INDEX_PATH))
    return parser.by_id


def test_mobile_viewport_and_five_level_script_order_are_preserved():
    index_source = _read(INDEX_PATH)
    expected_order = (
        "/static/js/api.js",
        "/static/js/editor.js",
        "/static/js/ocr.js",
        "/static/js/import.js",
        "/static/js/paper.js",
    )

    assert 'name="viewport"' in index_source
    assert "width=device-width, initial-scale=1.0" in index_source
    positions = [index_source.index(script_path) for script_path in expected_order]
    assert positions == sorted(positions)


def test_editor_preview_converts_exam_zh_paren_after_protecting_math_blocks():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    css_source = _read(CSS_PATH)

    helper_start = editor_source.index("function transformExamZhParenForPreview(text)")
    helper_end = editor_source.index("function preprocessFormulaForKaTeX(text)", helper_start)
    helper_source = editor_source[helper_start:helper_end]

    assert r"/\\paren\b/g" in helper_source
    assert 'class="exam-zh-paren-preview"' in helper_source
    assert 'aria-label="选择题作答括号"' in helper_source

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r"""
const rendered = transformExamZhParenForPreview(String.raw`题干 \paren`);
if (rendered.includes(String.raw`\paren`)) {
  throw new Error(`paren macro leaked into preview: ${rendered}`);
}
if (!rendered.includes('class="exam-zh-paren-preview"') || !rendered.includes('（&nbsp;&nbsp;）')) {
  throw new Error(`paren preview markup missing: ${rendered}`);
}
const similarlyNamed = transformExamZhParenForPreview(String.raw`\parent`);
if (similarlyNamed !== String.raw`\parent`) {
  throw new Error(`similarly named command was changed: ${similarlyNamed}`);
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    protect_position = editor_source.index("let tempText = clean;", helper_end)
    strip_position = editor_source.index(
        "tempText = tempText.replace(/<[^>]*>/g, '');", protect_position
    )
    transform_position = editor_source.index(
        "tempText = transformExamZhParenForPreview(tempText);", strip_position
    )
    assert protect_position < strip_position < transform_position

    assert ".exam-zh-paren-preview" in css_source
    assert "float: right;" in css_source
    assert re.search(r"\.choices-grid\s*\{[^}]*clear:\s*both;", css_source, re.DOTALL)


def test_editor_preview_restores_adjacent_math_without_splitting_fillin_lines():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
""" + _escape_html_helper_source() + helper_source + r"""
const fillin = String.raw`\fillin`;
const renderedFillin = String.raw`$\underline{\hspace{1.5cm}}$`;
for (let count = 1; count <= 6; count += 1) {
  const source = `11${fillin.repeat(count)}`;
  const rendered = window.preprocessFormulaForKaTeX(source);
  const expected = `11${renderedFillin.repeat(count)}`;
  if (rendered !== expected) {
    throw new Error(`continuous fillin changed at count ${count}: ${rendered}`);
  }
  if (rendered.includes('@@MATH_PLACEHOLDER_')) {
    throw new Error(`math placeholder leaked at count ${count}: ${rendered}`);
  }
}

for (const source of [
  String.raw`$a$$b$$c$`,
  String.raw`$a$$b$$c$$d$$e$`,
  String.raw`prefix$$a+b$$suffix`,
  String.raw`text $a$ and $b$`,
]) {
  const rendered = window.preprocessFormulaForKaTeX(source);
  if (rendered !== source) {
    throw new Error(`adjacent math changed: ${source} -> ${rendered}`);
  }
  if (rendered.includes('@@MATH_PLACEHOLDER_')) {
    throw new Error(`math placeholder leaked: ${rendered}`);
  }
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_shared_question_preview_pipeline_renders_fillin_through_local_preprocessor():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.renderQuestionPreviewContent = renderQuestionPreviewContent;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
    sanitizeRichHtml(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
let katexCalls = 0;
let choicesCalls = 0;
function renderMathInElement(container, options) {
  katexCalls += 1;
  const serialized = options.delimiters.map(item => `${item.left}:${item.right}:${item.display}`);
  const expected = ['$$:$$:true', '$:$:false', '\\(:\\):false', '\\[:\\]:true'];
  if (JSON.stringify(serialized) !== JSON.stringify(expected)) {
    throw new Error(`unexpected KaTeX delimiters: ${JSON.stringify(serialized)}`);
  }
  if (options.throwOnError !== false) throw new Error('question preview must tolerate KaTeX errors');
}
function adaptChoicesGridLayout(container) {
  choicesCalls += 1;
  if (!container) throw new Error('choices layout received no container');
}
""" + _escape_html_helper_source() + helper_source + r"""
const originalPreprocess = preprocessFormulaForKaTeX;
let preprocessCalls = 0;
preprocessFormulaForKaTeX = function(text) {
  preprocessCalls += 1;
  return originalPreprocess(text);
};
const container = { innerHTML: '' };
const source = String.raw`填空：\fillin ![配图](/static/uploads/a.png) <img src="/static/uploads/b.png">`;
const preparedHtml = window.renderQuestionPreviewContent(
  container,
  source,
  { includeImages: false }
);
if (container.innerHTML.includes(String.raw`\fillin`)) {
  throw new Error(`fillin macro leaked into shared preview: ${container.innerHTML}`);
}
if (!container.innerHTML.includes(String.raw`\underline{\hspace{1.5cm}}`)) {
  throw new Error(`local fillin underline was not generated: ${container.innerHTML}`);
}
if (container.innerHTML.includes('<img') || container.innerHTML.includes('/static/uploads/')) {
  throw new Error(`question images leaked into image-free preview: ${container.innerHTML}`);
}
const reusedContainer = { innerHTML: '' };
const reusedHtml = window.renderQuestionPreviewContent(
  reusedContainer,
  String.raw`这段\fillin不应再预处理`,
  { includeImages: false, preparedHtml: preparedHtml }
);
if (reusedHtml !== preparedHtml || reusedContainer.innerHTML !== preparedHtml) {
  throw new Error('prepared question HTML was not reused exactly');
}
if (preprocessCalls !== 1) {
  throw new Error(`preparedHtml path reran preprocessing: ${preprocessCalls}`);
}
if (katexCalls !== 2 || choicesCalls !== 2) {
  throw new Error(`shared pipeline calls were incomplete: katex=${katexCalls}, choices=${choicesCalls}`);
}
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_paper_preview_separates_choices_before_figure_layout():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    css_source = _read(CSS_PATH)

    helper_start = paper_source.index("function splitChoiceContentForPaperPreview(raw)")
    helper_end = paper_source.index("function formatQuestionContentHtml(", helper_start)
    helper_source = paper_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
const source = String.raw`题干
\begin{choices}
\item A
\item B
\end{choices}

![](/static/uploads/graph.png)`;
const result = splitChoiceContentForPaperPreview(source);
if (result.stemRaw.includes(String.raw`\begin{choices}`)) {
  throw new Error(`choices leaked into stem: ${result.stemRaw}`);
}
if (!result.stemRaw.includes('/static/uploads/graph.png')) {
  throw new Error(`figure was not retained with stem: ${result.stemRaw}`);
}
if (!result.choicesRaw.startsWith(String.raw`\begin{choices}`) || !result.choicesRaw.endsWith(String.raw`\end{choices}`)) {
  throw new Error(`choices block was not preserved: ${result.choicesRaw}`);
}
'''
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    split_position = paper_source.index(
        "const choiceContentParts = isChoiceQuestion"
    )
    stem_render_position = paper_source.index(
        "formatQuestionContentHtml(choiceContentParts.stemRaw", split_position
    )
    choices_render_position = paper_source.index(
        "formatQuestionContentHtml(choiceContentParts.choicesRaw", stem_render_position
    )
    assert split_position < stem_render_position < choices_render_position
    assert 'class="paper-choice-stem-row' in paper_source
    assert 'class="paper-choice-options-row"' in paper_source
    assert ".paper-choice-options-row" in css_source


def test_paper_preview_preserves_images_at_authored_complex_content_positions():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    helper_start = paper_source.index(
        "function shouldPreserveInlinePaperImages(raw)"
    )
    helper_end = paper_source.index("// Init on DOMContentLoaded", helper_start)
    helper_source = paper_source[helper_start:helper_end]

    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"
    script = helper_source + r'''
global.window = {
  parseMarkdownWithMath: value => value,
  MathBankSafe: {
    safeImageUrl: value => value,
    escapeAttribute: value => value,
    sanitizeRichHtml: value => value
  }
};

const complex = String.raw`【课本回顾】
![](/static/uploads/fig12.png)

【知识探究】
\begin{tabular}{|c|c|c|}
 & 思路一 & 思路二 \\
\multirow{2}{*}{第一步} & 证明一 & 证明二 \\
 & 推导一 & 推导二 \\
图形表达 & ![](/static/uploads/fig3.png) & ![](/static/uploads/fig4.png) \\
\end{tabular}`;
const rendered = formatQuestionContentHtml(complex, 42, 'right', false, true);
const first = rendered.indexOf('/static/uploads/fig12.png');
const table = rendered.indexOf(String.raw`\begin{tabular}`);
const third = rendered.indexOf('/static/uploads/fig3.png');
const fourth = rendered.indexOf('/static/uploads/fig4.png');
const tableEnd = rendered.indexOf(String.raw`\end{tabular}`);
if (!(first >= 0 && first < table && table < third && third < fourth && fourth < tableEnd)) {
  throw new Error(`complex image anchors changed: ${rendered}`);
}
if (rendered.includes('data-figure-align-qid')) {
  throw new Error(`inline images were converted into a detached figure group: ${rendered}`);
}

const simple = String.raw`普通右图题

![](/static/uploads/graph.png)`;
const simpleRendered = formatQuestionContentHtml(simple, 43, 'right', false, true);
if (!simpleRendered.includes('data-figure-align-qid="43"')) {
  throw new Error(`legacy trailing figure layout was not preserved: ${simpleRendered}`);
}
'''
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_solution_space_controls_stay_at_the_resizable_zone_top():
    paper_source = _read(STATIC_JS_DIR / "paper.js")
    block_start = paper_source.index("solutionBlankHtml = `")
    block_end = paper_source.index("const itemHtml = `", block_start)
    block_source = paper_source[block_start:block_end]

    zone_position = block_source.index("solution-space-zone")
    controls_position = block_source.index("solution-space-controls", zone_position)
    assert zone_position < controls_position
    assert 'data-solution-space-controls-qid=' in block_source
    assert 'data-solution-space-zone-qid=' in block_source
    assert 'data-solution-space-delta="-1"' in block_source
    assert 'data-solution-space-delta="-0.5"' in block_source
    assert 'data-solution-space-delta="0.5"' in block_source
    assert 'data-solution-space-delta="1"' in block_source
    assert 'inline-flex w-14 justify-center' in block_source

    controls_source = block_source[controls_position:]
    assert "absolute right-2 top-2" in controls_source
    assert "bottom-2" not in controls_source
    assert "min-h-[32px]" not in block_source

    handler_start = paper_source.index(
        "function restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta)"
    )
    handler_end = paper_source.index(
        "window.updateGlobalSolutionSpace", handler_start
    )
    handler_source = paper_source[handler_start:handler_end]
    assert "getBoundingClientRect().top" in handler_source
    assert "scrollParent.scrollTop += shift" in handler_source
    assert "requestAnimationFrame(restoreAnchor)" in handler_source
    assert "nextButton.focus({ preventScroll: true })" in handler_source
    assert handler_source.index("window.renderPaperCanvas()") < handler_source.rindex(
        "restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta)"
    )


def test_static_dialogs_expose_modal_semantics_and_accessible_names():
    elements = _index_elements()
    labelled_dialogs = {
        "aiClassifyModal": "aiClassifyModalTitle",
        "settingsModal": "settingsModalTitle",
        "updateModal": "updateModalTitle",
        "statsModal": "statsModalTitle",
        "lessonEditorModal": "lessonEditorTitle",
        "parsedDuplicateReviewModal": "parsedDuplicateReviewTitle",
        "pdfCropModal": "pdfCropModalTitle",
        "answerTikzWorkbenchModal": "answerTikzWorkbenchTitle",
    }

    for dialog_id, title_id in labelled_dialogs.items():
        attributes = elements[dialog_id]
        assert attributes["role"] == "dialog"
        assert attributes["aria-modal"] == "true"
        assert attributes["aria-hidden"] == "true"
        assert attributes["aria-labelledby"] == title_id
        assert title_id in elements

    # 已改造为内嵌工作区面板的旧弹窗：不能再声明模态语义，否则会把页面
    # 其余内容对辅助技术置为 inert，但仍必须保留可读名称。
    embedded_panels = {"latexImportModal"}
    for panel_id in embedded_panels:
        attributes = elements[panel_id]
        assert attributes["role"] == "region"
        assert attributes["aria-label"]
        assert "aria-modal" not in attributes

    lightbox = elements["imageLightbox"]
    assert lightbox["role"] == "dialog"
    assert lightbox["aria-modal"] == "true"
    assert lightbox["aria-hidden"] == "true"
    assert lightbox["aria-label"]

    workspace_button = elements["workspaceDropdownBtn"]
    assert workspace_button["role"] == "button"
    assert workspace_button["tabindex"] == "0"
    assert workspace_button["aria-haspopup"] == "menu"
    assert workspace_button["aria-expanded"] == "false"
    assert workspace_button["aria-label"]
    for button_id in ("toggleSidebarBtn", "themeDropdownBtn", "darkModeBtn", "statsOpenBtn"):
        assert elements[button_id]["aria-label"]


def test_duplicate_review_has_accessible_decision_controls_and_live_summary():
    elements = _index_elements()
    summary = elements["parsedDuplicateSummary"]
    assert summary["role"] == "status"
    assert summary["aria-live"] == "polite"
    assert summary["aria-atomic"] == "true"

    modal = elements["parsedDuplicateReviewModal"]
    assert modal["aria-describedby"] == "parsedDuplicateReviewDescription"
    for button_id in (
        "duplicateReviewReturnBtn",
        "duplicateReviewSkipBtn",
        "duplicateReviewIndependentBtn",
    ):
        assert button_id in elements

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "window.MathBankModal.open(modal" in import_source
    assert "onEscape: () => closeParsedDuplicateReviewModal('review')" in import_source
    assert "badge.focus({ preventScroll: true })" in import_source


def test_ai_classification_requires_manual_single_or_multi_choice_confirmation():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")
    css_source = _read(CSS_PATH)

    assert "temporaryClassifyData.question_type" not in import_source
    assert "qtypeLabels[data.question_type]" not in import_source
    assert "temporaryClassifyData.question_form === 'choice'" in import_source
    assert "!temporaryClassifyQuestionType" in import_source
    assert "请先确认此题是单选题还是多选题" in import_source
    assert "qtypeSelect.value = temporaryClassifyQuestionType" in import_source
    assert "window.selectClassifiedChoiceType = selectClassifiedChoiceType" in import_source
    assert 'id="recQType"' not in index_source
    assert 'id="choiceTypeConfirm"' in index_source
    assert 'id="classifySingleChoiceBtn"' in index_source
    assert 'id="classifyMultiChoiceBtn"' in index_source
    assert 'role="radiogroup"' in index_source
    assert index_source.count("question-type-choice-check") == 2
    assert "已识别为选择题，请手动确认" in index_source
    assert "确认分类并保存题目" in index_source
    assert '.question-type-choice-button[aria-checked="true"]:hover' in css_source
    assert 'color: #ffffff;' in css_source
    assert '.question-type-choice-button[aria-checked="true"] .question-type-choice-check' in css_source
    assert "button.classList.toggle('bg-brand-50'" not in import_source


def test_modal_manager_traps_focus_handles_escape_and_restores_focus():
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    import_source = _read(STATIC_JS_DIR / "import.js")
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "window.MathBankModal = MathBankModal",
        "const focusableSelector",
        "function resolveInitialFocusTarget(dialog, options = {})",
        "dialog.querySelector('[autofocus]')",
        "const labelledBy = dialog.getAttribute('aria-labelledby')",
        "document.getElementById(labelledBy)",
        "data-modal-initial-focus",
        "focusDialogContext(dialog, options)",
        "previousFocus: document.activeElement",
        "event.key === 'Escape'",
        "event.key !== 'Tab'",
        "if (!entry.dialog.contains(document.activeElement))",
        "!focusable.includes(document.activeElement)",
        "element.inert = isIsolated",
        "restoreTarget.focus({ preventScroll: true })",
    ):
        assert marker in api_source

    assert "dialog.querySelector('[autofocus], [data-modal-close]')" not in api_source
    assert "|| getFocusable(dialog)[0]" not in api_source

    assert api_source.count("window.MathBankModal.open") >= 2
    assert editor_source.count("window.MathBankModal.open") >= 3
    assert import_source.count("window.MathBankModal.open") >= 3
    assert "window.MathBankModal.open(lightbox" in ocr_source
    assert "window.MathBankModal.open(modal" in paper_source


def test_loading_saved_paper_restores_modal_background_interactivity():
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    close_start = paper_source.index("window.closeSavedPapersModal = function ()")
    close_end = paper_source.index("window.openSavedPapersModal = async function ()", close_start)
    close_source = paper_source[close_start:close_end]
    load_start = paper_source.index("window.loadSavedPaper = async function (paperId)")
    load_end = paper_source.index("window.deleteSavedPaper = async function (paperId)", load_start)
    load_source = paper_source[load_start:load_end]

    assert close_source.index("window.MathBankModal.close(modal)") < close_source.index("modal.remove()")
    assert "window.closeSavedPapersModal();" in load_source
    assert "modal.remove()" not in load_source


def test_mobile_layout_touch_targets_and_dialog_panes_have_regression_guards():
    css_source = _read(CSS_PATH)

    for marker in (
        "@media (max-width: 768px)",
        "@media (max-width: 768px), (pointer: coarse)",
        "@media (hover: none), (pointer: coarse)",
        "min-width: 44px",
        "min-height: 44px",
        "height: calc(100dvh - 56px)",
        "#bankWorkspaceSection",
        "#paperWorkspaceSection",
        "#previewSection",
        "#latexImportModalContent",
        "#pdfCropModalContent",
        "#pdfPagesThumbnailsContainer",
        '.question-card button[aria-label="删除题目"]',
        "max-width: calc(100vw - 1rem)",
        ".sidebar-pagination-controls",
        "overflow-x: auto",
        "overscroll-behavior-x: contain",
        ".tikz-workbench-modal",
        "#answerTikzWorkbenchModal",
        ".answer-tikz-entry",
    ):
        assert marker in css_source

    assert "html.init-ws-paper #paperWorkspaceSection { display: flex !important; }" in css_source
    assert "sidebar-pagination-controls" in _read(STATIC_JS_DIR / "editor.js")


def test_shared_tikz_workbench_is_multimodal_contextual_and_persistent():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")
    api_source = _read(STATIC_JS_DIR / "api.js")

    for marker in (
        'id="openContentTikzWorkbenchBtn"',
        'id="contentTikzAssetsPanel"',
        'id="contentTikzAssetsList"',
        'id="openAnswerTikzWorkbenchBtn"',
        'id="answerTikzInstruction"',
        'id="answerTikzReferenceInput"',
        'id="answerTikzReferenceDropZone"',
        'id="answerTikzUseContext"',
        'id="answerTikzWorkbenchCode"',
        'id="insertAnswerTikzWorkbenchBtn"',
        'id="tikzWorkbenchTargetBadge"',
        'role="button" tabindex="0"',
    ):
        assert marker in index_source

    assert "const TikzState = {" in api_source
    assert "contentAssets: []" in api_source
    assert "answerAssets: []" in api_source
    for marker in (
        "window.openTikzWorkbench",
        "window.openContentTikzWorkbench",
        "window.openAnswerTikzWorkbench",
        "window.renderContentTikzAssets",
        "window.registerAutoContentTikzAsset",
        "window.renderAnswerTikzAssets",
        "window.collectAnswerImagePaths",
        "formData.append('reference_image'",
        "formData.append('reference_image_path'",
        "fetch('/api/ai/draw_tikz'",
        "insertMarkdownAtSavedCursor",
        "answer_tikz_assets",
        "content_tikz_assets",
        "tikz_reference_image_path",
        "window.MathBankModal.open(modal",
        "EditorState.isCurrent(tikzWorkbenchState.editorSession)",
    ):
        assert marker in import_source

    for removed_legacy_marker in (
        'id="contentTikzContainer"',
        'id="answerTikzContainer"',
        'id="editContentTikzCode"',
        'id="editAnswerTikzCode"',
        "window.renderContentTikzToImage",
        "window.renderAnswerTikzToImage",
        "fetch('/api/correct_tikz'",
        "fetch('/api/ai/draw_tikz_from_image'",
    ):
        assert removed_legacy_marker not in index_source
        assert removed_legacy_marker not in import_source


def test_ocr_auto_tikz_keeps_original_question_image_as_edit_reference():
    ocr_source = _read(STATIC_JS_DIR / "ocr.js")
    import_source = _read(STATIC_JS_DIR / "import.js")

    for marker in (
        "formData.append('skip_tikz'",
        "window.registerAutoContentTikzAsset",
        "referenceImagePath: data.image_path || ''",
        "registerAutoTikzAsset('content', payload)",
        "setTikzReferencePath(",
        "data.reference_image_path",
        "formData.append('reference_image_path'",
    ):
        assert marker in ocr_source or marker in import_source

    assert "uploadedImages.push(safeReferencePath)" not in import_source
    assert "hiddenTikzReferencePaths" in import_source


def test_add_drawing_and_edit_drawing_are_separate_actions():
    index_source = _read(INDEX_PATH)
    import_source = _read(STATIC_JS_DIR / "import.js")

    assert index_source.count("<span>新增绘图</span>") == 2
    assert "<span>插入绘图</span>" not in index_source
    assert "新增一幅题干 TikZ 绘图，不会覆盖已有绘图" in index_source
    assert "新增一幅解答 TikZ 绘图，不会覆盖已有绘图" in index_source
    assert "window.openContentTikzWorkbench = function(assetId = null)" in import_source
    assert "window.openAnswerTikzWorkbench = function(assetId = null)" in import_source
    assert "? `修改${targetLabel} TikZ 绘图`" in import_source
    assert ": `新增${targetLabel} TikZ 绘图`" in import_source
    assert "TikzState.contentAssets.push(nextAsset)" in import_source
    assert "TikzState.contentAssets.splice(editingIndex, 1, nextAsset)" in import_source


def test_paper_question_answers_are_collapsible_and_loaded_on_demand():
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        "answerCache: Object.create(null)",
        "expandedAnswerIds: new Set()",
        "window.togglePaperQuestionAnswer",
        "window.collapseAllPaperAnswers",
        "fetch(`/api/questions/${qid}`)",
        "window.parseMarkdownWithMath(answerText)",
        'aria-expanded="${answerExpanded ? \'true\' : \'false\'}"',
        "参考答案与解析",
        "收起全部答案",
    ):
        assert marker in paper_source


def test_reduced_motion_dark_contrast_and_busy_feedback_are_explicit():
    css_source = _read(CSS_PATH)
    index_source = _read(INDEX_PATH)
    api_source = _read(STATIC_JS_DIR / "api.js")
    editor_source = _read(STATIC_JS_DIR / "editor.js")

    assert "@media (prefers-reduced-motion: reduce)" in css_source
    assert "animation-duration: 0.01ms !important" in css_source
    assert "transition-duration: 0.01ms !important" in css_source
    assert ".animate-spin" in css_source

    assert ".dark input::placeholder" in css_source
    assert "color: #94A3B8 !important" in css_source
    assert "outline: 2px solid rgb(var(--brand-500-rgb))" in css_source
    assert 'button[aria-busy="true"]' in css_source
    assert 'button[aria-disabled="true"]' in css_source
    assert '#questionsList[aria-busy="true"]' in css_source
    assert '[data-modal-initial-focus="true"]:focus-visible' in css_source

    assert "new MutationObserver" in api_source
    assert "button.setAttribute('aria-busy', 'true')" in api_source
    assert "triggerButton.disabled = true" in editor_source
    assert "triggerButton.setAttribute('aria-busy', 'true')" in editor_source
    assert ".finally(() =>" in editor_source
    assert 'id="toast" role="status" aria-live="polite"' in index_source
    for loading_id in (
        "classifyLoading",
        "importLoadingState",
        "contentOcrLoadingIndicator",
        "ocrLoadingIndicator",
        "aiLoadingIndicator",
    ):
        element = _index_elements()[loading_id]
        assert element["role"] == "status"
        assert element["aria-live"] == "polite"


def test_sidebar_uses_server_pagination_and_latest_request_wins():
    editor_source = _read(STATIC_JS_DIR / "editor.js")
    load_start = editor_source.index("function loadQuestions(retryCount = 0)")
    load_end = editor_source.index("//       SIDEBAR PAGINATION SYSTEM HELPERS", load_start)
    load_source = editor_source[load_start:load_end]

    for marker in (
        "new AbortController()",
        "bankQuestionsLoadController.abort()",
        "const loadSequence = ++bankQuestionsLoadSequence",
        "loadSequence !== bankQuestionsLoadSequence",
        "requestController.signal",
        "err.name === 'AbortError'",
        "params.append('page', String(requestedPage))",
        "params.append('page_size', String(PAGE_LIMIT))",
        "params.append('sort', sortOrder === 'asc' ? 'asc' : 'desc')",
        "Array.isArray(payload.items)",
        "const questions = payload.items",
        "Number(payload.total)",
        "Number(payload.total_pages)",
    ):
        assert marker in load_source

    assert "questions.sort(" not in load_source
    assert "questions.slice(" not in load_source


def test_word_export_prepares_pandoc_once_and_can_continue_in_compatibility_mode():
    index_source = _read(INDEX_PATH)
    paper_source = _read(STATIC_JS_DIR / "paper.js")

    for marker in (
        'id="pandocInstallModal"',
        '安装 Word 可编辑公式组件',
        '安装并继续导出',
        '本次兼容导出',
        'id="pandocInstallProgressBar"',
    ):
        assert marker in index_source

    for marker in (
        "fetch('/api/runtime/pandoc/status')",
        "fetch('/api/runtime/pandoc/install', { method: 'POST' })",
        "pollPandocInstall(state.task_id)",
        "await ensurePandocForWordExport()",
        "await generateAndDownloadWord(payload)",
        "decision === 'compatibility'",
    ):
        assert marker in paper_source


def test_generated_tailwind_classes_use_configured_scales():
    combined_source = "\n".join((_read(INDEX_PATH), *(_read(path) for path in JS_FILES)))
    assert "text-2xs" not in combined_source
    for invalid_shadow in ("shadow-xs", "shadow-2xs", "shadow-3xs"):
        assert invalid_shadow not in combined_source

    index_source = _read(INDEX_PATH)
    brand_start = index_source.index("brand: {")
    brand_block = index_source[brand_start:index_source.index("}", brand_start)]
    configured_brand_steps = {
        int(step)
        for step in re.findall(r"(\d{2,3}):\s*'rgba\(var\(--brand-", brand_block)
    }
    assert {50, 200, 500, 600, 700, 900} <= configured_brand_steps, "brand 色阶配置缺少基础档位"

    used_brand_steps = {
        int(step)
        for step in re.findall(
            r"(?:text|bg|border|ring)-brand-(\d{2,3})(?=[/\s\"'`])",
            combined_source,
        )
    }
    assert used_brand_steps <= configured_brand_steps

    standard_steps = {50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950}
    used_standard_steps = {
        int(step)
        for step in re.findall(
            r"(?:text|bg|border|ring)-(?:slate|gray|red|rose|orange|amber|yellow|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink)-(\d{2,3})(?=[/\s\"'`])",
            combined_source,
        )
    }
    assert used_standard_steps <= standard_steps


def _tree_editor_source() -> str:
    """api.js 中双栏大纲树编辑器（treeEditors ... window.treeAddComp）的源码片段。"""

    api = _read(STATIC_JS_DIR / "api.js")
    return api.split("var treeEditors", 1)[1].split("window.treeAddComp", 1)[0]


def test_metadata_tree_defaults_to_collapsed():
    """知识点树默认闭合：展开判据必须是 `=== true`（未记录即视为收起）。"""

    tree = _tree_editor_source()
    assert "!== false" not in tree
    assert tree.count("=== true") >= 8


def test_metadata_tree_supports_drag_sorting_on_all_levels():
    """册次 / 章节 / 小节 / 考点 四级均支持拖动排序。"""

    api = _read(STATIC_JS_DIR / "api.js")
    for helper in (
        "function treeGrip(",
        "function treeBindDrop(",
        "function treeMoveComp(",
        "function treeMoveChap(",
        "function treeMoveSec(",
        "function treeMoveTopic(",
    ):
        assert helper in api, helper
    tree = _tree_editor_source()
    for kind in ("'comp'", "'chap'", "'sec'", "'topic'"):
        assert "kind: " + kind in tree, kind


def test_legacy_single_pane_tree_does_not_overwrite_dual_pane_container():
    """旧版单栏树不得再渲染到 metadataTree（会覆盖右侧高三专题复习树）。"""

    api = _read(STATIC_JS_DIR / "api.js")
    start = api.index("window.renderMetadataVisualEditors = function()")
    body = api[start:start + 800]
    assert "metadataTreeRender()" not in body


def test_difficulty_gradient_tab_is_wired_into_settings_nav():
    """难度梯度是独立设置页签（导航 + 内容 + 切换逻辑齐全），并以表格呈现。"""

    index_source = _read(INDEX_PATH)
    elements = _index_elements()
    assert "btn-settings-difficulty" in elements
    assert "settings-tab-difficulty" in elements
    assert elements["btn-settings-difficulty"].get("onclick") == "switchSettingsTab('difficulty')"

    api = _read(STATIC_JS_DIR / "api.js")
    assert "btn-settings-difficulty" in api
    assert "settings-tab-difficulty" in api
    assert "tabName === 'difficulty'" in api

    start = index_source.index('id="settings-tab-difficulty"')
    end = index_source.index("<!-- JSON Config Tab Content -->")
    body = index_source[start:end]
    assert "<table" in body
    assert "难度梯度定义（L1–L5）" in body
    for level in ("概念理解", "基础运算", "性质应用", "综合运用", "创新拓展"):
        assert level in body, level
    for behavior in ("能说出", "会代公式", "会由性质求参数", "能拆解综合题", "能探究"):
        assert behavior in body, behavior
    for sub in ("定义、符号、表示", "直接套公式", "含参简单讨论", "标准压轴题", "竞赛 / 强基衔接"):
        assert sub in body, sub
    # 每个细目都带冒号说明
    for note in (
        "知道概念的内涵与外延",
        "进行常规的代数运算、化简与求值",
        "借助单调性、奇偶性等性质",
        "考查综合灵活运用能力的常规难题",
        "面向竞赛或强基计划的拔高题",
    ):
        assert note in body, note


def test_lesson_plan_workspace_is_kaoxiang_tree_driven():
    """教案以考向树为骨架：骨架 / 知识块 / 题目 / 思维导图四件套必须就位。"""

    index_source = _read(INDEX_PATH)

    # 工作区骨架（左侧检索列表 + 右侧文档预览）
    for node_id in (
        "lessonWorkspace",
        "lessonPreviewArea",
        "lessonSearchInput",
        "lessonFilterChips",
        "lessonListContainer",
        "lessonListFooter",
    ):
        assert 'id="' + node_id + '"' in index_source, node_id

    # 编辑器区块：考向骨架级联、知识块、思维导图、六环节
    for node_id in (
        "lessonOutlineCompulsory",
        "lessonOutlineChapter",
        "lessonOutlineSection",
        "lessonBlocks",
        "mindmapEditor",
        "processStagesContainer",
    ):
        assert 'id="' + node_id + '"' in index_source, node_id

    # 教学过程不再让用户手写 JSON
    assert "lessonProcess" not in index_source
    assert "JSON格式，可自定义环节" not in index_source

    lesson = _read(STATIC_JS_DIR / "lesson.js")
    for token in (
        "/api/lesson-plans/from-section",
        "/api/lesson-plans/section-topics",
        "knowledge_content",
        "mindmap",
        "question_refs",
        "buildOutline",
        "rebuildMindmap",
        "addBlock",
        "rowsToTree",
        "treeToRows",
        "exportMarkdown",
    ):
        assert token in lesson, token

    # 题目可增可删：题库检索 + 手动录入 + 移除
    assert "/api/questions?q=" in lesson
    assert "lesson-q-remove" in lesson
    assert "lesson-q-manual-add" in lesson


def test_parsed_card_image_source_badges_distinguish_bitmap_from_tikz():
    """拆题预览的配图角标必须能区分「PDF 位图裁剪」与「AI 重绘 TikZ」。

    背景：用户曾需要反复确认预览里的插图到底是不是 TikZ。角标由文件名判定，
    因此这里把该纯函数抽出来在 node 里跑真实分支。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function describeQuestionImageSource(path) {")
    end = import_source.index("\n        }\n", start) + len("\n        }\n")
    helper = import_source[start:end]

    script = (
        helper
        + """
const cases = [
  ['/static/uploads/tmp/pdf_crop_75824d00-eb64-4970-9e1e-d0e33aed0527_2_0.png', 'crop', '位图 · 第3页'],
  ['/static/uploads/pdf_crop_taskA_0_1.png', 'crop', '位图 · 第1页'],
  ['/static/uploads/tikz_abc123.png', 'tikz', 'AI · TikZ'],
  ['/static/uploads/dc5687b50f70a0eae76.png', 'bitmap', '位图']
];
const previewPage = describeQuestionImageSource(
  '/static/uploads/tmp/pdf_crop_taskA_4_0.png?cache=1#frag'
);
if (previewPage.kind !== 'crop' || previewPage.label !== '位图 · 第5页') {
  throw new Error('query/hash 未剥离: ' + JSON.stringify(previewPage));
}
for (const [path, kind, label] of cases) {
  const got = describeQuestionImageSource(path);
  if (got.kind !== kind || got.label !== label) {
    throw new Error(path + ' -> ' + JSON.stringify(got));
  }
  if (!got.title) throw new Error('缺少 title 提示: ' + path);
}
const empty = describeQuestionImageSource('');
if (empty.kind !== 'bitmap') throw new Error('空路径应回落为位图');
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_tikz_badge_helper_reports_all_four_states():
    """转 TikZ 的角标必须能表达四态：位图 / 已转 TikZ / 转换中 / 转换失败。

    背景：TikZ 转换存在失败率，失败若不显式标出，用户会以为「已经转好了」。
    这里把共享判定器抽到 node 里跑真实分支，锁住状态机与文案。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function describeQuestionImageSource(path) {")
    end = import_source.index("function parsedCardLocalToken() {", start)
    helper = import_source[start:end]
    # 判定器必须集中在同一段里，不能散落到各渲染点
    assert "function describeQuestionImageBadge(" in helper
    assert "function tikzBadgeToneClass(" in helper

    script = (
        "const parsedCardTikzStates = new Map();\n"
        + helper
        + """
const CROP = '/static/uploads/tmp/pdf_crop_taskA_2_0.png';
const base = describeQuestionImageBadge(3, CROP, false);
if (base.tone !== 'bitmap' || base.label !== '位图 · 第3页' || base.canRetry !== true) {
  throw new Error('位图态错误: ' + JSON.stringify(base));
}
setParsedCardTikzState(3, CROP, { status: 'converting' });
const converting = describeQuestionImageBadge(3, CROP, false);
if (converting.tone !== 'converting' || converting.label !== '转换中…' || converting.canRetry !== false) {
  throw new Error('转换中态错误: ' + JSON.stringify(converting));
}
setParsedCardTikzState(3, CROP, { status: 'failed', message: 'xelatex 不可用' });
const failed = describeQuestionImageBadge(3, CROP, false);
if (failed.tone !== 'failed' || failed.label !== '转换失败' || failed.canRetry !== true) {
  throw new Error('失败态错误: ' + JSON.stringify(failed));
}
if (failed.title.indexOf('xelatex 不可用') < 0) {
  throw new Error('失败原因未透出到 tooltip: ' + failed.title);
}
if (describeQuestionImageBadge(3, CROP, true).tone !== 'tikz') {
  throw new Error('已转 TikZ 是持久事实，不得被陈旧的「失败」记录盖住');
}
setParsedCardTikzState(3, CROP, { status: 'converting' });
if (describeQuestionImageBadge(3, CROP, true).tone !== 'converting') {
  throw new Error('转换中的进度必须可见，优先级高于已转');
}
clearParsedCardTikzState(3, CROP);
const restored = describeQuestionImageBadge(3, CROP, false);
if (restored.tone !== 'bitmap') {
  throw new Error('清除瞬时状态后未回到位图态: ' + JSON.stringify(restored));
}
const done = describeQuestionImageBadge(3, CROP, true);
if (done.tone !== 'tikz' || done.label !== 'AI · TikZ' || done.canRetry !== false) {
  throw new Error('已转态错误: ' + JSON.stringify(done));
}
const tones = ['tikz', 'converting', 'failed', 'bitmap'].map(tikzBadgeToneClass);
if (new Set(tones).size !== 4) {
  throw new Error('四态底色未区分: ' + tones.join('|'));
}
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_soft_delete_image_cleans_all_three_places():
    """删一张配图必须三处同步：image_paths、正文引用、指向它的 TikZ 资产。

    少任何一处都会留破绽：只删 image_paths 会在解析里留破图（后端只清题干、
    解答里的引用是特意保留的）；不删 TikZ 资产则 reference_image_path 悬空。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function stripParsedImageReference(")
    end = import_source.index("function parsedCardAssetFor(", start)
    helper = import_source[start:end]
    for token in (
        "function softDeleteParsedCardImage(",
        "function dropParsedTikzAssetsFor(",
        "function parsedCardImageHasTikz(",
    ):
        assert token in helper, token

    script = (
        "globalThis.window = { MathBankSafe: { safeImageUrl: p => (p ? String(p) : '') } };\n"
        "globalThis.document = { getElementById: () => null };\n"
        "const clearedStates = [];\n"
        "function clearParsedCardTikzState(index, safePath) "
        "{ clearedStates.push(index + ':' + safePath); }\n"
        "let parsedQuestionsData = [];\n"
        + helper
        + """
const IMG0 = '/static/uploads/tmp/pdf_crop_taskX_1_0.png';
const IMG1 = '/static/uploads/tmp/pdf_crop_taskX_1_1.png';
parsedQuestionsData = [{
  content: '如图 ![图](' + IMG0 + ') 所示，求角。',
  answer_markdown: '由 ![解答图](' + IMG1 + ') 可得。',
  image_paths: [IMG0, IMG1],
  referenced_images: [IMG0],
  content_tikz_assets: [{ reference_image_path: IMG0, image_path: '/static/uploads/tikz_a.png' }],
  answer_tikz_assets: [{ reference_image_path: IMG1, image_path: '/static/uploads/tikz_b.png' }]
}];

if (softDeleteParsedCardImage(0, IMG0) !== true) throw new Error('删除应返回 true');
const q = parsedQuestionsData[0];
if (q.image_paths.length !== 1 || q.image_paths[0] !== IMG1) {
  throw new Error('image_paths 未摘干净: ' + JSON.stringify(q.image_paths));
}
if (q.content.indexOf('pdf_crop_taskX_1_0.png') >= 0) throw new Error('题干引用未清除');
if (q.answer_markdown.indexOf('pdf_crop_taskX_1_1.png') < 0) throw new Error('解答引用被误删');
if (q.referenced_images.length !== 0) throw new Error('referenced_images 未清理');
if (q.content_tikz_assets.length !== 0) throw new Error('已转 TikZ 资产未连带移除');
if (q.answer_tikz_assets.length !== 1) throw new Error('其它图的 TikZ 资产被误删');
if (clearedStates.length !== 1) throw new Error('未清理转 TikZ 瞬时状态');

if (softDeleteParsedCardImage(0, '/static/uploads/tmp/not-owned.png') !== false) {
  throw new Error('不属于本题的图不应返回 true');
}

// 正文里写成相对路径 / 带 query 的形态，也要按文件名匹配删干净
parsedQuestionsData.push({
  content: '相对路径 ![图](tmp/pdf_crop_taskY_2_3.png?x=1)',
  answer_markdown: '',
  image_paths: ['/static/uploads/tmp/pdf_crop_taskY_2_3.png']
});
softDeleteParsedCardImage(1, '/static/uploads/tmp/pdf_crop_taskY_2_3.png');
if (parsedQuestionsData[1].content.indexOf('pdf_crop_taskY_2_3.png') >= 0) {
  throw new Error('相对路径/带 query 的引用未清除: ' + parsedQuestionsData[1].content);
}
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_parsed_image_overview_is_wired_and_reuses_the_badge_helper():
    """整卷配图总览必须有入口按钮，且角标与批量转 TikZ 复用既有单一实现。"""

    import_source = _read(STATIC_JS_DIR / "import.js")
    html_source = _read(PROJECT_ROOT / "static" / "index.html")

    assert "openParsedImageOverview()" in html_source
    assert "window.openParsedImageOverview = openParsedImageOverview" in import_source

    start = import_source.index("function openParsedImageOverview() {")
    end = import_source.index("function buildParsedCardImageTile(", start)
    overview = import_source[start:end]
    # 角标复用共享判定器（不再各写一份四态规则）
    assert "describeQuestionImageBadge" in overview
    assert "tikzBadgeToneClass" in overview
    # 批量转 TikZ 复用既有的单张转换 + 限流并发
    assert "convertParsedCardImageToTikz" in overview
    assert "await Promise.all([worker(), worker()])" in overview
    # 删除走软删除，并带「已转 TikZ 需二次确认」
    assert "softDeleteParsedCardImage" in overview
    assert "parsedOverviewDeleteArmed" in overview
    # 弹窗控件用 DOM 构建后再挂监听（不经 DOMPurify，但遵循项目既有弹窗写法）
    assert "MathBankModal.open" in overview
    assert "addEventListener" in overview


def test_image_overview_exposes_gallery_and_pages_tabs():
    """审图面板要能「一屏看全所有图」，也能翻「本卷原页」。

    用户明确要求过：不要按题分组的列表（得逐个点开才能看），要把所有图铺成一面
    图墙、每张图直接标出属于第几题或未归属。所以这里是**自适应网格**而非列表。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function openParsedImageOverview() {")
    end = import_source.index("function handleParsedImageOverviewConvert(", start)
    overview = import_source[start:end]

    for tab in ('data-overview-tab="gallery"', 'data-overview-tab="pages"'):
        assert tab in overview, tab
    # 两个页签各自的渲染入口
    assert "renderParsedOverviewGalleryTab(" in overview
    assert "renderParsedOverviewPagesTab(" in overview
    # 图墙用自适应列宽铺开（窗口越宽铺得越多），不是按题分组的竖排列表
    assert "repeat(auto-fill, minmax(" in overview
    # 图墙上每张图直接标出题号 / 未归属
    assert "第 ' + (item.index + 1) + ' 题" in overview or "item.index + 1" in overview
    assert "opts.unassigned" in overview
    # 未归属图混排在图墙里，可指派给某题、也可直接删除
    assert "parsedOverviewAssignTarget" in overview
    assert "handleParsedUnassignedAssign" in overview
    assert "discardParsedUnassignedImages" in overview
    # 没有配图的题要能标出来源页，并能一键跳到原页
    assert "buildParsedOverviewEmptyRow(" in overview
    assert "focusParsedOverviewPage(" in overview
    # 未归属图没有归属题，转 TikZ 无处安放 → 不给转换入口
    assert "allowConvert: false" in overview
    # 整页图拿不到时必须说明原因（任务过期/需重新导入），
    # 而不是静默显示成「没有未归属图」。
    assert "重新导入一次即可" in import_source
    assert "parsedOverviewShowEmpty" in overview


def test_question_card_exposes_source_page_jump():
    """题目卡片上要能直接看到「源自第 N 页」并一键跳到原页。

    「我不知道这道题到底有没有图」是核心痛点，而卡片是用户最先看到的地方，
    不该强迫他先去总览面板里翻。只推断出来源页时才显示这个入口。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "window.openParsedQuestionSourcePage = openParsedQuestionSourcePage" in import_source

    start = import_source.index("function renderParsedQuestionsList(")
    end = import_source.index("function renderParsedCardPreview(", start)
    card_source = import_source[start:end]
    assert "openParsedQuestionSourcePage(" in card_source
    assert "card-source-page" in card_source
    assert "q._source_page" in card_source
    # 触摸目标遵守移动端最小高度约定
    assert "min-h-11" in card_source

    # 未归属图除了「指派给该题」，还必须能直接删除（垃圾图不必强行指派）
    assert "discardParsedUnassignedImages" in import_source
    delete_start = import_source.index("function handleParsedImageOverviewDelete(")
    delete_end = import_source.index("function handleParsedImageOverviewConvert(", delete_start)
    assert "discardParsedUnassignedImages" in import_source[delete_start:delete_end]


def test_parsed_card_does_not_inline_tikz_source_into_content():
    """题干编辑框**不得**拼接 TikZ 源码。

    源码已存在 `content_tikz_assets` 里，导出时 `paper_helper` 会直接取它内联编译成
    矢量图。曾经在渲染卡片时把源码追加到编辑框的 value，带来两条后果：
      ① 预览是从编辑框 value 渲染的 → 整段代码被当正文显示；
      ② 保存时 `content` 直接取编辑框 value → 源码被**永久写进题干 markdown**
         （后续 AI 调用要重读这堆代码，导出时一个括号不匹配就编译失败）。
    需要核对源码时走卡片上的「TikZ 源码」入口。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    card_start = import_source.index("function renderParsedQuestionsList(")
    card_end = import_source.index("function renderParsedCardPreview(", card_start)
    card_source = import_source[card_start:card_end]
    # 拼接逻辑（当时的临时变量名）必须已经消失
    assert "initialContent" not in card_source
    assert "card-content-textarea').value = String(q.content || '')" in card_source

    # 源码入口：卡片模板 + 绑定 + 全局可达
    assert "card-content-tikz-source" in card_source
    assert "card-answer-tikz-source" in card_source
    assert "openParsedCardTikzSource(index, pair[0])" in card_source
    assert "window.openParsedCardTikzSource = openParsedCardTikzSource" in import_source

    # 方案 B：入口必须是绿色成功态，并带上**字符数**
    # —— 字符数是「确实存了源码」最直接的凭据（空的就是 0 字），
    #    用户曾反复怀疑转换是假的，靠这个数字消除疑虑。
    assert "✓ TikZ 源码已存" in card_source
    assert "border-emerald-300" in card_source
    assert "chars" in card_source

    # 只读查看器用 textContent 渲染源码（用 innerHTML 会把源码里的尖括号当 HTML 解析）
    viewer_start = import_source.index("function openParsedCardTikzSource(")
    viewer_end = import_source.index(
        "window.openParsedCardTikzSource = openParsedCardTikzSource", viewer_start
    )
    viewer = import_source[viewer_start:viewer_end]
    assert "pre.textContent = code;" in viewer
    assert "innerHTML" not in viewer

    # 方案 D：弹窗必须摊开「存储事实」——存在哪个字段、多少字、编译产物与原参考图分别是什么。
    # 因为点图只能看到 PNG（给人看的预览），源码是文本、没有图片 URL，
    # 不在这里说清楚，用户永远无法确认「源码到底存没存」。
    assert "源码已存于本题资产" in viewer
    assert "编译产物" in viewer
    assert "原参考图" in viewer
    assert "不是「没存源码」" in viewer


def test_choice_images_have_their_own_slot():
    """选项图必须与题干图区分开（图墙标注「选项图」）。

    选项图跟在 `A．/B．/C．/D．` 后面，属于**选项本身**而不是题干配图；
    前端判据必须与后端 `_CHOICE_LABEL_RE` 同口径，否则会出现
    「后端保留了选项图、前端却把它当普通题干图」这种两边打架的情况。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    assert "PARSED_CHOICE_LABEL_RE" in import_source
    assert "PARSED_IMAGE_ONLY_RE" in import_source
    assert "parsedCardChoiceImagePaths" in import_source
    assert "item.slot === 'choice' ? '选项图'" in import_source
    # choices 环境内 `\item` 行也是选项图（OCR 提示词要求剥离 A-D 标号，
    # 产出 `\item ![插图](...)`，只认标号的判据会漏掉它）。
    assert "PARSED_CHOICES_ITEM_RE" in import_source

    # 判定顺序：解答 > 选项 > 题干
    slot_start = import_source.index("function parsedCardImageSlot(")
    slot_end = import_source.index("function convertParsedCardImageToTikz(", slot_start)
    slot_source = import_source[slot_start:slot_end]
    assert slot_source.index("'answer'") < slot_source.index("'choice'")

    # 与后端同口径（选项行判据 + 保留选项图的剥离器 + 裸标号行 + choices 内 \item）
    pipeline = _read(PROJECT_ROOT / "mathbank" / "parsing_pipeline.py")
    assert "_CHOICE_LABEL_RE" in pipeline
    assert "_BARE_CHOICE_LABEL_RE" in pipeline
    assert "_strip_image_reference" in pipeline
    assert "_CHOICES_ITEM_RE" in pipeline
    assert "_choices_env_state" in pipeline


def test_choices_env_item_images_are_classified_as_choice_slot():
    r"""choices 环境内 `\item` 行上的图必须归到「选项图」槽。

    整页 OCR 的提示词要求剥离 A/B/C/D 标号，产出 `\item ![插图](...)`；
    只认标号的判据认不出来，图会被错标成「题干图」。这里把该函数抽到 node
    里跑真实代码，锁住与后端同口径的第三种形态。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    # 从第一个选项判据常量起切，把 PARSED_* 正则与函数一起带进沙箱。
    start = import_source.index("const PARSED_CHOICE_LABEL_RE")
    end = import_source.index("// 选项横排（仅预览层", start)
    source = import_source[start:end]

    script = (
        "const window = { MathBankSafe: { safeImageUrl: (u) => (u ? String(u) : '') } };\n"
        + source
        + "\n"
        # ① OCR 形态：choices 内 \item 行上的图 -> 选项图
        "const ocr = '题干（ ）\\n\\n\\\\begin{choices}\\n"
        "\\\\item ![插图](tmp/a.png)\\n"
        "\\\\item ![插图](tmp/b.png)\\n"
        "\\\\end{choices}';\n"
        "const got = parsedCardChoiceImagePaths(ocr);\n"
        "if (got.length !== 2) throw new Error('choices 内 \\\\item 图应全部归为选项图: ' + JSON.stringify(got));\n"
        # ② choices 环境之外的普通题干配图不能算选项图
        "const outside = '题干见图 ![插图](tmp/c.png)\\n\\n"
        "\\\\begin{choices}\\n\\\\item 甲\\n\\\\item 乙\\n\\\\end{choices}';\n"
        "if (parsedCardChoiceImagePaths(outside).length !== 0) throw new Error('环境外配图被误判为选项图');\n"
        # ③ 环境之后再出现的游离 \\item 行也不算（只认环境内）
        "const after = '\\\\begin{choices}\\n\\\\item 甲\\n\\\\item 乙\\n\\\\end{choices}\\n\\n"
        "\\\\item ![插图](tmp/d.png)';\n"
        "if (parsedCardChoiceImagePaths(after).length !== 0) throw new Error('环境外的 \\\\item 行被误判');\n"
        # ④ 旧形态仍然有效：A．![](url) 同行
        "const inline = '题干\\nA．![插图](tmp/e.png) B．2 C．3 D．4';\n"
        "if (parsedCardChoiceImagePaths(inline).length !== 1) throw new Error('A．同行形态失效');\n"
    )

    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr


def test_plain_choice_paragraphs_are_grouped_into_choices_grid():
    """竖排的选项段落要在预览 DOM 上重组进 .choices-grid 四列自适应网格。

    MinerU/拆题产出的选项是「A 一段 / 图形一段 / B 一段 / …」，渲染成
    <p> 后一行一个选项，A4 与预览都浪费纵向空间；项目里已有 .choices-grid
    网格与选项图角标装饰，但只认 \\begin{choices} 源，普通文本选项触发不了。
    重组必须足够保守：连续 ≥3 个选项段（缺 A/B/C 之一不重组），
    「A、B 两点」这类普通正文绝不能被误判成选项。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("const PARSED_CHOICE_LABEL_RE")
    end = import_source.index("function parsedCardImageSlot(", start)
    source = import_source[start:end]
    for token in (
        "function groupParsedCardOptionParagraphs(",
        "PARSED_BARE_CHOICE_LABEL_RE",
        "choices-grid",
        "data-preferred-columns",
    ):
        assert token in source, token

    script = (
        # —— 最小 DOM 桩：只实现分组函数用到的行为 ——
        "class FakeEl {\n"
        "  constructor(tag, text, hasImg) {\n"
        "    this.tagName = tag; this.nodes = []; this._text = text || '';\n"
        "    this._hasImg = !!hasImg; this.parentNode = null;\n"
        "  }\n"
        "  get children() { return this.nodes; }\n"
        "  get firstChild() { return this.nodes[0] || null; }\n"
        "  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }\n"
        "  insertBefore(n, ref) {\n"
        "    const i = this.nodes.indexOf(ref);\n"
        "    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);\n"
        "    n.parentNode = this; return n;\n"
        "  }\n"
        "  removeChild(n) { const i = this.nodes.indexOf(n); if (i >= 0) this.nodes.splice(i, 1); return n; }\n"
        "  querySelector(sel) { return (sel === 'img' && this._hasImg) ? {} : null; }\n"
        "  querySelectorAll() { return []; }\n"
        "  setAttribute() {}\n"
        "  cloneNode() { return new FakeEl(this.tagName, this._text, false); }\n"
        "  get textContent() { return this._text; }\n"
        "  set textContent(v) { this._text = String(v); this.nodes = []; }\n"
        "}\n"
        + source.replace(
            "function groupParsedCardOptionParagraphs(container) {",
            "function groupParsedCardOptionParagraphs(container) {\n"
            "  const _gridMarker = (el) => { el._grid = true; return el; };\n"
            "  if (typeof document === 'undefined') {\n"
            "    globalThis.document = { createElement: (tag) => _gridMarker(new FakeEl(tag.toUpperCase())) };\n"
            "  }\n"
            "  return _groupParsedCardOptionParagraphsInner(container);\n"
            "}\n"
            "function _groupParsedCardOptionParagraphsInner(container) {",
        )
    ) + (
        "const container = new FakeEl('DIV');\n"
        "const p = (t, img) => { const el = new FakeEl('P', t, img); container.appendChild(el); return el; };\n"
        "// 题干 + 「A/图形/B/图形/C/图形/D/图形」竖排选项（本卷第 1 题真实形态）\n"
        "p('下列各曲线中，不能表示 $y$ 是 $x$ 的函数的是（ ）');\n"
        "const a1 = p('A'); const a2 = p('', true);\n"
        "const b1 = p('B'); const b2 = p('', true);\n"
        "const c1 = p('C'); const c2 = p('', true);\n"
        "const d1 = p('D'); const d2 = p('', true);\n"
        "groupParsedCardOptionParagraphs(container);\n"
        "const grids = container.nodes.filter(n => n._grid);\n"
        "if (grids.length !== 1) throw new Error('应恰好重组出 1 个网格: ' + grids.length);\n"
        "const grid = grids[0];\n"
        "if (grid.nodes.length !== 4) throw new Error('网格应有 4 个选项单元: ' + grid.nodes.length);\n"
        "if (grid.nodes[0].textContent !== 'A．') throw new Error('裸标号应补全为 A．: ' + grid.nodes[0].textContent);\n"
        "if (grid.nodes[3].textContent !== 'D．') throw new Error('第 4 项标号不对');\n"
        "// 网格要插在第一个选项段原来的位置（题干之后）\n"
        "if (container.nodes[0] !== container.nodes.find(n => n._text && n._text.indexOf('下列') === 0)) {\n"
        "  throw new Error('题干应仍在网格之前');\n"
        "}\n"
        "if (container.nodes[container.nodes.length - 1]._grid !== true) {\n"
        "  throw new Error('网格应在容器末尾（原选项段位置）');\n"
        "}\n"
        "// 「A、B 两点」这类正文不得误判：两个连续标号段 < 3 不重组\n"
        "const caseText = new FakeEl('DIV');\n"
        "caseText.appendChild(new FakeEl('P', 'A、B 两点共线'));\n"
        "caseText.appendChild(new FakeEl('P', 'C、D 也在直线上'));\n"
        "groupParsedCardOptionParagraphs(caseText);\n"
        "if (caseText.nodes.some(n => n._grid)) throw new Error('非选项正文被误重组');\n"
        "// 整行选项（A．106 B．105 C．104 D．103 同一段）保持原样，不拆不重组\n"
        "const caseInline = new FakeEl('DIV');\n"
        "caseInline.appendChild(new FakeEl('P', 'A．106 B．105 C．104 D．103'));\n"
        "groupParsedCardOptionParagraphs(caseInline);\n"
        "if (caseInline.nodes.some(n => n._grid)) throw new Error('单段多选项不应被拆');\n"
        "console.log('OK');\n"
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_unassigned_images_are_derived_from_task_assets():
    """「未归属图」= 任务临时资产里的裁剪图 − 已被题目引用的那些（按 basename 比对）。

    整页原图（pdf_page_*）必须排除，它归「本卷原页」页签；顺序要按
    「页序_序号」数值排，否则多页任务下列表顺序会漂。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")
    start = import_source.index("function parsedImageBasename(")
    end = import_source.index("function parsedCardAssetFor(", start)
    helper = import_source[start:end]
    for token in (
        "function collectParsedUnassignedImages(",
        "function collectParsedPageImages(",
        "function assignParsedUnassignedImages(",
        "function collectParsedImageOverview(",
    ):
        assert token in helper, token

    script = (
        "globalThis.window = { MathBankSafe: { safeImageUrl: p => (p ? String(p) : '') } };\n"
        "function parsedCardImageSlot() { return 'content'; }\n"
        "function parsedCardImageHasTikz() { return false; }\n"
        "let parsedQuestionsData = [];\n"
        + helper
        + """
const A = '/static/uploads/tmp/pdf_crop_taskZ_0_1.png';
const B = '/static/uploads/tmp/pdf_crop_taskZ_0_2.png';
const C = '/static/uploads/tmp/pdf_crop_taskZ_1_0.png';
const PAGE = '/static/uploads/tmp/pdf_page_taskZ_0.png';

window.pdfTempAssets = [PAGE, A, B, C];
window.pdfPageImages = [PAGE];
parsedQuestionsData = [{ content: '题干', answer_markdown: '', image_paths: [A] }];

const unassigned = collectParsedUnassignedImages();
if (unassigned.join(',') !== [B, C].join(',')) {
  throw new Error('未归属图算错（应排除已引用与整页图并按文档序）: ' + unassigned.join(','));
}
if (collectParsedPageImages().length !== 1) throw new Error('整页图未收集到');

// 指派：加进该题 image_paths；已在题上的图不重复添加
if (assignParsedUnassignedImages(0, [B, A]) !== 1) throw new Error('重复图不应被再次加入');
if (parsedQuestionsData[0].image_paths.length !== 2) throw new Error('指派结果不对');
const after = collectParsedUnassignedImages();
if (after.join(',') !== C) {
  throw new Error('指派后未归属图应只剩 C: ' + after.join(','));
}

// 题干里写成相对路径的引用，也要按 basename 认出来（否则会重复列为未归属）
parsedQuestionsData = [{ content: '题干', answer_markdown: '', image_paths: ['tmp/pdf_crop_taskZ_0_2.png'] }];
const relativeOwned = collectParsedUnassignedImages();
if (relativeOwned.join(',') !== [A, C].join(',')) {
  throw new Error('相对路径引用未被识别: ' + relativeOwned.join(','));
}

// 放弃未归属图：从本次会话的资产清单里摘掉，重开面板也不该再出现
window.pdfTempAssets = [PAGE, A, B, C];
parsedQuestionsData = [{ content: '题干', answer_markdown: '', image_paths: [A] }];
if (discardParsedUnassignedImages([C]) !== 1) throw new Error('放弃计数不对');
if (collectParsedUnassignedImages().join(',') !== B) {
  throw new Error('放弃后未归属图应只剩 B: ' + collectParsedUnassignedImages().join(','));
}
if (window.pdfTempAssets.indexOf(C) >= 0) throw new Error('资产清单里仍留着已放弃的图');
if (discardParsedUnassignedImages(['/static/uploads/tmp/pdf_crop_nope_0_0.png']) !== 0) {
  throw new Error('放弃不存在的图应返回 0');
}
window.pdfTempAssets = [PAGE, A, B, C];

// 题库为空时，全部裁剪图都是未归属（整页图仍需排除）
parsedQuestionsData = [];
if (collectParsedUnassignedImages().length !== 3) throw new Error('空题库时未归属图数量不对');

// 任务没有登记临时资产时不得抛错，返回空数组
window.pdfTempAssets = [];
if (collectParsedUnassignedImages().length !== 0) throw new Error('无资产清单时应返回空');
console.log('OK');
"""
    )
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_parsed_card_image_strip_offers_tikz_conversion_and_marks_converted():
    """配图条必须给位图「转 TikZ」入口，且已重绘的位图改显 TikZ 渲染结果。"""

    import_source = _read(STATIC_JS_DIR / "import.js")

    assert "/api/ai/draw_tikz" in import_source
    assert "/api/render_tikz" in import_source
    assert "window.convertParsedCardImageToTikz = convertParsedCardImageToTikz" in import_source
    assert "appendParsedCardImageStrip(contentPrev, q, index, answerText)" in import_source

    tile_start = import_source.index("function buildParsedCardImageTile(")
    tile_end = import_source.index("function appendParsedCardImageStrip(", tile_start)
    tile_source = import_source[tile_start:tile_end]
    # 未重绘 -> 显示转 TikZ 按钮；已重绘 -> 显示编译结果并标 AI · TikZ
    assert "转 TikZ" in tile_source
    assert "已转为 TikZ" in tile_source
    assert "reference_image_path: safePath" in import_source
    # 四态角标（位图 / AI·TikZ / 转换中 / 转换失败）统一由共享判定器产出，
    # 渲染点只负责取 tone/label/title——规则集中在一处才不会分叉。
    assert "describeQuestionImageBadge" in tile_source
    assert "tikzBadgeToneClass" in tile_source
    assert "badgeInfo.label" in tile_source
    # 缩略图不得再走 innerHTML 拼接（<button>/data-* 会被 DOMPurify 剥掉）
    assert "card-img-to-tikz" in tile_source
    assert "createElement('button')" in tile_source

    # 解答预览里的插图同样要标注来源并可直接转 TikZ
    assert "decorateParsedCardAnswerImages(answerPrev, q, index)" in import_source
    deco_start = import_source.index("function decorateParsedCardAnswerImages(")
    deco_end = import_source.index("function renderParsedCardPreview(", deco_start)
    deco_source = import_source[deco_start:deco_end]
    assert "card-img-to-tikz" in deco_source
    assert "describeQuestionImageBadge" in deco_source
    assert "tikzBadgeToneClass" in deco_source


def test_stem_and_answer_figures_are_routed_to_their_own_slots():
    """题干有图、解答也有图时：归属判定必须复用既有规则，并分槽展示与登记。

    DB 只有一个扁平 image_paths（题干图与解答图混存），因此归属只能在渲染/登记时
    按「是否出现在解答 markdown 里」派生——这与 editor.js / import.js 的保存路径
    使用的 collectAnswerImagePaths 是同一条规则。
    """

    import_source = _read(STATIC_JS_DIR / "import.js")

    # 1) 复用既有规则，不另造一套
    assert "window.collectAnswerImagePaths" in import_source
    helper_start = import_source.index("function parsedCardAnswerImagePaths(answerText)")
    helper_end = import_source.index("function parsedCardImageSlot(", helper_start)
    helper_source = import_source[helper_start:helper_end]
    assert "collectAnswerImagePaths" in helper_source
    # 切片沙箱里 window 未必带该函数，必须做存在性守卫
    assert "typeof window.collectAnswerImagePaths !== 'function'" in helper_source

    # 2) 题干配图条剔除解答图（解答区已内联呈现，不再重复）
    strip_start = import_source.index("function appendParsedCardImageStrip(")
    strip_end = import_source.index("function decorateParsedCardAnswerImages(", strip_start)
    strip_source = import_source[strip_start:strip_end]
    assert "parsedCardAnswerImagePaths(answerText)" in strip_source
    assert "!answerOwned.has(safePath)" in strip_source

    # 3) 卡片上的配图徽章同样剔除解答图
    assert "parsedCardAnswerImagePaths(q.answer_markdown)" in import_source
    assert ".filter(path => !answerOwned.has(path))" in import_source

    # 4) 转 TikZ 按归属写入对应槽位，且沿用同一 assetsKey 做幂等更新
    convert_start = import_source.index("async function convertParsedCardImageToTikz(")
    convert_end = import_source.index("window.convertParsedCardImageToTikz", convert_start)
    convert_source = import_source[convert_start:convert_end]
    assert "slot === 'answer' ? 'answer' : 'content'" in convert_source
    assert "'answer_tikz_assets'" in convert_source
    assert "'content_tikz_assets'" in convert_source
    assert "q[assetsKey] = assets" in convert_source
    # 题干条传 content、解答区传 answer
    assert "buildParsedCardImageTile(safePath, assetByRef[safePath], index, 'content')" in import_source
    assert "convertParsedCardImageToTikz(index, src, 'answer')" in import_source

    # 5) 手动裁图也能投放到解答：默认题干，可在弹窗里切换
    assert "window.pdfCropTargetSlot = 'content'" in import_source
    assert "window.setPdfCropTarget = function(slot)" in import_source
    assert "window.setPdfCropTarget('content');" in import_source  # 每次打开弹窗复位
    crop_start = import_source.index("window.setPdfCropTarget = function(slot)")
    crop_end = import_source.index("// Crop Selection variables", crop_start)
    crop_source = import_source[crop_start:crop_end]
    assert "aria-pressed" in crop_source
    assert "'pdfCropTargetContent'" in crop_source
    assert "'pdfCropTargetAnswer'" in crop_source

    index_source = _read(STATIC_DIR / "index.html")
    for element_id in ("pdfCropTargetToggle", "pdfCropTargetContent", "pdfCropTargetAnswer"):
        assert f'id="{element_id}"' in index_source, element_id
    assert 'onclick="setPdfCropTarget(\'answer\')"' in index_source



def test_import_logs_console_lives_in_left_nav_bottom():
    """解析日志应位于左侧导航栏底部（用户箭头指定的红色区域）。

    先在右侧预览区待过，后按用户截图要求迁回左侧导航栏底部、主题切换按钮上方。
    """

    index_source = _read(STATIC_DIR / "index.html")
    import_source = _read(STATIC_JS_DIR / "import.js")

    nav = index_source.index('id="globalNav"')
    nav_bottom = index_source.index('class="nav-bottom')
    log_panel = index_source.index('id="importLogPanel"')
    console = index_source.index('id="importLogsConsole"')
    review = index_source.index('id="importReviewPane"')
    wrapper = index_source.index('id="parsedQuestionsWrapper"')

    # 位置：必须在左侧导航栏底部的 nav-bottom 内，不能在右侧预览区
    assert nav < nav_bottom < log_panel < console
    assert log_panel < review
    assert log_panel < wrapper

    # 旧右侧高面板（min-h-[18rem]）已移除；旧左侧 h-24 / max-w-md 控制台也不存在
    assert "min-h-[18rem]" not in index_source
    assert 'max-w-md bg-slate-50 rounded-xl p-3 text-left font-mono text-[10px] text-slate-600 overflow-y-auto h-24' not in index_source

    panel_markup = index_source[log_panel:console]
    console_markup = index_source[console:console + 500]

    # 保持日志语义与可访问性
    assert 'role="log"' in console_markup
    assert 'aria-live="polite"' in console_markup

    # 字体调大、不加粗（用户明确要求）；左侧深色背景适配
    assert "font-mono text-xs" in panel_markup or "font-mono text-xs" in console_markup
    assert "font-bold" not in panel_markup

    # 有折叠按钮与终端图标，便于在窄栏里展开/收起
    assert "fa-terminal" in panel_markup
    assert 'onclick="toggleImportLogPanel()"' in panel_markup

    # 显隐同步：拆解期间与结果审查期间都显示，仅初始空态隐藏
    assert "function setupImportLogPanelSync()" in import_source
    assert "setupImportLogPanelSync();" in import_source
    assert "function toggleImportLogPanel()" in import_source
    sync_start = import_source.index("function setupImportLogPanelSync()")
    sync_end = import_source.index("// App Initialization", sync_start)
    sync_source = import_source[sync_start:sync_end]
    assert "importLoadingState" in sync_source
    assert "parsedQuestionsWrapper" in sync_source
    assert "isLoading || hasResults" in sync_source
    assert "MutationObserver" in sync_source
    assert "attributeFilter: ['class']" in sync_source

    # 日志行一律不加粗，只用颜色区分状态
    log_start = import_source.index("function appendImportLog(")
    log_end = import_source.index("function runAIPaperParse()", log_start)
    log_source = import_source[log_start:log_end]
    assert "font-semibold" not in log_source
    assert "font-bold" not in log_source
    assert "text-emerald-600" in log_source
    assert "text-red-500" in log_source


def test_markdown_pipe_table_keeps_structure_without_inline_style():
    """Markdown 管线表格必须靠 CSS 类出框线，不能靠内联 style。

    背景：用户反馈"表格不对"——渲染出来是一行挤在一起的文字。根因是表格 HTML 的
    边框/内边距全写在 style 属性里，而 MathBankSafe 的 DOMPurify 白名单
    (RICH_HTML_CONFIG.ALLOWED_ATTR) 不含 style，内联样式被静默剥掉。
    附带约束：AI/OCR 常把分隔行的列数写少（表头 6 格 / 分隔行 5 格），
    必须补齐而不是错位。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    css_source = _read(CSS_PATH)

    # 1) 单一构造器：两条管线都必须走它
    assert editor_source.count("function buildPipeTableHtml(") == 1
    assert editor_source.count("buildPipeTableHtml(") == 3  # 1 处定义 + 2 处调用
    builder_start = editor_source.index("function buildPipeTableHtml(")
    builder_end = editor_source.index("function convertTabularEnvs(", builder_start)
    builder_source = editor_source[builder_start:builder_end]
    assert 'class="md-table-wrap"' in builder_source
    assert 'class="md-table"' in builder_source
    # 构造器里不允许出现内联样式
    assert "style=" not in builder_source

    # 2) 全文件不得再有"表格 + 内联 style"的产出
    assert not re.search(r"<table[^>]*style=", editor_source)
    assert not re.search(r"<t[hd] style=", editor_source)

    # 3) 样式由 app.css 提供
    assert ".md-table th," in css_source
    assert ".md-table td {" in css_source
    assert "border: 1px solid #cbd5e1;" in css_source

    # 4) 跑真实预处理：截图里那张 6 列 / 5 格分隔行的表
    helper_start = editor_source.index("function transformFillinMacro(clean)")
    helper_marker = "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;"
    helper_end = editor_source.index(helper_marker, helper_start) + len(helper_marker)
    helper_source = editor_source[helper_start:helper_end]

    script = r"""
global.window = {
  MathBankSafe: {
    safeImageUrl(value) { return value; },
    escapeAttribute(value) { return value; },
    sanitizeRichHtml(value) { return value; },
""" + _ESCAPE_TEXT_STUB + r"""  },
};
""" + _escape_html_helper_source() + helper_source + r"""
const screenshotTable = [
  '| $x$ | 10 | 15 | 20 | 25 | 30 |',
  '| :---: | :---: | :---: | :---: | :---: |',
  '| $Q(x)$ | 50 | 55 | 60 | 55 | 50 |',
].join(String.fromCharCode(10));
const rendered = window.preprocessFormulaForKaTeX(screenshotTable);
if (!rendered.includes('<table class="md-table">')) {
  throw new Error('表格未使用 md-table 类: ' + rendered);
}
if (rendered.includes('style=')) {
  throw new Error('表格仍依赖内联 style（会被 DOMPurify 剥掉）: ' + rendered);
}
const headerCount = (rendered.match(/<th /g) || []).length;
const bodyCount = (rendered.match(/<td /g) || []).length;
if (headerCount !== 6) throw new Error('表头列数应为 6，实际 ' + headerCount);
if (bodyCount !== 6) throw new Error('表体列数应为 6，实际 ' + bodyCount);
for (const token of ['$x$', '$Q(x)$', '10', '30', '55']) {
  if (!rendered.includes(token)) throw new Error('丢失单元格内容: ' + token);
}

// 必须保持"首尾都有竖线"作为表格判据：放宽会误吞 |x| 这类绝对值
const absoluteValue = ['设 |x| < 1 且 |y| > 2，求范围', '因为 |a| + |b| >= 0'].join(String.fromCharCode(10));
const absoluteRendered = window.preprocessFormulaForKaTeX(absoluteValue);
if (absoluteRendered.includes('<table')) {
  throw new Error('绝对值竖线被误判成表格: ' + absoluteRendered);
}
console.log('OK');
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_preview_illustration_tikz_entry_is_dom_appended_not_sanitized_away():
    """预览配图的「转 TikZ」入口必须净化之后用 DOM 追加。

    背景：用户看到图下方只有一行 "TikZ" 文本，分不清是"已转成 TikZ"还是"没转"。
    根因是入口被拼成 <button onclick=...> 塞进 HTML，而 sanitizeRichHtml 的
    DOMPurify 白名单不含 <button>、也不放行 onclick，净化后按钮外壳被拆掉、
    只剩文本。现在改为 renderQuestionPreviewContent 里 DOM 追加。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")

    # 1) 预处理阶段不得再产出按钮/内联事件
    assert 'onclick="autoTikzFromIllustration(' not in editor_source
    preprocess_start = editor_source.index("function preprocessFormulaForKaTeX(text)")
    preprocess_end = editor_source.index(
        "window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;", preprocess_start
    )
    preprocess_source = editor_source[preprocess_start:preprocess_end]
    # 只断言"实际产出的标记"，注释里提到 <button> 不算
    assert "<button type=" not in preprocess_source
    assert "onclick=" not in preprocess_source
    # 图片本体与安全大图标记必须保留（既有断言依赖该字面量）
    assert 'data-safe-image-open="true"' in preprocess_source

    # 2) 净化之后用 DOM 追加真正可点的按钮
    decorator_start = editor_source.index("function decoratePreviewIllustrationButtons(")
    decorator_end = editor_source.index(
        "window.decoratePreviewIllustrationButtons = decoratePreviewIllustrationButtons;",
        decorator_start,
    )
    decorator = editor_source[decorator_start:decorator_end]
    assert "createElement('button')" in decorator
    assert "addEventListener('click'" in decorator
    assert "window.autoTikzFromIllustration(src)" in decorator
    # 沙箱（无 document / 容器是纯对象）里必须安全空转
    assert "typeof document === 'undefined'" in decorator
    assert "typeof container.querySelectorAll !== 'function'" in decorator
    # 已经是 TikZ 渲染结果 / AI 专用参考图不给入口
    assert "skipPaths" in decorator
    assert "reference_image_path" in decorator
    assert "asset && asset.image_path" in decorator

    # 3) 调用位置：必须在 KaTeX/选择题布局之后（净化已发生）
    call_index = editor_source.index("decoratePreviewIllustrationButtons(container);")
    adapt_index = editor_source.index("adaptChoicesGridLayout(container);", decorator_end)
    assert adapt_index < call_index


def test_editor_source_chips_render_and_delete_one_source():
    """「已存来源」chips 必须真在 DOM 上可点，且删除后主来源跟着换。

    内联七字段只能改写主来源，第 2..N 条此前既看不见也删不掉（多来源来自查重
    「并入已有题」），删掉弹窗旧实现后更没有入口。本用例用最小 DOM 桩真跑一遍
    渲染 + 删除，不用文本断言糊过去。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    start = editor_source.index("var EDITOR_GRADE_LABELS")
    end = editor_source.index("window.applyEditorSourceFields = applyEditorSourceFields;", start)
    source = editor_source[start:end]
    for token in (
        "function renderEditorSourceChips(",
        "function buildEditorSourceChip(",
        "function removeEditorSourceAt(",
        "function setSelectWithFallback(",
    ):
        assert token in source, token
    # 来源文本是用户可控内容：只能 createElement + textContent，不得走 innerHTML
    chips_start = source.index("function editorSourceChipsHost(")
    assert "innerHTML" not in source[chips_start:]

    script = r"""
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'DIV').toUpperCase();
    this._id = ''; this.nodes = []; this._text = ''; this.attrs = {};
    this.listeners = {}; this.hidden = false; this.parentNode = null;
    this.className = ''; this.type = ''; this.title = ''; this._value = '';
    if (id) this.id = id;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; if (v) registry[v] = this; }
  get options() { return this.tagName === 'SELECT' ? this.nodes : undefined; }
  get children() { return this.nodes; }
  get firstChild() { return this.nodes[0] || null; }
  get value() { return this._value; }
  set value(v) {
    const want = v === null || v === undefined ? '' : String(v);
    if (this.tagName === 'SELECT') {
      // 浏览器语义：值不在 option 列表里就回落成空 —— 静默丢数据正是从这里进来的
      this._value = this.nodes.some(function (o) { return o.value === want; }) ? want : '';
    } else { this._value = want; }
  }
  get textContent() {
    return this.nodes.length ? this.nodes.map(function (n) { return n.textContent || ''; }).join('') : this._text;
  }
  set textContent(v) { this._text = String(v); this.nodes = []; }
  set innerHTML(v) { throw new Error('来源 chips 不得使用 innerHTML'); }
  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }
  insertBefore(n, ref) {
    if (!n) return n;
    const i = ref ? this.nodes.indexOf(ref) : -1;
    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);
    n.parentNode = this; return n;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  dispatch(t) { (this.listeners[t] || []).forEach(function (fn) { fn(); }); }
  click() { this.dispatch('click'); }
  closest(sel) {
    let n = this;
    const cls = /^\.([\w-]+)$/.exec(sel);
    while (n) {
      if (!cls || (n.className || '').split(/\s+/).indexOf(cls[1]) >= 0) return n;
      n = n.parentNode;
    }
    return null;
  }
}
class Opt {
  constructor(v, t) { this.value = v; this.textContent = t === undefined ? v : t; this.parentNode = null; }
}
const registry = {};
function el(tag, id) { const e = new El(tag, id); if (id) registry[id] = e; return e; }
function select(id, label, items, custom) {
  const s = el('SELECT', id);
  s.appendChild(new Opt('', label));
  (items || []).forEach(function (v) { s.appendChild(new Opt(v, v)); });
  if (custom) s.appendChild(new Opt('__custom__', '＋ 自定义…'));
  return s;
}
const metaRow = el('DIV', 'metaFullRow');
const group = el('DIV'); group.className = 'editor-meta-group';
const col = el('DIV'); col.className = 'editor-meta-col';
const fields = el('DIV'); fields.className = 'editor-meta-fields';
const row = el('DIV'); row.className = 'inline-flex items-center gap-1.5 shrink-0';
metaRow.appendChild(group); group.appendChild(col); col.appendChild(fields); fields.appendChild(row);
[
  select('editSrcYear', '年份', ['2023', '2024', '2025', '2026']),
  select('editSrcSemester', '学段', ['上', '下']),
  select('editSrcRegion', '学校', ['哈三中'], true),
  select('editSrcExamType', '类型', ['高考', '期末']),
  el('INPUT', 'editSrcPaperTitle'),
  el('INPUT', 'editSource'),
  select('editApplicableGrade', '年级', ['g1']),
].forEach(function (n) { row.appendChild(n); });
globalThis.document = {
  getElementById: function (id) { return registry[id] || null; },
  createElement: function (tag) { return tag === 'OPTION' ? new Opt('') : new El(tag); },
};
globalThis.window = globalThis;
window.EditorSources = [];
window.confirm = function () { return true; };
function fillEditorSrcProvince() {}
function fillEditorSrcCity() {}
function fillEditorSrcSchoolSelect() {}
function renderEditorPaperMeta() {}
""" + source + """
const out = {};
const year = document.getElementById('editSrcYear');
const region = document.getElementById('editSrcRegion');

// —— 1) 主来源的学校不在词库里：同步后不得被回落成空
window.EditorSources = [
  { year: '2023', region: '哈三中', exam_type: '期末', paper_title: '2023哈三中期末' },
  { year: '2016', region: '南溪一中', exam_type: '高考', paper_title: '2016宜宾期末' },
];
renderSourceChips();
out.mainFieldsKept = year.value === '2016' && region.value === '南溪一中';
out.tailOptions = region.options.map(function (o) { return o.value; }).slice(-2);

const host = document.getElementById('editorSourceChips');
out.chipsInSourceCol = host.parentNode === col;
out.chipLabels = host.children.map(function (c) { return c.firstChild.textContent; });
out.delAria = host.children.map(function (c) { return c.nodes[1].getAttribute('aria-label'); });
out.mainBadge = host.children[0].className.indexOf('is-main') >= 0;

// —— 2) 点 × 删掉主来源：剩下那条升为主来源，字段与隐藏域一起跟随
host.children[0].nodes[1].click();
out.yearsAfterDelete = window.EditorSources.map(function (s) { return s.year; });
out.fieldsAfterDelete = year.value + '|' + region.value;
out.hiddenAfterDelete = document.getElementById('editSource').value;
out.labelsAfterDelete = document.getElementById('editorSourceChips').children.map(function (c) { return c.firstChild.textContent; });

// —— 3) 取消确认时一条都不能少
window.EditorSources = [
  { year: '2023', region: '哈三中', exam_type: '三模' },
  { year: '2024', region: '师大附中', exam_type: '期末' },
];
renderSourceChips();
window.confirm = function () { return false; };
document.getElementById('editorSourceChips').children[0].nodes[1].click();
out.cancelledCount = window.EditorSources.length;

// —— 4) 内容没变就不得重建 DOM（否则内联字段每敲一个字都会抢走焦点）
window.confirm = function () { return true; };
renderSourceChips();
const before = document.getElementById('editorSourceChips').children[0];
renderEditorSourceChips();
out.rebuildSkipped = document.getElementById('editorSourceChips').children[0] === before;

console.log(JSON.stringify(out));
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    import json

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["mainFieldsKept"] is True, out
    assert out["tailOptions"] == ["南溪一中", "__custom__"], out   # 补的选项插在「自定义」之前
    assert out["chipsInSourceCol"] is True, out   # chips 落在来源组的字段列里，不挤进控件行
    assert out["mainBadge"] is True, out
    # 排序口径与后端一致：高考 > 模考 > 联考 > 校考/期末，同级别年份新在前
    assert out["chipLabels"] == [
        "主来源 · 2016年南溪一中高考",
        "2023年哈三中期末",
    ], out["chipLabels"]
    assert out["delAria"][0].startswith("删除来源："), out["delAria"]

    assert out["yearsAfterDelete"] == ["2023"], out
    assert out["fieldsAfterDelete"] == "2023|哈三中", out
    assert out["hiddenAfterDelete"] == "2023年哈三中期末", out
    assert out["labelsAfterDelete"] == ["主来源 · 2023年哈三中期末"], out

    assert out["cancelledCount"] == 2, out
    assert out["rebuildSkipped"] is True, out
def test_editor_source_sync_survives_the_province_city_cascade():
    """回写来源必须先重建级联选项再赋值，否则城市会被静默清掉。

    真实浏览器实测：旧 `syncEditorSourceFields` 先 `pEl.value=` / `cEl.value=`，
    再调 `fillEditorSrcCity()` 重建 option —— 而重建前城市列表是空的（要按省份过滤），
    于是「哈尔滨市」根本挂不上，下一次字段变更 `applyEditorSourceFields` 读回空值，
    就把这道题的 city 覆写成空。
    """

    editor_source = _read(STATIC_JS_DIR / "editor.js")
    start = editor_source.index("var EDITOR_GRADE_LABELS")
    end = editor_source.index("window.applyEditorSourceFields = applyEditorSourceFields;", start)
    main_block = editor_source[start:end]
    cascade_start = editor_source.index("function fillEditorSrcProvince(")
    cascade_end = editor_source.index("function matchEditorSrcSchoolToGeo(", cascade_start)
    cascade_block = editor_source[cascade_start:cascade_end]
    assert "fillEditorSrcCity();" in cascade_block
    assert "fillEditorSrcSchoolSelect();" in cascade_block

    script = r"""
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'DIV').toUpperCase();
    this._id = ''; this.nodes = []; this._text = ''; this.attrs = {};
    this.listeners = {}; this.hidden = false; this.parentNode = null;
    this.className = ''; this.type = ''; this.title = ''; this._value = '';
    if (id) this.id = id;
  }
  get id() { return this._id; }
  set id(v) { this._id = v; if (v) registry[v] = this; }
  get options() { return this.tagName === 'SELECT' ? this.nodes : undefined; }
  get children() { return this.nodes; }
  get value() { return this._value; }
  set value(v) {
    const want = v === null || v === undefined ? '' : String(v);
    if (this.tagName === 'SELECT') {
      // 浏览器语义：option 列表里没有这个值就回落成空
      this._value = this.nodes.some(function (o) { return o.value === want; }) ? want : '';
    } else { this._value = want; }
  }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); }
  set innerHTML(v) {
    const m = /^<option value="">([^<]*)<\/option>$/.exec(String(v));
    if (!m) throw new Error('这里只允许 select 的 option 复位写法：' + String(v).slice(0, 40));
    this.nodes = []; this.appendChild(new Opt('', m[1])); this._value = '';
  }
  appendChild(n) { this.nodes.push(n); n.parentNode = this; return n; }
  insertBefore(n, ref) {
    if (!n) return n;
    const i = ref ? this.nodes.indexOf(ref) : -1;
    if (i < 0) this.nodes.push(n); else this.nodes.splice(i, 0, n);
    n.parentNode = this; return n;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener() {}
  closest(sel) {
    let n = this;
    const cls = /^\.([\w-]+)$/.exec(sel);
    while (n) {
      if (!cls || (n.className || '').split(/\s+/).indexOf(cls[1]) >= 0) return n;
      n = n.parentNode;
    }
    return null;
  }
}
class Opt {
  constructor(v, t) { this.value = v; this.textContent = t === undefined ? v : t; this.parentNode = null; }
}
const registry = {};
function el(tag, id) { const e = new El(tag, id); if (id) registry[id] = e; return e; }
function select(id, label) { const s = el('SELECT', id); s.appendChild(new Opt('', label)); return s; }
const row = el('DIV');
[
  (function () { const y = select('editSrcYear', '年份'); ['2023', '2024', '2025'].forEach(function (v) { y.appendChild(new Opt(v, v)); }); return y; })(),
  select('editSrcSemester', '学期'),
  select('editSrcProvince', '省份'), select('editSrcCity', '城市'),
  select('editSrcRegion', '学校'), select('editSrcExamType', '考试类型'),
  el('INPUT', 'editSrcPaperTitle'), el('INPUT', 'editSource'),
  select('editApplicableGrade', '年级'),
].forEach(function (n) { row.appendChild(n); });
globalThis.document = {
  getElementById: function (id) { return registry[id] || null; },
  createElement: function (tag) { return tag === 'OPTION' ? new Opt('') : new El(tag); },
};
globalThis.window = globalThis;
window.EditorSources = [];
window.confirm = function () { return true; };;
window._regionsTree = [{ province: '黑龙江省', cities: ['哈尔滨市', '大庆市'] }];
window._schoolsDict = [{ name: '哈三中', province: '黑龙江省', city: '哈尔滨市' }];
function renderEditorPaperMeta() {}
""" + cascade_block + main_block + """
const out = {};
document.getElementById('editSrcCity').appendChild(new Opt('__custom__', '＋ 自定义…'));
window.EditorSources = [{
  year: '2024', province: '黑龙江省', city: '哈尔滨市', region: '哈三中',
  semester: '上', exam_type: '三模', paper_title: '2024哈三中三模',
}];
renderSourceChips();
out.provinceAfterSync = document.getElementById('editSrcProvince').value;
out.cityAfterSync = document.getElementById('editSrcCity').value;

// 模拟用户只动了一下年份：字段回读后城市不得消失
document.getElementById('editSrcYear').value = '2023';
applyEditorSourceFields();
const saved = window.EditorSources[0];
out.cityAfterApply = saved.city;
out.yearAfterApply = saved.year;
out.hiddenAfterApply = document.getElementById('editSource').value;
console.log(JSON.stringify(out));
"""
    result = _run_node_script(script)
    assert result.returncode == 0, result.stderr

    import json

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["provinceAfterSync"] == "黑龙江省", out
    assert out["cityAfterSync"] == "哈尔滨市", out
    assert out["cityAfterApply"] == "哈尔滨市", out
    assert out["yearAfterApply"] == "2023", out
    assert "哈尔滨市" in out["hiddenAfterApply"], out

def test_editor_meta_group_styles_live_in_app_css():
    """元数据分组与来源 chips 的样式必须在 app.css 里，且带窄屏断点。

    预览净化白名单不含 `style`（AGENTS.md §6），录入区同样禁止内联样式：
    类名一旦只出现在 JS/HTML 里而 CSS 没有对应规则，就会静默变成「挤成一行的文字」。
    375px 必须可操作，所以还要有 768px 断点把组标签翻到字段上方、并取消左缩进。
    """

    css = _read(CSS_PATH)
    html = _read(INDEX_PATH)
    for cls in (
        ".editor-meta-group",
        ".editor-meta-group-label",
        ".editor-meta-fields",
        ".editor-meta-col",
        ".editor-meta-indent",
        ".editor-source-chip",
        ".editor-source-chip-del",
    ):
        assert cls in css, cls
    assert ".editor-source-chips[hidden]" in css and ".editor-meta-indent" in css
    media_at = css.index("@media (max-width: 768px)", css.index(".editor-meta-group"))
    block_end = css.index(".extra-topic-row {", media_at)
    block = css[media_at:css.index(chr(10) + "}", block_end)]
    assert ".editor-meta-indent" in block and "margin-left: 0" in block
    assert ".editor-meta-group" in block and "flex-direction: column" in block
    # HTML 侧只挂类名，不得出现内联 style
    region = html[html.index('<div id="metaFullRow"'):html.index('id="questionContentPanel"')]
    assert 'style="' not in region, "元数据区不得写内联 style"
    assert region.count('class="editor-meta-group"') == 3, region.count('class="editor-meta-group"')


