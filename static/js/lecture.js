/* ================================================================
 * 讲义工作台（MathBank Lecture）
 * 依赖：api.js（fetch 打点/localToken）、editor.js（parseMarkdownWithMath）
 * 数据流：小节 → 考向树+题库选题 → 角色编排 → POST /api/lecture/preview
 *         → buildLectureHtml() → iframe.srcdoc（A4 打印视图）
 * 知识块（知识梳理/易错点/解题方法）支持手动编辑并入库复用。
 * ================================================================ */
(function () {
  'use strict';

  var ST = {
    grade: 'g1', compulsory: '', chapter: '', knowledge: '',
    sections: [],        // 多节合并的唯一容器 [{compulsory,chapter,knowledge,section,topics,extraIds,questions}]
    activeKey: '',       // 当前激活节的键 compulsory|chapter|knowledge
    topics: [],          // → 激活节的编排数组（面板操作唯一入口，禁止平行状态）
    extraIds: [],        // → 激活节的实战演练数组
    questions: [],       // → 激活节已加载的题库数据
    settings: null,      // 讲义设置
    noteTarget: null,    // 当前编辑的知识块目标
    draftId: null,       // 当前草稿 id
    draftStamp: '',      // 上次落库时的 抽屉|小节|届次，用于判断标题是否需要重写
    coverBg: {},         // 封面背景库
    previewData: null,
    view: 'yuan',        // 当前打印视图：yuan 学生版 / jiexi 教师版 / daan 答案册
    yearFilter: '',      // 顶部届次筛选：'' 为全部（只显示最新架子）
    knowledgeMind: null, // → 激活节思维导图知识梳理 {data,raw}
    topicMetaById: {},   // 大纲考向 topic_id → {no, example}，编号与默认内容同一口径
    qIndex: {},          // 预览题目查找表 id→题（整本一份，跨节共用）
    dirty: false,        // 编排改过但还没写入草稿
    autosaveBlocked: false, // LocalStorage 写不进（配额/隐私模式），只提示一次
    autosaveBound: false,
  };

  var ROLE_LABELS = { example: '例题', variant: '变式', practice: '趁热打铁', homework: '实战演练' };

  function $(id) { return document.getElementById(id); }

  function toast(msg, type) {
    if (window.showToast) { window.showToast(msg, type); return; }
    alert(msg);
  }

  /* 转义的唯一来源是 api.js 的 window.escapeHtml（MathBankSafe.escapeText），
     这里只留模块内短名，不再自带第二套实现。 */
  function esc(s) { return window.escapeHtml(s); }

  function roleVal(role) { return ROLE_LABELS[role] || ROLE_LABELS.example; }

  /* 讲义模块唯一的请求出口：后端 4xx/5xx 的错误体同样是合法 JSON，
     裸 fetch + r.json() 会把「请求被拒绝」当成功，继续执行删除/清空等下游动作。 */
  function apiCall(url, options, asBlob) {
    return fetch(url, options).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error((body && (body.detail || body.message)) || ('HTTP ' + r.status));
          err.status = r.status;
          err.body = body;
          throw err;
        });
      }
      if (!asBlob) return r.json();
      var notes = r.headers.get('X-MathBank-Export-Notes') || '';
      try { notes = notes ? decodeURIComponent(notes) : ''; } catch (e) { notes = ''; }
      return r.blob().then(function (b) { return { blob: b, notes: notes }; });
    });
  }

  function apiJson(url, options) { return apiCall(url, options, false); }
  function apiBlob(url, options) { return apiCall(url, options, true); }

  /* ---------------- 级联选择 ---------------- */
  function metaCur() {
    var m = window.systemMetadata;
    return (m && m.curriculum) ? m.curriculum : {};
  }

  function renderGrade() {
    var sel = $('lectureGrade');
    if (!sel) return;
    sel.innerHTML = '';
    [['g1', '高一'], ['g2', '高二'], ['g3', '高三']].forEach(function (g) {
      var o = document.createElement('option');
      o.value = g[0]; o.textContent = g[1];
      sel.appendChild(o);
    });
    sel.value = ST.grade || 'g1';
  }

  function renderCompulsory() {
    var cur = metaCur();
    var sel = $('lectureCompulsory');
    if (!sel) return;
    var keys = Object.keys(cur);
    sel.innerHTML = '';
    keys.forEach(function (k) {
      var o = document.createElement('option');
      o.value = k; o.textContent = k;
      sel.appendChild(o);
    });
    if (keys.indexOf(ST.compulsory) >= 0) sel.value = ST.compulsory;
    else if (keys.length) { sel.value = keys[0]; ST.compulsory = keys[0]; }
    renderChapter();
  }

  function renderChapter() {
    var cur = metaCur();
    var comp = $('lectureCompulsory') ? $('lectureCompulsory').value : ST.compulsory;
    var chapters = (cur[comp] || {});
    var sel = $('lectureChapter');
    if (!sel) return;
    var keys = Object.keys(chapters);
    sel.innerHTML = '';
    keys.forEach(function (k) {
      var o = document.createElement('option');
      o.value = k; o.textContent = k;
      sel.appendChild(o);
    });
    if (keys.indexOf(ST.chapter) >= 0) sel.value = ST.chapter;
    else if (keys.length) { sel.value = keys[0]; ST.chapter = keys[0]; }
    renderKnowledgeDropdown();
  }

  /* ---------------- 多节合并状态 ----------------
     ST.sections 是编排的唯一容器；ST.topics/extraIds/questions 只是指向
     激活节内部数组的别名，面板操作永远落在激活节上。 */
  function secKey(compulsory, chapter, knowledge) {
    return [compulsory, chapter, knowledge].join('|');
  }

  function sectionOf(entry) {
    // 纯取数：从激活节派生，测试与运行期同一口径
    return entry ? { compulsory: entry.compulsory, chapter: entry.chapter, knowledge: entry.knowledge } : null;
  }

  function findSectionEntry(key) {
    for (var i = 0; i < ST.sections.length; i++) {
      if (ST.sections[i].key === key) return ST.sections[i];
    }
    return null;
  }

  function ensureSectionEntry(compulsory, chapter, knowledge) {
    var key = secKey(compulsory, chapter, knowledge);
    var entry = findSectionEntry(key);
    if (!entry) {
      entry = {
        key: key, compulsory: compulsory, chapter: chapter, knowledge: knowledge,
        section: null, topics: [], extraIds: [], questions: [],
      };
      ST.sections.push(entry);
    }
    return entry;
  }

  function activateSection(entry) {
    ST.activeKey = entry.key;
    ST.compulsory = entry.compulsory;
    ST.chapter = entry.chapter;
    ST.knowledge = entry.knowledge;
    ST.section = entry.section;
    ST.topics = entry.topics;
    ST.extraIds = entry.extraIds;
    ST.questions = entry.questions;
    ST.knowledgeMind = null;
  }

  function switchActive(key) {
    var entry = findSectionEntry(key);
    if (!entry) return;
    activateSection(entry);
    renderKnowledgeDropdown();
    renderSectionChips();
    if (entry.section) {
      renderTopicPanel(entry.section, entry.questions);
      refreshPreview();
    } else {
      loadSection({ keepSelection: true });
    }
  }

  function removeSectionEntry(key) {
    var idx = -1;
    for (var i = 0; i < ST.sections.length; i++) { if (ST.sections[i].key === key) { idx = i; break; } }
    if (idx < 0) return;
    ST.sections.splice(idx, 1);
    var wasActive = ST.activeKey === key;
    if (wasActive) {
      var next = ST.sections[Math.min(idx, ST.sections.length - 1)];
      if (next) activateSection(next);
      else {
        ST.activeKey = ''; ST.section = null;
        ST.topics = []; ST.extraIds = []; ST.questions = [];
        ST.knowledgeMind = null;
      }
    }
    renderSectionChips();
    if (wasActive) {
      renderTopicPanelFromState();
      refreshPreview();
    }
    commitOrchestration();
  }

  function collectSectionsPayload() {
    return ST.sections.map(function (e) {
      return {
        compulsory: e.compulsory, chapter: e.chapter, knowledge: e.knowledge,
        topics: e.topics, extra_ids: e.extraIds,
      };
    });
  }

  /* ---------------- 小节多选下拉（事件委托，坐标绝不拼进内联属性） ---------------- */
  function selectedKeys() {
    return ST.sections.map(function (e) { return e.key; });
  }

  function knowledgeOptions() {
    var cur = metaCur();
    var comp = $('lectureCompulsory') ? $('lectureCompulsory').value : ST.compulsory;
    var chap = $('lectureChapter') ? $('lectureChapter').value : ST.chapter;
    var sections = (cur[comp] || {})[chap] || [];
    return sections.map(function (sec) {
      return typeof sec === 'string' ? sec : (sec && sec.name) || '';
    }).filter(Boolean);
  }

  function toggleSection(name) {
    var comp = $('lectureCompulsory') ? $('lectureCompulsory').value : ST.compulsory;
    var chap = $('lectureChapter') ? $('lectureChapter').value : ST.chapter;
    if (!comp || !chap || !name) return;
    var key = secKey(comp, chap, name);
    if (findSectionEntry(key)) {
      removeSectionEntry(key);
      return;
    }
    var entry = ensureSectionEntry(comp, chap, name);
    activateSection(entry);
    renderKnowledgeDropdown();
    renderSectionChips();
    loadSection({ keepSelection: true });
  }

  function addCurrentSection() {
    var comp = $('lectureCompulsory') ? $('lectureCompulsory').value : ST.compulsory;
    var chap = $('lectureChapter') ? $('lectureChapter').value : ST.chapter;
    var know = knowledgeOptions()[0] || ST.knowledge;
    if (!comp || !chap || !know) { toast('请先选择册次 / 章节，小节在「小节」多选里添加'); return; }
    var key = secKey(comp, chap, know);
    if (findSectionEntry(key)) { loadSection(); return; } // 已在清单里：重新加载而不重复添加
    var entry = ensureSectionEntry(comp, chap, know);
    activateSection(entry);
    renderSectionChips();
    loadSection({ keepSelection: true });
  }

  function renderKnowledgeDropdown() {
    var dd = $('lectureKnowledgeDropdown');
    if (!dd) return;
    var opts = knowledgeOptions();
    var sel = selectedKeys();
    while (dd.firstChild) dd.removeChild(dd.firstChild);
    if (!opts.length) {
      var empty = document.createElement('div');
      empty.className = 'px-3 py-2 text-xs text-slate-400';
      empty.textContent = '当前章节下没有小节';
      dd.appendChild(empty);
      return;
    }
    opts.forEach(function (name) {
      var key = secKey(
        $('lectureCompulsory') ? $('lectureCompulsory').value : ST.compulsory,
        $('lectureChapter') ? $('lectureChapter').value : ST.chapter,
        name);
      var row = document.createElement('label');
      row.className = 'flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800';
      var box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = sel.indexOf(key) >= 0;
      box.addEventListener('change', function () { toggleSection(name); });
      var span = document.createElement('span');
      span.textContent = name;
      row.appendChild(box);
      row.appendChild(span);
      dd.appendChild(row);
    });
  }

  function renderSectionChips() {
    var bar = $('lectureSectionChips');
    if (!bar) return;
    while (bar.firstChild) bar.removeChild(bar.firstChild);
    var count = $('lectureSectionCount');
    if (count) {
      count.textContent = String(ST.sections.length);
      count.classList.toggle('hidden', !ST.sections.length);
    }
    if (!ST.sections.length) return;
    ST.sections.forEach(function (e) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.setAttribute('data-key', e.key);
      chip.className = 'rounded-full border px-2.5 py-0.5 text-xs max-w-[220px] truncate ' +
        (e.key === ST.activeKey
          ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 font-bold'
          : 'border-slate-300 dark:border-slate-700 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800');
      chip.title = e.compulsory + ' · ' + e.chapter + ' · ' + e.knowledge + '（点击切换，悬停点 × 移除）';
      chip.textContent = e.knowledge;
      var x = document.createElement('span');
      x.setAttribute('data-remove', '1');
      x.className = 'ml-1 text-slate-400 hover:text-red-500';
      x.textContent = '\u00d7';
      chip.appendChild(x);
      bar.appendChild(chip);
    });
  }

  var _sectionBarBound = false;
  function bindSectionBar() {
    if (_sectionBarBound) return;
    _sectionBarBound = true;
    var bar = $('lectureSectionChips');
    if (bar) {
      bar.addEventListener('click', function (e) {
        var el = e.target;
        while (el && el !== bar && !(el.hasAttribute && el.hasAttribute('data-key'))) el = el.parentNode;
        if (!el || el === bar) return;
        var key = el.getAttribute('data-key');
        var isRemove = e.target && e.target.hasAttribute && e.target.hasAttribute('data-remove');
        if (isRemove) removeSectionEntry(key);
        else if (key !== ST.activeKey) switchActive(key);
      });
    }
    var btn = $('lectureKnowledgeBtn');
    var dd = $('lectureKnowledgeDropdown');
    if (btn && dd) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        renderKnowledgeDropdown();
        dd.classList.toggle('hidden');
      });
      dd.addEventListener('click', function (e) { e.stopPropagation(); });
      document.addEventListener('click', function () { dd.classList.add('hidden'); });
    }
  }

  /* ---------------- 题目加载（按小节拉题） ---------------- */
  // 后端 page_size 上限是 100（routes_questions 的 max(1, min(page_size, 100))），
  // 传 200 会被静默裁成 100：小节题量一大就少题且毫无提示。
  var Q_PAGE_SIZE = 100;
  var Q_MAX_PAGES = 10;

  function loadQuestions(entry) {
    var comp = entry ? entry.compulsory : ($('lectureCompulsory') ? $('lectureCompulsory').value : '');
    var chap = entry ? entry.chapter : ($('lectureChapter') ? $('lectureChapter').value : '');
    var know = entry ? entry.knowledge : ST.knowledge;
    var base = 'compulsory=' + encodeURIComponent(comp) +
      '&chapter=' + encodeURIComponent(chap) +
      '&knowledge=' + encodeURIComponent(know) +
      '&page_size=' + Q_PAGE_SIZE + '&sort=asc';
    var page = function (n) { return apiJson('/api/questions?' + base + '&page=' + n); };
    var rows = function (data) { return (data && data.items) || (Array.isArray(data) ? data : []); };
    var total = 0;
    return page(1).then(function (first) {
      var acc = rows(first).slice();
      total = (first && first.total) || acc.length;
      var pages = Math.min(Math.ceil(total / Q_PAGE_SIZE) || 1, Q_MAX_PAGES);
      var next = function (n) {
        if (n > pages) return Promise.resolve(acc);
        return page(n).then(function (d) { acc = acc.concat(rows(d)); return next(n + 1); });
      };
      return next(2);
    }).then(function (all) {
      // 取完仍不足 total = 撞到 Q_MAX_PAGES 上限，必须说出来而不是假装这就是全部
      if (total > all.length) {
        toast('本小节共 ' + total + ' 题，仅载入前 ' + all.length + ' 题，其余题目未参与选题', 'error');
      }
      ST.questions = all;
      if (entry) entry.questions = all;
      return all;
    });
  }

  /* ---------------- 考向面板 ---------------- */
  /* ST.topics 必须与 ST.section.topics 按下标对齐：面板把 idx 直接传给
     addItem / setRole / removeItem / editNote，错位就会改到别的考向。
     旧写法只在渲染时临时造一个 { topic_id, items: [] } 而不落进 ST.topics，
     于是新建讲义（config.topics 为空）点选题和易错点全部静默失效，
     考向页也整段不进预览。 */
  function ensureTopicConfigs() {
    var topics = (ST.section && ST.section.topics) || [];
    var byId = {};
    ST.topics.forEach(function (t) {
      if (t && t.topic_id && !byId[t.topic_id]) byId[t.topic_id] = t;
    });
    var out = [];
    topics.forEach(function (t) {
      var cfg = byId[t.id];
      if (cfg) { cfg.name = t.name || cfg.name; cfg.items = cfg.items || []; out.push(cfg); return; }
      out.push({ topic_id: t.id, name: t.name, items: [] });
    });
    // 大纲里已不存在的旧编排留在末尾：换教材树后静默丢弃用户选题是不可接受的
    ST.topics.forEach(function (t) { if (t && out.indexOf(t) < 0) out.push(t); });
    ST.topics = out;
    // 数组被整体替换时必须重新锚定激活节，否则面板改动会写进旧数组、节里丢编排
    var entry = ensureActiveEntry();
    if (entry) entry.topics = ST.topics;
  }

  function renderTopicPanel(section, questions) {
    var panel = $('lectureTopicPanel');
    if (!panel) return;
    var topics = section.topics || [];
    ensureTopicConfigs();
    if (!topics.length) {
      panel.innerHTML = '<div class="text-sm text-slate-400 p-4">该小节暂无考向，请先在「自定义维度配置」中添加考向。</div>';
      return;
    }
    var html = '<div class="text-xs text-slate-400 px-1 pb-2">按考向选题并分配角色：例题 → 变式（挂例题下）→ 趁热打铁 → 实战演练</div>';
    var qByTopic = {};
    questions.forEach(function (q) {
      var t = q.topic_id || '';
      (qByTopic[t] = qByTopic[t] || []).push(q);
    });
    topics.forEach(function (t, idx) {
      var tq = qByTopic[t.id] || [];
      var cfg = ST.topics[idx];
      html += '<div class="rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-3">';
      html += '<div class="flex items-center justify-between mb-2">';
      html += '<div class="font-bold text-sm text-slate-700 dark:text-slate-200">考向' + (t.no || idx + 1) + '　' + esc(t.name) + '</div>';
      html += '<div class="text-xs text-slate-400">' + tq.length + ' 题可选</div></div>';
      // 易错点 / 解题方法
      html += '<div class="flex gap-2 mb-2">';
      html += '<button onclick="LectureManager.editNote(\'pit\', ' + idx + ')" class="text-xs px-2 py-1 rounded bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-800 hover:bg-amber-100">易错点</button>';
      html += '<button onclick="LectureManager.editNote(\'method\', ' + idx + ')" class="text-xs px-2 py-1 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-800 hover:bg-blue-100">解题方法</button>';
      html += '</div>';
      // 已编排题目
      if (cfg.items.length) {
        html += '<div class="space-y-1.5 mb-2">';
        cfg.items.forEach(function (item, ii) {
          var q = null;
          for (var k = 0; k < questions.length; k++) { if (questions[k].id === item.question_id) { q = questions[k]; break; } }
          html += '<div class="flex items-center gap-2 bg-slate-50 dark:bg-slate-800 rounded-lg px-2 py-1.5">';
          html += '<span class="text-xs px-1.5 py-0.5 rounded bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300 font-bold">' + roleVal(item.role) + '</span>';
          html += '<span class="flex-1 text-xs text-slate-500 truncate">' + esc(q ? (q.content || '').replace(/[#*$\\]/g, '').slice(0, 60) : ('#' + item.question_id)) + '</span>';
          html += '<select onchange="LectureManager.setRole(' + idx + ',' + ii + ',this.value)" class="text-xs rounded border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 px-1 py-0.5">';
          ['example', 'variant', 'practice', 'homework'].forEach(function (r) {
            html += '<option value="' + r + '"' + (item.role === r ? ' selected' : '') + '>' + ROLE_LABELS[r] + '</option>';
          });
          html += '</select>';
          html += '<button onclick="LectureManager.removeItem(' + idx + ',' + ii + ')" class="text-slate-400 hover:text-red-500 text-sm leading-none">&times;</button>';
          html += '</div>';
        });
        html += '</div>';
      }
      // 可选题目
      if (tq.length) {
        html += '<div class="space-y-1">';
        tq.forEach(function (q) {
          var added = cfg.items.some(function (it) { return it.question_id === q.id; });
          var diff = q.difficulty ? esc(q.difficulty) : '';
          html += '<div class="flex items-center gap-2 text-xs rounded-lg px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800 border border-transparent hover:border-slate-200">';
          html += '<span class="text-slate-400">#' + q.id + '</span>';
          html += '<span class="flex-1 text-slate-500 truncate">' + esc((q.content || '').replace(/[#*$\\]/g, '').slice(0, 50)) + '</span>';
          if (diff) html += '<span class="text-slate-400">' + diff + '</span>';
          html += '<button onclick="LectureManager.addItem(' + idx + ',' + q.id + ')" class="px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 hover:bg-emerald-100">+ 选用</button>';
          html += '</div>';
        });
        html += '</div>';
      } else {
        html += '<div class="text-xs text-slate-400">该考向下暂无题目，可在录题时选择此考向。</div>';
      }
      html += '</div>';
    });
    // 实战演练（未分配考向的题或全部可选）
    var unused = questions.filter(function (q) {
      return !ST.topics.some(function (t) { return t.items.some(function (it) { return it.question_id === q.id; }); });
    });
    html += '<div class="rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 p-3">';
    html += '<div class="font-bold text-sm text-slate-700 dark:text-slate-200 mb-2">实战演练（课后作业）</div>';
    if (ST.extraIds.length) {
      html += '<div class="space-y-1.5 mb-2">';
      ST.extraIds.forEach(function (qid, ii) {
        var q = null;
        for (var k = 0; k < questions.length; k++) { if (questions[k].id === qid) { q = questions[k]; break; } }
        html += '<div class="flex items-center gap-2 bg-slate-50 dark:bg-slate-800 rounded-lg px-2 py-1.5">';
        html += '<span class="flex-1 text-xs text-slate-500 truncate">' + esc(q ? (q.content || '').replace(/[#*$\\]/g, '').slice(0, 60) : ('#' + qid)) + '</span>';
        html += '<button onclick="LectureManager.removeExtra(' + ii + ')" class="text-slate-400 hover:text-red-500 text-sm">&times;</button>';
        html += '</div>';
      });
      html += '</div>';
    }
    if (unused.length) {
      html += '<div class="space-y-1">';
      unused.forEach(function (q) {
        html += '<div class="flex items-center gap-2 text-xs rounded-lg px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800">';
        html += '<span class="text-slate-400">#' + q.id + '</span>';
        html += '<span class="flex-1 text-slate-500 truncate">' + esc((q.content || '').replace(/[#*$\\]/g, '').slice(0, 50)) + '</span>';
        html += '<button onclick="LectureManager.addExtra(' + q.id + ')" class="px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 hover:bg-emerald-100">+ 选用</button>';
        html += '</div>';
      });
      html += '</div>';
    } else if (!ST.extraIds.length) {
      html += '<div class="text-xs text-slate-400">当前小节题目已全部编排，可从其他小节选题加入实战演练。</div>';
    }
    html += '</div>';
    panel.innerHTML = html;
  }

  /* ---------------- 讲义 HTML 渲染 ---------------- */
  function matchCoverKey(title) {
    var order = ['空间向量', '圆锥曲线', '直线和圆', '成对数据', '随机变量', '计数原理', '立体几何', '解析几何', '三角函数', '指数', '对数', '不等式', '导数', '数列', '统计', '复数', '向量', '概率', '函数', '集合'];
    for (var i = 0; i < order.length; i++) {
      if (String(title || '').indexOf(order[i]) >= 0) return order[i];
    }
    return 'default';
  }

  function cleanChapterTitle(t) {
    var s = String(t || '').replace(/^\s*\d+(\.\d+)*[.．、]?\s*/, '');
    return s || String(t || '');
  }
  /* 讲次/考向的中文数字：纯加法进位，1→一 … 99→九十九，讲义规模内够用。 */
  var CN_DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];

  function cnNumeral(n) {
    var k = Math.floor(Number(n));
    if (!isFinite(k) || k < 1 || k > 99) return String(n == null ? '' : n);
    if (k < 10) return CN_DIGITS[k];
    if (k < 20) return '十' + (k % 10 ? CN_DIGITS[k % 10] : '');
    return CN_DIGITS[Math.floor(k / 10)] + '十' + (k % 10 ? CN_DIGITS[k % 10] : '');
  }

  /* 重编号只发生在渲染层：note_key 与草稿 config 仍存真实教材坐标。
     页眉统一「第X讲 <小节名> · …」，章名与教材章节号都不再出现。 */
  function lectureTitle(lectureNo, knowledge) {
    return '第' + cnNumeral(lectureNo) + '讲 ' + cleanChapterTitle(knowledge);
  }


  function buildCover(settings, sectionTitle, versionText) {
    var bg = ST.coverBg[matchCoverKey(sectionTitle)] || ST.coverBg.default || '';
    return '<div class="sheet cover">' + bg +
      '<div class="cover-brand">' + esc(settings.cover_brand || '') + '</div>' +
      '<div class="cover-subtitle">' + esc(settings.cover_subtitle || '同步讲义') + '</div>' +
      '<div class="main-title" data-cover-edit="title" contenteditable="true" spellcheck="false" title="点击直接修改封面标题">' + esc(sectionTitle) + '</div>' +
      '<div class="cover-author">' + esc(settings.cover_author || '崔老师 编著') + '</div>' +
      '<div class="cover-ver" id="cover-ver">' + esc(versionText) + '</div>' +
      '</div>';
  }

  /* 知识块修订稿键：必须与 mathbank/lecture_helper.note_key 逐字符同口径，
     否则前端算出的键在 notes 里永远查不到。 */
  function noteKey(type, compulsory, chapter, knowledge, topicId) {
    var parts = [type, compulsory, chapter, knowledge];
    if (topicId) parts.push(topicId);
    return parts.map(function (p) {
      var s = String(p == null ? '' : p);
      if (s.indexOf(':') < 0 && s.indexOf('\\') < 0) return s;
      return s.split('\\').join('\\\\').split(':').join('\\:');
    }).join(':');
  }

  function buildKnowledge(notes, compulsory, chapter, knowledge, headTitle) {
    var head = headTitle || (cleanChapterTitle(chapter) + ' · ' + knowledge);
    var key = noteKey('knowledge', compulsory, chapter, knowledge);
    var src = notes[key] || '';
    if (src.indexOf('@mindmap') === 0) {
      var mm = parseMindmap(src);
      if (!mm) {
        return '<div class="sheet" id="page-knowledge"><div class="chapter-info">' + esc(head) + ' · 知识梳理</div>' +
          '<div class="knowledge"><div class="kn-empty">' +
          '（思维导图数据无法解析，已停止输出原始源码；请在工具栏「知识梳理」中修复后重新保存）</div></div></div>';
      }
      return buildMindmapSheets(mm, compulsory, chapter, knowledge, src, head);
    }
    var body = src || '<div class="kn-empty">（尚未编写知识梳理，点击工具栏「知识梳理」开始编辑）</div>';
    return '<div class="sheet" id="page-knowledge"><div class="chapter-info">' + esc(head) + ' · 知识梳理</div>' +
      '<div class="knowledge">' + body + '</div></div>';
  }

  /* ---------------- 思维导图知识梳理 ---------------- */
  var MIND_COLORS = [
    { c: '#2563eb', b: '#eff6ff' },
    { c: '#ea580c', b: '#fff7ed' },
    { c: '#16a34a', b: '#f0fdf4' },
    { c: '#7c3aed', b: '#f5f3ff' },
    { c: '#e11d48', b: '#fff1f2' },
    { c: '#0d9488', b: '#f0fdfa' },
    { c: '#d97706', b: '#fffbeb' }
  ];

  function parseMindmap(src) {
    if (!src || src.indexOf('@mindmap') !== 0) return null;
    var text = String(src).replace(/^@mindmap\s*/, '').trim();
    try {
      var data = JSON.parse(text);
      if (data && typeof data === 'object' && Array.isArray(data.branches)) return data;
    } catch (e) { /* 非思维导图数据，退回普通 HTML 渲染 */ }
    return null;
  }

  function mindTextLines(s, perLine) {
    var t = String(s == null ? '' : s);
    var w = 0;
    for (var i = 0; i < t.length; i++) {
      var ch = t.charCodeAt(i);
      w += (ch > 0x2E7F || ch === 0x2014 || ch === 0x2026) ? 1 : 0.5;
    }
    return Math.max(1, Math.ceil(w / (perLine || 42)));
  }

  function mindItemHeight(item) {
    var h = 0;
    if (item.label != null) h += 4 + mindTextLines(item.label) * 6.4;
    if (item.table) {
      h += 8;
      item.table.forEach(function (r) {
        var lines = 1;
        (r || []).forEach(function (cell) { lines = Math.max(lines, mindTextLines(cell, 40)); });
        h += 5.2 + lines * 2.6;
      });
    }
    (item.items || []).forEach(function (sub) { h += mindItemHeight(sub) + 1.6; });
    return h;
  }

  function mindBranchHeight(branch) {
    var h = 11;
    (branch.items || []).forEach(function (it) { h += mindItemHeight(it) + 2.4; });
    return h;
  }

  function mindBuildTable(table, path) {
    var html = '<div class="mind-table-wrap"><table class="mind-table">';
    table.forEach(function (row, ri) {
      html += '<tr>';
      (row || []).forEach(function (cell, ci) {
        html += '<td data-mp-path="' + path + '.c.' + ri + '.' + ci + '">' + esc(cell) + '</td>';
      });
      html += '</tr>';
    });
    html += '</table></div>';
    return html;
  }

  function mindBuildItem(item, path) {
    var html = '';
    if (item.label != null) {
      html += '<div class="mind-item" data-mp-path="' + path + '">' + esc(item.label) + '</div>';
    }
    if (item.table) {
      html += mindBuildTable(item.table, path);
    }
    var kids = item.items || [];
    if (kids.length) {
      html += '<div class="mind-item-children">';
      kids.forEach(function (k, ki) { html += mindBuildItem(k, path + '.' + ki); });
      html += '</div>';
    }
    return html;
  }

  function mindBuildBranchHTML(branch, bi) {
    var pal = MIND_COLORS[bi % MIND_COLORS.length];
    var html = '<div class="mind-branch" style="--mc:' + pal.c + ';--mcb:' + pal.b + '">';
    html += '<div class="mind-branch-label" data-mp-path="b.' + bi + '" style="background:' + pal.c + '">' + esc(branch.label || '') + '</div>';
    html += '<div class="mind-branch-line"></div>';
    html += '<div class="mind-items">';
    (branch.items || []).forEach(function (it, ii) {
      html += mindBuildItem(it, 'b.' + bi + '.' + ii);
    });
    html += '</div></div>';
    return html;
  }

  function buildMindmapSheets(data, compulsory, chapter, knowledge, raw, headTitle) {
    var head = headTitle || (cleanChapterTitle(chapter) + ' · ' + knowledge);
    var entry = findSectionEntry(secKey(compulsory, chapter, knowledge)) || ensureActiveEntry();
    if (entry) entry.knowledgeMind = { data: data, raw: raw };
    ST.knowledgeMind = entry ? entry.knowledgeMind : null;
    var PAGE_BUDGET = 220;
    var pages = [];
    var cur = { branches: [], height: 0 };
    pages.push(cur);
    (data.branches || []).forEach(function (b, bi) {
      var h = mindBranchHeight(b);
      if (cur.branches.length && cur.height + h > PAGE_BUDGET) {
        cur = { branches: [], height: 0 };
        pages.push(cur);
      }
      cur.branches.push({ branch: b, bi: bi, h: h });
      cur.height += h + 5;
    });
    var html = '';
    pages.forEach(function (pg, pi) {
      html += '<div class="sheet"><div class="chapter-info">' + esc(head) + ' · 知识梳理</div>';
      html += '<div class="mind-hint">思维导图 · 点击任一文字块可直接编辑</div>';
      html += '<div class="mind-wrap">';
      if (pi === 0 && data.title) {
        html += '<div class="mind-title" data-mp-path="t">' + esc(data.title) + '</div>';
      }
      pg.branches.forEach(function (it) { html += mindBuildBranchHTML(it.branch, it.bi); });
      html += '</div>';
      html += '<div class="page-foot">第 ' + (pi + 1) + ' 页</div></div>';
    });
    return html;
  }

  function mindNodeAt(data, path) {
    if (!data || !path) return null;
    var parts = String(path).split('.');
    if (parts[0] === 't') return { kind: 'label', ref: data, prop: 'title', label: data.title || '', name: '中心主题' };
    if (parts[0] !== 'b') return null;
    var bi = parseInt(parts[1], 10);
    var branch = (data.branches || [])[bi];
    if (!branch) return null;
    if (parts.length === 2) return { kind: 'label', ref: branch, prop: 'label', label: branch.label || '', name: '分支：' + (branch.label || '') };
    var item = branch;
    var items = branch.items || [];
    for (var i = 2; i < parts.length; i++) {
      if (parts[i] === 'c') {
        var r = parseInt(parts[i + 1], 10), c2 = parseInt(parts[i + 2], 10);
        var tbl = (item && item.table) || null;
        if (!tbl || !tbl[r] || tbl[r][c2] == null) return null;
        return { kind: 'table', table: tbl, r: r, c: c2, name: '表格内容' };
      }
      var idx = parseInt(parts[i], 10);
      if (!items[idx]) return null;
      item = items[idx];
      items = item.items || [];
    }
    return { kind: 'label', ref: item, prop: 'label', label: item.label != null ? item.label : '', name: item.label != null ? String(item.label).slice(0, 24) : '内容' };
  }

  function editMindNode(path) {
    var entry = ensureActiveEntry();
    var mind = entry && entry.knowledgeMind;
    if (!mind || !mind.data) { toast('请先加载讲义小节'); return; }
    var node = mindNodeAt(mind.data, path);
    if (!node) return;
    ST.noteTarget = { type: 'mind-node', mindPath: path, name: node.name, key: entry.key };
    // 单节点编辑器不允许整块删除思维导图
    setNoteDeleteVisible(false);
    var titleEl = $('lectureNoteModalTitle');
    if (titleEl) titleEl.textContent = '编辑知识梳理 · ' + node.name;
    var ta = $('lectureNoteContent');
    if (ta) { ta.value = node.label != null ? node.label : ''; ta.focus(); }
    var modal = $('lectureNoteModal');
    if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
  }

  var _mindBound = false;
  function bindMindEdit() {
    if (_mindBound) return;
    _mindBound = true;
    window.addEventListener('message', function (e) {
      var d = e.data;
      if (d && d.type === 'mathbank-mind-edit' && typeof d.path === 'string') {
        editMindNode(d.path);
      }
      if (d && d.type === 'mathbank-cover-title' && typeof d.text === 'string') {
        saveCoverTitle(d.text);
      }
    });
  }

  function saveCoverTitle(text) {
    var clean = String(text || '').trim().slice(0, 200);
    if (ST.settings && (ST.settings.cover_title || '') === clean) return;
    var attempt = function (retries) {
      return apiJson('/api/lecture/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cover_title: clean }),
      }).catch(function (e) {
        // 偶发 5xx 自动重试一次，仍失败必须报错而不是静默吞
        if (retries > 0) return attempt(retries - 1);
        throw e;
      });
    };
    ST._coverSave = attempt(1)
      .then(function (s) {
        ST.settings = s;
        toast(clean ? '封面标题已保存' : '封面标题已清空（恢复按章节名）', 'success');
      })
      .catch(function (e) { toast('保存失败：' + e.message, 'error'); });
  }

  function buildMap(section, headTitle) {
    var head = headTitle || (cleanChapterTitle(section.chapter) + ' · ' + section.knowledge);
    var rows = (section.topics || []).map(function (t, i) {
      return '<div class="map-row"><span class="map-idx">' + cnNumeral(t.no || i + 1) + '</span><span class="map-name">' + esc(t.name) + '</span></div>';
    }).join('');
    return '<div class="sheet" id="page-map"><div class="chapter-info">' + esc(head) + ' · 考向地图</div><div class="map">' + rows + '</div></div>';
  }

  function buildSourceHtml(q) {
    var srcs = (q.sources || []).filter(function (s) { return s && (s.text || '').trim(); });
    if (!srcs.length) {
      var old = (q.source || '').trim();
      if (!old) return '';
      return '<div class="q-src q-src-main">（' + esc(old) + '）</div>' +
             '<div class="q-src q-src-all">（' + esc(old) + '）</div>';
    }
    var main = srcs[0].text.trim();
    var more = srcs.length - 1;
    var mainLine = '（' + esc(main) + (more > 0 ? '，亦见于 ' + more + ' 份试卷' : '') + '）';
    var allLine = '（' + srcs.map(function (s) { return esc(s.text.trim()); }).join('；') + '）';
    return '<div class="q-src q-src-main">' + mainLine + '</div>' +
           '<div class="q-src q-src-all">' + allLine + '</div>';
  }

  function buildQuestion(q, label) {
    var content = window.parseMarkdownWithMath ? window.parseMarkdownWithMath(q.content || '') : esc(q.content || '');
    var ans = '';
    if (q.answer) {
      var parsedAns = window.parseMarkdownWithMath ? window.parseMarkdownWithMath(q.answer) : esc(q.answer);
      ans = '<div class="answer-block"><span class="ab-label ans">【答案】</span>' + parsedAns + '</div>';
    }
    // 来源行：放题号正上方，左边缘与题干首字对齐（字号小于题干）
    // 一题多来源：学生版只显示主来源 + 亦见于N份试卷；教师版全部列出
    var src = buildSourceHtml(q);
    return '<div class="qa" data-qid="' + q.id + '">' +
      src +
      '<div class="q-head"><span class="tag">' + esc(label) + '</span></div>' +
      '<div class="q-body">' + content + '</div>' + ans + '</div>';
  }

  function buildTopicSheet(topicCfg, idx, notes, compulsory, chapter, knowledge, topicNo, outlineExample, headTitle) {
    var tid = topicCfg.topic_id || '';
    var pit = tid ? notes[noteKey('pit', compulsory, chapter, knowledge, tid)] : '';
    var method = tid ? notes[noteKey('method', compulsory, chapter, knowledge, tid)] : '';
    var pitEmpty = tid ? '<span class="km-empty">（待补充）</span>'
      : '<span class="km-empty">（该考向缺少考点标识，修订稿无法定位，请重新加载小节后再编辑）</span>';
    // 无修订稿时回落到大纲自带的例题说明，并显式标注未修订——
    // LectureNote 一直承诺「无修订则用大纲默认」，此前从没实现
    var methodDefault = (tid && outlineExample)
      ? '<span class="km-default">大纲默认 · 未修订</span>' + esc(outlineExample) : '';
    var html = '<div class="sheet" data-answer-page="true"><div class="page-head"><span class="ph-title">' + esc(headTitle || '') + '</span><span class="ph-quote"></span></div>';
    html += '<div class="subsection-title">考向' + cnNumeral(topicNo || (idx + 1)) + '　' + esc(topicCfg.name) + '</div>';
    html += '<div class="km-box">';
    html += '<div class="km-item km-pit"><span class="km-label">易错点</span>' + (pit || pitEmpty) + '</div>';
    html += '<div class="km-item km-method"><span class="km-label">解题方法</span>' + (method || methodDefault || pitEmpty) + '</div>';
    html += '</div>';
    // 编号口径与 lecture_export.py 镜像：例<中文考向序>-<考向内例题序>；
    // 变式/趁热打铁为纯裸号、每个考向归零。板块头带字，题目不带前缀。
    var topicCn = cnNumeral(topicNo || (idx + 1));
    var hasExample = false, ex = 0, variant = 0, practice = 0;
    (topicCfg.items || []).forEach(function (item) {
      var q = ST.qIndex[item.question_id];
      if (!q) return;
      if (item.role === 'example') {
        if (!hasExample) { html += blockHeadHtml('典型例题'); hasExample = true; }
        ex++;
        // 变式跨母题连续编号（1→N），只有例题本身归零重排
        html += buildQuestion(q, '例' + topicCn + '-' + ex);
      } else if (item.role === 'variant') {
        // 孤儿变式（该考向还没配例题）不冠「典型例题」头，避免空板块头
        if (ex > 0 && !hasExample) { html += blockHeadHtml('典型例题'); hasExample = true; }
        variant++;
        html += buildQuestion(q, '变式' + variant);
      } else if (item.role === 'practice') {
        practice++;
        if (practice === 1) html += blockHeadHtml('趁热打铁');
        html += buildQuestion(q, String(practice));
      } else {
        html += buildQuestion(q, '例' + topicCn + '-' + Math.max(ex, 1) + '·练');
      }
    });
    html += '<div class="page-foot"><span class="pg"></span></div></div>';
    return html;
  }

  function blockHeadHtml(text) {
    return '<div class="block-title">【' + text + '】</div>';
  }

  /* 目录页（封面后第二页）：逐讲列「第X讲 小节名 …… 第 N 页」。 */
  function buildToc(entries, startPage) {
    var rows = entries.map(function (e, i) {
      var sec = e.section || {};
      return '<div class="toc-row"><span class="toc-name">' + esc(lectureTitle(i + 1, sec.knowledge)) + '</span>' +
        '<span class="toc-dots"></span><span class="toc-page">第 ' + (startPage + i) + ' 页</span></div>';
    }).join('');
    return '<div class="sheet toc"><div class="section-title">目　录</div><div class="toc">' + rows + '</div></div>';
  }

  function countLeaves(html) {
    // 与 iframe 脚本给 .pg 写「第 N 页」同一口径：数所有带页脚的 sheet。
    // 思维导图分页的 page-foot 是裸文本（无 .pg），只按 <div class="sheet 数会漏页。
    var s = String(html), n = 0, i = 0;
    while (i < s.length) {
      var open = s.indexOf('<div class="sheet', i);
      if (open < 0) break;
      var next = s.indexOf('<div class="sheet', open + 1);
      if (next < 0) next = s.length;
      if (s.indexOf('page-foot', open) >= 0 && s.indexOf('page-foot', open) < next) n++;
      i = next;
    }
    return n;
  }

  /* preview 载荷归一为节条目数组（单节旧载荷 → 一条）。 */
  function normalizePreviewEntries(data) {
    if (Array.isArray(data.sections) && data.sections.length) {
      return data.sections.map(function (e) {
        return { section: e.section || {}, notes: e.notes || {}, topics: e.topics || [], extra_ids: e.extra_ids || [] };
      });
    }
    return [{ section: data.section || {}, notes: data.notes || {}, topics: data.topics || [], extra_ids: data.extra_ids || [] }];
  }

  /* 多节合并时每节各出一组：知识梳理页 → 考向地图页 → 该节考向页。
     metaMapFor 按节重建 topic_id→编号映射，跨节同名 topic_id 不会串号。 */
  function buildSectionPages(entry, settings, lectureNo) {
    var section = entry.section || {};
    var notes = entry.notes || {};
    var headTitle = lectureTitle(lectureNo, section.knowledge);
    var metaMap = buildTopicMetaMap(section);
    var html = buildKnowledge(notes, section.compulsory, section.chapter, section.knowledge, headTitle);
    html += buildMap(section, headTitle);
    (entry.topics || []).forEach(function (t, i) {
      html += buildTopicSheet(t, i, notes, section.compulsory, section.chapter, section.knowledge,
        topicNoOf(metaMap, t, i), outlineExampleOf(metaMap, t), headTitle);
    });
    var items = entry.extra_ids || [];
    if (items.length) {
      html += '<div class="sheet" data-answer-page="true"><div class="page-head"><span class="ph-title">' + esc(headTitle) + '</span><span class="ph-quote"></span></div>';
      html += blockHeadHtml('实战演练');
      var hw = 0;
      items.forEach(function (qid) {
        var q = ST.qIndex[qid];
        if (!q) return;
        hw++;
        html += buildQuestion(q, String(hw));
      });
      html += '<div class="page-foot"><span class="pg"></span></div></div>';
    }
    return html;
  }


  function buildAnswerBook(entries) {
    var html = '<div class="sheet" id="page-answer-book"><div class="section-title">答案册</div>';
    entries.forEach(function (entry, ei) {
      var section = entry.section || {};
      var lecturePrefix = '第' + cnNumeral(ei + 1) + '讲';
      var metaMap = buildTopicMetaMap(section);
      (entry.topics || []).forEach(function (topicCfg, idx) {
        var topicNo = topicNoOf(metaMap, topicCfg, idx);
        var topicCn = cnNumeral(topicNo);
        var hasExample = false, ex = 0, variant = 0, practice = 0;
        (topicCfg.items || []).forEach(function (item) {
          var q = ST.qIndex[item.question_id];
          if (!q) return;
          var label;
          if (item.role === 'example') {
            if (!hasExample) { html += blockHeadHtml('典型例题'); hasExample = true; }
            ex++;
            label = '例' + topicCn + '-' + ex;
          } else if (item.role === 'variant') {
            // 与正文同口径：孤儿变式（无母题）不冠「典型例题」头
            if (ex > 0 && !hasExample) { html += blockHeadHtml('典型例题'); hasExample = true; }
            variant++;
            label = '变式' + variant;
          } else if (item.role === 'practice') {
            practice++;
            if (practice === 1) html += blockHeadHtml('趁热打铁');
            label = String(practice);
          } else {
            label = '例' + topicCn + '-' + Math.max(ex, 1) + '·练';
          }
          var ans = q.answer ? (window.parseMarkdownWithMath ? window.parseMarkdownWithMath(q.answer) : esc(q.answer)) : '';
          html += '<div class="ans-item"><span class="ans-tag">' + esc(lecturePrefix + '　考向' + cnNumeral(topicNo) + '　' + label) + '</span>' + (ans || '<span class="text-slate-400">（暂无答案）</span>') + '</div>';
        });
      });
      var hwItems = entry.extra_ids || [];
      var hwShown = false, hw = 0;
      hwItems.forEach(function (qid) {
        var q = ST.qIndex[qid];
        if (!q) return;
        if (!hwShown) { html += blockHeadHtml('实战演练'); hwShown = true; }
        hw++;
        var ans = q.answer ? (window.parseMarkdownWithMath ? window.parseMarkdownWithMath(q.answer) : esc(q.answer)) : '';
        html += '<div class="ans-item"><span class="ans-tag">' + esc(lecturePrefix + '　' + hw) + '</span>' + (ans || '<span class="text-slate-400">（暂无答案）</span>') + '</div>';
      });
    });
    html += '</div>';
    return html;
  }

  /* 考向编号与大纲默认内容都取自 section.topics，
     与「考向地图」和选题面板同一口径 */
  function buildTopicMetaMap(section) {
    var map = {};
    ((section && section.topics) || []).forEach(function (t, i) {
      if (t && t.id) map[t.id] = { no: t.no || (i + 1), example: String(t.example || '') };
    });
    return map;
  }

  function topicNoOf(topicMetaById, topicCfg, idx) {
    var meta = topicMetaById[topicCfg.topic_id];
    return (meta && meta.no) || (idx + 1);
  }

  function outlineExampleOf(topicMetaById, topicCfg) {
    var meta = topicMetaById[topicCfg.topic_id];
    return (meta && meta.example) || '';
  }

  function buildLectureHtml(data) {
    ST.previewData = data;
    var settings = data.settings || {};
    // 题目查找表按整本建一次：多节合并时同一道题可能出现在任意一节
    ST.qIndex = {};
    (data.questions || []).forEach(function (q) { ST.qIndex[q.id] = q; });
    var entries = normalizePreviewEntries(data);
    entries.forEach(function (e) {
      (e.questions || []).forEach(function (q) { ST.qIndex[q.id] = q; });
    });
    // 把后端回带的修订稿与考向树落到对应节的编排容器上（思维导图编辑入口用）
    entries.forEach(function (e) {
      var sec = e.section;
      var entry = findSectionEntry(secKey(sec.compulsory, sec.chapter, sec.knowledge));
      if (!entry) return;
      entry.notes = e.notes;
      entry.section = entry.section || sec;
      if (sec.topics) entry.section = sec;
    });
    var firstSection = entries[0].section;
    ST.topicMetaById = buildTopicMetaMap(firstSection);
    var title = settings.cover_title || cleanChapterTitle(firstSection.chapter) || firstSection.knowledge || '讲义';
    // 版面顺序：封面 → 目录 → 逐讲（知识梳理/考向地图/考向页/实战演练）→ 答案册。
    // 目录起始页码按「带页脚的 sheet」数出来，与 iframe 脚本给 .pg 写的「第 N 页」
    // 同一口径；思维导图长页在页面生成后追加，只会让后面的页码整体后移，方向安全。
    var body = '';
    body += buildCover(settings, title, '学生版');
    var sectionHtml = entries.map(function (e, i) { return buildSectionPages(e, settings, i + 1); }).join('');
    body += buildToc(entries, 2 + countLeaves(sectionHtml));
    body += sectionHtml;
    body += buildAnswerBook(entries);

    var quotePool = data.quote_pool || [];
    var quotesJs = quotePool.map(function (q) { return JSON.stringify(q); }).join(',');

    var css = LECTURE_CSS();
    var html = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">' +
      '<link rel="stylesheet" href="/static/lib/katex/katex.min.css">' +
      '<style>' + css + '</style></head><body class="view-yuan">' +
      body +
      '<script src="/static/lib/katex/katex.min.js"><\/script>' +
      '<script src="/static/lib/katex/contrib/auto-render.min.js"><\/script>' +
      '<script>var MATH_QUOTES=[' + quotesJs + '];' +
      'document.addEventListener("DOMContentLoaded",function(){' +
      'if(window.renderMathInElement){renderMathInElement(document.body,{delimiters:[{left:"$$",right:"$$",display:true},{left:"\\\\[",right:"\\\\]",display:true},{left:"\\\\(",right:"\\\\)",display:false}],throwOnError:false});}' +
      'var pages=document.querySelectorAll(".sheet[data-answer-page=\\"true\\"] .ph-quote");var used={};for(var i=0;i<pages.length;i++){var q;do{q=MATH_QUOTES[Math.floor(Math.random()*MATH_QUOTES.length)];}while(used[q]);used[q]=true;pages[i].textContent=q;}' +
      'var pgs=document.querySelectorAll(".pg");for(var j=0;j<pgs.length;j++){pgs[j].textContent="第 "+(j+1)+" 页";}' +
      'document.addEventListener("click",function(e){var el=e.target;while(el&&el!==document.body&&!el.getAttribute("data-mp-path")){el=el.parentNode;}if(el&&el.getAttribute("data-mp-path")){window.parent.postMessage({type:"mathbank-mind-edit",path:el.getAttribute("data-mp-path")},"*");}});' +
      'var ct=document.querySelector("[data-cover-edit=\\"title\\"]");' +
      'if(ct){' +
      'ct.addEventListener("blur",function(){window.parent.postMessage({type:"mathbank-cover-title",text:ct.textContent},"*");});' +
      'ct.addEventListener("keydown",function(e){if(e.key==="Enter"){e.preventDefault();ct.blur();}});' +
      '}' +
      '});' +
      'function switchView(v){var b=document.body;b.classList.remove("view-yuan","view-jiexi","view-daan");b.classList.add("view-"+v);var cv=document.getElementById("cover-ver");if(cv)cv.textContent=(v==="yuan")?"学生版":(v==="jiexi"?"教师版":"答案册");var ab=document.getElementById("page-answer-book");if(ab)ab.style.display=(v==="daan")?"block":"none";}' +
      'window.addEventListener("message",function(e){var d=e.data;if(d&&d.type==="mathbank-set-view"&&d.view){switchView(d.view);}});' +
      '<\/script></body></html>';
    return html;
  }

  function LECTURE_CSS() {
    return [
      '@page{size:A4;margin:0}',
      '*{box-sizing:border-box}',
      'body{margin:0;background:#e5e7eb;font-family:"CangErJinKai","仓耳今楷","KaiTi","SimSun",serif;color:#1e293b}',
      '.sheet{width:210mm;min-height:297mm;margin:14px auto;padding:18mm 18mm 16mm;background:#fff;box-shadow:0 4px 18px rgba(0,0,0,.15);position:relative;page-break-after:always;overflow:hidden}',
      '@media print{.sheet{margin:0;box-shadow:none;page-break-after:always}.main-title{border-bottom:none !important;padding:0 !important}}',
      /* 封面 */
      '.cover{display:flex;flex-direction:column;align-items:center;justify-content:space-between;padding:22mm 24mm}',
      '.cover svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}',
      '.cover-brand{position:absolute;top:23mm;left:28mm;font-size:20px;font-weight:700;color:#1f3a5f;z-index:2}',
      '.cover-subtitle{position:absolute;top:23mm;right:28mm;text-align:right;font-size:20px;color:#334155;z-index:2}',
      '.main-title{position:absolute;top:99mm;left:0;right:0;text-align:center;font-size:56px;font-weight:700;color:#0f2440;letter-spacing:6px;z-index:2;cursor:text;border-bottom:2px dashed rgba(100,116,139,.25);padding:0 6mm;border-radius:2mm}',
      '.main-title:hover{border-bottom-color:rgba(37,99,235,.5)}',
      '.main-title:focus{outline:none;border-bottom-color:#2563eb;background:rgba(37,99,235,.05)}',
      '.cover-author{position:absolute;bottom:99mm;left:0;right:0;text-align:center;font-size:20px;font-weight:700;color:#000;z-index:2}',
      '.cover-ver{position:absolute;bottom:112mm;left:0;right:0;text-align:center;font-size:16px;color:#000;z-index:2}',
      'body.view-yuan .cover-ver{display:none}',
      /* 知识梳理 */
      '.chapter-info{font-size:24px;font-weight:700;color:#1f3a5f;text-align:center;margin-bottom:6mm}',
      '.section-title{font-size:22px;font-weight:700;color:#1e3a8a;text-align:center;margin:4mm 0 6mm;position:relative}',
      '.knowledge{font-size:14px;line-height:1.9}',
      '.knowledge .key{font-weight:700;color:#1e3a8a}',
      '.knowledge p{margin:2mm 0}',
      '.knowledge table.def{width:100%;border-collapse:collapse;margin:2mm 0;font-size:13px}',
      '.knowledge table.def th,.knowledge table.def td{border:1px solid #cbd5e1;padding:1.6mm 2mm;text-align:left}',
      '.knowledge table.def th{background:#f1f5f9;font-weight:700}',
      '.kn-empty{color:#94a3b8;font-size:14px}',
      /* 考向地图 */
      '.map{display:flex;flex-direction:column;gap:4mm;padding:6mm 0}',
      '.map-row{display:flex;align-items:center;gap:4mm;padding:3mm 4mm;border:1px solid #dbe3ef;border-radius:2mm;background:#f8fafc}',
      '.map-idx{width:8mm;height:8mm;border-radius:50%;background:#1e3a8a;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:14px;flex-shrink:0}',
      '.map-name{font-size:15px;font-weight:600;color:#1e293b}',
      /* 题目页 */
      '.page-head{display:flex;justify-content:space-between;align-items:baseline;border-bottom:1px solid #cbd5e1;padding-bottom:2mm;margin-bottom:4mm}',
      '.ph-title{font-size:17px;font-weight:700;color:#1e3a8a}',
      '.ph-quote{font-size:13px;color:#64748b}',
      '.subsection-title{font-size:18px;font-weight:700;color:#1f3a5f;margin:3mm 0}',
      '.km-box{display:flex;flex-direction:column;gap:2mm;margin-bottom:4mm}',
      '.km-item{padding:2.5mm 3mm;border-radius:1.5mm;font-size:13.5px;line-height:1.7}',
      '.km-pit{background:#fffbeb;border-left:3px solid #f59e0b}',
      '.km-method{background:#eff6ff;border-left:3px solid #3b82f6}',
      '.km-label{font-weight:700;margin-right:2mm}',
      '.km-pit .km-label{color:#b45309}.km-method .km-label{color:#1d4ed8}',
      '.km-empty{color:#94a3b8}',
      '.km-default{display:inline-block;font-size:11.5px;color:#475569;background:#e2e8f0;border-radius:1mm;padding:0 1.5mm;margin-right:2mm;vertical-align:1px}',
      '.qa{margin-bottom:6mm}',
      '.q-src{font-size:12px;color:#64748b;margin-bottom:1mm;line-height:1.5}',
      'body.view-yuan .q-src-all{display:none}',
      'body.view-jiexi .q-src-main{display:none}',
      '.q-head{font-weight:700;margin-bottom:1.5mm}',
      '.tag{display:inline-block;background:#1e3a8a;color:#fff;padding:0.6mm 2.5mm;border-radius:1mm;font-size:13px;margin-right:2mm}',
      '.q-body{font-size:14px;line-height:1.9}',
      '.q-body .choices-grid{display:grid;grid-template-columns:1fr 1fr;gap:1mm 6mm;margin-top:1.5mm}',
      '.q-body table{border-collapse:collapse;margin:2mm 0}',
      '.q-body table td,.q-body table th{border:1px solid #cbd5e1;padding:1.5mm 2mm}',
      '.answer-block{margin-top:2mm;padding:2mm 3mm;background:#f8fafc;border-radius:1.5mm;font-size:13.5px;line-height:1.8}',
      '.ab-label{font-weight:700;margin-right:1mm}',
      '.ab-label.ans{color:#dc2626}',
      '.page-foot{position:absolute;bottom:5mm;left:0;right:0;text-align:center;font-size:12px;color:#64748b}',
      /* 答案册 */
      '#page-answer-book{display:none}',
      '.ans-item{margin-bottom:3mm;font-size:13.5px;line-height:1.8}',
      '.ans-tag{display:inline-block;background:#f1f5f9;color:#1e3a8a;padding:0.6mm 2.5mm;border-radius:1mm;font-weight:700;margin-right:2mm;font-size:12.5px}',
      /* 三视图 */
      '.view-jiexi .answer-block{display:block}',
      '.view-yuan .answer-block{display:none}',
      'body.view-daan .sheet:not(.cover):not(#page-answer-book){display:none!important}',
      'body.view-daan #page-answer-book{display:block}',
      /* 思维导图知识梳理 */
      '.mind-hint{font-size:12px;color:#94a3b8;text-align:center;margin:-2mm 0 4mm}',
      '.mind-wrap{display:flex;flex-direction:column;gap:5mm}',
      '.mind-title{display:inline-block;background:#1e3a8a;color:#fff;font-size:18px;font-weight:700;padding:2.5mm 8mm;border-radius:2mm;margin:0 auto 2mm;cursor:pointer;box-shadow:0 2px 6px rgba(30,58,138,.25)}',
      '.mind-title:hover{outline:2px solid #60a5fa}',
      '.mind-branch{display:flex;align-items:center;gap:0}',
      '.mind-branch-label{min-width:30mm;max-width:34mm;flex:none;color:#fff;font-weight:700;font-size:15px;padding:2.5mm 3mm;border-radius:1.5mm;text-align:center;cursor:pointer;box-shadow:0 2px 5px rgba(0,0,0,.12)}',
      '.mind-branch-label:hover{outline:2px solid rgba(0,0,0,.25)}',
      '.mind-branch-line{width:5mm;flex:none;border-top:2px solid var(--mc)}',
      '.mind-items{flex:1;display:flex;flex-direction:column;gap:2mm;min-width:0;border-left:2px solid var(--mc);padding-left:4mm}',
      '.mind-items > *{position:relative}',
      '.mind-items > *::before{content:"";position:absolute;left:-4mm;top:50%;width:4mm;border-top:1.5px solid var(--mc)}',
      '.mind-item{background:var(--mcb);border-left:3px solid var(--mc);padding:2mm 3mm;border-radius:0 1.5mm 1.5mm 0;font-size:13.5px;line-height:1.7;cursor:pointer}',
      '.mind-item:hover{outline:1.5px solid var(--mc)}',
      '.mind-item-children{margin-left:6mm;padding-left:4mm;border-left:1.5px dashed var(--mc);display:flex;flex-direction:column;gap:1.8mm}',
      '.mind-table-wrap{margin:1mm 0}',
      '.mind-table{width:100%;border-collapse:collapse;font-size:12.5px;line-height:1.5}',
      '.mind-table td{border:1px solid #cbd5e1;padding:1.2mm 2mm;cursor:pointer;background:#fff}',
      '.mind-table td:hover{outline:1.5px solid var(--mc);background:#f8fafc}',
      '.mind-table tr:first-child td{background:#f1f5f9;font-weight:700}',
    ].join('\n');
  }

  /* ---------------- 预览刷新 ---------------- */
  function collectConfig() {
    // 坐标一律取激活节：小节已改成多选下拉，DOM 里不再有单值可回退
    var comp = ST.compulsory || ($('lectureCompulsory') ? $('lectureCompulsory').value : '');
    var chap = ST.chapter || ($('lectureChapter') ? $('lectureChapter').value : '');
    return {
      grade: $('lectureGrade') ? $('lectureGrade').value : 'g1',
      compulsory: comp, chapter: chap, knowledge: ST.knowledge,
      topics: ST.topics, extra_ids: ST.extraIds,
    };
  }

  function ensureActiveEntry() {
    if (!ST.compulsory || !ST.chapter || !ST.knowledge) return null;
    return findSectionEntry(secKey(ST.compulsory, ST.chapter, ST.knowledge));
  }

  /* preview/export 共用载荷：多节走 sections，未合并时退回单节三坐标。 */
  function buildPreviewPayload(extra) {
    var payload = extra || {};
    if (ST.sections.length > 1) {
      payload.sections = collectSectionsPayload();
      return payload;
    }
    var cfg = collectConfig();
    payload.compulsory = cfg.compulsory;
    payload.chapter = cfg.chapter;
    payload.knowledge = cfg.knowledge;
    payload.topics = ST.topics;
    payload.extra_ids = ST.extraIds;
    return payload;
  }

  function refreshPreview() {
    var iframe = $('lecturePreview');
    if (!iframe) return;
    if (!ST.sections.length) { toast('请先选择册次 / 章节 / 小节'); return; }
    // 封面标题的 PUT 尚未落库时先等它完成，否则重建的预览会复活旧标题
    (ST._coverSave || Promise.resolve()).then(function () {
    apiJson('/api/lecture/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPreviewPayload()),
    })
      .then(function (data) {
        ST.settings = data.settings;
        ST.coverBg = data.cover_bg || ST.coverBg;
        var missing = data.missing_ids || [];
        if (missing.length) {
          toast('有 ' + missing.length + ' 道编排题目已删除（#' + missing.join('、#') + '），请检查选题', 'error');
        }
        if (data.section_found === false) {
          toast('当前教材树中未找到该小节，考向地图与易错点/解题方法会不完整，请检查活跃大纲', 'error');
        }
        var invalid = data.invalid_ids || [];
        if (invalid.length) {
          toast('有 ' + invalid.length + ' 处选题引用不是有效题目 ID，已跳过', 'error');
        }
        var html = buildLectureHtml(data);
        var frame = $('lecturePreview');
        frame.srcdoc = html;
        frame.onload = function () {
          if (ST.view && ST.view !== 'yuan') {
            frame.contentWindow.postMessage({ type: 'mathbank-set-view', view: ST.view }, '*');
          }
        };
      })
      .catch(function (e) { toast('预览失败：' + e.message); });
    });
  }

  /* ---------------- 视图切换与打印 ---------------- */
  function switchView(view) {
    if (['yuan', 'jiexi', 'daan'].indexOf(view) < 0) return;
    ST.view = view;
    var iframe = $('lecturePreview');
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage({ type: 'mathbank-set-view', view: view }, '*');
  }

  function printLecture() {
    var iframe = $('lecturePreview');
    if (!iframe || !iframe.contentWindow) { toast('请先加载讲义小节'); return; }
    iframe.contentWindow.focus();
    iframe.contentWindow.print();
  }

  var VIEW_LABELS = { yuan: '学生版', jiexi: '教师版', daan: '答案册' };

  /* 高清 PDF 走组卷引擎编译（十秒级），期间禁用按钮并置 aria-busy，
     否则用户会连点触发多次 XeLaTeX 编译。降级说明经响应头回传，必须说出来。 */
  function exportPdf() {
    if (!ST.sections.length) { toast('请先加载讲义小节'); return; }
    if (ST.exporting) { toast('已有讲义 PDF 在编译，请稍候'); return; }
    var view = ST.view || 'yuan';
    var button = $('lectureExportPdf');
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    ST.exporting = true;
    var tab = window.open('', '_blank');
    apiBlob('/api/lecture/export/pdf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPreviewPayload({ view: view })),
    }).then(function (result) {
      var url = URL.createObjectURL(result.blob);
      if (tab && !tab.closed) tab.location.href = url; else window.location.href = url;
      if (result.notes) toast('排版降级：' + result.notes, 'error');
      else toast((VIEW_LABELS[view] || view) + ' PDF 已生成', 'success');
    }).catch(function (e) {
      if (tab && !tab.closed) tab.close();
      toast('导出失败：' + e.message, 'error');
    }).then(function () {
      ST.exporting = false;
      if (button) { button.disabled = false; button.removeAttribute('aria-busy'); }
    });
  }

  /* ---------------- 草稿树（两级：系列 → 抽屉）+ 届次筛选 ---------------- */
  var SERIES_LABELS = { sync: '同步讲义', review: '复习讲义' };
  var REVIEW_DRAWERS = ['一轮复习', '二轮复习', '三轮复习'];

  function draftMeta(d) {
    var cfg = d.config || {};
    var series = cfg.series === 'review' ? 'review' : 'sync';
    var drawer = cfg.drawer || (series === 'sync' ? (d.compulsory || '未归类') : '一轮复习');
    var year = cfg.year || String(d.created_at || '').slice(0, 4) || '';
    return { series: series, drawer: drawer, year: year, archived: d.status === 'archived' };
  }

  function currentYear() { return new Date().getFullYear(); }

  function collectTreeControls() {
    return {
      series: $('lectureSeries') ? $('lectureSeries').value : 'sync',
      drawer: $('lectureDrawer') ? $('lectureDrawer').value : '',
      year: $('lectureYearSelect') ? $('lectureYearSelect').value : String(currentYear()),
    };
  }

  function renderDrawerOptions() {
    var sel = $('lectureDrawer');
    if (!sel) return;
    var series = collectTreeControls().series;
    var prev = sel.value;
    sel.innerHTML = '';
    var opts = series === 'review' ? REVIEW_DRAWERS.slice() : Object.keys(metaCur());
    if (series === 'sync') {
      var comp = $('lectureCompulsory') ? $('lectureCompulsory').value : '';
      if (comp && opts.indexOf(comp) < 0) opts.unshift(comp);
    }
    opts.forEach(function (o) {
      var el = document.createElement('option');
      el.value = o; el.textContent = o;
      sel.appendChild(el);
    });
    if (opts.indexOf(prev) >= 0) sel.value = prev;
    else if (series === 'sync') {
      var comp2 = $('lectureCompulsory') ? $('lectureCompulsory').value : '';
      if (comp2 && opts.indexOf(comp2) >= 0) sel.value = comp2;
    }
  }

  function renderYearOptions() {
    var sel = $('lectureYearSelect');
    if (!sel) return;
    var prev = sel.value;
    sel.innerHTML = '';
    for (var y = currentYear() + 3; y >= 2024; y--) {
      var el = document.createElement('option');
      el.value = String(y); el.textContent = y + '届';
      sel.appendChild(el);
    }
    if (prev) sel.value = prev;
  }

  function collectYears(drafts) {
    var set = {};
    drafts.forEach(function (d) { var y = draftMeta(d).year; if (y) set[y] = 1; });
    return Object.keys(set).sort().reverse();
  }

  /* 届次条走事件委托：年份来自草稿 config.year，拼进内联 onclick 或属性里都是注入点。 */
  function renderYearBar(drafts) {
    var bar = $('lectureYearBar');
    if (!bar) return;
    bindYearBar();
    var chipClass = function (active) {
      return 'rounded-full px-2 py-0.5 border ' + (active
        ? 'border-blue-500 bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-300 font-bold'
        : 'border-slate-300 dark:border-slate-700 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800');
    };
    var html = '<button type="button" data-year="" class="' + chipClass(!ST.yearFilter) + '">全部</button>';
    collectYears(drafts).forEach(function (y) {
      html += '<button type="button" data-year="' + esc(y) + '" class="' + chipClass(ST.yearFilter === y) + '">' + esc(y) + '</button>';
    });
    bar.innerHTML = html;
  }

  var _yearBarBound = false;
  function bindYearBar() {
    if (_yearBarBound) return;
    _yearBarBound = true;
    var bar = $('lectureYearBar');
    if (!bar) return;
    bar.addEventListener('click', function (e) {
      var el = e.target;
      while (el && el !== bar && !(el.hasAttribute && el.hasAttribute('data-year'))) el = el.parentNode;
      if (!el || el === bar) return;
      setYearFilter(el.getAttribute('data-year'));
    });
  }

  function draftCardHtml(d, meta) {
    var showYear = meta.series === 'review' || ST.yearFilter;
    var badge = showYear && meta.year ? '<span class="text-[10px] px-1 py-px rounded bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 font-bold">' + esc(meta.year.slice(2)) + '</span>' : '';
    var oldMark = meta.archived ? '<span class="text-[10px] px-1 py-px rounded bg-slate-200 dark:bg-slate-700 text-slate-500 dark:text-slate-300">旧版</span>' : '';
    return '<div class="rounded-lg border border-slate-200 dark:border-slate-700 p-2 cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800' + (ST.draftId === d.id ? ' ring-2 ring-blue-400' : '') + '" onclick="LectureManager.loadDraft(' + d.id + ')">' +
      '<div class="flex items-center gap-1"><span class="text-xs font-bold text-slate-700 dark:text-slate-200 truncate flex-1">' + esc(d.title) + '</span>' + oldMark + badge + '</div>' +
      '<div class="flex justify-end gap-2 mt-1">' +
      (meta.archived
        ? '<button onclick="event.stopPropagation();LectureManager.restoreDraft(' + d.id + ')" class="text-[11px] text-emerald-600 hover:text-emerald-700">恢复</button>'
        : '<button onclick="event.stopPropagation();LectureManager.cloneDraft(' + d.id + ')" class="text-[11px] text-blue-500 hover:text-blue-700">复制为新版</button>') +
      '<button onclick="event.stopPropagation();LectureManager.deleteDraft(' + d.id + ')" class="text-[11px] text-red-400 hover:text-red-600">删除</button>' +
      '</div></div>';
  }

  function renderDraftTree() {
    var box = $('lectureDraftTree');
    if (!box) return;
    apiJson('/api/lecture/drafts')
      .then(function (list) {
        renderYearBar(list);
        if (!list.length) { box.innerHTML = '<div class="text-xs text-slate-400 p-2">暂无讲义，点击「新建讲义」开始。</div>'; return; }
        var actives = [], archives = [];
        list.forEach(function (d) { (draftMeta(d).archived ? archives : actives).push(d); });
        // 选中具体届次时，该年份的旧版直接进树；否则收进「历史版本」折叠区
        var promoted = [];
        if (ST.yearFilter) {
          promoted = archives.filter(function (d) { return draftMeta(d).year === ST.yearFilter; });
          archives = archives.filter(function (d) { return draftMeta(d).year !== ST.yearFilter; });
          actives = actives.concat(promoted);
        }
        var visible = actives.filter(function (d) { return !ST.yearFilter || draftMeta(d).year === ST.yearFilter; });
        var html = '';
        ['sync', 'review'].forEach(function (series) {
          var group = visible.filter(function (d) { return draftMeta(d).series === series; });
          var drawerOrder = series === 'review' ? REVIEW_DRAWERS : Object.keys(metaCur());
          var drawers = [];
          group.forEach(function (d) {
            var dr = draftMeta(d).drawer;
            if (drawers.indexOf(dr) < 0) drawers.push(dr);
          });
          drawers.sort(function (a, b) {
            var ia = drawerOrder.indexOf(a), ib = drawerOrder.indexOf(b);
            return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || String(a).localeCompare(String(b), 'zh');
          });
          if (!drawers.length && !archives.length) return;
          html += '<div class="pt-1"><div class="text-xs font-bold text-slate-600 dark:text-slate-300 mb-1"><i class="fa-solid fa-folder mr-1 text-slate-400"></i>' + SERIES_LABELS[series] + '</div>';
          drawers.forEach(function (dr) {
            var items = group.filter(function (d) { return draftMeta(d).drawer === dr; });
            html += '<details open class="ml-3 mb-1"><summary class="text-xs font-semibold text-slate-500 dark:text-slate-400 cursor-pointer py-0.5 select-none">' + esc(dr) + ' <span class="font-normal text-slate-400">(' + items.length + ')</span></summary><div class="ml-2 mt-1 space-y-1.5">';
            items.forEach(function (d) { html += draftCardHtml(d, draftMeta(d)); });
            html += '</div></details>';
          });
          html += '</div>';
        });
        if (!visible.length) html += '<div class="text-xs text-slate-400 p-2">' + (ST.yearFilter ? '该届次暂无讲义。' : '暂无讲义，点击「新建讲义」开始。') + '</div>';
        if (archives.length) {
          html += '<details class="pt-2"><summary class="text-xs font-semibold text-slate-400 cursor-pointer select-none px-1"><i class="fa-solid fa-clock-rotate-left mr-1"></i>历史版本 (' + archives.length + ')</summary><div class="ml-2 mt-1.5 space-y-1.5">';
          archives.forEach(function (d) { html += draftCardHtml(d, draftMeta(d)); });
          html += '</div></details>';
        }
        box.innerHTML = html;
      })
      .catch(function (e) { box.innerHTML = '<div class="text-xs text-slate-400 p-2">草稿列表加载失败：' + esc(e.message) + '</div>'; });
  }

  function setYearFilter(y) { ST.yearFilter = y || ''; renderDraftTree(); }

  /* 草稿标题：首次创建时生成，之后只有小节或届次真的变了才重写。
     旧写法每次保存都按 chapter+knowledge 重拼，届次后缀会被冲掉，
     【复制为新版】出来的新草稿也和旧版同名。 */
  /* 草稿标题：首次创建时生成，之后只有小节或届次真的变了才重写。
     旧写法每次保存都按 chapter+knowledge 重拼，届次后缀会被冲掉，
     【复制为新版】出来的新草稿也和旧版同名。 */
  function draftTitle(drawer, knowledge, year) {
    return (drawer || '未归类') + ' ' + (knowledge || '') + ' 讲义（' + (year || currentYear()) + '届）';
  }

  function draftStamp(drawer, knowledge, year) {
    return [drawer || '', knowledge || '', year || ''].join('|');
  }

  /* 多节合并时主行坐标只反映激活节（兼容旧查询），完整清单在 config.sections；
     标题用「首节 等N节」，避免整本讲义只显示最后加载的小节名。 */
  function draftKnowledgeLabel() {
    if (ST.sections.length <= 1) return ST.knowledge;
    return ST.sections[0].knowledge + ' 等' + ST.sections.length + '节';
  }

  function cloneDraft(id) {
    var targetYear = collectTreeControls().year || String(currentYear());
    if (!confirm('复制为 ' + targetYear + ' 届新版？当前版本将收进「历史版本」。')) return;
    apiJson('/api/lecture/drafts/' + id)
      .then(function (d) {
        var meta = draftMeta(d);
        var cfg = d.config || {};
        var newCfg = {
          topics: cfg.topics || [], extra_ids: cfg.extra_ids || [],
          series: meta.series, drawer: meta.drawer, year: targetYear, cloned_from: id,
        };
        if (Array.isArray(cfg.sections)) newCfg.sections = JSON.parse(JSON.stringify(cfg.sections));
        var compulsory = meta.series === 'sync' ? meta.drawer : d.compulsory;
        return apiJson('/api/lecture/drafts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: draftTitle(meta.drawer, d.knowledge, targetYear), grade: d.grade,
            compulsory: compulsory,
            chapter: d.chapter, knowledge: d.knowledge, config: newCfg,
          }),
        });
      })
      .then(function (created) {
        return apiJson('/api/lecture/drafts/' + id, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: "archived" }),
        }).then(function () {
          toast('已复制为 ' + targetYear + ' 届新版，旧版已收起', 'success');
        }).catch(function (e) {
          // 新行已经落库、只有归档这步失败：必须说清是哪一步，
          // 否则用户以为没成功会再复制一次，树上多出重复讲义
          toast('新版已创建（#' + created.id + '），但旧版未能收起：' + e.message, 'error');
        }).then(function () { renderDraftTree(); });
      })
      .catch(function (e) { toast('复制失败：' + e.message); });
  }

  function restoreDraft(id) {
    apiJson('/api/lecture/drafts/' + id, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: "draft" }),
    })
      .then(function () { toast('已恢复为当前版本', 'success'); renderDraftTree(); })
      .catch(function (e) { toast('恢复失败：' + e.message); });
  }

  function newDraft() {
    ST.draftId = null; ST.draftStamp = '';
    ST.sections = []; ST.activeKey = ''; ST.section = null;
    ST.topics = []; ST.extraIds = []; ST.questions = [];
    ST.knowledgeMind = null;
    ST.dirty = false;
    clearAutosave();
    renderSectionChips();
    renderAutosaveBar();
    var panel = $('lectureTopicPanel');
    if (panel) panel.innerHTML = '<div class="text-sm text-slate-400 p-4">选择册次 / 章节 / 小节并加载。勾选多个小节即可合并为一本讲义。</div>';
    var iframe = $('lecturePreview');
    if (iframe) iframe.srcdoc = '';
    if ($('lectureSeries')) $('lectureSeries').value = 'sync';
    renderDrawerOptions();
    renderYearOptions();
    renderDraftTree();
  }

  function selectHasValue(sel, value) {
    if (!sel || !value) return false;
    for (var i = 0; i < sel.options.length; i++) { if (sel.options[i].value === value) return true; }
    return false;
  }

  /* 把册次/章节/小节落到激活节（级联只会把不在候选里的值改写成首项）。
     返回无法在当前教材树里定位的那一段，供调用方如实提示。 */
  function applySelection(compulsory, chapter, knowledge) {
    var entry = findSectionEntry(secKey(compulsory, chapter, knowledge)) || ensureActiveEntry();
    if (entry) {
      ST.compulsory = entry.compulsory; ST.chapter = entry.chapter; ST.knowledge = entry.knowledge;
    } else {
      ST.compulsory = compulsory; ST.chapter = chapter; ST.knowledge = knowledge;
    }
    renderCompulsory();
    return !selectHasValue($('lectureCompulsory'), ST.compulsory) ? ST.compulsory
      : !selectHasValue($('lectureChapter'), ST.chapter) ? ST.chapter
        : !knowledgeOptions().some(function (n) { return n === ST.knowledge; }) ? ST.knowledge : '';
  }

  function loadDraft(id) {
    apiJson('/api/lecture/drafts/' + id)
      .then(function (d) {
        ST.draftId = d.id;
        var cfg = d.config || {};
        var meta = draftMeta(d);
        ST.grade = d.grade || 'g1';
        // sections 是编排唯一来源；主行坐标只是激活节快照，不再单独读旧字段
        ST.sections = [];
        ST.activeKey = ''; ST.section = null;
        ST.topics = []; ST.extraIds = []; ST.questions = [];
        ST.knowledgeMind = null;
        var list = Array.isArray(cfg.sections) && cfg.sections.length ? cfg.sections : null;
        if (list) {
          list.forEach(function (s) {
            if (!s || !s.compulsory || !s.chapter || !s.knowledge) return;
            var entry = ensureSectionEntry(s.compulsory, s.chapter, s.knowledge);
            entry.topics = s.topics || [];
            entry.extraIds = s.extra_ids || [];
          });
        } else if (d.compulsory && d.chapter && d.knowledge) {
          var only = ensureSectionEntry(d.compulsory, d.chapter, d.knowledge);
          only.topics = cfg.topics || [];
          only.extraIds = cfg.extra_ids || [];
        }
        var first = ST.sections[0];
        if (first) activateSection(first);
        ST.draftStamp = draftStamp(meta.drawer, draftKnowledgeLabel(), meta.year);
        renderGrade();
        if ($('lectureSeries')) $('lectureSeries').value = meta.series;
        renderDrawerOptions();
        renderYearOptions();
        if ($('lectureDrawer') && meta.drawer) $('lectureDrawer').value = meta.drawer;
        if ($('lectureYearSelect') && meta.year) $('lectureYearSelect').value = meta.year;
        // 不用定时器延时等 DOM：旧写法在慢机器上会加载到错误的小节
        var stale = applySelection(ST.compulsory, ST.chapter, ST.knowledge);
        renderSectionChips();
        ST.dirty = false;
        renderDraftTree();
        renderAutosaveBar();
        if (stale) {
          toast('草稿的小节「' + stale + '」已不在当前教材树，请先切换教材版本或重选小节', 'error');
        }
        loadSection({ keepSelection: true });
      })
      .catch(function (e) { toast('载入草稿失败：' + e.message); });
  }

  function saveDraft() {
    if (!ST.sections.length) { toast('请先选择册次 / 章节 / 小节'); return; }
    var cfg = collectConfig();
    if (!cfg.compulsory || !cfg.chapter || !cfg.knowledge) { toast('请先选择册次 / 章节 / 小节'); return; }
    var tree = collectTreeControls();
    var drawer = tree.drawer || cfg.compulsory;
    var url = ST.draftId ? '/api/lecture/drafts/' + ST.draftId : '/api/lecture/drafts';
    var method = ST.draftId ? 'PUT' : 'POST';
    var body = {
      grade: cfg.grade, compulsory: cfg.compulsory,
      chapter: cfg.chapter, knowledge: cfg.knowledge,
      config: {
        topics: ST.topics, extra_ids: ST.extraIds,
        sections: collectSectionsPayload(),
        series: tree.series, drawer: drawer, year: tree.year,
      },
    };
    // 标题只在首次创建、或小节/届次确实变化时重写；其余情况不带 title
    // （后端 patch 语义未传字段不动），避免每次保存都把届次后缀冲掉
    var stamp = draftStamp(drawer, draftKnowledgeLabel(), tree.year);
    if (!ST.draftId || stamp !== ST.draftStamp) body.title = draftTitle(drawer, draftKnowledgeLabel(), tree.year);
    apiJson(url, {
      method: method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(function (d) {
        ST.draftId = d.id;
        ST.draftStamp = stamp;
        ST.dirty = false;
        clearAutosave();
        renderAutosaveBar();
        toast('讲义已保存', 'success');
        renderDraftTree();
      })
      .catch(function (e) { toast('保存失败：' + e.message); });
  }

  function deleteDraft(id) {
    if (!confirm('确定删除该讲义草稿？')) return;
    apiJson('/api/lecture/drafts/' + id, { method: 'DELETE' })
      .then(function () {
        // 只有真删成功才动界面：后端拒绝时过去也会走到这里把编辑区清空
        if (ST.draftId === id) newDraft();
        else renderDraftTree();
      })
      .catch(function (e) { toast('删除失败：' + e.message); });
  }

  /* ---------------- 编排本地快照与未保存提示 ---------------- */
  var AUTOSAVE_KEY = 'mathbank_lecture_autosave';

  function snapshotCount(snap) {
    var n = (snap.extra_ids || []).length;
    (snap.topics || []).forEach(function (t) { n += ((t && t.items) || []).length; });
    return n;
  }

  /* 快照计数兼容单节（topics/extra_ids）与多节（sections）两种形态 */
  function snapshotTotal(snap) {
    if (!snap) return 0;
    var n = snapshotCount(snap);
    (snap.sections || []).forEach(function (s) { n += snapshotCount(s || {}); });
    return n;
  }

  function readAutosave() {
    try {
      var raw = localStorage.getItem(AUTOSAVE_KEY);
      if (!raw) return null;
      var snap = JSON.parse(raw);
      return (snap && Array.isArray(snap.sections)) ? snap : null;
    } catch (e) { return null; }
  }

  function clearAutosave() {
    try { localStorage.removeItem(AUTOSAVE_KEY); } catch (e) { /* 隐私模式下读也失败，忽略 */ }
  }

  /* 每次编排变更都同步写快照：LocalStorage 写不进（配额/隐私模式）必须说一声，
     否则用户会以为刷新后还有保险。 */
  function snapshotOrchestration() {
    // 只看真正编排了几道题：小节一加载每节就有几个空槽，按长度判会天天写快照
    var sections = collectSectionsPayload();
    if (!snapshotTotal({ topics: [], extra_ids: [], sections: sections })) { clearAutosave(); return; }
    var cfg = collectConfig();
    var snap = {
      saved_at: Date.now(), draft_id: ST.draftId,
      compulsory: cfg.compulsory, chapter: cfg.chapter, knowledge: cfg.knowledge,
      sections: sections,
    };
    try {
      localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(snap));
      ST.autosaveBlocked = false;
    } catch (e) {
      if (!ST.autosaveBlocked) toast('本地自动快照不可用，编排只能靠「保存讲义」留存', 'error');
      ST.autosaveBlocked = true;
    }
  }

  function renderAutosaveBar() {
    var bar = $('lectureAutosaveBar');
    if (!bar) return;
    var snap = readAutosave();
    var count = snapshotTotal(snap);
    var text = $('lectureAutosaveText');
    var restore = $('lectureAutosaveRestore');
    if (!snap || !count) { bar.classList.add('hidden'); return; }
    if (text) {
      text.textContent = ST.dirty
        ? '当前编排有改动未写入草稿（共 ' + count + ' 题，本地已留快照）'
        : '上次有编排未保存：' + (snap.knowledge || '未知小节') + ' · ' + count + ' 题';
    }
    if (restore) restore.classList.toggle('hidden', !!ST.dirty);
    bar.classList.remove('hidden');
  }

  function restoreAutosave() {
    var snap = readAutosave();
    if (!snap || !snapshotTotal(snap)) return;
    ST.sections = [];
    ST.activeKey = ''; ST.section = null;
    (snap.sections || []).forEach(function (s) {
      if (!s || !s.compulsory || !s.chapter || !s.knowledge) return;
      var entry = ensureSectionEntry(s.compulsory, s.chapter, s.knowledge);
      entry.topics = s.topics || [];
      entry.extraIds = s.extra_ids || [];
    });
    var first = ST.sections[0];
    if (!first) return;
    activateSection(first);
    var stale = applySelection(first.compulsory, first.chapter, first.knowledge);
    if (stale) {
      toast('快照里的小节「' + stale + '」已不在当前教材树，无法恢复', 'error');
      return;
    }
    ST.draftId = snap.draft_id || null;
    ST.draftStamp = '';
    ST.dirty = true;
    renderSectionChips();
    toast('已恢复本地快照，请点「保存讲义」写入草稿', 'success');
    renderAutosaveBar();
    renderDraftTree();
    loadSection({ keepSelection: true });
  }

  function discardAutosave() {
    clearAutosave();
    ST.dirty = false;
    renderAutosaveBar();
    toast('已丢弃本地快照', 'success');
  }

  function bindAutosave() {
    if (ST.autosaveBound) return;
    ST.autosaveBound = true;
    var restore = $('lectureAutosaveRestore');
    var discard = $('lectureAutosaveDiscard');
    if (restore) restore.addEventListener('click', restoreAutosave);
    if (discard) discard.addEventListener('click', discardAutosave);
    window.addEventListener('beforeunload', function (e) {
      if (!ST.dirty) return;
      e.preventDefault();
      e.returnValue = '';
    });
  }

  /* ---------------- 编排操作 ---------------- */
  function commitOrchestration() {
    ST.dirty = true;
    snapshotOrchestration();
    renderTopicPanelFromState();
    renderAutosaveBar();
  }

  function addItem(topicIdx, qid) {
    if (!ST.topics[topicIdx]) return;
    if (ST.topics[topicIdx].items.some(function (it) { return it.question_id === qid; })) return;
    ST.topics[topicIdx].items.push({ question_id: qid, role: 'example' });
    commitOrchestration();
  }

  function setRole(topicIdx, itemIdx, role) {
    if (ST.topics[topicIdx] && ST.topics[topicIdx].items[itemIdx]) {
      ST.topics[topicIdx].items[itemIdx].role = role;
      commitOrchestration();
    }
  }

  function removeItem(topicIdx, itemIdx) {
    if (!ST.topics[topicIdx]) return;
    ST.topics[topicIdx].items.splice(itemIdx, 1);
    commitOrchestration();
  }

  function addExtra(qid) {
    if (ST.extraIds.indexOf(qid) >= 0) return;
    ST.extraIds.push(qid);
    commitOrchestration();
  }

  function removeExtra(i) {
    ST.extraIds.splice(i, 1);
    commitOrchestration();
  }

  function renderTopicPanelFromState() {
    // 用已加载的 section 重绘（section 缓存于 ST.section）
    if (ST.section) renderTopicPanel(ST.section, ST.questions);
  }

  function loadSection(opts) {
    var keep = opts && opts.keepSelection;
    var cfg = collectConfig();
    if (!cfg.compulsory || !cfg.chapter || !cfg.knowledge) { toast('请先选择册次 / 章节 / 小节'); return; }
    // 下拉框坐标与激活节不一致时（用户改了下拉没点加载），切到对应节；不在清单里则加入
    var key = secKey(cfg.compulsory, cfg.chapter, cfg.knowledge);
    var entry = findSectionEntry(key);
    if (!entry) {
      entry = ensureSectionEntry(cfg.compulsory, cfg.chapter, cfg.knowledge);
      activateSection(entry);
    } else if (key !== ST.activeKey) {
      activateSection(entry);
    }
    if (!keep) renderSectionChips();
    else { renderKnowledgeDropdown(); renderSectionChips(); }
    toast('加载小节…');
    apiJson('/api/lesson-plans/section-topics?compulsory=' + encodeURIComponent(cfg.compulsory) + '&chapter=' + encodeURIComponent(cfg.chapter) + '&knowledge=' + encodeURIComponent(cfg.knowledge))
      .then(function (built) {
        entry.section = { compulsory: cfg.compulsory, chapter: cfg.chapter, knowledge: cfg.knowledge, name: cfg.knowledge, topics: (built.blocks || []).map(function (b) { return { id: b.topic_id, name: b.title, no: b.no }; }) };
        ST.section = entry.section;
        return loadQuestions(entry);
      })
      .then(function (questions) {
        if (ST.activeKey === entry.key) renderTopicPanel(entry.section, questions);
        refreshPreview();
        toast('小节已加载', 'success');
      })
      .catch(function (e) { toast('加载失败：' + e.message); });
  }

  /* ---------------- 知识块编辑（坐标一律取所属节，不依赖下拉框当前值） ---------------- */
  function noteCoords(target) {
    var entry = target && target.key ? findSectionEntry(target.key) : ensureActiveEntry();
    if (!entry) return null;
    return { compulsory: entry.compulsory, chapter: entry.chapter, knowledge: entry.knowledge };
  }

  function setNoteDeleteVisible(on) {
    var btn = $('lectureNoteDelete');
    if (!btn) return;
    btn.classList.toggle('hidden', !on);
    btn.disabled = !on;
  }

  /* 弹窗回填失败不得静默：空 textarea + 保存 = 用空内容覆盖已存修订稿。
     fallbackText 是该考向在大纲里的默认内容（仅无修订稿时预载）。 */
  function loadNoteIntoModal(query, fallbackText) {
    setNoteDeleteVisible(false);
    apiJson('/api/lecture/notes?' + query)
      .then(function (rows) {
        var ta = $('lectureNoteContent');
        var found = !!(rows && rows.length);
        var content = (found && rows[0].content) || '';
        if (ta) ta.value = found ? content : (fallbackText || '');
        // 只有库里确实有内容时才有「恢复默认」可点
        setNoteDeleteVisible(!!String(content).trim());
      })
      .catch(function (e) {
        if (ST.noteTarget) ST.noteTarget.noteLoadFailed = true;
        toast('读取已存修订稿失败（' + e.message + '），请勿直接清空保存', 'error');
      });
  }

  /* 删除该条修订稿 = 回退到无修订稿状态（知识梳理清空、易错点/方法回到待补充） */
  function deleteNote() {
    var target = ST.noteTarget;
    if (!target) return;
    if (target.type === 'mind-node') { toast('思维导图整块删除请先用工具栏「知识梳理」打开源码'); return; }
    var coords = noteCoords(target);
    if (!coords) { toast('请先选择小节'); return; }
    if (!confirm('删除这条已保存的修订稿？删除后回到默认内容，不可撤销。')) return;
    var q = 'note_type=' + encodeURIComponent(target.type) +
      '&compulsory=' + encodeURIComponent(coords.compulsory) +
      '&chapter=' + encodeURIComponent(coords.chapter) +
      '&knowledge=' + encodeURIComponent(coords.knowledge) +
      (target.topic_id ? '&topic_id=' + encodeURIComponent(target.topic_id) : '');
    apiJson('/api/lecture/notes?' + q, { method: 'DELETE' })
      .then(function () {
        var ta = $('lectureNoteContent');
        if (ta) ta.value = '';
        closeNoteModal();
        toast('已删除该修订稿', 'success');
        refreshPreview();
      })
      .catch(function (e) { toast('删除失败：' + e.message, 'error'); });
  }

  function editNote(type, topicIdx) {
    var coords = noteCoords();
    if (!coords) { toast('请先选择小节'); return; }
    var entry = ensureActiveEntry();
    var topicCfg = ST.topics[topicIdx];
    if (!topicCfg) { toast('该考向尚未初始化'); return; }
    if (!topicCfg.topic_id) { toast('该考向缺少考点标识，请先重新加载小节后再编辑'); return; }
    ST.noteTarget = {
      type: type, topic_idx: topicIdx, topic_id: topicCfg.topic_id, name: topicCfg.name,
      key: entry.key,
    };
    // 无修订稿时「解题方法」以大纲例题为起点，与预览里渲染的默认内容同一份
    var fallback = (type === 'method') ? outlineExampleOf(ST.topicMetaById || {}, topicCfg) : '';
    var titleEl = $('lectureNoteModalTitle');
    if (titleEl) {
      titleEl.textContent = '编辑' + (type === 'pit' ? '易错点' : '解题方法') + '：考向'
        + topicNoOf(ST.topicMetaById || {}, topicCfg, topicIdx) + ' ' + topicCfg.name
        + (fallback ? '（起点：大纲默认）' : '');
    }
    var ta = $('lectureNoteContent');
    if (ta) ta.value = '';
    // 读取已保存
    loadNoteIntoModal('note_type=' + type + '&compulsory=' + encodeURIComponent(coords.compulsory) + '&chapter=' + encodeURIComponent(coords.chapter) + '&knowledge=' + encodeURIComponent(coords.knowledge) + '&topic_id=' + encodeURIComponent(topicCfg.topic_id), fallback);
    var modal = $('lectureNoteModal');
    if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
    if (ta) ta.focus();
  }

  function editKnowledge() {
    var coords = noteCoords();
    if (!coords) { toast('请先选择小节'); return; }
    var entry = ensureActiveEntry();
    ST.noteTarget = { type: 'knowledge', topic_id: '', name: '知识梳理', key: entry.key };
    var titleEl = $('lectureNoteModalTitle');
    if (titleEl) titleEl.textContent = '编辑知识梳理：' + coords.chapter + ' · ' + coords.knowledge;
    var ta = $('lectureNoteContent');
    if (ta) ta.value = '';
    loadNoteIntoModal('note_type=knowledge&compulsory=' + encodeURIComponent(coords.compulsory) + '&chapter=' + encodeURIComponent(coords.chapter) + '&knowledge=' + encodeURIComponent(coords.knowledge));
    var modal = $('lectureNoteModal');
    if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
    if (ta) ta.focus();
  }

  /* 修订稿写入：被 400 拒绝（如缺考点标识）时必须报错，
     而不是提示「下次生成自动复用」。 */
  function putNote(payload) {
    return apiJson('/api/lecture/notes', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  }

  function saveNote() {
    if (!ST.noteTarget) return;
    var coords = noteCoords(ST.noteTarget);
    if (!coords) { toast('请先选择小节'); return; }
    var content = $('lectureNoteContent') ? $('lectureNoteContent').value : '';
    if (ST.noteTarget.noteLoadFailed && !String(content).trim()) {
      toast('修订稿未读取成功，不能保存空内容覆盖已存版本', 'error');
      return;
    }
    if (ST.noteTarget.type === 'mind-node') {
      var entry = findSectionEntry(ST.noteTarget.key) || ensureActiveEntry();
      var mind = entry && entry.knowledgeMind;
      if (!mind || !mind.data) { closeNoteModal(); return; }
      var node = mindNodeAt(mind.data, ST.noteTarget.mindPath);
      if (!node) { closeNoteModal(); return; }
      if (node.kind === 'table') node.table[node.r][node.c] = content;
      else node.ref[node.prop] = content;
      var serialized = '@mindmap\n' + JSON.stringify(mind.data, null, 1);
      mind.raw = serialized;
      putNote({
        note_type: 'knowledge',
        compulsory: coords.compulsory, chapter: coords.chapter, knowledge: coords.knowledge,
        content: serialized
      })
        .then(function () {
          closeNoteModal();
          toast('知识块已保存，下次生成自动复用', 'success');
          refreshPreview();
        })
        .catch(function (e) { toast('保存失败：' + e.message, 'error'); });
      return;
    }
    var payload = {
      note_type: ST.noteTarget.type,
      compulsory: coords.compulsory, chapter: coords.chapter, knowledge: coords.knowledge,
      content: content,
    };
    if (ST.noteTarget.type !== 'knowledge') payload.topic_id = ST.noteTarget.topic_id || '';
    putNote(payload)
      .then(function () {
        closeNoteModal();
        toast('知识块已保存，下次生成自动复用', 'success');
        refreshPreview();
      })
      .catch(function (e) { toast('保存失败：' + e.message); });
  }

  function closeNoteModal() {
    var modal = $('lectureNoteModal');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
    ST.noteTarget = null;
  }

  /* ---------------- 孤儿修订稿清单 ---------------- */
  /* 考向树重建会换掉 topic_id、小节改名会换掉坐标：note_key 从此取不回来，
     这些修订稿既不进讲义也没有任何界面能看到。这里只报告，删除必须逐条显式点。 */
  var ORPHAN_REASONS = {
    section_missing: '小节在当前教材树里已定位不到',
    topic_missing: '考向已重建或改名，考点标识对不上',
    unknown_type: '未知的知识块类型',
  };
  var ORPHAN_TYPE_LABELS = { knowledge: '知识梳理', pit: '易错点', method: '解题方法' };

  function openOrphans() {
    var modal = $('lectureOrphanModal');
    if (modal) { modal.classList.remove('hidden'); modal.classList.add('flex'); }
    loadOrphans();
  }

  function closeOrphans() {
    var modal = $('lectureOrphanModal');
    if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
  }

  function loadOrphans() {
    return apiJson('/api/lecture/orphan-notes').then(function (data) {
      renderOrphanList((data && data.items) || []);
    }).catch(function (e) {
      toast('读取孤儿修订稿失败：' + e.message, 'error');
    });
  }

  function renderOrphanList(items) {
    var badge = $('lectureOrphanCount');
    if (badge) {
      badge.textContent = String(items.length);
      badge.classList.toggle('hidden', !items.length);
    }
    var list = $('lectureOrphanList');
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!items.length) {
      list.appendChild(orphanLine('div', '没有发现无法定位的修订稿。', 'text-sm text-slate-400'));
      return;
    }
    items.forEach(function (row) { list.appendChild(orphanCard(row)); });
  }

  function orphanLine(tag, text, cls) {
    var el = document.createElement(tag);
    el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function orphanCard(row) {
    var card = orphanLine('div', '', 'rounded-xl border border-slate-200 dark:border-slate-700 p-3 flex items-start gap-3');
    var body = orphanLine('div', '', 'flex-1 min-w-0');
    var where = [row.compulsory, row.chapter, row.knowledge];
    if (row.topic_id) where.push(row.topic_id);
    body.appendChild(orphanLine('div',
      (ORPHAN_TYPE_LABELS[row.note_type] || row.note_type) + '　' + where.filter(Boolean).join(' / '),
      'text-sm font-bold text-slate-700 dark:text-slate-200 break-all'));
    body.appendChild(orphanLine('div',
      ORPHAN_REASONS[row.reason] || row.reason,
      'text-xs text-amber-600 dark:text-amber-400 mt-0.5'));
    body.appendChild(orphanLine('div',
      row.content_chars + ' 字 · 最后修改 ' + (row.updated_at ? String(row.updated_at).slice(0, 10) : '未知'),
      'text-xs text-slate-400 mt-0.5'));
    var del = orphanLine('button', '删除', 'shrink-0 rounded-lg border border-red-300 dark:border-red-800 px-3 py-1 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20');
    del.type = 'button';
    del.addEventListener('click', function () { deleteOrphan(row, del); });
    card.appendChild(body);
    card.appendChild(del);
    return card;
  }

  function deleteOrphan(row, button) {
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    var qs = ['note_type', 'compulsory', 'chapter', 'knowledge', 'topic_id'].map(function (key) {
      return key + '=' + encodeURIComponent(row[key] || '');
    }).join('&');
    apiJson('/api/lecture/notes?' + qs, { method: 'DELETE' })
      .then(loadOrphans)
      .catch(function (e) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        toast('删除失败：' + e.message, 'error');
      });
  }

  /* ---------------- 对外 ---------------- */
  var _bound = false;
  function bindSelectors() {
    if (_bound) return;
    var g = $('lectureGrade'), c = $('lectureCompulsory'), ch = $('lectureChapter');
    if (!g || !c || !ch) return;
    g.addEventListener('change', function () { ST.grade = g.value; renderCompulsory(); });
    c.addEventListener('change', function () {
      ST.compulsory = c.value; renderChapter();
      // 同步侧：抽屉默认跟随册次
      if (collectTreeControls().series === 'sync' && $('lectureDrawer')) $('lectureDrawer').value = c.value;
    });
    ch.addEventListener('change', function () { ST.chapter = ch.value; renderKnowledgeDropdown(); });
    var s = $('lectureSeries');
    if (s) s.addEventListener('change', function () { renderDrawerOptions(); });
    bindSectionBar();
    _bound = true;
  }
  window.LectureManager = {
    refresh: function () {
      bindSelectors();
      bindMindEdit();
      bindAutosave();
      renderGrade(); renderCompulsory(); renderKnowledgeDropdown();
      renderSectionChips();
      renderDrawerOptions(); renderYearOptions();
      renderDraftTree();
      renderAutosaveBar();
      loadOrphans();
      if (window.systemMetadata) refreshPreview();
    },
    newDraft: newDraft, loadDraft: loadDraft, saveDraft: saveDraft, deleteDraft: deleteDraft,
    cloneDraft: cloneDraft, restoreDraft: restoreDraft, setYearFilter: setYearFilter,
    loadSection: loadSection, addCurrentSection: addCurrentSection, switchActive: switchActive,
    removeSection: removeSectionEntry,
    refreshPreview: refreshPreview,
    switchView: switchView, printLecture: printLecture, exportPdf: exportPdf,
    addItem: addItem, setRole: setRole, removeItem: removeItem,
    addExtra: addExtra, removeExtra: removeExtra,
    editNote: editNote, editKnowledge: editKnowledge, saveNote: saveNote, closeNoteModal: closeNoteModal,
    deleteNote: deleteNote, editMindNode: editMindNode,
    openOrphans: openOrphans, closeOrphans: closeOrphans,
  };
})();
