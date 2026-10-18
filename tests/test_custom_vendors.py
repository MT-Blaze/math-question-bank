# -*- coding: utf-8 -*-
"""自定义厂商（瘦版：仅 OpenAI 兼容第三方文本厂商）前端回归测试。"""

from pathlib import Path
import shutil
import subprocess

# 弹窗过渡助手的桩替身：真实实现走 rAF，Node 环境里必须同步揭示。
from modal_transition_stub import MODAL_TRANSITION_STUB_JS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
STATIC_JS_DIR = STATIC_DIR / "js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_add_custom_vendor_button_and_modal_present_in_index_html():
    index_source = _read(STATIC_DIR / "index.html")
    assert 'onclick="openCustomVendorModal()"' in index_source
    assert 'id="customProvidersContainer"' in index_source
    assert 'id="customVendorModal"' in index_source
    assert 'id="cvName"' in index_source
    assert 'id="cvBaseUrl"' in index_source
    assert 'id="cvApiKey"' in index_source
    assert 'id="cvModel"' in index_source
    assert "onclick=\"submitCustomVendor()\"" in index_source
    assert "onclick=\"closeCustomVendorModal()\"" in index_source


def test_three_visible_base_url_inputs_and_advanced_options_removed():
    """MinerU / 中转站 A / 中转站 B 的接口地址框可见，且底部高级选项被移除。"""
    index_source = _read(STATIC_DIR / "index.html")
    assert 'id="settingsMineruApiBase"' in index_source
    assert 'id="settingsZhongzhanGptBaseUrl"' in index_source
    assert 'id="settingsZhongzhanClaudeBaseUrl"' in index_source
    # 这三行不再使用 type="hidden" 的 BaseUrl
    assert 'type="hidden" id="settingsZhongzhanGptBaseUrl"' not in index_source
    assert 'type="hidden" id="settingsZhongzhanClaudeBaseUrl"' not in index_source
    # 高级选项 details 块已删除
    assert "高级选项：MinerU 接口地址覆盖" not in index_source


def test_custom_vendor_helpers_build_cp_slug_and_options_in_real_js():
    """自定义厂商：slug 生成、cp_ 选项拼装、提交/删除/渲染均正确。"""
    node = shutil.which("node")
    assert node, "Node.js is required for the frontend executable regression"

    api_source = _read(STATIC_JS_DIR / "api.js")
    block_start = api_source.index("// ===== 自定义厂商")
    block_end = api_source.index("function openSettingsModal() {")
    block = api_source[block_start:block_end]

    script = (
        "const window = {};\n"
        "function showToast() {}\n"
        "function confirm() { return true; }\n"
        "window.MathBankModal = { open() {}, close() {} };\n"
        + MODAL_TRANSITION_STUB_JS
        + "\n"
        "const MathBankSafe = {\n"
        "  escapeAttribute(value) { return String(value).replace(/&/g, '&amp;').replace(/\"/g, '&quot;'); },\n"
        "  escapeText(value) { return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }\n"
        "};\n"
        "function makeEl() {\n"
        "  return {\n"
        "    innerHTML: '', value: '', options: [], textContent: '',\n"
        "    classList: { add() {}, remove() {} },\n"
        "    querySelector() { return null; },\n"
        "    appendChild(node) { this.options.push(node); },\n"
        "    setAttribute() {}, removeAttribute() {}\n"
        "  };\n"
        "}\n"
        "const elements = {};\n"
        "const document = {\n"
        "  getElementById(id) { if (!elements[id]) elements[id] = makeEl(); return elements[id]; },\n"
        "  createElement(tag) { const el = makeEl(); el.tagName = tag; return el; }\n"
        "};\n"
        + block
        + "\n"
        "if (slugifyVendorName('TokenPlan') !== 'TOKENPLAN') throw new Error('slug 未归一化: ' + slugifyVendorName('TokenPlan'));\n"
        "if (slugifyVendorName('My AI') !== 'MYAI') throw new Error('slug 未去空格: ' + slugifyVendorName('My AI'));\n"
        "customProviders = [\n"
        "  { name: 'TokenPlan', base_url: 'https://tp.example/v1', api_key: 'k', model: 'gpt-4o' },\n"
        "  { name: 'My AI', base_url: 'https://x.example/v1', api_key: 'k2', model: '' }\n"
        "];\n"
        "renderCustomProviders();\n"
        "const containerHtml = document.getElementById('customProvidersContainer').innerHTML;\n"
        "if (!containerHtml.includes('TokenPlan') || !containerHtml.includes('My AI')) {\n"
        "  throw new Error('渲染行未包含厂商名');\n"
        "}\n"
        "if (!containerHtml.includes('cp_TOKENPLAN') || !containerHtml.includes('cp_MYAI')) {\n"
        "  throw new Error('渲染行未包含 cp_ slug 标记');\n"
        "}\n"
        "syncCustomProviderOptions();\n"
        "const solveSel = document.getElementById('solveModelProvider');\n"
        "const optValues = solveSel.options.map(o => o.value);\n"
        "if (!optValues.includes('cp_tokenplan') || !optValues.includes('cp_myai')) {\n"
        "  throw new Error('首选模型下拉缺少自定义厂商选项: ' + optValues.join(','));\n"
        "}\n"
        "if (solveSel.options.some(o => o.textContent !== ('cp_tokenplan' === o.value ? 'TokenPlan' : 'My AI'))) {\n"
        "  throw new Error('下拉选项 label 不匹配');\n"
        "}\n"
        "document.getElementById('cvName').value = 'NewVendor';\n"
        "document.getElementById('cvBaseUrl').value = 'https://nv.example/v1';\n"
        "document.getElementById('cvApiKey').value = 'k3';\n"
        "document.getElementById('cvModel').value = 'gpt-4o-mini';\n"
        "submitCustomVendor();\n"
        "if (customProviders.length !== 3) throw new Error('submit 后厂商数异常: ' + customProviders.length);\n"
        "if (customProviders[2].name !== 'NewVendor') throw new Error('submit 内容错误');\n"
        "removeCustomProvider('TOKENPLAN');\n"
        "if (customProviders.length !== 2) throw new Error('remove 后厂商数异常: ' + customProviders.length);\n"
        "if (customProviders.some(p => p.name === 'TokenPlan')) throw new Error('remove 未真正移除 TokenPlan');\n"
        "document.getElementById('cvName').value = 'newvendor';\n"
        "document.getElementById('cvBaseUrl').value = 'https://dup.example/v1';\n"
        "document.getElementById('cvApiKey').value = 'k4';\n"
        "document.getElementById('cvModel').value = '';\n"
        "submitCustomVendor();\n"
        "if (customProviders.length !== 2) throw new Error('重名提交未被拒绝: ' + customProviders.length);\n"
    )
    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
