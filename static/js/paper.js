/**
 * paper.js - 本地化数学题库组卷系统 (Paper Studio)
 * 纯 Vanilla JS + 渐进式级联架构
 * 严禁使用正则后行断言 (?<!...) 和 (?<=...)
 */

(function () {
    'use strict';

    // Local Storage Keys
    const STORAGE_KEY_CART = 'mathbank_paper_cart';
    const STORAGE_KEY_META = 'mathbank_paper_meta';
    const STORAGE_KEY_COLLAPSED = 'mathbank_paper_filter_collapsed';

    // Global Store State
    window.PaperStore = {
        cart: [], // Array of { id: number, score: number }
        meta: {
            title: '2026年高中数学模拟考试试卷',
            subtitle: '',
            paper_type: 'exam_19',
            solution_space_default: '7.0',
            show_notice: true,
            show_secret: true
        },
        filters: {
            compulsory: '',
            chapter: '',
            knowledge: '',
            question_type: '',
            grade: '',
            topic: '',
            difficulty: '',
            keyword: '',
            tab: 'all' // 'all' or 'selected'
        },
        isFilterCollapsed: false,
        bankQuestions: [], // Loaded questions from DB based on filters
        questionsMap: {}, // qid -> Question Object
        answerCache: Object.create(null), // qid -> full answer_markdown, loaded on demand
        expandedAnswerIds: new Set(),
        answerLoadingIds: new Set(),
        answerErrors: Object.create(null),
        activeWorkspace: 'bank'
    };

    // Load State from LocalStorage
    function loadStateFromStorage() {
        try {
            const rawCart = localStorage.getItem(STORAGE_KEY_CART);
            if (rawCart) {
                window.PaperStore.cart = JSON.parse(rawCart);
            }
        } catch (e) {
            window.PaperStore.cart = [];
        }

        try {
            const rawMeta = localStorage.getItem(STORAGE_KEY_META);
            if (rawMeta) {
                window.PaperStore.meta = Object.assign({}, window.PaperStore.meta, JSON.parse(rawMeta));
            }
        } catch (e) { }

        try {
            window.PaperStore.isFilterCollapsed = localStorage.getItem(STORAGE_KEY_COLLAPSED) === 'true';
        } catch (e) { }
    }

    function saveCartToStorage() {
        try {
            if (window.PaperStore.cart.length === 0) {
                localStorage.removeItem(STORAGE_KEY_CART);
            } else {
                localStorage.setItem(STORAGE_KEY_CART, JSON.stringify(window.PaperStore.cart));
            }
        } catch (e) { }
        updateCartBadges();
    }

    function saveMetaToStorage() {
        try {
            localStorage.setItem(STORAGE_KEY_META, JSON.stringify(window.PaperStore.meta));
        } catch (e) { }
    }

    function getQuestionFigAlign(q) {
        const defaultAlign = (window.PaperStore.meta.paper_type === 'quiz') ? 'bottom_right' : 'right';
        if (!q) return defaultAlign;
        if (q.custom_figure_align) return q.custom_figure_align;
        if (q.figure_align && q.figure_align !== 'right') return q.figure_align;
        return defaultAlign;
    }

    function hasCachedPaperAnswer(qid) {
        return Object.prototype.hasOwnProperty.call(window.PaperStore.answerCache, qid);
    }

    function seedPaperAnswerCache(q) {
        if (!q || !q.id || typeof q.answer_markdown !== 'string') return;
        window.PaperStore.answerCache[q.id] = q.answer_markdown;
        q.has_answer = Boolean(q.answer_markdown.trim());
    }

    async function loadPaperQuestionAnswer(qid) {
        qid = parseInt(qid, 10);
        if (!qid || window.PaperStore.answerLoadingIds.has(qid)) return;

        const q = window.PaperStore.questionsMap[qid];
        if (q) seedPaperAnswerCache(q);
        if (hasCachedPaperAnswer(qid)) {
            renderPart3QuestionStream();
            return;
        }

        window.PaperStore.answerLoadingIds.add(qid);
        delete window.PaperStore.answerErrors[qid];
        renderPart3QuestionStream();

        try {
            const response = await fetch(`/api/questions/${qid}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const detail = await response.json();
            const answer = typeof detail.answer_markdown === 'string'
                ? detail.answer_markdown
                : '';
            window.PaperStore.answerCache[qid] = answer;
            if (q) {
                q.answer_markdown = answer;
                q.has_answer = Boolean(answer.trim());
            }
        } catch (error) {
            console.error('Load paper question answer error:', error);
            window.PaperStore.answerErrors[qid] = '答案加载失败，请重试。';
        } finally {
            window.PaperStore.answerLoadingIds.delete(qid);
            renderPart3QuestionStream();
        }
    }

    window.togglePaperQuestionAnswer = function (qid) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        if (window.PaperStore.expandedAnswerIds.has(qid)) {
            window.PaperStore.expandedAnswerIds.delete(qid);
            renderPart3QuestionStream();
            return;
        }

        window.PaperStore.expandedAnswerIds.add(qid);
        loadPaperQuestionAnswer(qid);
    };

    window.retryPaperQuestionAnswer = function (qid) {
        qid = parseInt(qid, 10);
        if (!qid) return;
        delete window.PaperStore.answerErrors[qid];
        delete window.PaperStore.answerCache[qid];
        loadPaperQuestionAnswer(qid);
    };

    window.collapseAllPaperAnswers = function () {
        window.PaperStore.expandedAnswerIds.clear();
        renderPart3QuestionStream();
    };

    // Public Cart Helper Functions
    window.isInCart = function (qid) {
        qid = parseInt(qid, 10);
        return window.PaperStore.cart.some(item => item.id === qid);
    };

    window.addToCart = function (qid, score = null) {
        qid = parseInt(qid, 10);
        if (!window.isInCart(qid)) {
            const q = window.PaperStore.questionsMap[qid];
            if (!score && q) {
                score = q.question_type === 'detailed_answer' ? 12 : 5;
            } else if (!score) {
                score = 5;
            }
            window.PaperStore.cart.push({ id: qid, score: parseInt(score, 10) || 5 });
            saveCartToStorage();
            const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
            if (window.showToast) window.showToast(`已将题目 #${seqNum} 加入试卷`, 'success');
            
            if (window.PaperStore.activeWorkspace === 'paper') {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
        }
    };

    window.removeFromCart = function (qid) {
        qid = parseInt(qid, 10);
        window.PaperStore.cart = window.PaperStore.cart.filter(item => item.id !== qid);
        saveCartToStorage();
        if (window.PaperStore.activeWorkspace === 'paper') {
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        }
        const q = window.PaperStore.questionsMap[qid];
        const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
        if (window.showToast) window.showToast(`已将题目 #${seqNum} 移出试卷`, 'info');
    };

    window.clearCart = function () {
        if (confirm('确定要清空已加入试卷的所有题目吗？')) {
            window.PaperStore.cart = [];
            saveCartToStorage();
            if (window.PaperStore.activeWorkspace === 'paper') {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
            if (window.showToast) window.showToast('已清空试卷题目', 'info');
        }
    };

    function updateCartBadges() {
        const count = window.PaperStore.cart.length;
        const badges = document.querySelectorAll('.paper-cart-badge');
        badges.forEach(b => {
            b.textContent = count;
            if (count > 0) {
                b.classList.remove('hidden');
            } else {
                b.classList.add('hidden');
            }
        });
    }

    // Workspace View Switcher
    const originalSelectWorkspace = window.selectWorkspace;
    window.selectWorkspace = function (workspaceId, workspaceName) {
        if (typeof originalSelectWorkspace === 'function') {
            originalSelectWorkspace(workspaceId, workspaceName);
        }

        window.PaperStore.activeWorkspace = workspaceId;
        try {
            localStorage.setItem('mathbank_active_workspace', workspaceId);
            if (window.__serverInstanceId) {
                localStorage.setItem('mathbank_server_instance_id', window.__serverInstanceId);
            }
        } catch (e) { }

        const bankSec = document.getElementById('bankWorkspaceSection');
        const paperSec = document.getElementById('paperWorkspaceSection');
        const toggleSidebarBtn = document.getElementById('toggleSidebarBtn');

        if (workspaceId === 'paper') {
            if (bankSec) bankSec.classList.add('hidden');
            if (toggleSidebarBtn) toggleSidebarBtn.classList.add('hidden');
            if (paperSec) {
                paperSec.classList.remove('hidden');
                window.renderPaperWorkspace();
            }
        } else {
            if (paperSec) paperSec.classList.add('hidden');
            if (toggleSidebarBtn) toggleSidebarBtn.classList.remove('hidden');
            if (bankSec && workspaceId === 'bank') bankSec.classList.remove('hidden');
        }
            // 草稿箱工作台
            const draftWs = document.getElementById('draftWorkspace');
            if (workspaceId === 'drafts') {
                if (draftWs) {
                    draftWs.classList.remove('hidden');
                    if (typeof window.renderDraftList === 'function') {
                        setTimeout(() => window.renderDraftList(), 100);
                    }
                }
            } else {
                if (draftWs) draftWs.classList.add('hidden');
            }
    };

    // Toggle Part 2 Filter Bar Collapsing
    window.togglePaperFilterBar = function () {
        window.PaperStore.isFilterCollapsed = !window.PaperStore.isFilterCollapsed;
        try {
            localStorage.setItem(STORAGE_KEY_COLLAPSED, window.PaperStore.isFilterCollapsed ? 'true' : 'false');
        } catch (e) { }

        const filterBox = document.getElementById('paperFilterSection');
        const toggleIcon = document.getElementById('paperFilterToggleIcon');
        const toggleTxt = document.getElementById('paperFilterToggleTxt');

        if (!filterBox) return;

        if (window.PaperStore.isFilterCollapsed) {
            filterBox.style.maxHeight = '0px';
            filterBox.style.opacity = '0';
            filterBox.style.overflow = 'hidden';
            filterBox.style.paddingTop = '0px';
            filterBox.style.paddingBottom = '0px';
            filterBox.style.marginTop = '0px';
            filterBox.style.marginBottom = '0px';
            if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-down text-xs';
            if (toggleTxt) toggleTxt.textContent = '展开组卷配置栏';
        } else {
            filterBox.style.maxHeight = '700px';
            filterBox.style.opacity = '1';
            filterBox.style.overflow = '';
            filterBox.style.paddingTop = '';
            filterBox.style.paddingBottom = '';
            filterBox.style.marginTop = '';
            filterBox.style.marginBottom = '';
            if (toggleIcon) toggleIcon.className = 'fa-solid fa-chevron-up text-xs';
            if (toggleTxt) toggleTxt.textContent = '收起组卷配置栏';
        }
    };

    // Move Question Order
    window.movePaperQuestion = function (index, direction) {
        const cart = window.PaperStore.cart;
        if (direction === 'up' && index > 0) {
            const temp = cart[index];
            cart[index] = cart[index - 1];
            cart[index - 1] = temp;
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        } else if (direction === 'down' && index < cart.length - 1) {
            const temp = cart[index];
            cart[index] = cart[index + 1];
            cart[index + 1] = temp;
            saveCartToStorage();
            renderPart3QuestionStream();
            window.renderPaperCanvas();
        }
    };

    // Update Score
    window.updatePaperQuestionScore = function (qid, newScore) {
        qid = parseInt(qid, 10);
        newScore = parseInt(newScore, 10) || 5;
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (item) {
            item.score = newScore;
            saveCartToStorage();
            window.renderPaperCanvas();
        }
    };

    // Fetch Questions from DB for Question Bank Stream
    async function fetchBankQuestions() {
        const f = window.PaperStore.filters;
        const params = new URLSearchParams();
        if (f.compulsory) {
            params.append('compulsory', f.compulsory);
            params.append('category_compulsory', f.compulsory);
        }
        if (f.chapter) {
            params.append('chapter', f.chapter);
            params.append('category_chapter', f.chapter);
        }
        if (f.knowledge) {
            params.append('knowledge', f.knowledge);
            params.append('category_knowledge', f.knowledge);
        }
        if (f.grade) {
            params.append('applicable_grade', f.grade);
        }
        if (f.topic) {
            params.append('topic', f.topic);
        }
        if (f.question_type) {
            params.append('qtype', f.question_type);
            params.append('question_type', f.question_type);
        }
        if (f.difficulty) {
            params.append('difficulty', f.difficulty);
        }
        if (f.keyword) {
            params.append('q', f.keyword);
            params.append('search', f.keyword);
        }

        try {
            const res = await fetch(`/api/questions?${params.toString()}`);
            const questions = await res.json();
            if (Array.isArray(questions)) {
                window.PaperStore.bankQuestions = questions;
                questions.forEach(q => {
                    window.PaperStore.questionsMap[q.id] = q;
                });
            }
        } catch (e) {
            console.error('Fetch bank questions error:', e);
        }
    }

    // Render Full Paper Workspace (Part 2, Part 3, Part 4)
    window.renderPaperWorkspace = async function () {
        await fetchBankQuestions();
        renderPart2FilterSection();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
    };

    // Render Part 2: Config & 3-Level Cascade Filter Section
    function renderPart2FilterSection() {
        const meta = window.PaperStore.meta;
        const f = window.PaperStore.filters;
        const container = document.getElementById('paperFilterSection');
        if (!container) return;

        const tree = window.categoryTree || {};
        const metadata = window.systemMetadata || {};

        // 1. Build Compulsory Book options
        let bookOptions = `<option value="">-- 选择册次 --</option>`;
        Object.keys(tree).forEach(b => {
            bookOptions += `<option value="${escapeHtml(b)}" ${f.compulsory === b ? 'selected' : ''}>${escapeHtml(b)}</option>`;
        });

        // 2. Build Chapter options (Level 2)
        let chapterOptions = `<option value="">-- 先选册次 --</option>`;
        let isChapterDisabled = true;
        if (f.compulsory && tree[f.compulsory]) {
            isChapterDisabled = false;
            chapterOptions = `<option value="">-- 所有章节 --</option>`;
            Object.keys(tree[f.compulsory]).forEach(ch => {
                chapterOptions += `<option value="${escapeHtml(ch)}" ${f.chapter === ch ? 'selected' : ''}>${escapeHtml(ch)}</option>`;
            });
        }

        // 3. Build Knowledge options (Level 3)
        let knowledgeOptions = `<option value="">-- 先选章节 --</option>`;
        let isKnowledgeDisabled = true;
        if (f.compulsory && f.chapter && tree[f.compulsory] && tree[f.compulsory][f.chapter]) {
            isKnowledgeDisabled = false;
            knowledgeOptions = `<option value="">-- 所有小节/知识点 --</option>`;
            const knowList = tree[f.compulsory][f.chapter];
            if (typeof knowList === 'object' && knowList !== null) {
                Object.keys(knowList).forEach(k => {
                    knowledgeOptions += `<option value="${escapeHtml(k)}" ${f.knowledge === k ? 'selected' : ''}>${escapeHtml(k)}</option>`;
                });
            }
        }

        // 3.2 Build Grade options
        let gradeOptions = `<option value="">全部年级</option>`;
        const gradeList = (metadata && Array.isArray(metadata.applicable_grades))
            ? metadata.applicable_grades
            : [{value:'g1',label:'高一'},{value:'g2',label:'高二'},{value:'g3',label:'高三'}];
        gradeList.forEach(g => {
            gradeOptions += `<option value="${escapeHtml(g.value)}" ${f.grade === g.value ? 'selected' : ''}>${escapeHtml(g.label)}</option>`;
        });

        // 3.5 Build Topic options (Level 4)
        let topicOptions = `<option value="">-- 先选小节 --</option>`;
        let isTopicDisabled = true;
        if (f.compulsory && f.chapter && f.knowledge && tree[f.compulsory] && tree[f.compulsory][f.chapter] && tree[f.compulsory][f.chapter][f.knowledge]) {
            isTopicDisabled = false;
            topicOptions = `<option value="">-- 所有考点 --</option>`;
            const topicList = tree[f.compulsory][f.chapter][f.knowledge];
            if (Array.isArray(topicList)) {
                topicList.forEach(t => {
                    const topicName = typeof t === 'object' ? t.name : t;
                    topicOptions += `<option value="${escapeHtml(topicName)}" ${f.topic === topicName ? 'selected' : ''}>${escapeHtml(topicName)}</option>`;
                });
            }
        }

        // 4. Build Question Type options
        let qTypeOptions = `<option value="">全部题型</option>`;
        const qTypes = metadata.question_types || [
            { value: 'single_choice', label: '单选题' },
            { value: 'multi_choice', label: '多选题' },
            { value: 'fill_in_blank', label: '填空题' },
            { value: 'detailed_answer', label: '解答题' }
        ];
        qTypes.forEach(t => {
            qTypeOptions += `<option value="${escapeHtml(t.value)}" ${f.question_type === t.value ? 'selected' : ''}>${escapeHtml(t.label)}</option>`;
        });

        // 5. Build Difficulty options
        let diffOptions = `<option value="">全部难度</option>`;
        const difficulties = metadata.difficulties || [
            { value: 'basic', label: '基础' },
            { value: 'medium', label: '中档' },
            { value: 'hard', label: '压轴' },
            { value: 'novel', label: '新定义' }
        ];
        difficulties.forEach(d => {
            diffOptions += `<option value="${escapeHtml(d.value)}" ${f.difficulty === d.value ? 'selected' : ''}>${escapeHtml(d.label)}</option>`;
        });

        container.innerHTML = `
            <div class="space-y-2 bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl shadow-sm">
                <!-- Top Row: Paper Metadata -->
                <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">主标题</label>
                        <input type="text" id="paperMetaTitle" value="${escapeHtml(meta.title)}" 
                            oninput="updatePaperMeta('title', this.value)" onchange="updatePaperMeta('title', this.value)"
                            class="glass-input w-full px-2 py-1 text-xs rounded-lg" placeholder="如：2026年高中数学期末考试">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">副标题 / 备注</label>
                        <input type="text" id="paperMetaSubtitle" value="${escapeHtml(meta.subtitle)}" 
                            oninput="updatePaperMeta('subtitle', this.value)" onchange="updatePaperMeta('subtitle', this.value)"
                            class="glass-input w-full px-2 py-1 text-xs rounded-lg" placeholder="可选副标题/说明...">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">试卷类型预设</label>
                        <select id="paperMetaType" onchange="updatePaperMeta('paper_type', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            <option value="exam_19" ${meta.paper_type === 'exam_19' ? 'selected' : ''}>19题高考卷 (含答题卡)</option>
                            <option value="exam" ${meta.paper_type === 'exam' ? 'selected' : ''}>常规试卷</option>
                            <option value="quiz" ${meta.paper_type === 'quiz' ? 'selected' : ''}>日常小练</option>
                        </select>
                    </div>
                </div>

                <!-- Middle Row 1: 年级 -> 册次 -> 章节 -> 小节 -> 考点 -->
                <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-1.5 border-t border-slate-100 dark:border-slate-800/60">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">年级</label>
                        <select id="paperFilterGrade" onchange="onPaperFilterChange('grade', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${gradeOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">册次</label>
                        <select id="paperFilterCompulsory" onchange="onPaperFilterChange('compulsory', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${bookOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">章节</label>
                        <select id="paperFilterChapter" onchange="onPaperFilterChange('chapter', this.value)" ${isChapterDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${chapterOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">小节</label>
                        <select id="paperFilterKnowledge" onchange="onPaperFilterChange('knowledge', this.value)" ${isKnowledgeDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${knowledgeOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">考点</label>
                        <select id="paperFilterTopic" onchange="onPaperFilterChange('topic', this.value)" ${isTopicDisabled ? 'disabled' : ''}
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg disabled:opacity-50">
                            ${topicOptions}
                        </select>
                    </div>
                </div>

                <!-- Middle Row 2: Question Type, Difficulty & Search Input -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">题型</label>
                        <select id="paperFilterType" onchange="onPaperFilterChange('question_type', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${qTypeOptions}
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">难度</label>
                        <select id="paperFilterDifficulty" onchange="onPaperFilterChange('difficulty', this.value)"
                            class="glass-select w-full px-2 py-1 text-xs rounded-lg">
                            ${diffOptions}
                        </select>
                    </div>
                    <div class="col-span-2">
                        <label class="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-0.5">来源 / 自定义标签 / 关键词</label>
                        <div class="relative">
                            <i class="fa-solid fa-magnifying-glass text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2 text-xs"></i>
                            <input type="text" id="paperFilterKeyword" value="${escapeHtml(f.keyword)}"
                                oninput="onPaperFilterChange('keyword', this.value)"
                                class="glass-input w-full pl-7 pr-2.5 py-1 text-xs rounded-lg" placeholder="搜索题干内容 / 来源 / 标签 / 批注...">
                        </div>
                    </div>
                </div>

                <!-- Bottom Row: AI Prompt Selection Bar -->
                <div class="pt-1.5 border-t border-slate-100 dark:border-slate-800/60 flex items-center space-x-2">
                    <div class="relative flex-1">
                        <i class="fa-solid fa-wand-magic-sparkles text-brand-500 absolute left-2.5 top-1/2 -translate-y-1/2 text-[10px]"></i>
                        <input type="text" id="paperAiPromptInput" 
                            class="glass-input w-full pl-7 pr-2.5 py-1 text-[10px] rounded-lg border-brand-200/80"
                            placeholder="智能一键抽卷：例如“帮我抽 5 道难度中等的函数选择题”"
                            onkeypress="if(event.key==='Enter') triggerAiPaperSelect()">
                    </div>
                    <button onclick="triggerAiPaperSelect()" class="glass-btn-primary h-[28px] px-3 rounded-lg text-[10px] font-semibold flex items-center space-x-1 shrink-0">
                        <span>智能抽取</span>
                    </button>
                </div>
            </div>
        `;
    }
    window.renderPart2FilterSection = renderPart2FilterSection;

    let filterDebounceTimer = null;
    window.onPaperFilterChange = function (key, value) {
        window.PaperStore.filters[key] = value;
        
        // Handle cascade resets
        if (key === 'compulsory') {
            window.PaperStore.filters.chapter = '';
            window.PaperStore.filters.knowledge = '';
            window.PaperStore.filters.topic = '';
            renderPart2FilterSection();
        } else if (key === 'chapter') {
            window.PaperStore.filters.knowledge = '';
            window.PaperStore.filters.topic = '';
            renderPart2FilterSection();
        } else if (key === 'knowledge') {
            window.PaperStore.filters.topic = '';
            renderPart2FilterSection();
        }

        if (key === 'keyword') {
            clearTimeout(filterDebounceTimer);
            filterDebounceTimer = setTimeout(async () => {
                await fetchBankQuestions();
                renderPart3QuestionStream();
            }, 300);
        } else {
            fetchBankQuestions().then(() => {
                renderPart3QuestionStream();
            });
        }
    };

    function syncCanvasHeaderMeta(key, value) {
        const cleanVal = (value || '').trim();
        if (key === 'title') {
            const nodes = document.querySelectorAll('.canvas-meta-title');
            nodes.forEach(node => {
                if (node !== document.activeElement) {
                    if (!cleanVal) {
                        node.innerHTML = '';
                    } else if (node.innerText !== value) {
                        node.innerText = value;
                    }
                } else if (!cleanVal && node.innerHTML !== '') {
                    if (node.innerText.trim() === '') node.innerHTML = '';
                }
            });
            const leftInput = document.getElementById('paperMetaTitle');
            if (leftInput && leftInput !== document.activeElement && leftInput.value !== value) {
                leftInput.value = value;
            }
        } else if (key === 'subtitle') {
            const nodes = document.querySelectorAll('.canvas-meta-subtitle');
            nodes.forEach(node => {
                if (node !== document.activeElement) {
                    if (!cleanVal) {
                        node.innerHTML = '';
                    } else if (node.innerText !== value) {
                        node.innerText = value;
                    }
                } else if (!cleanVal && node.innerHTML !== '') {
                    if (node.innerText.trim() === '') node.innerHTML = '';
                }
            });
            const leftInput = document.getElementById('paperMetaSubtitle');
            if (leftInput && leftInput !== document.activeElement && leftInput.value !== value) {
                leftInput.value = value;
            }
        }
    }

    window.updatePaperMeta = function (key, value) {
        window.PaperStore.meta[key] = value;
        if (key === 'paper_type') {
            const newDefault = value === 'exam_19' ? '0.0' : '7.0';
            window.PaperStore.meta.solution_space_default = newDefault;
            window.PaperStore.cart.forEach(item => {
                item.solution_space = newDefault;
            });
            saveCartToStorage();
            renderPart2FilterSection();
        }
        saveMetaToStorage();

        if (key === 'title' || key === 'subtitle') {
            syncCanvasHeaderMeta(key, value);
        } else {
            window.renderPaperCanvas();
        }
    };

    window.triggerAiPaperSelect = async function () {
        const input = document.getElementById('paperAiPromptInput');
        const btn = document.querySelector('button[onclick="triggerAiPaperSelect()"]');
        if (!input || !input.value.trim()) {
            if (window.showToast) window.showToast('请先输入智能抽卷要求', 'warning');
            return;
        }
        const promptText = input.value.trim();
        const origBtnHtml = btn ? btn.innerHTML : '';

        try {
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = `<i class="fa-solid fa-brain fa-spin text-xs"></i><span>AI 思考组卷中...</span>`;
            }
            if (window.showToast) window.showToast('正在调用 AI 大模型分析需求并遴选最佳题目...', 'info');
            const f = window.PaperStore.filters;
            const res = await fetch('/api/paper/ai-select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: promptText,
                    limit: 5,
                    compulsory: f.compulsory,
                    chapter: f.chapter,
                    knowledge: f.knowledge,
                    question_type: f.question_type,
                    difficulty: f.difficulty
                })
            });
            const data = await res.json();
            if (data.status === 'success' && Array.isArray(data.data)) {
                let addedCount = 0;
                data.data.forEach(q => {
                    if (!window.isInCart(q.id)) {
                        window.PaperStore.cart.push({ id: q.id, score: q.question_type === 'detailed_answer' ? 12 : 5 });
                        window.PaperStore.questionsMap[q.id] = q;
                        addedCount++;
                    }
                });
                if (data.ai_analysis) {
                    window.PaperStore.meta.ai_analysis = data.ai_analysis;
                    window.PaperStore.meta.ai_model_used = data.model_used || '大模型';
                    saveMetaToStorage();
                }
                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) {
                    const engineLabel = data.fallback ? '算法' : (data.model_used || 'AI 大模型');
                    window.showToast(`AI (${engineLabel}) 成功挑选并加入了 ${addedCount} 道试题`, 'success');
                }
            } else {
                if (window.showToast) window.showToast(data.message || '抽选试题无结果', 'warning');
            }
        } catch (e) {
            if (window.showToast) window.showToast('AI 抽题请求异常', 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origBtnHtml;
            }
        }
    };

    window.clearAiAnalysis = function () {
        delete window.PaperStore.meta.ai_analysis;
        delete window.PaperStore.meta.ai_model_used;
        saveMetaToStorage();
        window.renderPaperCanvas();
    };

    // Switch Part 3 Tab ('all' or 'selected')
    window.switchPaperStreamTab = function (tabName) {
        window.PaperStore.filters.tab = tabName;
        renderPart3QuestionStream();
    };

    // Render Part 3: Full-Width Question Stream
    function renderPart3QuestionStream() {
        const container = document.getElementById('paperQuestionStream');
        if (!container) return;

        const cart = window.PaperStore.cart;
        const bankQuestions = window.PaperStore.bankQuestions;
        const currentTab = window.PaperStore.filters.tab || 'all';

        // Prepare list based on tab
        let displayList = [];
        if (currentTab === 'selected') {
            displayList = cart.map(item => window.PaperStore.questionsMap[item.id]).filter(Boolean);
        } else {
            displayList = bankQuestions;
        }
        const hasVisibleExpandedAnswers = displayList.some(
            q => q && window.PaperStore.expandedAnswerIds.has(q.id)
        );

        let html = `
            <!-- Part 3 Stream Header Bar -->
            <div class="flex flex-wrap items-center justify-between gap-2 pb-3 mb-4 border-b border-slate-200/60 dark:border-slate-700/60">
                <div class="flex items-center space-x-1.5 bg-slate-200/60 p-1 rounded-xl dark:bg-slate-800">
                    <button onclick="switchPaperStreamTab('all')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'all' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        全库试题 (${bankQuestions.length})
                    </button>
                    <button onclick="switchPaperStreamTab('selected')" 
                        class="px-3 py-1 rounded-lg text-xs font-bold transition-all ${currentTab === 'selected' ? 'bg-white text-brand-600 shadow-sm dark:bg-slate-700 dark:text-brand-200' : 'text-slate-500 hover:text-slate-800 dark:text-slate-400'}">
                        已选试题 (${cart.length})
                    </button>
                </div>

                <div class="flex flex-wrap items-center justify-end gap-3">
                    ${hasVisibleExpandedAnswers ? `
                        <button type="button" onclick="window.collapseAllPaperAnswers()"
                            class="text-xs font-medium text-slate-500 hover:text-brand-600 transition-colors flex items-center space-x-1">
                            <i class="fa-solid fa-eye-slash text-[10px]"></i>
                            <span>收起全部答案</span>
                        </button>
                    ` : ''}
                    ${cart.length > 0 ? `
                        <button type="button" onclick="window.clearCart()" class="text-xs font-medium text-slate-400 hover:text-rose-500 transition-colors flex items-center space-x-1">
                            <i class="fa-solid fa-trash-can text-[10px]"></i>
                            <span>清空卷面 (${cart.length})</span>
                        </button>
                    ` : ''}
                </div>
            </div>
        `;

        if (displayList.length === 0) {
            html += `
                <div class="flex flex-col items-center justify-center py-20 bg-white/50 backdrop-blur-md rounded-2xl border border-dashed border-slate-300 dark:bg-slate-800/40 dark:border-slate-700">
                    <div class="w-12 h-12 rounded-2xl bg-brand-50 text-brand-500 flex items-center justify-center text-xl mb-3 dark:bg-slate-800">
                        <i class="fa-solid fa-folder-open"></i>
                    </div>
                    <h4 class="font-semibold text-slate-700 dark:text-slate-200 mb-1">未找到符合条件的题目</h4>
                    <p class="text-xs text-slate-500 max-w-xs text-center">请在上方调节学段、章节、题型、难度或搜索条件。</p>
                </div>
            `;
            container.innerHTML = html;
            return;
        }

        html += `<div class="space-y-4">`;

        displayList.forEach((q, index) => {
            const inCart = window.isInCart(q.id);
            const cartItem = cart.find(it => it.id === q.id);
            const currentScore = cartItem ? cartItem.score : (q.question_type === 'detailed_answer' ? 12 : 5);
            const qTypeLabel = getQuestionTypeCn(q.question_type);
            const diffTag = getDifficultyBadge(q.difficulty, { size: 'regular' });
            const usageCount = q.usage_count || 0;
            seedPaperAnswerCache(q);
            const answerExpanded = window.PaperStore.expandedAnswerIds.has(q.id);
            const answerLoading = window.PaperStore.answerLoadingIds.has(q.id);
            const answerError = window.PaperStore.answerErrors[q.id] || '';
            const answerCached = hasCachedPaperAnswer(q.id);
            const answerText = answerCached ? window.PaperStore.answerCache[q.id] : '';
            const answerAvailabilityKnown = typeof q.has_answer === 'boolean' || answerCached;
            const hasAnswer = Boolean((answerText || '').trim()) || q.has_answer === true || !answerAvailabilityKnown;

            let answerBodyHtml = '';
            if (answerExpanded) {
                if (answerLoading) {
                    answerBodyHtml = `
                        <div class="flex items-center justify-center gap-2 py-5 text-xs text-slate-500 dark:text-slate-400" aria-live="polite">
                            <i class="fa-solid fa-spinner fa-spin text-brand-500"></i>
                            <span>正在加载答案与解析...</span>
                        </div>
                    `;
                } else if (answerError) {
                    answerBodyHtml = `
                        <div class="flex flex-wrap items-center justify-between gap-2 py-3 text-xs text-rose-600 dark:text-rose-300" role="alert">
                            <span><i class="fa-solid fa-circle-exclamation mr-1"></i>${escapeHtml(answerError)}</span>
                            <button type="button" onclick="window.retryPaperQuestionAnswer(${q.id})"
                                class="px-3 py-1.5 rounded-lg border border-rose-200 bg-white hover:bg-rose-50 font-semibold transition-colors dark:bg-slate-800 dark:border-rose-900/60 dark:hover:bg-slate-700">
                                重试
                            </button>
                        </div>
                    `;
                } else if ((answerText || '').trim()) {
                    answerBodyHtml = typeof window.parseMarkdownWithMath === 'function'
                        ? window.parseMarkdownWithMath(answerText)
                        : window.MathBankSafe.sanitizeRichHtml(answerText);
                } else {
                    answerBodyHtml = '<p class="py-3 text-xs text-slate-400 italic">本题暂无答案与解析。</p>';
                }
            }

            const cardBorderClass = inCart 
                ? 'border-brand-500 ring-2 ring-brand-500/20 bg-brand-50/10 dark:border-brand-500/60 dark:bg-brand-900/20'
                : 'border-slate-200/80 hover:border-brand-200/80 bg-white/80 dark:bg-slate-800/80 dark:border-slate-700/70';

            html += `
                <div class="p-5 rounded-2xl border ${cardBorderClass} shadow-sm hover:shadow-md transition-all">
                    <!-- Card Top Controls Bar -->
                    <div class="flex flex-col gap-3 pb-3 mb-3 border-b border-slate-100 sm:flex-row sm:items-center sm:justify-between dark:border-slate-700/60">
                        <div class="flex w-full items-center space-x-2 flex-wrap gap-y-1 sm:w-auto">
                            <span class="font-bold text-slate-800 dark:text-slate-100 text-sm">#${escapeHtml(q.seq_num !== undefined ? q.seq_num : q.id)}</span>
                            <span class="px-2 py-0.5 rounded-lg text-xs font-semibold bg-brand-50 text-brand-600 border border-brand-200/50 dark:bg-brand-900/30 dark:text-brand-200 dark:border-brand-900/50">${escapeHtml(qTypeLabel)}</span>
                            ${diffTag}
                            ${q.category_compulsory ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">${escapeHtml(q.category_compulsory)}</span>` : ''}
                            ${q.category_chapter ? `<span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400">${escapeHtml(q.category_chapter)}</span>` : ''}
                            <span class="px-2 py-0.5 rounded-lg text-xs font-medium bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400" title="引用次数">引用 ${escapeHtml(usageCount)} 次</span>
                        </div>

                        <div class="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
                            <button type="button"
                                onclick="window.togglePaperQuestionAnswer(${q.id})"
                                aria-expanded="${answerExpanded ? 'true' : 'false'}"
                                ${answerExpanded ? `aria-controls="paper-q-answer-${q.id}"` : ''}
                                ${(!hasAnswer && !answerExpanded) || answerLoading ? 'disabled' : ''}
                                title="${hasAnswer || answerExpanded ? (answerExpanded ? '收起本题答案与解析' : '查看本题答案与解析') : '本题暂无答案'}"
                                class="min-h-[44px] sm:min-h-[32px] px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all flex items-center justify-center space-x-1.5
                                    ${answerExpanded
                                        ? 'border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100 dark:border-brand-700 dark:bg-brand-900/40 dark:text-brand-200'
                                        : 'border-slate-200 bg-white/80 text-slate-600 hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-brand-700'}
                                    disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-slate-200 disabled:hover:bg-white/80 disabled:hover:text-slate-600">
                                <i class="fa-solid ${answerLoading ? 'fa-spinner fa-spin' : (answerExpanded ? 'fa-eye-slash' : 'fa-eye')} text-[11px]"></i>
                                <span>${answerLoading ? '加载中' : (answerExpanded ? '收起答案' : (hasAnswer ? '查看答案' : '暂无答案'))}</span>
                            </button>

                            ${inCart ? `
                                <!-- Score Selector -->
                                <div class="paper-score-pill flex items-center space-x-1 px-2.5 py-1 rounded-xl">
                                    <span class="text-xs font-medium">分值:</span>
                                    <input type="number" min="1" max="100" value="${currentScore}" 
                                        onchange="window.updatePaperQuestionScore(${q.id}, this.value)"
                                        class="w-12 text-center text-xs font-bold rounded-lg focus:outline-none">
                                    <span class="text-xs font-medium">分</span>
                                </div>

                                <!-- Move Up / Move Down -->
                                ${currentTab === 'selected' ? `
                                    <button onclick="window.movePaperQuestion(${index}, 'up')" ${index === 0 ? 'disabled' : ''} 
                                        class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="上移">
                                        <i class="fa-solid fa-arrow-up text-xs"></i>
                                    </button>
                                    <button onclick="window.movePaperQuestion(${index}, 'down')" ${index === displayList.length - 1 ? 'disabled' : ''} 
                                        class="p-1.5 rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800 disabled:opacity-30 dark:hover:bg-slate-700" title="下移">
                                        <i class="fa-solid fa-arrow-down text-xs"></i>
                                    </button>
                                ` : ''}

                                <!-- Remove Button -->
                                <button onclick="window.removeFromCart(${q.id})" 
                                    class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-emerald-500 text-white shadow-sm hover:bg-rose-600 active:scale-95 transition-all flex items-center space-x-1" title="点击移出试卷">
                                    <i class="fa-solid fa-check text-xs"></i>
                                    <span>已入卷</span>
                                </button>
                            ` : `
                                <!-- Add Button -->
                                <button onclick="window.addToCart(${q.id})" 
                                    class="px-3 py-1.5 rounded-xl text-xs font-semibold bg-brand-600 text-white shadow-sm hover:bg-brand-700 active:scale-95 transition-all flex items-center space-x-1">
                                    <i class="fa-solid fa-plus text-xs"></i>
                                    <span>加入试卷</span>
                                </button>
                            `}
                        </div>
                    </div>

                    <!-- Full Question Render Content -->
                    <div class="question-full-render-box text-sm leading-relaxed text-slate-800 dark:text-slate-100 overflow-x-auto select-text" id="paper-q-render-${q.id}">
                        ${formatQuestionContentHtml(q.content, q.id, getQuestionFigAlign(q), false, false)}
                    </div>

                    ${answerExpanded ? `
                        <section id="paper-q-answer-${q.id}"
                            role="region"
                            aria-label="题目 #${escapeHtml(q.seq_num !== undefined ? q.seq_num : q.id)} 的参考答案与解析"
                            class="mt-4 pt-4 border-t border-dashed border-brand-200/80 dark:border-brand-900/70">
                            <div class="mb-2 flex items-center gap-2 text-xs font-bold text-brand-700 dark:text-brand-200">
                                <i class="fa-solid fa-signature"></i>
                                <span>参考答案与解析</span>
                            </div>
                            <div class="paper-answer-render-box rounded-xl border border-brand-100 bg-brand-50/40 px-4 py-3 text-sm leading-relaxed text-slate-800 overflow-x-auto select-text dark:border-brand-900/60 dark:bg-brand-900/20 dark:text-slate-100">
                                <div id="paper-q-answer-content-${q.id}">${answerBodyHtml}</div>
                            </div>
                        </section>
                    ` : ''}
                </div>
            `;
        });

        html += `</div>`;
        container.innerHTML = html;

        // Render math formulas for Part 3 question cards
        //
        // 这里的两种渲染必须拆开走（2026-09-25 性能修复，方案 2）：
        //   · KaTeX 只能**逐卡**调 —— renderMathInElement 只处理传入子树，无法
        //     一次覆盖 349 个彼此独立的容器。
        //   · 选项列数自适应恰恰相反 —— adaptChoicesGridLayout(root) 本就支持传
        //     任意 root，内部自己做 querySelectorAll('.choices-grid') 收全部。
        //     原先放在循环里逐卡调用，等于把同一份查询重复 349 遍：在这个 10.8
        //     万节点的文档上，实测 350 次 querySelectorAll 纯属浪费。故统一挪到
        //     循环外，对整个 container 只算一次（见本段结尾）。
        displayList.forEach(q => {
            const el = document.getElementById(`paper-q-render-${q.id}`);
            if (el && typeof renderMathInElement === 'function') {
                try {
                    renderMathInElement(el, {
                        delimiters: [
                            { left: '$$', right: '$$', display: true },
                            { left: '$', right: '$', display: false },
                            { left: '\\(', right: '\\)', display: false },
                            { left: '\\[', right: '\\]', display: true }
                        ],
                        throwOnError: false
                    });
                } catch (e) { }
            }

            const answerEl = document.getElementById(`paper-q-answer-content-${q.id}`);
            if (answerEl && !window.PaperStore.answerLoadingIds.has(q.id) && !window.PaperStore.answerErrors[q.id] && typeof renderMathInElement === 'function') {
                try {
                    renderMathInElement(answerEl, {
                        delimiters: [
                            { left: '$$', right: '$$', display: true },
                            { left: '$', right: '$', display: false },
                            { left: '\\(', right: '\\)', display: false },
                            { left: '\\[', right: '\\]', display: true }
                        ],
                        throwOnError: false
                    });
                } catch (e) { }
            }
        });

        // Part 3 选项列数自适应：整个题目流一次算完（理由见本段开头注释）。
        // 放在全部 KaTeX 渲染之后，保证自适应读到的是最终布局。
        if (typeof window.adaptChoicesGridLayout === 'function') {
            try {
                window.adaptChoicesGridLayout(container);
            } catch (e) { }
        }
    }

    // Render Part 4: Right A4 Canvas & Action Bar
    window.renderPaperCanvas = function () {
        const container = document.getElementById('paperCanvasSection');
        if (!container) return;

        // 保存更新前的 A4 画布与外层 Section 滚动位置，解决重绘导致的视口跳回第一页问题
        const oldSheet = document.getElementById('a4PaperPreviewSheet');
        const savedSheetScrollTop = oldSheet ? oldSheet.scrollTop : 0;
        const savedContainerScrollTop = container ? container.scrollTop : 0;

        const cart = window.PaperStore.cart;
        const meta = window.PaperStore.meta;

        const validCartStats = cart.filter(item => {
            const q = window.PaperStore.questionsMap[item.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const totalScore = validCartStats.reduce((sum, item) => sum + (parseInt(item.score, 10) || 5), 0);
        const totalCount = validCartStats.length;

        // Calculate difficulty ratio
        let easyCount = 0, medCount = 0, hardCount = 0;
        validCartStats.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (q) {
                if (q.difficulty === 'basic' || q.difficulty === 'medium') easyCount++;
                else if (q.difficulty === 'hard' || q.difficulty === 'novel') hardCount++;
                else medCount++;
            }
        });
        const easyPct = totalCount > 0 ? Math.round((easyCount / totalCount) * 100) : 0;
        const medPct = totalCount > 0 ? Math.round((medCount / totalCount) * 100) : 0;
        const hardPct = totalCount > 0 ? Math.max(0, 100 - easyPct - medPct) : 0;

        let aiAnalysisBanner = '';
        if (meta.ai_analysis) {
            aiAnalysisBanner = `
                <div class="mb-4 p-3.5 rounded-2xl border border-brand-200/80 bg-brand-50/50 backdrop-blur-md shadow-sm dark:bg-brand-900/40 dark:border-brand-900 transition-all">
                    <div class="flex items-center justify-between mb-1.5 pb-1 border-b border-brand-200/50 dark:border-brand-900/60">
                        <div class="flex items-center space-x-1.5 text-xs font-bold text-brand-700 dark:text-brand-200">
                            <i class="fa-solid fa-brain text-brand-500"></i>
                            <span>双向细目表与考点覆盖分析 (${escapeHtml(meta.ai_model_used || '大模型')})</span>
                        </div>
                        <button onclick="window.clearAiAnalysis()" class="text-[10px] text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 px-1.5 py-0.5 rounded-md hover:bg-slate-200/60 font-medium transition-all" title="关闭分析框">
                            <i class="fa-solid fa-xmark mr-1"></i>关闭分析
                        </button>
                    </div>
                    <div class="text-xs text-slate-700 dark:text-slate-300 whitespace-pre-wrap leading-relaxed">
                        ${escapeHtml(meta.ai_analysis)}
                    </div>
                </div>
            `;
        }

        container.innerHTML = `
            ${aiAnalysisBanner}
            <!-- Part 1: Top Fixed Control Section (Non-scrolling Studio Panel) -->
            <div class="shrink-0 mb-3">
                <div class="bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-3 rounded-2xl flex flex-col space-y-2.5 shadow-sm">
                    <!-- Row 1: Header Stats & Solution Space Config -->
                    <div class="flex items-center justify-between flex-wrap gap-2 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        <div class="flex items-center space-x-3">
                            <div class="flex items-center space-x-1.5 px-3 py-1 rounded-xl bg-brand-50 text-brand-700 font-bold text-xs border border-brand-200/60 dark:bg-brand-900/40 dark:text-brand-200 dark:border-brand-900">
                                <span>总分: ${totalScore} 分</span>
                                <span class="text-slate-400 font-normal">|</span>
                                <span>${totalCount} 题</span>
                            </div>
                            <!-- Difficulty ratio bar -->
                            <div class="hidden xl:flex items-center space-x-1.5 text-xs">
                                <span class="text-slate-400 font-medium">难度比:</span>
                                <div class="w-20 h-2 rounded-full bg-slate-200 overflow-hidden flex dark:bg-slate-700" title="普通题: ${easyPct}% | 挑战题: ${medPct}% | 强基题: ${hardPct}%">
                                    <div class="bg-emerald-500 h-full" style="width: ${easyPct}%"></div>
                                    <div class="bg-amber-500 h-full" style="width: ${medPct}%"></div>
                                    <div class="bg-rose-500 h-full" style="width: ${hardPct}%"></div>
                                </div>
                            </div>
                            <!-- Solution Space Selector -->
                            <div class="flex items-center space-x-1 text-xs">
                                <span class="text-slate-500 font-semibold dark:text-slate-300 flex items-center space-x-1">
                                    <i class="fa-solid fa-arrows-up-down text-brand-500"></i>
                                    <span>留白:</span>
                                </span>
                                <select onchange="window.updateGlobalSolutionSpace(this.value)"
                                    class="px-2 py-1 text-xs rounded-xl border border-brand-200/80 bg-brand-50/60 text-brand-900 font-bold focus:ring-2 focus:ring-brand-500 focus:outline-none dark:bg-brand-900/50 dark:border-brand-900 dark:text-brand-200">
                                    ${meta.paper_type === 'exam_19' ? `
                                        <option value="0.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '0.0') === 0.0) ? 'selected' : ''}>0 cm (不留白)</option>
                                        <option value="3.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '0.0') === 3.0) ? 'selected' : ''}>3 cm (紧凑留白)</option>
                                    ` : `
                                        <option value="0.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '7.0') === 0.0) ? 'selected' : ''}>0 cm (不留白)</option>
                                        <option value="7.0" ${(parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : '7.0') === 7.0) ? 'selected' : ''}>7 cm (标准留白)</option>
                                    `}
                                </select>
                            </div>
                        </div>
                        <!-- 画布缩放（待办 #18）：只缩页面盒子，页内仍按真 A4 像素排版 -->
                        <div class="flex items-center space-x-1.5 text-[10px] font-sans select-none">
                            <button type="button" data-paper-zoom-mode="fit" onclick="window.setPaperZoomFit()"
                                    class="paper-zoom-btn px-2 py-0.5 rounded-lg" title="按画布宽度自动缩放整卷">适应宽度</button>
                            <button type="button" data-paper-zoom-mode="manual" onclick="window.setPaperZoomActual()"
                                    class="paper-zoom-btn px-2 py-0.5 rounded-lg" title="按 100% 实际尺寸显示（A4 = 794px 宽）">100%</button>
                            <div class="flex items-center space-x-1 ml-1">
                                <button type="button" onclick="window.stepPaperZoom(-0.1)" class="paper-zoom-btn px-1.5 py-0.5 rounded-lg" title="缩小画布">
                                    <i class="fa-solid fa-minus text-[9px]"></i>
                                </button>
                                <span id="paperZoomReadout" class="paper-zoom-readout font-bold text-slate-600">100%</span>
                                <button type="button" onclick="window.stepPaperZoom(0.1)" class="paper-zoom-btn px-1.5 py-0.5 rounded-lg" title="放大画布">
                                    <i class="fa-solid fa-plus text-[9px]"></i>
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- Row 2: Paper Management Actions (Clear, Save, History) -->
                    <div class="flex items-center justify-between gap-2.5 pb-2 border-b border-slate-100 dark:border-slate-800/60">
                        ${totalCount > 0 ? `
                            <button onclick="clearCart()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-rose-50/80 text-rose-600 border border-rose-200/70 hover:bg-rose-100/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-rose-950/40 dark:border-rose-800/60 dark:text-rose-300" title="清空当前试卷已选题目">
                                <i class="fa-solid fa-trash-can"></i>
                                <span>清空卷面</span>
                            </button>
                        ` : ''}
                        <button onclick="savePaperToDb()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap" title="保存当前试卷至本地数据库">
                            <i class="fa-solid fa-floppy-disk"></i>
                            <span>保存试卷</span>
                        </button>
                        <button onclick="openSavedPapersModal()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-amber-500 text-white shadow-sm hover:bg-amber-600 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap" title="查阅与管理历史归档试卷">
                            <i class="fa-solid fa-folder-open"></i>
                            <span>历史试卷库</span>
                        </button>
                    </div>

                    <!-- Row 3: Preview & Export Options -->
                    <div class="flex flex-wrap items-center justify-between gap-2 sm:gap-2.5">
                        ${meta.paper_type === 'exam_19' ? `
                            <button onclick="exportPaperPdf('paper')" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开试卷 PDF 预览">
                                <i class="fa-solid fa-file-pdf"></i>
                                <span>试卷 PDF 预览</span>
                            </button>
                            <button onclick="exportPaperPdf('sheet')" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开 A3 双面答题卡 PDF 预览">
                                <i class="fa-solid fa-file-lines"></i>
                                <span>答题卡 PDF 预览</span>
                            </button>
                            <button onclick="exportPaperWord()" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="导出可编辑 Word 试卷正文（不含答题卡）">
                                <i class="fa-solid fa-file-word"></i>
                                <span>Word 导出</span>
                            </button>
                            <button onclick="exportPaperBundle()" class="flex-1 px-2.5 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="打包导出 LaTeX 源码、插图及编译好的 PDF 全套文件">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>LaTeX 打包</span>
                            </button>
                        ` : `
                            <button onclick="exportPaperPdf('paper')" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="编译并打开试卷 PDF 预览">
                                <i class="fa-solid fa-file-pdf"></i>
                                <span>PDF 预览</span>
                            </button>
                            <button onclick="exportPaperWord()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="导出保留原生公式的可编辑 Word 试卷">
                                <i class="fa-solid fa-file-word"></i>
                                <span>Word 导出</span>
                            </button>
                            <button onclick="exportPaperBundle()" class="flex-1 py-1.5 justify-center rounded-xl text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200 hover:bg-slate-200/80 active:scale-95 transition-all flex items-center space-x-1.5 whitespace-nowrap dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700 dark:hover:bg-slate-700" title="打包导出 LaTeX 源码、插图及编译好的 PDF 全套文件">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>LaTeX 打包</span>
                            </button>
                        `}
                    </div>
                </div>
            </div>

            <!-- Part 2: Independent Scrollable A4 Desk Canvas Paper Container -->
            <div class="flex-1 overflow-y-auto overflow-x-auto custom-scrollbar pt-1 pb-10 flex flex-col items-start" id="a4PaperPreviewSheet">
                <div class="paper-zoom-stage" id="paperZoomStage">
                    ${generateA4PaperPagesHtml(cart, meta, totalCount, totalScore)}
                </div>
            </div>
        `;

        // 先定缩放倍率（适应宽度依赖滚动容器的 clientWidth，innerHTML 后即可取到）
        window.applyPaperZoom();

        // Render math in A4 sheet
        const sheet = document.getElementById('a4PaperPreviewSheet');
        if (sheet && typeof renderMathInElement === 'function') {
            try {
                renderMathInElement(sheet, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false },
                        { left: '\\(', right: '\\)', display: false },
                        { left: '\\[', right: '\\]', display: true }
                    ],
                    throwOnError: false
                });
                if (typeof window.adaptChoicesGridLayout === 'function') {
                    window.adaptChoicesGridLayout(sheet);
                }
            } catch (e) { }
        }

        // 恢复更新前的滚动位置，保证调排版/留白/格式时在原视口位置零跳跃渲染
        const restoreScroll = () => {
            const newSheet = document.getElementById('a4PaperPreviewSheet');
            if (newSheet && savedSheetScrollTop > 0) {
                newSheet.scrollTop = savedSheetScrollTop;
            }
            const curContainer = document.getElementById('paperCanvasSection');
            if (curContainer && savedContainerScrollTop > 0) {
                curContainer.scrollTop = savedContainerScrollTop;
            }
        };

        restoreScroll();
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(restoreScroll);
        }
    };

    // ---- A4 画布几何（待办 #18 · 方案 A-lite：真 A4 盒子 + 等比缩放）----
    // 口径与导出侧 exam-zh 一致：96dpi 下 A4 = 794×1123px、geometry margin=1in=96px、
    // ctex 五号正文 10.5bp≈14px、行距 12.6bp（= 字号 × 1.2）、1cm=37.8px。
    // 页面盒子在 app.css 的 .a4-paper-sheet 里按同一组数书写，分页估算也吃这一份常量，
    // 两边不再各写一套数字。
    const A4_PAGE = {
        widthPx: 794,
        heightPx: 1123,
        marginPx: 96,
        bodyPx: 14,
        linePx: 17,          // 14×1.2=16.8 → 取 17：估算宁可就高，撑破页面比页底少排一行更糟
        mathLinePx: 21,      // 行内含 KaTeX 的行盒：实测 20~21px
        fracLinePx: 24,      // 行内含 \frac / \sqrt 的行盒：实测 23~24px
        bigMathLinePx: 34,   // 行内含 \dfrac / cases / array 的行盒：实测 34~35px
        textPx: 602,         // 版心宽 = 794 - 96×2
        cmPx: 37.8,          // 原留白换算用的是 35px/cm，比真 A4 少 7.4%
        cjkPerLine: 40,      // 版心 602px ÷ 14px ≈ 43 个汉字；按 40 折算留出公式余量
        itemGapPx: 11,       // 题与题之间 0.75em
        choiceColGapPx: 14,  // .choices-grid 列间距 1em
        choiceGridPadPx: 14, // 选项区上下 0.5em×2
        choiceRowGapPx: 7,   // 选项折成多行时的行间距
        choiceLabelPx: 26,   // 选项标号「A．」的印刷宽
        figurePx: 170,       // 行内插图无显式上限时按典型高度计（上限 180 + my-2.5 的七折）
        figureMaxPx: 180,    // 渲染器给插图写的 max-h-[180px]
        figureMarginPx: 20,  // 插图外层 div.my-2.5（上下各 10px）
        tableRowPx: 30       // tabular 每行：实测两行表 59px
    };
    A4_PAGE.contentPx = A4_PAGE.heightPx - A4_PAGE.marginPx * 2;   // 931px：一页正文可用高度

// ==== 新版 A4 画布估高模型（替换 paper.js 里的旧实现）====
    const PAPER_WIDE_CHAR_RE = /[\u1100-\u115F\u2E80-\u33FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6]/;
    // 数学段：$$…$$ / $…$ / \(…\) / \[…\]
    const PAPER_MATH_SEGMENT_RE = /\$\$[\s\S]+?\$\$|\$[^$]+\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]/g;
    // 插图三种写法（Markdown / \includegraphics / 裸 <img>）：不占行宽，高度另算
    const PAPER_FIGURE_TOKEN_RE = /!\[[^\]]*\]\([^)]*\)|\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}|<img[^>]*>/gi;
    // 半角单位宽（1 单位 = bodyPx / 2 ≈ 7px）
    const PAPER_UNIT_PX = 7;
    // 数学段放大系数：KaTeX 自带算符间距、数学斜体比源码宽。无头浏览器在真 A4 版心上
    // 对 86 个内联式实测「预测/真实」中位数 0.77，对 51 个选项单元（含 A. 标号）实测
    // 中位数 1.05 —— 取 1.3 让估高落在安全侧（宁可不排满，也不能把页面撑破）。
    const PAPER_MATH_WIDTH_SCALE = 1.3;
    // \command → 半角单位宽（关系符/二元运算符含 KaTeX 前后空白）
    const PAPER_TEX_OP_UNITS = {
        in: 1.8, notin: 2.4, neq: 2.1, ne: 2.1, leq: 1.7, le: 1.7, geq: 1.7, ge: 1.7,
        lt: 1.24, gt: 1.24, equiv: 2, approx: 2, sim: 1.8, simeq: 2, cong: 2.2,
        subset: 2, subseteq: 2.6, subsetneq: 2.6, supset: 2, supseteq: 2.6,
        forall: 2.5, exists: 2.7, cup: 1.6, cap: 1.6, setminus: 2, wedge: 1.6, vee: 1.6,
        land: 1.6, lor: 1.6, lnot: 1.4, neg: 1.4, infty: 2, cdot: 1.1, times: 1.7,
        div: 1.7, pm: 1.8, mp: 1.8, ast: 1.2, star: 1.4, circ: 1.2, bullet: 1.2,
        to: 2.2, rightarrow: 2.8, Rightarrow: 3.2, leftarrow: 2.8, Leftrightarrow: 3.6,
        mapsto: 2.6, implies: 3,iff: 2.4, perp: 1.8, parallel: 2, mid: 2.2, nmid: 2.6,
        triangleright: 2, triangleleft: 2, triangle: 2, square: 2, diamond: 1.8,
        ldots: 2.4, cdots: 2.4, dots: 2.4, vdots: 1, ddots: 1.6,
        varnothing: 2.4, emptyset: 2.4, partial: 1.8, nabla: 1.8, angle: 1.8,
        sum: 2.4, prod: 2.4, int: 2, iint: 2.6, iiint: 3.2, oint: 2.4, lim: 1.8,
        sin: 2.2, cos: 2.2, tan: 2.2, cot: 2.2, sec: 2.2, csc: 2.2, log: 2.2, ln: 1.8,
        exp: 2, lg: 1.8, min: 1.8, max: 1.8, deg: 2.2, gcd: 2.2, arcsin: 3.4, arccos: 3.6,
        alpha: 1.2, beta: 1.3, gamma: 1.3, delta: 1.4, epsilon: 1.2, varepsilon: 1.2,
        zeta: 1.1, eta: 1.2, theta: 1.2, vartheta: 1.3, iota: 0.9, kappa: 1.2, mu: 1.2,
        nu: 1.1, xi: 1.3, pi: 1.4, rho: 1.2, sigma: 1.4, tau: 1.2, upsilon: 1.4,
        phi: 1.4, varphi: 1.6, chi: 1.3, psi: 1.6, omega: 1.6,
        Gamma: 2, Delta: 2, Theta: 2.2, Lambda: 2.2, Xi: 1.8, Pi: 2.2, Sigma: 2,
        Phi: 2, Upsilon: 2.4, Psi: 2.2, Omega: 2.4,
        bmod: 1.6, pmod: 2.2, mod: 2, colon: 1, greaterdot: 1.4,
        // \paren 是「选作答括号」宏：源码 6 字符，纸面是一对空括号约 56px
        paren: 8, cead: 0, chead: 0,
        langle: 1.4, rangle: 1.4, lvert: 0.8, rvert: 0.8, lVert: 1.2, rVert: 1.2,
        backslash: 1.2, setminus2: 2
    };
    // 只包内容、自身几乎不占宽的命令
    const PAPER_TEX_WRAPPERS = {
        mathrm: 0, mathbf: 0, mathbb: 0.4, mathit: 0, mathsf: 0, mathtt: 0, boldsymbol: 0,
        operatorname: 0, text: 0, textrm: 0, textbf: 0, textit: 0, mbox: 0, textnormal: 0,
        underlined: 0, colorbox: 0
    };
    // 自身要额外占宽的带参命令
    const PAPER_TEX_ACCENTS = {
        hat: 0.5, widehat: 0.9, bar: 0.5, overline: 0.5, underline: 0.5, vec: 0.5,
        dot: 0.4, ddot: 0.6, tilde: 0.5, widetilde: 0.9, overrightarrow: 0.6,
        stackrel: 0.8, overset: 0.8, underset: 0.8
    };
    const PAPER_TEX_SPACERS = {
        ",": 0.34, ";": 0.56, ":": 0.56, "!": -0.34, quad: 4, qquad: 8,
        thinspace: 0.4, medspace: 0.8, thickspace: 1, " ": 0.25
    };
    // 括号/成对定界符本身
    const PAPER_TEX_DELIMS = { "left": 0.9, "right": 0.9, "bigl": 0.7, "bigr": 0.7, "Bigl": 1, "Bigr": 1, "big": 0.7, "Big": 1, "bigg": 1.2 };

    function paperWidthUnits(text) {
        let units = 0;
        const source = String(text || "");
        for (let i = 0; i < source.length; i++) {
            units += PAPER_WIDE_CHAR_RE.test(source[i]) ? 2 : 1;
        }
        return units;
    }

    // 读一个 LaTeX 组：返回 [组内文本, 组之后的下标]
    function paperReadTexGroup(source, from) {
        if (source[from] !== "{") return ["", from];
        let depth = 0;
        for (let i = from; i < source.length; i++) {
            if (source[i] === "{") depth += 1;
            else if (source[i] === "}") {
                depth -= 1;
                if (!depth) return [source.slice(from + 1, i), i + 1];
            }
        }
        return [source.slice(from + 1), source.length];
    }

    // 源码 → 半角单位宽。只处理题目里真会出现的 LaTeX 子集：分式取分子/分母较宽的一侧，
    // 上下标按 0.5 折算（角标字号小），未收录的命令按 0.9 兜底。
    function paperTexHalfUnits(tex, depth) {
        const level = depth || 0;
        if (level > 8) return 0;
        const source = String(tex || "");
        let total = 0;
        let i = 0;
        while (i < source.length) {
            const ch = source[i];
            if (ch === "\\") {
                const matched = /^\\([a-zA-Z]+|.)/.exec(source.slice(i));
                const name = matched[1];
                i += matched[0].length;
                if (name === "frac" || name === "dfrac" || name === "tfrac" || name === "cfrac") {
                    const num = paperReadTexGroup(source, i); i = num[1];
                    const den = paperReadTexGroup(source, i); i = den[1];
                    total += Math.max(paperTexHalfUnits(num[0], level + 1), paperTexHalfUnits(den[0], level + 1)) + 0.7;
                    if (name === "dfrac") total += 0.5;
                } else if (name === "sqrt") {
                    if (source[i] === "[") {
                        const close = source.indexOf("]", i);
                        i = close < 0 ? i + 1 : close + 1;
                    }
                    if (source[i] === "{") {
                        const body = paperReadTexGroup(source, i);
                        total += paperTexHalfUnits(body[0], level + 1) + 0.9;
                        i = body[1];
                    } else {
                        total += paperTexHalfUnits(source.slice(i, i + 1), level + 1) + 0.9;
                        i += 1;
                    }
                } else if (name === "begin" || name === "end") {
                    const body = paperReadTexGroup(source, i);
                    i = body[1];
                    if (body[0] === "cases" || body[0] === "array" || body[0] === "aligned" || body[0] === "matrix") total += 1.6;
                } else if (PAPER_TEX_ACCENTS[name] !== undefined) {
                    const body = paperReadTexGroup(source, i);
                    total += paperTexHalfUnits(body[0], level + 1) + PAPER_TEX_ACCENTS[name];
                    i = body[1];
                } else if (PAPER_TEX_WRAPPERS[name] !== undefined) {
                    const body = paperReadTexGroup(source, i);
                    total += paperTexHalfUnits(body[0], level + 1) + PAPER_TEX_WRAPPERS[name];
                    i = body[1];
                } else if (PAPER_TEX_DELIMS[name] !== undefined) {
                    total += PAPER_TEX_DELIMS[name];
                    if (name === "left" || name === "right") {
                        const next = source.slice(i, i + 2);
                        if (next === "\\{") i += 2;
                        else if (next === "\\}") i += 2;
                        else i += 1;
                    }
                } else if (PAPER_TEX_SPACERS[name] !== undefined) {
                    total += PAPER_TEX_SPACERS[name];
                } else if (PAPER_TEX_OP_UNITS[name] !== undefined) {
                    total += PAPER_TEX_OP_UNITS[name];
                } else if (name === "_" || name === "^" || name === "\\" || name === "item") {
                    // 换行/标号不占行宽
                } else {
                    total += 0.9;
                }
                continue;
            }
            if (ch === "{" || ch === "}" || ch === "$") { i += 1; continue; }
            if (ch === "_" || ch === "^") {
                i += 1;
                if (source[i] === "{") {
                    const body = paperReadTexGroup(source, i);
                    total += paperTexHalfUnits(body[0], level + 1) * 0.5;
                    i = body[1];
                } else {
                    total += 0.3;
                    i += 1;
                }
                continue;
            }
            if (PAPER_WIDE_CHAR_RE.test(ch)) { total += 2; i += 1; continue; }
            if (/\s/.test(ch)) { i += 1; continue; }
            if ("+-=<>".indexOf(ch) >= 0) { total += 1.9; i += 1; continue; }   // 关系/二元符带前后空白
            if ("|.,;:!'\"".indexOf(ch) >= 0) { total += 0.64; i += 1; continue; }
            if ("()[]".indexOf(ch) >= 0) { total += 0.84; i += 1; continue; }
            if (/[0-9]/.test(ch)) { total += 1; i += 1; continue; }
            if (/[a-zA-Z]/.test(ch)) { total += 1.04; i += 1; continue; }       // 数学斜体略宽于等宽数字
            total += 1;
            i += 1;
        }
        return total;
    }

    // 只留「会排进正文的东西」：插图另有高度、表格按行计高，环境名/线标记不占宽。
    // ⚠️ 不用后行断言（AGENTS.md 禁令），一律整段匹配。
    function stripPaperEstimateNoise(text) {
        return String(text || "")
            .replace(PAPER_FIGURE_TOKEN_RE, "")
            .replace(/\\begin\{tabular\}(?:\{[^}]*\})?[\s\S]*?\\end\{tabular\}/g, "")
            .replace(/\\begin\{[^{}]*\}|\\end\{[^{}]*\}/g, "")
            .replace(/\\hline|\\cline\{[^}]*\}/g, "")
            .replace(/\\(?:centering|raggedright|noindent)/g, "")
            .replace(/\\(?:label|tag|nonumber|displaystyle|scriptstyle|textstyle|limits|nolimits)/g, "");
    }

    // 剥掉数学段的外层定界符，只留公式体
    function paperMathBody(segment) {
        let body = String(segment);
        if (body.indexOf("$$") === 0) return body.slice(2, body.length - 2);
        if (body.indexOf("\\[") === 0) return body.slice(2, body.length - 2);
        if (body.indexOf("\\(") === 0) return body.slice(2, body.length - 2);
        if (body.charAt(0) === "$") return body.slice(1, body.length - 1);
        return body;
    }

    // 一段混排文本的印刷宽度（px）：文字按字符宽累加，数学段额外放大
    function paperInlineWidthPx(text) {
        const source = stripPaperEstimateNoise(text);
        const scanner = new RegExp(PAPER_MATH_SEGMENT_RE.source, "g");
        let px = 0;
        let cursor = 0;
        let matched;
        while ((matched = scanner.exec(source))) {
            px += paperTexHalfUnits(source.slice(cursor, matched.index)) * PAPER_UNIT_PX;
            px += paperTexHalfUnits(paperMathBody(matched[0])) * PAPER_UNIT_PX * PAPER_MATH_WIDTH_SCALE;
            cursor = matched.index + matched[0].length;
        }
        px += paperTexHalfUnits(source.slice(cursor)) * PAPER_UNIT_PX;
        return px;
    }

    // 行盒高度：纯文字 17px；含 KaTeX 的实测 20~21px；含 \frac/\sqrt 23~24px；
    // 含 \dfrac / cases / array 这类真正拉高的式子 34~35px。
    function paperBlockLinePx(text) {
        const source = String(text || "");
        if (/\\dfrac|\\begin\{(?:cases|array|aligned|matrix)\}|\\substack/.test(source)) return A4_PAGE.bigMathLinePx;
        if (/\\(?:frac|tfrac|sqrt|stackrel)\{/.test(source)) return A4_PAGE.fracLinePx;
        // 只要这行里真出现了公式就按 mathLinePx：像 $[0,2)$ 这种不带命令的区间式，
        // KaTeX 仍把行盒撑到 20px（实测），只查 \\command 会漏判成 17px。
        if (/\\[a-zA-Z]+|\$[^$]+\$|\\\(|\\\[/.test(source)) return A4_PAGE.mathLinePx;
        return A4_PAGE.linePx;
    }

    function paperFigureCount(text) {
        return (String(text || "").match(PAPER_FIGURE_TOKEN_RE) || []).length;
    }

    // 插图块高：显式写了 max height=Xcm 的按 X×37.8 折（再与渲染上限 180 取小），
    // 没写的按典型高度 figurePx 计。一律取上限会让宽幅函数图象白占半页，首页排不满。
    function paperFigureHeightPx(source) {
        const tokens = String(source || "").match(PAPER_FIGURE_TOKEN_RE) || [];
        let px = 0;
        tokens.forEach(function (token) {
            const capped = token.match(/max\s+height\s*=\s*([0-9.]+)\s*cm/i);
            if (capped) {
                const cm = parseFloat(capped[1]);
                px += Math.min(A4_PAGE.figureMaxPx, Math.round(cm * A4_PAGE.cmPx)) + A4_PAGE.figureMarginPx;
            } else {
                px += A4_PAGE.figurePx;
            }
        });
        return px;
    }

    // tabular 行数：每个 \\ 结束一行，另加末行
    function paperTableRowCount(text) {
        const source = String(text || "");
        const scanner = /\\begin\{tabular\}(?:\{[^}]*\})?([\s\S]*?)\\end\{tabular\}/g;
        let rows = 0;
        let matched;
        while ((matched = scanner.exec(source))) {
            // 末行 \ 之后只剩 \hline，那不算一行：拆段后只数有内容的段
            const body = matched[1].replace(/\\hline|\\cline\{[^}]*\}/g, " ");
            const segments = body.split("\\\\").filter(function (row) { return row.trim(); });
            rows += Math.max(1, segments.length);
        }
        return rows;
    }

    // 文字块高度：段落按行折，段与段之间的空行（预览里渲染成 <br><br>）实测多占一个行高。
    // widthRatio 用于「右侧浮动图把文字压窄」的情形。
    function paperTextHeightPx(rawText, widthRatio) {
        const ratio = widthRatio && widthRatio > 0 && widthRatio <= 1 ? widthRatio : 1;
        const capacity = A4_PAGE.textPx * ratio;
        const blocks = String(rawText || "").replace(/\r\n?/g, "\n").split(/\n{2,}/);
        let height = 0;
        let filledBlocks = 0;
        blocks.forEach(function (block) {
            const oneLine = block.replace(/\n+/g, " ");
            if (!stripPaperEstimateNoise(oneLine).trim()) return;
            if (filledBlocks) height += A4_PAGE.linePx;   // 段间空行
            const lines = Math.max(1, Math.ceil(paperInlineWidthPx(oneLine) / capacity));
            height += lines * paperBlockLinePx(oneLine);
            filledBlocks += 1;
        });
        return height;
    }

    // 兼容旧口径的「折行数」：卷首语/大题标题这类纯文字够用
    function paperLineCount(rawText, widthRatio) {
        const ratio = widthRatio && widthRatio > 0 && widthRatio <= 1 ? widthRatio : 1;
        const capacity = A4_PAGE.textPx * ratio;
        const source = String(rawText || "").replace(/\r\n?/g, "\n");
        let lines = 0;
        source.split("\n").forEach(function (line) {
            const clean = stripPaperEstimateNoise(line).replace(/\s+/g, " ").trim();
            if (!clean) return;
            lines += Math.max(1, Math.ceil(paperInlineWidthPx(clean) / capacity));
        });
        return lines;
    }

    // 选项区高度：先按「4 → 2 → 1 折半列」挑列数（与编辑器 adaptChoicesGridLayout 的
    // 实测规则同口径），再按列宽折行、取本组最高的一档行高。
    function paperChoiceCellWidthPx(columns) {
        return (A4_PAGE.textPx - (columns - 1) * A4_PAGE.choiceColGapPx) / columns;
    }

    function paperChoicesHeightPx(items) {
        const cells = (items || []).map(function (rawItem) {
            const text = String(rawItem || "");
            const figurePx = paperFigureHeightPx(text);
            const widthPx = paperInlineWidthPx(text) + A4_PAGE.choiceLabelPx;
            // 选项图受格子宽收口（.choices-grid img { max-width: 100% }），
            // 宽度不撑破 4 列，但行高要按图块算
            return {
                widthPx: widthPx,
                linePx: figurePx ? figurePx : paperBlockLinePx(text)
            };
        });
        if (!cells.length) return 0;

        let widest = 0;
        cells.forEach(function (cell) { widest = Math.max(widest, cell.widthPx); });
        let columns = 4;
        while (columns > 1 && widest > paperChoiceCellWidthPx(columns)) columns /= 2;
        const cellWidth = paperChoiceCellWidthPx(columns);

        // 行优先填充：各项折行数累加后摊回行数；行高取最高的一档（含图/含分式的行更挤）
        let visualRows = 0;
        let rowLinePx = A4_PAGE.linePx;
        cells.forEach(function (cell) {
            visualRows += Math.max(1, Math.ceil(cell.widthPx / cellWidth));
            rowLinePx = Math.max(rowLinePx, cell.linePx);
        });
        const rows = Math.max(1, Math.ceil(visualRows / columns));
        return rows * rowLinePx
            + Math.max(0, rows - 1) * A4_PAGE.choiceRowGapPx
            + A4_PAGE.choiceGridPadPx;
    }

    // 单题估高：题干（折行 × 行盒）+ 选项区 + 表格 + 插图 + 解答题留白 + 题间距。
    // 所有常数都来自无头浏览器在真 A4 版心上的实测（见 tests/test_frontend_usability.py 的守卫）。
    function estimateQuestionBlockHeight(qType, rawContent, solSpaceCm, figAlign, imgCount) {
        const source = String(rawContent || "").replace(/\r\n?/g, "\n");
        let stemSource = source;
        let choicesPx = 0;
        const choicesMatch = source.match(/\\begin\{choices\}([\s\S]*?)\\end\{choices\}/);
        if (choicesMatch) {
            stemSource = source.replace(choicesMatch[0], "");
            const items = choicesMatch[1].split("\\item").slice(1);
            if (items.length) choicesPx = paperChoicesHeightPx(items);
        }

        // 右栏浮图会把文字压到 calc(100% - 170px)，按 74% 行宽折行
        const floatRight = imgCount > 0 && (figAlign || "right") === "right";
        let heightPx = paperTextHeightPx(stemSource, floatRight ? 0.74 : 1)
            + choicesPx
            + paperTableRowCount(stemSource) * A4_PAGE.tableRowPx;

        // 正文里的插图（\includegraphics / <img> / 未 detach 的 Markdown 图）：
        // 预览里它们是独立块（div.my-2.5 + img），必须整块计高，不能只算一行文字
        if (imgCount === 0) heightPx += paperFigureHeightPx(stemSource);

        if (imgCount > 0) {
            // 上限与 formatQuestionContentHtml 里的 max-h 保持一致
            const imgPx = floatRight ? (imgCount > 1 ? 115 : 135) : (imgCount > 1 ? 140 : 170);
            heightPx = floatRight
                ? Math.max(heightPx, imgPx)
                : heightPx + imgPx + 16 + (imgCount > 0 ? 22 : 0);   // 上下方图再留一个切换按钮行
        }

        if (qType === "detailed_answer") {
            // 留白 0cm 时纸面仍有一条 20px 的作答线（.is-zero 底边）+ 上下边距
            heightPx += solSpaceCm > 0 ? Math.round(solSpaceCm * A4_PAGE.cmPx) + 13 : 33;
        }

        return Math.max(A4_PAGE.linePx * 2, Math.round(heightPx)) + A4_PAGE.itemGapPx;
    }

    // 大题标题估高：标题本身可能折行（多选题那句说明很长），再加 1.4em/0.7em 上下留白。
    function estimateSectionTitleHeightPx(headerText) {
        return paperTextHeightPx(headerText, 1) + 29;
    }

    // 首页页眉估高（绝密行 + 标题组 + 全卷说明 + 注意事项）。
    // 常量来自无头浏览器实测（14px/1.2 版式下逐项量得），改动页眉结构后要重新量。
    function estimateA4HeaderHeight(meta) {
        const isExamType = (meta.paper_type === "exam" || meta.paper_type === "exam_19");
        // 132 = 主标题 + 「数 学」 + 副标题（实测 345 px 首页页眉倒推，另留 20px 安全余量：
        // 页眉估低了会把首页撑破，估高了只少排一道题）
        let height = 132;
        height += Math.max(0, paperLineCount(meta.title || "", 0.55) - 1) * 32;   // 长标题折行
        if (isExamType) {
            height += 34;                                   // 绝密★启用前 / 恢复提示行
            height += 32;                                   // 「本试卷共 N 页…」说明行
            height += meta.show_notice !== false ? 154 : 34; // 注意事项三条 / 恢复提示
        }
        return Math.round(height);
    }

    // 取元素的有效缩放率：rect.width 是「视口像素」，offsetWidth 是元素自己的「布局像素」，
    // 比值就是该元素到视口之间的累计缩放（对 zoom 与 transform: scale() 都成立，未缩放时为 1）。
    function paperStageZoomFactor(el) {
        if (!el || !el.offsetWidth) return 1;
        const ratio = el.getBoundingClientRect().width / el.offsetWidth;
        return (Number.isFinite(ratio) && ratio > 0.01) ? ratio : 1;
    }

    // ---- 画布缩放（待办 #18）----
    // 三态：'fit' 适应宽度（默认，倍率不超过 1）/ 手动态（± 或 100% 进入）。
    // 缩放只作用于 .paper-zoom-stage，页面内部几何不动，因此 KaTeX 渲染与选项降列
    // 的测量口径不受影响；拖拽插入判定比较的是 getBoundingClientRect() 与 e.clientY，
    // 两者同为视口像素，缩放后仍然同一坐标系，无需额外换算。
    let paperZoomMode = "fit";
    let paperZoomManual = 1;
    const PAPER_ZOOM_MIN = 0.4;
    const PAPER_ZOOM_MAX = 1.5;
    const PAPER_ZOOM_STEP = 0.1;
    let paperZoomResizeQueued = false;

    function paperZoomFitRatio() {
        const scroller = document.getElementById("a4PaperPreviewSheet");
        if (!scroller || !scroller.clientWidth) return 1;
        // 留 8px 余量，避免刚好压线时冒出横向滚动条（那又会少 15px，反复抖动）
        const available = scroller.clientWidth - 8;
        if (available <= 0) return 1;
        return Math.max(PAPER_ZOOM_MIN, Math.min(1, available / A4_PAGE.widthPx));
    }

    function resolvePaperZoomRatio() {
        const ratio = paperZoomMode === "fit" ? paperZoomFitRatio() : paperZoomManual;
        const clamped = Math.max(PAPER_ZOOM_MIN, Math.min(PAPER_ZOOM_MAX, ratio));
        return Math.round(clamped * 100) / 100;
    }

    window.applyPaperZoom = function () {
        const ratio = resolvePaperZoomRatio();
        const stage = document.getElementById("paperZoomStage");
        if (stage) stage.style.setProperty("--paper-zoom", String(ratio));
        const readout = document.getElementById("paperZoomReadout");
        if (readout) readout.textContent = Math.round(ratio * 100) + "%";
        ["fit", "manual"].forEach(function (mode) {
            const btn = document.querySelector("[data-paper-zoom-mode='" + mode + "']");
            if (btn) btn.classList.toggle("is-active", paperZoomMode === mode);
        });
    };

    window.setPaperZoomFit = function () {
        paperZoomMode = "fit";
        window.applyPaperZoom();
    };

    window.setPaperZoomActual = function () {
        paperZoomMode = "manual";
        paperZoomManual = 1;
        window.applyPaperZoom();
    };

    window.stepPaperZoom = function (delta) {
        const base = paperZoomMode === "fit" ? resolvePaperZoomRatio() : paperZoomManual;
        paperZoomMode = "manual";
        paperZoomManual = Math.round(Math.max(PAPER_ZOOM_MIN, Math.min(PAPER_ZOOM_MAX, base + delta)) * 100) / 100;
        window.applyPaperZoom();
    };

    // 视口宽度变化时重算「适应宽度」；手动态尊重用户设定，不跟着变。
    window.addEventListener("resize", function () {
        if (paperZoomMode !== "fit" || paperZoomResizeQueued) return;
        paperZoomResizeQueued = true;
        requestAnimationFrame(function () {
            paperZoomResizeQueued = false;
            window.applyPaperZoom();
        });
    });

    function renderA4Header(meta, totalCount, totalScore, totalPages) {
        const isExamType = (meta.paper_type === 'exam' || meta.paper_type === 'exam_19');
        return `
            <!-- Top Secret Mark Bar -->
            ${isExamType ? `
                ${meta.show_secret !== false ? `
                    <div class="group relative flex justify-between items-center mb-3 text-xs font-serif font-bold text-slate-800 pb-1 border border-transparent hover:border-amber-300 hover:bg-amber-50/40 px-2 py-0.5 rounded-lg transition-all duration-200 cursor-default">
                        <span>绝密★启用前</span>
                        <button type="button" onclick="updatePaperMeta('show_secret', false)" 
                                title="点击移除绝密标记"
                                class="opacity-0 group-hover:opacity-100 absolute top-0.5 right-1 bg-amber-500 hover:bg-amber-600 text-white text-[10px] font-sans px-2 py-0.5 rounded-full shadow-md transition-all duration-200 flex items-center space-x-1 cursor-pointer z-20">
                            <i class="fa-solid fa-eye-slash text-[9px]"></i>
                            <span>移除标记</span>
                        </button>
                    </div>
                ` : `
                    <div onclick="updatePaperMeta('show_secret', true)" 
                         title="点击恢复绝密标记"
                         class="mb-3 border border-dashed border-slate-300 hover:border-brand-500 bg-slate-50/50 hover:bg-brand-50/50 rounded-lg py-0.5 px-2 text-xs text-slate-400 hover:text-brand-600 cursor-pointer transition-all duration-200 group select-none flex items-center space-x-1.5 w-fit">
                        <i class="fa-solid fa-circle-plus text-slate-400 group-hover:text-brand-500 text-xs group-hover:scale-110 transition-transform"></i>
                        <span class="font-sans font-medium text-[10px]">已移除绝密标记 (点击在此恢复)</span>
                    </div>
                `}
            ` : ''}

            <!-- Exam Header Title & Subject -->
            <div class="title-header-group text-center mb-3">
                <h1 contenteditable="true"
                    oninput="updatePaperMeta('title', this.innerText)"
                    onblur="saveMetaToStorage()"
                    title="点击直接在试卷上修改主标题"
                    placeholder="+ 点击在此直接添加主标题"
                    class="canvas-meta-title text-2xl font-bold tracking-normal text-slate-900 font-serif mb-1.5 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text inline-block min-w-[200px]"
                    spellcheck="false">${(meta.title && meta.title.trim()) ? escapeHtml(meta.title) : ''}</h1>
                <div class="text-xl font-bold text-slate-900 font-serif my-2 select-none">数 学</div>
                <div contenteditable="true"
                     oninput="updatePaperMeta('subtitle', this.innerText)"
                     onblur="saveMetaToStorage()"
                     title="点击直接在试卷上修改副标题/备注"
                     placeholder="+ 点击在此直接添加副标题 / 备注"
                     class="canvas-meta-subtitle text-sm font-bold font-serif text-slate-900 my-1.5 outline-none hover:bg-amber-50/60 focus:bg-white focus:ring-2 focus:ring-brand-200/80 rounded-lg px-3 py-0.5 transition-all cursor-text min-w-[140px] inline-block"
                     spellcheck="false">${(meta.subtitle && meta.subtitle.trim()) ? escapeHtml(meta.subtitle) : ''}</div>
            </div>

            ${isExamType ? `
                <div class="text-[12px] text-center font-serif text-slate-800 mb-4">
                    本试卷共 ${totalPages} 页，${totalCount} 题。全卷满分 ${totalScore} 分。考试用时 120 分钟。
                </div>

                <!-- Standard LaTeX Notice Block with Interactive Toggle -->
                ${meta.show_notice !== false ? `
                    <div class="group relative mb-5 text-[11.5px] leading-relaxed font-serif text-slate-800 border border-transparent hover:border-amber-300 hover:bg-amber-50/40 p-2.5 rounded-xl transition-all duration-200 cursor-default">
                        <button type="button" onclick="updatePaperMeta('show_notice', false)" 
                                title="点击移除注意事项"
                                class="opacity-0 group-hover:opacity-100 absolute -top-2.5 right-2 bg-amber-500 hover:bg-amber-600 text-white text-[10px] font-sans px-2.5 py-0.5 rounded-full shadow-md transition-all duration-200 flex items-center space-x-1 cursor-pointer z-20">
                            <i class="fa-solid fa-eye-slash text-[9px]"></i>
                            <span>移除注意事项</span>
                        </button>
                        <div class="font-bold mb-1 text-slate-900 text-[12px]">注意事项：</div>
                        <ol class="list-decimal list-inside space-y-0.5 text-slate-800 pl-4">
                            <li>答卷前，考生务必将自己的姓名、考生号、考场号、座位号填写在答题卡上。</li>
                            <li>回答选择题时，选出每小题答案后，用铅笔把答题卡上对应题目的答案标号涂黑，如需改动，用橡皮擦干净后，再选涂其他答案标号。回答非选择题时，将答案写在答题卡上。写在本试卷上无效。</li>
                            <li>考试结束后，将本试卷和答题卡一并交回。</li>
                        </ol>
                    </div>
                ` : `
                    <div onclick="updatePaperMeta('show_notice', true)" 
                         title="点击恢复注意事项"
                         class="mb-4 my-2 border border-dashed border-slate-300 hover:border-brand-500 bg-slate-50/50 hover:bg-brand-50/50 rounded-xl p-2 text-center text-xs text-slate-400 hover:text-brand-600 cursor-pointer transition-all duration-200 group select-none flex items-center justify-center space-x-1.5">
                        <i class="fa-solid fa-circle-plus text-slate-400 group-hover:text-brand-500 text-sm group-hover:scale-110 transition-transform"></i>
                        <span class="font-sans font-medium text-[10px]">已移除注意事项 (点击在此恢复)</span>
                    </div>
                `}
            ` : ''}
        `;
    }

    function generateA4PaperPagesHtml(cart, meta, totalCount, totalScore) {
        if (cart.length === 0) {
            return `
                <div class="a4-paper-sheet">
                    ${renderA4Header(meta, totalCount, totalScore, 1)}
                    <div class="text-center py-24 text-slate-400 font-sans text-xs">暂无试题数据，请在左侧点击“加入试卷”添加题目</div>
                    <div class="paper-page-footer">数学 &nbsp; 第 1 页 (共 1 页)</div>
                </div>
            `;
        }

        const validCart = cart.filter(item => {
            const q = window.PaperStore.questionsMap[item.id];
            return q && q.content && q.content.trim().length > 0;
        });

        const cartItemsWithIndex = validCart.map((item, idx) => ({ ...item, cartIndex: idx }));

        const typeOrder = ['single_choice', 'multi_choice', 'fill_in_blank', 'detailed_answer'];
        const grouped = {};

        cartItemsWithIndex.forEach(item => {
            const q = window.PaperStore.questionsMap[item.id];
            if (!q) return;
            const qType = q.question_type || 'single_choice';
            if (!grouped[qType]) grouped[qType] = [];
            grouped[qType].push(item);
        });

        const blocks = [];
        const secNums = ['一', '二', '三', '四', '五'];
        let secIdx = 0;
        const isExam19 = (meta.paper_type === 'exam_19');
        let globalQIndex = 1;

        typeOrder.forEach(qType => {
            const items = grouped[qType];
            if (!items || items.length === 0) return;

            // For exam_19: set fixed starting question number according to Gaokao rules
            if (isExam19) {
                if (qType === 'single_choice') globalQIndex = 1;
                else if (qType === 'multi_choice') globalQIndex = 9;
                else if (qType === 'fill_in_blank') globalQIndex = 12;
                else if (qType === 'detailed_answer') globalQIndex = 15;
            }

            const secNum = secNums[secIdx] || (secIdx + 1);
            secIdx++;

            const count = items.length;
            const secScore = items.reduce((s, it) => s + (parseInt(it.score, 10) || 5), 0);
            const unitScore = items[0] ? (parseInt(items[0].score, 10) || 5) : 5;

            let secHeaderText = '';
            if (meta.paper_type === 'quiz') {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、单选题`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题`;
                } else {
                    secHeaderText = `${secNum}、解答题`;
                }
            } else {
                if (qType === 'single_choice') {
                    secHeaderText = `${secNum}、选择题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，只有一项是符合题目要求的。`;
                } else if (qType === 'multi_choice') {
                    secHeaderText = `${secNum}、多选题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。在每小题给出的四个选项中，有多项符合题目要求。全部选对的得 ${unitScore} 分，部分选对的得部分分，有选错的得 0 分。`;
                } else if (qType === 'fill_in_blank') {
                    secHeaderText = `${secNum}、填空题：本题共 ${count} 小题，每小题 ${unitScore} 分，共 ${secScore} 分。`;
                } else {
                    secHeaderText = `${secNum}、解答题：本题共 ${count} 小题，共 ${secScore} 分。解答应写出文字说明、证明过程或演算步骤。`;
                }
            }

            blocks.push({
                type: 'section_title',
                qType: qType,
                html: `
                    <div class="paper-sec-block" data-qtype="${qType}">
                        <h3 class="font-bold">${secHeaderText}</h3>
                    </div>
                `,
                estHeight: estimateSectionTitleHeightPx(secHeaderText)
            });

            items.forEach((item, subIdx) => {
                const q = window.PaperStore.questionsMap[item.id];
                let rawContent = q ? q.content : '';
                const figAlign = getQuestionFigAlign(q);

                let solSpaceCm = 0;
                let isSolSpaceEmbedded = false;

                if (qType === 'detailed_answer') {
                    const defaultFallback = meta.paper_type === 'exam_19' ? '0.0' : '7.0';
                    const defaultSpace = parseFloat(meta.solution_space_default !== undefined ? meta.solution_space_default : defaultFallback);
                    solSpaceCm = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
                    if (isNaN(solSpaceCm)) solSpaceCm = 0.0;

                    if (solSpaceCm > 0 && (figAlign === 'bottom_right' || figAlign === 'center')) {
                        isSolSpaceEmbedded = true;
                    }
                }

                const isChoiceQuestion = qType === 'single_choice' || qType === 'multi_choice';
                const choiceContentParts = isChoiceQuestion
                    ? splitChoiceContentForPaperPreview(rawContent)
                    : { stemRaw: rawContent, choicesRaw: '' };
                let contentRes = q ? formatQuestionContentHtml(choiceContentParts.stemRaw, q.id, figAlign, isSolSpaceEmbedded) : '';
                const separatedChoicesHtml = q && choiceContentParts.choicesRaw
                    ? formatQuestionContentHtml(choiceContentParts.choicesRaw, q.id, figAlign, false, false)
                    : '';
                let contentHtml = '';
                let embeddedImgHtml = '';

                if (isSolSpaceEmbedded && typeof contentRes === 'object') {
                    contentHtml = contentRes.stemHtml;
                    embeddedImgHtml = contentRes.imgHtml || '';
                } else {
                    contentHtml = typeof contentRes === 'string' ? contentRes : (contentRes.stemHtml || '');
                }

                let stemLine = '';
                if (isChoiceQuestion) {
                    let stemContent = contentHtml;
                    let choicesGrid = separatedChoicesHtml;
                    if (!choicesGrid && (contentHtml.includes('choices-grid') || contentHtml.includes('katex-choices-grid') || contentHtml.includes('grid-cols-'))) {
                        const match = contentHtml.match(/([\s\S]*?)(<(?:div|p)[^>]*class="[^"]*(?:choices-grid|katex-choices-grid|grid-cols-[124])"[\s\S]*)/i);
                        if (match) {
                            stemContent = match[1];
                            choicesGrid = match[2];
                        }
                    }

                    stemLine = `
                        <div class="paper-choice-stem-row flex justify-between items-baseline mb-1">
                            <div class="flex-1">${stemContent}</div>
                        </div>
                        <div class="paper-choice-options-row">${choicesGrid}</div>
                    `;
                } else {
                    stemLine = contentHtml;
                }

                let solutionBlankHtml = '';
                if (qType === 'detailed_answer') {
                    const spacePx = Math.round(solSpaceCm * A4_PAGE.cmPx);
                    const isZero = solSpaceCm <= 0;

                    let embeddedImgContainer = '';
                    if (isSolSpaceEmbedded && embeddedImgHtml) {
                        const posClass = figAlign === 'center' ? 'left-1/2 -translate-x-1/2' : 'right-3';
                        embeddedImgContainer = `
                            <div class="absolute ${posClass} top-2 z-10">
                                ${embeddedImgHtml}
                            </div>
                        `;
                    }

                    const minHeightStyle = (isSolSpaceEmbedded && embeddedImgHtml)
                        ? `min-height: ${Math.max(spacePx, 180)}px; height: ${Math.max(spacePx, 180)}px;`
                        : (isZero ? 'min-height: 20px;' : `height: ${spacePx}px;`);

                    solutionBlankHtml = `
                        <div class="solution-space-zone group/blank relative mt-2 mb-1 ${isZero ? 'is-zero' : ''}" data-solution-space-zone-qid="${q ? q.id : 0}" style="${minHeightStyle}">
                            ${embeddedImgContainer}
                            <!-- Anchor controls to the zone's top edge. They stay out
                                 of document flow, so preview pagination remains stable. -->
                            <div class="solution-space-controls absolute right-2 top-2 opacity-0 group-hover/blank:opacity-100 focus-within:opacity-100 transition-opacity flex items-center space-x-1 whitespace-nowrap bg-white/95 backdrop-blur-sm px-2 py-0.5 rounded-lg border border-slate-200 shadow-sm text-[10px] font-sans select-none z-20" data-solution-space-controls-qid="${q ? q.id : 0}">
                                <span class="text-slate-400 mr-1 font-medium">留白微调:</span>
                                <button type="button" data-solution-space-delta="-1" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 1cm 留白">
                                    - 1cm
                                </button>
                                <button type="button" data-solution-space-delta="-0.5" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, -0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="减少 0.5cm 留白">
                                    - 0.5
                                </button>
                                <span class="inline-flex w-14 justify-center px-1 font-bold text-brand-600" data-solution-space-value="${q ? q.id : 0}">${solSpaceCm.toFixed(1)} cm</span>
                                <button type="button" data-solution-space-delta="0.5" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 0.5)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 0.5cm 留白">
                                    + 0.5
                                </button>
                                <button type="button" data-solution-space-delta="1" onclick="event.stopPropagation(); window.updateQuestionSolutionSpace(${q ? q.id : 0}, 1.0)" class="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 hover:bg-brand-100 hover:text-brand-700 font-bold transition-all" title="增加 1cm 留白">
                                    + 1cm
                                </button>
                            </div>
                            <!-- 纸面上不画水印：留白厘米数由 hover 出现的「留白微调」控件显示 -->
                        </div>
                    `;
                }

                const itemHtml = `
                    <div class="paper-q-item group relative"
                        data-qid="${q ? q.id : ''}"
                        data-qtype="${qType}"
                        data-sub-index="${subIdx}"
                        ondragover="onPaperCanvasDragOver(event)"
                        ondragenter="onPaperCanvasDragEnter(event)"
                        ondragleave="onPaperCanvasDragLeave(event)"
                        ondragend="onPaperCanvasDragEnd(event)"
                        ondrop="onPaperCanvasDrop(event)">

                        <!-- Hover Action Bar: Drag Handle & Quick Move/Remove Buttons -->
                        <div class="paper-canvas-toolbar absolute right-2 top-2 transition-opacity flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[10px] font-sans select-none z-10">
                            <span class="toolbar-label font-medium mr-0.5 cursor-grab active:cursor-grabbing"
                                  draggable="true"
                                  ondragstart="onPaperCanvasDragStart(event, ${q ? q.id : 0}, ${subIdx}, '${qType}')"
                                  ondragend="onPaperCanvasDragEnd(event)"
                                  title="按住此处拖拽，可在同一题型内调整题目顺序"><i class="fa-solid fa-grip-vertical"></i> 按住拖拽排序</span>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'up')" ${subIdx === 0 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="上移">
                                <i class="fa-solid fa-chevron-up"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.movePaperQuestionWithinType('${qType}', ${subIdx}, 'down')" ${subIdx === items.length - 1 ? 'disabled' : ''} class="toolbar-btn p-0.5 disabled:opacity-30" title="下移">
                                <i class="fa-solid fa-chevron-down"></i>
                            </button>
                            <button onclick="event.stopPropagation(); window.removeFromCart(${q ? q.id : 0})" class="toolbar-btn p-0.5 hover:text-rose-500" title="移出试卷">
                                <i class="fa-solid fa-xmark"></i>
                            </button>
                        </div>

                        <div class="flex items-baseline">
                            <span class="font-bold mr-1 text-slate-900 shrink-0">${globalQIndex}.</span>
                            <div class="inline flex-1">
                                ${stemLine}
                                ${solutionBlankHtml}
                            </div>
                        </div>
                    </div>
                `;

                // 高度估算（待办 #18）：按真 A4 版心逐块折算行数，取代原先一律 75px 的魔法数。
                // 仍然不是实测分页（方案 B 本期否决），但估算口径与页面盒子对齐：偏差只会让页底
                // 少排一点，不再像过去那样把页面撑高、破坏 A4 比例。
                const detachedImgCount = shouldPreserveInlinePaperImages(rawContent)
                    ? 0
                    : (String(rawContent || '').match(/!\[[^\]]*\]\([^)]*\)/g) || []).length;
                const estH = estimateQuestionBlockHeight(qType, rawContent, solSpaceCm, figAlign, detachedImgCount);

                blocks.push({
                    type: 'question',
                    qType: qType,
                    html: itemHtml,
                    estHeight: estH
                });

                globalQIndex++;
            });
        });

        // Group blocks into A4 Page cards
        const pages = [];
        let currentPage = [];
        let currentH = 0;
        // 分页预算（待办 #18）：每页正文可用高度 = 1123 - 96×2 = 931px；首页再扣掉页眉
        // 估算高度，保证首页排满、续页不再随机出现大片空白。
        const PAGE_N_MAX = A4_PAGE.contentPx;
        const PAGE_1_MAX = A4_PAGE.contentPx - estimateA4HeaderHeight(meta);

        blocks.forEach(blk => {
            const maxH = (pages.length === 0) ? PAGE_1_MAX : PAGE_N_MAX;
            if (currentH + blk.estHeight > maxH && currentPage.length > 0) {
                pages.push(currentPage);
                currentPage = [blk];
                currentH = blk.estHeight;
            } else {
                currentPage.push(blk);
                currentH += blk.estHeight;
            }
        });
        if (currentPage.length > 0) {
            pages.push(currentPage);
        }

        const totalPages = pages.length;

        // Generate A4 Page Sheet DOM Cards
        let pagesHtml = '';
        pages.forEach((pgBlocks, pgIdx) => {
            const isFirstPage = (pgIdx === 0);
            let pgContent = pgBlocks.map(b => b.html).join('');

            pagesHtml += `
                <div class="a4-paper-sheet mb-8">
                    ${isFirstPage ? renderA4Header(meta, totalCount, totalScore, totalPages) : ''}
                    
                    <div class="paper-page-body">
                        ${pgContent}
                    </div>

                    <!-- Page Footer -->
                    <div class="paper-page-footer">
                        数学 &nbsp; 第 ${pgIdx + 1} 页 (共 ${totalPages} 页)
                    </div>
                </div>
            `;
        });

        return pagesHtml;
    }

    // Reorder Items strictly within the same Question Type section
    function reorderItemsWithinType(cart, qType, fromSubIdx, toSubIdx) {
        const itemsOfType = [];
        cart.forEach((item) => {
            const q = window.PaperStore.questionsMap[item.id];
            const t = q ? q.question_type : 'single_choice';
            if (t === qType) {
                itemsOfType.push(item);
            }
        });

        if (fromSubIdx < 0 || fromSubIdx >= itemsOfType.length || toSubIdx < 0 || toSubIdx >= itemsOfType.length) {
            return cart;
        }

        const moved = itemsOfType.splice(fromSubIdx, 1)[0];
        itemsOfType.splice(toSubIdx, 0, moved);

        const newCart = [...cart];
        let subIdx = 0;
        cart.forEach((item, idx) => {
            const q = window.PaperStore.questionsMap[item.id];
            const t = q ? q.question_type : 'single_choice';
            if (t === qType) {
                newCart[idx] = itemsOfType[subIdx];
                subIdx++;
            }
        });

        return newCart;
    }

    // Move Question Order within same question type
    window.movePaperQuestionWithinType = function (qType, subIndex, direction) {
        const cart = window.PaperStore.cart;
        const targetSubIdx = direction === 'up' ? subIndex - 1 : subIndex + 1;
        window.PaperStore.cart = reorderItemsWithinType(cart, qType, subIndex, targetSubIdx);
        saveCartToStorage();
        renderPart3QuestionStream();
        window.renderPaperCanvas();
    };

    // Real-Time Dynamic Drag and Drop for A4 Paper Canvas Items (Restricted to same question type)
    let draggedItemData = null;
    let dragPlaceholder = null;

    window.onPaperCanvasDragStart = function (e, qid, subIndex, qType) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (!card) return;

        draggedItemData = { 
            qid: parseInt(qid, 10), 
            fromSubIndex: parseInt(subIndex, 10),
            qType: qType,
            element: card
        };

        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(qid));

        // Create or reuse dynamic drop placeholder
        if (!dragPlaceholder) {
            dragPlaceholder = document.createElement('div');
            dragPlaceholder.className = 'paper-drag-placeholder border-2 border-dashed border-brand-500 bg-brand-50/70 rounded-xl my-2 flex items-center justify-center text-xs font-semibold text-brand-600 shadow-inner transition-all duration-200 select-none';
            dragPlaceholder.style.height = `${Math.max(48, card.offsetHeight - 8)}px`;
            dragPlaceholder.innerHTML = '<span class="flex items-center space-x-1.5"><i class="fa-solid fa-arrow-down-long text-brand-500 animate-bounce"></i> <span>释放在同题型内插入试题</span></span>';
        }

        // Apply drag style to current card after browser creates drag ghost image
        setTimeout(() => {
            if (card) {
                card.classList.add('opacity-30', 'scale-[0.98]', 'bg-slate-100');
                if (card.parentNode) {
                    card.parentNode.insertBefore(dragPlaceholder, card);
                }
            }
        }, 0);
    };

    window.onPaperCanvasDragOver = function (e) {
        e.preventDefault();
        if (!draggedItemData || !dragPlaceholder) return;

        const targetCard = e.target.closest('.paper-q-item');
        if (!targetCard || targetCard === draggedItemData.element) return;

        // Strict boundary: check if targetCard belongs to the SAME question type section!
        const targetQType = targetCard.dataset.qtype;
        if (targetQType !== draggedItemData.qType) {
            // Different question type section! Disallow drag placeholder insertion
            e.dataTransfer.dropEffect = 'none';
            return;
        }

        e.dataTransfer.dropEffect = 'move';
        const rect = targetCard.getBoundingClientRect();
        const midY = rect.top + rect.height / 2;

        if (e.clientY < midY) {
            if (targetCard.previousElementSibling !== dragPlaceholder) {
                targetCard.parentNode.insertBefore(dragPlaceholder, targetCard);
            }
        } else {
            if (targetCard.nextElementSibling !== dragPlaceholder) {
                targetCard.parentNode.insertBefore(dragPlaceholder, targetCard.nextElementSibling);
            }
        }
    };

    window.onPaperCanvasDragEnter = function (e) {
        e.preventDefault();
    };

    window.onPaperCanvasDragLeave = function (e) {
        e.preventDefault();
    };

    window.onPaperCanvasDragEnd = function (e) {
        const card = e.currentTarget.closest('.paper-q-item');
        if (card) {
            card.classList.remove('opacity-30', 'scale-[0.98]', 'bg-slate-100');
        }

        // Find new index within the SAME question type section
        if (dragPlaceholder && dragPlaceholder.parentNode && draggedItemData) {
            const container = dragPlaceholder.parentNode;
            const allItems = Array.from(container.children);
            
            let newSubIndex = 0;
            for (let i = 0; i < allItems.length; i++) {
                const child = allItems[i];
                if (child === dragPlaceholder) {
                    break;
                }
                if (child.classList && child.classList.contains('paper-q-item') && child !== draggedItemData.element) {
                    newSubIndex++;
                }
            }

            const fromSubIndex = draggedItemData.fromSubIndex;
            const qType = draggedItemData.qType;
            
            if (dragPlaceholder.parentNode) {
                dragPlaceholder.parentNode.removeChild(dragPlaceholder);
            }

            if (fromSubIndex !== newSubIndex && fromSubIndex >= 0 && newSubIndex >= 0) {
                window.PaperStore.cart = reorderItemsWithinType(window.PaperStore.cart, qType, fromSubIndex, newSubIndex);

                saveCartToStorage();
                renderPart3QuestionStream();
                window.renderPaperCanvas();
                if (window.showToast) window.showToast(`试题顺序已更新`, 'info');
            } else {
                renderPart3QuestionStream();
                window.renderPaperCanvas();
            }
        } else if (dragPlaceholder && dragPlaceholder.parentNode) {
            dragPlaceholder.parentNode.removeChild(dragPlaceholder);
        }

        draggedItemData = null;
        dragPlaceholder = null;
    };

    window.onPaperCanvasDrop = function (e) {
        e.preventDefault();
        window.onPaperCanvasDragEnd(e);
    };

    // Solution Space Handlers
    function restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta) {
        if (!Number.isFinite(anchorTop)) return;

        const restoreAnchor = () => {
            const nextControl = document.querySelector(`[data-solution-space-controls-qid="${qid}"]`);
            if (!nextControl) return;
            const shift = nextControl.getBoundingClientRect().top - anchorTop;
            if (Math.abs(shift) > 0.5) {
                let scrollParent = nextControl.parentElement;
                while (scrollParent) {
                    const style = window.getComputedStyle(scrollParent);
                    const canScroll = /(auto|scroll)/.test(style.overflowY || '') &&
                        scrollParent.scrollHeight > scrollParent.clientHeight + 1;
                    if (canScroll) {
                        // rect 差值是「视口像素」，scrollTop 用的是滚动容器自己的布局像素：
                        // 画布被缩放后两者相差一个有效缩放率，不换算就会跳过头或跳不足。
                        scrollParent.scrollTop += shift / paperStageZoomFactor(scrollParent);
                        break;
                    }
                    scrollParent = scrollParent.parentElement;
                }
            }
            if (activeDelta !== null && activeDelta !== undefined) {
                const nextButton = Array.from(
                    nextControl.querySelectorAll('[data-solution-space-delta]')
                ).find(button => button.getAttribute('data-solution-space-delta') === String(activeDelta));
                if (nextButton) nextButton.focus({ preventScroll: true });
            }
        };

        // renderPaperCanvas schedules its own scroll restoration. Queue this
        // anchor correction afterwards, then repeat once for late layout.
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(() => {
                restoreAnchor();
                requestAnimationFrame(restoreAnchor);
            });
        } else {
            restoreAnchor();
        }
    }

    window.updateQuestionSolutionSpace = function (qid, delta) {
        qid = parseInt(qid, 10);
        const item = window.PaperStore.cart.find(it => it.id === qid);
        if (!item) return;
        const currentControl = document.querySelector(
            `[data-solution-space-controls-qid="${qid}"]`
        );
        const anchorTop = currentControl
            ? currentControl.getBoundingClientRect().top
            : Number.NaN;
        const activeDelta = currentControl && currentControl.contains(document.activeElement)
            ? document.activeElement.getAttribute('data-solution-space-delta')
            : null;
        const defaultSpace = parseFloat(window.PaperStore.meta.solution_space_default || '7.0');
        let currentSpace = parseFloat(item.solution_space !== undefined ? item.solution_space : defaultSpace);
        if (isNaN(currentSpace)) currentSpace = 7.0;
        
        let newSpace = Math.max(0.0, Math.min(15.0, Math.round((currentSpace + delta) * 10) / 10));
        item.solution_space = newSpace.toFixed(1);
        saveCartToStorage();
        window.renderPaperCanvas();
        restoreSolutionSpaceControlViewport(qid, anchorTop, activeDelta);
        const q = window.PaperStore.questionsMap[qid];
        const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
        if (window.showToast) window.showToast(`题目 #${seqNum} 留白高度设为 ${newSpace.toFixed(1)} cm`, 'info');
    };

    window.updateGlobalSolutionSpace = function (val) {
        const spaceVal = parseFloat(val).toFixed(1);
        window.PaperStore.meta.solution_space_default = spaceVal;
        window.PaperStore.cart.forEach(item => {
            item.solution_space = spaceVal;
        });
        saveMetaToStorage();
        saveCartToStorage();
        window.renderPaperCanvas();
        if (window.showToast) {
            window.showToast(`解答题全局留白设为 ${spaceVal} cm（点击 PDF 预览可即时生效）`, 'success');
        }
    };

    // Helper: build cart questions payload with solution_space
    function buildCartQuestionsPayload() {
        const cart = window.PaperStore.cart;
        const defaultSpace = (window.PaperStore.meta.solution_space_default || '7.0').toString();
        return cart.map((item, idx) => {
            const q = window.PaperStore.questionsMap[item.id] || {};
            return {
                id: item.id,
                score: item.score,
                order: idx + 1,
                figure_align: getQuestionFigAlign(q),
                solution_space: item.solution_space !== undefined ? item.solution_space.toString() : defaultSpace
            };
        });
    }

    // 缺图上报：后端在准备阶段就把「清单里找不到」的图片引用降级成卷面红字占位
    // （mathbank/paper_helper.py: patch_missing_latex_images），并用两个响应头把缺了几张、
    // 缺哪些图告知前端。这里必须翻成 Toast：否则用户拿到 PDF 只看见凭空多出的红字，
    // 完全不知道是自己题库里的图丢了。
    function notifyMissingPaperImages(response) {
        if (!response || !response.headers) return 0;
        const count = parseInt(response.headers.get('X-Paper-Missing-Images') || '0', 10);
        if (!Number.isInteger(count) || count <= 0) return 0;
        let files = response.headers.get('X-Paper-Missing-Image-Files') || '';
        try {
            files = files ? decodeURIComponent(files) : '';
        } catch (e) {
            // 头值按后端约定是百分号编码；万一被中间层解过一次，直接用原文继续提示。
        }
        const names = files.split(',').map((name) => name.trim()).filter(Boolean);
        let message = `⚠️ 本次导出有 ${count} 张图片缺失，已在卷面用红字占位，请检查题库`;
        if (names.length) {
            const preview = names.slice(0, 3).join('、');
            message += `：${preview}${names.length > 3 ? ` 等 ${names.length} 张` : ''}`;
        }
        if (window.showToast) window.showToast(message, 'warning');
        return count;
    }

    // 导出说明上报：后端把「入库即坏、导出前被自动救回来」的公式形态（以及死图降级）
    // 汇总在 X-MathBank-Export-Notes 头里（mathbank/routes_paper.py: _export_notes_headers）。
    // 这里必须翻成 Toast：否则教师只看见 PDF 里公式排得怪，永远不知道要回题库改原文。
    function notifyPaperExportNotes(response) {
        if (!response || !response.headers) return '';
        let notes = response.headers.get('X-MathBank-Export-Notes') || '';
        if (!notes) return '';
        try {
            notes = decodeURIComponent(notes);
        } catch (e) {
            // 头值按后端约定是百分号编码；被中间层解过一次就直接用原文继续提示。
        }
        // 条数由后端单独给一个头：说明正文里本来就带「；」（样例之间用它分隔），
        // 前端拆字符串必数错。旧响应没有计数头时退化成 1，提示照样出。
        const count = parseInt(response.headers.get('X-MathBank-Export-Note-Count') || '0', 10) || 1;
        const digest = notes.slice(0, 90);
        let message = '⚠️ 本次导出已自动修复 ' + count + ' 处入库公式问题，请回题库核对原文';
        message += '：' + digest + (notes.length > digest.length ? '…' : '');
        if (window.showToast) window.showToast(message, 'warning');
        return notes;
    }

    // Export PDF, Word, TeX, and Save Handlers
    window.exportPaperPdf = async function (target = 'paper') {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 PDF', 'warning');
            return;
        }

        const targetName = (target === 'sheet') ? '答题卡' : '试卷';
        const iconEmoji = (target === 'sheet') ? '📝' : '📄';

        // Pre-open single tab synchronously during click event -> 100% bypasses popup blockers!
        const tab = window.open('', '_blank');
        setPdfTabLoadingState(tab, `${iconEmoji} ${targetName} PDF 编译中`, iconEmoji, `正在为您在线静默编译 ${targetName} 高清 PDF...`);

        try {
            if (window.showToast) {
                window.showToast(`正在静默编译 ${targetName} PDF...`, 'info');
            }

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                target: target,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/pdf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok && res.headers.get('content-type')?.includes('application/pdf')) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                if (tab && !tab.closed) {
                    tab.location.href = url;
                }
                if (window.showToast) window.showToast(`${targetName} PDF 编译成功！已在新窗口打开`, 'success');
                notifyMissingPaperImages(res);
                notifyPaperExportNotes(res);
            } else {
                let errLog = `${targetName} PDF 编译失败`;
                let errData = {};
                try {
                    errData = await res.json();
                    if (errData.message) errLog = errData.message;
                } catch (e) {}
                if (tab && !tab.closed) {
                    setPdfTabErrorState(tab, `${targetName} PDF 编译失败`, errData.diagnostic, errLog);
                }
                if (window.showToast) window.showToast(errLog, 'error');
            }
        } catch (e) {
            if (tab && !tab.closed) tab.close();
            if (window.showToast) window.showToast('PDF 请求编译异常', 'error');
        }
    };

    function setPdfTabLoadingState(tab, title, iconEmoji, text) {
        if (!tab) return;
        try {
            const safeTitle = escapeHtml(title);
            const safeIcon = escapeHtml(iconEmoji);
            const safeText = escapeHtml(text);
            tab.document.write(`
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>${safeTitle}</title>
                    <style>
                        body {
                            margin: 0;
                            padding: 0;
                            background-color: #0f172a;
                            color: #f8fafc;
                            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                            display: flex;
                            min-height: 100vh;
                            align-items: center;
                            justify-content: center;
                        }
                        .card {
                            background: #1e293b;
                            border: 1px solid #334155;
                            border-radius: 16px;
                            padding: 32px 40px;
                            text-align: center;
                            box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);
                            max-width: 360px;
                        }
                        .icon-box {
                            width: 56px;
                            height: 56px;
                            border-radius: 14px;
                            background: rgba(99, 102, 241, 0.15);
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            font-size: 28px;
                            margin: 0 auto 16px auto;
                        }
                        h2 { margin: 0 0 8px 0; font-size: 18px; font-weight: 700; color: #ffffff; }
                        p { margin: 0 0 20px 0; font-size: 13px; color: #94a3b8; line-height: 1.5; }
                        .status {
                            display: inline-flex;
                            align-items: center;
                            justify-content: center;
                            gap: 8px;
                            color: #818cf8;
                            font-size: 13px;
                            font-weight: 600;
                        }
                        @keyframes spin { 100% { transform: rotate(360deg); } }
                        .spinner {
                            width: 14px;
                            height: 14px;
                            border: 2px solid rgba(99, 102, 241, 0.3);
                            border-top-color: #818cf8;
                            border-radius: 50%;
                            animation: spin 0.8s linear infinite;
                        }
                    </style>
                </head>
                <body>
                    <div class="card">
                        <div class="icon-box">${safeIcon}</div>
                        <h2>${safeTitle}</h2>
                        <p>${safeText}</p>
                        <div class="status">
                            <div class="spinner"></div>
                            <span>LaTeX 引擎静默编译中...</span>
                        </div>
                    </div>
                </body>
                </html>
            `);
            tab.document.close();
        } catch(e) {}
    }

    function setPdfTabErrorState(tab, title, diagnostic, fallbackMessage) {
        if (!tab) return;
        const report = diagnostic || {};
        const fixes = Array.isArray(report.fixes) && report.fixes.length
            ? report.fixes
            : ['返回题目编辑页，核对报错位置附近的公式或排版命令。'];
        const fixesHtml = fixes.map(item => `<li>${escapeHtml(item)}</li>`).join('');
        const sourceHtml = report.source_context
            ? `<details><summary>查看出错位置附近的 LaTeX 源码</summary><pre>${escapeHtml(report.source_context)}</pre></details>`
            : '';
        const technicalHtml = report.technical_error
            ? `<details><summary>查看编译器技术信息</summary><pre>${escapeHtml(report.technical_error)}</pre></details>`
            : '';
        try {
            tab.document.open();
            tab.document.write(`
                <!DOCTYPE html>
                <html lang="zh-CN">
                <head>
                    <meta charset="utf-8">
                    <title>${escapeHtml(title)}</title>
                    <style>
                        * { box-sizing: border-box; }
                        body { margin: 0; padding: 36px 20px; background: #f8fafc; color: #1e293b; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }
                        .card { max-width: 760px; margin: 0 auto; background: white; border: 1px solid #e2e8f0; border-radius: 18px; padding: 28px; box-shadow: 0 18px 45px rgba(15,23,42,.08); }
                        .head { display: flex; gap: 14px; align-items: flex-start; }
                        .icon { width: 46px; height: 46px; flex: 0 0 46px; border-radius: 13px; background: #fff1f2; color: #e11d48; display: grid; place-items: center; font-size: 23px; }
                        h1 { margin: 0 0 6px; font-size: 20px; }
                        .badge { display: inline-block; margin-top: 4px; padding: 3px 8px; border-radius: 999px; background: ${report.ai_used ? '#eef2ff' : '#f1f5f9'}; color: ${report.ai_used ? '#4f46e5' : '#64748b'}; font-size: 12px; }
                        .section { margin-top: 22px; padding-top: 18px; border-top: 1px solid #e2e8f0; }
                        h2 { margin: 0 0 8px; font-size: 14px; color: #475569; }
                        p, li { font-size: 14px; line-height: 1.75; }
                        p { margin: 0; }
                        ol { margin: 6px 0 0; padding-left: 22px; }
                        code { background: #f1f5f9; border-radius: 5px; padding: 2px 5px; }
                        details { margin-top: 14px; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px 12px; }
                        summary { cursor: pointer; color: #475569; font-size: 13px; font-weight: 600; }
                        pre { margin: 10px 0 0; padding: 12px; border-radius: 8px; overflow: auto; background: #0f172a; color: #e2e8f0; font-size: 12px; line-height: 1.55; white-space: pre-wrap; }
                        button { margin-top: 22px; border: 0; border-radius: 9px; padding: 10px 16px; background: #334155; color: white; cursor: pointer; }
                    </style>
                </head>
                <body>
                    <main class="card">
                        <div class="head">
                            <div class="icon">!</div>
                            <div>
                                <h1>${escapeHtml(report.summary || fallbackMessage || title)}</h1>
                                <p>${escapeHtml(report.location || '试卷公式或模板附近')}</p>
                                <span class="badge">${report.ai_used ? 'AI 已结合编译日志解释' : '本地诊断结果'}</span>
                            </div>
                        </div>
                        <section class="section"><h2>为什么会这样</h2><p>${escapeHtml(report.cause || fallbackMessage || 'LaTeX 编译没有完成。')}</p></section>
                        <section class="section"><h2>建议如何修复</h2><ol>${fixesHtml}</ol></section>
                        ${sourceHtml}
                        ${technicalHtml}
                        <button onclick="window.close()">关闭此页并返回修改</button>
                    </main>
                </body>
                </html>
            `);
            tab.document.close();
        } catch (e) {}
    }

    function setPandocModalVisible(visible) {
        const modal = document.getElementById('pandocInstallModal');
        if (!modal) return;
        if (visible) {
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modal.setAttribute('aria-hidden', 'false');
            // 旧实现只挂 rAF：后台标签页里 rAF 被冻结，弹窗会永远停在 opacity:0 吞点击。
            // 公共助手自带 setTimeout 兜底，两种环境都能揭示。
            window.revealModalWithTransition(modal);
        } else {
            modal.setAttribute('aria-hidden', 'true');
            window.closeModalWithTransition(modal);
        }
    }

    function resetPandocInstallModal() {
        const progress = document.getElementById('pandocInstallProgress');
        const error = document.getElementById('pandocInstallError');
        const install = document.getElementById('pandocInstallBtn');
        const compatibility = document.getElementById('pandocCompatibilityBtn');
        const cancel = document.getElementById('pandocCancelBtn');
        if (progress) progress.classList.add('hidden');
        if (error) {
            error.classList.add('hidden');
            error.textContent = '';
        }
        if (install) {
            install.disabled = false;
            install.innerHTML = '<i class="fa-solid fa-download mr-1.5" aria-hidden="true"></i>安装并继续导出';
        }
        if (compatibility) compatibility.disabled = false;
        if (cancel) cancel.disabled = false;
    }

    function updatePandocInstallProgress(state) {
        const progress = document.getElementById('pandocInstallProgress');
        const text = document.getElementById('pandocInstallProgressText');
        const value = document.getElementById('pandocInstallProgressValue');
        const bar = document.getElementById('pandocInstallProgressBar');
        const install = document.getElementById('pandocInstallBtn');
        const compatibility = document.getElementById('pandocCompatibilityBtn');
        const cancel = document.getElementById('pandocCancelBtn');
        const percent = Math.max(0, Math.min(100, parseInt(state.progress || '0', 10)));
        if (progress) progress.classList.remove('hidden');
        if (text) text.textContent = state.message || '正在准备 Word 可编辑公式组件…';
        if (value) value.textContent = `${percent}%`;
        if (bar) bar.style.width = `${percent}%`;
        if (install) {
            install.disabled = true;
            install.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1.5" aria-hidden="true"></i>正在安装…';
        }
        if (compatibility) compatibility.disabled = true;
        if (cancel) cancel.disabled = true;
    }

    function showPandocInstallError(message) {
        const error = document.getElementById('pandocInstallError');
        const install = document.getElementById('pandocInstallBtn');
        const compatibility = document.getElementById('pandocCompatibilityBtn');
        const cancel = document.getElementById('pandocCancelBtn');
        if (error) {
            error.textContent = message || 'Word 可编辑公式组件安装失败，尚未修改现有运行环境。';
            error.classList.remove('hidden');
        }
        if (install) {
            install.disabled = false;
            install.innerHTML = '<i class="fa-solid fa-rotate-right mr-1.5" aria-hidden="true"></i>重新安装';
        }
        if (compatibility) compatibility.disabled = false;
        if (cancel) cancel.disabled = false;
    }

    function waitForPandocDecision() {
        resetPandocInstallModal();
        setPandocModalVisible(true);
        return new Promise(resolve => {
            const install = document.getElementById('pandocInstallBtn');
            const compatibility = document.getElementById('pandocCompatibilityBtn');
            const cancel = document.getElementById('pandocCancelBtn');
            const finish = decision => {
                if (install) install.onclick = null;
                if (compatibility) compatibility.onclick = null;
                if (cancel) cancel.onclick = null;
                if (decision !== 'install') setPandocModalVisible(false);
                resolve(decision);
            };
            if (install) install.onclick = () => finish('install');
            if (compatibility) compatibility.onclick = () => finish('compatibility');
            if (cancel) cancel.onclick = () => finish('cancel');
        });
    }

    async function pollPandocInstall(taskId) {
        const deadline = Date.now() + 15 * 60 * 1000;
        while (Date.now() < deadline) {
            const response = await fetch(`/api/runtime/pandoc/install/${encodeURIComponent(taskId)}`);
            if (!response.ok) throw new Error('Pandoc 安装任务已失效。');
            const data = await response.json();
            const state = data.pandoc || {};
            updatePandocInstallProgress(state);
            if (state.status === 'completed' || state.status === 'ready') return true;
            if (state.status === 'error') throw new Error(state.error || state.message || 'Pandoc 安装失败。');
            await new Promise(resolve => setTimeout(resolve, 750));
        }
        throw new Error('Pandoc 安装等待超时，请重试。');
    }

    async function installPandocAndContinue() {
        while (true) {
            try {
                updatePandocInstallProgress({ progress: 0, message: '正在准备下载…' });
                const response = await fetch('/api/runtime/pandoc/install', { method: 'POST' });
                const data = await response.json();
                if (!response.ok || data.status !== 'success') {
                    throw new Error(data.message || 'Pandoc 安装任务创建失败。');
                }
                const state = data.pandoc || {};
                if (state.status !== 'ready') {
                    if (!state.task_id) throw new Error('Pandoc 安装任务缺少标识。');
                    await pollPandocInstall(state.task_id);
                }
                setPandocModalVisible(false);
                if (window.showToast) window.showToast('Word 可编辑公式组件已安装，正在继续导出…', 'success');
                return true;
            } catch (error) {
                showPandocInstallError(error && error.message ? error.message : 'Pandoc 安装失败。');
                const decision = await new Promise(resolve => {
                    const install = document.getElementById('pandocInstallBtn');
                    const compatibility = document.getElementById('pandocCompatibilityBtn');
                    const cancel = document.getElementById('pandocCancelBtn');
                    if (install) install.onclick = () => resolve('retry');
                    if (compatibility) compatibility.onclick = () => resolve('compatibility');
                    if (cancel) cancel.onclick = () => resolve('cancel');
                });
                if (decision === 'retry') continue;
                setPandocModalVisible(false);
                return decision === 'compatibility';
            }
        }
    }

    async function ensurePandocForWordExport() {
        let response;
        try {
            response = await fetch('/api/runtime/pandoc/status');
            if (!response.ok) throw new Error('无法检查 Pandoc 状态。');
            const data = await response.json();
            const state = data.pandoc || {};
            if (state.available || state.status === 'ready') return true;
            if (['queued', 'downloading', 'verifying'].includes(state.status) && state.task_id) {
                resetPandocInstallModal();
                setPandocModalVisible(true);
                updatePandocInstallProgress(state);
                await pollPandocInstall(state.task_id);
                setPandocModalVisible(false);
                return true;
            }
            if (state.status === 'unsupported') {
                if (window.showToast) window.showToast('当前系统暂不支持自动安装 Pandoc，可使用兼容模式导出', 'warning');
            }
        } catch (error) {
            if (window.showToast) window.showToast(error.message || '无法检查 Word 公式组件', 'error');
            return false;
        }

        const decision = await waitForPandocDecision();
        if (decision === 'cancel') return false;
        if (decision === 'compatibility') return true;
        return installPandocAndContinue();
    }

    async function generateAndDownloadWord(payload) {
        try {
            const isExam19 = payload.paper_type === 'exam_19';
            if (window.showToast) {
                window.showToast(isExam19 ? '正在生成 Word 试卷及解析压缩包（正文+解析，不含答题卡）...' : '正在生成 Word 试卷及参考答案压缩包...', 'info');
            }
            const res = await fetch('/api/paper/export/word', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            if (!res.ok) {
                let message = 'Word 导出失败';
                try {
                    const data = await res.json();
                    message = data.message || message;
                } catch (e) {}
                if (window.showToast) window.showToast(message, 'error');
                return;
            }

            const blob = await res.blob();
            const rawTitle = (payload.title || '试卷').trim();
            const safeTitle = rawTitle.replace(/[/\\?%*:|"<>]/g, '_') || '试卷';
            const filename = `${safeTitle}_Word打包.zip`;
            const nativeCount = parseInt(res.headers.get('X-Word-Native-Formulas') || '0', 10);
            const fallbackCount = parseInt(res.headers.get('X-Word-Fallback-Formulas') || '0', 10);
            const failedCount = parseInt(res.headers.get('X-Word-Failed-Formulas') || '0', 10);

            if (typeof window.showSaveFilePicker === 'function') {
                try {
                    const handle = await window.showSaveFilePicker({
                        suggestedName: filename,
                        types: [{
                            description: 'Zip Archive',
                            accept: { 'application/zip': ['.zip'] }
                        }]
                    });
                    const writable = await handle.createWritable();
                    await writable.write(blob);
                    await writable.close();
                } catch (err) {
                    if (err && err.name === 'AbortError') return;
                    const url = URL.createObjectURL(blob);
                    const anchor = document.createElement('a');
                    anchor.href = url;
                    anchor.download = filename;
                    anchor.click();
                    URL.revokeObjectURL(url);
                }
            } else {
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement('a');
                anchor.href = url;
                anchor.download = filename;
                anchor.click();
                URL.revokeObjectURL(url);
            }

            if (window.showToast) {
                const baseTip = `Word 打包《${filename}》导出成功（含试卷正文与含答案解析两份文档）！`;
                if (failedCount > 0) {
                    window.showToast(`${baseTip}，有 ${failedCount} 处公式已用红字标出`, 'warning');
                } else if (fallbackCount > 0) {
                    window.showToast(`${baseTip}，${nativeCount} 处原生公式，${fallbackCount} 处图片保真公式`, 'warning');
                } else {
                    window.showToast(`${baseTip}，${nativeCount} 处公式均可直接编辑`, 'success');
                }
            }
        } catch (e) {
            if (window.showToast) window.showToast('Word 生成请求异常', 'error');
        }
    }

    window.exportPaperWord = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法导出 Word 试卷', 'warning');
            return;
        }
        const payload = {
            title: window.PaperStore.meta.title,
            subtitle: window.PaperStore.meta.subtitle,
            paper_type: window.PaperStore.meta.paper_type,
            show_notice: window.PaperStore.meta.show_notice !== false,
            show_secret: window.PaperStore.meta.show_secret !== false,
            questions: buildCartQuestionsPayload()
        };
        if (!await ensurePandocForWordExport()) return;
        await generateAndDownloadWord(payload);
    };

    window.exportPaperBundle = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法打包导出 LaTeX 资源包', 'warning');
            return;
        }

        try {
            if (window.showToast) window.showToast('正在打包 LaTeX 源码并编译全套 PDF 归档...', 'info');

            const cartQuestions = buildCartQuestionsPayload();

            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: cartQuestions
            };

            const res = await fetch('/api/paper/export/bundle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                const blob = await res.blob();
                const rawTitle = (window.PaperStore.meta.title || '试卷').trim();
                const safeTitle = rawTitle.replace(/[/\\?%*:|"<>]/g, '_') || '试卷';
                const filename = `${safeTitle}_LaTeX打包.zip`;

                if (typeof window.showSaveFilePicker === 'function') {
                    try {
                        const handle = await window.showSaveFilePicker({
                            suggestedName: filename,
                            types: [{
                                description: 'Zip Archive',
                                accept: { 'application/zip': ['.zip'] }
                            }]
                        });
                        const writable = await handle.createWritable();
                        await writable.write(blob);
                        await writable.close();
                        if (window.showToast) window.showToast(`LaTeX 打包归档已保存至指定目录`, 'success');
                        notifyMissingPaperImages(res);
                        notifyPaperExportNotes(res);
                        return;
                    } catch (err) {
                        if (err && err.name === 'AbortError') return;
                    }
                }

                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(url);
                if (window.showToast) window.showToast(`LaTeX 打包《${filename}》导出成功！`, 'success');
                notifyMissingPaperImages(res);
                notifyPaperExportNotes(res);
            } else {
                const errData = await res.json();
                if (window.showToast) window.showToast(errData.message || '导出失败', 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast('LaTeX 打包请求异常', 'error');
        }
    };

    window.savePaperToDb = async function () {
        const cart = window.PaperStore.cart;
        if (cart.length === 0) {
            if (window.showToast) window.showToast('卷面为空，无法保存试卷', 'warning');
            return;
        }

        try {
            const payload = {
                title: window.PaperStore.meta.title,
                subtitle: window.PaperStore.meta.subtitle,
                paper_type: window.PaperStore.meta.paper_type,
                show_notice: window.PaperStore.meta.show_notice !== false,
                show_secret: window.PaperStore.meta.show_secret !== false,
                questions: cart
            };

            const res = await fetch('/api/paper/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await res.json();
            if (data.status === 'success') {
                if (window.showToast) window.showToast('试卷已保存到数据库，题目引用次数已自动更新！', 'success');
            } else {
                if (window.showToast) window.showToast(data.message || '保存失败', 'error');
            }
        } catch (e) {
            if (window.showToast) window.showToast('保存试卷请求异常', 'error');
        }
    };

    // ----------------- Saved Papers Archive Library Modal -----------------
    window.closeSavedPapersModal = function () {
        const modal = document.getElementById('savedPapersModal');
        if (!modal) return;
        window.MathBankModal.close(modal);
        modal.remove();
    };

    window.openSavedPapersModal = async function () {
        let modal = document.getElementById('savedPapersModal');
        if (modal) {
            window.MathBankModal.close(modal);
            modal.remove();
        }

        modal = document.createElement('div');
        modal.id = 'savedPapersModal';
        modal.className = 'fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'savedPapersModalTitle');
        modal.innerHTML = `
            <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-3xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden font-sans">
                <!-- Header -->
                <div class="px-6 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between bg-slate-50/50 dark:bg-slate-800/50">
                    <div class="flex items-center space-x-2.5">
                        <div class="w-9 h-9 rounded-2xl bg-amber-500/10 text-amber-600 dark:text-amber-400 flex items-center justify-center font-bold text-lg">
                            <i class="fa-solid fa-folder-open"></i>
                        </div>
                        <div>
                            <h3 id="savedPapersModalTitle" class="font-bold text-slate-800 dark:text-slate-100 text-base">历史试卷归档库</h3>
                            <p class="text-xs text-slate-400">查看、一键载入还原或删除已保存的历史试卷记录</p>
                        </div>
                    </div>
                    <button type="button" data-modal-close onclick="closeSavedPapersModal()" aria-label="关闭历史试卷归档库" class="w-8 h-8 rounded-full hover:bg-slate-200/60 dark:hover:bg-slate-700 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 flex items-center justify-center transition-colors">
                        <i class="fa-solid fa-xmark text-sm"></i>
                    </button>
                </div>

                <!-- Body (Scrollable List) -->
                <div class="p-6 overflow-y-auto flex-1 space-y-3" id="savedPapersListContainer">
                    <div class="text-center py-12 text-slate-400 font-sans text-xs">
                        <i class="fa-solid fa-spinner fa-spin text-xl text-brand-500 mb-2 block"></i>
                        正在获取历史试卷列表...
                    </div>
                </div>

                <!-- Footer -->
                <div class="px-6 py-3.5 border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-800/50 flex items-center justify-between text-xs text-slate-400">
                    <span>共保存 <strong id="savedPaperTotalCount" class="text-slate-700 dark:text-slate-200">0</strong> 份历史试卷</span>
                    <button type="button" onclick="closeSavedPapersModal()" class="px-4 py-1.5 rounded-xl bg-slate-200 text-slate-700 hover:bg-slate-300 font-medium transition-colors dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700">
                        关闭窗口
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        window.MathBankModal.open(modal, { onEscape: window.closeSavedPapersModal });

        try {
            const res = await fetch('/api/papers');
            const data = await res.json();
            const container = document.getElementById('savedPapersListContainer');
            const countEl = document.getElementById('savedPaperTotalCount');

            if (data.status === 'success' && data.data) {
                const papers = data.data;
                if (countEl) countEl.textContent = papers.length;

                if (papers.length === 0) {
                    container.innerHTML = `
                        <div class="text-center py-16">
                            <div class="w-14 h-14 mx-auto rounded-3xl bg-slate-100 dark:bg-slate-800 text-slate-300 dark:text-slate-600 flex items-center justify-center text-2xl mb-3">
                                <i class="fa-solid fa-box-open"></i>
                            </div>
                            <p class="text-sm font-semibold text-slate-500 dark:text-slate-400">暂无保存的历史试卷</p>
                            <p class="text-xs text-slate-400 mt-1">在组卷工作台中挑选题目后点击“保存试卷”即可归档在此处</p>
                        </div>
                    `;
                    return;
                }

                const paperTypeMap = {
                    'exam_19': { label: '19题高考卷', color: 'bg-indigo-50 text-indigo-600 border-indigo-200 dark:bg-indigo-950/40 dark:border-indigo-800 dark:text-indigo-300' },
                    'exam': { label: '常规试卷', color: 'bg-emerald-50 text-emerald-600 border-emerald-200 dark:bg-emerald-950/40 dark:border-emerald-800 dark:text-emerald-300' },
                    'quiz': { label: '日常小练', color: 'bg-amber-50 text-amber-600 border-amber-200 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300' },
                    'handout': { label: '讲义/教案', color: 'bg-rose-50 text-rose-600 border-rose-200 dark:bg-rose-950/40 dark:border-rose-800 dark:text-rose-300' }
                };

                container.innerHTML = papers.map(p => {
                    const typeInfo = paperTypeMap[p.paper_type] || { label: '试卷', color: 'bg-slate-100 text-slate-600' };
                    const dateStr = p.created_at ? new Date(p.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未知时间';

                    return `
                        <div class="bg-slate-50/70 dark:bg-slate-800/40 border border-slate-200/80 dark:border-slate-700/60 rounded-2xl p-4 flex items-center justify-between hover:border-brand-200 dark:hover:border-brand-600 hover:shadow-md transition-all group">
                            <div class="flex-1 min-w-0 pr-4">
                                <div class="flex items-center space-x-2 mb-1">
                                    <span class="inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded-full border ${typeInfo.color}">
                                        ${typeInfo.label}
                                    </span>
                                    <h4 class="font-bold text-slate-800 dark:text-slate-100 text-sm truncate group-hover:text-brand-600 transition-colors">${escapeHtml(p.title)}</h4>
                                </div>
                                <div class="flex items-center space-x-4 text-xs text-slate-400">
                                    <span><i class="fa-solid fa-calculator text-[10px] mr-1 text-slate-400"></i>总分: <strong class="text-slate-600 dark:text-slate-300">${p.total_score}分</strong></span>
                                    <span><i class="fa-solid fa-list-check text-[10px] mr-1 text-slate-400"></i>题目数: <strong class="text-slate-600 dark:text-slate-300">${p.question_count}题</strong></span>
                                    <span><i class="fa-regular fa-clock text-[10px] mr-1 text-slate-400"></i>${dateStr}</span>
                                </div>
                                ${p.subtitle ? `<p class="text-xs text-slate-400 mt-1 truncate italic">备注: ${escapeHtml(p.subtitle)}</p>` : ''}
                            </div>
                            <div class="flex items-center space-x-2 shrink-0">
                                <button onclick="loadSavedPaper(${p.id})" class="px-3 py-1.5 rounded-xl bg-brand-500 hover:bg-brand-600 active:scale-95 text-white text-xs font-semibold shadow-sm transition-all flex items-center space-x-1" title="载入试卷至工作台">
                                    <i class="fa-solid fa-arrow-right-to-bracket text-[11px]"></i>
                                    <span>载入试卷</span>
                                </button>
                                <button onclick="quickExportPaperPdf(${p.id})" class="px-3 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 active:scale-95 text-white text-xs font-semibold shadow-sm transition-all flex items-center space-x-1" title="快速编译 PDF">
                                    <i class="fa-solid fa-file-pdf text-[11px]"></i>
                                    <span>PDF</span>
                                </button>
                                <button onclick="deleteSavedPaper(${p.id})" class="p-1.5 rounded-xl text-slate-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors" title="删除此保存试卷">
                                    <i class="fa-solid fa-trash-can text-xs"></i>
                                </button>
                            </div>
                        </div>
                    `;
                }).join('');
            }
        } catch (e) {
            console.error(e);
            const container = document.getElementById('savedPapersListContainer');
            if (container) {
                container.innerHTML = `<div class="text-center py-12 text-rose-500 text-xs">加载历史试卷失败: ${escapeHtml(e.message)}</div>`;
            }
        }
    };

    window.loadSavedPaper = async function (paperId) {
        try {
            if (window.showToast) window.showToast('正在载入试卷数据...', 'info');

            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;

                // Update metadata
                window.PaperStore.meta.title = paper.title || '未命名试卷';
                window.PaperStore.meta.subtitle = paper.subtitle || '';
                window.PaperStore.meta.paper_type = paper.paper_type || 'exam';
                window.PaperStore.meta.show_notice = paper.show_notice !== false;
                window.PaperStore.meta.show_secret = paper.show_secret !== false;

                // Rebuild cart & questionsMap
                window.PaperStore.cart = [];
                if (paper.questions && paper.questions.length > 0) {
                    paper.questions.forEach(item => {
                        const qObj = item.question;
                        window.PaperStore.questionsMap[qObj.id] = qObj;
                        window.PaperStore.cart.push({
                            id: qObj.id,
                            score: item.score || 5
                        });
                    });
                }

                // Close through the shared manager so background inert/aria state is restored.
                window.closeSavedPapersModal();

                // Re-render UI
                if (typeof window.renderPaperWorkspace === 'function') {
                    window.renderPaperWorkspace();
                }
                if (window.showToast) window.showToast(`已成功载入试卷: 《${paper.title}》`, 'success');
            } else {
                if (window.showToast) window.showToast(data.message || '载入试卷失败', 'error');
            }
        } catch (e) {
            console.error('Load paper failed:', e);
            if (window.showToast) window.showToast('载入试卷请求失败', 'error');
        }
    };

    window.deleteSavedPaper = function (paperId) {
        return window.openDeletePaperConfirm(paperId);
    };

    // 删卷确认弹窗：统一走 MathBankModal（焦点陷阱/Esc/背景隔离/焦点恢复），
    // 裸 confirm 既给不出「含 N 题、题目保留」的实数据，也不符合项目弹窗规范。
    // 文案与按钮一律 createElement + textContent 构建，不拼 innerHTML 交互。
    let deleteConfirmState = null;

    function closeDeletePaperConfirm() {
        const modal = document.getElementById('deletePaperConfirmModal');
        if (!modal) return;
        window.MathBankModal.close(modal);
        modal.remove();
        deleteConfirmState = null;
    }

    window.openDeletePaperConfirm = async function (paperId) {
        if (deleteConfirmState && deleteConfirmState.id === paperId) return;
        let preview = null;
        try {
            const res = await fetch(`/api/papers/${paperId}/delete-preview`);
            const data = await res.json();
            if (data && data.status === 'success') preview = data.data;
        } catch (e) { /* 预检失败也要能删，退化成通用文案 */ }

        const modal = document.createElement('div');
        modal.id = 'deletePaperConfirmModal';
        modal.className = 'fixed inset-0 z-[97] flex items-center justify-center bg-slate-950/55 p-4 backdrop-blur-sm';
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-hidden', 'false');
        modal.setAttribute('aria-labelledby', 'deletePaperConfirmTitle');

        const surface = document.createElement('div');
        surface.className = 'w-full max-w-md rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl dark:border-slate-600 dark:bg-slate-800';
        modal.appendChild(surface);

        const title = document.createElement('h3');
        title.id = 'deletePaperConfirmTitle';
        title.tabIndex = -1;
        title.textContent = '删除这份试卷？';
        title.className = 'text-base font-bold text-slate-800 dark:text-slate-100';
        surface.appendChild(title);

        const body = document.createElement('div');
        body.className = 'mt-3 space-y-2 text-xs leading-relaxed text-slate-600 dark:text-slate-300';
        const nameLine = document.createElement('p');
        nameLine.textContent = preview ? `《${preview.paper_name}》` : '该历史试卷记录';
        nameLine.className = 'font-bold text-slate-800 dark:text-slate-100';
        body.appendChild(nameLine);
        const countLine = document.createElement('p');
        // 这条文案是「题目不会被级联删」的唯一口头承诺，必须真的加粗：本弹窗全程用
        // createElement + textContent 构建（见第 6 节净化白名单规则），而 Markdown 的
        // 星号强调在 textContent 上不会生效——实测弹窗里会直接印出一对字面星号。
        // 强调一律用 <strong> 元素。
        if (preview) {
            countLine.appendChild(document.createTextNode(
                `该试卷包含 ${preview.question_count} 道题。删除试卷`));
            const emphasis = document.createElement('strong');
            emphasis.className = 'font-bold text-slate-800 dark:text-slate-100';
            emphasis.textContent = '不会影响题库中的题目';
            countLine.appendChild(emphasis);
            countLine.appendChild(document.createTextNode('——题目全部保留，只是不再属于这份卷。'));
        } else {
            countLine.textContent = '删除试卷不会影响题库中的题目——题目全部保留，只是不再属于这份卷。';
        }
        body.appendChild(countLine);
        const warnLine = document.createElement('p');
        warnLine.textContent = '试卷记录本身删除后不可撤销（系统会先自动落一份完整备份 ZIP，可用它恢复）。';
        warnLine.className = 'text-slate-400 dark:text-slate-400';
        body.appendChild(warnLine);
        surface.appendChild(body);

        const errLine = document.createElement('p');
        errLine.id = 'deletePaperConfirmError';
        errLine.setAttribute('role', 'alert');
        errLine.className = 'mt-2 hidden text-xs font-bold text-red-500';
        surface.appendChild(errLine);

        const foot = document.createElement('div');
        foot.className = 'mt-4 flex items-center justify-end gap-2';
        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.textContent = '取消';
        cancelBtn.className = 'min-h-11 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700';
        cancelBtn.addEventListener('click', () => closeDeletePaperConfirm());
        foot.appendChild(cancelBtn);
        const okBtn = document.createElement('button');
        okBtn.type = 'button';
        okBtn.id = 'deletePaperConfirmOk';
        okBtn.textContent = '确认删除试卷';
        okBtn.className = 'min-h-11 rounded-xl bg-rose-600 px-4 py-2 text-xs font-bold text-white hover:bg-rose-700 disabled:opacity-50';
        okBtn.addEventListener('click', () => runDeletePaper(paperId));
        foot.appendChild(okBtn);
        surface.appendChild(foot);

        document.body.appendChild(modal);
        deleteConfirmState = { id: paperId };
        window.MathBankModal.open(modal, {
            initialFocus: title,
            onEscape: () => closeDeletePaperConfirm(),
        });
    };

    async function runDeletePaper(paperId) {
        const btn = document.getElementById('deletePaperConfirmOk');
        const errEl = document.getElementById('deletePaperConfirmError');
        if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
        if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
        try {
            const res = await fetch(`/api/papers/${paperId}`, { method: 'DELETE' });
            const data = await res.json();
            if (data.status === 'success') {
                closeDeletePaperConfirm();
                if (window.showToast) window.showToast('历史试卷已删除（题目保留在题库）', 'success');
                window.openSavedPapersModal();
            } else {
                // 失败保持弹窗打开，用户能看到原因并决定重试或放弃
                if (errEl) {
                    errEl.textContent = '删除失败：' + (data.message || ('HTTP ' + res.status));
                    errEl.classList.remove('hidden');
                }
                if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
            }
        } catch (e) {
            if (errEl) {
                errEl.textContent = '删除请求失败：' + e.message;
                errEl.classList.remove('hidden');
            }
            if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
        }
    }

    window.quickExportPaperPdf = async function (paperId) {
        try {
            const res = await fetch(`/api/papers/${paperId}`);
            const data = await res.json();
            if (data.status === 'success' && data.data) {
                const paper = data.data;
                const cartQuestions = (paper.questions || []).map(item => ({
                    id: item.id,
                    score: item.score,
                    figure_align: getQuestionFigAlign(item.question)
                }));

                const tab = window.open('', '_blank');
                setPdfTabLoadingState(tab, '📄 试卷 PDF 编译中', '📄', `正在在线静默编译《${paper.title}》高清 PDF...`);

                const pdfRes = await fetch('/api/paper/export/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: paper.title,
                        subtitle: paper.subtitle,
                        paper_type: paper.paper_type,
                        target: 'paper',
                        questions: cartQuestions
                    })
                });

                if (pdfRes.ok && pdfRes.headers.get('content-type')?.includes('application/pdf')) {
                    const blob = await pdfRes.blob();
                    const url = URL.createObjectURL(blob);
                    if (tab && !tab.closed) {
                        tab.location.href = url;
                    }
                    notifyMissingPaperImages(pdfRes);
                    notifyPaperExportNotes(pdfRes);
                } else {
                    let errorData = {};
                    try { errorData = await pdfRes.json(); } catch (e) {}
                    if (tab && !tab.closed) {
                        setPdfTabErrorState(tab, '试卷 PDF 编译失败', errorData.diagnostic, errorData.message || '编译 PDF 失败');
                    }
                    if (window.showToast) window.showToast(errorData.message || '编译 PDF 失败', 'error');
                }
            }
        } catch (e) {
            console.error('Quick export PDF failed:', e);
        }
    };

    // escapeHtml is provided by the shared api.js layer.

    function getQuestionTypeCn(type) {
        if (!type) return '题目';
        if (window.systemMetadata && Array.isArray(window.systemMetadata.question_types)) {
            const found = window.systemMetadata.question_types.find(t => t.value === type);
            if (found) return found.label;
        }
        const map = {
            single_choice: '单选题',
            multi_choice: '多选题',
            fill_in_blank: '填空题',
            detailed_answer: '解答题'
        };
        return map[type] || type;
    }

    window.setFigureAlign = function (qid, alignVal) {
        qid = parseInt(qid, 10);
        if (!qid) return;

        // 1. Optimistically update local memory store
        if (window.PaperStore.questionsMap[qid]) {
            window.PaperStore.questionsMap[qid].figure_align = alignVal;
            window.PaperStore.questionsMap[qid].custom_figure_align = alignVal;
        }

        // Close popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        // 2. Optimistically re-render UI IMMEDIATELY for instant visual feedback!
        if (typeof window.renderPart3QuestionStream === 'function') {
            window.renderPart3QuestionStream();
        }
        if (typeof window.renderPaperCanvas === 'function') {
            window.renderPaperCanvas();
        }

        // 3. Send POST API request to persist in DB (api.js monkey-patch automatically attaches X-Local-Token)
        const formData = new FormData();
        formData.append('figure_align', alignVal);

        fetch(`/api/questions/${qid}/figure_align`, {
            method: 'POST',
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                const labelMap = { 'right': '题干右侧', 'center': '下方居中', 'bottom_right': '下方居右' };
                const q = window.PaperStore.questionsMap[qid];
                const seqNum = (q && q.seq_num !== undefined) ? q.seq_num : qid;
                if (window.showToast) window.showToast(`已调整题目 #${seqNum} 插图排版为：${labelMap[alignVal] || alignVal}`, 'success');
            }
        })
        .catch(err => {
            console.error('Update figure_align failed:', err);
        });
    };

    window.showFigureAlignPopover = function (event, qid) {
        event.preventDefault();
        event.stopPropagation();

        qid = parseInt(qid, 10);
        const q = window.PaperStore.questionsMap[qid] || {};
        const currentAlign = getQuestionFigAlign(q);

        // Remove existing popover
        const existingPopover = document.getElementById('figureAlignPopoverMenu');
        if (existingPopover) existingPopover.remove();

        const popover = document.createElement('div');
        popover.id = 'figureAlignPopoverMenu';
        popover.className = 'fixed z-50 bg-white/95 backdrop-blur-md rounded-2xl border border-slate-200 shadow-xl p-2 font-sans text-xs flex flex-col space-y-1 animate-in fade-in zoom-in-95 duration-150 dark:bg-slate-800 dark:border-slate-700 text-slate-800 dark:text-slate-100';

        // Position popover near mouse cursor
        let left = event.clientX + 5;
        let top = event.clientY + 5;

        // Keep inside viewport bounds
        if (left + 170 > window.innerWidth) left = window.innerWidth - 180;
        if (top + 150 > window.innerHeight) top = window.innerHeight - 160;

        popover.style.left = `${left}px`;
        popover.style.top = `${top}px`;

        popover.innerHTML = `
            <div class="px-2 py-1 text-[11px] font-bold text-slate-400 border-b border-slate-100 dark:border-slate-700 flex items-center justify-between">
                <span><i class="fa-solid fa-sliders text-brand-500 mr-1"></i> 调整插图排版位置</span>
                <button onclick="document.getElementById('figureAlignPopoverMenu').remove()" class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <button onclick="window.setFigureAlign(${qid}, 'right')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干右侧 (默认)</span>
                ${currentAlign === 'right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'center')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'center' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-center text-xs mr-2 text-brand-500"></i> 题干下方居中</span>
                ${currentAlign === 'center' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
            <button onclick="window.setFigureAlign(${qid}, 'bottom_right')" class="w-full text-left px-3 py-1.5 rounded-xl hover:bg-brand-50 hover:text-brand-600 transition-colors flex items-center justify-between ${currentAlign === 'bottom_right' ? 'bg-brand-50 font-bold text-brand-600' : ''}">
                <span><i class="fa-solid fa-align-right text-xs mr-2 text-brand-500"></i> 题干下方居右</span>
                ${currentAlign === 'bottom_right' ? '<i class="fa-solid fa-check text-xs"></i>' : ''}
            </button>
        `;

        document.body.appendChild(popover);

        // Click outside listener
        const closeHandler = function (e) {
            if (!popover.contains(e.target)) {
                popover.remove();
                document.removeEventListener('click', closeHandler);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', closeHandler);
        }, 50);
    };

    function handleFigureAlignImageEvent(event) {
        const image = event.target && event.target.closest
            ? event.target.closest('img[data-figure-align-qid]')
            : null;
        if (!image) return;
        const qid = parseInt(image.dataset.figureAlignQid, 10);
        if (!qid) return;
        event.preventDefault();
        event.stopPropagation();
        window.showFigureAlignPopover(event, qid);
    }

    document.addEventListener('click', handleFigureAlignImageEvent);
    document.addEventListener('contextmenu', handleFigureAlignImageEvent);

    function splitChoiceContentForPaperPreview(raw) {
        const source = String(raw || '');
        const beginMarker = '\\begin{choices}';
        const endMarker = '\\end{choices}';
        const beginIndex = source.indexOf(beginMarker);
        if (beginIndex < 0) {
            return { stemRaw: source, choicesRaw: '' };
        }

        const endIndex = source.indexOf(endMarker, beginIndex + beginMarker.length);
        if (endIndex < 0) {
            return { stemRaw: source, choicesRaw: '' };
        }

        const choicesEnd = endIndex + endMarker.length;
        return {
            stemRaw: `${source.slice(0, beginIndex)}\n${source.slice(choicesEnd)}`.trim(),
            choicesRaw: source.slice(beginIndex, choicesEnd).trim()
        };
    }

    function shouldPreserveInlinePaperImages(raw) {
        const source = String(raw || '');
        const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
        const matches = [...source.matchAll(imagePattern)];
        if (matches.length === 0) return false;

        // A trailing image (or a trailing cluster of images) is the legacy
        // detachable figure layout controlled by figure_align.  As soon as
        // authored content continues after the first image, the image is an
        // inline document anchor.  This includes images inside tabular cells,
        // between sub-questions, and before captions or explanatory text.
        const firstImageIndex = matches[0].index || 0;
        const trailingContent = source.slice(firstImageIndex)
            .replace(imagePattern, '')
            .trim();
        return trailingContent.length > 0;
    }

    function formatQuestionContentHtml(raw, qid = null, figAlign = 'right', embedInSolSpace = false, showControls = true) {
        if (!raw) return embedInSolSpace ? { stemHtml: '', imgHtml: null } : '';
        let html = String(raw).trim();
        figAlign = figAlign || 'right';

        // ⚠️ 这里**不**再剥题干末尾的 \paren / 空括号，也不自己画作答括号：
        // 括号的唯一渲染出口是 editor.js 的 transformExamZhParenForPreview
        // （在 parseMarkdownWithMath 里被调用）。在这之前剥掉，transform 就无
        // 货可画（0 个括号）；在这之后补画，就和 transform 重复（2 个括号）。

        if (shouldPreserveInlinePaperImages(html)) {
            const inlineHtml = typeof window.parseMarkdownWithMath === 'function'
                ? window.parseMarkdownWithMath(html)
                : window.MathBankSafe.sanitizeRichHtml(html);
            if (embedInSolSpace) {
                return {
                    stemHtml: `<div>${inlineHtml}</div>`,
                    imgHtml: null,
                    figAlign: figAlign
                };
            }
            return inlineHtml;
        }

        // 1. Extract ALL Markdown image syntaxes ![](/static/uploads/xxx.png) BEFORE KaTeX processing
        const imgSrcList = [];
        const imgMatches = [...html.matchAll(/!\[.*?\]\(([^)]+)\)/g)];
        imgMatches.forEach(m => {
            const safeSrc = window.MathBankSafe.safeImageUrl(m[1]);
            if (safeSrc && !imgSrcList.includes(safeSrc)) imgSrcList.push(safeSrc);
        });
        html = html.replace(/!\[.*?\]\(([^)]+)\)/g, '').trim();
        
        // 2. Process LaTeX formulas, \underline, choices environment & LaTeX standard paragraphs via preprocessFormulaForKaTeX
        if (typeof window.parseMarkdownWithMath === 'function') {
            html = window.parseMarkdownWithMath(html);
        } else {
            html = window.MathBankSafe.sanitizeRichHtml(html);
        }

        const stemText = html;

        if (imgSrcList.length > 0) {
            // 如果存在多张插图且原设定为右侧，默认自动优化调整为下方居中 (center) 展示
            const effectiveAlign = (imgSrcList.length > 1 && figAlign === 'right') ? 'center' : (figAlign || 'right');
            const alignLabelMap = {
                'right': '题干右侧',
                'center': '下方居中',
                'bottom_right': '下方居右'
            };
            const currentLabel = alignLabelMap[effectiveAlign] || '下方居中';
            const iconClass = effectiveAlign === 'center' ? 'fa-align-center' : 'fa-align-right';
            const qidAttr = parseInt(qid, 10) || 0;
            const countTag = imgSrcList.length > 1 ? ` (${imgSrcList.length}图)` : '';

            const imgClass = showControls 
                ? `${imgSrcList.length > 1 ? 'max-w-[150px] max-h-[140px]' : 'max-w-[200px] max-h-[170px]'} object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block`
                : `${imgSrcList.length > 1 ? 'max-w-[150px] max-h-[140px]' : 'max-w-[200px] max-h-[170px]'} object-contain rounded-lg border border-slate-200 shadow-sm inline-block`;

            const imgsHtml = imgSrcList.map((src, idx) => {
                const controlAttrs = showControls
                    ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                    : '';
                return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${imgClass}" ${controlAttrs} loading="lazy" decoding="async">`;
            }).join('');

            const btnHtml = showControls ? `
                <div class="mt-1 ${effectiveAlign === 'center' ? 'text-center' : 'text-right'}">
                    <button onclick="event.stopPropagation(); window.showFigureAlignPopover(event, ${qidAttr})" class="paper-fig-align-btn inline-flex items-center text-[10px] font-sans text-brand-700 bg-brand-50 hover:bg-brand-100 border border-brand-200/80 rounded-md px-1.5 py-0.5 transition-colors shadow-sm">
                        <i class="fa-solid ${iconClass} text-[9px] mr-1 text-brand-500"></i> ${currentLabel}${countTag} <i class="fa-solid fa-chevron-down text-[8px] ml-1 opacity-70"></i>
                    </button>
                </div>
            ` : '';

            const imgControlHtml = `
                <div class="inline-block relative group/fig">
                    <div class="flex flex-wrap items-center ${effectiveAlign === 'center' ? 'justify-center' : 'justify-end'} gap-2">
                        ${imgsHtml}
                    </div>
                    ${btnHtml}
                </div>
            `;

            if (embedInSolSpace && (effectiveAlign === 'center' || effectiveAlign === 'bottom_right')) {
                return {
                    stemHtml: `<div>${stemText}</div>`,
                    imgHtml: imgControlHtml,
                    figAlign: effectiveAlign
                };
            }

            if (effectiveAlign === 'center') {
                return `<div>${stemText}</div><div class="my-2 text-center">${imgControlHtml}</div>`;
            } else if (effectiveAlign === 'bottom_right') {
                return `<div>${stemText}</div><div class="my-2 text-right">${imgControlHtml}</div>`;
            } else { // default 'right': Give text 70%+ dominant width, constrain figure container to 160px
                const rightImgsHtml = imgSrcList.map((src, idx) => {
                    const controlAttrs = showControls
                        ? `data-figure-align-qid="${qidAttr}" title="点击或右击可切换插图排版位置 (图${idx + 1} 当前: ${currentLabel})"`
                        : '';
                    const rightImgClass = showControls
                        ? `${imgSrcList.length > 1 ? 'max-w-[125px] max-h-[115px]' : 'max-w-[155px] max-h-[135px]'} object-contain rounded-lg border border-slate-200 shadow-sm cursor-pointer hover:ring-2 hover:ring-brand-500 hover:scale-[1.02] transition-all inline-block`
                        : `${imgSrcList.length > 1 ? 'max-w-[125px] max-h-[115px]' : 'max-w-[155px] max-h-[135px]'} object-contain rounded-lg border border-slate-200 shadow-sm inline-block`;
                    return `<img src="${window.MathBankSafe.escapeAttribute(src)}" alt="题目配图 ${idx + 1}" class="${rightImgClass}" ${controlAttrs} loading="lazy" decoding="async">`;
                }).join('');

                const rightImgControlHtml = `
                    <div class="inline-block relative group/fig">
                        <div class="flex flex-wrap items-center justify-end gap-1.5">
                            ${rightImgsHtml}
                        </div>
                        ${btnHtml}
                    </div>
                `;

                return `
                    <div class="flex items-start justify-between gap-3 my-1">
                        <div class="flex-1 min-w-0 pr-1" style="max-width: calc(100% - 170px);">${stemText}</div>
                        <div class="shrink-0 text-right" style="width: 160px; max-width: 160px;">${rightImgControlHtml}</div>
                    </div>
                `;
            }
        }
        
        if (embedInSolSpace) {
            return { stemHtml: stemText, imgHtml: null, figAlign: figAlign };
        }

        return stemText;
    }

    // Init on DOMContentLoaded
    document.addEventListener('DOMContentLoaded', function () {
        loadStateFromStorage();
        updateCartBadges();

        // Restore active workspace if same server instance run (page refresh / tab re-open)
        const currentServerId = window.__serverInstanceId || '';
        let savedServerId = '';
        let savedWorkspace = 'bank';
        try {
            savedServerId = localStorage.getItem('mathbank_server_instance_id') || '';
            savedWorkspace = localStorage.getItem('mathbank_active_workspace') || 'bank';
        } catch (e) { }

        if (currentServerId && savedServerId === currentServerId) {
            if (savedWorkspace === 'paper') {
                if (typeof window.selectWorkspace === 'function') {
                    window.selectWorkspace('paper', '组卷排版工作台');
                }
            }
        } else {
            // Fresh server startup (.command / .bat re-launch) -> reset to bank studio default
            try {
                localStorage.setItem('mathbank_active_workspace', 'bank');
                if (currentServerId) {
                    localStorage.setItem('mathbank_server_instance_id', currentServerId);
                }
            } catch (e) { }
        }
        document.documentElement.classList.remove('init-ws-paper');
    });

})();
