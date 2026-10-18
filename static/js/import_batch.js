/* 第四录入方案：批量录入工作流前端（教材/练习册 + 整卷）。
 *
 * 对接后端 mathbank/routes_import_batch.py 的 6 阶段流水线：
 *   session -> plan -> recognize(后台) -> risk -> commit / rollback
 * 识别进度轮询沿用既有的 DOCUMENT_TASKS 状态接口 /api/tasks/{id}/status。
 */
(function () {
  'use strict';

  const API = {
    session: '/api/import/batch/session',
    plan: '/api/import/batch/plan',
    recognize: '/api/import/batch/recognize',
    risk: '/api/import/batch/risk',
    commit: '/api/import/batch/commit',
    rollback: '/api/import/batch/rollback',
    list: '/api/import/batch/list',
    detail: '/api/import/batch/detail',
    taskStatus: (id) => '/api/tasks/' + encodeURIComponent(id) + '/status',
    taskCancel: (id) => '/api/tasks/' + encodeURIComponent(id) + '/cancel',
  };

  const STEPS = ['session', 'plan', 'recognize', 'risk', 'commit'];

  const token = () =>
    (typeof window.getLocalToken === 'function'
      ? window.getLocalToken()
      : (localStorage.getItem('local_token') || ''));

  const esc = (s) =>
    (window.MathBankSafe && typeof window.MathBankSafe.escapeText === 'function')
      ? window.MathBankSafe.escapeText(s == null ? '' : String(s))
      : String(s == null ? '' : s)
          .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const $ = (id) => document.getElementById(id);

  function toast(msg, type) {
    if (typeof window.showToast === 'function') window.showToast(msg, type);
    else if (typeof console !== 'undefined') console.log(msg);
  }

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Local-Token': token() },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  }

  const state = {
    session: null,
    taskId: null,
    pollTimer: null,
    risks: [],
    revision: 0,
  };

  function renderSteps(active) {
    const ol = $('batchSteps');
    if (!ol) return;
    Array.from(ol.children).forEach((li) => {
      const step = li.getAttribute('data-step');
      const idx = STEPS.indexOf(step);
      const aIdx = STEPS.indexOf(active);
      const isActive = step === active;
      const done = idx >= 0 && aIdx >= 0 && idx < aIdx;
      li.className =
        'flex-1 px-2 py-1.5 rounded-lg text-center border transition-all ' +
        (isActive
          ? 'bg-brand-500 text-white border-brand-500 font-bold'
          : done
          ? 'bg-green-50 text-green-600 border-green-200'
          : 'bg-slate-50 text-slate-400 border-slate-200');
    });
  }

  function setLog(msg) {
    const el = $('batchLog');
    if (el) el.textContent = msg || '';
  }

  function showProgress(show, pct) {
    const wrap = $('batchProgressWrap');
    if (wrap) wrap.classList.toggle('hidden', !show);
    if (show && pct != null) {
      const bar = $('batchProgressBar');
      if (bar) bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
    }
  }

  function renderActions(step) {
    const box = $('batchActions');
    if (!box) return;
    box.innerHTML = '';
    const primary =
      'px-3 py-1.5 rounded-lg text-xs font-bold text-white bg-brand-500 hover:bg-brand-600 active:scale-95 shadow-sm transition-all';
    const ghost =
      'px-3 py-1.5 rounded-lg text-xs font-bold text-rose-600 border border-rose-200 hover:bg-rose-50 active:scale-95 transition-all';
    const subtle =
      'px-3 py-1.5 rounded-lg text-xs font-bold text-slate-600 border border-slate-200 hover:bg-slate-50 active:scale-95 transition-all';
    const add = (label, cls, fn, id) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.className = cls;
      if (id) b.id = id;
      b.onclick = fn;
      box.appendChild(b);
    };
    if (step === 'session') {
      add('建立会话（上传 PDF）', primary, uploadSession);
    } else if (step === 'plan') {
      add('生成识别计划', primary, buildPlan);
    } else if (step === 'recognize') {
      add('开始逐页识别', primary, startRecognize);
      add('取消识别', ghost, cancelTask);
    } else if (step === 'risk') {
      add('一键接受全部风险', subtle, acceptAllRisks);
      add('确认已勾选的风险', primary, acceptRisks);
    } else if (step === 'commit') {
      add('提交入库', primary, commit);
      if (state.session && state.session.committed) add('回滚本批次', ghost, rollback);
    }
  }

  // ---- 1. session ---------------------------------------------------------
  async function uploadSession() {
    const input = $('batchFileInput');
    const files = input && input.files ? Array.from(input.files) : [];
    if (!files.length) {
      toast('请先选择至少一个 PDF 文件', 'warning');
      return;
    }
    const form = new FormData();
    form.append('mode', $('batchMode').value);
    form.append('title', $('batchTitle').value || '');
    files.forEach((f) => form.append('files', f));
    toast('正在上传并建立会话…', 'info');
    try {
      const res = await fetch(API.session, {
        method: 'POST',
        headers: { 'X-Local-Token': token() },
        body: form,
      });
      const data = await res.json();
      if (data.status === 'success') {
        state.session = data.session;
        toast('会话已建立', 'success');
        renderSteps('plan');
        renderActions('plan');
      } else {
        toast('建立失败：' + (data.message || ''), 'error');
      }
    } catch (e) {
      toast('上传异常：' + e.message, 'error');
    }
  }

  // ---- 2. plan ------------------------------------------------------------
  async function buildPlan() {
    if (!state.session) return;
    const res = await postJson(API.plan, { session_id: state.session.session_id });
    if (res.status === 'success') {
      state.session = res.session;
      renderPlan();
      renderSteps('recognize');
      renderActions('recognize');
    } else {
      toast('生成计划失败：' + (res.message || ''), 'error');
    }
  }

  function renderPlan() {
    const wrap = $('batchPlanWrap');
    const list = $('batchPlanList');
    if (!wrap || !list) return;
    const items = state.session.items || [];
    wrap.classList.remove('hidden');
    list.innerHTML = items
      .map(
        (it) =>
          '<div class="flex justify-between"><span>第 ' +
          esc(it.page) +
          ' 页</span><span class="text-slate-400">' +
          esc(it.status) +
          '</span></div>'
      )
      .join('');
  }

  // ---- 3. recognize (后台任务 + 轮询) ------------------------------------
  async function startRecognize() {
    if (!state.session) return;
    const res = await postJson(API.recognize, { session_id: state.session.session_id });
    if (res.status === 'success') {
      state.taskId = res.task_id;
      showProgress(true, 0);
      setLog('已排队，准备逐页视觉识别…');
      renderSteps('recognize');
      renderActions('recognize');
      pollTask(res.task_id);
    } else {
      toast('启动识别失败：' + (res.message || ''), 'error');
    }
  }

  async function cancelTask() {
    if (!state.taskId) return;
    try {
      await fetch(API.taskCancel(state.taskId), {
        method: 'POST',
        headers: { 'X-Local-Token': token() },
      });
      toast('已发送取消请求', 'info');
    } catch (e) {
      toast('取消异常：' + e.message, 'error');
    }
  }

  function pollTask(taskId) {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      try {
        const res = await fetch(API.taskStatus(taskId), { headers: { 'X-Local-Token': token() } });
        const snap = await res.json();
        const pct = snap.progress != null ? snap.progress : 0;
        showProgress(true, pct);
        setLog(snap.log || snap.message || '处理中…');
        const st = String(snap.status || '').toLowerCase();
        if (st === 'completed' || st === 'failed' || st === 'cancelled') {
          clearInterval(state.pollTimer);
          state.pollTimer = null;
          if (st === 'completed') {
            toast('逐页识别完成', 'success');
            await afterRecognize();
          } else {
            toast('识别' + (st === 'cancelled' ? '已取消' : '失败') + '：' + (snap.message || ''), 'error');
            showProgress(false, 0);
          }
        }
      } catch (e) {
        setLog('轮询状态异常：' + e.message);
      }
    }, 1200);
  }

  async function afterRecognize() {
    await loadSession();
    renderCatalog();
    renderSteps('risk');
    await loadRisks();
  }

  async function loadSession() {
    if (!state.session) return;
    try {
      const res = await fetch(
        API.detail + '?session_id=' + encodeURIComponent(state.session.session_id),
        { headers: { 'X-Local-Token': token() } }
      );
      const data = await res.json();
      if (data.status === 'success') state.session = data.session;
    } catch (e) {
      /* 详情拉取失败不阻断风险流程 */
    }
  }

  function renderCatalog() {
    const wrap = $('batchCatalogWrap');
    const tree = $('batchCatalogTree');
    if (!wrap || !tree) return;
    const catalog = (state.session && state.session.catalog_tree) || [];
    wrap.classList.toggle('hidden', catalog.length === 0);
    if (!catalog.length) return;
    tree.innerHTML = catalog
      .map(
        (ch) =>
          '<div class="border border-slate-200 rounded-xl p-2.5">' +
          '<div class="font-semibold text-slate-700">' +
          esc(ch.title) +
          '</div>' +
          (ch.sections && ch.sections.length
            ? '<div class="mt-1.5 space-y-1">' +
              ch.sections
                .map(
                  (sec) =>
                    '<div class="flex justify-between text-[11px] text-slate-500 pl-2">' +
                    '<span>' +
                    esc(sec.title) +
                    '</span>' +
                    '<span class="text-slate-400">' +
                    sec.count +
                    ' 题</span>' +
                    '</div>'
                )
                .join('') +
              '</div>'
            : '') +
          '</div>'
      )
      .join('');
  }

  // ---- 4. risk ------------------------------------------------------------
  async function loadRisks() {
    if (!state.session) return;
    try {
      const res = await fetch(API.risk + '?session_id=' + encodeURIComponent(state.session.session_id), {
        headers: { 'X-Local-Token': token() },
      });
      const data = await res.json();
      state.risks = data.risks || [];
      state.revision = data.revision || 0;
      renderRisks();
      renderActions('risk');
    } catch (e) {
      toast('加载风险失败：' + e.message, 'error');
    }
  }

  function renderRisks() {
    const wrap = $('batchRiskWrap');
    const list = $('batchRiskList');
    if (!wrap || !list) return;
    wrap.classList.toggle('hidden', state.risks.length === 0);
    list.innerHTML = state.risks
      .map(
        (r, i) =>
          '<div class="border border-amber-200 bg-amber-50/60 rounded-xl p-2.5" data-risk-idx="' +
          i +
          '">' +
          '<div class="text-[11px] font-semibold text-amber-700 flex items-start gap-1.5">' +
          '<i class="fa-solid fa-triangle-exclamation mt-0.5"></i>' +
          '<span>' +
          esc(r.message) +
          '</span></div>' +
          '<div class="mt-1.5 flex items-center gap-2">' +
          '<textarea data-risk-reason="' +
          i +
          '" rows="1" placeholder="必填：接受此风险并入库的理由（如「该页为封底/空白页，无题」）" ' +
          'class="flex-1 text-[11px] rounded-lg border border-amber-300 px-2 py-1 focus:border-amber-500 focus:ring-1 focus:ring-amber-100 resize-y"></textarea>' +
          '</div>' +
          '<label class="mt-1 flex items-center gap-1 text-[10px] text-slate-500">' +
          '<input type="checkbox" data-risk-accept="' +
          i +
          '" class="mr-1"> 我已知悉并接受此风险</label>' +
          '</div>'
      )
      .join('');
  }

  function acceptAllRisks() {
    state.risks.forEach((r, i) => {
      const chk = document.querySelector('[data-risk-accept="' + i + '"]');
      const ta = document.querySelector('[data-risk-reason="' + i + '"]');
      if (chk) chk.checked = true;
      if (ta && !ta.value.trim()) ta.value = '确认按现状入库';
    });
    toast('已为全部风险填入默认接受理由，可逐项修改后再确认', 'info');
  }

  async function acceptRisks() {
    const acceptances = [];
    let bad = null;
    state.risks.forEach((r, i) => {
      const chk = document.querySelector('[data-risk-accept="' + i + '"]');
      if (chk && chk.checked) {
        const ta = document.querySelector('[data-risk-reason="' + i + '"]');
        const reason = (ta ? ta.value : '') || '';
        if (!reason.trim()) {
          bad = i + 1;
          return;
        }
        acceptances.push({ riskId: r.id, reason: reason.trim() });
      }
    });
    if (bad) {
      toast('第 ' + bad + ' 个风险项已勾选接受但理由为空，请填写', 'warning');
      return;
    }
    if (state.risks.length === 0) {
      toast('没有风险项，可直接入库', 'info');
      renderSteps('commit');
      renderActions('commit');
      return;
    }
    if (acceptances.length === 0) {
      toast('请至少勾选并接受一个风险项（全部风险都需确认后才能入库）', 'warning');
      return;
    }
    const res = await postJson(API.risk, {
      session_id: state.session.session_id,
      acceptances: acceptances,
      expected_revision: state.revision,
    });
    if (res.status === 'success') {
      state.session = res.session;
      toast('风险已确认', 'success');
      renderSteps('commit');
      renderActions('commit');
    } else {
      toast('确认失败：' + (res.message || ''), 'error');
    }
  }

  // ---- 5. commit / rollback ----------------------------------------------
  async function commit() {
    const unaccepted = (state.risks || []).filter((r) => !r.accepted);
    if (unaccepted.length) {
      toast('仍有 ' + unaccepted.length + ' 个风险未确认，无法入库', 'warning');
      return;
    }
    const res = await postJson(API.commit, {
      session_id: state.session.session_id,
      preview_hash: state.session.preview_hash,
    });
    if (res.status === 'success') {
      toast('入库成功，共 ' + res.count + ' 题', 'success');
      const box = $('batchResult');
      if (box) {
        box.classList.remove('hidden');
        box.innerHTML =
          '<div class="text-green-600 font-semibold">✓ 入库成功，新增 ' +
          res.count +
          ' 道题（批次 ' +
          esc(res.session_id) +
          '）</div>' +
          '<div class="text-slate-400 mt-1">已可在「本地题库」按来源「' +
          esc(state.session.title || '') +
          '」查看。</div>';
      }
      renderActions('commit');
      await loadHistory();
    } else {
      toast('入库失败：' + (res.message || ''), 'error');
    }
  }

  async function rollback() {
    if (!state.session) return;
    if (!confirm('确认回滚本批次已入库的题？此操作会删除刚刚入库的题目，且不可撤销。')) return;
    const res = await postJson(API.rollback, {
      session_id: state.session.session_id,
      rollback_hash: state.session.preview_hash,
    });
    if (res.status === 'success') {
      toast('已回滚，删除 ' + res.removed + ' 题', 'info');
      await loadHistory();
    } else {
      toast('回滚失败：' + (res.message || ''), 'error');
    }
  }

  // ---- 历史批次 -----------------------------------------------------------
  async function loadHistory() {
    const el = $('batchHistory');
    if (!el) return;
    try {
      const res = await fetch(API.list, { headers: { 'X-Local-Token': token() } });
      const data = await res.json();
      const sessions = (data.sessions || []).slice(0, 12);
      el.innerHTML = sessions.length
        ? sessions
            .map(
              (s) =>
                '<div class="flex justify-between items-center border border-slate-200 rounded-lg px-2 py-1">' +
                '<span class="truncate">' +
                esc(s.title || s.session_id) +
                '</span>' +
                '<span class="text-[10px] text-slate-400 ml-2 whitespace-nowrap">' +
                esc(s.mode) +
                ' · ' +
                (s.committed ? '已入库' : '未完成') +
                '</span></div>'
            )
            .join('')
        : '<div class="text-slate-400">暂无历史批次</div>';
    } catch (e) {
      el.innerHTML = '<div class="text-slate-400">历史加载失败：' + esc(e.message) + '</div>';
    }
  }

  function renderFileList() {
    const input = $('batchFileInput');
    const list = $('batchFileList');
    if (!input || !list) return;
    const files = Array.from(input.files || []);
    list.innerHTML = files.length
      ? files
          .map(
            (f) =>
              '<div class="flex justify-between"><span class="truncate">' +
              esc(f.name) +
              '</span><span class="text-slate-400 ml-2 whitespace-nowrap">' +
              (f.size / 1024 / 1024).toFixed(1) +
              'MB</span></div>'
          )
          .join('')
      : '<div class="text-slate-400">未选择文件</div>';
  }

  function onOpen() {
    renderSteps('session');
    renderActions('session');
    showProgress(false);
    const wrap = $('batchPlanWrap');
    if (wrap) wrap.classList.add('hidden');
    const cw = $('batchCatalogWrap');
    if (cw) cw.classList.add('hidden');
    const rw = $('batchRiskWrap');
    if (rw) rw.classList.add('hidden');
    const res = $('batchResult');
    if (res) res.classList.add('hidden');
    loadHistory();
  }

  function bind() {
    const dz = $('batchDropZone');
    const input = $('batchFileInput');
    if (dz && input) {
      dz.onclick = () => input.click();
      input.onchange = () => renderFileList();
      ['dragover', 'dragenter'].forEach((e) =>
        dz.addEventListener(e, (ev) => {
          ev.preventDefault();
          dz.classList.add('border-brand-500');
        })
      );
      ['dragleave', 'drop'].forEach((e) =>
        dz.addEventListener(e, (ev) => {
          ev.preventDefault();
          dz.classList.remove('border-brand-500');
        })
      );
      dz.addEventListener('drop', (ev) => {
        if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
          try {
            input.files = ev.dataTransfer.files;
            renderFileList();
          } catch (err) {
            toast('该浏览器不支持拖拽赋值，请点击选择文件', 'warning');
          }
        }
      });
    }
    const tabBtn = $('tabBtn-batch');
    if (tabBtn) tabBtn.addEventListener('click', onOpen);
  }

  window.BatchImport = {
    onOpen: onOpen,
    uploadSession: uploadSession,
    buildPlan: buildPlan,
    startRecognize: startRecognize,
    cancelTask: cancelTask,
    acceptAllRisks: acceptAllRisks,
    acceptRisks: acceptRisks,
    commit: commit,
    rollback: rollback,
    loadHistory: loadHistory,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
