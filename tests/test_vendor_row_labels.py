# -*- coding: utf-8 -*-
"""厂商名下拉 → Key 输入框标签/placeholder 跟随 的前端回归测试。"""

from pathlib import Path
import shutil
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_six_vendor_rows_marked_with_data_vendor_row():
    """六行（DeepSeek/SiliconFlow/阿里百炼/MinerU/中转站A/中转站B）都要有 data-vendor-row。"""
    index_source = _read(STATIC_DIR / "index.html")
    assert index_source.count('data-vendor-row') == 6, (
        "data-vendor-row 标记数量应为 6，实际 %d"
        % index_source.count('data-vendor-row')
    )
    # 中转站五行在 data-provider-slot 之外，还要各自带上 data-vendor-row
    for slot in ('zhongzhan_gpt', 'zhongzhan_claude'):
        marker = 'data-provider-slot="%s" data-vendor-row' % slot
        assert marker in index_source, "中转站行缺少 data-vendor-row: " + slot


def test_first_four_vendor_selects_disabled_last_two_switchable():
    """方案B'：前四行（DeepSeek/SiliconFlow/阿里百炼/MinerU）厂商名只读，中转站 A/B 仍可切换。

    前四行的 Key label / input id / 保存目标 env 全是写死的，允许自由切换厂商名只会
    造成「看着配了 A、实际存进 B 那一行」的误导；中转站两行仍需智元 ↔ Agnes AI 切换。
    """
    index_source = _read(STATIC_DIR / "index.html")
    import re

    selects = re.findall(
        r'<select class="vendor-select[\s\S]*?</select>', index_source
    )
    assert len(selects) == 6, "厂商名下拉应有 6 个，实际 %d" % len(selects)
    fixed_selects, switchable_selects = selects[:4], selects[4:]
    starts = [m.start() for m in re.finditer(r'<select class="vendor-select', index_source)]
    # 每个厂商行自己那一对 </select>（全文还有别的 </select>，必须按块取）
    closes = [m.end() for m in [re.search(r'</select>', s) for s in selects]]
    assert len(starts) == 6 and len(closes) == 6
    for i, vendor in enumerate(("DeepSeek", "SiliconFlow", "阿里百炼", "MinerU")):
        block = fixed_selects[i]
        assert " disabled" in block.split(">")[0], "前四行应 disabled: " + vendor
        assert 'title="固定厂商' in block, "缺少 title 提示: " + vendor
        # 小字提示必须紧跟在本行 </select> 之后（下一个厂商行之前）
        tail = index_source[closes[i]: starts[i + 1] if i + 1 < 6 else len(index_source)]
        assert "固定厂商；改地址见右侧接口地址框" in tail, "缺少小字提示: " + vendor
    for i, vendor in enumerate(("中转站 A", "中转站 B")):
        block = switchable_selects[i]
        assert " disabled" not in block.split(">")[0], "中转站两行必须可切换: " + vendor
        assert "固定厂商" not in block, "中转站行不应有固定厂商提示: " + vendor


def test_on_base_url_input_skips_disabled_vendor_rows():
    """固定厂商行不得被「接口地址反查厂商名」改值，否则标签与保存目标会再次错位。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    map_start = api_source.index("const BASE_URL_VENDOR_HOST_MAP = Object.freeze([")
    map_end = api_source.index("function onBaseUrlInput(inputEl) {", map_start)
    fn_start = api_source.index("function onBaseUrlInput(inputEl) {")
    fn_end = api_source.index("\n        }\n        window.onBaseUrlInput", fn_start)
    block = api_source[map_start:map_end] + api_source[fn_start:fn_end + len("\n        }")]

    script = (
        "let syncCalls = 0;\n"
        "function syncModelProviderOptions() { syncCalls++; }\n"
        + block
        + "\n"
        # --- 1. 固定行（select.disabled = true）：地址反查不得改厂商名 ---
        "const fixedSelect = { value: 'DeepSeek', disabled: true };\n"
        "const fixedRow = { querySelector: (s) => (s === '.vendor-select' ? fixedSelect : null) };\n"
        "const fixedInput = { value: 'https://dashscope.aliyuncs.com/compatible-mode/v1', closest: () => fixedRow };\n"
        "onBaseUrlInput(fixedInput);\n"
        "if (fixedSelect.value !== 'DeepSeek') {\n"
        "  throw new Error('固定行厂商名被改动: ' + fixedSelect.value);\n"
        "}\n"
        "if (syncCalls !== 0) {\n"
        "  throw new Error('固定行不应触发模型候选同步, syncCalls=' + syncCalls);\n"
        "}\n"
        # --- 2. 中转站行（可切换）：命中 dashscope 应切到阿里百炼 ---
        "syncCalls = 0;\n"
        "const zzSelect = { value: '智元/SenseNova', disabled: false };\n"
        "const zzRow = { querySelector: (s) => (s === '.vendor-select' ? zzSelect : null) };\n"
        "const zzInput = { value: 'https://dashscope.aliyuncs.com/compatible-mode/v1', closest: () => zzRow };\n"
        "onBaseUrlInput(zzInput);\n"
        "if (zzSelect.value !== '阿里百炼') {\n"
        "  throw new Error('中转站行未跟随地址切换: ' + zzSelect.value);\n"
        "}\n"
        "if (syncCalls !== 1) {\n"
        "  throw new Error('中转站行应触发一次模型候选同步, syncCalls=' + syncCalls);\n"
        "}\n"
        # --- 3. 空地址 / 无 select 不得抛错 ---
        "onBaseUrlInput({ value: '', closest: () => null });\n"
        "onBaseUrlInput(null);\n"
    )
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_vendor_key_input_ids_untouched():
    """syncVendorRowLabels 只改显示文字，input 的 id 必须保持原样（保存链路靠 id 定位）。"""
    index_source = _read(STATIC_DIR / "index.html")
    for key_id in (
        "settingsDeepseekKey",
        "settingsSiliconflowKey",
        "settingsAliBailianKey",
        "settingsMineruToken",
        "settingsZhongzhanGptKey",
        "settingsZhongzhanClaudeKey",
    ):
        assert 'id="%s"' % key_id in index_source
    api_source = _read(STATIC_JS_DIR / "api.js")
    assert 'window.syncVendorRowLabels' in api_source
    assert 'window.syncAllVendorRowLabels' in api_source


def test_sync_vendor_row_labels_in_real_js():
    """在真实 api.js 代码上验证：标签/placeholder 跟随厂商名，id 与 __custom__ 行为正确。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    block_start = api_source.index("function syncVendorRowLabels(rowEl) {")
    block_end = api_source.index("function onVendorSelectChange(")
    block = api_source[block_start:block_end]

    script = (
        "const document = { querySelectorAll() { return globalThis.__rows; } };\n"
        "globalThis.__rows = [];\n"
        + block
        + "\n"
        # --- 构造一个最小的厂商行 DOM 替身 ---
        "const originalId = 'settingsSiliconflowKey';\n"
        "const label = { textContent: 'SiliconFlow API Key' };\n"
        "const keyInput = {\n"
        "  type: 'password', id: originalId, value: '',\n"
        "  placeholder: '输入您的 SILICONFLOW_API_KEY',\n"
        "  parentElement: { querySelector: (s) => (s === 'label' ? label : null) }\n"
        "};\n"
        "const select = { value: 'SiliconFlow', querySelector: () => null };\n"
        "const row = {\n"
        "  querySelector(sel) {\n"
        "    if (sel === '.vendor-select') return select;\n"
        "    if (sel === 'input[type=\"password\"]') return keyInput;\n"
        "    return null;\n"
        "  }\n"
        "};\n"
        # --- 1. 切到「阿里百炼」：标签与 placeholder 都要跟上 ---
        "select.value = '阿里百炼';\n"
        "syncVendorRowLabels(row);\n"
        "if (label.textContent !== '阿里百炼 API Key') {\n"
        "  throw new Error('标签未跟随厂商名: ' + label.textContent);\n"
        "}\n"
        "if (keyInput.placeholder !== '输入 阿里百炼 API Key') {\n"
        "  throw new Error('placeholder 未跟随厂商名: ' + keyInput.placeholder);\n"
        "}\n"
        # --- 2. 绝不能动 id ---
        "if (keyInput.id !== originalId) {\n"
        "  throw new Error('input 的 id 被改动: ' + keyInput.id);\n"
        "}\n"
        # --- 3. MinerU 是 Token 不是 API Key（与 autoFillVendorBaseUrl 后缀规则一致）---
        "select.value = 'MinerU';\n"
        "syncVendorRowLabels(row);\n"
        "if (label.textContent !== 'MinerU Token') {\n"
        "  throw new Error('MinerU 后缀错误: ' + label.textContent);\n"
        "}\n"
        "if (keyInput.placeholder !== '输入 MinerU Token') {\n"
        "  throw new Error('MinerU placeholder 错误: ' + keyInput.placeholder);\n"
        "}\n"
        # --- 4. 「其他…」态：保持上一个厂商名不动 ---
        "select.value = '__custom__';\n"
        "syncVendorRowLabels(row);\n"
        "if (label.textContent !== 'MinerU Token') {\n"
        "  throw new Error('__custom__ 态不应改标签: ' + label.textContent);\n"
        "}\n"
        # --- 5. 批量同步（openSettingsModal 初始化走这条）---
        "select.value = 'DeepSeek';\n"
        "globalThis.__rows = [row];\n"
        "syncAllVendorRowLabels();\n"
        "if (label.textContent !== 'DeepSeek API Key') {\n"
        "  throw new Error('批量同步未生效: ' + label.textContent);\n"
        "}\n"
        # --- 6. 空行/无 select 的容错，不得抛错 ---
        "syncVendorRowLabels(null);\n"
        "syncVendorRowLabels({ querySelector: () => null });\n"
        "syncAllVendorRowLabels();\n"
    )
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
