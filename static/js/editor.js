

        // 根据考点名称查找L1-L4层次
        
        // 本地题库分页状态
        let currentBankPage = 1;
        let currentDraftPage = 1;
        const PAGE_LIMIT = 20;
        let bankQuestionsLoadSequence = 0;
        let bankQuestionsRetryTimer = null;
        let bankQuestionsLoadController = null;

function findTopicLevel(comp, chap, know, topicName) {
            if (!topicName || !window.categoryTree) return '';
            try {
                const section = findEditorSectionByName(comp, chap, know);
                if (section && Array.isArray(section.topics)) {
                    for (const t of section.topics) {
                        const name = typeof t === 'object' ? (t.name || '') : String(t);
                        if (name === topicName && typeof t === 'object' && t.level) {
                            return t.level;
                        }
                    }
                }
            } catch(e) {}
            return '';
        }

        // 显示考点的L1-L4标签
        function updateTopicLevelBadge() {
            const badge = document.getElementById('editTopicLevel');
            const topicInput = document.getElementById('editTopic');
            if (!badge || !topicInput) return;
            const comp = document.getElementById('editCompulsory') ? document.getElementById('editCompulsory').value : '';
            const chap = document.getElementById('editChapter') ? document.getElementById('editChapter').value : '';
            const know = document.getElementById('editKnowledge') ? document.getElementById('editKnowledge').value : '';
            const level = findTopicLevel(comp, chap, know, topicInput.value);
            if (level) {
                const colors = {
                    'L1': 'bg-green-100 text-green-700 border-green-300',
                    'L2': 'bg-blue-100 text-blue-700 border-blue-300',
                    'L3': 'bg-orange-100 text-orange-700 border-orange-300',
                    'L4': 'bg-red-100 text-red-700 border-red-300'
                };
                badge.textContent = level;
                badge.className = 'absolute inset-y-0 right-2 flex items-center text-[9px] font-bold rounded-full px-1.5 py-0.5 border ' + (colors[level] || 'bg-slate-100 text-slate-600 border-slate-300');
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }
        window.updateTopicLevelBadge = updateTopicLevelBadge;

        // 考点输入变化时更新level标签
        document.addEventListener('input', function(e) {
            if (e.target && e.target.id === 'editTopic') {
                updateTopicLevelBadge();
            }
        });

        function initResizers() {
            const preview = document.getElementById('previewSection');
            const resizer2 = document.getElementById('resizer-2');
            const mainContainer = document.querySelector('main');
            let isResizingRight = false;

            if (resizer2) {
                resizer2.addEventListener('mousedown', function(e) {
                    e.preventDefault();
                    isResizingRight = true;
                    document.body.style.cursor = 'col-resize';
                    document.body.classList.add('select-none');
                });
            }

            // 命名函数 + 松手即摘：全局 mousemove 监听器若常驻，会在弹窗打开期间
            // 继续每帧触发（getBoundingClientRect 强制布局），放大输入节流的影响面。
            function onResizerMouseMove(e) {
                if (!isResizingRight) return;
                const containerRect = mainContainer.getBoundingClientRect();
                let newWidth = containerRect.right - e.clientX;
                if (newWidth < 45) {
                    newWidth = 0;
                    preview.style.width = '0px';
                    preview.style.minWidth = '0px';
                    preview.style.borderLeftWidth = '0px';
                } else {
                    preview.style.borderLeftWidth = '1px';
                    if (newWidth > containerRect.width * 0.5) {
                        newWidth = containerRect.width * 0.5;
                    }
                    preview.style.width = newWidth + 'px';
                }
            }
            document.addEventListener('mousemove', onResizerMouseMove);

            function onResizerMouseUp() {
                if (isResizingRight) {
                    isResizingRight = false;
                    document.body.style.cursor = '';
                    document.body.classList.remove('select-none');
                    window.dispatchEvent(new Event('resize'));
                }
                document.removeEventListener('mousemove', onResizerMouseMove);
            }
            document.addEventListener('mouseup', onResizerMouseUp);
        }

        // Save callbacks may arrive after the user has typed more text. Accepting
        // an explicit request snapshot keeps those later edits dirty instead of
        // accidentally treating the current DOM as the server-confirmed state.
        function backupEditorState(id = null, draftId = null, requestSnapshot = null) {
            const snapshot = requestSnapshot || {
                content: document.getElementById('editContent').value,
                answer_markdown: document.getElementById('editAnswerMarkdown').value,
                review: document.getElementById('editReview').value,
                question_type: document.getElementById('editQType').value,
                difficulty: document.getElementById('editDifficulty').value,
                source: document.getElementById('editSource').value,
                paper_category: document.getElementById('editPaperCategory') ? document.getElementById('editPaperCategory').value : '',
                category_compulsory: document.getElementById('editCompulsory').value,
                category_chapter: document.getElementById('editChapter').value,
                category_knowledge: document.getElementById('editKnowledge').value,
                applicable_grade: document.getElementById('editApplicableGrade') ? document.getElementById('editApplicableGrade').value : '',
                topic: document.getElementById('editTopic') ? document.getElementById('editTopic').value : '',
                related_question_id: document.getElementById('editRelatedQuestion').value,
                image_paths: JSON.stringify(Array.from(new Set([
                    ...uploadedImages,
                    ...TikzState.assetImagePaths()
                ]))),
                tikz_code: TikzState.contentAssets[0] ? TikzState.contentAssets[0].tikz_code : '',
                tikz_reference_image_path: TikzState.contentAssets[0]
                    ? (TikzState.contentAssets[0].reference_image_path || '')
                    : '',
                content_tikz_assets: JSON.stringify(TikzState.contentAssets),
                answer_tikz_assets: JSON.stringify(TikzState.answerAssets),
                tags: document.getElementById('editTags') ? document.getElementById('editTags').value : '',
                ...(window.MathBankExtendedMeta ? window.MathBankExtendedMeta.read() : {})
            };
            originalQuestionState = {
                id: id,
                draftId: draftId,
                content: snapshot.content,
                answer_markdown: snapshot.answer_markdown,
                review: snapshot.review,
                question_type: snapshot.question_type,
                difficulty: snapshot.difficulty,
                source: snapshot.source,
                category_compulsory: snapshot.category_compulsory,
                category_chapter: snapshot.category_chapter,
                category_knowledge: snapshot.category_knowledge,
                applicable_grade: snapshot.applicable_grade || '',
                topic: snapshot.topic || '',
                related_question_id: snapshot.related_question_id || '',
                image_paths: snapshot.image_paths,
                tikz_code: snapshot.tikz_code || '',
                tikz_reference_image_path: snapshot.tikz_reference_image_path || '',
                content_tikz_assets: snapshot.content_tikz_assets || '[]',
                answer_tikz_assets: snapshot.answer_tikz_assets || '[]',
                tags: snapshot.tags,
                ...(window.MathBankExtendedMeta ? window.MathBankExtendedMeta.read() : {})
            };
        }
        window.backupEditorState = backupEditorState;

        // 考试阶段联动：仅校考题(xiaokao)可编辑
        function syncExamStageAvailability() {
            const catSelect = document.getElementById('editPaperCategory');
            const stageSelect = document.getElementById('editExamStage');
            if (!catSelect || !stageSelect) return;
            const enabled = catSelect.value === 'xiaokao';
            stageSelect.disabled = !enabled;
            if (!enabled && stageSelect.value) {
                stageSelect.value = '';
            }
        }
        window.syncExamStageAvailability = syncExamStageAvailability;

        // 守卫：极简/测试 DOM 可能没有 addEventListener，避免顶层副作用抛错。
        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
            document.addEventListener('DOMContentLoaded', function() {
                const catSelect = document.getElementById('editPaperCategory');
                if (catSelect && typeof catSelect.addEventListener === 'function') {
                    catSelect.addEventListener('change', syncExamStageAvailability);
                }
                syncExamStageAvailability();

                // 单题录入界面：左右双栏（左栏题干/解析编辑，右栏实时预览）。
                // 编辑框即上传窗口，OCR/配图面板已就位于面板内，这里仅做胶囊样式化。
                setTimeout(function() {
                    // 题干 OCR 面板与快捷公式面板常显（编辑框即上传入口，不再做 tab 切换）
                    var contentOcrPane = document.getElementById('contentTabContent-ocr');
                    if (contentOcrPane) contentOcrPane.classList.remove('hidden');
                    var manualPane = document.getElementById('contentTabContent-manual');
                    if (manualPane) manualPane.classList.remove('hidden');

                    // 题干区"新增绘图"按钮 + 题干 TikZ 资产面板
                    var contentTikzBtn = document.getElementById('openContentTikzWorkbenchBtn');
                    var contentTikzPanel = document.getElementById('contentTikzAssetsPanel');
                    // 解答区"新增绘图"按钮 + 解答 TikZ 资产面板
                    var answerTikzBtn = document.getElementById('openAnswerTikzWorkbenchBtn');
                    var answerTikzPanel = document.getElementById('answerTikzAssetsPanel');
                    // 统一胶囊按钮的绝对宽高
                    var HEADER_ITEM_W = '72px';
                    var HEADER_ITEM_H = '44px';
                    var HEADER_ITEM_GAP = '4px';
                    var HEADER_BG = '#eef2ff';
                    var HEADER_COLOR = '#4f46e5';
                    var HEADER_BORDER = '#c7d2fe';
                    var HEADER_ACTIVE_BG = '#4338ca';
                    var HEADER_ACTIVE_COLOR = '#ffffff';
                    var HEADER_ACTIVE_BORDER = '#4338ca';

                    function styleHeaderItem(el, opts) {
                        if (!el) return;
                        opts = opts || {};
                        el.style.boxSizing = 'border-box';
                        el.style.display = 'inline-flex';
                        el.style.flex = '0 0 auto';
                        el.style.flexDirection = 'column';
                        el.style.alignItems = 'center';
                        el.style.justifyContent = 'center';
                        el.style.width = HEADER_ITEM_W;
                        el.style.height = HEADER_ITEM_H;
                        el.style.minWidth = '0';
                        el.style.minHeight = '0';
                        el.style.padding = '0 3px';
                        el.style.fontSize = '10px';
                        el.style.lineHeight = '1.15';
                        el.style.borderRadius = '8px';
                        el.style.border = '1px solid ' + HEADER_BORDER;
                        el.style.background = HEADER_BG;
                        el.style.color = HEADER_COLOR;
                        el.style.fontWeight = '500';
                        el.style.textAlign = 'center';
                        el.style.whiteSpace = opts.wrap ? 'pre-line' : 'nowrap';
                        el.style.overflow = 'hidden';
                        el.style.cursor = opts.cursor || 'default';
                        el.style.userSelect = 'none';
                        var ic = el.querySelector('i');
                        if (ic) {
                            ic.style.fontSize = '10px';
                            ic.style.lineHeight = '1';
                            ic.style.flexShrink = '0';
                            ic.style.marginBottom = '1px';
                        }
                    }

                    // 新增绘图按钮改成"新增 TikZ 绘图"，与禁用按钮同尺寸
                    function setTikzBtnContent(btn) {
                        if (!btn) return;
                        btn.innerHTML = '<i class="fa-solid fa-pen-ruler"></i><span>新增\nTikZ绘图</span>';
                        styleHeaderItem(btn, { wrap: true, cursor: 'pointer' });
                    }
                    setTikzBtnContent(contentTikzBtn);
                    setTikzBtnContent(answerTikzBtn);

                    // TikZ 资产面板随面板常显
                    if (contentTikzPanel) contentTikzPanel.classList.remove('hidden');
                    if (answerTikzPanel) answerTikzPanel.classList.remove('hidden');
                }, 100);
            });
        }

        function editorMatchesBackupSnapshot(snapshot) {
            if (!snapshot) return false;
            const currentContent = document.getElementById('editContent').value;
            const currentAnswer = document.getElementById('editAnswerMarkdown').value;
            const currentReview = document.getElementById('editReview').value;
            const currentType = document.getElementById('editQType').value;
            const currentDifficulty = document.getElementById('editDifficulty').value;
            const currentSource = document.getElementById('editSource').value;
            const currentComp = document.getElementById('editCompulsory').value;
            const currentChap = document.getElementById('editChapter').value;
            const currentKnow = document.getElementById('editKnowledge').value;
            const currentApplicableGrade = document.getElementById('editApplicableGrade') ? document.getElementById('editApplicableGrade').value : '';
            const currentTopic = document.getElementById('editTopic') ? document.getElementById('editTopic').value : '';
            const currentRelatedQuestionId = document.getElementById('editRelatedQuestion').value;
            const currentImages = JSON.stringify(uploadedImages);
            const currentTikzCode = TikzState.contentAssets[0]
                ? TikzState.contentAssets[0].tikz_code
                : '';
            const currentTikzReferencePath = TikzState.contentAssets[0]
                ? (TikzState.contentAssets[0].reference_image_path || '')
                : '';
            const currentContentTikzAssets = JSON.stringify(TikzState.contentAssets);
            const currentAnswerTikzAssets = JSON.stringify(TikzState.answerAssets);
            const currentTags = document.getElementById('editTags') ? document.getElementById('editTags').value : '';
            const currentExtraTopics = JSON.stringify(
                (typeof window.collectExtraTopics === 'function' ? window.collectExtraTopics() : [])
            );
            // 扩展字段统一比对：字段增删只改 import.js 的 EXTENDED_META_FIELDS。
            const currentMeta = (window.MathBankExtendedMeta ? window.MathBankExtendedMeta.read() : {});
            const metaMatches = Object.keys(currentMeta).every(function (key) {
                const stored = snapshot[key];
                return currentMeta[key] === ((stored === null || stored === undefined) ? '' : String(stored));
            });

            return currentContent === snapshot.content &&
                   currentAnswer === snapshot.answer_markdown &&
                   currentReview === snapshot.review &&
                   currentType === snapshot.question_type &&
                   currentDifficulty === snapshot.difficulty &&
                   currentSource === snapshot.source &&
                   currentComp === snapshot.category_compulsory &&
                   currentChap === snapshot.category_chapter &&
                   currentKnow === snapshot.category_knowledge &&
                   currentApplicableGrade === (snapshot.applicable_grade || '') &&
                   currentTopic === (snapshot.topic || '') &&
                   currentRelatedQuestionId === (snapshot.related_question_id || '') &&
                   currentImages === snapshot.image_paths &&
                   currentTikzCode === (snapshot.tikz_code || '') &&
                   currentTikzReferencePath === (snapshot.tikz_reference_image_path || '') &&
                   currentContentTikzAssets === (snapshot.content_tikz_assets || '[]') &&
                   currentAnswerTikzAssets === (snapshot.answer_tikz_assets || '[]') &&
                   currentTags === snapshot.tags &&
                   metaMatches &&
                   currentExtraTopics === (snapshot.extra_topics || '[]');
        }
        window.editorMatchesBackupSnapshot = editorMatchesBackupSnapshot;

        // Helper to check if the current question has been modified from its original loaded state
        function isEditorModified() {
            if (!originalQuestionState) return false;
            return !editorMatchesBackupSnapshot(originalQuestionState);
        }
        window.isEditorModified = isEditorModified;

        // Custom Premium Confirmation Modal for Unsaved Changes (3 Options)
        function showUnsavedChangesModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none";
                modalDiv.setAttribute('role', 'dialog');
                modalDiv.setAttribute('aria-modal', 'true');
                modalDiv.setAttribute('aria-labelledby', 'unsavedChangesModalTitle');
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-sm shadow-2xl p-6 space-y-4 transform scale-100 transition-all border border-slate-100">
                        <div class="flex items-center space-x-2 pb-2 border-b">
                            <i class="fa-solid fa-circle-question text-brand-600 text-base animate-pulse"></i>
                            <h3 id="unsavedChangesModalTitle" class="font-bold text-sm text-slate-800">当前编辑内容有未保存的修改</h3>
                        </div>
                        <p class="text-xs text-slate-500 leading-relaxed">
                            您刚才编辑的题目尚未存入正式题库。请选择您希望如何处理这些修改？
                        </p>
                        <div class="flex flex-col space-y-2 pt-2">
                            <button id="saveToBankBtn" type="button" class="w-full px-4 py-2 bg-brand-600/80 hover:bg-brand-600 text-white rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 backdrop-blur-sm border border-brand-500/20 shadow-sm">
                                <i class="fa-solid fa-cloud-arrow-up"></i>
                                <span>存入本地库 (正式题库)</span>
                            </button>
                            <button id="saveToDraftsBtn" type="button" class="w-full px-4 py-2 bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 active:scale-[0.98]">
                                <i class="fa-solid fa-box-archive"></i>
                                <span>暂存至草稿箱</span>
                            </button>
                            <button id="discardBtn" type="button" class="w-full px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-1.5 active:scale-[0.98]">
                                <i class="fa-solid fa-trash-can"></i>
                                <span>直接离开 (不保存)</span>
                            </button>
                        </div>
                        <div class="flex justify-end pt-2 border-t">
                            <button id="cancelBtn" type="button" class="px-4 py-1.5 border border-slate-300 rounded-xl text-slate-600 hover:bg-slate-50 transition-all text-[10px] font-medium">
                                返回编辑
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                const finish = (result) => {
                    window.MathBankModal.close(modalDiv);
                    if (modalDiv.isConnected) modalDiv.remove();
                    resolve(result);
                };
                window.MathBankModal.open(modalDiv, { onEscape: () => finish('cancel') });

                document.getElementById('saveToBankBtn').onclick = () => {
                    finish('bank');
                };

                document.getElementById('saveToDraftsBtn').onclick = () => {
                    finish('drafts');
                };

                document.getElementById('discardBtn').onclick = () => {
                    finish('discard');
                };

                document.getElementById('cancelBtn').onclick = () => {
                    finish('cancel');
                };
            });
        }

        // Custom Premium Confirmation Modal for Missing School Phase (Compulsory)
        function showMissingCompulsoryModal() {
            return new Promise((resolve) => {
                const modalDiv = document.createElement('div');
                modalDiv.className = "fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none opacity-0 transition-opacity duration-300";
                modalDiv.setAttribute('role', 'dialog');
                modalDiv.setAttribute('aria-modal', 'true');
                modalDiv.setAttribute('aria-labelledby', 'missingClassificationModalTitle');
                modalDiv.innerHTML = `
                    <div class="bg-white rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4 transform scale-95 transition-all duration-300 border border-slate-100/55">
                        <div class="flex items-center space-x-2.5 pb-2.5 border-b border-slate-100">
                            <div class="w-8 h-8 rounded-xl bg-brand-50 flex items-center justify-center">
                                <i class="fa-solid fa-wand-magic-sparkles text-brand-600 text-sm animate-pulse"></i>
                            </div>
                            <div>
                                <h3 id="missingClassificationModalTitle" class="font-bold text-sm text-slate-800">题目分类信息不完整</h3>
                                <p class="text-[10px] text-slate-400">MATHBANK 教研分类指引</p>
                            </div>
                        </div>
                        <p class="text-xs text-slate-500 leading-relaxed">
                            为了确保题目能够被精准定位和检索，每道题都需要分配<strong>学段（如：必修一）</strong>与<strong>章节</strong>。您可以选择：
                        </p>
                        <div class="flex flex-col space-y-2 pt-1">
                            <button id="manualCompulsoryBtn" type="button" class="w-full px-4 py-2.5 bg-slate-50 hover:bg-slate-100 active:scale-[0.99] text-slate-700 rounded-xl font-semibold transition-all text-xs flex items-center justify-center space-x-2 border border-slate-200/50">
                                <i class="fa-solid fa-pen-to-square text-slate-500"></i>
                                <span>手动选择 / 输入教材定位</span>
                            </button>
                            <button id="autoSaveClassifyBtn" type="button" class="w-full px-4 py-2.5 bg-indigo-600 hover:bg-indigo-500 active:scale-[0.99] text-white rounded-xl font-bold transition-all text-xs flex items-center justify-center space-x-2 shadow-sm">
                                <i class="fa-solid fa-wand-magic-sparkles"></i>
                                <span>交给系统自动分析并定位</span>
                            </button>
                        </div>
                        <div class="flex justify-end pt-2 border-t border-slate-100">
                            <button id="cancelCompulsoryBtn" type="button" class="px-4 py-2 border border-slate-200 rounded-xl text-slate-500 hover:bg-slate-50 transition-all text-[11px] font-medium active:scale-[0.98]">
                                取消保存
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(modalDiv);

                const closeModal = (result) => {
                    // 一次性弹窗：公共助手关栈+复位后直接摘除节点，不再保留淡出窗口期
                    window.closeModalWithTransition(modalDiv);
                    document.body.removeChild(modalDiv);
                    resolve(result);
                };
                window.openModalWithTransition(modalDiv, {
                    onEscape: () => closeModal('cancel'),
                    surface: modalDiv.querySelector('div')
                });

                document.getElementById('manualCompulsoryBtn').onclick = () => closeModal('manual');
                document.getElementById('autoSaveClassifyBtn').onclick = () => closeModal('ai');
                document.getElementById('cancelCompulsoryBtn').onclick = () => closeModal('cancel');
            });
        }

        // Check if editor has unsaved changes, show modal if needed, then run callback
        async function checkAndSwitch(actionCallback) {
            if (window.isQuestionSaveInFlight && window.isQuestionSaveInFlight()) {
                showToast('题目正在保存，请等待完成后再切换', 'info');
                return;
            }
            if (isEditorModified()) {
                const choice = await showUnsavedChangesModal();
                
                if (choice === 'bank') {
                    // Try to save to SQLite database
                    const saveSuccess = await saveQuestion();
                    if (saveSuccess) {
                        actionCallback();
                    }
                } else if (choice === 'drafts') {
                    // Save to Drafts Box in LocalStorage
                    saveCurrentToDrafts();
                    actionCallback();
                } else if (choice === 'discard') {
                    // Directly leave
                    actionCallback();
                } else {
                    // 'cancel' -> do nothing!
                }
            } else {
                actionCallback();
            }
        }

        // ==========================================
        //         LOCALSTORAGE DRAFTS SYSTEM
        // ==========================================

        function getLocalStorageDrafts() {
            try {
                return JSON.parse(localStorage.getItem('mathbank_local_drafts')) || [];
            } catch(e) {
                return [];
            }
        }

        function setLocalStorageDrafts(drafts) {
            localStorage.setItem('mathbank_local_drafts', JSON.stringify(drafts));
        }

        function updateDraftCountBadge() {
            const drafts = getLocalStorageDrafts();
            const badge = document.getElementById('draftCount');
            if (badge) {
                badge.textContent = drafts.length;
            }
        }

        function saveCurrentToDrafts() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return false;
            }
            const content = document.getElementById('editContent').value;
            const qtype = document.getElementById('editQType').value;
            const compulsory = document.getElementById('editCompulsory').value;
            const chapter = document.getElementById('editChapter').value;
            const knowledge = document.getElementById('editKnowledge').value;
            const applicableGrade = document.getElementById('editApplicableGrade') ? document.getElementById('editApplicableGrade').value : '';
            const topic = document.getElementById('editTopic') ? document.getElementById('editTopic').value : '';
            const difficulty = document.getElementById('editDifficulty').value;
            const source = document.getElementById('editSource').value;
            const answerMarkdown = document.getElementById('editAnswerMarkdown').value;
            const review = document.getElementById('editReview').value;
            const tags = document.getElementById('editTags') ? document.getElementById('editTags').value.trim() : '';
            
            const draft = {
                id: EditorState.draftId || ('draft-' + Date.now()),
                content: content,
                question_type: qtype,
                category_compulsory: compulsory,
                category_chapter: chapter,
                category_knowledge: knowledge,
                applicable_grade: applicableGrade,
                topic: topic,
                difficulty: difficulty,
                source: source,
                paper_category: document.getElementById('editPaperCategory') ? document.getElementById('editPaperCategory').value : '',
                answer_markdown: answerMarkdown,
                review: review,
                tags: tags,
                ...(window.MathBankExtendedMeta ? window.MathBankExtendedMeta.read() : {}),
                image_paths: Array.from(new Set([
                    ...uploadedImages,
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : []),
                    ...TikzState.assetImagePaths()
                ])),
                tikz_code: TikzState.contentAssets[0]
                    ? TikzState.contentAssets[0].tikz_code
                    : '',
                tikz_reference_image_path: TikzState.contentAssets[0]
                    ? (TikzState.contentAssets[0].reference_image_path || '')
                    : '',
                content_tikz_assets: TikzState.contentAssets,
                answer_tikz_assets: TikzState.answerAssets,
                extra_topics: JSON.stringify(
                    typeof window.collectExtraTopics === 'function' ? window.collectExtraTopics() : []
                ),
                isDraft: true,
                updated_at: new Date().toISOString()
            };
            
            let drafts = getLocalStorageDrafts();
            const index = drafts.findIndex(d => d.id === draft.id);
            if (index > -1) {
                drafts[index] = draft;
            } else {
                drafts.unshift(draft);
            }
            
            setLocalStorageDrafts(drafts);
            EditorState.setDraftId(draft.id);
            
            // Backup the new draft state as the "original state" so the editor is no longer modified
            backupEditorState(null, draft.id);
            
            updateDraftCountBadge();
            showToast('已暂存至草稿箱！');
            
        }

        function selectDraft(draft) {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            EditorState.useDraft(draft);
            
            // Populate form fields
            document.getElementById('editContent').value = draft.content || '';
            requestAnimationFrame(() => { if (window.__autoResizeContent) window.__autoResizeContent(); });
            document.getElementById('editQType').value = draft.question_type || 'single_choice';
            document.getElementById('editDifficulty').value = draft.difficulty || 'L2';
            document.getElementById('editSource').value = draft.source || '';
            if (document.getElementById('editPaperCategory')) document.getElementById('editPaperCategory').value = draft.paper_category || '';
            document.getElementById('editAnswerMarkdown').value = draft.answer_markdown || '';
            requestAnimationFrame(() => { if (window.__autoResizeAnswer) window.__autoResizeAnswer(); });
            document.getElementById('editReview').value = draft.review || '';
            if (document.getElementById('editTags')) {
                document.getElementById('editTags').value = draft.tags || '';
            }
            if (window.MathBankExtendedMeta) {
                window.MathBankExtendedMeta.apply(draft);
            }

            if (document.getElementById('editApplicableGrade') && draft.applicable_grade) {
                document.getElementById('editApplicableGrade').value = draft.applicable_grade;
            }
            
            // Load cascading categories
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            const topicSelect = document.getElementById('editTopic');
            
            // Reset dropdowns
            compSelect.value = '';
            compSelect.onchange();
            
            if (draft.category_compulsory) {
                compSelect.value = draft.category_compulsory;
                compSelect.onchange();
                if (draft.category_chapter) {
                    chapSelect.value = draft.category_chapter;
                    chapSelect.onchange();
                    if (draft.category_knowledge) {
                        knowSelect.value = draft.category_knowledge;
                        knowSelect.onchange();
                        if (draft.topic && topicSelect) {
                            topicSelect.value = draft.topic;
                        }
                    }
                }
            }

            // Restore extra kaoxiang slots
            if (typeof window.renderExtraTopicRows === 'function') {
                let parsedExtra = [];
                try {
                    parsedExtra = JSON.parse(draft.extra_topics || '[]');
                } catch (e) { parsedExtra = []; }
                window.renderExtraTopicRows(parsedExtra);
            }
            
            // Load images
            const allDraftImages = Array.isArray(draft.image_paths)
                ? draft.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                : [];
            uploadedAnswerImages = typeof window.collectAnswerImagePaths === 'function'
                ? window.collectAnswerImagePaths(draft.answer_markdown || '')
                : [];
            uploadedImages = allDraftImages.filter(path => !uploadedAnswerImages.includes(path));
            window.hydrateTikzState(draft);
            const hiddenTikzReferencePaths = new Set(TikzState.referencePaths());
            uploadedImages = uploadedImages.filter(path => !hiddenTikzReferencePaths.has(path));
            renderIllustrationBadges();
            if (typeof window.renderAnswerImageBadges === 'function') {
                window.renderAnswerImageBadges();
            }
            if (typeof window.renderAnswerTikzAssets === 'function') {
                window.renderAnswerTikzAssets();
            }
            if (typeof window.renderContentTikzAssets === 'function') {
                window.renderContentTikzAssets();
            }
            
            // Update preview and side panels
            if (typeof window.updateContentPreview === 'function') {
                window.updateContentPreview();
            } else {
                document.getElementById('editContent').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateAnswerPreview === 'function') {
                window.updateAnswerPreview();
            } else {
                document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            }
            if (typeof window.updateReviewPreview === 'function') {
                window.updateReviewPreview();
            } else {
                document.getElementById('editReview').dispatchEvent(new Event('input'));
            }
            renderEditorPaperMeta();
            
            document.getElementById('editorTitle').textContent = `编辑草稿 - 暂存中`;
            
            // Backup draft state
            backupEditorState(null, draft.id);
        }


function deleteDraft(id) {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认要删除这篇草稿吗？')) {
                let drafts = getLocalStorageDrafts();
                drafts = drafts.filter(d => d.id !== id);
                setLocalStorageDrafts(drafts);
                
                showToast('草稿已删除！');
                updateDraftCountBadge();
                
                if (EditorState.draftId === id) {
                    // Reset current draft state
                    EditorState.clearDraft();
                    startNewQuestionWithoutPrompt();
                }
                
            }
        }


        // ==========================================
        // FULL-BANK DUPLICATE SCAN (全库查重)
        // ==========================================
        let scanDupInFlight = false;
        let scanDupIgnored = new Set();

        function scanDupLevelBadge(level) {
            const map = {
                exact: ['text-rose-700 bg-rose-50 border-rose-200 dark:text-rose-200 dark:bg-rose-500/15 dark:border-rose-500/40', '精确重复'],
                probable: ['text-amber-700 bg-amber-50 border-amber-200 dark:text-amber-200 dark:bg-amber-500/15 dark:border-amber-500/40', '高度疑似'],
                possible_variant: ['text-blue-700 bg-blue-50 border-blue-200 dark:text-blue-200 dark:bg-blue-500/15 dark:border-blue-500/40', '疑似变式']
            };
            const entry = map[String(level || '').toLowerCase()] || ['text-slate-600 bg-slate-50 border-slate-200 dark:text-slate-300 dark:bg-slate-700/40 dark:border-slate-600', level || '未知'];
            return '<span class="inline-block px-2 py-0.5 text-[10px] font-bold rounded border ' + entry[0] + '">' + entry[1] + '</span>';
        }

        function scanDupContentBlock(content, truncated) {
            const safeText = window.MathBankSafe ? window.MathBankSafe.escapeText(String(content || '')) : String(content || '');
            return '<div class="text-xs text-slate-600 dark:text-slate-300 leading-relaxed whitespace-pre-wrap break-words">' +
                safeText + (truncated ? '<span class="text-slate-400">…</span>' : '') + '</div>';
        }

        function scanDupMeta(item) {
            const parts = [];
            if (item.source) parts.push('来源: ' + window.MathBankSafe.escapeText(item.source));
            if (item.applicable_grade) parts.push('年级: ' + window.MathBankSafe.escapeText(item.applicable_grade));
            if (item.topic) parts.push('考点: ' + window.MathBankSafe.escapeText(item.topic));
            if (item.tags) parts.push('标签: ' + window.MathBankSafe.escapeText(item.tags));
            return parts.length ? '<div class="text-[10px] text-slate-400 space-x-2">' + parts.join(' <span class="text-slate-300">|</span> ') + '</div>' : '';
        }

        function renderScanDuplicateGroup(group) {
            const row = document.createElement('div');
            row.className = 'rounded-xl border border-slate-200/70 dark:border-slate-700/60 bg-white/70 dark:bg-slate-900/50 p-3.5 space-y-2.5';
            const bestLevel = group.level || '';
            const header = document.createElement('div');
            header.className = 'flex items-center justify-between gap-2 flex-wrap';
            header.innerHTML = '<div class="flex items-center gap-2">' +
                '<span class="font-mono text-xs font-bold text-slate-700 dark:text-slate-200">#' + (group.seq_num != null ? group.seq_num : group.question_id) + '</span>' +
                scanDupLevelBadge(bestLevel) +
                '</div>';
            row.appendChild(header);

            const body = document.createElement('div');
            body.className = 'space-y-1';
            body.innerHTML = scanDupContentBlock(group.content, group.content_truncated) + scanDupMeta(group);
            row.appendChild(body);

            // Candidate list
            const listWrap = document.createElement('div');
            listWrap.className = 'mt-1 ml-4 border-l-2 border-slate-200 dark:border-slate-700 pl-3 space-y-2';
            (group.candidates || []).forEach(cand => {
                const candKey = group.question_id + ':' + cand.id;
                if (scanDupIgnored.has(candKey)) return;
                const item = document.createElement('div');
                item.className = 'rounded-lg bg-slate-50/70 dark:bg-slate-800/40 border border-slate-100 dark:border-slate-700/50 p-2.5 space-y-1.5';
                const scoreText = (cand.score != null) ? (' 相似度 ' + Math.round(Number(cand.score) * 100) + '%') : '';
                const candHeader = document.createElement('div');
                candHeader.className = 'flex items-center justify-between gap-2 flex-wrap';
                candHeader.innerHTML = '<div class="flex items-center gap-2">' +
                    '<span class="font-mono text-xs font-bold text-slate-600 dark:text-slate-300">#' + (cand.seq_num != null ? cand.seq_num : cand.id) + '</span>' +
                    scanDupLevelBadge(cand.level) +
                    '<span class="text-[10px] text-slate-400">' + scoreText + '</span>' +
                    (cand.needs_visual_review ? '<span class="text-[10px] text-amber-600">配图待核对</span>' : '') +
                    '</div>';
                const candActions = document.createElement('div');
                candActions.className = 'flex items-center gap-2';
                const tagBtn = document.createElement('button');
                tagBtn.type = 'button';
                const targetTag = (String(cand.level || '').toLowerCase().indexOf('variant') !== -1 ? '疑似变式#' : '疑似重复#') + (group.seq_num != null ? group.seq_num : group.question_id);
                tagBtn.className = 'text-[10px] font-semibold px-2 py-1 rounded-lg border border-rose-200 text-rose-600 hover:bg-rose-50 dark:border-rose-500/40 dark:text-rose-300 dark:hover:bg-rose-500/15 transition-all';
                tagBtn.textContent = '加标签「' + targetTag + '」';
                tagBtn.addEventListener('click', () => applyScanDupTag(cand.id, targetTag, tagBtn, candKey));
                const ignoreBtn = document.createElement('button');
                ignoreBtn.type = 'button';
                ignoreBtn.className = 'text-[10px] px-2 py-1 rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700/40 transition-all';
                ignoreBtn.textContent = '忽略';
                ignoreBtn.addEventListener('click', () => {
                    scanDupIgnored.add(candKey);
                    item.remove();
                });
                candActions.appendChild(tagBtn);
                candActions.appendChild(ignoreBtn);
                candHeader.appendChild(candActions);
                item.appendChild(candHeader);
                const candBody = document.createElement('div');
                candBody.className = 'space-y-1';
                candBody.innerHTML = scanDupContentBlock(cand.content, cand.content_truncated) + scanDupMeta(cand);
                item.appendChild(candBody);
                listWrap.appendChild(item);
            });
            if (listWrap.children.length > 0) row.appendChild(listWrap);
            return row;
        }

        async function applyScanDupTag(questionId, tag, btn, candKey) {
            if (btn.disabled) return;
            btn.disabled = true;
            btn.setAttribute('aria-busy', 'true');
            btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> 添加中';
            try {
                const response = await fetch('/api/questions/' + questionId + '/add-tag', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: JSON.stringify({ tag: tag })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error((data && (data.detail || data.message)) || ('HTTP ' + response.status));
                }
                btn.classList.add('pointer-events-none', 'opacity-60');
                btn.classList.remove('border-rose-200', 'text-rose-600', 'hover:bg-rose-50', 'dark:border-rose-500/40', 'dark:text-rose-300', 'dark:hover:bg-rose-500/15');
                btn.classList.add('border-emerald-200', 'text-emerald-600', 'dark:border-emerald-500/40', 'dark:text-emerald-300');
                btn.textContent = '已添加';
                showToast('已为题 #' + questionId + ' 添加标签「' + tag + '」。', 'success');
            } catch (error) {
                btn.disabled = false;
                btn.removeAttribute('aria-busy');
                btn.innerHTML = '加标签失败';
                showToast('添加标签失败：' + error.message, 'error');
            }
        }

        function openScanDuplicatesModal() {
            const modal = document.getElementById('scanDuplicatesModal');
            const triggerButton = document.getElementById('scanDuplicatesOpenBtn');
            if (triggerButton && triggerButton.disabled) return;
            scanDupIgnored = new Set();
            document.getElementById('scanDupResults').innerHTML = '<div class="p-10 text-center text-slate-400 text-xs">点击「开始扫描」检查全库的重复与近似题目。</div>';
            document.getElementById('scanDupStatus').textContent = '';
            document.body.classList.add('modal-active');
            window.openModalWithTransition(modal, { onEscape: closeScanDuplicatesModal });
        }

        function closeScanDuplicatesModal() {
            const modal = document.getElementById('scanDuplicatesModal');
            // 原实现只关了焦点栈：弹窗既不 hidden 也不复位 opacity-0，叠加 modal-active
            // 把 header/main 压成 opacity:0——又一类"整页点不动"的事故形态，走公共助手补齐。
            window.closeModalWithTransition(modal);
            document.body.classList.remove('modal-active');
        }

        async function runScanDuplicates() {
            if (scanDupInFlight) return;
            const minLevel = document.getElementById('scanDupMinLevel') ? document.getElementById('scanDupMinLevel').value : 'probable';
            const resultsBox = document.getElementById('scanDupResults');
            const statusEl = document.getElementById('scanDupStatus');
            const runBtn = document.getElementById('scanDupRunBtn');
            const originalHtml = runBtn ? runBtn.innerHTML : '';
            scanDupInFlight = true;
            if (runBtn) {
                runBtn.disabled = true;
                runBtn.setAttribute('aria-busy', 'true');
                runBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i><span>扫描中</span>';
            }
            if (statusEl) statusEl.textContent = '正在扫描全库，请稍候...';
            resultsBox.innerHTML = '<div class="p-10 text-center text-slate-400 text-xs flex items-center justify-center gap-2"><i class="fa-solid fa-circle-notch fa-spin"></i><span>正在扫描全库，请稍候...</span></div>';
            try {
                const response = await fetch('/api/questions/scan-duplicates', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: JSON.stringify({ min_level: minLevel, max_per_question: 5 })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error((data && (data.detail || data.message)) || ('HTTP ' + response.status));
                }
                if (!data || data.status !== 'success') {
                    throw new Error((data && data.message) || '扫描服务返回无效结果');
                }
                const items = Array.isArray(data.items) ? data.items : [];
                resultsBox.innerHTML = '';
                if (items.length === 0) {
                    const empty = document.createElement('div');
                    empty.className = 'p-10 text-center text-emerald-600 dark:text-emerald-400 text-xs flex items-center justify-center gap-2';
                    empty.innerHTML = '<i class="fa-solid fa-circle-check"></i><span>未发现符合当前级别的重复或近似题目。</span>';
                    resultsBox.appendChild(empty);
                } else {
                    items.forEach(group => resultsBox.appendChild(renderScanDuplicateGroup(group)));
                }
                const idx = data.index_status || {};
                const coverage = Math.min(99, Math.floor(Number(idx.coverage || 0) * 100));
                let statusText = '已扫描 ' + data.scanned + ' 题，发现 ' + items.length + ' 组疑似重复';
                if (idx.ready === false) {
                    statusText += '（查重索引覆盖 ' + coverage + '%，未完全就绪）';
                }
                if (Number(data.fingerprint_missing || 0) > 0) {
                    statusText += '，' + data.fingerprint_missing + ' 题缺少指纹待补建';
                }
                if (statusEl) statusEl.textContent = statusText;
            } catch (error) {
                resultsBox.innerHTML = '<div class="p-10 text-center text-rose-500 text-xs">全库查重失败：' + window.MathBankSafe.escapeText(error.message) + '</div>';
                if (statusEl) statusEl.textContent = '';
            } finally {
                scanDupInFlight = false;
                if (runBtn) {
                    runBtn.disabled = false;
                    runBtn.removeAttribute('aria-busy');
                    runBtn.innerHTML = originalHtml;
                }
            }
        }

        function openStatsModal() {
            const modal = document.getElementById('statsModal');
            const triggerButton = document.getElementById('statsOpenBtn');
            if (triggerButton && triggerButton.disabled) return;
            const triggerButtonContent = triggerButton ? triggerButton.innerHTML : '';
            if (triggerButton) {
                triggerButton.disabled = true;
                triggerButton.setAttribute('aria-busy', 'true');
                triggerButton.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i><span>加载统计</span>';
            }
            
            // 🟢 先拉取并渲染数据，让弹窗内部 DOM 完全静态就绪后再显示弹窗，完美消除毛玻璃背景下的二次重绘闪烁冲突
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        globalStatsData = data;
                        
                        // Render total counters
                        document.getElementById('statsTotalCount').textContent = data.total_count;

                        // Dynamic difficulty cards: use difficulty_counts if available, fall back to legacy keys
                        const diffCounts = data.difficulty_counts || {};
                        const legacyCounts = {
                            'L1': data.l1_count,
                            'L2': data.l2_count,
                            'L3': data.l3_count,
                            'L4': data.l4_count
                        };
                        const counts = Object.keys(diffCounts).length > 0 ? diffCounts : legacyCounts;

                        // Build or update dynamic difficulty cards container
                        let diffCards = document.getElementById('statsDifficultyCards');
                        if (!diffCards) {
                            // Create container after total card
                            const totalCard = document.querySelector('.grid.grid-cols-5');
                            // Replace the old static difficulty cards with dynamic ones
                            diffCards = document.createElement('div');
                            diffCards.id = 'statsDifficultyCards';
                            diffCards.className = 'grid grid-cols-5 gap-3.5';

                            // Remove old static cards (statsBasicCount through statsNovelCount)
                            const oldCards = document.querySelectorAll('#statsModal .bg-emerald-50, #statsModal .bg-blue-50, #statsModal .bg-rose-50, #statsModal .bg-purple-50');
                            oldCards.forEach(c => c.remove());

                            totalCard.appendChild(diffCards);
                        }

                        // Card color map
                        const cardColors = {
                            'L1': { bg: 'emerald', text: 'emerald', icon: 'fa-layer-group' },
                            'L2': { bg: 'blue', text: 'blue', icon: 'fa-bars-progress' },
                            'L3': { bg: 'rose', text: 'rose', icon: 'fa-fire' },
                            'L4': { bg: 'purple', text: 'purple', icon: 'fa-lightbulb' },
                            'L5': { bg: 'violet', text: 'violet', icon: 'fa-sparkles' }
                        };

                        // Get labels from metadata if available
                        const diffLabels = (data.difficulty_labels || {}) ;
                        diffCards.innerHTML = '';
                        Object.keys(counts).sort().forEach(level => {
                            const count = counts[level] || 0;
                            const colors = cardColors[level] || { bg: 'slate', text: 'slate', icon: 'fa-question' };
                            const label = diffLabels[level] || level;
                            const card = document.createElement('div');
                            card.className = `bg-${colors.bg}-50/80 border border-${colors.bg}-200/60 dark:bg-${colors.bg}-500/10 dark:border-${colors.bg}-500/25 rounded-xl p-3.5 flex items-center justify-between shadow-sm relative overflow-hidden group`;
                            card.innerHTML = `
                                <div class="space-y-1 z-10">
                                    <span class="text-xs font-bold text-${colors.text}-700 dark:text-${colors.text}-300 uppercase tracking-wider">${label}</span>
                                    <h2 class="text-3xl font-black text-${colors.text}-950 dark:text-${colors.text}-200 font-mono">${count}</h2>
                                </div>
                                <i class="fa-solid ${colors.icon} text-3xl text-${colors.text}-500/20 dark:text-${colors.text}-400/30 absolute right-3 group-hover:scale-110 transition-transform duration-300 z-0"></i>
                            `;
                            diffCards.appendChild(card);
                        });
                        
                        // Populate compulsory stages for stats query
                        populateStatsQueryCompulsory();
                        
                        // Set current local Year and Month
                        const now = new Date();
                        document.getElementById('statsYearSelect').value = now.getFullYear().toString();
                        document.getElementById('statsMonthSelect').value = (now.getMonth() + 1).toString();
                        
                        // Render increments calendar
                        renderStatsCalendar();
                        
                        // Reset query selections
                        document.getElementById('statsQueryCompulsory').value = '';
                        const chapSelect = document.getElementById('statsQueryChapter');
                        chapSelect.innerHTML = '<option value="">-- 先选择册次 --</option>';
                        chapSelect.disabled = true;
                        document.getElementById('statsQueryResultEmpty').classList.remove('hidden');
                        document.getElementById('statsQueryResultData').classList.add('hidden');
                        
                        // 数据和图表完全就绪，再顺滑滑入弹窗并淡化背景
                        document.body.classList.add('modal-active');
                        window.openModalWithTransition(modal, { onEscape: closeStatsModal });
                    } else {
                        showToast('获取统计大屏数据失败: ' + data.message, 'error');
                    }
                })
                .catch(err => {
                    showToast('请求统计数据出错: ' + err, 'error');
                })
                .finally(() => {
                    if (!triggerButton) return;
                    triggerButton.disabled = false;
                    triggerButton.removeAttribute('aria-busy');
                    triggerButton.innerHTML = triggerButtonContent;
                });
        }

        function closeStatsModal() {
            const modal = document.getElementById('statsModal');
            window.closeModalWithTransition(modal);
            document.body.classList.remove('modal-active');
        }

        function populateStatsQueryCompulsory() {
            const compSelect = document.getElementById('statsQueryCompulsory');
            compSelect.innerHTML = '<option value="">-- 选择册次 --</option>';
            if (globalStatsData && globalStatsData.compulsory_chapter_counts) {
                Object.keys(globalStatsData.compulsory_chapter_counts).forEach(comp => {
                    compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(comp)}">${window.MathBankSafe.escapeText(comp)}</option>`;
                });
            }
        }

        function onStatsQueryCompulsoryChange() {
            const compVal = document.getElementById('statsQueryCompulsory').value;
            const chapSelect = document.getElementById('statsQueryChapter');
            
            chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
            document.getElementById('statsQueryResultEmpty').classList.remove('hidden');
            document.getElementById('statsQueryResultData').classList.add('hidden');
            
            if (compVal && globalStatsData && globalStatsData.compulsory_chapter_counts[compVal]) {
                chapSelect.disabled = false;
                Object.keys(globalStatsData.compulsory_chapter_counts[compVal]).forEach(chap => {
                    chapSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(chap)}">${window.MathBankSafe.escapeText(chap)}</option>`;
                });
            } else {
                chapSelect.disabled = true;
            }
        }

        async function onStatsQueryChapterChange() {
            const compVal = document.getElementById('statsQueryCompulsory').value;
            const chapVal = document.getElementById('statsQueryChapter').value;
            
            const emptyPanel = document.getElementById('statsQueryResultEmpty');
            const dataPanel = document.getElementById('statsQueryResultData');
            
            if (!compVal || !chapVal) {
                emptyPanel.classList.remove('hidden');
                dataPanel.classList.add('hidden');
                return;
            }
            
            emptyPanel.classList.add('hidden');
            dataPanel.classList.remove('hidden');
            
            // Get count for selected chapter
            const count = globalStatsData.compulsory_chapter_counts[compVal][chapVal] || 0;
            document.getElementById('statsQueryCount').textContent = count;
            
            // Query local questions list to get knowledge point distributions
            const params = new URLSearchParams();
            params.append('compulsory', compVal);
            params.append('chapter', chapVal);
            
            const listContainer = document.getElementById('statsQueryKnowledgeList');
            listContainer.innerHTML = '<div class="text-[10px] text-slate-400 py-4 text-center"><i class="fa-solid fa-spinner animate-spin mr-1"></i>正在计算知识点分布...</div>';
            
            try {
                const response = await fetch(`/api/questions?${params.toString()}`);
                const questions = await response.json();
                
                // Group by knowledge
                const knowStats = {};
                questions.forEach(q => {
                    const know = q.category_knowledge || '未细分知识点';
                    knowStats[know] = (knowStats[know] || 0) + 1;
                });
                
                listContainer.innerHTML = '';
                if (Object.keys(knowStats).length === 0) {
                    listContainer.innerHTML = '<div class="text-[10px] text-slate-500 text-center py-4">本章暂无细分知识点</div>';
                } else {
                    Object.entries(knowStats).forEach(([know, knCount]) => {
                        const pct = Math.round((knCount / count) * 100);
                        listContainer.innerHTML += `
                            <div class="space-y-1 bg-slate-50/70 dark:bg-slate-800/50 p-2 rounded-lg border border-slate-100 dark:border-slate-700/60">
                                <div class="flex justify-between items-center text-[10px] font-semibold text-slate-700 dark:text-slate-200">
                                    <span class="truncate pr-2">${window.MathBankSafe.escapeText(know)}</span>
                                    <span class="font-mono text-slate-600 dark:text-slate-400 text-[10px]">${knCount} 题 (${pct}%)</span>
                                </div>
                                <div class="w-full bg-slate-200 dark:bg-slate-700 h-1.5 rounded-full overflow-hidden">
                                    <div class="bg-brand-500 h-1.5 rounded-full" style="width: ${pct}%"></div>
                                </div>
                            </div>
                        `;
                    });
                }
            } catch (err) {
                listContainer.innerHTML = '<div class="text-[10px] text-red-500 py-4 text-center">加载失败</div>';
            }
        }

        function renderStatsCalendar() {
            const year = parseInt(document.getElementById('statsYearSelect').value);
            const month = parseInt(document.getElementById('statsMonthSelect').value);
            const grid = document.getElementById('statsCalendarGrid');
            
            grid.innerHTML = '';
            
            // Get first day of month (0 = Sunday, 6 = Saturday)
            const firstDayIndex = new Date(year, month - 1, 1).getDay();
            // Get total days in month
            const daysInMonth = new Date(year, month, 0).getDate();
            
            // Pre-fill empty days for previous month alignment
            for (let i = 0; i < firstDayIndex; i++) {
                const emptyCell = document.createElement('div');
                emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                grid.appendChild(emptyCell);
            }
            
            // Daily additions data
            const dailyAdds = (globalStatsData && globalStatsData.daily_adds) ? globalStatsData.daily_adds : {};
            
            // Generate cells
            for (let day = 1; day <= daysInMonth; day++) {
                const dayCell = document.createElement('div');
                
                // Format YYYY-MM-DD
                const mStr = String(month).padStart(2, '0');
                const dStr = String(day).padStart(2, '0');
                const dateStr = `${year}-${mStr}-${dStr}`;
                
                const count = dailyAdds[dateStr] || 0;
                
                const isToday = (new Date().getFullYear() === year && new Date().getMonth() + 1 === month && new Date().getDate() === day);
                
                if (count > 0) {
                    dayCell.className = `p-1 bg-rose-50/80 dark:bg-rose-500/15 border border-rose-200/60 dark:border-rose-500/30 hover:bg-rose-100/80 dark:hover:bg-rose-500/25 rounded-lg flex flex-col justify-between items-center transition-all shadow-sm cursor-help select-none ${isToday ? 'ring-2 ring-rose-400 dark:ring-rose-400' : ''}`;
                    dayCell.title = `当天最终录入：${count} 道题目`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-bold text-rose-800 dark:text-rose-200 ${isToday ? 'bg-rose-200 dark:bg-rose-500/30 px-1 py-0.5 rounded-md' : ''}">${day}</span>
                        <span class="text-[10px] font-extrabold text-rose-600 dark:text-rose-400 font-mono animate-[bounce_1.5s_infinite]">+${count}</span>
                    `;
                } else {
                    dayCell.className = `p-1 bg-white dark:bg-slate-900/50 border border-slate-200/40 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/60 rounded-lg flex flex-col justify-start items-center transition-all select-none ${isToday ? 'ring-2 ring-brand-500 border-brand-200 dark:ring-brand-500' : ''}`;
                    dayCell.innerHTML = `
                        <span class="text-[10px] font-medium text-slate-600 dark:text-slate-300 ${isToday ? 'bg-brand-100 dark:bg-brand-900/60 text-brand-700 dark:text-brand-200 px-1 py-0.5 rounded-md font-bold' : ''}">${day}</span>
                    `;
                }
                
                grid.appendChild(dayCell);
            }
            
            // Fill remaining grid spaces to keep calendar layout perfect
            const totalCellsUsed = firstDayIndex + daysInMonth;
            const remainingCells = 42 - totalCellsUsed;
            if (remainingCells > 0 && remainingCells < 7) {
                const limit = totalCellsUsed <= 35 ? 35 : 42;
                const pad = limit - totalCellsUsed;
                for (let i = 0; i < pad; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            } else {
                for (let i = 0; i < remainingCells; i++) {
                    const emptyCell = document.createElement('div');
                    emptyCell.className = "bg-slate-100/30 dark:bg-slate-800/20 rounded-lg border border-transparent";
                    grid.appendChild(emptyCell);
                }
            }
        }

        function findEditorSectionByName(comp, chap, know) {
            if (!categoryTree || !comp || !chap || !know) return null;
            const sections = categoryTree[comp] && categoryTree[comp][chap];
            if (!sections || typeof sections !== 'object') return null;
            if (sections[know]) return { name: know, topics: sections[know] };
            return null;
        }

        function populateCategoryDropdowns() {
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            const topicSelect = document.getElementById('editTopic');
            const addTopicBtn = document.getElementById('addTopicBtn');
            const editTopicsBtn = document.getElementById('editTopicsBtn');
            const topicDatalist = document.getElementById('editTopicList');
            
            if (!compSelect || !chapSelect || !knowSelect) return;
            if (!categoryTree || typeof categoryTree !== 'object') {
                console.warn('[Security Shield] 分类数据未准备完毕，跳过编辑区分类级联填充');
                return;
            }
            
            // Backup selection values to prevent losing them during async reloads
            const selectedComp = compSelect.value;
            const selectedChap = chapSelect.value;
            const selectedKnow = knowSelect.value;
            const selectedTopic = topicSelect ? topicSelect.value : '';
            
            // 1. Compulsory (册次)
            compSelect.innerHTML = '<option value="">-- 选择册次 --</option>';
            Object.keys(categoryTree).forEach(c => {
                compSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(c)}">${window.MathBankSafe.escapeText(c)}</option>`;
            });
            
            compSelect.onchange = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;
                if (topicSelect) {
                    topicSelect.value = '';
                    topicSelect.disabled = true;
                    if (topicDatalist) topicDatalist.innerHTML = '';
                    if (addTopicBtn) addTopicBtn.disabled = true;
                    if (editTopicsBtn) editTopicsBtn.disabled = true;
                }
                
                if (comp && categoryTree[comp]) {
                    // 适用年级自动建议：必修一/二 → 高一，选修一/二/三 → 高二（可手动改）
                    const gradeAutoMap = { '必修一': 'g1', '必修二': 'g1', '选修一': 'g2', '选修二': 'g2', '选修三': 'g2' };
                    const gradeSel = document.getElementById('editApplicableGrade');
                    if (gradeSel) {
                        const prevAutoComp = compSelect.dataset.gradeAutoComp || '';
                        const prevAuto = gradeAutoMap[prevAutoComp] || '';
                        const curAuto = gradeAutoMap[comp] || '';
                        // 年级为空，或仍等于上次自动建议值（说明未手动改）时跟随新册次
                        if (!gradeSel.value || gradeSel.value === prevAuto) {
                            if (curAuto) {
                                gradeSel.value = curAuto;
                                compSelect.dataset.gradeAutoComp = comp;
                            } else {
                                gradeSel.value = '';
                                delete compSelect.dataset.gradeAutoComp;
                            }
                        }
                    }
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        chapSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(ch)}">${window.MathBankSafe.escapeText(ch)}</option>`;
                    });
                } else {
                    chapSelect.disabled = true;
                }
            };
            
            chapSelect.onchange = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (可不选，默认整章) --</option>';
                if (topicSelect) {
                    topicSelect.value = '';
                    topicSelect.disabled = true;
                    if (topicDatalist) topicDatalist.innerHTML = '';
                    if (addTopicBtn) addTopicBtn.disabled = true;
                    if (editTopicsBtn) editTopicsBtn.disabled = true;
                }
                
                if (comp && chap && categoryTree[comp][chap]) {
                    knowSelect.disabled = false;
                    Object.keys(categoryTree[comp][chap]).forEach(kName => {
                        knowSelect.innerHTML += `<option value="${window.MathBankSafe.escapeAttribute(kName)}">${window.MathBankSafe.escapeText(kName)}</option>`;
                    });
                } else {
                    knowSelect.disabled = true;
                }
            };
            
            knowSelect.onchange = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                const know = knowSelect.value;
                if (!topicSelect) return;
                topicSelect.value = '';
                const isG = typeof window.isGCurriculum === 'function' && window.isGCurriculum();
                topicSelect.disabled = !(comp && chap && know) || isG;
                if (topicDatalist) topicDatalist.innerHTML = '';
                const section = findEditorSectionByName(comp, chap, know);
                const topics = (section && Array.isArray(section.topics)) ? section.topics : [];
                if (comp && chap && know && topics.length > 0 && !isG) {
                    // 考点选项直接挂载到 editTopic 下拉（原 topicDatalist 元素已不存在，select 用 datalist 不生效）
                    const placeholder = topicSelect.querySelector('option');
                    topicSelect.innerHTML = '<option value="">先选小节</option>';
                    topics.forEach(t => {
                        const opt = document.createElement('option');
                        const tName = window.topicName(t);
                        opt.value = tName;
                        const tLevel = (typeof t === 'object' && t.level) ? t.level : '';
                        opt.textContent = tName + (tLevel ? ' (' + tLevel + ')' : '');
                        topicSelect.appendChild(opt);
                    });
                    if (typeof updateTopicLevelBadge === 'function') updateTopicLevelBadge();
                }
                if (addTopicBtn) {
                    addTopicBtn.disabled = !(comp && chap && know) || isG;
                }
                if (editTopicsBtn) {
                    editTopicsBtn.disabled = !(comp && chap && know) || isG;
                }
            };

            // Restore backed up values if they exist in the new categoryTree
            if (selectedComp && categoryTree[selectedComp]) {
                compSelect.value = selectedComp;
                compSelect.onchange();
                if (selectedChap && categoryTree[selectedComp][selectedChap]) {
                    chapSelect.value = selectedChap;
                    chapSelect.onchange();
                    if (selectedKnow && findEditorSectionByName(selectedComp, selectedChap, selectedKnow)) {
                        knowSelect.value = selectedKnow;
                        knowSelect.onchange();
                        if (selectedTopic && topicSelect) {
                            topicSelect.value = selectedTopic;
                        }
                    }
                }
            }
        }

        // ---- 考向（考点）写入大纲的唯一出口 ----
        // 手动新增/改名都走「直接写大纲」这条路，**不经过** mathbank/metadata.py 的
        // STRICT_AUTO_TOPICS 闸门 —— 那道闸只管录题时按名字猜建档（防 AI 幻觉）。
        // 拆卷卡片与编辑器都必须调这两个助手，别再各抄一份：两份实现一旦分叉，
        // 「push 裸字符串 vs push {id,name}」这种差别不会报错，只会让 topic_id 漂移。
        window.locateCurriculumSection = function(meta, comp, chap, know, createIfMissing) {
            if (!meta || typeof meta !== 'object') return null;
            if (!meta.curriculum) meta.curriculum = {};
            if (!comp || !chap || !know) return null;
            if (!meta.curriculum[comp]) meta.curriculum[comp] = {};
            if (!Array.isArray(meta.curriculum[comp][chap])) meta.curriculum[comp][chap] = [];
            const list = meta.curriculum[comp][chap];
            let idx = -1;
            for (let i = 0; i < list.length; i++) {
                const it = list[i];
                if ((it && typeof it === 'object' && it.name === know) || it === know) {
                    idx = i;
                    break;
                }
            }
            if (idx === -1) {
                if (!createIfMissing) return null;
                list.push({ name: know, topics: [] });
                idx = list.length - 1;
            } else if (typeof list[idx] === 'string') {
                list[idx] = { name: know, topics: [] };
            }
            if (!Array.isArray(list[idx].topics)) list[idx].topics = [];
            return list[idx];
        };

        // 取当前活跃大纲的可编辑深拷贝。刻意**不**以 window.systemMetadata 兜底：
        // 那是页面加载时的快照，别的会话改过大纲后拿它整棵 POST 回去会把新内容覆盖掉
        // （读-改-写窗口）。GET 失败就直接报错，不做静默降级。
        window.fetchEditableMetadata = function() {
            return fetch('/api/config/metadata')
                .then(function(r) {
                    if (!r.ok) throw new Error('读取大纲失败 HTTP ' + r.status);
                    return r.json();
                })
                .then(function(meta) {
                    if (!meta || typeof meta !== 'object' || !meta.curriculum) {
                        throw new Error('大纲响应缺少 curriculum');
                    }
                    return JSON.parse(JSON.stringify(meta));
                });
        };

        window.createCurriculumTopic = function(comp, chap, know, name) {
            const clean = String(name || '').trim();
            if (!clean) return Promise.reject(new Error('请输入考点名称'));
            if (clean.indexOf(',') !== -1 || clean.indexOf('，') !== -1) {
                return Promise.reject(new Error('考点名称不能包含逗号'));
            }
            if (typeof window.isGCurriculum === 'function' && window.isGCurriculum()) {
                return Promise.reject(new Error('高三专题大纲下小节即考向，无需新增考点'));
            }
            if (!comp || !chap || !know) {
                return Promise.reject(new Error('请先选择册次 / 章节 / 小节'));
            }
            return window.fetchEditableMetadata().then(function(meta) {
                const section = window.locateCurriculumSection(meta, comp, chap, know, true);
                if (section.topics.some(t => window.topicName(t) === clean)) {
                    throw new Error('该考点已存在，可直接选用');
                }
                // 必须是对象形态：裸字符串要靠后端 normalize_topic_list 自愈成 {id,name}，
                // 少一次依赖就少一处可能漂移的地方。
                section.topics.push({ id: '', name: clean });
                return fetch('/api/config/metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(meta)
                }).then(function(r) {
                    if (!r.ok) {
                        return r.json().catch(() => null).then(function(d) {
                            throw new Error((d && d.detail) || ('HTTP ' + r.status));
                        });
                    }
                    return r.json();
                }).then(function() { return clean; });
            });
        };

        // 保 ID 改名：只动 name，id 原样带回。题目侧存的是 topic_id（确定性哈希含名字），
        // 展示层每次按当前大纲翻译 —— 所以 id 不变旧题就自动跟随新名。
        // 若改成「先删后加」或按名字重新算 id，旧题会指向一个大纲里已不存在的 id，
        // 变成显示为空的孤儿考向。
        window.renameCurriculumTopic = function(comp, chap, know, topicId, newName) {
            const clean = String(newName || '').trim();
            if (!clean) return Promise.reject(new Error('请输入考点名称'));
            if (clean.indexOf(',') !== -1 || clean.indexOf('，') !== -1) {
                return Promise.reject(new Error('考点名称不能包含逗号'));
            }
            if (!topicId) return Promise.reject(new Error('该考向没有稳定 ID，无法安全改名'));
            return window.fetchEditableMetadata().then(function(meta) {
                const section = window.locateCurriculumSection(meta, comp, chap, know, false);
                if (!section) throw new Error('找不到该小节，未能定位要改名的考向');
                // 关键：按 id 定位，不按名字匹配。
                const target = section.topics.find(function(t) {
                    return t && typeof t === 'object' && t.id === topicId;
                });
                if (!target) throw new Error('大纲里找不到 ID 为 ' + topicId + ' 的考向');
                if (section.topics.some(function(t) {
                    return t !== target && window.topicName(t) === clean;
                })) {
                    throw new Error('同一小节已有同名考向');
                }
                target.name = clean;
                return fetch('/api/config/metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(meta)
                }).then(function(r) {
                    if (!r.ok) {
                        return r.json().catch(() => null).then(function(d) {
                            throw new Error((d && d.detail) || ('HTTP ' + r.status));
                        });
                    }
                    return r.json();
                }).then(function() { return clean; });
            });
        };

        window.openAddTopicModal = function() {
            const compSel = document.getElementById('editCompulsory');
            const chapSel = document.getElementById('editChapter');
            const knowSel = document.getElementById('editKnowledge');
            const topicInput = document.getElementById('editTopic');
            if (!compSel || !chapSel || !knowSel || !topicInput) return;
            const comp = compSel.value;
            const chap = chapSel.value;
            const know = knowSel.value;
            if (!comp || !chap || !know) {
                showToast('请先选择册次 / 章节 / 小节', 'info');
                return;
            }
            if (typeof window.isGCurriculum === 'function' && window.isGCurriculum()) {
                showToast('高三专题大纲下小节即考向，无需新增考点', 'info');
                return;
            }
            const backdrop = document.createElement('div');
            backdrop.id = 'addTopicModal';
            backdrop.className = 'fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-[70] flex items-center justify-center select-none';
            backdrop.innerHTML =
                '<div class="bg-white rounded-2xl w-full max-w-sm shadow-2xl p-5 space-y-3 border border-slate-100" role="dialog" aria-modal="true" aria-labelledby="addTopicTitle">' +
                    '<div class="flex items-center justify-between">' +
                        '<h3 id="addTopicTitle" class="text-sm font-bold text-slate-800">新增考点</h3>' +
                        '<button type="button" aria-label="关闭" onclick="document.getElementById(\'addTopicModal\').remove()" class="text-slate-400 hover:text-slate-600 text-lg leading-none cursor-pointer"><i class="fa-solid fa-xmark"></i></button>' +
                    '</div>' +
                    '<div class="text-[10px] text-slate-500 leading-relaxed">将写入大纲：' +
                        window.MathBankSafe.escapeText([comp, chap, know].join(' / ')) +
                    '</div>' +
                    '<input id="addTopicNameInput" type="text" maxlength="40" placeholder="输入考点名称（如：含参讨论）" class="glass-input w-full px-3 py-2 rounded-xl text-sm border border-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none">' +
                    '<div class="flex space-x-2 pt-1">' +
                        '<button type="button" onclick="document.getElementById(\'addTopicModal\').remove()" class="flex-1 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-semibold transition-all text-xs cursor-pointer">取消</button>' +
                        '<button type="button" id="addTopicConfirmBtn" class="flex-1 px-4 py-2 bg-brand-600/80 hover:bg-brand-600 text-white rounded-xl font-semibold transition-all text-xs cursor-pointer">确认新增</button>' +
                    '</div>' +
                '</div>';
            document.body.appendChild(backdrop);
            const input = document.getElementById('addTopicNameInput');
            if (topicInput.value.trim()) input.value = topicInput.value.trim();
            input.focus();
            input.select();
            const doAdd = () => {
                const newTopic = input.value.trim();
                const btn = document.getElementById('addTopicConfirmBtn');
                if (btn) btn.disabled = true;
                // 写入统一走 window.createCurriculumTopic（拆卷卡片同一个出口）。
                window.createCurriculumTopic(comp, chap, know, newTopic)
                .then(() => {
                    const modal = document.getElementById('addTopicModal');
                    if (modal) modal.remove();
                    showToast('考点已写入大纲，可在下拉中直接选用', 'success');
                    if (typeof window.loadCategories === 'function') {
                        return window.loadCategories({ reloadCurrentQuestion: false }).then(() => {
                            const comp2 = document.getElementById('editCompulsory');
                            const chap2 = document.getElementById('editChapter');
                            const know2 = document.getElementById('editKnowledge');
                            const topic2 = document.getElementById('editTopic');
                            if (comp2 && chap2 && know2 && topic2) {
                                comp2.value = comp;
                                comp2.onchange();
                                chap2.value = chap;
                                chap2.onchange();
                                know2.value = know;
                                know2.onchange();
                                topic2.value = newTopic;
                            }
                        });
                    }
                })
                .catch(err => {
                    const btn2 = document.getElementById('addTopicConfirmBtn');
                    if (btn2) btn2.disabled = false;
                    showToast('新增考点失败: ' + err.message, 'error');
                });
            };
            document.getElementById('addTopicConfirmBtn').addEventListener('click', doAdd);
            input.addEventListener('keydown', ev => {
                if (ev.key === 'Enter') {
                    ev.preventDefault();
                    doAdd();
                }
            });
            backdrop.addEventListener('mousedown', ev => {
                if (ev.target === backdrop) backdrop.remove();
            });
        };

        window.openEditTopicsModal = function() {
            const compSel = document.getElementById('editCompulsory');
            const chapSel = document.getElementById('editChapter');
            const knowSel = document.getElementById('editKnowledge');
            if (!compSel || !chapSel || !knowSel) return;
            const comp = compSel.value;
            const chap = chapSel.value;
            const know = knowSel.value;
            if (!comp || !chap || !know) {
                showToast('请先选择册次 / 章节 / 小节', 'info');
                return;
            }
            if (typeof window.isGCurriculum === 'function' && window.isGCurriculum()) {
                showToast('高三专题大纲下小节即考向，无需调整考点', 'info');
                return;
            }
            let meta;
            try {
                meta = JSON.parse(JSON.stringify(window.systemMetadata || {}));
            } catch (e) {
                meta = {};
            }
            const locate = function(m) {
                if (!m.curriculum) m.curriculum = {};
                if (!m.curriculum[comp]) m.curriculum[comp] = {};
                if (!Array.isArray(m.curriculum[comp][chap])) m.curriculum[comp][chap] = [];
                const list = m.curriculum[comp][chap];
                let idx = -1;
                for (let i = 0; i < list.length; i++) {
                    const it = list[i];
                    if ((it && typeof it === 'object' && it.name === know) || it === know) {
                        idx = i;
                        break;
                    }
                }
                if (idx === -1) {
                    list.push({ name: know, topics: [] });
                    idx = list.length - 1;
                } else if (typeof list[idx] === 'string') {
                    list[idx] = { name: know, topics: [] };
                }
                if (!Array.isArray(list[idx].topics)) list[idx].topics = [];
                return list[idx];
            };
            const section = locate(meta);
            const topics = section.topics.slice();

            const backdrop = document.createElement('div');
            backdrop.id = 'editTopicsModal';
            backdrop.className = 'fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-[70] flex items-center justify-center select-none';
            const esc = window.MathBankSafe ? window.MathBankSafe.escapeText : function(s) { return String(s); };
            backdrop.innerHTML =
                '<div class="bg-white rounded-2xl w-full max-w-lg shadow-2xl p-5 space-y-3 border border-slate-100" role="dialog" aria-modal="true" aria-labelledby="editTopicsTitle">' +
                    '<div class="flex items-center justify-between">' +
                        '<div>' +
                            '<h3 id="editTopicsTitle" class="text-sm font-bold text-slate-800">微调考点</h3>' +
                            '<div class="text-[10px] text-slate-500 mt-0.5">' + esc([comp, chap, know].join(' / ')) + '</div>' +
                        '</div>' +
                        '<button type="button" aria-label="关闭" onclick="document.getElementById(\'editTopicsModal\').remove()" class="text-slate-400 hover:text-slate-600 text-lg leading-none cursor-pointer"><i class="fa-solid fa-xmark"></i></button>' +
                    '</div>' +
                    '<div class="text-[10px] text-slate-500 leading-relaxed">修改保存后写入大纲，下次录题直接使用新候选；已录题目不受影响。删除需点行内 ×，取消即不生效。</div>' +
                    '<div id="editTopicsList" class="space-y-2 max-h-64 overflow-y-auto pr-1 custom-scrollbar"></div>' +
                    '<div class="flex items-center space-x-2 pt-1 border-t border-slate-100">' +
                        '<input id="editTopicsNewInput" type="text" maxlength="40" placeholder="新增考点名称（如：含参讨论）" class="glass-input flex-1 px-3 py-2 rounded-xl text-sm border border-slate-200 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 outline-none">' +
                        '<button type="button" id="editTopicsAddRowBtn" class="shrink-0 px-3 py-2 bg-brand-500/10 hover:bg-brand-500/20 text-brand-600 rounded-xl font-semibold transition-all text-xs cursor-pointer">添加</button>' +
                    '</div>' +
                    '<div class="flex space-x-2 pt-1">' +
                        '<button type="button" onclick="document.getElementById(\'editTopicsModal\').remove()" class="flex-1 px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-semibold transition-all text-xs cursor-pointer">取消</button>' +
                        '<button type="button" id="editTopicsSaveBtn" class="flex-1 px-4 py-2 bg-amber-500/90 hover:bg-amber-500 text-white rounded-xl font-semibold transition-all text-xs cursor-pointer">保存修改</button>' +
                    '</div>' +
                '</div>';
            document.body.appendChild(backdrop);

            const listEl = document.getElementById('editTopicsList');
            const rows = [];
            const renderRows = function() {
                listEl.innerHTML = '';
                rows.length = 0;
                topics.forEach(function(t, i) {
                    const row = document.createElement('div');
                    row.className = 'flex items-center space-x-2';
                    const input = document.createElement('input');
                    input.type = 'text';
                    input.maxLength = 40;
                    input.value = window.topicName(t);
                    input._topicId = (t && typeof t === 'object') ? (t.id || '') : '';
                    input.className = 'glass-input flex-1 px-3 py-2 rounded-xl text-sm border border-slate-200 focus:ring-2 focus:ring-amber-500 focus:border-amber-500 outline-none';
                    const del = document.createElement('button');
                    del.type = 'button';
                    del.title = '删除该考点';
                    del.className = 'shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-red-50 hover:bg-red-100 text-red-500 transition-all cursor-pointer';
                    del.innerHTML = '<i class="fa-solid fa-xmark"></i>';
                    del.addEventListener('click', function() {
                        rows.splice(i, 1);
                        topics.splice(i, 1);
                        renderRows();
                    });
                    row.appendChild(input);
                    row.appendChild(del);
                    rows.push(input);
                    listEl.appendChild(row);
                });
                if (topics.length === 0) {
                    listEl.innerHTML = '<div class="text-xs text-slate-400 text-center py-6">本小节暂无预置考点，可在下方输入新增</div>';
                }
            };
            renderRows();

            document.getElementById('editTopicsAddRowBtn').addEventListener('click', function() {
                const v = document.getElementById('editTopicsNewInput').value.trim();
                if (!v) { showToast('请输入考点名称', 'info'); return; }
                if (!topics.some(function(x) { return window.topicName(x) === v; })) topics.push({ name: v });
                document.getElementById('editTopicsNewInput').value = '';
                renderRows();
            });
            document.getElementById('editTopicsNewInput').addEventListener('keydown', function(ev) {
                if (ev.key === 'Enter') {
                    ev.preventDefault();
                    document.getElementById('editTopicsAddRowBtn').click();
                }
            });

            document.getElementById('editTopicsSaveBtn').addEventListener('click', function() {
                const collected = [];
                rows.forEach(function(inp) {
                    const v = inp.value.trim();
                    if (v && !collected.some(function(c) { return window.topicName(c) === v; })) {
                        collected.push({ id: inp._topicId || '', name: v });
                    }
                });
                const saveBtn = document.getElementById('editTopicsSaveBtn');
                saveBtn.disabled = true;
                section.topics = collected;
                fetch('/api/config/metadata', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(meta)
                })
                .then(function(r) {
                    if (!r.ok) {
                        return r.json().catch(function() { return null; }).then(function(d) {
                            throw new Error((d && d.detail) || ('HTTP ' + r.status));
                        });
                    }
                    return r.json();
                })
                .then(function() {
                    const modal = document.getElementById('editTopicsModal');
                    if (modal) modal.remove();
                    showToast('考点已更新（' + collected.length + ' 条），下次录题直接选用', 'success');
                    if (typeof window.loadCategories === 'function') {
                        return window.loadCategories({ reloadCurrentQuestion: false }).then(function() {
                            const c2 = document.getElementById('editCompulsory');
                            const h2 = document.getElementById('editChapter');
                            const k2 = document.getElementById('editKnowledge');
                            const t2 = document.getElementById('editTopic');
                            if (c2 && h2 && k2 && t2) {
                                c2.value = comp;
                                c2.onchange();
                                h2.value = chap;
                                h2.onchange();
                                k2.value = know;
                                k2.onchange();
                                t2.value = '';
                            }
                        });
                    }
                })
                .catch(function(err) {
                    saveBtn.disabled = false;
                    showToast('保存失败: ' + err.message, 'error');
                });
            });

            backdrop.addEventListener('mousedown', function(ev) {
                if (ev.target === backdrop) backdrop.remove();
            });
            const first = listEl.querySelector('input');
            if (first) { first.focus(); first.select(); }
        };


        // Format ISO Date string to Chinese local datetime: xxxx年xx月xx日xx时xx分
        function escapeEditorMetaText(value) {
            return window.MathBankSafe.escapeText(value);
        }

        function renderEditorPaperMeta() {
            const badges = document.getElementById('paperBadges');
            const sourceEl = document.getElementById('paperFooterSource');
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            const editTags = document.getElementById('editTags');

            if (!badges || !sourceEl || !editQType || !editDifficulty || !editSource) {
                return;
            }

            const seqBadge = EditorState.seqNum != null
                ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-100 text-slate-700">编号：#${escapeEditorMetaText(EditorState.seqNum)}</span>`
                : '<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-slate-200 text-slate-500">编号：新题目</span>';

            const createdAtBadge = EditorState.createdAt
                ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 inline-flex items-center"><i class="fa-regular fa-clock mr-1"></i>录入于：${escapeEditorMetaText(formatChineseDate(EditorState.createdAt))}</span>`
                : '';

            let paperTagsHtml = '';
            const tagsVal = editTags ? editTags.value.trim() : '';
            if (tagsVal) {
                const tagList = tagsVal.split(/[,，]+/).map(tag => tag.trim()).filter(tag => tag.length > 0);
                tagList.forEach(tag => {
                    paperTagsHtml += `<span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-amber-50 text-amber-600 border border-amber-300/60 flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[8px] text-amber-500 mr-1"></i>${escapeEditorMetaText(tag)}</span>`;
                });
            }

            const diffColor = (typeof getDifficultyColor === 'function')
                ? getDifficultyColor(editDifficulty.value)
                : 'bg-indigo-50 text-indigo-700';

            badges.innerHTML = `
                ${seqBadge}
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md bg-brand-50 text-brand-700">题型：${escapeEditorMetaText(getTypeText(editQType.value))}</span>
                <span class="text-[10px] font-bold px-2 py-0.5 rounded-md ${diffColor}">难度：${escapeEditorMetaText(getDifficultyText(editDifficulty.value))}</span>
                ${createdAtBadge}
                ${paperTagsHtml}
            `;
            sourceEl.textContent = `来源: ${editSource.value || '本地教研录入'}`;
        }
        window.renderEditorPaperMeta = renderEditorPaperMeta;

        // 2026-09-25 性能修复（方案 1）：把「每个 grid 一个 ResizeObserver」改成
        // 「全局单例 RO + 尺寸去重 + 单帧批量」。详细动机见 adaptChoicesGridLayout。
        //   choicesGridResizeObservers : WeakMap<grid, {width, height}>，存**上一次
        //     adapt 之后的 border-box 尺寸**，RO 回调拿它当基准做去重。键是 DOM
        //     节点，grid 被 innerHTML 换掉后条目自动回收，不需要手动清理。
        //   choicesGridPending   : 本帧待重算的 grid（Set 是强引用，故 flush 时
        //     显式剔除 isConnected === false 的脏引用）。
        //   choicesGridRafId     : 合并同一帧内多次请求用的 rAF 句柄（null = 空闲）。
        //   choicesGridResizeObserver : 全局唯一的 RO 实例（惰性创建）。
        const choicesGridResizeObservers = new WeakMap();
        const choicesGridPending = new Set();
        let choicesGridRafId = null;
        let choicesGridResizeObserver = null;
        // 滚轮映射只在 editor.js 求值时绑一次；组卷页反复调适配函数，靠这个标记
        // 保证 document 级监听不会被重复挂上去。
        let choicesMathWheelBound = false;

        function getPreferredChoicesColumns(grid) {
            const stored = parseInt(grid.dataset.preferredColumns || '', 10);
            if ([1, 2, 4].includes(stored)) return stored;
            if (grid.classList.contains('grid-cols-1')) return 1;
            if (grid.classList.contains('grid-cols-2')) return 2;
            return 4;
        }

        function choicesGridOverflows(grid) {
            return Array.from(grid.children).some(item => {
                const content = item.querySelector('.choices-content') || item;
                return content.scrollWidth > content.clientWidth + 1;
            });
        }

        // 2026-09-25 超宽公式兜底：降到 1 列仍装不下时，把整行选项等比缩小。
        //
        // 「装不下就降列」只能解决列数，而列数最低就是 1 —— 若算式本体的自然宽
        // 超过 1 列的可用宽（A4 内容宽 714px → 1 列时 `.choices-content` 可用
        // 690px），KaTeX 的 `.katex .base` 自带 white-space:nowrap，物理上折不了
        // 行，结果只剩一条横向滚动条，等于没排。
        //
        // 按最宽的那一格算出 scale 并**整行统一应用**（不是逐格），保证同一题的
        // A/B/C/D 字号一致。下限 FLOOR=0.5：再宽的式子宁可退回滚动条，也不缩成
        // 一团糊 —— 实测缩到 0.5 仍能覆盖 ≤1380px 的式子。
        //
        // 不用 transform: scale()：它不改变布局宽度，scrollWidth 依旧溢出、
        // 滚动条照样在，缩放文字还容易发虚；改 font-size 会真正回流，滚动条
        // 自然消失（CSS 侧对应 .choices-grid .choices-content 的 --choices-scale）。
        function fitOversizedChoicesContent(grid) {
            if (!grid || !grid.isConnected || grid.clientWidth <= 0) return;
            // 每次进来先回到 1:1：容器变宽（ResizeObserver 重入）后必须能重新
            // 测量，否则会一直挂着上一次的缩小值、再也回不到原字号。
            grid.style.removeProperty('--choices-scale');
            void grid.offsetWidth;

            const FLOOR = 0.5;
            let needed = 1;      // 最宽内容的 scrollW / 可用 clientW
            let hasKatex = false;
            const items = Array.from(grid.children);
            for (const item of items) {
                const content = item.querySelector('.choices-content') || item;
                if (content.querySelector('.katex')) hasKatex = true;
                const avail = content.clientWidth;
                if (avail > 0 && content.scrollWidth > avail + 1) {
                    needed = Math.max(needed, content.scrollWidth / avail);
                }
            }
            if (!hasKatex) return;    // 缩不动的东西（超宽图片）别白设变量
            if (needed <= 1) return;  // 本来就没溢出
            grid.style.setProperty(
                '--choices-scale',
                String(Math.max(FLOOR, Math.min(1, 1 / needed)))
            );
        }

        // 2026-09-25：列数一律「先试满 4 列，装不下再降」，不再受首选值掐死。
        //
        // 旧实现把候选表裁成 `[4,2,1].filter(cols => cols <= preferred)`，而
        // preferred 来自 \begin{choices} 渲染期的**源码字符数**估算（>18 字符→2、
        // >40 字符→1）。一条 32 字符的纯公式就被判成「长选项」→ preferred=2 →
        // 候选只剩 [2,1]，连「4 列到底装不装得下」这一问都没问过。
        //
        // 又：测量期必须真的不折行，否则内容一折行、`scrollWidth === clientWidth`
        // （这正是当初 nowrap 选择器写错 `> .katex` 的直接后果），检测器每次都
        // 看到「没溢出」，列数就此卡在首选值上下不来。
        //
        // 落地后摘掉 .is-measuring：最终 ≥2 列时内容本就装得下（不折行也不会
        // 溢出）；若降到 1 列仍装不下，就让它折行——与导出侧 exam-zh「减列、
        // 绝不折行」的排印语义对齐。
        // ── 单格适配拆成两步，供「逐格」与「整批」两条路径复用 ─────────────
        // 拆出来的唯一目的是**批量化**：`.is-measuring` 的加与摘都会触发 Tailwind
        // 运行时 JIT 的 MutationObserver 重扫**整个文档**（组卷页十余万节点）。
        // 原先每格各加各摘 → 近 200 格共约 400 次全文档扫描，这正是 Profiler 里
        // tailwind `pf` 占 41% CPU 的直接来源。批量化后全批只扫 2 次。

        // 步骤一：**必须在测量态（`.is-measuring` ⇒ nowrap）下**试列数，返回与 live
        // 等长的选中列数数组。
        //
        // 关键在「按列数整批试」而不是「按格逐个试」：同一轮里先把该轮列数**一次
        // 性**写进所有还没定的 grid（只让样式失效一次），再去统一测量 —— 布局在每轮
        // 第一次读时才发生一次，后续读复用同一份布局。逐格试的话每格都要自己触发
        // 一次强制布局：196 格 × 平均 1.5 档 ≈ 300 次，这正是批量化 class 之后
        // 剩余那几秒的来源（Profiler：measureChoicesGridColumns + 其强制的 native
        // 布局仍占大头）。整批后布局次数降到「档数」量级，与卡片数量无关。
        //
        // 与逐格版语义等价：某个 grid 在第 k 轮不溢出即定稿（对应原来的 break），
        // 之后各轮不再改它的列数；各 grid 的列数互不影响，因此「先全写后全读」与
        // 「各自写读」得到的布局结果一致。
        function measureChoicesGridsBatch(live) {
            // 2026-09-25 补丁 B：候选表恒为 [4,2,1]，不再按 preferred 裁剪。
            // 两条写入点（editor.js 渲染期 / import.js 解析卡）目前都写 4，filter
            // 恒等、是死逻辑；但只要有任何路径写入 1 或 2，候选表就会被裁剪成
            // 不含 4 的 [2,1] / [1]，「4 列到底装不装得下」这一问再也不会被问到，
            // 该题永远试不到 4 列（最坏情况直接锁死单列）。preferred 仍作兜底读取
            // 但不再裁剪；把列数上限交给浏览器实测。
            const picked = new Array(live.length).fill(0);

            // 测量前先回到 1:1：上一次可能因容器变窄而缩过，带着它测会偏小。
            for (const grid of live) grid.style.removeProperty('--choices-scale');

            for (const columns of [4, 2, 1]) {
                let pending = false;
                for (let i = 0; i < live.length; i++) {
                    if (picked[i]) continue;
                    live[i].style.setProperty('--choices-columns', String(columns));
                    pending = true;
                }
                if (!pending) break;          // 已全部定稿，后面几档不必再试
                // 先结算本轮的样式失效（读任意元素的布局属性都会触发一次全文档
                // 样式重算 + 布局），后面的测量就都复用这一份布局。
                void live[0].offsetWidth;
                for (let i = 0; i < live.length; i++) {
                    if (picked[i]) continue;
                    if (!choicesGridOverflows(live[i])) picked[i] = columns;
                }
            }
            for (let i = 0; i < live.length; i++) if (!picked[i]) picked[i] = 1;
            return picked;
        }

        // 步骤二：**必须在摘掉 `.is-measuring` 之后**落地。摘掉 nowrap 后 1 列装
        // 不下才允许折行；fitOversizedChoicesContent 也要在这个最终布局下测，否则
        // 会把「折行后其实装得下」判成溢出、把字号缩过头。
        //
        // 只有 selected === 1 才需要跑超宽兜底，两条依据：
        //   · ≥2 列时测量期已确认不溢出（choicesGridOverflows 返回 false）→ fit
        //     进去也只会走到 `needed <= 1` 直接 return；
        //   · 摘掉 nowrap 只会让内容更容易折行、更不容易溢出，不会反过来。
        // 而 fit 每次都带一次 `void grid.offsetWidth` 强制 reflow + 逐子元素读
        // scrollWidth/clientWidth，对占 94% 的多列 grid 纯属白付（组卷页实测
        // 196 格里只有 12 格降到 1 列）。缩放值不会因此残留：measureChoicesGridColumns
        // 每一轮进来都先清 --choices-scale，容器变宽后自然回到 1:1。
        // 超宽公式横向滚动（移植自上游 V2.7.0）：1 列 + 缩字号到 FLOOR 仍装不下时，
        // 给那一条公式加 .choices-math-scroll，由 CSS 给它自己的横向滚动条。
        // 每次先清空再重标，保证容器变宽 / 撤掉缩放后能自动摘掉滚动态。
        function markUnshrinkableChoiceMath(grid) {
            if (!grid || !grid.isConnected) return;
            grid.querySelectorAll('.choices-math-scroll').forEach(function (math) {
                math.classList.remove('choices-math-scroll');
                // 外层那一格的滚动态跟着一起清：仍只走这一趟查询，不加第二遍遍历。
                const staleContent = math.closest('.choices-content');
                if (staleContent) staleContent.classList.remove('choices-content-scrolled');
            });
            const scrolled = new Set();
            grid.querySelectorAll('.choices-content .katex').forEach(function (math) {
                if (math.closest('.katex-display')) return;
                const content = math.closest('.choices-content');
                if (!content) return;
                // 容差取 2px：fitOversizedChoicesContent 的比例来自取整后的
                // scrollWidth / clientWidth，缩完常常只差零点几像素，按 1px 判会把
                // 「其实已经装得下」的式子误标成滚动态、白挂一根滚动条。
                if (math.getBoundingClientRect().width > content.clientWidth + 2) {
                    math.classList.add('choices-math-scroll');
                    scrolled.add(content);
                }
            });
            // 式子已由内层滚动盒接管，给外层那一格挂标记，CSS 据此不再出第二条
            // （.choices-content 本身是 overflow-x: auto，混排格子会双层同时冒条）。
            // 但只在外层「确实没自己的东西要滚」时才挂：格子里若还藏着不可断的长词，
            // 外层那条滚动条是真在干活，压掉它等于把尾巴彻底藏起来（实测 220 个 Z）。
            scrolled.forEach(function (content) {
                if (content.scrollWidth > content.clientWidth + 2) return;
                content.classList.add('choices-content-scrolled');
            });
            // 到这一步才绑滚轮映射：没有溢出公式的页面不必为非 passive 的 wheel 监听付费。
            if (scrolled.size) bindChoicesMathWheelScroll();
        }

        // 滚轮映射：Windows 鼠标没有横向滚轮，指针停在超宽式子上推滚轮本该横向走。
        // 只接管「真的能横滚」的滚轮，且顶到左右两端就把事件放行给页面 —— 否则用户
        // 滚到头会发现整页推不动，像被一条公式卡住。触控板给了 deltaX 时优先用它。
        function scrollChoicesMathByWheel(math, deltaX, deltaY) {
            const maxX = math.scrollWidth - math.clientWidth;
            if (maxX <= 0) return false;
            const delta = Math.abs(deltaX) >= Math.abs(deltaY) ? deltaX : deltaY;
            if (!delta) return false;
            // 1px 容差：scrollWidth / clientWidth 都是取整值，正好贴边时别把事件吞掉。
            const atStart = math.scrollLeft <= 1;
            const atEnd = math.scrollLeft >= maxX - 1;
            if ((delta < 0 && atStart) || (delta > 0 && atEnd)) return false;
            math.scrollLeft = Math.min(Math.max(math.scrollLeft + delta, 0), maxX);
            return true;
        }

        // 懒绑：由 markUnshrinkableChoiceMath 第一次真标出溢出公式时调用，靠标记只生效一次。
        function bindChoicesMathWheelScroll() {
            if (choicesMathWheelBound) return;
            choicesMathWheelBound = true;
            document.addEventListener('wheel', function (event) {
                // Ctrl/Meta 是浏览器缩放快捷键，Alt 是各家横向滚动的既有惯例，都不抢。
                if (event.ctrlKey || event.metaKey || event.altKey) return;
                const target = event.target;
                if (!target || typeof target.closest !== 'function') return;
                const math = target.closest('.choices-math-scroll');
                if (!math) return;
                // Firefox 的滚轮按「行」、翻页按「页」给增量，不换算成像素就滑得没力气。
                let factor = 1;
                if (event.deltaMode === 1) {
                    factor = 16;
                } else if (event.deltaMode === 2) {
                    factor = math.clientHeight || 100;
                }
                if (scrollChoicesMathByWheel(math, event.deltaX * factor, event.deltaY * factor)) {
                    event.preventDefault();
                }
            }, { passive: false });
        }

        function applyChoicesGridColumns(grid, selected) {
            grid.style.setProperty('--choices-columns', String(selected));
            grid.dataset.choiceColumns = String(selected);
            if (selected === 1) {
                // 降到 1 列仍装不下时，KaTeX 物理折不了行、只剩横向滚动条 ——
                // 交给 fitOversizedChoicesContent 整行等比缩小（1:1 重测，幂等）。
                fitOversizedChoicesContent(grid);
                // 缩到下限之后还剩多少溢出，只有最终布局下量得准，故紧跟其后。
                markUnshrinkableChoiceMath(grid);
            }
        }

        function canAdaptChoicesGrid(grid) {
            return Boolean(grid) && grid.isConnected && grid.clientWidth > 0;
        }

        // 整批适配：把「进入测量态 → 整批试列数 → 摘测量态 → 逐格落地」四段分开。
        // class 切换从 2N 次收敛成 2 次；强制布局次数从「N × 档数」降到「档数」。
        function adaptChoicesGridsBatch(grids) {
            const live = [];
            for (const grid of grids) {
                if (!canAdaptChoicesGrid(grid)) continue;
                getPreferredChoicesColumns(grid);   // 保留原有的兜底读取语义
                live.push(grid);
            }
            if (!live.length) return;

            for (const grid of live) grid.classList.add('is-measuring');
            const picked = measureChoicesGridsBatch(live);
            // 测量期结束：撤掉 nowrap，1 列装不下时允许折行。
            for (const grid of live) grid.classList.remove('is-measuring');
            for (let i = 0; i < live.length; i++) {
                applyChoicesGridColumns(live[i], picked[i]);
            }
        }

        // 单格适配：对外语义与改造前完全一致（等价于只含一个 grid 的整批）。
        function adaptSingleChoicesGrid(grid) {
            adaptChoicesGridsBatch([grid]);
        }

        // 记录「刚 adapt 完」的尺寸，作为下一次 RO 回调的去重基准。
        // 必须在 adaptSingleChoicesGrid 之后读，拿到的才是适配后的最终值 —— 这样
        // RO 回调看到「尺寸没变」即可判定「这是我上一轮自己改出来的」，不再重算，
        // 自激循环在第一圈后即收敛。读 offsetWidth 会触发一次强制 reflow，但此处
        // 紧随 adaptSingleChoicesGrid（其内部本就要量 scrollWidth），属既有成本。
        function noteChoicesGridSize(grid) {
            choicesGridResizeObservers.set(grid, {
                width: grid.offsetWidth,
                height: grid.offsetHeight
            });
        }

        // 单帧批量：同一帧内被标记的 grid 只排一次 rAF，避免 N 个变更各排一帧。
        function flushChoicesGridPending() {
            choicesGridRafId = null;
            const pending = Array.from(choicesGridPending);
            choicesGridPending.clear();
            const grids = [];
            for (const grid of pending) {
                if (!grid.isConnected) continue;   // 已被换掉：丢弃，WeakMap 自行回收
                grids.push(grid);
            }
            if (!grids.length) return;
            adaptChoicesGridsBatch(grids);         // 整批一次，class 切换只 2 次
            for (const grid of grids) noteChoicesGridSize(grid);
        }

        function scheduleChoicesGridLayout(grid) {
            choicesGridPending.add(grid);
            if (choicesGridRafId !== null) return;
            if (typeof window.requestAnimationFrame === 'function') {
                choicesGridRafId = window.requestAnimationFrame(flushChoicesGridPending);
            } else {
                flushChoicesGridPending();     // 无 rAF 的极老环境：同步兜底
            }
        }

        // 全局唯一 RO：惰性创建一次，所有 grid 共用同一个实例。
        // 旧实现每个 grid 各 new 一个（组卷页实测 186 个），每个回调只认自己的
        // grid、且无任何去重 —— adapt 改尺寸 → 自己又被唤醒 → 再 adapt，如此往复。
        function ensureChoicesGridResizeObserver() {
            if (choicesGridResizeObserver || typeof ResizeObserver === 'undefined') return;
            choicesGridResizeObserver = new ResizeObserver(entries => {
                for (const entry of entries) {
                    const grid = entry.target;
                    const prev = choicesGridResizeObservers.get(grid);
                    if (!prev) { noteChoicesGridSize(grid); continue; }
                    // border-box 口径，与 noteChoicesGridSize 的 offsetWidth/Height 对齐
                    const bbs = entry.borderBoxSize;
                    const box = Array.isArray(bbs) ? bbs[0] : bbs;
                    const w = box ? box.inlineSize : grid.offsetWidth;
                    const h = box ? box.blockSize : grid.offsetHeight;
                    if (Math.abs(prev.width - w) < 0.5 && Math.abs(prev.height - h) < 0.5) {
                        continue;      // 尺寸没变 = 上一轮自己 adapt 的结果，跳过
                    }
                    noteChoicesGridSize(grid);
                    scheduleChoicesGridLayout(grid);
                }
            });
        }

        // 观察登记表：ResizeObserver.observe 持的是强引用。整卷重渲染走 innerHTML 替换，
        // 旧网格虽然已经脱离文档，却仍被观察器一并攥着 —— #28 压测实测 11 轮重渲染后
        // observe 调用 264 次、实际网格只有 22 个、unobserve 一次都没发生，242 个僵尸
        // 目标连同各自的子树整体无法回收。每次整批适配前回收一遍已断开的目标，
        // 让观察表数量与实际网格同阶。
        const choicesGridObserved = new Set();

        function pruneDetachedChoicesGridTargets() {
            if (!choicesGridResizeObserver) return;
            for (const grid of choicesGridObserved) {
                if (grid.isConnected) continue;
                choicesGridObserved.delete(grid);
                choicesGridResizeObserver.unobserve(grid);
            }
        }

        function adaptChoicesGrids(grids) {
            if (!grids.length) return;
            ensureChoicesGridResizeObserver();
            pruneDetachedChoicesGridTargets();   // 先放掉上一代脱离的目标，再登记这一代
            adaptChoicesGridsBatch(grids);       // 整批一次，class 切换只 2 次
            if (choicesGridResizeObserver) {
                for (const grid of grids) {
                    noteChoicesGridSize(grid);
                    choicesGridObserved.add(grid);
                    choicesGridResizeObserver.observe(grid);   // 重复 observe 同一元素是幂等的
                }
            }
        }

        function collectChoicesGrids(node, sink) {
            if (!node) return;
            if (node.matches && node.matches('.choices-grid')) sink.push(node);
            if (!node.querySelectorAll) return;
            const found = node.querySelectorAll('.choices-grid');
            for (let i = 0; i < found.length; i += 1) sink.push(found[i]);
        }

        function adaptChoicesGridLayout(root) {
            if (!root) return;
            const grids = [];
            collectChoicesGrids(root, grids);
            adaptChoicesGrids(grids);
        }
        window.adaptChoicesGridLayout = adaptChoicesGridLayout;

        // 虚拟滚动逐帧只会新增几张卡，但它们共用同一趟批处理：每格单独 adapt 的话，
        // prune + 强制重排要按格数重复做（万题压测里这是 p95 帧时长的主要来源之一）。
        // 调用方把这一帧新排版出来的节点交过来即可。
        function adaptChoicesGridNodes(nodes) {
            if (!nodes || !nodes.length) return;
            const grids = [];
            for (let i = 0; i < nodes.length; i += 1) collectChoicesGrids(nodes[i], grids);
            adaptChoicesGrids(grids);
        }
        window.adaptChoicesGridNodes = adaptChoicesGridNodes;

        // 自动高度：随内容伸缩 textarea，改善编辑体验

        // 2026-09-24 回退：曾用 CSS `field-sizing: content` 撑高，但其存在已知渲染
        // bug——撑高后点击坐标映射错乱，编辑框内点击任意位置插入符都停在框顶、不跟随
        // 点击。现改回 **纯 JS 撑高**：autoResizeTextarea 始终写 inline height，不依赖
        // field-sizing，也不注册任何"切窗后自愈"的钩子。

        function autoResizeTextarea(el, opts) {
            if (!el) return;
            opts = opts || {};
            const bindWindowResize = opts.bindWindowResize !== false;
            // 首次布局可能还没完成（容器尚未 display、字体未加载、滚动条未出现），
            // 此时 scrollHeight 为 0。历史 bug：直接 return 后就再没重试，框停在
            // 浏览器默认高度（~130px）把内容截断。改为定时重试，最多 10 次 × 100ms。
            let retryLeft = 10;
            function resize() {
                el.style.height = 'auto';               // 先重置，才能正确测量 scrollHeight
                const scrollH = el.scrollHeight;
                if (scrollH === 0 && retryLeft-- > 0) {
                    setTimeout(resize, 100);
                    return;                              // 量不到就先不动，等下一次重试
                }
                retryLeft = 10;                          // 已经量到，恢复重试额度
                // 底部留白按「实际行高 × 2」算，不硬编码 px —— 行高随字号/字体变化，
                // 写死 44/52 在别的字号下就不是「正好 2 行」了。
                const cs = getComputedStyle(el);
                let lineH = parseFloat(cs.lineHeight);
                if (!lineH || isNaN(lineH)) lineH = (parseFloat(cs.fontSize) || 14) * 1.5;
                const bottomPad = opts.bottomPadding != null
                    ? opts.bottomPadding
                    : Math.round(lineH * 2);
                const minH = opts.minHeight != null ? opts.minHeight : Math.round(lineH * 3);
                const maxH = opts.maxHeight != null ? opts.maxHeight : window.innerHeight * 0.7;
                const target = Math.min(Math.max(scrollH + bottomPad, minH), maxH);
                el.style.height = target + 'px';
                el.style.overflowY = (scrollH + bottomPad > maxH) ? 'auto' : 'hidden';
            }
            el.addEventListener('input', resize);
            if (bindWindowResize) {
                window.addEventListener('resize', resize);   // 视口变化时重算上限
            }
            // ResizeObserver：容器/布局变化时（Modal 展开、字体加载完、父级尺寸变化）
            // 自动重算。
            if (typeof ResizeObserver !== 'undefined' && !el.__autoResizeObs) {
                el.__autoResizeObs = new ResizeObserver(resize);
                el.__autoResizeObs.observe(el);
            }
            resize();                                    // 立即量一次（失败会自行重试）
            requestAnimationFrame(resize);               // 下一帧再量一次（布局更稳）
            return resize;                                // 返回给外部，加载题目时手动触发
        }
        window.autoResizeTextarea = autoResizeTextarea;

        function setupRealtimePreviews() {
            const editContent = document.getElementById('editContent');
            const editAnswer = document.getElementById('editAnswerMarkdown');

            // 真自适应：不传参数，由 autoResizeTextarea 按实际行高自己算
            // （内容高度 + 2 行留白；封顶视口 70% 后滚动）。
            const _resizeContent = autoResizeTextarea(editContent, {});
            const _resizeAnswer = autoResizeTextarea(editAnswer, {});
            window.__autoResizeContent = _resizeContent;
            window.__autoResizeAnswer = _resizeAnswer;

            // 上一次刷新时的正文快照：只有「刚才还在正文里、现在没了」的图才算被用户删掉。
            let contentTextForImageSync = '';

            // 判断一张已登记的配图在正文刷新后是否仍应保留登记。
            // 从没进过正文的登记必须原样保留：TikZ 渲染图、拆卷题干截图、MinerU/PDF 裁图
            // 都是按设计只登记在 image_paths、由配图条或绘图槽统一展示的（后端
            // reconcile_image_paths_with_text 的「宁留不错删」是同一条口径）。
            function illustrationRegistrationSurvives(path, previousText, currentText, claimedPaths) {
                if (!path) return false;
                if (currentText.indexOf(path) !== -1) return true;
                if (Array.isArray(claimedPaths) && claimedPaths.indexOf(path) !== -1) return true;
                return String(previousText || '').indexOf(path) === -1;
            }
            window.illustrationRegistrationSurvives = illustrationRegistrationSurvives;

            const updateContentPreview = () => {
                // 载入题目/恢复草稿时 textarea 是程序化赋值，不会触发 input；
                // 选项卡跟着这条共用预览链路刷新，避免残留上一题的选项。
                if (typeof window.refreshOptionEditorCard === 'function') {
                    window.refreshOptionEditorCard();
                }
                const text = editContent.value;
                const previewContainer = document.getElementById('contentPreview');
                const paperContainer = document.getElementById('paperContent');
                if (!previewContainer && !paperContainer) return;
                
                // Automatically sync illustrations list with text content
                if (uploadedImages.length > 0) {
                    const initialLength = uploadedImages.length;
                    const claimedPaths = typeof TikzState.assetImagePaths === 'function'
                        ? TikzState.assetImagePaths()
                        : TikzState.referencePaths();
                    uploadedImages = uploadedImages.filter((path) => illustrationRegistrationSurvives(
                        path, contentTextForImageSync, text, claimedPaths));
                    contentTextForImageSync = text;
                    if (uploadedImages.length !== initialLength) {
                        renderIllustrationBadges();
                    }
                }
                
                if (!text.trim()) {
                    if (previewContainer) previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将展示实时试卷排版效果。</p>';
                    return;
                }
                
                const preparedHtml = previewContainer ? renderQuestionPreviewContent(previewContainer, text) : null;
                renderQuestionPreviewContent(paperContainer, text, { preparedHtml: preparedHtml });
            };

            const updateAnswerPreview = () => {
                // Automatically synchronize answer image badges on manual or programmatic text changes
                if (typeof syncAnswerImagesFromMarkdown === 'function') {
                    syncAnswerImagesFromMarkdown();
                }
                const text = editAnswer.value;
                const previewContainer = document.getElementById('answerPreview');
                const paperContainer = document.getElementById('paperAnalysisContent');
                if (!paperContainer) return;
                
                if (!text.trim()) {
                    if (previewContainer) previewContainer.innerHTML = '<p class="text-slate-400 italic">在左侧输入解析内容，此处将实时展示极其精美的 LaTeX 排版...</p>';
                    paperContainer.innerHTML = '<p class="text-slate-400 italic">暂无解析内容。</p>';
                    return;
                }
                
                let html = parseMarkdownWithMath(text);
                if (previewContainer) previewContainer.innerHTML = html;
                paperContainer.innerHTML = html;
                
                try {
                    if (previewContainer) {
                        renderMathInElement(previewContainer, {
                            delimiters: [
                                {left: '$$', right: '$$', display: true},
                                {left: '$', right: '$', display: false},
                                {left: '\\(', right: '\\)', display: false},
                                {left: '\\[', right: '\\]', display: true}
                            ],
                            throwOnError: false
                        });
                    }
                    renderMathInElement(paperContainer, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    console.error(e);
                }
            };
            
            // Attach inputs
            editContent.addEventListener('input', debounce(updateContentPreview, 250));
            editAnswer.addEventListener('input', debounce(updateAnswerPreview, 250));
            
            const editReview = document.getElementById('editReview');
            const updateReviewPreview = () => {
                const text = editReview.value;
                const wrapper = document.getElementById('paperReviewWrapper');
                const content = document.getElementById('paperReviewContent');
                
                if (!text.trim()) {
                    if (wrapper) wrapper.classList.add('hidden');
                    if (content) content.innerHTML = '';
                    return;
                }
                
                if (wrapper) wrapper.classList.remove('hidden');
                let html = parseMarkdownWithMath(text);
                if (content) content.innerHTML = html;
                
                try {
                    if (content) {
                        renderMathInElement(content, {
                            delimiters: [
                                {left: '$$', right: '$$', display: true},
                                {left: '$', right: '$', display: false},
                                {left: '\\(', right: '\\)', display: false},
                                {left: '\\[', right: '\\]', display: true}
                            ],
                            throwOnError: false
                        });
                    }
                } catch(e) {
                    console.error('KaTeX review rendering error: ', e);
                }
            };
            editReview.addEventListener('input', debounce(updateReviewPreview, 250));
            
            // Expose update preview functions to global scope to allow synchronous direct updates when loading questions/drafts
            window.updateContentPreview = updateContentPreview;
            window.updateAnswerPreview = updateAnswerPreview;
            window.updateReviewPreview = updateReviewPreview;
            
            // Keep all editor metadata preview updates on one rendering path.
            const editQType = document.getElementById('editQType');
            const editDifficulty = document.getElementById('editDifficulty');
            const editSource = document.getElementById('editSource');
            const editTags = document.getElementById('editTags');

            if (editQType) editQType.addEventListener('change', renderEditorPaperMeta);
            if (editDifficulty) editDifficulty.addEventListener('change', renderEditorPaperMeta);
            if (editSource) editSource.addEventListener('input', renderEditorPaperMeta);
            ['editSrcYear', 'editSrcRegion', 'editSrcExamType', 'editSrcSemester'].forEach(function (sid) {
                const el = document.getElementById(sid);
                if (el) {
                    el.addEventListener('input', applyEditorSourceFields);
                    el.addEventListener('change', applyEditorSourceFields);
                }
            });
            const eSrcPaperTitle = document.getElementById('editSrcPaperTitle');
            if (eSrcPaperTitle) {
                eSrcPaperTitle.addEventListener('input', applyEditorSourceFields);
                eSrcPaperTitle.addEventListener('change', applyEditorSourceFields);
            }
            const eGradeSel = document.getElementById('editApplicableGrade');
            if (eGradeSel) eGradeSel.addEventListener('change', applyEditorSourceFields);
            const eSrcProv = document.getElementById('editSrcProvince');
            const eSrcCity = document.getElementById('editSrcCity');
            if (eSrcProv) eSrcProv.addEventListener('change', function () { fillEditorSrcCity(); applyEditorSourceFields(); });
            if (eSrcCity) eSrcCity.addEventListener('change', applyEditorSourceFields);
            const eSrcRegion2 = document.getElementById('editSrcRegion');
            if (eSrcRegion2) {
                eSrcRegion2.addEventListener('change', function () { handleCustomSelect('editSrcRegion', '输入学校简称（如 哈三中）'); matchEditorSrcSchoolToGeo(); });
            }
            const eSrcExamType2 = document.getElementById('editSrcExamType');
            if (eSrcExamType2) {
                eSrcExamType2.addEventListener('change', function () { handleCustomSelect('editSrcExamType', '输入考试类型（如 单元测试）'); });
            }
            loadEditorSourceGeo();
            if (editTags) editTags.addEventListener('input', renderEditorPaperMeta);
            
            // Initial render of meta badges
            renderEditorPaperMeta();
        }

        function cleanChoiceStemParentheses(text) {
            if (!text) return "";
            text = text.trim();
            text = text.replace(/(?:<br\s*\/?>\s*)+$/i, '').trim();

            // 逐个剥离末尾的“空括号”标记（如 (\quad)、(\qquad)、(\hspace{1cm})、
            // ( )、（ ）等）。历史实现使用带嵌套惰性量词的单条正则，
            // `(?:...*?)+$` 在长题干上会触发灾难性回溯，直接卡死浏览器主线程
            // （表现为只渲染出第一题、实时预览空白）。这里改成锚定末尾、
            // 单次匹配的循环，复杂度与文本长度线性相关。
            const trailingBlankPattern = /\$?\s*[\(（]\s*(?:\\quad|\\qquad|\\hspace\{[^{}]*\}|[\s\xa0\u3000_]+)*\s*[\)）]\s*\$?\s*$/;
            let cleaned = text;
            for (let guard = 0; guard < 50; guard += 1) {
                const before = cleaned;
                cleaned = cleaned.replace(trailingBlankPattern, '').trim();
                if (cleaned === before) break;
            }
            cleaned = cleaned.replace(/\\paren\b/g, '').trim();
            cleaned = cleaned.replace(/(?:<br\s*\/?>\s*)+$/i, '').trim();

            const sanitizedDollars = cleaned.replace(/\\\$/g, '');
            const dollarCount = (sanitizedDollars.match(/\$/g) || []).length;
            if (dollarCount % 2 !== 0) {
                cleaned += '$';
            }

            return cleaned;
        }
        window.cleanChoiceStemParentheses = cleanChoiceStemParentheses;

        function transformFillinMacro(clean) {
            if (!clean) return "";
            return clean.replace(/(\$?)\\fillin(?:\[([^\]]*?)\])?(?:\[([^\]]*?)\])?(\$?)/g, function(match, preDollar, p1, p2, postDollar, offset, fullString) {
                // 计算当前 \fillin 之前未转义 $ 的数量
                const preString = fullString.substring(0, offset).replace(/\\\$/g, '');
                const dollarsBefore = (preString.match(/\$/g) || []).length;
                const isInsideMath = (dollarsBefore % 2 !== 0) || (preDollar === '$');
                
                function isLengthStr(str) {
                    return /^\s*\d+(?:\.\d+)?\s*(?:cm|mm|in|pt|pc|em|ex)\s*$/i.test(str || '');
                }

                let innerTex = '\\underline{\\hspace{1.5cm}}';
                if (p1 !== undefined && p2 !== undefined) {
                    const len = isLengthStr(p1) ? p1 : '1.5cm';
                    innerTex = '\\underline{\\hspace{' + len + '}' + p2 + '\\hspace{' + len + '}}';
                } else if (p1 !== undefined) {
                    if (isLengthStr(p1)) {
                        innerTex = '\\underline{\\hspace{' + p1 + '}}';
                    } else {
                        innerTex = '\\underline{\\quad ' + p1 + ' \\quad}';
                    }
                }

                // 检查从当前位置往后，本行内是否还有未转义的 $
                const postString = fullString.substring(offset + match.length).replace(/\\\$/g, '');
                const nextDollarIdx = postString.indexOf('$');
                const nextNewlineIdx = postString.indexOf('\n');
                const hasClosingDollarInLine = (nextDollarIdx !== -1 && (nextNewlineIdx === -1 || nextDollarIdx < nextNewlineIdx));

                if (isInsideMath) {
                    if (postDollar === '$') {
                        // 紧跟 postDollar 为 $ 时必须将闭合 $ 重新补回，绝对不能将其吞掉
                        return innerTex + '$';
                    } else if (hasClosingDollarInLine) {
                        return innerTex;
                    } else {
                        // 句末漏写闭合 $ 时自动补齐闭合
                        return innerTex + '$';
                    }
                } else {
                    // 处于非数学环境中：防错隔离！若后方紧跟着 $（如 \fillin$. 且后续文本以 $ 开头），避免产生双 $$
                    if (postDollar === '$' || postString.trim().startsWith('$')) {
                        return '$' + innerTex;
                    }
                    return '$' + innerTex + '$';
                }
            });
        }

        function transformExamZhParenForPreview(text) {
            if (!text) return "";
            // 括号内部用两个**全角空格** U+3000，与导出侧 _render_choice_paren 的
            // 「（　　）」同宽（半角 &nbsp; 只有一半宽，看着挤、也和 PDF 不一致）。
            return text.replace(/\\paren\b/g, '<span class="exam-zh-paren-preview" role="img" aria-label="选择题作答括号">（\u3000\u3000）</span>');
        }

        // 全局图片解析表：basename -> 完整 URL。彻底 LaTeX 化后正文只写
        // \includegraphics{文件名}（无路径），预览靠这份表把文件名找回完整地址。
        // 导入预览期由 renderParsedCardPreview 用每道题的 image_paths 填充（指向 tmp/根目录）；
        // 已保存的题图片统一在 /static/uploads/{basename}，找不到时兜底根目录。
        // 用 typeof 守卫：本文件部分片段会被抽出来在 Node 里做单测，那里没有 window。
        if (typeof window !== "undefined") {
            window.__mathbankImgByBase = window.__mathbankImgByBase || {};
            window.registerMathbankImages = function (urlList) {
                if (!Array.isArray(urlList)) return;
                urlList.forEach(function (u) {
                    if (!u) return;
                    var base = String(u).split(/[?#]/)[0].split("/").pop();
                    if (base) window.__mathbankImgByBase[base] = u;
                });
            };
        }

        function preprocessFormulaForKaTeX(text) {
            if (!text) return "";
            
            // Clean up any historical \vphantom{...} or \strut from underline text to prevent KaTeX rendering artifact letters
            let clean = text.replace(/\\vphantom\s*\{\s*[^}]*?\}/g, '')
                            .replace(/\\strut\b/g, '');

            // Auto-heal punctuation inside math environment between \fillin and closing dollar (e.g. $ \fillin, $ -> $ \fillin $,) using safe replacer function
            clean = clean.replace(/(\$[^$]*?\\fillin)\s*([。，,；;！？!?\.]+)\s*\$/g, function(match, p1, p2) {
                return p1 + '$' + p2;
            });

            // Transform exam-zh \fillin macro into KaTeX compatible \underline with math environment awareness
            clean = transformFillinMacro(clean);

            // Safely escape raw < and > inside math environments to \lt and \gt without lookbehind regex for maximum browser compatibility
            clean = clean.replace(/\$([^\$]+?)\$/g, function(match, inner) {
                let safeInner = inner.replace(/\\</g, '@@ESCAPED_LESS@@')
                                     .replace(/</g, '\\lt ')
                                     .replace(/@@ESCAPED_LESS@@/g, '\\<')
                                     .replace(/\\>/g, '@@ESCAPED_GREAT@@')
                                     .replace(/>/g, '\\gt ')
                                     .replace(/@@ESCAPED_GREAT@@/g, '\\>');
                return '$' + safeInner + '$';
            });

            // Clean up illegal nesting like \underline{\quad $\mathbf{14}$ \quad} in KaTeX
            clean = clean.replace(/(\\underline\s*\{[^}]*?)\$([^$]+?)\$([^}]*?\})/g, function(match, p1, p2, p3) {
                return '$' + p1 + p2 + p3 + '$';
            });
            
            // If \underline{\hspace{...}} is directly exposed outside math environments, wrap it inside '$...$' so KaTeX scanner can parse it!
            clean = clean.replace(/(\$?)\\underline\s*\{\s*\\hspace\s*\{([^}]+?)\}\s*\}(\$?)/g, function(match, p1, p2, p3, offset, fullString) {
                const preString = fullString.substring(0, offset).replace(/\\\$/g, '');
                const dollarsBefore = (preString.match(/\$/g) || []).length;
                if (dollarsBefore % 2 !== 0 || p1 === '$' || p3 === '$') {
                    return match;
                }
                return '$\\underline{\\hspace{' + p2 + '}}$';
            });
            
            // Protect math blocks to avoid replacing spacing commands inside math environments
            const placeholders = [];
            const htmlPlaceholders = [];
            let placeholderCounter = 0;

            function savePlaceholder(match) {
                const placeholder = `@@MATH_PLACEHOLDER_${placeholderCounter++}@@`;
                placeholders.push({ placeholder, original: match });
                return placeholder;
            }

            // 汉字 ↔ 行内公式的间距归一（待办 #15，D 档：维持半角空格口径）。
            // 为什么必须等数学段全部换成 @@MATH_PLACEHOLDER_k@@、且非数学段的 HTML 标签被
            // 剥净之后再改写：此刻字符串只剩「纯文本 + 占位符」，按字符邻接插空格才不会误伤
            // 标签属性。判据与 XeLaTeX 侧同口径（实测 ctexart 下 `汉$x$汉` 与 `汉 $x$ 汉` 等宽，
            // 说明 xeCJK 自己会补 ecglue 并吸收源里的多余空格；而 `汉，$x$` 的标点旁不加缝）：
            // 只对汉字触发，全角标点一律不动，块级公式（`$$` 与 `\[`）独占一行也不补行内缝，
            // 换行不算「缝」——段间距交给既有的段落逻辑；选项段（choices 环境）与行首带选项标号的行
            // 整体豁免：选项宽度直接决定 .choices-grid 的列数，补缝会让窄栏无故掉档。
            // ⚠️ 严禁后行断言（AGENTS.md 第 2 节禁令），左右两侧各跑一趟捕获组替换。
            function normalizeCjkMathGlue(source) {
                const displayTokens = new Set();
                placeholders.forEach(function(entry) {
                    const original = String(entry.original).trim();
                    if (original.indexOf('$$') === 0 || original.indexOf('\\[') === 0) {
                        displayTokens.add(entry.placeholder);
                    }
                });
                function glueBeforeMath(match, han, token) {
                    return displayTokens.has(token) ? match : han + ' ' + token;
                }
                function glueAfterMath(match, token, han) {
                    return displayTokens.has(token) ? match : token + ' ' + han;
                }
                function glueInline(text) {
                    return text
                        .replace(/([\u4e00-\u9fff\u3400-\u4dbf])[ \t]*(@@MATH_PLACEHOLDER_\d+@@)/g, glueBeforeMath)
                        .replace(/(@@MATH_PLACEHOLDER_\d+@@)[ \t]*([\u4e00-\u9fff\u3400-\u4dbf])/g, glueAfterMath);
                }
                // 选项段整体豁免：下面的 choices 换境会把它变成 .choices-grid，而列数是拿选项的**真实像素宽**量出来的
                // （adaptChoicesGridLayout → choicesGridOverflows）。浏览器实测一个半角空格 = 4.73px，选项越贴死补得越多，
                // 窄容器里就会凭空掉一档列数（实测 4 列→2 列）。这里用与换境完全相同的正则，保证
                // 「豁免的区间」恰好等于「会变成网格的区间」，不会多豁也不会漏豁。
                // split 带捕获组：偶数段是正文（照常补缝），奇数段是选项区（原样拼回）。
                // 导入解析卡的选项还有另一种形态：没有 choices 环境，只是「A. 一段 / B. 一段」的独立段落，
                // 由 import.js 的 groupParsedCardOptionParagraphs 在 DOM 上重组进同一个 .choices-grid（判据原文：
                // import.js:6518 的行首标号与 import.js:6436 的 PARSED_BARE_CHOICE_LABEL_RE）。这些行的宽度同样决定列数，
                // 而且不豁免就会出现「解析卡里补了缝、点导入进正式编辑器又因 choices 豁免不补」的闪动，
                // 所以正则**逐字抄这两处**（含 [A-D] 边界，不自行扩写），保证两边永远同口径。
                const choiceLabelLine = /^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*[.．、,，:：)）]/;
                const choiceLabelBareLine = /^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*[.．、,，:：)）]?[ \t]*$/;
                function glueBodySegment(segment) {
                    // 按行判定：行首是选项标号就整行跳过；标号独占一行时连下一行（选项正文）一起跳过。
                    // 补缝本来就不跨行（[ \t]* 不吃 \n），因此拆行再拼回不改变原有行为。
                    const lines = segment.split('\n');
                    let bodyFollowsBareLabel = false;
                    for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
                        const head = lines[lineIndex].replace(/\r$/, '');
                        const isOptionLine = choiceLabelLine.test(head) || bodyFollowsBareLabel;
                        // 裸标号（「A.」独占一行）的下一行就是该选项的正文
                        bodyFollowsBareLabel = choiceLabelBareLine.test(head);
                        if (!isOptionLine) {
                            lines[lineIndex] = glueInline(lines[lineIndex]);
                        }
                    }
                    return lines.join('\n');
                }
                const segments = source.split(/(\\begin\{choices\}[\s\S]*?\\end\{choices\})/g);
                for (let index = 0; index < segments.length; index += 2) {
                    segments[index] = glueBodySegment(segments[index]);
                }
                return segments.join('');
            }

            // Markdown 管线表格的**唯一**构造器（表头/分隔行/表体 → HTML 表格）。
            // 两条管线（MinerU 早期转换、OCR 兜底）都必须走这里，避免两份实现漂移。
            // 关键约束：**只能挂 CSS 类，不能写内联 style**——MathBankSafe 的
            // DOMPurify 白名单（ALLOWED_ATTR）里没有 style，内联样式会被静默剥掉，
            // 表格就会退化成"没有框线、挤成一行文字"，样式一律由 app.css 的
            // .md-table-wrap / .md-table 提供。
            // 表头与分隔行列数不一致（AI/OCR 常见）时按最宽者补齐，避免错位。
            function buildPipeTableHtml(headerCells, bodyRows, aligns) {
                const rows = Array.isArray(bodyRows) ? bodyRows : [];
                const width = Math.max(
                    headerCells.length,
                    (aligns && aligns.length) || 0,
                    ...rows.map(row => row.length)
                );
                const pad = cells => {
                    const out = cells.slice(0, width);
                    while (out.length < width) out.push('');
                    return out;
                };
                const alignAt = idx => (aligns && aligns[idx]) || 'text-center';
                let html = '<div class="md-table-wrap"><table class="md-table"><tbody>';
                html += '<tr>';
                pad(headerCells).forEach((cell, idx) => {
                    html += '<th scope="col" class="' + alignAt(idx) + '">' + cell + '</th>';
                });
                html += '</tr>';
                rows.forEach(row => {
                    html += '<tr>';
                    pad(row).forEach((cell, idx) => {
                        html += '<td class="' + alignAt(idx) + '">' + cell + '</td>';
                    });
                    html += '</tr>';
                });
                html += '</tbody></table></div>';
                return html;
            }

            // Shared tabular -> HTML table converter. Extracted so that a
            // $$ ... $$ block wrapping a tabular (common in AI-parsed papers)
            // can be converted before the math-block placeholder sweep.
            function convertTabularEnvs(src) {
                return src.replace(/\\begin\{tabular\*?\}\s*\{([^}]*?)\}([\s\S]*?)\\end\{tabular\*?\}/g, function(match, colSpec, inner) {
                    const alignList = [];
                    let colIdx = 0;
                    for (let i = 0; i < colSpec.length; i++) {
                        const ch = colSpec[i].toLowerCase();
                        if (ch === "l" || ch === "c" || ch === "r" || ch === "p" || ch === "m") {
                            alignList[colIdx] = ch === "l" ? "text-left" : (ch === "r" ? "text-right" : "text-center");
                            colIdx++;
                        }
                    }

                    const normalizedInner = inner.replace(/\\\\\[[^\]]*?\]/g, "\\\\");
                    const rawRows = splitLatexTableParts(normalizedInner, "rows");
                    let activeRowspans = [];
                    let html = '<div class="overflow-x-auto my-3 max-w-full text-center select-none"><table class="inline-table mx-auto text-xs text-slate-700 dark:text-slate-200 border-collapse border border-slate-300 dark:border-slate-600 bg-slate-50/60 dark:bg-slate-800/40 rounded-lg shadow-sm"><tbody>';

                    rawRows.forEach((rowStr) => {
                        const trimmed = rowStr.trim();
                        if (!trimmed) return;

                        const isTopRule = /\\toprule/.test(trimmed);
                        const isMidRule = /\\midrule/.test(trimmed);
                        const isBottomRule = /\\bottomrule/.test(trimmed);
                        const cleanRow = trimmed.replace(/\\(?:hline|toprule|midrule|bottomrule)\b/g, "")
                                                .replace(/\\cline\s*\{[^}]*?\}/g, "")
                                                .trim();
                        if (!cleanRow) return;

                        let rowClass = "border-b border-slate-300 dark:border-slate-700 hover:bg-slate-100/40 dark:hover:bg-slate-700/30 transition-colors";
                        if (isTopRule) rowClass += " border-t-2 border-t-slate-800 dark:border-t-slate-200";
                        if (isMidRule) rowClass += " border-b-2 border-b-slate-600 dark:border-b-slate-400";
                        if (isBottomRule) rowClass += " border-b-2 border-b-slate-800 dark:border-b-slate-200";

                        html += '<tr class="' + rowClass + '">';
                        const rawCells = splitLatexTableParts(cleanRow, "cells");
                        const nextActiveRowspans = activeRowspans.map((count) => Math.max(0, count - 1));
                        let currentCol = 0;

                        rawCells.forEach((cellStr) => {
                            let cell = cellStr.trim();
                            let colspan = 1;
                            let rowspan = 1;
                            let alignClass = alignList[currentCol] || "text-center";

                            const multicolumn = parseLeadingLatexCommand(cell, "multicolumn", 3);
                            if (multicolumn) {
                                colspan = parseInt(multicolumn.groups[0], 10) || 1;
                                const spec = multicolumn.groups[1].toLowerCase();
                                if (spec.includes("l")) alignClass = "text-left";
                                else if (spec.includes("r")) alignClass = "text-right";
                                else alignClass = "text-center";
                                cell = (multicolumn.groups[2] + multicolumn.remainder).trim();
                            }

                            const multirow = parseLeadingLatexCommand(cell, "multirow", 3);
                            if (multirow) {
                                rowspan = parseInt(multirow.groups[0], 10) || 1;
                                cell = (multirow.groups[2] + multirow.remainder).trim();
                            }

                            let coveredByPriorRow = true;
                            for (let offset = 0; offset < colspan; offset++) {
                                if (!(activeRowspans[currentCol + offset] > 0)) {
                                    coveredByPriorRow = false;
                                    break;
                                }
                            }
                            if (!cell && coveredByPriorRow) {
                                currentCol += colspan;
                                return;
                            }

                            if (rowspan > 1) {
                                for (let offset = 0; offset < colspan; offset++) {
                                    nextActiveRowspans[currentCol + offset] = rowspan - 1;
                                }
                            }

                            cell = unescapeTableCellForHtml(cell);
                            const borderClass = "border border-slate-300 dark:border-slate-700";
                            const rowspanAttr = rowspan > 1 ? ' rowspan="' + rowspan + '"' : "";
                            html += '<td colspan="' + colspan + '"' + rowspanAttr + ' class="px-3 py-1.5 ' + borderClass + ' ' + alignClass + ' font-normal align-middle">' + cell + '</td>';
                            currentCol += colspan;
                        });

                        activeRowspans = nextActiveRowspans;
                        html += '</tr>';
                    });

                    html += '</tbody></table></div>';
                    return html;
                });
            }

            let tempText = clean;
            // MinerU 云端/VLM 经常直接吐 HTML <table> 源码。先把它们整体抠出来
            // 作为可信 HTML 占位符，否则后续 <[^>]*> 剥离会把标签全删、把
            // 表格源码当纯文本 escape 成红色文本。
            tempText = tempText.replace(/<table[\s\S]*?<\/table>/gi, function(match) {
                const ph = '@@TABLE_PLACEHOLDER_' + htmlPlaceholders.length + '@@';
                // 只挂类名，不写内联 style —— DOMPurify 白名单不含 style，
                // 边框/内边距由 app.css 的 .md-table 提供。
                const wrapped = '<div class="md-table-wrap">'
                    + match
                        .replace(/^<table[^>]*>/i, '<table class="md-table">')
                        .replace(/<\/table>$/i, '</table>')
                    + '</div>';
                htmlPlaceholders.push({ placeholder: ph, html: wrapped });
                return ph;
            });
            // MinerU 也常吐 Markdown 表格 | a | b |，转成 HTML table
            tempText = tempText.replace(/(?:^\|[^\n]+\|\n(?:^\|[\s:|-]+\|\n)(?:^\|[^\n]+\|\n?)+)/gm, function(match) {
                const rows = match.trim().split('\n').map(r => r.trim()).filter(Boolean);
                if (rows.length < 2) return match;
                // 第二行是分隔行 |---|---|；分隔行列数与表头不一致时由构造器补齐
                const headerCells = rows[0].split('|').slice(1, -1).map(c => c.trim());
                const bodyRows = rows.slice(2).map(r => r.split('|').slice(1, -1).map(c => c.trim()));
                const ph = '@@TABLE_PLACEHOLDER_' + htmlPlaceholders.length + '@@';
                htmlPlaceholders.push({
                    placeholder: ph,
                    html: buildPipeTableHtml(headerCells, bodyRows, null)
                });
                return ph;
            });
            tempText = tempText.replace(/\$\$([\s\S]*?)\$\$/g, function(match) {
                // A $$ block wrapping a tabular is not math: convert it to an
                // HTML table now and park it outside the math placeholder sweep,
                // otherwise KaTeX prints the \begin{tabular} source as red text.
                if (match.indexOf('\\begin{tabular') !== -1) {
                    const html = convertTabularEnvs(match);
                    const ph = '@@TABLE_PLACEHOLDER_' + htmlPlaceholders.length + '@@';
                    htmlPlaceholders.push({ placeholder: ph, html: html });
                    return ph;
                }
                return savePlaceholder(match);
            })
                               .replace(/\\\[([\s\S]*?)\\\]/g, savePlaceholder)
                               .replace(/\\\(([\s\S]*?)\\\)/g, savePlaceholder)
                               .replace(/\$([^\$]+?)\$/g, savePlaceholder);
            
            // Auto-heal exposed LaTeX math environments (e.g. \begin{cases}...\end{cases}) that lack $...$ wrapper
            tempText = tempText.replace(/\\begin\{(cases|aligned|matrix|pmatrix|bmatrix|array|equation|gather)\}([\s\S]*?)\\end\{\1\}/g, function(match) {
                return savePlaceholder('$' + match.trim() + '$');
            });
            
            // Strip HTML tags from non-math parts
            tempText = tempText.replace(/<[^>]*>/g, '');

            // 表格/图片等 HTML 段还在各自的占位符里，够不到；这里只管正文流的汉字↔公式。
            // 选项段由 normalizeCjkMathGlue 自己按 choices 环境边界整体跳过，不会把选项撑宽。
            tempText = normalizeCjkMathGlue(tempText);

            // exam-zh defines \paren outside math mode, while KaTeX does not.
            // Convert only the protected-and-sanitized text portion into the
            // same empty answer parentheses used by the A4 paper preview.
            tempText = transformExamZhParenForPreview(tempText);
            
            // Process LaTeX lists & environments outside math blocks
            tempText = tempText.replace(/\\\\\s*\\begin\{/g, '\\begin{')
                               .replace(/\\begin\{([^}]+?)\}\s*\\\\/g, '\\begin{$1}')
                               .replace(/\\\\\s*\\end\{/g, '\\end{')
                               .replace(/\\end\{([^}]+?)\}\s*\\\\/g, '\\end{$1}')
                               .replace(/\\\\\s*\\item/g, '\\item')
                               .replace(/\\item\s*\\\\/g, '\\item');

            // Process choices environment (exam-zh-choices)
            tempText = tempText.replace(/\\begin\{choices\}([\s\S]*?)\\end\{choices\}/g, function(match, inner) {
                const items = inner.split(/\\item/).map(item => item.trim()).filter(item => item.length > 0);
                const labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

                // 2026-09-25：首选列数固定为 4，不再按源码字符数估算。
                //
                // 旧判据（>18 字符→2 列、>40 字符→1 列）是拿**源码字节**当像素用：
                // LaTeX 里一条 `$\exists x \in R, x^2-2mx+m^2-4=0$` 源码才 32 字符，
                // 被判成「长选项」→ 4 个选项压成 2 列 2 行；可它 KaTeX 实测宽
                // 211px，A4 下 4 列内容宽 165px 装不下、2 列 367px 装得下 ——
                // 到底几列得量了才知道，凭字符数猜必错（近似值还把公式折算成 8
                // 星号，跟真实渲染毫无关系）。
                //
                // 与 import.js 重组选项段时写死的 data-preferred-columns="4" 同源。
                const preferredColumns = 4;
                const gridCols = 'grid-cols-4';
                let html = `<div class="grid ${gridCols} gap-2 my-2 select-none choices-grid items-baseline" data-preferred-columns="${preferredColumns}">`;
                items.forEach((item, idx) => {
                    const label = labels[idx] || (idx + 1);
                    let cleanItem = item;
                    // 若 OCR/导入把选项字母也写进了 \item 内容，渲染时会叠加上 choices-label 造成
                    // "A. A. -1" / "B. B. 1"。这里只剥离与当前序号对应的期望标签（如 A. / B. / 1. 等），
                    // 避免误删内容本身以 A 开头的文本。
                    const escapedLabel = String(label).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                    cleanItem = cleanItem.replace(new RegExp('^\\s*' + escapedLabel + '[\\.、．]\\s*', 'i'), '').trim();
                    // Auto-wrap LaTeX math macros (e.g. \dfrac{5}{2}) in choices option if missing $
                    if (/\\(dfrac|frac|sqrt|cdot|times|pm|le|ge|ne|in|vec|mathbf|mathrm|text|alpha|beta|gamma|delta|theta|pi|varphi|omega)\b/.test(cleanItem) && !/\$/.test(cleanItem)) {
                        cleanItem = '$' + cleanItem + '$';
                    }
                    html += `<div class="choices-item flex items-baseline"><span class="choices-label font-bold mr-1.5 text-slate-800 shrink-0">${label}.</span><span class="choices-content flex-1 [&>p]:m-0 [&>p]:inline">${cleanItem}</span></div>`;
                });
                html += '</div>';
                return html;
            });

            function splitLatexTableParts(source, mode) {
                const parts = [];
                let current = "";
                let braceDepth = 0;
                for (let i = 0; i < source.length; i++) {
                    const ch = source[i];
                    const next = source[i + 1];
                    if (ch === "\\") {
                        if (mode === "rows" && braceDepth === 0 && next === "\\") {
                            parts.push(current);
                            current = "";
                            i++;
                            continue;
                        }
                        if (mode === "rows" && braceDepth === 0 && source.slice(i, i + 3) === "\\cr") {
                            parts.push(current);
                            current = "";
                            i += 2;
                            continue;
                        }
                        current += ch;
                        if (i + 1 < source.length) {
                            current += source[i + 1];
                            i++;
                        }
                        continue;
                    }
                    if (ch === "{") braceDepth++;
                    else if (ch === "}" && braceDepth > 0) braceDepth--;
                    if (mode === "cells" && ch === "&" && braceDepth === 0) {
                        parts.push(current);
                        current = "";
                    } else {
                        current += ch;
                    }
                }
                parts.push(current);
                return parts;
            }

            function readLatexGroup(source, startIndex) {
                if (source[startIndex] !== "{") return null;
                let depth = 0;
                for (let i = startIndex; i < source.length; i++) {
                    if (source[i] === "\\" && i + 1 < source.length) {
                        i++;
                        continue;
                    }
                    if (source[i] === "{") depth++;
                    else if (source[i] === "}") {
                        depth--;
                        if (depth === 0) {
                            return { value: source.slice(startIndex + 1, i), end: i + 1 };
                        }
                    }
                }
                return null;
            }

            function parseLeadingLatexCommand(source, command, groupCount) {
                const trimmed = source.trim();
                const prefix = "\\" + command;
                if (!trimmed.startsWith(prefix)) return null;
                let cursor = prefix.length;
                const groups = [];
                for (let i = 0; i < groupCount; i++) {
                    while (/\s/.test(trimmed[cursor] || "")) cursor++;
                    const group = readLatexGroup(trimmed, cursor);
                    if (!group) return null;
                    groups.push(group.value);
                    cursor = group.end;
                }
                return { groups: groups, remainder: trimmed.slice(cursor).trim() };
            }

            function unescapeTableCellForHtml(value) {
                return value.replace(/\\textbackslash\{\}/g, "&#92;")
                            .replace(/\\textdollar\{\}/g, "$")
                            .replace(/\\textasciicircum\{\}/g, "^")
                            .replace(/\\textasciitilde\{\}/g, "~")
                            .replace(/\\&/g, "&amp;")
                            .replace(/\\%/g, "%")
                            .replace(/\\#/g, "#")
                            .replace(/\\_/g, "_")
                            .replace(/\\\{/g, "{")
                            .replace(/\\\}/g, "}");
            }

            // Process any remaining tabular environments outside $$ blocks
            // (brace-aware cell splitting with colspan/rowspan support).
            tempText = convertTabularEnvs(tempText);

            // Markdown pipe table -> HTML table (OCR fallback)
            tempText = (function(src) {
                var lines = src.split(/\r?\n/);
                var out = [];
                var i = 0;
                while (i < lines.length) {
                    var line = lines[i];
                    // a markdown table: current line starts with |, next line is separator |---|---|
                    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i+1]) && /\|/.test(lines[i+1])) {
                        var sepLine = lines[i+1];
                        var sepCells = sepLine.trim().replace(/^\||\|$/g, '').split('|').map(function(c){ return c.trim(); });
                        var aligns = sepCells.map(function(c){
                            var left = c.charAt(0) === ':';
                            var right = c.charAt(c.length-1) === ':';
                            if (left && right) return 'text-center';
                            if (right) return 'text-right';
                            if (left) return 'text-left';
                            return 'text-center';
                        });
                        // header
                        var headerCells = line.trim().replace(/^\||\|$/g, '').split('|')
                            .map(function(c){ return c.trim(); });
                        i += 2;
                        var bodyRows = [];
                        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
                            bodyRows.push(lines[i].trim().replace(/^\||\|$/g, '').split('|')
                                .map(function(c){ return c.trim(); }));
                            i++;
                        }
                        out.push(buildPipeTableHtml(headerCells, bodyRows, aligns));
                    } else {
                        out.push(line);
                        i++;
                    }
                }
                return out.join('\n');
            })(tempText);

            tempText = tempText.replace(/\\begin\{center\}/g, '<div class="text-center my-1">')
                               .replace(/\\end\{center\}/g, '</div>')
                               .replace(/\\item\s*\[([^\]]+?)\]/g, '</li><li class="my-0.5 list-none -ml-4">$1 ')
                               .replace(/\\item/g, '</li><li class="my-0.5">')
                               .replace(/\\begin\{itemize\}/g, '<ul class="list-disc pl-4 my-1">')
                               .replace(/\\begin\{enumerate\}/g, '<ol class="list-decimal pl-4 my-1">')
                               .replace(/\\end\{itemize\}/g, '</li></ul>')
                               .replace(/\\end\{enumerate\}/g, '</li></ol>')
                               .replace(/<ul class="list-disc pl-4 my-1">\s*<\/li>/g, '<ul class="list-disc pl-4 my-1">')
                               .replace(/<ol class="list-decimal pl-4 my-1">\s*<\/li>/g, '<ol class="list-decimal pl-4 my-1">');

            // Process LaTeX bold formatting outside math environments
            tempText = tempText.replace(/\\textbf\s*\{([^{}]*?)\}/g, '<strong>$1</strong>');

            // Replace spacing commands outside math blocks with non-breaking spaces for a clean sidebar preview
            tempText = tempText.replace(/\\\\qquad/g, '&nbsp;&nbsp;&nbsp;&nbsp;')
                               .replace(/\\\\quad/g, '&nbsp;&nbsp;')
                               .replace(/\\qquad/g, '&nbsp;&nbsp;&nbsp;&nbsp;')
                               .replace(/\\quad/g, '&nbsp;&nbsp;');
                               
            // Replace LaTeX line breaks with HTML br tags outside math environments
            tempText = tempText.replace(/\\\\/g, '<br>');
            
            // 小问分行自愈：单回车或标点后紧跟小问编号 (如 \n(1), \n(2), \n(i), \n（1）, \n①) 自动升格为段落换行 <br><br>
            // ⚠️ 圈号必须**独立成一支**。原先它写在 `[(（]?(?:…)[)）\.]` 里，要求后面还要跟
            // `)`/`）`/`.`；而 AI 的实际形态是裸的 `①求证：…`（后面直接跟汉字）→ 永远匹配
            // 不上，预览既不换行也不缩进（用户反馈过）。圈号后允许跟 `./．/、`：一起吃掉，
            // 否则会留下「① . 求证」这种中间多一个点的怪形态。
            tempText = tempText.replace(/(?:\r?\n|\s+|[。；;!！\.]\s*)([(（]?(?:[1-9]|10|[ivxIVX]+)[)）\.]|\([1-9]\)|（[1-9]）|\([ivxIVX]+\)|（[ivxIVX]+）|[①②③④⑤⑥⑦⑧⑨⑩][.．、]?)(?=\s*[\u4e00-\u9fa5a-zA-Z\$])/g, '<br><br>$1 ');

            // 严格遵循 LaTeX 标准规范：双回车 (\n\n+) 代表起新段落 (<br><br>)；单回车 (\n) 仅视为空格，不产生硬换行；显式 \\\\ 代表强制换行 (<br>)
            tempText = tempText.replace(/\r\n/g, '\n')
                               .replace(/\n\n+/g, '<br><br>')
                               .replace(/\n/g, ' ');

            // 小问分级缩进：圈号（①②③…）是 (1)/(2) 内部的**小小问**，必须比一级再深一层，
            // 否则预览里两者齐平、看不出层级（用户反馈过）。这段放在 <br><br> 归一化之后，
            // 因为此刻「段落」的边界已经统一成 <br><br>；段落内容直到下一个 <br><br> 或文本结束。
            // 只能挂 class 不能写 inline style —— DOMPurify 白名单（api.js RICH_HTML_CONFIG）
            // 只放行 class，style 属性会被剥掉。样式见 app.css 的 .preview-subitem。
            // 圈号段落包成块级 div 做分级缩进；不再带前导 <br>——div 是块级，
            // 自带换行，再叠一个 <br> 会多出一行间距（第6题 ①②③ 紧挨着显得空）。
            // 段间距完全交给 app.css 的 .preview-subitem margin-top 控制。
            tempText = tempText.replace(/(<br><br>|^)\s*([①②③④⑤⑥⑦⑧⑨⑩][\s\S]*?)(?=<br><br>|$)/g, function (_match, separator, segment) {
                return '<div class="preview-subitem">' + segment.replace(/\s+$/, '') + '</div>';
            });
                               
            // 彻底 LaTeX 化：\includegraphics[选项]{文件名} -> <img>。文件名按 basename 在
            // 全局解析表里找完整 URL（导入预览期指向 tmp，保存后指向根目录），找不到兜底根目录。
            // 与下面的 Markdown 插图渲染共用同一套自适应样式与点击放大。
            tempText = tempText.replace(/\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}/g, function (match, file) {
                const base = String(file).split(/[?#]/)[0].split("/").pop();
                const resolved = (window.__mathbankImgByBase && window.__mathbankImgByBase[base]) || ("/static/uploads/" + base);
                const safeSrc = window.MathBankSafe.safeImageUrl(resolved);
                if (!safeSrc) return "";
                const safeSrcAttr = window.MathBankSafe.escapeAttribute(safeSrc);
                return '<div class="my-2.5 text-center relative inline-block align-middle">'
                    + '<img src="' + safeSrcAttr + '" alt="题目配图" class="max-w-[220px] max-h-[180px] object-contain rounded-lg border border-slate-200 shadow-sm cursor-zoom-in hover:shadow-md hover:scale-[1.02] transition-all" data-safe-image-open="true" title="点击在新标签页查看高清原图">'
                    + '</div>';
            });

            // 转换 Markdown 题目插图与配图语法 ![](/static/uploads/xxx.png) 为精美自适应预览图
            tempText = tempText.replace(/!\[(.*?)\]\(([^)]+)\)/g, function(match, alt, src) {
                const safeSrc = window.MathBankSafe.safeImageUrl(src);
                if (!safeSrc) return '';
                const safeAlt = window.MathBankSafe.escapeAttribute(alt || '题目配图');
                const safeSrcAttr = window.MathBankSafe.escapeAttribute(safeSrc);
                // 只输出图片本体。「以此图重绘 TikZ」的按钮**绝不能**拼在这里：
                // sanitizeRichHtml 的 DOMPurify 白名单不含 <button>、也不放行 onclick，
                // 净化后按钮外壳会被拆掉、只剩一行 "TikZ" 文本——既点不动，
                // 又会被误读成"这张图已经是 TikZ"（用户反馈过）。
                // 该入口改由 decoratePreviewIllustrationButtons() 在净化之后用 DOM 追加。
                return `<div class="my-2.5 text-center relative inline-block align-middle">
                    <img src="${safeSrcAttr}" alt="${safeAlt}" class="max-w-[220px] max-h-[180px] object-contain rounded-lg border border-slate-200 shadow-sm cursor-zoom-in hover:shadow-md hover:scale-[1.02] transition-all" data-safe-image-open="true" title="点击在新标签页查看高清原图">
                </div>`;
            });
                               
            // Restore math blocks with HTML escaping. escapeHtml is the shared
            // MathBankSafe-backed helper declared in api.js.
            // Adjacent inline math such as $a$$b$$c$ can make a later
            // placeholder contain an earlier one. Restore in LIFO order so
            // the outer placeholder is expanded before its nested token.
            placeholders.slice().reverse().forEach(({ placeholder, original }) => {
                tempText = tempText.replace(placeholder, () => escapeHtml(original));
            });

            // Restore HTML table placeholders verbatim (already sanitized HTML).
            htmlPlaceholders.slice().reverse().forEach(({ placeholder, html }) => {
                tempText = tempText.replace(placeholder, () => html);
            });
            
            return tempText;
        }
        window.preprocessFormulaForKaTeX = preprocessFormulaForKaTeX;
            
        function parseMarkdownWithMath(text) {
            if (!text) return "";
            return window.MathBankSafe.sanitizeRichHtml(preprocessFormulaForKaTeX(text));
        }
        window.parseMarkdownWithMath = parseMarkdownWithMath;

        // 给预览里的 Markdown 配图补一个**真正可点**的「转 TikZ」入口。
        //
        // 为什么必须放在净化之后：sanitizeRichHtml 的 DOMPurify 白名单不含 <button>、
        // 且 ALLOW_DATA_ATTR: false + 不放行 onclick。任何拼进 HTML 的按钮都会被拆掉，
        // 只剩文本 "TikZ"，既点不动又像"已经是 TikZ"的标记。
        //
        // 跳过三类图：
        //  - 已经是 TikZ 渲染结果（文件名 tikz_*，或资产的 image_path）——它本来就是 TikZ；
        //  - AI 专用参考图（资产的 reference_image_path）——本就不该在正文里给操作入口。
        //
        // 「文件名以 tikz_ 开头」这一条并不可省：TikzState 是纯内存态，老题目刷新后
        // hydrate 到空的 content_tikz_assets、或资产记录因迁移丢失时，图还是那张
        // 转好的 TikZ，靠资产表已经判不出来。文件名是后端与前端共同承认的物理事实。
        function decoratePreviewIllustrationButtons(container) {
            if (!container || typeof container.querySelectorAll !== 'function') return;
            if (typeof document === 'undefined' || typeof window === 'undefined') return;
            if (typeof window.autoTikzFromIllustration !== 'function') return;

            const skipPaths = new Set();
            const tikzState = window.TikzState
                || (typeof TikzState !== 'undefined' ? TikzState : null);
            if (tikzState) {
                ['contentAssets', 'answerAssets'].forEach(key => {
                    const assets = Array.isArray(tikzState[key]) ? tikzState[key] : [];
                    assets.forEach(asset => {
                        const rendered = window.MathBankSafe.safeImageUrl(asset && asset.image_path);
                        const reference = window.MathBankSafe.safeImageUrl(
                            asset && asset.reference_image_path
                        );
                        if (rendered) skipPaths.add(rendered);
                        if (reference) skipPaths.add(reference);
                    });
                });
            }

            Array.prototype.forEach.call(container.querySelectorAll('img'), img => {
                // wrapper 是 img 的**父节点**（下面 insertBefore + appendChild 就是这么搭的），
                // 而 closest() 从 img 自身往上找祖先，永远找不到那个 wrapper —— 这道
                // 「别给同一张图挂第二个按钮」的守卫因此一直是空转的。增量重渲染时
                // （对同一容器再调一次本函数）旧按钮会原样留着、新按钮照样追加。
                if (img.parentElement
                    && img.parentElement.hasAttribute('data-preview-illustration')) return;
                const src = window.MathBankSafe.safeImageUrl(img.getAttribute('src'));
                if (!src) return;
                if (window.isTikzRenderImage(src)) return;
                if (skipPaths.has(src)) return;
                if (!img.parentNode) return;

                const wrapper = document.createElement('span');
                wrapper.className = 'relative inline-block align-middle';
                wrapper.setAttribute('data-preview-illustration', '1');
                img.parentNode.insertBefore(wrapper, img);
                wrapper.appendChild(img);

                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'absolute -bottom-2 left-1/2 -translate-x-1/2 px-2.5 py-1 '
                    + 'rounded-full bg-brand-500 hover:bg-brand-600 text-white text-[11px] '
                    + 'font-semibold shadow-lg transition-all active:scale-95 whitespace-nowrap '
                    + 'cursor-pointer';
                button.title = '以此图为参考，打开 TikZ 工作台重绘';
                button.innerHTML = '<i class="fa-solid fa-pen-ruler mr-0.5"></i>转 TikZ';
                button.addEventListener('click', function () {
                    window.autoTikzFromIllustration(src);
                });
                wrapper.appendChild(button);
            });
        }
        window.decoratePreviewIllustrationButtons = decoratePreviewIllustrationButtons;

        function renderQuestionPreviewContent(container, text, options = {}) {
            if (!container) return;
            const settings = options && typeof options === 'object' ? options : {};
            const includeImages = settings.includeImages !== false;
            let preparedHtml;
            if (typeof settings.preparedHtml === 'string') {
                preparedHtml = window.MathBankSafe.sanitizeRichHtml(settings.preparedHtml);
                if (!includeImages) {
                    preparedHtml = preparedHtml.replace(/<img\b[^>]*>/gi, '');
                }
            } else {
                let source = String(text || '');
                if (!includeImages) {
                    source = source
                        .replace(/!\[[^\]\n]*\]\(\s*(?:<[^>\n]*>|[^\s)]+)(?:\s+(?:"[^"\n]*"|'[^'\n]*'))?\s*\)/gi, '')
                        .replace(/<img\b[^>]*>/gi, '');
                }
                preparedHtml = parseMarkdownWithMath(source);
            }
            container.innerHTML = preparedHtml;
            try {
                renderMathInElement(container, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false},
                        {left: '\\(', right: '\\)', display: false},
                        {left: '\\[', right: '\\]', display: true}
                    ],
                    throwOnError: false
                });
            } catch (error) {
                console.error('KaTeX question preview rendering error: ', error);
            }
            adaptChoicesGridLayout(container);
            // 配图上的「转 TikZ」入口必须在净化之后追加（见函数注释）。
            decoratePreviewIllustrationButtons(container);
            return preparedHtml;
        }
        window.renderQuestionPreviewContent = renderQuestionPreviewContent;

        // Format raw OCR questions by detecting choice options and introducing nice line breaks
        function formatQuestionContent(text) {
            if (!text) return "";
            
            // Strip the LaTeX negative space command "\!" and thin space "\," which are cluttering
            let formatted = text.replace(/\\!/g, '').replace(/\\,/g, '');
            
            // 1. Check if it is actually a choice question with options A, B, C, D
            const hasA = /[\s,，、]*\bA(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasB = /[\s,，、]*\bB(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasC = /[\s,，、]*\bC(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const hasD = /[\s,，、]*\bD(?:[\.\s、，．]+|\b|\))/i.test(formatted);
            const isChoiceQuestion = hasA && hasB && hasC && hasD;
            
            // Protect math blocks from being replaced, and clean up formula-level exclamation noise
            const parts = formatted.split(/(\$\$[\s\S]*?\$\$|\$[^\$]+?\$)/g);
            for (let i = 0; i < parts.length; i++) {
                // If it is a math block (odd indices in split result)
                if (i % 2 === 1) {
                    // 1. Remove all Chinese full-width exclamation marks "！" inside math
                    parts[i] = parts[i].replace(/！/g, '');
                    // 2. Remove all standalone half-width exclamation marks "!" not preceded by numbers or letters
                    parts[i] = parts[i].replace(/(^|[^0-9a-zA-Z\)\}\]])!/g, '$1');
                    // 3. Remove any remaining negative spacing "\!" just in case
                    parts[i] = parts[i].replace(/\\!/g, '');
                    // 4. Remove all LaTeX thin spaces "\,"
                    parts[i] = parts[i].replace(/\\,/g, '');
                } else {
                    // Only modify non-math blocks (even indices in split result)
                    if (isChoiceQuestion) {
                        // Replace option labels with clean newlines and normalized format
                        parts[i] = parts[i]
                            .replace(/[\s,，、]*\b([A-D])(?:[\.\s、，．]+)(?!\$)/g, '\n\n$1. ')
                            .replace(/[\s,，、]*\(([A-D])\)(?!\$)/g, '\n\n($1) ')
                            .replace(/[\s,，、]*（([A-D])）(?!\$)/g, '\n\n($1) ');
                    }
                }
            }
            formatted = parts.join('');
            
            // Clean up any leading/trailing duplicate newlines
            formatted = formatted.replace(/\n{3,}/g, '\n\n').trim();
            
            return formatted;
        }

        // 🟢 智能心跳循环：每 15 秒向后台发送一次轻量级心跳
        // 只要题库页面（有任意标签页）处于打开状态，后台的 1 小时闲置自杀机制就不会被触发
        setInterval(() => {
            fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
        }, 15000);


        // 按卷分组视图：调用 /api/questions/papers 聚合，列出全部试卷，点击跳转到该卷扁平视图
        window.renderBankGroups = function () {
            const container = document.getElementById('questionsList');
            if (!container) return;
            const srcEl = document.getElementById('filterSource');
            const source = srcEl ? srcEl.value.trim() : '';
            let url = '/api/questions/papers';
            if (source) url += '?source=' + encodeURIComponent(source);
            container.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs"><i class="fa-solid fa-spinner fa-spin"></i> 加载试卷列表…</div>';
            fetch(url, { headers: { 'X-Local-Token': window.getLocalToken ? window.getLocalToken() : '' } })
                .then(r => r.json())
                .then(data => {
                    const groups = (data && data.groups) || [];
                    if (!groups.length) {
                        container.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs">没有可分组的试卷</div>';
                        return;
                    }
                    container.innerHTML = '';
                    groups.forEach(g => {
                        const row = document.createElement('div');
                        row.className = 'question-card bank-group-row p-3.5 mb-2 flex items-center justify-between cursor-pointer group hover:border-brand-400 transition-colors';
                        const meta = [g.year, g.school, g.exam_type].filter(Boolean).join(' · ');
                        row.innerHTML = `
                            <div class="min-w-0">
                                <div class="text-sm font-semibibold text-slate-800 dark:text-slate-100 truncate">${window.MathBankSafe.escapeText(g.source)}</div>
                                <div class="text-[10px] text-slate-400 mt-0.5">${window.MathBankSafe.escapeText(meta)}</div>
                            </div>
                            <div class="flex items-center gap-2 shrink-0">
                                <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-brand-50 text-brand-600">${g.count} 题</span>
                                <i class="fa-solid fa-chevron-right text-slate-300 group-hover:text-brand-500 transition-transform"></i>
                            </div>`;
                        row.addEventListener('click', () => {
                            const se = document.getElementById('filterSource');
                            if (se) se.value = g.source;
                            const ge = document.getElementById('filterGroupMode');
                            if (ge) {
                                ge.dataset.active = 'false';
                                ge.classList.remove('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                                ge.classList.add('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
                            }
                            const so = document.getElementById('filterSort');
                            if (so) so.value = 'paper_no';
                            loadQuestions();
                        });
                        container.appendChild(row);
                    });
                })
                .catch(() => { container.innerHTML = '<div class="p-6 text-center text-slate-400 text-xs">试卷列表加载失败</div>'; });
        };

        function buildBankExactMatchBanner(exactMatch, exactOnly) {
            if (!exactMatch) return null;
            const box = document.createElement('div');
            box.id = 'bankExactMatchBanner';
            box.setAttribute('role', 'status');
            box.className = 'exact-match-banner mx-1.5 mb-2 flex items-start gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-[11px] font-semibold text-brand-700 dark:border-brand-700/60 dark:bg-brand-900/30 dark:text-brand-200';
            const icon = document.createElement('i');
            icon.className = 'fa-solid fa-crosshairs mt-0.5';
            icon.setAttribute('aria-hidden', 'true');
            const text = document.createElement('span');
            const seq = exactMatch.seq_num == null ? '—' : exactMatch.seq_num;
            if (exactMatch.kind === 'seq_num') {
                text.textContent = `精准匹配 · 题号 #${seq}（内部 ID #${exactMatch.id}）`;
            } else if (exactOnly) {
                text.textContent = `ID 精确查找 · 内部 ID #${exactMatch.id}（已忽略筛选条件）`;
            } else {
                text.textContent = `精准匹配 · 内部 ID #${exactMatch.id}（题号 #${seq}）`;
            }
            box.appendChild(icon);
            box.appendChild(text);
            return box;
        }

        function loadQuestions(retryCount = 0) {
            const loadSequence = ++bankQuestionsLoadSequence;
            if (bankQuestionsRetryTimer) {
                clearTimeout(bankQuestionsRetryTimer);
                bankQuestionsRetryTimer = null;
            }
            if (bankQuestionsLoadController) {
                bankQuestionsLoadController.abort();
            }
            const requestController = typeof AbortController !== 'undefined'
                ? new AbortController()
                : null;
            bankQuestionsLoadController = requestController;

            const q = document.getElementById('searchInput').value;
            const qtype = document.getElementById('filterType').value;
            const difficulty = document.getElementById('filterDifficulty').value;
            const compulsory = document.getElementById('filterCompulsory').value;
            const chapter = document.getElementById('filterChapter').value;
            const source = document.getElementById('filterSource') ? document.getElementById('filterSource').value : '';
            const paperCategory = document.getElementById('filterPaperCategory') ? document.getElementById('filterPaperCategory').value : '';
            const examStage = document.getElementById('filterExamStage') ? document.getElementById('filterExamStage').value : '';
            const sortOrder = document.getElementById('filterSort') ? document.getElementById('filterSort').value : 'desc';
            const filterTikzPending = document.getElementById('filterTikzPending');
            const tikzPending = filterTikzPending && filterTikzPending.dataset.active === 'true';
            const requestedPage = Math.max(1, currentBankPage);

            const params = new URLSearchParams();
            if (q) params.append('q', q);
            if (qtype) params.append('qtype', qtype);
            if (difficulty) params.append('difficulty', difficulty);
            if (compulsory) params.append('compulsory', compulsory);
            if (chapter) params.append('chapter', chapter);
            if (source) params.append('source', source);
            if (paperCategory) params.append('paper_category', paperCategory);
            if (examStage) params.append('exam_stage', examStage);
            if (tikzPending) params.append('tikz_pending', '1');
            params.append('page', String(requestedPage));
            params.append('page_size', String(PAGE_LIMIT));
            params.append('sort', sortOrder === 'asc' ? 'asc' : 'desc');
            // 默认视图排除烂题（可由筛选区的「显示烂题」开关关闭）
            const excludeBadEl = document.getElementById('filterExcludeBad');
            if (!excludeBadEl || excludeBadEl.dataset.active !== 'false') {
                params.append('exclude_quality', 'bad');
            }
            
            const qListContainer = document.getElementById('questionsList');
            if (!qListContainer) return;

            // 按卷分组模式：渲染试卷聚合视图（不再走扁平列表）
            const groupEl = document.getElementById('filterGroupMode');
            if (groupEl && groupEl.dataset.active === 'true') {
                window.renderBankGroups();
                return;
            }
            qListContainer.setAttribute('aria-busy', 'true');
            const fetchOptions = requestController ? { signal: requestController.signal } : undefined;
            
            fetch(`/api/questions?${params.toString()}`, fetchOptions)
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`HTTP 状态码异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(payload => {
                    if (loadSequence !== bankQuestionsLoadSequence) return;
                    if (!payload || !Array.isArray(payload.items)) {
                        throw new Error('题库分页响应格式无效');
                    }

                    const questions = payload.items;
                    const parsedTotal = Number(payload.total);
                    const totalItems = Number.isFinite(parsedTotal) && parsedTotal >= 0 ? parsedTotal : 0;
                    const parsedTotalPages = Number(payload.total_pages);
                    const totalPages = Number.isFinite(parsedTotalPages) && parsedTotalPages > 0
                        ? parsedTotalPages
                        : Math.max(1, Math.ceil(totalItems / PAGE_LIMIT));
                    const parsedResponsePage = Number(payload.page);
                    const responsePage = Number.isFinite(parsedResponsePage) && parsedResponsePage > 0
                        ? parsedResponsePage
                        : requestedPage;

                    currentBankPage = Math.min(responsePage, totalPages);

                    const searchMeta = payload && payload.search && typeof payload.search === 'object'
                        ? payload.search
                        : null;
                    const exactMatch = searchMeta && searchMeta.exact_match
                        ? searchMeta.exact_match
                        : null;
                    const exactOnly = !!searchMeta && searchMeta.mode === 'id_exact';

                    qListContainer.innerHTML = '';
                    
                    if (totalItems === 0) {
                        const emptyTitle = exactOnly
                            ? `未找到 ID 为 ${String(q).trim().replace(/^#\s*/, '')} 的题目`
                            : '未找到匹配题目';
                        const emptyHint = exactOnly
                            ? '<p class="mt-1 text-[10px] text-slate-400"># 前缀按内部 ID 精确查找，不会退回全文搜索</p>'
                            : '';
                        qListContainer.innerHTML = `
                            <div class="p-6 text-center text-slate-400 text-xs">
                                <i class="fa-solid fa-box-open text-2xl mb-1 text-slate-400"></i>
                                <p>${window.MathBankSafe.escapeText(emptyTitle)}</p>
                                ${emptyHint}
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        return;
                    }

                    if (exactMatch) {
                        const banner = buildBankExactMatchBanner(exactMatch, exactOnly);
                        if (banner) qListContainer.appendChild(banner);
                    }
                    
                    // 质量标签：四档单击循环（普通→好题→经典→烂题→普通）
                    const QUALITY_LABEL = { 0: '普通', 1: '好题', 2: '经典', '-1': '烂题' };
                    const QUALITY_NEXT = { 0: 1, 1: 2, 2: -1, '-1': 0 };
                    const QUALITY_BADGE_CLASS = {
                        0: 'text-slate-400 bg-transparent border-slate-200 dark:text-slate-500 dark:border-slate-700',
                        1: 'text-emerald-600 bg-emerald-50 border-emerald-300/60 dark:bg-emerald-900/30 dark:text-emerald-400',
                        2: 'text-amber-600 bg-amber-50 border-amber-300/60 dark:bg-amber-900/30 dark:text-amber-400',
                        '-1': 'text-rose-600 bg-rose-50 border-rose-300/60 dark:bg-rose-900/30 dark:text-rose-400',
                    };
                    function qualityBadgeHtml(level) {
                        const lv = String(Number(level) || 0);
                        return `<span class="quality-badge text-[9px] font-bold px-1.5 py-0.5 rounded-full border cursor-pointer hover:opacity-80 transition-opacity ${QUALITY_BADGE_CLASS[lv]}" title="点击切换质量标签（普通→好题→经典→烂题→普通）">${QUALITY_LABEL[lv]}</span>`;
                    }

                    questions.forEach(item => {
                        // Create card element
                        const difficultyBadge = getDifficultyBadge(item.difficulty);
                        const typeText = getTypeText(item.question_type);
                        const qualityLevel = Number(item.quality) || 0;

                        const itemCard = document.createElement('div');
                        itemCard.className = `question-card p-3.5 mx-1.5 flex flex-col space-y-2 select-none group relative ${EditorState.questionId === item.id ? 'active' : ''}`;
                        itemCard.dataset.id = item.id;
                        
                        const cleanContent = parseMarkdownWithMath(item.content || '');
                        
                        let tagsHtml = '';
                        if (item.tags) {
                            const tagList = item.tags.split(/[,，]+/).map(t => t.trim()).filter(t => t.length > 0);
                            if (tagList.length > 0) {
                                const displayTags = tagList.slice(0, 2);
                                const hiddenCount = tagList.length - 2;
                                
                                displayTags.forEach(tag => {
                                    tagsHtml += `<span class="text-[9px] font-bold text-amber-600 bg-amber-50 border border-amber-300/60 px-1.5 py-0.5 rounded-full flex items-center space-x-0.5"><i class="fa-solid fa-tag text-[7px] text-amber-500 mr-0.5"></i><span class="max-w-[80px] truncate">${window.MathBankSafe.escapeText(tag)}</span></span>`;
                                });
                                
                                if (hiddenCount > 0) {
                                    const fullTagsHtml = tagList.map(tag => `<span class="inline-flex items-center whitespace-nowrap"><i class="fa-solid fa-tag text-[7px] text-amber-500/80 mr-1"></i>${window.MathBankSafe.escapeText(tag)}</span>`).join('<span class="mx-1.5 text-amber-300/50">|</span>');
                                    tagsHtml += `
                                    <div class="relative flex items-center" onclick="event.stopPropagation()">
                                        <span class="peer text-[9px] font-bold text-amber-600 bg-amber-100 border border-amber-300/60 px-1.5 py-0.5 rounded-full cursor-default flex items-center shadow-sm hover:bg-amber-200 transition-colors">+${hiddenCount}</span>
                                        <div class="absolute top-full right-0 mt-1.5 w-max max-w-[220px] bg-amber-50 border border-amber-200/80 text-amber-800 text-[10px] px-2.5 py-1.5 rounded-lg shadow-md opacity-0 pointer-events-none peer-hover:opacity-100 transition-opacity duration-150 z-50 font-medium invisible peer-hover:visible">
                                            <div class="flex flex-wrap items-center leading-relaxed">
                                                ${fullTagsHtml}
                                            </div>
                                        </div>
                                    </div>`;
                                }
                            }
                        }

                        itemCard.innerHTML = `
                            <div class="flex items-start justify-between">
                                <div class="flex items-center gap-2 flex-wrap">
                                    <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0 mt-0.5">${window.MathBankSafe.escapeText(typeText)}</span>
                                    ${difficultyBadge}
                                </div>
                                <div class="flex items-center gap-1.5 justify-end flex-wrap flex-1 ml-2 pr-9">
                                    ${qualityBadgeHtml(qualityLevel)}
                                    ${tagsHtml}
                                    ${exactMatch && exactMatch.id === item.id
                                        ? '<span class="text-[9px] font-extrabold px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-200 shrink-0">精准匹配</span>'
                                        : ''}
                                    <span class="text-[10px] font-extrabold px-1.5 py-0.5 rounded bg-brand-50 text-brand-600 shadow-sm">#${window.MathBankSafe.escapeText(item.seq_num)}</span>
                                </div>
                            </div>
                            <!-- Delete Button -->
                            <button type="button" aria-label="删除题目" onclick="event.stopPropagation(); deleteQuestion(${item.id})" class="absolute top-3 right-3 w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-red-500 transition-colors" title="删除此题">
                                <i class="fa-solid fa-trash-can text-[13px]"></i>
                            </button>
                            <div class="text-xs text-slate-700 leading-relaxed font-medium line-clamp-2 card-formula-render">${cleanContent || '[空白题干]'}</div>
                            <!-- Time Badge -->
                            <div class="text-[8px] text-slate-400/80 flex items-center space-x-1 py-0.5">
                                <i class="fa-regular fa-clock text-[8px]"></i>
                                <span>录入：${window.MathBankSafe.escapeText(formatChineseDate(item.created_at))}</span>
                            </div>
                            <div class="flex justify-between items-center text-[9px] text-slate-400 border-t pt-1.5">
                                <span class="truncate max-w-[120px] font-semibold"><i class="fa-solid fa-folder-open mr-0.5"></i>${window.MathBankSafe.escapeText(item.category_knowledge || item.category_chapter || '未分类')}</span>
                                <span class="font-mono text-slate-400">${window.MathBankSafe.escapeText(item.source ? item.source.substring(0, 12) : '本地录入')}</span>
                            </div>
                        `;
                        
                        // Render KaTeX inline for this card
                        try {
                            renderMathInElement(itemCard.querySelector('.card-formula-render'), {
                                delimiters: [
                                    {left: '$$', right: '$$', display: false},
                                    {left: '$', right: '$', display: false},
                                    {left: '\\(', right: '\\)', display: false},
                                    {left: '\\[', right: '\\]', display: false}
                                ],
                                throwOnError: false
                            });
                        } catch(e) {
                            console.error('KaTeX sidebar rendering error: ', e);
                        }

                        // 质量标签单击循环
                        const qBadge = itemCard.querySelector('.quality-badge');
                        if (qBadge) {
                            qBadge.addEventListener('click', (e) => {
                                e.stopPropagation();
                                const cur = Number(item.quality) || 0;
                                const nxt = QUALITY_NEXT[String(cur)];
                                fetch(`/api/questions/${item.id}/quality`, {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json', 'X-Local-Token': window.getLocalToken ? window.getLocalToken() : '' },
                                    body: JSON.stringify({ level: nxt })
                                }).then(r => r.json()).then(res => {
                                    if (!res || res.status !== 'success') { if (window.showToast) window.showToast('质量标签更新失败', 'error'); return; }
                                    item.quality = res.level;
                                    qBadge.className = `quality-badge text-[9px] font-bold px-1.5 py-0.5 rounded-full border cursor-pointer hover:opacity-80 transition-opacity ${QUALITY_BADGE_CLASS[String(res.level)]}`;
                                    qBadge.textContent = QUALITY_LABEL[String(res.level)];
                                    if (res.level === -1 && document.getElementById('filterExcludeBad') && document.getElementById('filterExcludeBad').dataset.active === 'true') {
                                        itemCard.remove();
                                    }
                                    if (window.showToast) window.showToast('质量：' + QUALITY_LABEL[String(res.level)], 'success');
                                }).catch(() => { if (window.showToast) window.showToast('质量标签更新失败', 'error'); });
                            });
                        }

                        itemCard.onclick = () => {
                            checkAndSwitch(() => {
                                if (typeof window.selectWorkspace === 'function') {
                                    window.selectWorkspace('bank', '录题工作台');
                                }
                                selectQuestion(item);
                            });
                        };
                        
                        qListContainer.appendChild(itemCard);
                    });
                    
                    renderSidebarPagination(totalItems, currentBankPage, 'bank');
                })
                .catch(err => {
                    if ((err && err.name === 'AbortError') || loadSequence !== bankQuestionsLoadSequence) {
                        return;
                    }
                    console.error('加载题库列表发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载题库数据...`);
                        bankQuestionsRetryTimer = setTimeout(() => {
                            if (loadSequence === bankQuestionsLoadSequence) {
                                loadQuestions(retryCount + 1);
                            }
                        }, 1500);
                    } else {
                        qListContainer.innerHTML = `
                            <div class="p-6 text-center text-red-500 text-xs">
                                <i class="fa-solid fa-triangle-exclamation text-2xl mb-1 text-red-400"></i>
                                <p class="font-semibold">获取题库列表失败</p>
                                <p class="text-[10px] text-slate-500 mt-0.5 mb-2.5">后台服务正在启动或连接超时</p>
                                <button onclick="loadQuestions()" class="px-3.5 py-1.5 bg-red-50 hover:bg-red-100 text-red-600 font-bold rounded-xl transition-all border border-red-200 hover:scale-95 text-[10px] inline-flex items-center space-x-1 cursor-pointer">
                                    <i class="fa-solid fa-arrows-rotate"></i><span>重新加载</span>
                                </button>
                            </div>`;
                        renderSidebarPagination(0, 1, 'bank');
                        showToast('系统正在连接或初始化后台，加载题库失败，请稍后刷新重试', 'error');
                    }
                })
                .finally(() => {
                    if (loadSequence !== bankQuestionsLoadSequence) return;
                    qListContainer.removeAttribute('aria-busy');
                    if (bankQuestionsLoadController === requestController) {
                        bankQuestionsLoadController = null;
                    }
                });
        }

        // ==========================================
        //       SIDEBAR PAGINATION SYSTEM HELPERS
        // ==========================================
        function renderSidebarPagination(totalItems, currentPage, tabType) {
            const container = document.getElementById('sidebarPagination');
            if (!container) return;
            
            if (totalItems === 0) {
                container.innerHTML = '';
                container.style.display = 'none';
                return;
            }
            container.style.display = 'flex';
            
            const totalPages = Math.ceil(totalItems / PAGE_LIMIT) || 1;
            
            // Build pages array with sliding window folding
            let pages = [];
            if (totalPages <= 5) {
                for (let i = 1; i <= totalPages; i++) {
                    pages.push(i);
                }
            } else {
                pages.push(1);
                
                let start = Math.max(2, currentPage - 1);
                let end = Math.min(totalPages - 1, currentPage + 1);
                
                if (currentPage <= 3) {
                    end = 4;
                }
                if (currentPage >= totalPages - 2) {
                    start = totalPages - 3;
                }
                
                if (start > 2) {
                    pages.push('...');
                }
                
                for (let i = start; i <= end; i++) {
                    pages.push(i);
                }
                
                if (end < totalPages - 1) {
                    pages.push('...');
                }
                
                pages.push(totalPages);
            }
            
            let pagesHtml = '';
            pages.forEach(p => {
                if (p === '...') {
                    pagesHtml += `<span class="pagination-ellipsis">...</span>`;
                } else {
                    pagesHtml += `<button class="pagination-btn ${p === currentPage ? 'active' : ''}" onclick="goToSidebarPage(${p}, '${tabType}')">${p}</button>`;
                }
            });
            
            container.innerHTML = `
                <div class="flex items-center justify-between text-[10px] text-slate-400 font-semibold px-0.5">
                    <span>共 ${totalItems} 题 / ${totalPages} 页</span>
                    <div class="flex items-center space-x-1">
                        <span>跳转至</span>
                        <input type="number" min="1" max="${totalPages}" value="${currentPage}" class="pagination-jump-input" onkeydown="if(event.key==='Enter') jumpToSidebarPage(this.value, ${totalPages}, '${tabType}')">
                        <span>页</span>
                    </div>
                </div>
                <div class="sidebar-pagination-controls flex items-center justify-center space-x-1">
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === 1 ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage - 1}, '${tabType}')">
                        <i class="fa-solid fa-chevron-left text-[9px] mr-0.5"></i>上一页
                    </button>
                    ${pagesHtml}
                    <button class="pagination-btn pagination-btn-nav" ${currentPage === totalPages ? 'disabled' : ''} onclick="goToSidebarPage(${currentPage + 1}, '${tabType}')">
                        下一页<i class="fa-solid fa-chevron-right text-[9px] ml-0.5"></i>
                    </button>
                </div>
            `;
        }
        
        function goToSidebarPage(page, tabType) {
            if (tabType === 'bank') {
                currentBankPage = page;
                loadQuestions();
            } else {
                currentDraftPage = page;
                loadDrafts();
            }
            // Scroll questionsList back to top gently
            const qListContainer = document.getElementById('questionsList');
            if (qListContainer) {
                qListContainer.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }
        
        function jumpToSidebarPage(value, maxPage, tabType) {
            let page = parseInt(value, 10);
            if (isNaN(page)) return;
            if (page < 1) page = 1;
            if (page > maxPage) page = maxPage;
            goToSidebarPage(page, tabType);
        }


// 本地题库筛选条件事件绑定
document.addEventListener('DOMContentLoaded', () => {
    // 搜索框
    const searchInput = document.getElementById('bankListSearch');
    if (searchInput) {
        searchInput.addEventListener('input', () => {
            // 同步到searchInput（loadQuestions读取的是searchInput）
            const oldSearch = document.getElementById('searchInput');
            if (oldSearch) oldSearch.value = searchInput.value;
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 题型筛选
    const filterType = document.getElementById('filterType');
    if (filterType) {
        filterType.addEventListener('change', () => {
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 难度筛选
    const filterDifficulty = document.getElementById('filterDifficulty');
    if (filterDifficulty) {
        filterDifficulty.addEventListener('change', () => {
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 册次筛选
    const filterCompulsory = document.getElementById('filterCompulsory');
    if (filterCompulsory) {
        filterCompulsory.addEventListener('change', () => {
            // 联动更新章节下拉框
            if (typeof populateCategoryDropdowns === 'function') {
                populateCategoryDropdowns();
            }
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 章节筛选
    const filterChapter = document.getElementById('filterChapter');
    if (filterChapter) {
        filterChapter.addEventListener('change', () => {
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 来源筛选
    const filterSource = document.getElementById('filterSource');
    if (filterSource) {
        filterSource.addEventListener('input', () => {
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 试卷分类 → 考试阶段联动
    const filterPaperCategory = document.getElementById('filterPaperCategory');
    const filterExamStage = document.getElementById('filterExamStage');
    /**
     * 「规范分式」：把编辑框里的正文级分式统一成 \dfrac，上标/分式内部降回 \frac。
     *
     * 后端只做纯文本变换、不碰数据库，所以这里坚持「先预览、后写回」：拿到结果先让老师
     * 看清要改几行，确认之后才替换 textarea 的内容，真正入库仍然是点「保存」那一次。
     * 替换走 insertText，保留浏览器撤销栈——误按一次按钮不该把半小时的手打公式丢掉。
     */
    window.applyFractionStyle = async function (fieldId) {
        const box = document.getElementById(fieldId);
        if (!box) return;
        const source = String(box.value || '');
        if (!source.trim()) {
            if (window.showToast) window.showToast('内容为空，无需规范分式', 'info');
            return;
        }
        if (box.dataset.fractionBusy === '1') return;
        box.dataset.fractionBusy = '1';
        try {
            const form = new FormData();
            form.append('text', source);
            const res = await fetch('/api/format/fractions', { method: 'POST', body: form });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            if (typeof data.text !== 'string') throw new Error('规范分式响应缺少 text');
            if (!data.changed || data.text === source) {
                if (window.showToast) window.showToast('分式排版已是规范形态，无需修改', 'info');
                return;
            }
            const before = source.split('\n');
            const after = data.text.split('\n');
            let touched = 0;
            for (let i = 0; i < Math.max(before.length, after.length); i += 1) {
                if (before[i] !== after[i]) touched += 1;
            }
            if (!window.confirm('规范分式：将改写 ' + touched + ' 行公式的分式排版（\dfrac / \frac 归一）。'
                + '\n\n确定应用到编辑框吗？应用后仍需点「保存」才会写入题库。')) return;
            const caretStart = box.selectionStart;
            const caretEnd = box.selectionEnd;
            box.focus();
            box.setSelectionRange(0, source.length);
            let undoable = false;
            try {
                undoable = document.execCommand('insertText', false, data.text);
            } catch (e) {
                undoable = false;
            }
            if (!undoable) box.value = data.text;
            const limit = data.text.length;
            box.setSelectionRange(Math.min(caretStart, limit), Math.min(caretEnd, limit));
            // 走 input 事件，实时预览与草稿缓存才会跟着刷新
            box.dispatchEvent(new Event('input', { bubbles: true }));
            if (window.showToast) window.showToast('已规范分式排版（尚未保存，可 Ctrl+Z 撤销）', 'success');
        } catch (e) {
            console.error('Normalize fraction style failed:', e);
            if (window.showToast) window.showToast('规范分式失败：' + (e.message || '请稍后重试'), 'error');
        } finally {
            box.dataset.fractionBusy = '';
        }
    };

    function syncFilterExamStageAvailability() {
        if (!filterPaperCategory || !filterExamStage) return;
        const enabled = filterPaperCategory.value === 'xiaokao';
        filterExamStage.disabled = !enabled;
        if (!enabled) filterExamStage.value = '';
    }
    if (filterPaperCategory) {
        filterPaperCategory.addEventListener('change', () => {
            syncFilterExamStageAvailability();
            currentBankPage = 1;
            loadQuestions();
        });
    }
    if (filterExamStage) {
        filterExamStage.addEventListener('change', () => {
            currentBankPage = 1;
            loadQuestions();
        });
    }
    syncFilterExamStageAvailability();

    // 排序
    const filterSort = document.getElementById('filterSort');
    if (filterSort) {
        filterSort.addEventListener('change', () => {
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 未转 TikZ 筛选开关
    const filterTikzPending = document.getElementById('filterTikzPending');
    if (filterTikzPending) {
        filterTikzPending.addEventListener('click', () => {
            const active = filterTikzPending.dataset.active !== 'true';
            filterTikzPending.dataset.active = String(active);
            if (active) {
                filterTikzPending.classList.add('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                filterTikzPending.classList.remove('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
            } else {
                filterTikzPending.classList.remove('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                filterTikzPending.classList.add('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
            }
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 质量：显示烂题 开关（默认关闭 = 排除烂题）
    const filterExcludeBad = document.getElementById('filterExcludeBad');
    if (filterExcludeBad) {
        filterExcludeBad.addEventListener('click', () => {
            const active = filterExcludeBad.dataset.active !== 'true';
            filterExcludeBad.dataset.active = String(active);
            if (active) {
                filterExcludeBad.classList.add('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                filterExcludeBad.classList.remove('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
            } else {
                filterExcludeBad.classList.remove('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                filterExcludeBad.classList.add('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
            }
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 按卷分组 开关（切换试卷聚合视图）
    const filterGroupMode = document.getElementById('filterGroupMode');
    if (filterGroupMode) {
        filterGroupMode.addEventListener('click', () => {
            const active = filterGroupMode.dataset.active !== 'true';
            filterGroupMode.dataset.active = String(active);
            if (active) {
                filterGroupMode.classList.add('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                filterGroupMode.classList.remove('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
            } else {
                filterGroupMode.classList.remove('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
                filterGroupMode.classList.add('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
            }
            currentBankPage = 1;
            loadQuestions();
        });
    }

    // 列表 / 网格 视图切换（持久化到 localStorage）
    const filterViewMode = document.getElementById('filterViewMode');
    const questionsListEl = document.getElementById('questionsList');
    function applyBankViewMode(mode) {
        const btn = document.getElementById('filterViewMode');
        const list = document.getElementById('questionsList');
        if (!btn || !list) return;
        const isGrid = mode === 'grid';
        list.classList.toggle('bank-grid', isGrid);
        try { localStorage.setItem('bankViewMode', mode); } catch (e) {}
        const icon = btn.querySelector('i');
        const label = document.getElementById('filterViewModeText');
        if (isGrid) {
            if (icon) icon.className = 'fa-solid fa-grip mr-1';
            if (label) label.textContent = '网格';
            btn.classList.add('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
            btn.classList.remove('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
        } else {
            if (icon) icon.className = 'fa-solid fa-list mr-1';
            if (label) label.textContent = '列表';
            btn.classList.remove('bg-brand-50', 'border-brand-500', 'text-brand-700', 'dark:bg-slate-700');
            btn.classList.add('bg-white', 'dark:bg-slate-800', 'border-slate-300', 'text-slate-700', 'dark:text-slate-200');
        }
    }
    if (filterViewMode && questionsListEl) {
        let savedMode = 'list';
        try { savedMode = localStorage.getItem('bankViewMode') || 'list'; } catch (e) {}
        applyBankViewMode(savedMode);
        filterViewMode.addEventListener('click', () => {
            const next = questionsListEl.classList.contains('bank-grid') ? 'list' : 'grid';
            applyBankViewMode(next);
        });
    }
});


// ============ 一题多来源：录入与编辑 (v21) ============
window.EditorSources = window.EditorSources || [];

// 与后端 database._GRADE_LABELS / format_source_text 保持一致的标准名称汇总。
var EDITOR_GRADE_LABELS = { g1: '高一', g2: '高二', g3: '高三' };

var EDITOR_PROVINCE_STEMS = [
    ['黑龙江', '黑龙江省'], ['内蒙古', '内蒙古自治区'], ['广西', '广西壮族自治区'],
    ['西藏', '西藏自治区'], ['宁夏', '宁夏回族自治区'], ['新疆', '新疆维吾尔自治区'],
    ['北京', '北京市'], ['天津', '天津市'], ['上海', '上海市'], ['重庆', '重庆市'],
    ['河北', '河北省'], ['山西', '山西省'], ['辽宁', '辽宁省'], ['吉林', '吉林省'],
    ['江苏', '江苏省'], ['浙江', '浙江省'], ['安徽', '安徽省'], ['福建', '福建省'],
    ['江西', '江西省'], ['山东', '山东省'], ['河南', '河南省'], ['湖北', '湖北省'],
    ['湖南', '湖南省'], ['广东', '广东省'], ['海南', '海南省'], ['四川', '四川省'],
    ['贵州', '贵州省'], ['云南', '云南省'], ['陕西', '陕西省'], ['甘肃', '甘肃省'],
    ['青海', '青海省'], ['台湾', '台湾省'], ['香港', '香港特别行政区'], ['澳门', '澳门特别行政区']
];

var EDITOR_EXAM_TYPE_TOKENS = [
    '高考', '中考', '学业水平', '学考', '会考',
    '零模', '一模', '二模', '三模', '四模', '五模',
    '期初', '开学考', '摸底', '月考', '期中', '期末',
    '联考', '统考', '统测', '质检', '模拟', '竞赛'
];

function formatEditorSourceText(src) {
    if (!src) return '';
    const year = String(src.year || '').trim();
    const province = String(src.province || '').trim();
    const city = String(src.city || '').trim();
    const region = String(src.region || '').trim();
    const grade = EDITOR_GRADE_LABELS[String(src.grade || '').trim()] || '';
    const semester = String(src.semester || '').trim();
    const examType = String(src.exam_type || '').trim();
    return [
        year ? year + '年' : '',
        province,
        city,
        region,
        grade,
        semester,
        examType
    ].filter(Boolean).join('');
}

function editorSourceRank(examType) {
    const t = String(examType || '');
    if (t.indexOf('高考') >= 0) return 0;
    if (t.indexOf('模') >= 0) return 1;
    if (t.indexOf('联考') >= 0) return 2;
    return 3;
}

function sortEditorSources(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.slice().sort(function (a, b) {
        const ra = editorSourceRank(a.exam_type);
        const rb = editorSourceRank(b.exam_type);
        if (ra !== rb) return ra - rb;
        const ya = parseInt(String(a.year || '0'), 10) || 0;
        const yb = parseInt(String(b.year || '0'), 10) || 0;
        return yb - ya;
    });
}

function editorMainSource(arr) {
    if (!Array.isArray(arr) || !arr.length) return '';
    return formatEditorSourceText(sortEditorSources(arr)[0]);
}

function renderSourceChips() {
    syncEditorSourceFields();
    renderEditorSourceChips();
}

// 单题来源 = 整卷式五字段（年份/省份/城市/学校/考试类型），无弹窗。
// window.EditorSources 仍是唯一数据源：加载回填 / 保存提交均不变。
function syncEditorSourceFields() {
    const arr = Array.isArray(window.EditorSources) ? window.EditorSources : [];
    const hidden = document.getElementById('editSource');
    if (hidden) hidden.value = editorMainSource(arr);
    let main = null;
    if (arr.length) main = sortEditorSources(arr)[0];
    const yEl = document.getElementById('editSrcYear');
    const pEl = document.getElementById('editSrcProvince');
    const cEl = document.getElementById('editSrcCity');
    const rEl = document.getElementById('editSrcRegion');
    const tEl = document.getElementById('editSrcExamType');
    const sEl = document.getElementById('editSrcSemester');
    const ptEl = document.getElementById('editSrcPaperTitle');
    if (yEl) setSelectWithFallback(yEl, main ? main.year : '');
    if (rEl) setSelectWithFallback(rEl, main ? main.region : '');
    if (tEl) setSelectWithFallback(tEl, main ? main.exam_type : '');
    if (sEl) setSelectWithFallback(sEl, main ? main.semester : '');
    if (ptEl) ptEl.value = main ? String(main.paper_title || '') : '';
    if (pEl || cEl) {
        // 必须先重建级联选项、再回写值。旧实现先赋值后重建，而省份/城市的 option
        // 列表在重建前是空的（城市还要按省份过滤），主来源的 city 根本赋不上；
        // 下一次字段变更 applyEditorSourceFields 读回空值就把 city 覆写掉
        // —— 实测打开挂过「哈尔滨市」的来源再保存，城市就静默丢了。
        if (pEl && (window._regionsTree || []).length && pEl.options.length <= 1) {
            fillEditorSrcProvince();      // 内部会连着重建城市与学校
        }
        if (pEl) setSelectWithFallback(pEl, main ? main.province : '');
        fillEditorSrcCity();
        if (cEl) setSelectWithFallback(cEl, main ? main.city : '');
        fillEditorSrcSchoolSelect();
        if (main) setSelectWithFallback(rEl, main.region);
    }
    // 折叠起来的省市/学期里有值时必须自动展开，否则用户以为数据丢了
    if (main && (main.province || main.city || main.semester)) toggleEditorSourceMore(true);
    refreshEditorSourceMoreButton();
}

function toggleEditorSourceMore(force) {
    const wrap = document.getElementById('editorSrcMoreFields');
    if (!wrap) return;
    wrap.hidden = typeof force === 'boolean' ? !force : !wrap.hidden;
    refreshEditorSourceMoreButton();
}

// 收起状态下里面若已有值，按钮上点一颗标，免得看起来像「填了又消失」
function refreshEditorSourceMoreButton() {
    const btn = document.getElementById('editorSrcMoreBtn');
    const wrap = document.getElementById('editorSrcMoreFields');
    if (!btn || !wrap) return;
    const filled = ['editSrcProvince', 'editSrcCity', 'editSrcSemester'].some(function (id) {
        const el = document.getElementById(id);
        return el && String(el.value || '').trim();
    });
    btn.textContent = (wrap.hidden ? '＋ 省市·学期' : '－ 省市·学期') + (wrap.hidden && filled ? ' ·' : '');
    btn.setAttribute('aria-expanded', wrap.hidden ? 'false' : 'true');
}

// 选项表里查不到的存量值必须就地补一个选项：学校不在词库（导入题、查重并入的
// 来源、跨省市改名）、年份超出下拉范围、自定义考试类型都是这种。直接赋值的话
// el.value 会变回空，而下一次字段变更就把这个键覆写成空 —— 静默丢数据。
function setSelectWithFallback(sel, value) {
    if (!sel) return;
    const want = String(value || '').trim();
    sel.value = want;
    if (!want || sel.value === want) return;
    const option = document.createElement('option');
    option.value = want;
    option.textContent = want;
    let anchor = null;
    for (let i = 0; i < sel.options.length; i += 1) {
        if (sel.options[i].value === '__custom__') { anchor = sel.options[i]; break; }
    }
    sel.insertBefore(option, anchor);
    sel.value = want;
}

function applyEditorSourceFields() {
    const yEl = document.getElementById('editSrcYear');
    const pEl = document.getElementById('editSrcProvince');
    const cEl = document.getElementById('editSrcCity');
    const rEl = document.getElementById('editSrcRegion');
    const tEl = document.getElementById('editSrcExamType');
    const sEl = document.getElementById('editSrcSemester');
    const ptEl = document.getElementById('editSrcPaperTitle');
    const gEl = document.getElementById('editApplicableGrade');
    const year = yEl ? String(yEl.value || '').trim() : '';
    const province = pEl ? String(pEl.value || '').trim() : '';
    const city = cEl ? String(cEl.value || '').trim() : '';
    const region = rEl ? String(rEl.value || '').trim() : '';
    const exam = tEl ? String(tEl.value || '').trim() : '';
    const semester = sEl ? String(sEl.value || '').trim() : '';
    const paperTitle = ptEl ? String(ptEl.value || '').trim() : '';
    const grade = gEl ? String(gEl.value || '').trim() : '';
    let arr = Array.isArray(window.EditorSources) ? window.EditorSources.slice() : [];
    if (year || province || city || region || exam || semester || paperTitle) {
        const newSrc = { year: year, province: province, city: city, region: region, grade: grade, semester: semester, exam_type: exam, paper_title: paperTitle };
        if (arr.length) {
            const mainIdx = arr.indexOf(sortEditorSources(arr)[0]);
            if (mainIdx >= 0) arr[mainIdx] = newSrc;
            else arr.push(newSrc);
        } else {
            arr.push(newSrc);
        }
    } else {
        arr = [];
    }
    window.EditorSources = arr;
    const hidden = document.getElementById('editSource');
    if (hidden) hidden.value = editorMainSource(arr);
    if (typeof renderEditorPaperMeta === 'function') renderEditorPaperMeta();
    renderEditorSourceChips();
}

// ============ 已存来源列表（内联字段的投影，不是第二份数据）============
// 内联七字段只能改写「主来源」，第 2..N 条此前在界面上完全看不见、也删不掉
// （多来源来自查重「并入已有题」与旧弹窗）。这里按后端同一排序口径把它们列出来，
// 每条给一个删除入口。数据源仍旧只有 window.EditorSources 一份。
function editorSourceChipsHost() {
    const existing = document.getElementById('editorSourceChips');
    if (existing) return existing;
    const yearEl = document.getElementById('editSrcYear');
    if (!yearEl) return null;
    const host = document.createElement('div');
    host.id = 'editorSourceChips';
    host.className = 'editor-source-chips';
    host.setAttribute('role', 'list');
    host.setAttribute('aria-label', '这道题已挂的来源');
    // 录入区把来源放在 .editor-meta-col 里（字段行在上、chips 在下）；
    // 找不到就退回「跟在来源那一行后面」，拆卷卡片等其它宿主仍可用。
    const col = yearEl.closest('.editor-meta-col');
    if (col) {
        col.appendChild(host);
        return host;
    }
    const row = yearEl.closest('div');
    if (!row || !row.parentNode) return null;
    row.parentNode.insertBefore(host, row.nextSibling);
    return host;
}

function editorSourceChipText(src) {
    return formatEditorSourceText(src) || String((src && src.paper_title) || '').trim() || '（未填写来源）';
}

function renderEditorSourceChips() {
    const host = editorSourceChipsHost();
    if (!host) return;
    const sorted = sortEditorSources(Array.isArray(window.EditorSources) ? window.EditorSources : []);
    const signature = sorted.map(editorSourceChipText).join('\u0001');
    // 内联字段每敲一个字都会回来一次：内容没变就别重建 DOM，否则会抢走焦点
    if (host.getAttribute('data-chips') === signature) return;
    host.setAttribute('data-chips', signature);
    host.textContent = '';
    host.hidden = !sorted.length;
    sorted.forEach(function (src, idx) {
        host.appendChild(buildEditorSourceChip(src, idx));
    });
}

function buildEditorSourceChip(src, idx) {
    const text = editorSourceChipText(src);
    const chip = document.createElement('span');
    chip.className = 'editor-source-chip' + (idx === 0 ? ' is-main' : '');
    chip.setAttribute('role', 'listitem');

    const label = document.createElement('span');
    label.className = 'editor-source-chip-text';
    // 来源文本是用户可控内容：一律走 textContent，不用字符串拼 HTML
    label.textContent = idx === 0 ? ('主来源 · ' + text) : text;
    chip.appendChild(label);

    if (src && src.paper_title && formatEditorSourceText(src) !== String(src.paper_title).trim()) {
        chip.title = '整卷出处：' + src.paper_title;
    }

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'editor-source-chip-del';
    del.textContent = '×';
    del.title = '删除这条来源';
    del.setAttribute('aria-label', '删除来源：' + text);
    del.addEventListener('click', function () {
        removeEditorSourceAt(idx, text);
    });
    chip.appendChild(del);
    return chip;
}

function removeEditorSourceAt(index, shownText) {
    const arr = Array.isArray(window.EditorSources) ? window.EditorSources.slice() : [];
    const target = sortEditorSources(arr)[index];
    if (!target) return;
    const pos = arr.indexOf(target);
    if (pos < 0) return;
    if (!window.confirm('删除来源「' + (shownText || '') + '」？保存后这道题就不再挂这条出处。')) return;
    arr.splice(pos, 1);
    window.EditorSources = arr;
    // 主来源换人时，内联字段与隐藏域必须跟着换成新的第一条
    syncEditorSourceFields();
    if (typeof renderEditorPaperMeta === 'function') renderEditorPaperMeta();
    renderEditorSourceChips();
}
window.applyEditorSourceFields = applyEditorSourceFields;

// 单题省份/城市/学校联动（复用整卷学校词库与地区树数据）
function loadEditorSourceGeo() {
    const needSchools = !window._schoolsDict || !window._schoolsDict.length;
    const needRegions = !window._regionsTree || !window._regionsTree.length;
    if (needSchools) {
        fetch('/api/school-suggestions?limit=500', { headers: { 'X-Local-Token': window.LOCAL_TOKEN || '' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.suggestions) window._schoolsDict = d.suggestions;
                fillEditorSrcProvince();
            })
            .catch(function () { /* 离线静默 */ });
    }
    if (needRegions) {
        fetch('/api/school-regions', { headers: { 'X-Local-Token': window.LOCAL_TOKEN || '' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.regions) window._regionsTree = d.regions;
                fillEditorSrcProvince();
            })
            .catch(function () { /* 离线静默 */ });
    }
    if (!needSchools && !needRegions) fillEditorSrcProvince();
    fillEditorSrcYearSelect();
    fillEditorSrcExamTypeSelect();
    // 兜底：异步加载完成 / 竞态后自愈省份下拉
    setTimeout(function () {
        const sel = document.getElementById('editSrcProvince');
        if (sel && (window._regionsTree || []).length && sel.options.length <= 1) {
            fillEditorSrcProvince();
        }
    }, 600);
}

function fillEditorSrcYearSelect() {
    const sel = document.getElementById('editSrcYear');
    if (!sel || sel.dataset.filled) return;
    sel.innerHTML = '<option value="">年份</option>';
    const now = new Date().getFullYear();
    for (let y = now; y >= now - 15; y--) {
        const o = document.createElement('option');
        o.value = String(y);
        o.textContent = String(y) + '年';
        sel.appendChild(o);
    }
    sel.dataset.filled = '1';
}

function fillEditorSrcExamTypeSelect() {
    const sel = document.getElementById('editSrcExamType');
    if (!sel || sel.dataset.filled) return;
    sel.innerHTML = '<option value="">考试类型</option>';
    ['高考', '一模', '二模', '三模', '联考', '期末', '期中', '月考', '周测', '强基', '竞赛', '自主招生'].forEach(function (v) {
        const o = document.createElement('option');
        o.value = v;
        o.textContent = v;
        sel.appendChild(o);
    });
    const custom = document.createElement('option');
    custom.value = '__custom__';
    custom.textContent = '＋ 自定义…';
    sel.appendChild(custom);
    sel.dataset.filled = '1';
}

function handleCustomSelect(sid, promptText) {
    const el = document.getElementById(sid);
    if (!el || el.value !== '__custom__') return;
    const val = window.prompt(promptText, '');
    const cleaned = val ? String(val).trim() : '';
    if (cleaned) {
        let exists = false;
        for (let i = 0; i < el.options.length; i++) {
            if (el.options[i].value === cleaned) { exists = true; break; }
        }
        if (!exists) {
            const o = document.createElement('option');
            o.value = cleaned;
            o.textContent = cleaned;
            el.appendChild(o);
        }
        el.value = cleaned;
    } else {
        el.value = '';
    }
    applyEditorSourceFields();
}

function fillEditorSrcProvince() {
    const sel = document.getElementById('editSrcProvince');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">省份</option>';
    (window._regionsTree || []).forEach(function (r) {
        const opt = document.createElement('option');
        opt.value = r.province;
        opt.textContent = r.province;
        sel.appendChild(opt);
    });
    if (cur) sel.value = cur;
    fillEditorSrcCity();
}

function fillEditorSrcCity() {
    const provSel = document.getElementById('editSrcProvince');
    const citySel = document.getElementById('editSrcCity');
    if (!provSel || !citySel) return;
    const prov = provSel.value;
    const cur = citySel.value;
    citySel.innerHTML = '<option value="">城市</option>';
    const node = (window._regionsTree || []).find(function (r) { return r.province === prov; });
    if (node) {
        node.cities.forEach(function (c) {
            const o = document.createElement('option');
            o.value = c;
            o.textContent = c;
            citySel.appendChild(o);
        });
    }
    const still = cur && prov && Array.from(citySel.options).some(function (o) { return o.value === cur; });
    citySel.value = still ? cur : '';
    fillEditorSrcSchoolSelect();
}

function fillEditorSrcSchoolSelect() {
    const sel = document.getElementById('editSrcRegion');
    if (!sel) return;
    const prov = (document.getElementById('editSrcProvince') || {}).value || '';
    const city = (document.getElementById('editSrcCity') || {}).value || '';
    const cur = sel.value;
    let items = (window._schoolsDict || []).slice();
    if (prov) items = items.filter(function (s) { return (s.province || '') === prov; });
    if (city) items = items.filter(function (s) { return (s.city || '') === city; });
    sel.innerHTML = '<option value="">学校</option>';
    items.forEach(function (s) {
        const o = document.createElement('option');
        o.value = s.name;
        o.textContent = s.name;
        sel.appendChild(o);
    });
    const custom = document.createElement('option');
    custom.value = '__custom__';
    custom.textContent = '＋ 自定义…';
    sel.appendChild(custom);
    if (cur && Array.from(sel.options).some(function (o) { return o.value === cur; })) {
        sel.value = cur;
    }
}

function matchEditorSrcSchoolToGeo() {
    const regionEl = document.getElementById('editSrcRegion');
    if (!regionEl) return;
    const val = regionEl.value.trim();
    const provSel = document.getElementById('editSrcProvince');
    const citySel = document.getElementById('editSrcCity');
    if (!val) {
        if (provSel) provSel.value = '';
        if (citySel) citySel.value = '';
        fillEditorSrcCity();
        return;
    }
    const provFilter = provSel ? provSel.value : '';
    let cands = (window._schoolsDict || []).filter(function (s) { return s.name === val; });
    if (provFilter) cands = cands.filter(function (s) { return (s.province || '') === provFilter; });
    if (cands.length === 1) {
        if (provSel) provSel.value = cands[0].province || '';
        fillEditorSrcCity();
        if (citySel) citySel.value = cands[0].city || '';
    } else if (cands.length > 1) {
        if (provSel && !provSel.value) {
            provSel.value = cands[0].province || '';
            fillEditorSrcCity();
        }
    }
    applyEditorSourceFields();
}
