// lesson.js - 教案管理工作台 v3
// 以考向树为骨架：考向骨架 → 知识内容（可增删 / 可挂题目）→ 思维导图（可编辑）
(function () {
    'use strict';

    const GRADE_MAP = {
        g1: { name: '高一', children: ['必修一', '必修二'] },
        g2: { name: '高二', children: ['选修一', '选修二', '选修三'] },
        g3: { name: '高三', children: ['一轮复习', '二轮复习', '三轮模拟'] }
    };
    const GRADE_LABEL = { g1: '高一', g2: '高二', g3: '高三' };
    const REVIEW_STAGES = ['一轮复习', '二轮复习', '三轮模拟'];
    const LEVEL_ORDER = ['L1', 'L2', 'L3', 'L4', 'L5'];
    const LEVEL_FALLBACK = { L1: 'L1 概念理解', L2: 'L2 基础运算', L3: 'L3 性质应用', L4: 'L4 综合运用', L5: 'L5 创新拓展' };
    const LEVEL_STYLE = {
        L1: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
        L2: 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300',
        L3: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
        L4: 'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300',
        L5: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300'
    };
    const STAGES = [
        { key: '导入', hint: '情境引入 / 复习旧知 / 明确目标' },
        { key: '新知讲解', hint: '概念生成 / 公式推导 / 要点归纳' },
        { key: '例题精讲', hint: '典型例题 / 思路分析 / 方法小结' },
        { key: '课堂练习', hint: '当堂训练 / 变式巩固 / 反馈纠错' },
        { key: '课堂小结', hint: '知识梳理 / 方法提炼 / 易错提醒' },
        { key: '作业布置', hint: '分层作业 / 拓展思考' }
    ];
    const STAGE_TEMPLATES = {
        '导入': '【情境/问题】\n\n【复习旧知】\n\n【本课目标】1. \n2. ',
        '新知讲解': '【核心概念】\n\n【推导过程】\n\n【要点归纳】\n1. \n2. ',
        '例题精讲': '【例1】\n思路：\n解答：\n小结：\n\n【例2】\n思路：\n解答：',
        '课堂练习': '【练习1】\n【练习2】\n【预设易错点】\n【反馈方式】',
        '课堂小结': '【知识梳理】\n\n【方法提炼】\n\n【易错提醒】',
        '作业布置': '【必做】\n\n【选做】\n\n【预习任务】'
    };

    const state = {
        list: [],
        tree: null,
        q: '',
        status: 'all',
        currentId: null,
        loading: false,
        blocks: [],
        nodes: [],
        questions: []
    };

    /* ---------------- 工具 ---------------- */

    function esc(text) {
        if (window.escapeHtml) return window.escapeHtml(text == null ? '' : String(text));
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function toast(message, type) {
        const palette = { success: 'bg-emerald-600', error: 'bg-rose-600', warning: 'bg-amber-500', info: 'bg-slate-700' };
        const el = document.createElement('div');
        el.className = 'fixed bottom-6 left-1/2 -translate-x-1/2 z-[9999] px-4 py-2.5 rounded-xl text-sm text-white shadow-lg transition-all duration-300 opacity-0 '
            + (palette[type] || palette.success);
        el.textContent = message;
        document.body.appendChild(el);
        requestAnimationFrame(() => { el.classList.remove('opacity-0'); });
        setTimeout(() => { el.classList.add('opacity-0'); setTimeout(() => el.remove(), 320); }, 2000);
    }

    function fmtTime(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        const pad = (n) => String(n).padStart(2, '0');
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function authHeaders() {
        // 令牌统一走 window.getLocalToken（键名 local_token）；取不到就干脆不写
        // 这个头，让全局 fetch 包装器补上，避免空令牌被服务端 403 拒绝。
        const token = window.getLocalToken ? window.getLocalToken() : '';
        const headers = { 'Content-Type': 'application/json' };
        if (token) headers['X-Local-Token'] = token;
        return headers;
    }

    function levelLabel(level) {
        const lv = String(level || '').trim();
        if (!lv) return '未分级';
        return LEVEL_FALLBACK[lv] || lv;
    }

    function levelRank(level) {
        const idx = LEVEL_ORDER.indexOf(String(level || '').trim());
        return idx === -1 ? 99 : idx;
    }

    /* ---------------- 数据加载 ---------------- */

    async function init() {
        await loadCategoryTree();
        await loadLessonList();
    }

    async function loadCategoryTree() {
        try {
            const res = await fetch('/api/categories');
            const data = await res.json();
            state.tree = data && typeof data === 'object' ? data : {};
        } catch (e) {
            console.error('[Lesson] 加载知识点树失败:', e);
            state.tree = {};
        }
    }

    async function loadLessonList() {
        state.loading = true;
        renderList();
        try {
            const res = await fetch('/api/lesson-plans');
            state.list = await res.json();
        } catch (e) {
            console.error('[Lesson] 加载教案列表失败:', e);
            state.list = [];
            toast('教案列表加载失败', 'error');
        } finally {
            state.loading = false;
            renderList();
        }
    }

    function curriculumKeys() {
        const tree = state.tree || {};
        return Object.keys(tree).filter((k) => k !== 'difficulty_labels');
    }

    /* ---------------- 列表 ---------------- */

    function filtered() {
        const q = state.q.trim().toLowerCase();
        return state.list.filter((p) => {
            if (state.status !== 'all' && (p.status || 'draft') !== state.status) return false;
            if (!q) return true;
            const hay = [p.title, p.chapter, p.knowledge, GRADE_LABEL[p.grade] || '', p.compulsory || ''].join(' ').toLowerCase();
            return hay.indexOf(q) !== -1;
        });
    }

    function counts() {
        let draft = 0;
        let published = 0;
        state.list.forEach((p) => {
            if ((p.status || 'draft') === 'published') published += 1;
            else draft += 1;
        });
        return { all: state.list.length, draft: draft, published: published };
    }

    function renderFilterChips() {
        const box = document.getElementById('lessonFilterChips');
        if (!box) return;
        const c = counts();
        const chips = [
            { key: 'all', label: '全部', n: c.all },
            { key: 'draft', label: '草稿', n: c.draft },
            { key: 'published', label: '已发布', n: c.published }
        ];
        box.innerHTML = chips.map((chip) => {
            const on = state.status === chip.key;
            const cls = on
                ? 'bg-brand-600 border-brand-600 text-white'
                : 'bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-700 text-slate-500 dark:text-slate-400 hover:border-brand-400 hover:text-brand-600';
            return '<button type="button" data-filter="' + chip.key + '" class="lesson-filter-chip px-2.5 py-1 text-[11px] rounded-full border transition-colors ' + cls + '">'
                + chip.label + ' <span class="font-mono">' + chip.n + '</span></button>';
        }).join('');
        box.querySelectorAll('.lesson-filter-chip').forEach((btn) => {
            btn.addEventListener('click', () => { state.status = btn.dataset.filter; renderFilterChips(); renderList(); });
        });
    }

    function listItemHtml(plan) {
        const published = (plan.status || 'draft') === 'published';
        const active = state.currentId === plan.id;
        const meta = [GRADE_LABEL[plan.grade] || '', plan.compulsory || '', plan.chapter || ''].filter(Boolean).join(' · ');
        const itemCls = active
            ? 'bg-brand-50 dark:bg-brand-500/10 ring-1 ring-brand-200 dark:ring-brand-500/30'
            : 'hover:bg-slate-100 dark:hover:bg-slate-800';
        return '<div class="lesson-item group relative flex items-start gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors ' + itemCls + '" data-id="' + plan.id + '" title="' + esc(plan.title) + '">'
            + '<span class="mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ' + (published ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600') + '"></span>'
            + '<div class="min-w-0 flex-1">'
            + '<div class="text-[13px] font-medium text-slate-700 dark:text-slate-200 truncate">' + esc(plan.title) + '</div>'
            + '<div class="text-[11px] text-slate-400 truncate mt-0.5">' + esc(meta || '未填写册次') + '</div>'
            + (plan.knowledge ? '<div class="text-[11px] text-slate-400 truncate">' + esc(plan.knowledge) + '</div>' : '')
            + '</div>'
            + '<div class="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity shrink-0">'
            + '<button type="button" class="lesson-edit p-1.5 rounded text-slate-400 hover:text-brand-600 hover:bg-white dark:hover:bg-slate-700" data-id="' + plan.id + '" title="编辑"><i class="fa-solid fa-pen text-[11px]"></i></button>'
            + '<button type="button" class="lesson-clone p-1.5 rounded text-slate-400 hover:text-indigo-600 hover:bg-white dark:hover:bg-slate-700" data-id="' + plan.id + '" title="复制为新教案"><i class="fa-solid fa-clone text-[11px]"></i></button>'
            + '<button type="button" class="lesson-delete p-1.5 rounded text-slate-400 hover:text-rose-500 hover:bg-white dark:hover:bg-slate-700" data-id="' + plan.id + '" title="删除"><i class="fa-solid fa-trash text-[11px]"></i></button>'
            + '</div></div>';
    }

    function renderList() {
        const container = document.getElementById('lessonListContainer');
        if (!container) return;
        renderFilterChips();

        const badge = document.getElementById('lessonCountBadge');
        const footer = document.getElementById('lessonListFooter');
        const c = counts();
        if (badge) badge.textContent = c.all;

        if (state.loading) {
            container.innerHTML = '<div class="text-center py-10 text-slate-400 text-xs"><i class="fa-solid fa-circle-notch fa-spin mr-1"></i>加载中…</div>';
            if (footer) footer.textContent = '';
            return;
        }

        const rows = filtered();
        if (rows.length === 0) {
            const hasAny = state.list.length > 0;
            container.innerHTML = '<div class="text-center py-12 px-4 text-slate-400 text-xs">'
                + '<i class="fa-regular fa-folder-open text-2xl mb-2 opacity-60 block"></i>'
                + (hasAny ? '没有符合条件的教案' : '暂无教案，点击「新建」开始编写')
                + '</div>';
            if (footer) footer.textContent = hasAny ? '0 / ' + c.all + ' 条' : '';
            return;
        }

        let html = '';
        Object.keys(GRADE_MAP).forEach((gradeKey) => {
            const grade = GRADE_MAP[gradeKey];
            const gradeRows = rows.filter((p) => p.grade === gradeKey);
            if (gradeRows.length === 0) return;
            html += '<div class="mb-3"><div class="px-2 mb-1.5 flex items-center gap-1.5">'
                + '<span class="text-[11px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">' + grade.name + '</span>'
                + '<span class="text-[10px] text-slate-300 dark:text-slate-600 font-mono">' + gradeRows.length + '</span></div>';
            grade.children.forEach((comp) => {
                const compRows = gradeRows.filter((p) => p.compulsory === comp);
                if (compRows.length === 0) return;
                html += '<div class="mb-2"><div class="px-2 text-[11px] text-slate-400 mb-0.5">' + esc(comp) + '</div>'
                    + '<div class="space-y-0.5">' + compRows.map(listItemHtml).join('') + '</div></div>';
            });
            const orphans = gradeRows.filter((p) => grade.children.indexOf(p.compulsory) === -1);
            if (orphans.length) {
                html += '<div class="mb-2"><div class="px-2 text-[11px] text-slate-400 mb-0.5">其他册次</div>'
                    + '<div class="space-y-0.5">' + orphans.map(listItemHtml).join('') + '</div></div>';
            }
            html += '</div>';
        });

        const noGrade = rows.filter((p) => !GRADE_MAP[p.grade]);
        if (noGrade.length) {
            html += '<div class="mb-3"><div class="px-2 mb-1.5 text-[11px] font-bold text-slate-500 uppercase tracking-wider">未指定年级 <span class="font-mono text-slate-300">' + noGrade.length + '</span></div>'
                + '<div class="space-y-0.5">' + noGrade.map(listItemHtml).join('') + '</div></div>';
        }

        container.innerHTML = html;
        if (footer) footer.textContent = rows.length + ' / ' + c.all + ' 条 · 草稿 ' + c.draft + ' · 已发布 ' + c.published;

        container.querySelectorAll('.lesson-item').forEach((item) => {
            item.addEventListener('click', (e) => {
                if (e.target.closest('.lesson-edit') || e.target.closest('.lesson-delete') || e.target.closest('.lesson-clone')) return;
                viewLesson(parseInt(item.dataset.id, 10));
            });
        });
        container.querySelectorAll('.lesson-edit').forEach((btn) => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); openLessonEditor(parseInt(btn.dataset.id, 10)); });
        });
        container.querySelectorAll('.lesson-clone').forEach((btn) => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); cloneLesson(parseInt(btn.dataset.id, 10)); });
        });
        container.querySelectorAll('.lesson-delete').forEach((btn) => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); deleteLesson(parseInt(btn.dataset.id, 10)); });
        });
    }

    /* ---------------- 预览 ---------------- */

    function renderEmptyPreview() {
        const area = document.getElementById('lessonPreviewArea');
        if (!area) return;
        area.innerHTML = '<div class="h-full flex items-center justify-center"><div class="text-center text-slate-400 max-w-sm">'
            + '<div class="w-16 h-16 rounded-2xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex items-center justify-center mx-auto mb-4 shadow-sm">'
            + '<i class="fa-solid fa-book-open text-2xl opacity-50"></i></div>'
            + '<p class="text-sm font-medium text-slate-500 dark:text-slate-400">选择左侧教案查看内容</p>'
            + '<p class="text-xs mt-1">或点击「新建」按考向树编写一份新教案</p></div></div>';
    }

    async function viewLesson(planId) {
        state.currentId = planId;
        renderList();
        const area = document.getElementById('lessonPreviewArea');
        if (!area) return;
        area.innerHTML = '<div class="h-full flex items-center justify-center text-slate-400"><i class="fa-solid fa-circle-notch fa-spin text-xl"></i></div>';
        try {
            const res = await fetch('/api/lesson-plans/' + planId);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            renderPreview(await res.json());
        } catch (e) {
            console.error('[Lesson] 加载教案失败:', e);
            area.innerHTML = '<div class="h-full flex items-center justify-center text-rose-500 text-sm">教案加载失败</div>';
        }
    }

    function chip(text, cls) {
        return '<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] '
            + (cls || 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400') + '">' + esc(text) + '</span>';
    }

    function sectionHtml(title, inner, icon) {
        if (!inner) return '';
        return '<section><h4 class="flex items-center gap-1.5 text-xs font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">'
            + (icon ? '<i class="fa-solid ' + icon + ' text-slate-300"></i>' : '')
            + esc(title) + '</h4>' + inner + '</section>';
    }

    function renderMindmapPreview(root) {
        if (!root || !root.text) return '';
        function walk(node) {
            const children = Array.isArray(node.children) ? node.children : [];
            if (children.length === 0) {
                return '<li class="relative pl-4"><span class="absolute left-0 top-[9px] w-2 h-px bg-slate-300 dark:bg-slate-600"></span>'
                    + '<span class="text-[12px] text-slate-600 dark:text-slate-300">' + esc(node.text) + '</span></li>';
            }
            return '<li class="relative pl-4"><span class="absolute left-0 top-[9px] w-2 h-px bg-slate-300 dark:bg-slate-600"></span>'
                + '<span class="text-[12px] font-semibold text-slate-700 dark:text-slate-200">' + esc(node.text) + '</span>'
                + '<ul class="mt-1 space-y-1 border-l border-dashed border-slate-200 dark:border-slate-700 ml-1">'
                + children.map(walk).join('') + '</ul></li>';
        }
        return '<ul class="space-y-1">' + walk(root) + '</ul>';
    }

    function renderPreview(plan) {
        const area = document.getElementById('lessonPreviewArea');
        if (!area) return;

        const published = (plan.status || 'draft') === 'published';
        const process = plan.teaching_process || {};
        const kc = plan.knowledge_content || {};
        const blocks = Array.isArray(kc.blocks) ? kc.blocks : [];
        const items = plan.question_refs && Array.isArray(plan.question_refs.items) ? plan.question_refs.items : [];
        const root = (plan.mindmap && plan.mindmap.root) || null;

        const metaChips = [GRADE_LABEL[plan.grade] || plan.grade, plan.compulsory, plan.chapter, plan.knowledge]
            .filter(Boolean).map((t) => chip(t)).join('');

        const byLevel = {};
        blocks.forEach((b) => {
            const lv = String(b.level || '').trim() || '未分级';
            (byLevel[lv] = byLevel[lv] || []).push(b);
        });
        const levels = Object.keys(byLevel).sort((a, b) => levelRank(a) - levelRank(b));
        const blocksHtml = blocks.length
            ? levels.map((lv) => {
                const rows = byLevel[lv].map((b) => {
                    const qs = items.filter((q) => q.block_id === b.id);
                    const qHtml = qs.length
                        ? '<div class="mt-1.5 space-y-1">' + qs.map((q) => '<div class="text-[12px] text-slate-600 dark:text-slate-300 bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded px-2 py-1.5">'
                            + '<i class="fa-regular fa-file-lines text-slate-300 mr-1"></i>' + esc((q.content || '').slice(0, 160)) + '</div>').join('') + '</div>'
                        : '';
                    return '<div class="rounded-lg border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-800/40 px-3 py-2">'
                        + '<div class="flex items-center gap-2">'
                        + '<span class="text-[11px] px-1.5 py-0.5 rounded ' + (LEVEL_STYLE[lv] || 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400') + '">' + esc(lv) + '</span>'
                        + '<span class="text-[13px] font-medium text-slate-700 dark:text-slate-200">' + esc(b.title || '未命名考向') + '</span>'
                        + (qs.length ? '<span class="text-[10px] text-slate-400 font-mono">' + qs.length + ' 题</span>' : '') + '</div>'
                        + (b.content ? '<div class="text-[13px] text-slate-700 dark:text-slate-300 mt-1 whitespace-pre-wrap">' + esc(b.content) + '</div>' : '')
                        + (b.example ? '<div class="text-[11px] text-slate-400 mt-1">例：' + esc(b.example) + '</div>' : '')
                        + qHtml + '</div>';
                }).join('');
                return '<div class="mb-3"><div class="text-[11px] font-semibold text-slate-500 mb-1">' + esc(levelLabel(lv)) + '（' + byLevel[lv].length + '）</div>'
                    + '<div class="space-y-1.5">' + rows + '</div></div>';
            }).join('')
            : '';

        const filledStages = STAGES.filter((s) => String(process[s.key] || '').trim());
        const stageHtml = filledStages.length
            ? filledStages.map((s, i) => {
                const bullet = '<span class="w-6 h-6 rounded-full bg-brand-50 dark:bg-brand-500/15 text-brand-600 dark:text-brand-400 text-[11px] font-bold flex items-center justify-center">' + (i + 1) + '</span>';
                const line = i < filledStages.length - 1 ? '<span class="flex-1 w-px bg-slate-200 dark:bg-slate-700 my-1"></span>' : '';
                return '<div class="flex gap-3"><div class="flex flex-col items-center shrink-0">' + bullet + line + '</div>'
                    + '<div class="flex-1 pb-3"><div class="text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">' + esc(s.key) + '</div>'
                    + '<div class="text-[13px] leading-relaxed text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/60 rounded-lg px-3 py-2 whitespace-pre-wrap">' + esc(process[s.key]) + '</div></div></div>';
            }).join('')
            : '<p class="text-xs text-slate-400 italic">尚未填写教学过程</p>';

        const statusCls = published
            ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400'
            : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400';

        const objHtml = plan.objectives ? '<p class="text-[13px] leading-relaxed text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/60 rounded-lg px-3 py-2.5 whitespace-pre-wrap">' + esc(plan.objectives) + '</p>' : '';
        const kdHtml = (plan.key_points || plan.difficult_points)
            ? '<div class="grid grid-cols-1 md:grid-cols-2 gap-3">'
                + '<div class="rounded-lg border border-emerald-100 dark:border-emerald-500/20 bg-emerald-50/60 dark:bg-emerald-500/5 px-3 py-2.5"><div class="text-[11px] font-semibold text-emerald-700 dark:text-emerald-400 mb-1">重点</div>'
                + '<div class="text-[13px] text-slate-700 dark:text-slate-300 whitespace-pre-wrap">' + esc(plan.key_points || '—') + '</div></div>'
                + '<div class="rounded-lg border border-amber-100 dark:border-amber-500/20 bg-amber-50/60 dark:bg-amber-500/5 px-3 py-2.5"><div class="text-[11px] font-semibold text-amber-700 dark:text-amber-400 mb-1">难点</div>'
                + '<div class="text-[13px] text-slate-700 dark:text-slate-300 whitespace-pre-wrap">' + esc(plan.difficult_points || '—') + '</div></div></div>'
            : '';
        const boardHtml = plan.board_design ? '<pre class="text-[12px] leading-relaxed text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/60 rounded-lg px-3 py-2.5 whitespace-pre-wrap font-mono">' + esc(plan.board_design) + '</pre>' : '';
        const reflHtml = plan.reflection ? '<p class="text-[13px] leading-relaxed text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-slate-800/60 rounded-lg px-3 py-2.5 whitespace-pre-wrap">' + esc(plan.reflection) + '</p>' : '';
        const mmHtml = root ? '<div class="rounded-lg border border-slate-100 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-800/40 px-4 py-3">' + renderMindmapPreview(root) + '</div>' : '';

        const skeletonHtml = blocks.length
            ? '<div class="flex flex-wrap gap-1.5">' + blocks.length + ' 个考向 · '
                + levels.map((lv) => '<span class="text-[11px] px-2 py-0.5 rounded-full ' + (LEVEL_STYLE[lv] || 'bg-slate-100 text-slate-500') + '">' + esc(lv) + ' ' + byLevel[lv].length + '</span>').join(' ')
                + '</div>'
            : '';

        area.innerHTML = '<div class="max-w-3xl mx-auto">'
            + '<div class="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">'
            + '<div class="px-6 pt-5 pb-4 border-b border-slate-100 dark:border-slate-800"><div class="flex items-start justify-between gap-4">'
            + '<div class="min-w-0"><h2 class="text-xl font-bold text-slate-800 dark:text-slate-100 leading-snug">' + esc(plan.title) + '</h2>'
            + '<div class="flex flex-wrap items-center gap-1.5 mt-2">' + metaChips + '</div></div>'
            + '<div class="flex items-center gap-1 shrink-0">'
            + '<span class="px-2 py-0.5 rounded-full text-[11px] font-medium ' + statusCls + '">' + (published ? '已发布' : '草稿') + '</span>'
            + '<button type="button" onclick="LessonManager.toggleStatus(' + plan.id + ')" class="p-2 rounded-lg text-slate-400 hover:text-emerald-600 hover:bg-slate-100 dark:hover:bg-slate-800" title="' + (published ? '转为草稿' : '发布') + '"><i class="fa-solid ' + (published ? 'fa-toggle-on text-emerald-500' : 'fa-toggle-off') + '"></i></button>'
            + '<button type="button" onclick="LessonManager.openLessonEditor(' + plan.id + ')" class="p-2 rounded-lg text-slate-400 hover:text-brand-600 hover:bg-slate-100 dark:hover:bg-slate-800" title="编辑"><i class="fa-solid fa-pen"></i></button>'
            + '<button type="button" onclick="LessonManager.cloneLesson(' + plan.id + ')" class="p-2 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-slate-100 dark:hover:bg-slate-800" title="复制为新教案"><i class="fa-solid fa-clone"></i></button>'
            + '<button type="button" onclick="LessonManager.exportMarkdown(' + plan.id + ')" class="p-2 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800" title="导出 Markdown"><i class="fa-solid fa-file-arrow-down"></i></button>'
            + '<button type="button" onclick="LessonManager.printLesson(' + plan.id + ')" class="p-2 rounded-lg text-slate-400 hover:text-slate-600 hover:bg-slate-100 dark:hover:bg-slate-800" title="打印 / 导出 PDF"><i class="fa-solid fa-print"></i></button>'
            + '<button type="button" onclick="LessonManager.deleteLesson(' + plan.id + ')" class="p-2 rounded-lg text-slate-400 hover:text-rose-500 hover:bg-slate-100 dark:hover:bg-slate-800" title="删除"><i class="fa-solid fa-trash"></i></button>'
            + '</div></div></div>'
            + '<div class="px-6 py-5 space-y-5">'
            + sectionHtml('考向骨架', skeletonHtml, 'fa-sitemap')
            + sectionHtml('知识内容', blocksHtml, 'fa-book')
            + sectionHtml('思维导图', mmHtml, 'fa-diagram-project')
            + sectionHtml('教学目标', objHtml, 'fa-bullseye')
            + sectionHtml('教学重点与难点', kdHtml, 'fa-scale-balanced')
            + sectionHtml('教学过程', '<div class="mt-1">' + stageHtml + '</div>', 'fa-list-ol')
            + sectionHtml('板书设计', boardHtml, 'fa-chalkboard')
            + sectionHtml('教学反思', reflHtml, 'fa-lightbulb')
            + '</div></div>'
            + '<div class="text-center text-[11px] text-slate-400 mt-3 font-mono">教案 #' + plan.id + ' · 创建 ' + esc(fmtTime(plan.created_at)) + ' · 更新 ' + esc(fmtTime(plan.updated_at)) + '</div></div>';
    }

    /* ---------------- 考向骨架 ---------------- */

    function fillSelect(sel, options, placeholder) {
        if (!sel) return;
        sel.innerHTML = '<option value="">' + esc(placeholder) + '</option>'
            + options.map((o) => '<option value="' + esc(o.value) + '">' + esc(o.label) + '</option>').join('');
    }

    function initOutlineCascade() {
        const compSel = document.getElementById('lessonOutlineCompulsory');
        const chapSel = document.getElementById('lessonOutlineChapter');
        const secSel = document.getElementById('lessonOutlineSection');
        if (!compSel) return;

        const tree = state.tree || {};
        fillSelect(compSel, curriculumKeys().map((k) => ({ value: k, label: k })), '册次');
        fillSelect(chapSel, [], '章节');
        fillSelect(secSel, [], '小节');
        chapSel.disabled = true;
        secSel.disabled = true;

        compSel.onchange = () => {
            const comp = compSel.value;
            if (!comp) {
                fillSelect(chapSel, [], '章节');
                fillSelect(secSel, [], '小节');
                chapSel.disabled = true; secSel.disabled = true;
                return;
            }
            fillSelect(chapSel, Object.keys(tree[comp] || {}).map((c) => ({ value: c, label: c })), '章节');
            fillSelect(secSel, [], '小节');
            chapSel.disabled = false; secSel.disabled = true;
        };

        chapSel.onchange = () => {
            const comp = compSel.value;
            const chap = chapSel.value;
            if (!chap) { fillSelect(secSel, [], '小节'); secSel.disabled = true; return; }
            const sections = (tree[comp] || {})[chap] || [];
            fillSelect(secSel, sections.map((s) => {
                const name = (typeof s === 'string' ? s : s.name);
                return { value: name, label: name };
            }), '小节');
            secSel.disabled = false;
        };
    }

    function setOutlineHint(text) {
        const el = document.getElementById('lessonOutlineHint');
        if (el) el.textContent = text || '';
    }

    function setOutlineSummary(blocks) {
        const el = document.getElementById('lessonOutlineSummary');
        if (!el) return;
        if (!blocks || !blocks.length) { el.textContent = ''; return; }
        const grouped = {};
        blocks.forEach((b) => {
            const lv = String(b.level || '').trim() || '未分级';
            grouped[lv] = (grouped[lv] || 0) + 1;
        });
        const parts = Object.keys(grouped).sort((a, b) => levelRank(a) - levelRank(b)).map((lv) => lv + ' ' + grouped[lv]);
        el.innerHTML = '已载入 <span class="font-mono text-brand-600 dark:text-brand-400">' + blocks.length + '</span> 个考向（' + parts.join(' · ') + '）';
    }

    async function buildOutline() {
        const compSel = document.getElementById('lessonOutlineCompulsory');
        const chapSel = document.getElementById('lessonOutlineChapter');
        const secSel = document.getElementById('lessonOutlineSection');
        const compulsory = compSel ? compSel.value : '';
        const chapter = chapSel ? chapSel.value : '';
        const knowledge = secSel ? secSel.value : '';
        if (!compulsory || !chapter || !knowledge) {
            toast('请先选择册次 / 章节 / 小节', 'warning');
            return;
        }

        const idInput = document.getElementById('lessonId');
        const planId = idInput ? idInput.value : '';
        const hasContent = state.blocks.some((b) => String(b.content || '').trim());
        if (hasContent && !window.confirm('重建会按大纲覆盖当前知识内容（含已录入文字），继续？')) return;

        setOutlineHint('生成中…');
        try {
            if (planId) {
                const res = await fetch('/api/lesson-plans/section-topics?compulsory=' + encodeURIComponent(compulsory)
                    + '&chapter=' + encodeURIComponent(chapter) + '&knowledge=' + encodeURIComponent(knowledge));
                if (!res.ok) throw new Error('HTTP ' + res.status);
                applyOutline(await res.json());
                setOutlineHint('已按大纲重建，保存后生效');
                toast('考向骨架已重建，记得保存');
            } else {
                const res = await fetch('/api/lesson-plans/from-section', {
                    method: 'POST',
                    headers: authHeaders(),
                    body: JSON.stringify({
                        title: (document.getElementById('lessonTitle').value || '').trim(),
                        grade: document.getElementById('lessonGrade').value,
                        compulsory: compulsory,
                        chapter: chapter,
                        knowledge: knowledge
                    })
                });
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const created = await res.json();
                await loadLessonList();
                toast('已按考向树生成教案');
                await openLessonEditor(created.id);
                viewLesson(created.id);
            }
        } catch (e) {
            console.error('[Lesson] 生成考向骨架失败:', e);
            setOutlineHint('生成失败');
            toast('生成考向骨架失败：' + e.message, 'error');
        }
    }

    function applyOutline(data) {
        state.blocks = (data.blocks || []).map((b, i) => ({
            id: b.id || ('kx_' + (i + 1)),
            no: b.no || (i + 1),
            level: b.level || '',
            title: b.title || '',
            topic_id: b.topic_id || '',
            example: b.example || '',
            content: ''
        }));
        state.nodes = treeToRows((data.mindmap && data.mindmap.root) || null);
        const chapInput = document.getElementById('lessonChapter');
        const knowInput = document.getElementById('lessonKnowledge');
        const compSel = document.getElementById('lessonOutlineCompulsory');
        const chapSel = document.getElementById('lessonOutlineChapter');
        const secSel = document.getElementById('lessonOutlineSection');
        if (chapInput && chapSel && chapSel.value) chapInput.value = chapSel.value;
        if (knowInput && secSel && secSel.value) knowInput.value = secSel.value;
        const compSelect = document.getElementById('lessonCompulsory');
        if (compSelect && compSel && compSel.value && !compSelect.value) compSelect.value = compSel.value;
        if (!document.getElementById('lessonTitle').value && secSel && secSel.value) {
            document.getElementById('lessonTitle').value = secSel.value + ' 教案';
        }
        renderBlocks();
        renderMindmapEditor();
        setOutlineSummary(state.blocks);
        updateFormStats();
    }

    /* ---------------- 知识块（考向内容 + 题目） ---------------- */

    function sortedBlocks() {
        return state.blocks.slice().sort((a, b) => levelRank(a.level) - levelRank(b.level) || (a.no || 0) - (b.no || 0));
    }

    function renderBlocks() {
        const box = document.getElementById('lessonBlocks');
        if (!box) return;
        if (state.blocks.length === 0) {
            box.innerHTML = '<div class="rounded-lg border border-dashed border-slate-200 dark:border-slate-700 px-4 py-6 text-center text-[12px] text-slate-400">'
                + '还没有考向。先在上方选择小节并「生成 / 重建」，或点「添加考向」手动增加。</div>';
            updateBlockStats();
            return;
        }

        box.innerHTML = sortedBlocks().map((b) => {
            const qs = state.questions.filter((q) => q.block_id === b.id);
            const lv = String(b.level || '').trim();
            return '<div class="rounded-lg border border-slate-200 dark:border-slate-700 p-2.5" data-block="' + esc(b.id) + '">'
                + '<div class="flex items-center gap-2 mb-2">'
                + '<span class="text-[11px] px-1.5 py-0.5 rounded ' + (LEVEL_STYLE[lv] || 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400') + '">' + esc(lv || '未分级') + '</span>'
                + '<span class="text-[11px] text-slate-400 font-mono">考向' + esc(b.no || '') + '</span>'
                + '<input type="text" class="lesson-block-title flex-1 px-2 py-1 text-[13px] border border-slate-200 rounded dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200" value="' + esc(b.title) + '" placeholder="考向名称">'
                + '<select class="lesson-block-level px-1.5 py-1 text-[11px] border border-slate-200 rounded dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200">'
                + LEVEL_ORDER.map((lv2) => '<option value="' + lv2 + '"' + (lv2 === lv ? ' selected' : '') + '>' + lv2 + '</option>').join('')
                + '<option value=""' + (lv ? '' : ' selected') + '>未分级</option></select>'
                + '<button type="button" class="lesson-block-up p-1.5 rounded text-slate-400 hover:text-brand-600" title="上移"><i class="fa-solid fa-arrow-up text-[10px]"></i></button>'
                + '<button type="button" class="lesson-block-down p-1.5 rounded text-slate-400 hover:text-brand-600" title="下移"><i class="fa-solid fa-arrow-down text-[10px]"></i></button>'
                + '<button type="button" class="lesson-block-del p-1.5 rounded text-slate-400 hover:text-rose-500" title="删除该考向"><i class="fa-solid fa-trash text-[10px]"></i></button>'
                + '</div>'
                + '<textarea class="lesson-block-content w-full px-2.5 py-1.5 text-[13px] border border-slate-200 rounded dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200" rows="2" placeholder="手动输入该考向的知识内容 / 讲解要点…">' + esc(b.content || '') + '</textarea>'
                + (b.example ? '<div class="text-[11px] text-slate-400 mt-1"><i class="fa-solid fa-quote-left mr-1 opacity-60"></i>大纲示例：' + esc(b.example) + '</div>' : '')
                + '<div class="flex items-center justify-between mt-2">'
                + '<button type="button" class="lesson-block-qbtn text-[11px] text-brand-600 dark:text-brand-400 hover:underline"><i class="fa-solid fa-file-circle-plus mr-0.5"></i>题目' + (qs.length ? '（' + qs.length + '）' : '') + '</button>'
                + '<span class="text-[10px] text-slate-400 font-mono">' + (b.topic_id ? '考向ID ' + esc(b.topic_id) : '未关联考向ID') + '</span>'
                + '</div>'
                + '<div class="lesson-block-qpanel hidden mt-2 rounded-lg border border-slate-200 dark:border-slate-700 p-2 space-y-2"></div>'
                + '</div>';
        }).join('');
        bindBlockEvents();
        updateBlockStats();
    }

    function updateBlockStats() {
        const el = document.getElementById('lessonBlockStats');
        if (!el) return;
        const filled = state.blocks.filter((b) => String(b.content || '').trim()).length;
        el.textContent = state.blocks.length + ' 个考向 · ' + filled + ' 个已填写 · ' + state.questions.length + ' 道题';
    }

    function findBlock(id) {
        return state.blocks.filter((b) => b.id === id)[0];
    }

    // 重绘知识块后把题面板重新打开，方便连续加题
    function reopenQuestionPanel(blockId) {
        const card = document.querySelector('#lessonBlocks [data-block="' + blockId + '"]');
        if (!card) return;
        const panel = card.querySelector('.lesson-block-qpanel');
        const block = findBlock(blockId);
        if (!panel || !block) return;
        panel.classList.remove('hidden');
        renderQuestionPanel(panel, block);
    }

    function bindBlockEvents() {
        const box = document.getElementById('lessonBlocks');
        if (!box) return;

        box.querySelectorAll('[data-block]').forEach((card) => {
            const id = card.dataset.block;
            const block = findBlock(id);
            if (!block) return;

            const titleInput = card.querySelector('.lesson-block-title');
            const levelSel = card.querySelector('.lesson-block-level');
            const contentInput = card.querySelector('.lesson-block-content');
            const panel = card.querySelector('.lesson-block-qpanel');

            if (titleInput) titleInput.addEventListener('input', () => { block.title = titleInput.value; updateBlockStats(); updateFormStats(); });
            if (levelSel) levelSel.addEventListener('change', () => { block.level = levelSel.value; renderBlocks(); renderMindmapEditor(); });
            if (contentInput) contentInput.addEventListener('input', () => { block.content = contentInput.value; updateBlockStats(); updateFormStats(); });

            const upBtn = card.querySelector('.lesson-block-up');
            const downBtn = card.querySelector('.lesson-block-down');
            const delBtn = card.querySelector('.lesson-block-del');
            const qBtn = card.querySelector('.lesson-block-qbtn');

            if (upBtn) upBtn.addEventListener('click', () => moveBlock(id, -1));
            if (downBtn) downBtn.addEventListener('click', () => moveBlock(id, 1));
            if (delBtn) delBtn.addEventListener('click', () => {
                if (!window.confirm('删除考向「' + (block.title || '') + '」？其下的题目关联也会移除。')) return;
                state.blocks = state.blocks.filter((b) => b.id !== id);
                state.questions = state.questions.filter((q) => q.block_id !== id);
                renderBlocks();
                renderMindmapEditor();
            });
            if (qBtn) qBtn.addEventListener('click', () => {
                const isHidden = panel.classList.contains('hidden');
                if (!isHidden) { panel.classList.add('hidden'); return; }
                panel.classList.remove('hidden');
                renderQuestionPanel(panel, block);
            });
        });
    }

    function moveBlock(id, delta) {
        const sorted = sortedBlocks();
        const idx = sorted.findIndex((b) => b.id === id);
        const target = idx + delta;
        if (idx === -1 || target < 0 || target >= sorted.length) return;
        const tmp = sorted[idx];
        sorted[idx] = sorted[target];
        sorted[target] = tmp;
        sorted.forEach((b, i) => { b.no = i + 1; });
        state.blocks = sorted;
        renderBlocks();
        renderMindmapEditor();
    }

    function addBlock() {
        state.blocks.push({
            id: 'kx_custom_' + Date.now(),
            no: state.blocks.length + 1,
            level: '',
            title: '',
            topic_id: '',
            example: '',
            content: ''
        });
        renderBlocks();
        renderMindmapEditor();
    }

    function renderQuestionPanel(panel, block) {
        const attached = state.questions.filter((q) => q.block_id === block.id);
        panel.innerHTML = '<div class="flex items-center gap-2">'
            + '<input type="text" class="lesson-q-search flex-1 px-2 py-1.5 text-[12px] border border-slate-200 rounded dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200" placeholder="搜索题库（关键词 / #序号），回车查询">'
            + '<button type="button" class="lesson-q-do px-2.5 py-1.5 text-[12px] rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-200">搜索</button>'
            + '</div>'
            + '<div class="lesson-q-results text-[12px] text-slate-400"></div>'
            + '<div class="border-t border-slate-100 dark:border-slate-800 pt-2">'
            + '<div class="text-[11px] text-slate-400 mb-1">已关联题目（' + attached.length + '）</div>'
            + '<div class="space-y-1">'
            + (attached.length
                ? attached.map((q) => '<div class="flex items-start gap-2 bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded px-2 py-1.5">'
                    + '<div class="flex-1 text-[12px] text-slate-600 dark:text-slate-300">' + esc((q.content || '').slice(0, 140)) + '</div>'
                    + '<button type="button" class="lesson-q-remove text-slate-400 hover:text-rose-500" data-key="' + esc(q.nonce || '') + '" title="移除"><i class="fa-solid fa-xmark text-[11px]"></i></button>'
                    + '</div>').join('')
                : '<div class="text-[11px] text-slate-400">暂无</div>')
            + '</div></div>'
            + '<div class="border-t border-slate-100 dark:border-slate-800 pt-2">'
            + '<div class="text-[11px] text-slate-400 mb-1">手动添加题目（题库没有时可直接粘贴）</div>'
            + '<textarea class="lesson-q-manual w-full px-2 py-1.5 text-[12px] border border-slate-200 rounded dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200" rows="2" placeholder="粘贴题干…"></textarea>'
            + '<button type="button" class="lesson-q-manual-add mt-1 px-2.5 py-1 text-[11px] rounded bg-brand-600 text-white hover:bg-brand-700">添加此题</button>'
            + '</div>';

        const searchInput = panel.querySelector('.lesson-q-search');
        const doBtn = panel.querySelector('.lesson-q-do');
        const results = panel.querySelector('.lesson-q-results');

        function doSearch() {
            const kw = (searchInput.value || '').trim();
            if (!kw) { results.textContent = '请输入关键词'; return; }
            results.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin mr-1"></i>查询中…';
            fetch('/api/questions?q=' + encodeURIComponent(kw) + '&page_size=8')
                .then((r) => r.json())
                .then((data) => {
                    const list = (data && data.items) || [];
                    if (!list.length) {
                        results.innerHTML = '<span class="text-slate-400">题库暂无匹配题目，可在下方手动添加</span>';
                        return;
                    }
                    results.innerHTML = list.map((it) => '<div class="flex items-start gap-2 bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded px-2 py-1.5 mt-1">'
                        + '<div class="flex-1 text-[12px] text-slate-600 dark:text-slate-300">' + esc((it.content || '').slice(0, 120)) + '</div>'
                        + '<button type="button" class="lesson-q-add text-brand-600 hover:underline text-[11px] shrink-0" data-qid="' + it.id + '">添加</button>'
                        + '</div>').join('');
                    results.querySelectorAll('.lesson-q-add').forEach((btn) => {
                        btn.addEventListener('click', () => {
                            const qid = parseInt(btn.dataset.qid, 10);
                            const hit = list.filter((x) => x.id === qid)[0];
                            attachQuestion(block.id, {
                                question_id: qid,
                                content: (hit && hit.content) || '',
                                topic_id: block.topic_id || ''
                            });
                            renderBlocks();
                            reopenQuestionPanel(block.id);
                        });
                    });
                })
                .catch((e) => {
                    console.error('[Lesson] 题目查询失败:', e);
                    results.innerHTML = '<span class="text-rose-500">查询失败</span>';
                });
        }

        if (doBtn) doBtn.addEventListener('click', doSearch);
        if (searchInput) searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); doSearch(); } });

        const manualAdd = panel.querySelector('.lesson-q-manual-add');
        const manualText = panel.querySelector('.lesson-q-manual');
        if (manualAdd) manualAdd.addEventListener('click', () => {
            const text = (manualText.value || '').trim();
            if (!text) { toast('请输入题目内容', 'warning'); return; }
            attachQuestion(block.id, { question_id: null, content: text, topic_id: block.topic_id || '' });
            renderBlocks();
            reopenQuestionPanel(block.id);
        });

        panel.querySelectorAll('.lesson-q-remove').forEach((btn) => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.key;
                state.questions = state.questions.filter((q) => !(q.block_id === block.id && String(q.nonce || '') === key));
                renderBlocks();
                reopenQuestionPanel(block.id);
            });
        });
    }

    function attachQuestion(blockId, payload) {
        state.questions.push({
            block_id: blockId,
            question_id: payload.question_id,
            content: payload.content || '',
            topic_id: payload.topic_id || '',
            nonce: 'q_' + Date.now() + '_' + Math.floor(Math.random() * 1000)
        });
        updateBlockStats();
        toast('已添加题目');
    }

    /* ---------------- 思维导图 ---------------- */

    function treeToRows(root) {
        const rows = [];
        if (!root || !root.text) return rows;
        (function walk(node, depth) {
            rows.push({ depth: depth, text: node.text });
            (node.children || []).forEach((child) => walk(child, depth + 1));
        })(root, 0);
        return rows;
    }

    function rowsToTree(rows) {
        if (!rows.length) return null;
        const root = { text: rows[0].text || '教案', children: [] };
        const stack = [{ node: root, depth: rows[0].depth }];
        for (let i = 1; i < rows.length; i += 1) {
            const row = rows[i];
            const node = { text: row.text || '', children: [] };
            while (stack.length > 1 && stack[stack.length - 1].depth >= row.depth) stack.pop();
            stack[stack.length - 1].node.children.push(node);
            stack.push({ node: node, depth: row.depth });
        }
        return root;
    }

    function renderMindmapEditor() {
        const box = document.getElementById('mindmapEditor');
        if (!box) return;
        if (!state.nodes.length) {
            box.innerHTML = '<div class="text-[12px] text-slate-400 px-2 py-4 text-center">暂无节点。点「由考向树重建」按考向自动生成，或「加节点」手动添加。</div>';
            return;
        }
        box.innerHTML = state.nodes.map((node, i) => {
            return '<div class="mm-row flex items-center gap-1.5" data-idx="' + i + '">'
                + '<span class="text-[10px] text-slate-300 font-mono w-8 shrink-0 text-right" style="padding-left:' + (node.depth * 16) + 'px">L' + node.depth + '</span>'
                + '<button type="button" class="mm-out p-1 rounded text-slate-400 hover:text-slate-700 dark:hover:text-slate-200" title="提升层级"><i class="fa-solid fa-arrow-left text-[10px]"></i></button>'
                + '<button type="button" class="mm-in p-1 rounded text-slate-400 hover:text-slate-700 dark:hover:text-slate-200" title="降为子节点"><i class="fa-solid fa-arrow-right text-[10px]"></i></button>'
                + '<input type="text" class="mm-text flex-1 px-2 py-1 text-[12px] border border-slate-200 rounded dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200" value="' + esc(node.text) + '" placeholder="节点文字">'
                + '<button type="button" class="mm-addchild p-1 rounded text-slate-400 hover:text-brand-600" title="添加子节点"><i class="fa-solid fa-plus text-[10px]"></i></button>'
                + '<button type="button" class="mm-del p-1 rounded text-slate-400 hover:text-rose-500" title="删除节点"><i class="fa-solid fa-xmark text-[10px]"></i></button>'
                + '</div>';
        }).join('');
        bindMindmapEvents();
    }

    function bindMindmapEvents() {
        const box = document.getElementById('mindmapEditor');
        if (!box) return;
        box.querySelectorAll('.mm-row').forEach((row) => {
            const idx = parseInt(row.dataset.idx, 10);
            const input = row.querySelector('.mm-text');
            if (input) input.addEventListener('input', () => { state.nodes[idx].text = input.value; });
            row.querySelector('.mm-out').addEventListener('click', () => {
                if (state.nodes[idx].depth > 0) { state.nodes[idx].depth -= 1; renderMindmapEditor(); }
            });
            row.querySelector('.mm-in').addEventListener('click', () => {
                const prev = state.nodes[idx - 1];
                if (!prev) { toast('第一行不能缩进', 'warning'); return; }
                if (state.nodes[idx].depth < prev.depth + 1) { state.nodes[idx].depth += 1; renderMindmapEditor(); }
            });
            row.querySelector('.mm-addchild').addEventListener('click', () => {
                state.nodes.splice(idx + 1, 0, { depth: state.nodes[idx].depth + 1, text: '' });
                renderMindmapEditor();
            });
            row.querySelector('.mm-del').addEventListener('click', () => {
                state.nodes.splice(idx, 1);
                renderMindmapEditor();
            });
        });
    }

    function addMindNode() {
        const last = state.nodes[state.nodes.length - 1];
        state.nodes.push({ depth: last ? last.depth : 0, text: '' });
        renderMindmapEditor();
    }

    function rebuildMindmap() {
        if (!state.blocks.length) { toast('请先生成考向骨架', 'warning'); return; }
        const rootText = (document.getElementById('lessonKnowledge').value || '').trim()
            || (document.getElementById('lessonTitle').value || '').trim() || '教案';
        const grouped = {};
        state.blocks.forEach((b) => {
            const lv = String(b.level || '').trim() || '未分级';
            (grouped[lv] = grouped[lv] || []).push(b);
        });
        const root = { text: rootText, children: [] };
        Object.keys(grouped).sort((a, b) => levelRank(a) - levelRank(b)).forEach((lv) => {
            root.children.push({
                text: levelLabel(lv),
                children: grouped[lv].map((b) => ({ text: (b.no ? '考向' + b.no + '：' : '') + (b.title || '未命名') }))
            });
        });
        state.nodes = treeToRows(root);
        renderMindmapEditor();
        toast('已按考向树重建思维导图');
    }

    /* ---------------- 教学过程 ---------------- */

    function buildStageInputs(process) {
        const box = document.getElementById('processStagesContainer');
        if (!box) return;
        box.innerHTML = STAGES.map((s) => '<div class="flex items-start gap-2">'
            + '<div class="w-24 shrink-0 pt-2"><div class="text-xs font-medium text-slate-600 dark:text-slate-300">' + esc(s.key) + '</div>'
            + '<div class="text-[10px] text-slate-400 leading-tight mt-0.5">' + esc(s.hint) + '</div></div>'
            + '<textarea id="process_' + s.key + '" rows="2" class="flex-1 px-3 py-2 text-sm border border-slate-200 rounded-lg dark:bg-slate-800 dark:border-slate-700 dark:text-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-transparent" placeholder="' + esc(s.hint) + '"></textarea>'
            + '</div>').join('');
        if (process) {
            STAGES.forEach((s) => {
                const el = document.getElementById('process_' + s.key);
                if (el && process[s.key]) el.value = process[s.key];
            });
        }
        STAGES.forEach((s) => {
            const el = document.getElementById('process_' + s.key);
            if (el) el.addEventListener('input', updateFormStats);
        });
    }

    function collectStages() {
        const out = {};
        STAGES.forEach((s) => {
            const el = document.getElementById('process_' + s.key);
            out[s.key] = el ? el.value : '';
        });
        return out;
    }

    function fillStageTemplate() {
        let filled = 0;
        STAGES.forEach((s) => {
            const el = document.getElementById('process_' + s.key);
            if (el && !String(el.value || '').trim() && STAGE_TEMPLATES[s.key]) {
                el.value = STAGE_TEMPLATES[s.key];
                filled += 1;
            }
        });
        updateFormStats();
        toast(filled ? ('已填入 ' + filled + ' 个环节的引导模板') : '各环节均已有内容，未改动', filled ? 'success' : 'info');
    }

    function updateFormStats() {
        const ids = ['lessonObjectives', 'lessonKeyPoints', 'lessonDifficultPoints', 'lessonBoardDesign', 'lessonReflection'];
        let chars = 0;
        ids.forEach((id) => {
            const el = document.getElementById(id);
            if (el) chars += String(el.value || '').replace(/\s/g, '').length;
        });
        const stages = collectStages();
        let filledStages = 0;
        STAGES.forEach((s) => {
            const v = String(stages[s.key] || '').trim();
            if (v) { filledStages += 1; chars += v.replace(/\s/g, '').length; }
        });
        state.blocks.forEach((b) => { chars += String(b.content || '').replace(/\s/g, '').length; });
        const el = document.getElementById('lessonFormHint');
        if (el) {
            el.textContent = '考向 ' + state.blocks.length
                + ' · 已填 ' + state.blocks.filter((b) => String(b.content || '').trim()).length
                + ' · 题目 ' + state.questions.length
                + ' · 环节 ' + filledStages + '/6 · 约 ' + chars + ' 字';
        }
    }

    /* ---------------- 编辑器 ---------------- */

    function fillCompulsorySelect(current) {
        const sel = document.getElementById('lessonCompulsory');
        if (!sel) return;
        const comps = curriculumKeys();
        const options = comps.concat(REVIEW_STAGES.filter((s) => comps.indexOf(s) === -1));
        // 历史教案可能存了不在大纲里的册次名，保留它避免被静默清空
        if (current && options.indexOf(current) === -1) options.unshift(current);
        sel.innerHTML = '<option value="">请选择</option>'
            + options.map((o) => '<option value="' + esc(o) + '">' + esc(o) + '</option>').join('');
        if (current) sel.value = current;
    }

    async function openLessonEditor(planId) {
        const modal = document.getElementById('lessonEditorModal');
        if (!modal) return;

        const form = document.getElementById('lessonForm');
        if (form) form.reset();
        document.getElementById('lessonId').value = '';
        state.blocks = [];
        state.nodes = [];
        state.questions = [];

        if (!state.tree || curriculumKeys().length === 0) await loadCategoryTree();

        const titleEl = document.getElementById('lessonEditorTitle');
        if (titleEl) titleEl.textContent = planId ? '编辑教案' : '新建教案';

        if (planId) {
            try {
                const res = await fetch('/api/lesson-plans/' + planId);
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const plan = await res.json();

                fillCompulsorySelect(plan.compulsory || '');
                initOutlineCascade();
                buildStageInputs(plan.teaching_process || {});

                document.getElementById('lessonId').value = plan.id;
                document.getElementById('lessonTitle').value = plan.title || '';
                document.getElementById('lessonGrade').value = plan.grade || '';
                document.getElementById('lessonChapter').value = plan.chapter || '';
                document.getElementById('lessonKnowledge').value = plan.knowledge || '';
                document.getElementById('lessonObjectives').value = plan.objectives || '';
                document.getElementById('lessonKeyPoints').value = plan.key_points || '';
                document.getElementById('lessonDifficultPoints').value = plan.difficult_points || '';
                document.getElementById('lessonBoardDesign').value = plan.board_design || '';
                document.getElementById('lessonReflection').value = plan.reflection || '';

                const kc = plan.knowledge_content || {};
                state.blocks = (Array.isArray(kc.blocks) ? kc.blocks : []).map((b, i) => ({
                    id: b.id || ('kx_' + (i + 1)),
                    no: b.no || (i + 1),
                    level: b.level || '',
                    title: b.title || '',
                    topic_id: b.topic_id || '',
                    example: b.example || '',
                    content: b.content || ''
                }));

                const refs = plan.question_refs || {};
                state.questions = (Array.isArray(refs.items) ? refs.items : []).map((q, i) => ({
                    block_id: q.block_id || '',
                    question_id: q.question_id != null ? q.question_id : null,
                    content: q.content || '',
                    topic_id: q.topic_id || '',
                    nonce: 'q_loaded_' + i
                })).filter((q) => q.block_id);

                state.nodes = treeToRows((plan.mindmap && plan.mindmap.root) || null);

                renderBlocks();
                renderMindmapEditor();
                setOutlineSummary(state.blocks);
                setOutlineHint(state.blocks.length ? '当前教案已有考向骨架' : '选择小节后生成 / 重建考向清单');
                updateFormStats();
            } catch (e) {
                console.error('[Lesson] 加载教案详情失败:', e);
                toast('教案详情加载失败', 'error');
                return;
            }
        } else {
            fillCompulsorySelect('');
            initOutlineCascade();
            buildStageInputs(null);
            renderBlocks();
            renderMindmapEditor();
            setOutlineSummary([]);
            setOutlineHint('选择小节后生成 / 重建考向清单');
            updateFormStats();
        }

        modal.classList.remove('hidden');
        modal.classList.add('flex');
        modal.setAttribute('aria-hidden', 'false');
        if (window.MathBankModal) window.MathBankModal.open(modal, { onEscape: closeLessonEditor });
        const titleInput = document.getElementById('lessonTitle');
        if (titleInput) setTimeout(() => titleInput.focus(), 60);
    }

    function closeLessonEditor() {
        const modal = document.getElementById('lessonEditorModal');
        if (!modal) return;
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        modal.setAttribute('aria-hidden', 'true');
        if (window.MathBankModal) window.MathBankModal.close(modal);
        setEditorHint('');
    }

    function setEditorHint(text) {
        const el = document.getElementById('lessonFormHint');
        if (el) el.textContent = text || '';
    }

    async function saveLesson() {
        const id = document.getElementById('lessonId').value;
        const titleValue = String(document.getElementById('lessonTitle').value || '').trim();
        if (!titleValue) {
            toast('请填写教案标题', 'warning');
            document.getElementById('lessonTitle').focus();
            return;
        }

        const payload = {
            title: titleValue,
            grade: document.getElementById('lessonGrade').value,
            compulsory: document.getElementById('lessonCompulsory').value,
            chapter: document.getElementById('lessonChapter').value,
            knowledge: document.getElementById('lessonKnowledge').value,
            topic_ids: state.blocks.map((b) => b.topic_id).filter(Boolean),
            objectives: document.getElementById('lessonObjectives').value,
            key_points: document.getElementById('lessonKeyPoints').value,
            difficult_points: document.getElementById('lessonDifficultPoints').value,
            teaching_process: collectStages(),
            board_design: document.getElementById('lessonBoardDesign').value,
            reflection: document.getElementById('lessonReflection').value,
            knowledge_content: { blocks: state.blocks },
            mindmap: { root: rowsToTree(state.nodes) },
            question_refs: {
                items: state.questions.map((q) => ({
                    block_id: q.block_id,
                    question_id: q.question_id,
                    content: q.content,
                    topic_id: q.topic_id || ''
                }))
            }
        };
        if (!id) payload.status = 'draft';

        setEditorHint('保存中…');
        try {
            const res = await fetch(id ? '/api/lesson-plans/' + id : '/api/lesson-plans', {
                method: id ? 'PUT' : 'POST',
                headers: authHeaders(),
                body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const saved = await res.json();
            closeLessonEditor();
            await loadLessonList();
            toast('教案已保存');
            if (saved && saved.id) viewLesson(saved.id);
        } catch (e) {
            console.error('[Lesson] 保存失败:', e);
            setEditorHint('');
            toast('保存失败：' + e.message, 'error');
        }
    }

    async function toggleStatus(planId) {
        const hit = state.list.filter((p) => p.id === planId)[0];
        if (!hit) return;
        const next = (hit.status || 'draft') === 'published' ? 'draft' : 'published';
        try {
            const res = await fetch('/api/lesson-plans/' + planId, {
                method: 'PUT', headers: authHeaders(), body: JSON.stringify({ status: next })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            await loadLessonList();
            viewLesson(planId);
            toast(next === 'published' ? '已发布' : '已转为草稿');
        } catch (e) {
            console.error('[Lesson] 状态切换失败:', e);
            toast('状态切换失败', 'error');
        }
    }

    async function cloneLesson(planId) {
        try {
            const res = await fetch('/api/lesson-plans/' + planId);
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const src = await res.json();
            const copy = {
                title: (src.title || '未命名教案') + '（副本）',
                grade: src.grade, compulsory: src.compulsory, chapter: src.chapter, knowledge: src.knowledge,
                topic_ids: src.topic_ids, objectives: src.objectives, key_points: src.key_points,
                difficult_points: src.difficult_points, teaching_process: src.teaching_process,
                board_design: src.board_design, reflection: src.reflection,
                question_refs: src.question_refs, knowledge_content: src.knowledge_content, mindmap: src.mindmap,
                status: 'draft'
            };
            const created = await fetch('/api/lesson-plans', {
                method: 'POST', headers: authHeaders(), body: JSON.stringify(copy)
            });
            if (!created.ok) throw new Error('HTTP ' + created.status);
            const saved = await created.json();
            await loadLessonList();
            toast('已复制为新教案');
            if (saved && saved.id) viewLesson(saved.id);
        } catch (e) {
            console.error('[Lesson] 复制失败:', e);
            toast('复制失败', 'error');
        }
    }

    async function deleteLesson(planId) {
        const hit = state.list.filter((p) => p.id === planId)[0];
        const name = hit ? hit.title : ('#' + planId);
        if (!window.confirm('确定删除教案「' + name + '」吗？此操作不可撤销。')) return;
        try {
            const res = await fetch('/api/lesson-plans/' + planId, { method: 'DELETE', headers: authHeaders() });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            if (state.currentId === planId) { state.currentId = null; renderEmptyPreview(); }
            await loadLessonList();
            toast('教案已删除');
        } catch (e) {
            console.error('[Lesson] 删除失败:', e);
            toast('删除失败', 'error');
        }
    }

    async function fetchPlan(planId) {
        const res = await fetch('/api/lesson-plans/' + planId);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
    }

    function download(filename, text, mime) {
        const blob = new Blob([text], { type: (mime || 'text/plain') + ';charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
    }

    async function exportMarkdown(planId) {
        try {
            const plan = await fetchPlan(planId);
            const process = plan.teaching_process || {};
            const blocks = (plan.knowledge_content && plan.knowledge_content.blocks) || [];
            const items = (plan.question_refs && plan.question_refs.items) || [];

            const lines = ['# ' + (plan.title || '教案'), ''];
            const meta = [GRADE_LABEL[plan.grade] || '', plan.compulsory, plan.chapter, plan.knowledge].filter(Boolean).join(' · ');
            if (meta) lines.push('> ' + meta, '');
            if (plan.objectives) lines.push('## 教学目标', '', plan.objectives, '');
            if (plan.key_points || plan.difficult_points) {
                lines.push('## 教学重点与难点', '', '- 重点：' + (plan.key_points || '—'), '- 难点：' + (plan.difficult_points || '—'), '');
            }
            if (blocks.length) {
                lines.push('## 知识内容（按考向）', '');
                const order = {};
                blocks.forEach((b) => { const lv = b.level || '未分级'; (order[lv] = order[lv] || []).push(b); });
                Object.keys(order).sort((a, b) => levelRank(a) - levelRank(b)).forEach((lv) => {
                    lines.push('### ' + levelLabel(lv), '');
                    order[lv].forEach((b) => {
                        lines.push('#### ' + (b.no ? '考向' + b.no + '：' : '') + (b.title || '未命名'));
                        if (b.content) lines.push('', b.content);
                        if (b.example) lines.push('', '> 例：' + b.example);
                        const qs = items.filter((q) => q.block_id === b.id);
                        if (qs.length) {
                            lines.push('', '关联题目：');
                            qs.forEach((q, i) => lines.push((i + 1) + '. ' + (q.content || '')));
                        }
                        lines.push('');
                    });
                });
            }
            if (plan.mindmap && plan.mindmap.root) {
                lines.push('## 思维导图', '', '```');
                (function walk(node, depth) {
                    lines.push(new Array(depth + 1).join('  ') + '- ' + node.text);
                    (node.children || []).forEach((c) => walk(c, depth + 1));
                })(plan.mindmap.root, 0);
                lines.push('```', '');
            }
            const stages = STAGES.filter((s) => String(process[s.key] || '').trim());
            if (stages.length) {
                lines.push('## 教学过程', '');
                stages.forEach((s) => lines.push('### ' + s.key, '', process[s.key], ''));
            }
            if (plan.board_design) lines.push('## 板书设计', '', '```', plan.board_design, '```', '');
            if (plan.reflection) lines.push('## 教学反思', '', plan.reflection, '');

            download((plan.title || 'lesson') + '.md', lines.join('\n'), 'text/markdown');
            toast('已导出 Markdown');
        } catch (e) {
            console.error('[Lesson] 导出失败:', e);
            toast('导出失败', 'error');
        }
    }

    async function printLesson(planId) {
        try {
            const plan = await fetchPlan(planId);
            const process = plan.teaching_process || {};
            const blocks = (plan.knowledge_content && plan.knowledge_content.blocks) || [];
            const items = (plan.question_refs && plan.question_refs.items) || [];

            const blockHtml = blocks.map((b) => {
                const qs = items.filter((q) => q.block_id === b.id);
                return '<h3>' + esc((b.level ? '[' + b.level + '] ' : '') + (b.no ? '考向' + b.no + '：' : '') + (b.title || '')) + '</h3>'
                    + (b.content ? '<p>' + esc(b.content).replace(/\n/g, '<br>') + '</p>' : '')
                    + (b.example ? '<p class="tiny">例：' + esc(b.example) + '</p>' : '')
                    + (qs.length ? '<ol>' + qs.map((q) => '<li>' + esc(q.content || '') + '</li>').join('') + '</ol>' : '');
            }).join('');

            const stageHtml = STAGES.filter((s) => String(process[s.key] || '').trim())
                .map((s) => '<h2>' + s.key + '</h2><p>' + esc(process[s.key]).replace(/\n/g, '<br>') + '</p>').join('');

            const meta = [GRADE_LABEL[plan.grade] || '', plan.compulsory, plan.chapter, plan.knowledge].filter(Boolean).join(' · ');
            const html = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>' + esc(plan.title) + '</title><style>'
                + 'body{font-family:"Songti SC","SimSun",serif;max-width:780px;margin:40px auto;padding:0 24px;color:#111;line-height:1.75}'
                + 'h1{font-size:22px;text-align:center;margin-bottom:6px}.meta{text-align:center;color:#666;font-size:12px;margin-bottom:26px}'
                + 'h2{font-size:15px;border-left:3px solid #3b82f6;padding-left:8px;margin:22px 0 8px}'
                + 'h3{font-size:13px;color:#374151;margin:14px 0 4px}p{font-size:13px;margin:0 0 8px;white-space:pre-wrap}'
                + '.tiny{color:#6b7280;font-size:12px}.box{border:1px solid #e5e7eb;border-radius:6px;padding:10px 12px;font-size:13px;white-space:pre-wrap}'
                + 'ol{margin:4px 0 10px 20px;font-size:13px}@media print{body{margin:0}}'
                + '</style></head><body><h1>' + esc(plan.title) + '</h1><div class="meta">' + meta + '</div>'
                + (plan.objectives ? '<h2>教学目标</h2><div class="box">' + esc(plan.objectives) + '</div>' : '')
                + ((plan.key_points || plan.difficult_points) ? '<h2>教学重点与难点</h2><div class="box">重点：' + esc(plan.key_points || '—') + '\n难点：' + esc(plan.difficult_points || '—') + '</div>' : '')
                + (blockHtml ? '<h2>知识内容（按考向）</h2>' + blockHtml : '')
                + (stageHtml ? '<h2>教学过程</h2>' + stageHtml : '')
                + (plan.board_design ? '<h2>板书设计</h2><div class="box">' + esc(plan.board_design) + '</div>' : '')
                + (plan.reflection ? '<h2>教学反思</h2><div class="box">' + esc(plan.reflection) + '</div>' : '')
                + '</body></html>';
            const win = window.open('', '_blank');
            if (!win) { toast('浏览器拦截了新窗口，请允许弹窗', 'warning'); return; }
            win.document.write(html);
            win.document.close();
            win.focus();
            setTimeout(() => win.print(), 350);
        } catch (e) {
            console.error('[Lesson] 打印失败:', e);
            toast('打印失败', 'error');
        }
    }

    /* ---------------- 全局事件 ---------------- */

    function bindGlobalEvents() {
        const search = document.getElementById('lessonSearchInput');
        if (search) {
            let timer = null;
            search.addEventListener('input', () => {
                clearTimeout(timer);
                timer = setTimeout(() => { state.q = search.value || ''; renderList(); }, 160);
            });
            search.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') { search.value = ''; state.q = ''; renderList(); }
            });
        }

        document.addEventListener('keydown', (e) => {
            const modal = document.getElementById('lessonEditorModal');
            const editing = modal && !modal.classList.contains('hidden');
            if (editing && (e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 's') {
                e.preventDefault();
                saveLesson();
            }
        });
    }

    window.LessonManager = {
        init: init,
        refresh: loadLessonList,
        loadLessonList: loadLessonList,
        openLessonEditor: openLessonEditor,
        closeLessonEditor: closeLessonEditor,
        saveLesson: saveLesson,
        deleteLesson: deleteLesson,
        cloneLesson: cloneLesson,
        toggleStatus: toggleStatus,
        printLesson: printLesson,
        exportMarkdown: exportMarkdown,
        viewLesson: viewLesson,
        buildOutline: buildOutline,
        addBlock: addBlock,
        rebuildMindmap: rebuildMindmap,
        addMindNode: addMindNode,
        fillStageTemplate: fillStageTemplate
    };

    function boot() {
        renderEmptyPreview();
        bindGlobalEvents();
        init();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
