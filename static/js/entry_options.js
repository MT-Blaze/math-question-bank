/* 单题录入 · 选项编辑卡 + 选项切图工具（#31 / #32）。
 *
 * 唯一真源是题干 textarea（#editContent）：
 *   解析 —— 认「A．xxx」逐行、单行挤多个标号、已归一化的 \begin{choices}\item；
 *           图片同时认 Markdown ![](url) 与入库形态 \includegraphics{文件名}；
 *   回写 —— 只写「A．正文」纯文本，choices 环境一律由后端 normalize_for_storage
 *           （latexify_choices）生成，前端绝不自己拼环境，也未改动时不重排题干。
 * 可点控件全部 createElement + addEventListener：站内富文本走 DOMPurify 白名单，
 * button / onclick / style 属性会被剥掉，拼字符串塞按钮必然「看得见、点不动」。
 */
(function () {
  'use strict';

  const LETTERS = 'ABCDEFGH';
  const CHOICE_TYPES = ['single_choice', 'multi_choice'];
  const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // 与后端单图上限一致
  const MAX_TOTAL_PIXELS = 30000000;         // 与后端像素上限一致
  const MAX_FRAGMENTS = 12;

  // 与后端 paper_helper._CHOICE_LABEL_LINE_RE 同口径：行首「A–D ＋ . ／ ． ／ 、」
  const LABEL_LINE_RE = /^[ \t]*(?:[-*][ \t]*)?([A-D])[ \t]*(?:[.．、][ \t]*)?(.*)$/;
  const CHOICES_ENV_RE = /\\begin\{choices\}([\s\S]*?)\\end\{choices\}/;
  const CHOICES_ITEM_SPLIT_RE = /\\item\b/;
  const MD_IMAGE_RE = /!\[[^\]]*\]\([^)\s]+\)/g;
  const LATEX_IMAGE_RE = /\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}/g;
  const INLINE_MARK_RE = /[A-D](?=[.．、\s])/g;

  function $(id) { return document.getElementById(id); }
  function stemArea() { return $('editContent'); }

  function toast(message, type) {
    if (typeof window.showToast === 'function') window.showToast(message, type);
  }

  // 数学段用等长占位遮住，标号扫描就不会把 $A\.B$ 里的字母当选项标号；
  // 等长保证下标仍可直接套用到原始字符串上。
  function maskMath(line) {
    const chars = line.split('');
    let index = 0;
    while (index < line.length) {
      if (line.charAt(index) !== '$') { index += 1; continue; }
      const closing = line.indexOf('$', index + (line.charAt(index + 1) === '$' ? 2 : 1));
      if (closing < 0) break;
      for (let i = index; i <= closing; i++) chars[i] = '\u0001';
      index = closing + 1;
    }
    return chars.join('');
  }

  function skipSpaces(text, from) {
    let cursor = from;
    while (cursor < text.length && (text.charAt(cursor) === ' ' || text.charAt(cursor) === '\t')) cursor += 1;
    return cursor;
  }

  // 行内标号候选：字母前后不接字母/数字（防「点B在圆C上」「AB」误判）
  function findMarks(line) {
    const masked = maskMath(line);
    const marks = [];
    INLINE_MARK_RE.lastIndex = 0;
    let match;
    while ((match = INLINE_MARK_RE.exec(masked)) !== null) {
      const before = match.index > 0 ? masked.charAt(match.index - 1) : '';
      if (/[A-Za-z0-9]/.test(before)) continue;
      let bodyStart = skipSpaces(masked, match.index + 1);
      const punct = masked.charAt(bodyStart);
      if (punct === '.' || punct === '．' || punct === '、') {
        bodyStart = skipSpaces(masked, bodyStart + 1);
      }
      marks.push({ labelStart: match.index, letter: masked.charAt(match.index), bodyStart });
    }
    return marks;
  }

  // 从某个标号起，最长的一段连续递增标号（A B C D…），与后端同口径
  function consecutiveRun(marks) {
    if (!marks.length) return [];
    let best = [marks[0]];
    let current = [marks[0]];
    for (let i = 1; i < marks.length; i++) {
      if (marks[i].letter === LETTERS.charAt(LETTERS.indexOf(marks[i - 1].letter) + 1)) {
        current.push(marks[i]);
      } else {
        current = [marks[i]];
      }
      if (current.length > best.length) best = current;
    }
    return best;
  }

  // 一行 → 若干候选选项；返回 null 表示这行不是选项行
  function lineOptions(line) {
    if (!line || !line.trim()) return null;
    const run = consecutiveRun(findMarks(line));
    if (run.length >= 2) {
      const options = [];
      for (let i = 0; i < run.length; i++) {
        const stop = i + 1 < run.length ? run[i + 1].labelStart : line.length;
        options.push({ letter: run[i].letter, body: line.slice(run[i].bodyStart, stop).trim() });
      }
      return { head: line.slice(0, run[0].labelStart).trim(), options };
    }
    const match = LABEL_LINE_RE.exec(line);
    if (match && (!run.length || run[0].labelStart === 0)) {
      return { head: '', options: [{ letter: match[1], body: (match[2] || '').trim() }] };
    }
    return null;
  }

  function buildOption(label, rawBody) {
    const images = [];
    let text = (rawBody || '').replace(MD_IMAGE_RE, (token) => {
      const url = (token.match(/\(([^)\s]+)/) || [])[1] || '';
      images.push({ token, url });
      return ' ';
    });
    text = text.replace(LATEX_IMAGE_RE, (token) => {
      const path = (token.match(/\{([^}]*)\}/) || [])[1] || '';
      images.push({ token, url: path });
      return ' ';
    });
    text = text.replace(/[ \t]{2,}/g, ' ').trim();
    return { label, raw: rawBody || '', text, images, edited: false };
  }

  function optionBody(option) {
    if (!option.edited) return option.raw;
    const parts = [];
    if (option.text) parts.push(option.text);
    option.images.forEach((image) => parts.push(image.token));
    return parts.join(' ');
  }

  // 从第 startIndex 行起收集一整块选项（块内允许空行，四个选项图各占一段是常态）
  function collectBlock(lines, startIndex) {
    const first = lineOptions(lines[startIndex]);
    if (!first || first.options[0].letter !== 'A') return null;

    const collected = [];
    let expect = 0;
    let cursor = startIndex;
    let blockEnd = startIndex;
    while (cursor < lines.length && expect < LETTERS.length) {
      const parsed = cursor === startIndex ? first : lineOptions(lines[cursor]);
      if (!parsed) {
        if (!lines[cursor].trim() && collected.length) { cursor += 1; continue; }
        break;
      }
      let taken = 0;
      while (taken < parsed.options.length && parsed.options[taken].letter === LETTERS.charAt(expect + taken)) {
        taken += 1;
      }
      if (!taken) break;
      for (let i = 0; i < taken; i++) collected.push(parsed.options[i]);
      expect += taken;
      blockEnd = cursor + 1;
      cursor += 1;
    }
    if (collected.length < 2) return null;

    const prefixLines = lines.slice(0, startIndex);
    if (first.head) prefixLines.push(first.head);
    return {
      found: true,
      prefix: prefixLines.join('\n'),
      suffix: lines.slice(blockEnd).join('\n').replace(/^\s+/, ''),
      options: collected.map((item) => buildOption(item.letter, item.body))
    };
  }

  function parseStem(text) {
    const source = text || '';
    const empty = { found: false, prefix: source, suffix: '', options: [] };
    const env = CHOICES_ENV_RE.exec(source);
    if (env) {
      const bodies = env[1].split(CHOICES_ITEM_SPLIT_RE).slice(1);
      if (!bodies.length) return empty;
      return {
        found: true,
        envBased: true,
        prefix: source.slice(0, env.index).replace(/\s+$/, ''),
        suffix: source.slice(env.index + env[0].length).replace(/^\s+/, ''),
        options: bodies.map((body, index) => buildOption(LETTERS.charAt(index) || String(index + 1), body.trim()))
      };
    }
    const lines = source.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const block = collectBlock(lines, i);
      if (block) return block;
    }
    return empty;
  }

  const card = {
    options: [],
    prefix: '',
    suffix: '',
    signature: '',
    writingToStem: false
  };

  function signatureOf(options) {
    return options.map((option) => option.label + '' + option.raw).join('|');
  }

  function composeStem() {
    const blocks = [];
    if (card.prefix) blocks.push(card.prefix);
    card.options.forEach((option) => blocks.push(option.label + '．' + optionBody(option)));
    if (card.suffix) blocks.push(card.suffix);
    return blocks.filter((block) => block !== '').join('\n');
  }

  function writeStem() {
    const area = stemArea();
    if (!area) return;
    card.writingToStem = true;
    area.value = composeStem();
    area.dispatchEvent(new Event('input'));
    card.writingToStem = false;
    card.signature = signatureOf(card.options);
  }

  function syncFromStem(force) {
    const area = stemArea();
    if (!area || card.writingToStem) return;
    const parsed = parseStem(area.value);
    if (!parsed.found) {
      if (card.options.length) {
        card.options = [];
        card.prefix = area.value;
        card.suffix = '';
        card.signature = '';
        renderRows();
      }
      return;
    }
    const signature = signatureOf(parsed.options);
    if (!force && signature === card.signature) return;
    card.options = parsed.options;
    card.prefix = parsed.prefix;
    card.suffix = parsed.suffix;
    card.signature = signature;
    renderRows();
  }

  function isChoiceType() {
    const select = $('editQType');
    return !!select && CHOICE_TYPES.indexOf(select.value) !== -1;
  }

  function refreshVisibility() {
    const panel = $('optionEditorPanel');
    if (!panel) return;
    panel.classList.toggle('hidden', !isChoiceType());
    // 非选择题也同步一次：载入填空题时不该在隐藏面板里留着上一题的选项行
    syncFromStem(false);
  }

  function makeElement(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeIconButton(className, icon, label, onClick, title) {
    const button = makeElement('button', className);
    button.type = 'button';
    const i = makeElement('i', 'fa-solid ' + icon);
    i.setAttribute('aria-hidden', 'true');
    button.appendChild(i);
    if (label) button.appendChild(makeElement('span', null, label));
    if (title) { button.title = title; button.setAttribute('aria-label', title); }
    button.addEventListener('click', onClick);
    return button;
  }

  function imageProxyUrl(url) {
    // 源码里可能是完整路径或裸文件名：沿用 editor.js 预览那套 basename 兜底
    if (!url) return '';
    if (/^https?:\/\//i.test(url) || url.indexOf('/uploads/') !== -1) return url;
    const base = url.split('/').pop();
    return (window.__mathbankImgByBase && window.__mathbankImgByBase[base]) || ('/static/uploads/' + base);
  }

  function renderRows() {
    const host = $('optionRows');
    const note = $('optionModeNote');
    if (!host) return;
    host.textContent = '';

    if (!card.options.length) {
      host.appendChild(makeElement('p', 'option-panel-hint',
        '题干里还没识别出选项。点「加选项」手工建，或贴一张选项拼图后用「切开拼图」自动填。'));
      if (note) note.textContent = '尚未解析到选项';
      return;
    }

    card.options.forEach((option, index) => {
      const row = makeElement('div', 'option-row');
      row.setAttribute('role', 'listitem');
      row.appendChild(makeElement('span', 'option-label', option.label));

      const body = makeElement('div', 'option-body');
      const input = makeElement('textarea', 'option-text');
      input.rows = 2;
      input.value = option.text;
      input.placeholder = option.images.length ? '（此项只有图片）' : '选项 ' + option.label + ' 的内容';
      input.setAttribute('aria-label', '选项 ' + option.label + ' 内容');
      input.addEventListener('input', () => {
        option.text = input.value;
        option.edited = true;
        writeStem();
      });
      body.appendChild(input);

      if (option.images.length) {
        const strip = makeElement('div', 'option-images');
        option.images.forEach((image) => {
          const chip = makeElement('span', 'option-image-chip');
          const img = makeElement('img');
          img.src = imageProxyUrl(image.url);
          img.alt = '选项 ' + option.label + ' 图片';
          img.addEventListener('click', () => {
            if (typeof window.zoomImage === 'function') window.zoomImage(img.src);
          });
          chip.appendChild(img);
          const tools = makeElement('span', 'option-chip-tools');
          tools.appendChild(makeIconButton('option-chip-tool', 'fa-scissors', null, () => {
            openCropDialog({ url: imageProxyUrl(image.url), token: image.token, sourceUrl: image.url });
          }, '切分这张图'));
          tools.appendChild(makeIconButton('option-chip-tool is-danger', 'fa-xmark', null, () => {
            option.images = option.images.filter((item) => item !== image);
            option.edited = true;
            writeStem();
            renderRows();
          }, '移除这张图'));
          chip.appendChild(tools);
          strip.appendChild(chip);
        });
        body.appendChild(strip);
      }
      row.appendChild(body);

      const actions = makeElement('div', 'option-actions');
      actions.appendChild(makeIconButton('option-icon-btn', 'fa-scissors', '切图', () => {
        openCropDialog(cropSourceForOption(index));
      }, '把选项拼图切开填进本项'));
      actions.appendChild(makeIconButton('option-icon-btn is-danger', 'fa-trash-can', '删', () => {
        card.options.splice(index, 1);
        card.options.forEach((item, position) => { item.label = LETTERS.charAt(position) || String(position + 1); });
        writeStem();
        renderRows();
      }, '删除该选项'));
      row.appendChild(actions);

      host.appendChild(row);
    });

    const imageOnly = card.options.length >= 2 && card.options.every((option) => !option.text && option.images.length);
    if (note) {
      note.textContent = imageOnly
        ? '四个选项都是图片：可在上方把「选项列数」设为 4 列，避免被压成两行'
        : '改动只回写「A．正文」，版式由后端统一生成';
    }
  }

  // ===== 选项切图工具（纯前端 canvas，切片走现有 POST /api/upload） =====
  const crop = {
    mode: 'grid',
    rows: 2,
    cols: 2,
    inset: 2,
    zoom: 1,
    baseWidth: 0,
    boxes: [], // 自由框选，自然像素坐标
    img: null,
    url: '',
    sourceUrl: '',
    sourceToken: '',
    objectUrl: '',
    busy: false,
    // 会话号：关闭或重开弹窗时递增，用来作废已经取消的在途上传批次
    session: 0
  };

  function clamp(value, low, high) {
    const number = Number(value);
    if (!isFinite(number)) return low;
    return Math.min(high, Math.max(low, number));
  }

  function naturalSize() {
    if (!crop.img) return { width: 0, height: 0 };
    return {
      width: crop.img.naturalWidth || crop.img.width || 0,
      height: crop.img.naturalHeight || crop.img.height || 0
    };
  }

  // 纯几何：把 width×height 的图均分成 rows×cols，并按 insetPct% 收掉相邻边距
  function computeGridBoxes(width, height, rows, cols, insetPct) {
    const rowCount = clamp(rows, 1, 9);
    const colCount = clamp(cols, 1, 9);
    const ratio = clamp(insetPct, 0, 40) / 100;
    const cellW = width / colCount;
    const cellH = height / rowCount;
    const boxes = [];
    for (let r = 0; r < rowCount; r++) {
      for (let c = 0; c < colCount; c++) {
        boxes.push({
          x: c * cellW + cellW * ratio,
          y: r * cellH + cellH * ratio,
          w: cellW * (1 - 2 * ratio),
          h: cellH * (1 - 2 * ratio)
        });
      }
    }
    return boxes;
  }

  function gridBoxes() {
    const size = naturalSize();
    return computeGridBoxes(size.width, size.height, crop.rows, crop.cols, crop.inset);
  }

  function currentBoxes() {
    return crop.mode === 'grid' ? gridBoxes() : crop.boxes;
  }

  function clearBoxNodes() {
    const frame = $('cropFrame');
    if (!frame) return;
    Array.prototype.slice.call(frame.querySelectorAll('.crop-box')).forEach((node) => node.remove());
  }

  function renderCropBoxes() {
    const frame = $('cropFrame');
    if (!frame || !crop.img) return;
    clearBoxNodes();
    const size = naturalSize();
    if (!size.width || !size.height) return;
    currentBoxes().slice(0, MAX_FRAGMENTS).forEach((box, index) => {
      const node = makeElement('div', 'crop-box');
      node.style.left = (box.x / size.width * 100) + '%';
      node.style.top = (box.y / size.height * 100) + '%';
      node.style.width = (box.w / size.width * 100) + '%';
      node.style.height = (box.h / size.height * 100) + '%';
      node.appendChild(makeElement('span', 'crop-box-label', LETTERS.charAt(index) || String(index + 1)));
      if (crop.mode === 'free') {
        node.appendChild(makeIconButton('crop-box-del', 'fa-xmark', null, () => {
          crop.boxes.splice(index, 1);
          renderCropBoxes();
          renderCropPreviews();
          updateCropCount();
        }, '删除此框'));
      }
      frame.appendChild(node);
    });
  }

  function cropCanvas(box, maxWidth, maxHeight) {
    const scale = Math.min(1, maxWidth / Math.max(1, box.w), maxHeight / Math.max(1, box.h));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(box.w * scale));
    canvas.height = Math.max(1, Math.round(box.h * scale));
    const ctx = canvas.getContext('2d');
    ctx.drawImage(crop.img, Math.round(box.x), Math.round(box.y), Math.round(box.w), Math.round(box.h),
      0, 0, canvas.width, canvas.height);
    return canvas;
  }

  function renderCropPreviews() {
    const host = $('cropResults');
    if (!host) return;
    host.textContent = '';
    if (!crop.img) return;
    currentBoxes().slice(0, MAX_FRAGMENTS).forEach((box, index) => {
      const item = makeElement('div', 'crop-result');
      const img = makeElement('img');
      img.src = cropCanvas(box, 176, 88).toDataURL('image/png');
      img.alt = '第 ' + (index + 1) + ' 块预览';
      item.appendChild(img);
      item.appendChild(makeElement('span', 'crop-result-label', LETTERS.charAt(index) || String(index + 1)));
      host.appendChild(item);
    });
  }

  function updateCropCount() {
    const count = crop.img ? Math.min(currentBoxes().length, MAX_FRAGMENTS) : 0;
    const badge = $('cropCountBadge');
    if (badge) badge.textContent = count + ' 块';
    const apply = $('cropApplyBtn');
    if (apply) apply.disabled = !count || crop.busy;
    const hint = $('cropHint');
    if (hint) {
      hint.textContent = count
        ? '确认无误后点右侧按钮：逐块上传并按 A/B/C/D 填入选项槽。'
        : '先在左侧确定要切出的区域。';
    }
  }

  function applyCropZoom() {
    const imgEl = $('cropSourceImg');
    if (!imgEl || !crop.baseWidth) return;
    imgEl.style.maxWidth = 'none';
    imgEl.style.width = Math.round(crop.baseWidth * crop.zoom) + 'px';
    const text = $('cropZoomText');
    if (text) text.textContent = Math.round(crop.zoom * 100) + '%';
    renderCropBoxes();
  }

  function setCropMode(mode, silent) {
    crop.mode = mode === 'free' ? 'free' : 'grid';
    ['grid', 'free'].forEach((name) => {
      const button = $('cropMode' + (name === 'grid' ? 'Grid' : 'Free'));
      if (button) button.setAttribute('aria-pressed', name === crop.mode ? 'true' : 'false');
    });
    const gridControls = $('cropGridControls');
    const freeControls = $('cropFreeControls');
    if (gridControls) gridControls.classList.toggle('hidden', crop.mode !== 'grid');
    if (freeControls) freeControls.classList.toggle('hidden', crop.mode !== 'free');
    if (!silent && crop.mode === 'free' && !crop.boxes.length) {
      toast('在图片上按住鼠标左键拖动即可加框，可连续切多块', 'info');
    }
    renderCropBoxes();
    renderCropPreviews();
    updateCropCount();
  }

  function setGridSize(rows, cols, presetKey) {
    crop.rows = clamp(rows, 1, 9);
    crop.cols = clamp(cols, 1, 9);
    const rowsInput = $('cropRows');
    const colsInput = $('cropCols');
    if (rowsInput) rowsInput.value = String(crop.rows);
    if (colsInput) colsInput.value = String(crop.cols);
    document.querySelectorAll('.crop-preset-btn').forEach((button) => {
      button.setAttribute('aria-pressed', presetKey && button.getAttribute('data-crop-preset') === presetKey ? 'true' : 'false');
    });
    renderCropBoxes();
    renderCropPreviews();
    updateCropCount();
  }

  function bindCropDrag() {
    const frame = $('cropFrame');
    if (!frame) return;
    let dragging = null;
    let ghost = null;

    const toNatural = (clientX, clientY) => {
      const rect = frame.getBoundingClientRect();
      const size = naturalSize();
      if (!rect.width || !rect.height) return { x: 0, y: 0 };
      return {
        x: clamp((clientX - rect.left) / rect.width, 0, 1) * size.width,
        y: clamp((clientY - rect.top) / rect.height, 0, 1) * size.height
      };
    };

    frame.addEventListener('pointerdown', (e) => {
      if (crop.mode !== 'free' || !crop.img || e.button !== 0) return;
      e.preventDefault();
      dragging = { start: toNatural(e.clientX, e.clientY) };
      ghost = makeElement('div', 'crop-box is-dragging');
      frame.appendChild(ghost);
      if (frame.setPointerCapture) frame.setPointerCapture(e.pointerId);
    });

    frame.addEventListener('pointermove', (e) => {
      if (!dragging || !ghost) return;
      const now = toNatural(e.clientX, e.clientY);
      const size = naturalSize();
      const box = {
        x: Math.min(dragging.start.x, now.x),
        y: Math.min(dragging.start.y, now.y),
        w: Math.abs(now.x - dragging.start.x),
        h: Math.abs(now.y - dragging.start.y)
      };
      ghost.style.left = (box.x / Math.max(1, size.width) * 100) + '%';
      ghost.style.top = (box.y / Math.max(1, size.height) * 100) + '%';
      ghost.style.width = (box.w / Math.max(1, size.width) * 100) + '%';
      ghost.style.height = (box.h / Math.max(1, size.height) * 100) + '%';
    });

    const finish = (e) => {
      if (!dragging) return;
      const now = toNatural(e.clientX, e.clientY);
      const box = {
        x: Math.min(dragging.start.x, now.x),
        y: Math.min(dragging.start.y, now.y),
        w: Math.abs(now.x - dragging.start.x),
        h: Math.abs(now.y - dragging.start.y)
      };
      dragging = null;
      if (ghost) { ghost.remove(); ghost = null; }
      if (box.w >= 6 && box.h >= 6) {
        if (crop.boxes.length >= MAX_FRAGMENTS) {
          toast('最多切 ' + MAX_FRAGMENTS + ' 块，已够用', 'warning');
        } else {
          crop.boxes.push(box);
        }
      }
      renderCropBoxes();
      renderCropPreviews();
      updateCropCount();
    };

    frame.addEventListener('pointerup', finish);
    frame.addEventListener('pointercancel', () => {
      dragging = null;
      if (ghost) { ghost.remove(); ghost = null; }
    });
  }

  function cropToBlob(box) {
    return new Promise((resolve, reject) => {
      const canvas = cropCanvas(box, box.w, box.h);
      canvas.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error('画布导出失败'));
      }, 'image/png');
    });
  }

  function uploadFragment(blob, index) {
    const file = new File([blob], 'optcrop_' + Date.now() + '_' + index + '.png', { type: 'image/png' });
    const form = new FormData();
    form.append('file', file);
    return fetch('/api/upload', { method: 'POST', body: form })
      .then((response) => response.json())
      .then((data) => {
        if (data.status !== 'success') throw new Error(data.message || '上传失败');
        return data.file_path;
      });
  }

  function ensureChoiceType() {
    const select = $('editQType');
    if (!select || CHOICE_TYPES.indexOf(select.value) !== -1) return;
    select.value = 'single_choice';
    select.dispatchEvent(new Event('change'));
  }

  function suggestOptionColumns(options) {
    const select = $('editOptionColumns');
    if (!select) return;
    const imageOnly = options.length >= 2 && options.every((option) => !option.text && option.images.length);
    if (!imageOnly) return;
    const wanted = options.length >= 4 ? '4' : (options.length === 2 ? '2' : '1');
    if (!select.querySelector('option[value="' + wanted + '"]') || select.value === wanted) return;
    select.value = wanted;
    select.dispatchEvent(new Event('change'));
    toast('选项全是图片，已把「选项列数」设为 ' + wanted + ' 列', 'info');
  }

  function fillOptionsFromFragments(urls) {
    const area = stemArea();
    if (!area) return;
    ensureChoiceType();
    syncFromStem(true);

    const sourceUrl = crop.sourceUrl;
    const options = card.options.map((option) => {
      const copy = buildOption(option.label, option.raw);
      copy.text = option.text;
      copy.edited = option.edited;
      copy.images = sourceUrl
        ? option.images.filter((image) => image.url !== sourceUrl)
        : option.images.slice();
      return copy;
    });
    while (options.length < urls.length) {
      options.push(buildOption(LETTERS.charAt(options.length) || String(options.length + 1), ''));
    }
    urls.forEach((url, index) => {
      const option = options[index];
      option.images.push({ token: '![选项' + option.label + '](' + url + ')', url });
      option.edited = true;
    });

    // 原来那张整张拼图已经变成各选项的分块，题干里那份就删掉，避免重复出现
    if (crop.sourceToken && card.prefix.indexOf(crop.sourceToken) !== -1) {
      card.prefix = card.prefix.split(crop.sourceToken).join('')
        .replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').replace(/^\s+|\s+$/g, '');
    }

    card.options = options;
    writeStem();
    renderRows();
    suggestOptionColumns(options);
    toast('已切开 ' + urls.length + ' 块并填入选项槽', 'success');
  }

  function applyCropFragments() {
    if (crop.busy) return;
    const boxes = currentBoxes().slice(0, MAX_FRAGMENTS);
    if (!boxes.length) {
      toast('先确定要切出的区域', 'warning');
      return;
    }
    const totalPixels = boxes.reduce((sum, box) => sum + box.w * box.h, 0);
    if (totalPixels > MAX_TOTAL_PIXELS) {
      toast('切分总像素过大，请缩小选区或调高收边比例', 'error');
      return;
    }
    crop.busy = true;
    const session = crop.session;
    const apply = $('cropApplyBtn');
    if (apply) apply.disabled = true;
    const urls = [];
    let chain = Promise.resolve();
    boxes.forEach((box, index) => {
      chain = chain
        .then(() => cropToBlob(box))
        .then((blob) => {
          if (blob.size > MAX_UPLOAD_BYTES) throw new Error('第 ' + (index + 1) + ' 块超过 10MB');
          return uploadFragment(blob, index);
        })
        .then((url) => { urls.push(url); });
    });
    chain.then(() => {
      if (crop.session !== session) return; // 用户已取消这一批
      crop.busy = false;
      fillOptionsFromFragments(urls);
      closeCropDialog();
    }).catch((error) => {
      if (crop.session !== session) return;
      crop.busy = false;
      if (apply) apply.disabled = false;
      toast('切图上传失败：' + (error && error.message ? error.message : error), 'error');
    });
  }

  function closeCropDialog() {
    // 关闭即作废：在途那批切片传完后不再回填题干，残留文件交给后端 1 小时孤儿回收
    crop.session += 1;
    crop.busy = false;
    if (typeof window.closeModalWithTransition === 'function') {
      window.closeModalWithTransition('optionCropModal');
    }
    if (crop.objectUrl) {
      URL.revokeObjectURL(crop.objectUrl);
      crop.objectUrl = '';
    }
    crop.img = null;
    crop.url = '';
    crop.sourceUrl = '';
    crop.sourceToken = '';
    crop.boxes = [];
  }

  function cropSourceForOption(index) {
    const option = card.options[index];
    if (option && option.images.length) {
      const image = option.images[0];
      return { url: imageProxyUrl(image.url), token: image.token, sourceUrl: image.url };
    }
    return bestStemImageSource();
  }

  function bestStemImageSource() {
    const area = stemArea();
    const text = area ? area.value : '';
    const tokens = (text.match(MD_IMAGE_RE) || []).concat(text.match(LATEX_IMAGE_RE) || []);
    if (tokens.length) {
      const token = tokens[0];
      const path = (token.match(/\(([^)\s]+)/) || [])[1] || (token.match(/\{([^}]*)\}/) || [])[1] || '';
      return { url: imageProxyUrl(path), token, sourceUrl: path };
    }
    return lastMediaSource();
  }

  function lastMediaSource() {
    const file = window.lastMediaFile;
    if (file && typeof URL !== 'undefined' && URL.createObjectURL) {
      if (crop.objectUrl) URL.revokeObjectURL(crop.objectUrl);
      crop.objectUrl = URL.createObjectURL(file);
      return { url: crop.objectUrl, sourceUrl: '' };
    }
    const preview = $('mediaPreviewImg');
    return preview && preview.src ? { url: preview.src, sourceUrl: '' } : null;
  }

  function openCropDialog(source) {
    if (!source || !source.url) {
      toast('没有可切分的图片：先在素材通道贴一张选项拼图，或点已上传选项图上的「切」', 'warning');
      return;
    }
    if (typeof window.openModalWithTransition !== 'function') {
      toast('弹窗助手尚未加载，请刷新页面后重试', 'error');
      return;
    }
    const image = new Image();
    image.onload = () => {
      crop.img = image;
      crop.url = source.url;
      crop.sourceUrl = source.sourceUrl || '';
      crop.sourceToken = source.token || '';
      crop.boxes = [];
      crop.zoom = 1;
      crop.baseWidth = Math.min(Math.max(1, image.naturalWidth || image.width || 600), 720);
      const imgEl = $('cropSourceImg');
      imgEl.src = source.url;
      applyCropZoom();
      setCropMode(crop.mode, true);
      window.openModalWithTransition('optionCropModal', { onEscape: closeCropDialog });
    };
    image.onerror = () => toast('图片读取失败，无法切分', 'error');
    image.src = source.url;
  }

  function bindCropToolbar() {
    const close = $('optionCropCloseBtn');
    if (close) close.addEventListener('click', closeCropDialog);
    const gridBtn = $('cropModeGrid');
    if (gridBtn) gridBtn.addEventListener('click', () => setCropMode('grid'));
    const freeBtn = $('cropModeFree');
    if (freeBtn) freeBtn.addEventListener('click', () => setCropMode('free'));
    document.querySelectorAll('.crop-preset-btn').forEach((button) => {
      button.addEventListener('click', () => {
        const parts = button.getAttribute('data-crop-preset').split('x');
        setGridSize(parts[0], parts[1], button.getAttribute('data-crop-preset'));
      });
    });
    const rowsInput = $('cropRows');
    const colsInput = $('cropCols');
    const syncGrid = () => {
      if (rowsInput) crop.rows = clamp(rowsInput.value, 1, 9);
      if (colsInput) crop.cols = clamp(colsInput.value, 1, 9);
      document.querySelectorAll('.crop-preset-btn').forEach((button) => button.setAttribute('aria-pressed', 'false'));
      renderCropBoxes();
      renderCropPreviews();
      updateCropCount();
    };
    if (rowsInput) rowsInput.addEventListener('change', syncGrid);
    if (colsInput) colsInput.addEventListener('change', syncGrid);
    const inset = $('cropInset');
    const insetText = $('cropInsetValue');
    if (inset) {
      inset.addEventListener('input', () => {
        crop.inset = clamp(inset.value, 0, 15);
        if (insetText) insetText.textContent = crop.inset + '%';
        renderCropBoxes();
        renderCropPreviews();
        updateCropCount();
      });
    }
    const zoomOut = $('cropZoomOut');
    if (zoomOut) zoomOut.addEventListener('click', () => { crop.zoom = clamp(crop.zoom / 1.25, 0.5, 3); applyCropZoom(); });
    const zoomIn = $('cropZoomIn');
    if (zoomIn) zoomIn.addEventListener('click', () => { crop.zoom = clamp(crop.zoom * 1.25, 0.5, 3); applyCropZoom(); });
    const zoomReset = $('cropResetZoom');
    if (zoomReset) zoomReset.addEventListener('click', () => { crop.zoom = 1; applyCropZoom(); });
    const clear = $('cropClearBtn');
    if (clear) clear.addEventListener('click', () => { crop.boxes = []; renderCropBoxes(); renderCropPreviews(); updateCropCount(); });
    const apply = $('cropApplyBtn');
    if (apply) apply.addEventListener('click', applyCropFragments);
    bindCropDrag();
    setCropMode('grid', true);
  }

  function bindCard() {
    const area = stemArea();
    if (area) area.addEventListener('input', refreshVisibility);
    const qtype = $('editQType');
    if (qtype) qtype.addEventListener('change', refreshVisibility);
    const add = $('optionAddBtn');
    if (add) {
      add.addEventListener('click', () => {
        if (!isChoiceType()) ensureChoiceType();
        syncFromStem(false);
        const label = LETTERS.charAt(card.options.length) || String(card.options.length + 1);
        card.options.push(buildOption(label, ''));
        writeStem();
        renderRows();
      });
    }
    const split = $('optionSplitBtn');
    if (split) split.addEventListener('click', () => openCropDialog(bestStemImageSource()));
    const mediaCrop = $('mediaOpenCropBtn');
    if (mediaCrop) mediaCrop.addEventListener('click', () => openCropDialog(lastMediaSource() || bestStemImageSource()));
    bindCropToolbar();
    refreshVisibility();
  }

  // 导出纯逻辑：前端回归测试用假 DOM 直接驱动解析与几何，不依赖浏览器
  window.OptionCardInternals = {
    parseStem: parseStem,
    lineOptions: lineOptions,
    findMarks: findMarks,
    buildOption: buildOption,
    optionBody: optionBody,
    computeGridBoxes: computeGridBoxes,
    LETTERS: LETTERS
  };

  // 供其它模块在程序化改写题干、载入题目或恢复草稿后主动刷新卡片。
  // 默认只做签名比对：内容没变就不重建 DOM，免得用户在选项行里打字时被重渲染抢走焦点。
  window.refreshOptionEditorCard = function (force) {
    refreshVisibility();
    if (force === true) syncFromStem(true);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindCard);
  } else {
    bindCard();
  }
})();
