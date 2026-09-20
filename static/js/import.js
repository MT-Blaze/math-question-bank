        // ocr.js is loaded immediately before this module. Keep its public badge
        // renderers, but normalize every image path at the cascade boundary so
        // stored answer Markdown and API payloads cannot break out through a
        // filename/title interpolation in the legacy badge markup.
        (() => {
            const renderContentBadges = window.renderIllustrationBadges;
            if (typeof renderContentBadges === 'function') {
                window.renderIllustrationBadges = function() {
                    uploadedImages = Array.isArray(uploadedImages)
                        ? Array.from(new Set(uploadedImages.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)))
                        : [];
                    return renderContentBadges();
                };
            }

            const renderAnswerBadges = window.renderAnswerImageBadges;
            if (typeof renderAnswerBadges === 'function') {
                window.renderAnswerImageBadges = function() {
                    uploadedAnswerImages = Array.isArray(uploadedAnswerImages)
                        ? Array.from(new Set(uploadedAnswerImages.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)))
                        : [];
                    return renderAnswerBadges();
                };
            }

            window.collectAnswerImagePaths = function(markdown) {
                markdown = String(markdown || '');
                const foundImages = [];
                const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
                let match;
                while ((match = imagePattern.exec(markdown)) !== null) {
                    const safePath = window.MathBankSafe.safeImageUrl(match[1]);
                    if (safePath && !foundImages.includes(safePath)) {
                        foundImages.push(safePath);
                    }
                }
                return foundImages;
            };

            window.syncAnswerImagesFromMarkdown = function() {
                const textarea = document.getElementById('editAnswerMarkdown');
                const markdown = textarea ? textarea.value : '';
                uploadedAnswerImages = window.collectAnswerImagePaths(markdown);
                if (typeof window.renderAnswerImageBadges === 'function') {
                    window.renderAnswerImageBadges();
                }
            };
        })();

        function findContentTikzImagePath(markdown) {
            return window.collectAnswerImagePaths(markdown).find(path => {
                const filename = String(path).split('/').pop() || '';
                return filename.startsWith('tikz_');
            }) || '';
        }

        window.hydrateTikzState = function(record) {
            const tikzCode = String(record && record.tikz_code || '').trim();
            const storedContentAssets = Array.isArray(record && record.content_tikz_assets)
                ? record.content_tikz_assets
                : [];
            TikzState.contentAssets = storedContentAssets.length > 0
                ? storedContentAssets
                : (tikzCode ? [{
                    id: 'content_tikz_legacy',
                    image_path: findContentTikzImagePath(record.content || ''),
                    tikz_code: tikzCode,
                    instruction: '',
                    reference_image_path: window.MathBankSafe.safeImageUrl(
                        record.tikz_reference_image_path || ''
                    )
                }] : []);
            TikzState.answerAssets = Array.isArray(record && record.answer_tikz_assets)
                ? record.answer_tikz_assets
                : [];
        };

        function resetTikzEditorState() {
            TikzState.reset();
            if (typeof window.renderContentTikzAssets === 'function') window.renderContentTikzAssets();
            if (typeof window.renderAnswerTikzAssets === 'function') window.renderAnswerTikzAssets();
        }

        function startNewQuestionWithoutPrompt() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            EditorState.reset();
            document.getElementById('editorTitle').textContent = '录入新数学题';
            
            document.getElementById('editContent').value = '';
            window.EditorSources = [];
            if (typeof renderSourceChips === 'function') renderSourceChips();
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
            _clearExamFields();

            document.getElementById('aiOutputBox').classList.add('hidden');
            document.getElementById('ocrOutputBox').classList.add('hidden');
            
            uploadedImages = [];
            uploadedAnswerImages = [];
            resetTikzEditorState();
            renderIllustrationBadges();
            
            const _qtR2 = document.getElementById('editQType');
            if (_qtR2) {
                _qtR2.value = 'single_choice';
                _qtR2.dispatchEvent(new Event('change'));
            }
            const _dfR2 = document.getElementById('editDifficulty');
            if (_dfR2) {
                _dfR2.value = 'L1';
                _dfR2.dispatchEvent(new Event('change'));
            }
            const _cpR2 = document.getElementById('editCompulsory');
            if (_cpR2) {
                _cpR2.value = '';
                if (typeof _cpR2.onchange === 'function') _cpR2.onchange();
            }
            clearContentOcrPreview();
            clearOcrPreview();
            
            document.getElementById('editContent').dispatchEvent(new Event('input'));
            document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            document.getElementById('editorSection').scrollTop = 0;
            
            backupEditorState(null, null);
        }


        // Toast Helper
        function switchWorkflowTab(tabId) {
            const tabs = ['ai', 'ocr', 'image'];
            tabs.forEach(t => {
                const btn = document.getElementById(`tabBtn-${t}`);
                const content = document.getElementById(`tabContent-${t}`);
                
                if (t === tabId) {
                    btn.className = "flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 transition-all text-brand-600 bg-white shadow-sm border border-slate-200";
                    content.classList.remove('hidden');
                } else {
                    btn.className = "flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 transition-all text-slate-600 hover:text-slate-800 hover:bg-white/50";
                    content.classList.add('hidden');
                }
            });
        }

        // Upload and drag and drop system for Illustration / OCR images
        function clearEditor() {
            // This function is called directly from index.html, so the guard
            // must live here rather than relying on the button or caller.
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认清空当前所有的编辑草稿吗？此操作无法撤销。')) {
                if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                    window.invalidatePendingQuestionDetailLoad();
                }
                cancelAllOcr(); // Cancel any active OCR requests!
                EditorState.reset();
                document.getElementById('editorTitle').textContent = '录入新数学题';
                
                document.getElementById('editContent').value = '';
                window.EditorSources = [];
            if (typeof renderSourceChips === 'function') renderSourceChips();
            document.getElementById('editSource').value = '';
                document.getElementById('editAnswerMarkdown').value = '';
                document.getElementById('aiCustomPrompt').value = '';
                document.getElementById('editReview').value = '';
                if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
                _clearExamFields();
                document.getElementById('editRelatedQuestion').value = '';
                document.getElementById('editRelatedQuestionNum').value = '';
                document.getElementById('editReview').dispatchEvent(new Event('input')); // Hide review preview
                loadAssociatedQuestionsInList(null); // Reset associated questions panel
                
                document.getElementById('aiOutputBox').classList.add('hidden');
                document.getElementById('ocrOutputBox').classList.add('hidden');
                
                uploadedImages = [];
                uploadedAnswerImages = [];
                resetTikzEditorState();
                renderIllustrationBadges();
                
                // Reset select lists
                const _qtR3 = document.getElementById('editQType');
                if (_qtR3) {
                    _qtR3.value = 'single_choice';
                    _qtR3.dispatchEvent(new Event('change'));
                }
                const _dfR3 = document.getElementById('editDifficulty');
                if (_dfR3) {
                    _dfR3.value = 'L1';
                    _dfR3.dispatchEvent(new Event('change'));
                }
                const _cpR3 = document.getElementById('editCompulsory');
                if (_cpR3) {
                    _cpR3.value = '';
                    if (typeof _cpR3.onchange === 'function') _cpR3.onchange();
                }
                
                // Clear OCR preview
                clearContentOcrPreview();
                clearOcrPreview();
                
                // Refresh previews
                document.getElementById('editContent').dispatchEvent(new Event('input'));
                document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
                
                showToast('草稿已重置');

                // Reset the original state directly from the DOM!
                backupEditorState(null, null);
            }
        }

        function startNewQuestion() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            EditorState.reset();
            document.getElementById('editorTitle').textContent = '录入新数学题';

            document.getElementById('editContent').value = '';
            window.EditorSources = [];
            if (typeof renderSourceChips === 'function') renderSourceChips();
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            document.getElementById('editReview').value = '';
            if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
            _clearExamFields();

            document.getElementById('editRelatedQuestion').value = '';
            document.getElementById('editRelatedQuestionNum').value = '';
            document.getElementById('editReview').dispatchEvent(new Event('input')); // Hide review preview
            loadAssociatedQuestionsInList(null); // Reset associated questions panel
            
            // Clear hidden caches to avoid invisible leftovers when switching tabs
            document.getElementById('contentOcrResultText').textContent = '';
            document.getElementById('ocrResultText').textContent = '';
            document.getElementById('aiResultText').textContent = '';
            
            document.getElementById('aiOutputBox').classList.add('hidden');
            document.getElementById('ocrOutputBox').classList.add('hidden');
            
            uploadedImages = [];
            uploadedAnswerImages = [];
            resetTikzEditorState();
            renderIllustrationBadges();
            
            // Reset selects
            const _qtReset = document.getElementById('editQType');
            if (_qtReset) {
                _qtReset.value = 'single_choice';
                _qtReset.dispatchEvent(new Event('change'));
            }
            const _dfReset = document.getElementById('editDifficulty');
            if (_dfReset) {
                _dfReset.value = 'L1';
                _dfReset.dispatchEvent(new Event('change'));
            }
            const _cpReset = document.getElementById('editCompulsory');
            if (_cpReset) {
                _cpReset.value = '';
                if (typeof _cpReset.onchange === 'function') _cpReset.onchange();
            }
            
            // Clear OCR preview
            clearContentOcrPreview();
            clearOcrPreview();
            
            // Sync-clear all previews to avoid 250ms debounce flash
            var _cp = document.getElementById('contentPreview');
            if (_cp) _cp.innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
            document.getElementById('paperContent').innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将展示实时试卷排版效果。</p>';
            var _ap = document.getElementById('answerPreview');
            if (_ap) _ap.innerHTML = '<p class="text-slate-400 italic">在左侧输入解析内容，此处将实时展示 LaTeX 渲染排版...</p>';
            document.getElementById('paperAnalysisContent').innerHTML = '<p class="text-slate-400 italic">暂无解析内容。</p>';
            
            // Refresh previews
            document.getElementById('editContent').dispatchEvent(new Event('input'));
            document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            
            // Scroll editor into view
            document.getElementById('editorSection').scrollTop = 0;
            
            // Reload list styling selection
            
            showToast('开始录入新数学题！');

            // Reset the original state directly from the DOM!
            backupEditorState(null, null);
        }

        // Load all questions to populate the related question dropdown list
        function refreshRelatedDropdown(selectedId = "") {
            fetch('/api/questions')
                .then(r => r.json())
                .then(questions => {
                    const dropdown = document.getElementById('editRelatedQuestion');
                    const numInput = document.getElementById('editRelatedQuestionNum');
                    if (!dropdown) return;
                    
                    dropdown.innerHTML = '<option value="">-- 选择要关联的题目 (可选) --</option>';
                    
                    let foundSelectedSeq = '';
                    
                    questions.forEach(q => {
                        // Exclude the current editing question
                        if (EditorState.questionId && q.id === EditorState.questionId) {
                            return;
                        }
                        
                        // Extract a snippet of the question stem
                        let textSnippet = q.content || '';
                        // Remove HTML/Markdown tags and LaTeX brackets to make it readable
                        textSnippet = textSnippet.replace(/[\$\#\*\_]/g, '').substring(0, 40);
                        if ((q.content || '').length > 40) textSnippet += '...';
                        
                        const optionText = `#${q.seq_num} [${getTypeText(q.question_type)}] - ${textSnippet}`;
                        const option = document.createElement('option');
                        option.value = q.id;
                        option.setAttribute('data-seq-num', q.seq_num);
                        option.textContent = optionText;
                        
                        if (String(q.id) === String(selectedId)) {
                            option.selected = true;
                            foundSelectedSeq = q.seq_num;
                        }
                        dropdown.appendChild(option);
                    });
                    
                    if (numInput) {
                        numInput.value = foundSelectedSeq;
                    }
                })
                .catch(err => {
                    console.error('Failed to load related questions list:', err);
                });
        }

        // Associate selected target question with current loaded question (bidirectional, backend + UI)
        async function associateRelatedQuestion() {
            const targetSelect = document.getElementById('editRelatedQuestion');
            const targetId = targetSelect ? targetSelect.value : '';
            if (!targetId) {
                showToast('请先在编号框或下拉框中选择要关联的目标题目', 'warning');
                return;
            }

            if (!EditorState.questionId) {
                showToast('当前正在录入新题目，请先保存本题后再建立实时关联。', 'info');
                return;
            }

            if (parseInt(targetId, 10) === parseInt(EditorState.questionId, 10)) {
                showToast('题目不能与自身建立关联', 'warning');
                return;
            }

            try {
                const formData = new FormData();
                formData.append('target_id', targetId);

                const res = await fetch(`/api/questions/${EditorState.questionId}/associate`, {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: formData
                });

                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    const selectedOpt = targetSelect.options[targetSelect.selectedIndex];
                    const seqNum = selectedOpt ? selectedOpt.getAttribute('data-seq-num') : '';
                    showToast(`成功与题目 #${seqNum || targetId} 建立关联绑定！`, 'success');

                    // 刷新右侧预览区的关联变式题目卡片及下拉框
                    if (typeof loadAssociatedQuestionsInList === 'function') {
                        loadAssociatedQuestionsInList(EditorState.questionId);
                    }
                } else {
                    showToast(data.detail || data.message || '关联建立失败', 'error');
                }
            } catch (e) {
                console.error(e);
                showToast('关联请求异常: ' + e.message, 'error');
            }
        }

        // Clear the related question association (bidirectional, backend + UI)
        function clearRelatedQuestion() {
            if (!EditorState.questionId) {
                // No question loaded, just clear the UI
                const dropdown = document.getElementById('editRelatedQuestion');
                const numInput = document.getElementById('editRelatedQuestionNum');
                if (dropdown) dropdown.value = '';
                if (numInput) numInput.value = '';
                showToast('已清除关联选择', 'success');
                return;
            }

            fetch(`/api/questions/${EditorState.questionId}/associated`, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        // Clear UI
                        const dropdown = document.getElementById('editRelatedQuestion');
                        const numInput = document.getElementById('editRelatedQuestionNum');
                        if (dropdown) dropdown.value = '';
                        if (numInput) numInput.value = '';

                        // Hide associated list in preview
                        const wrapper = document.getElementById('paperAssociatedWrapper');
                        const container = document.getElementById('paperAssociatedList');
                        if (wrapper) wrapper.classList.add('hidden');
                        if (container) container.innerHTML = '';

                        showToast('已解除所有关联（双向生效）', 'success');
                    } else {
                        showToast(data.detail || '解除关联失败', 'error');
                    }
                })
                .catch(err => {
                    console.error('Failed to remove association:', err);
                    showToast('解除关联失败: ' + err.message, 'error');
                });
        }

        // Fetch associated questions under transitive group and populate live preview
        function loadAssociatedQuestionsInList(questionId) {
            const wrapper = document.getElementById('paperAssociatedWrapper');
            const container = document.getElementById('paperAssociatedList');
            if (wrapper) wrapper.classList.add('hidden');
            if (container) container.innerHTML = '';
            
            if (!questionId) {
                refreshRelatedDropdown("");
                return;
            }
            
            fetch(`/api/questions/${questionId}/associated`)
                .then(r => r.json())
                .then(list => {
                    let associatedId = "";
                    if (list.length > 0) {
                        associatedId = list[0].id;
                        if (wrapper) wrapper.classList.remove('hidden');
                        
                        list.forEach(q => {
                            let cleanContent = (q.content || '').replace(/[\$\#\*\_]/g, '');
                            if (cleanContent.length > 50) cleanContent = cleanContent.substring(0, 50) + '...';

                            const item = document.createElement('div');
                            item.className = "glass-list-item p-2.5 rounded-xl text-xs text-slate-700 flex items-center justify-between";
                            item.onclick = () => selectQuestionById(q.id);
                            item.innerHTML = `
                                <div class="truncate pr-2">
                                    <span class="font-bold text-brand-600 bg-brand-50 px-1.5 py-0.5 rounded text-[10px] mr-1.5 shadow-sm">#${window.MathBankSafe.escapeText(q.seq_num)}</span>
                                    <span class="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-500 mr-1.5">${window.MathBankSafe.escapeText(getTypeText(q.question_type))}</span>
                                    <span>${window.MathBankSafe.escapeText(cleanContent)}</span>
                                </div>
                                <i class="fa-solid fa-chevron-right text-[9px] text-slate-400 shrink-0"></i>
                            `;
                            if (container) container.appendChild(item);
                        });
                    }
                    refreshRelatedDropdown(associatedId);
                })
                .catch(err => {
                    console.error('Failed to load associated questions list:', err);
                    refreshRelatedDropdown("");
                });
        }

        // Jump to select another question by ID
        function selectQuestionById(id) {
            fetch(`/api/questions/${id}`)
                .then(r => {
                    if (!r.ok) throw new Error('未找到对应的关联题目');
                    return r.json();
                })
                .then(q => {
                    checkAndSwitch(() => selectQuestion(q));
                })
                .catch(err => {
                    showToast('获取关联题目出错: ' + err.message, 'error');
                });
        }

        let questionDetailLoadSequence = 0;
        let questionDetailLoading = false;
        let saveQuestionInFlight = null;

        function blockEditorSessionChangeWhileSaving() {
            if (!saveQuestionInFlight) return false;
            showToast('题目正在保存，请等待完成后再重置或切换', 'info');
            return true;
        }
        window.blockEditorSessionChangeWhileSaving = blockEditorSessionChangeWhileSaving;

        window.isQuestionSaveInFlight = function() {
            return Boolean(saveQuestionInFlight);
        };

        function updateQuestionSaveButtonState() {
            const button = document.getElementById('saveQuestionBtn');
            if (!button) return;

            const isSaving = Boolean(saveQuestionInFlight);
            const isBusy = questionDetailLoading || isSaving;
            button.disabled = isBusy;
            button.setAttribute('aria-busy', isBusy ? 'true' : 'false');
            button.classList.toggle('opacity-60', isBusy);
            button.classList.toggle('pointer-events-none', isBusy);

            if (questionDetailLoading) {
                button.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>加载题目...</span>';
            } else if (isSaving) {
                button.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>保存中...</span>';
            } else {
                button.innerHTML = '<i class="fa-solid fa-floppy-disk"></i><span>保存</span>';
            }
        }

        function invalidatePendingQuestionDetailLoad() {
            questionDetailLoadSequence += 1;
            questionDetailLoading = false;
            updateQuestionSaveButtonState();
        }
        window.invalidatePendingQuestionDetailLoad = invalidatePendingQuestionDetailLoad;

        // Select a question to Edit & Preview. The editor identity is committed
        // only after the requested detail payload has arrived successfully.
        function selectQuestion(item) {
            if (blockEditorSessionChangeWhileSaving()) {
                return;
            }
            const requestedQuestionId = Number(item && item.id);
            if (!Number.isSafeInteger(requestedQuestionId) || requestedQuestionId <= 0) {
                showToast('无法加载题目：题目 ID 无效', 'error');
                return;
            }
            EditorState.beginTransition();
            const loadSequence = ++questionDetailLoadSequence;
            questionDetailLoading = true;
            updateQuestionSaveButtonState();

            // Lazy-load details asynchronously
            fetch(`/api/questions/${requestedQuestionId}`)
                .then(r => {
                    if (!r.ok) throw new Error('无法加载题目详情');
                    return r.json();
                })
                .then(fullItem => {
                    if (loadSequence !== questionDetailLoadSequence) {
                        return;
                    }
                    if (!fullItem || Number(fullItem.id) !== requestedQuestionId) {
                        throw new Error('题目详情与请求 ID 不匹配');
                    }
                    EditorState.useQuestion(fullItem);
                    document.getElementById('editorTitle').textContent = '编辑数学题';

                    // Clear previous OCR state only after the question switch commits.
                    clearContentOcrPreview();
                    clearOcrPreview();

                    // Sync the active card highlight after the matching details load.
                    const questionsList = document.getElementById('questionsList');
                    const listCards = questionsList ? questionsList.children : [];
                    for (let i = 0; i < listCards.length; i++) {
                        const card = listCards[i];
                        if (parseInt(card.dataset.id, 10) === requestedQuestionId) {
                            card.classList.add('active');
                        } else {
                            card.classList.remove('active');
                        }
                    }
                    // Load values to editor
                    document.getElementById('editContent').value = fullItem.content;
                    window.EditorSources = Array.isArray(fullItem.sources)
                        ? fullItem.sources.map(function (src) { return { year: src.year || '', province: src.province || '', city: src.city || '', region: src.region || '', exam_type: src.exam_type || '', paper_title: src.paper_title || '' }; })
                        : [];
                    if (typeof renderSourceChips === 'function') renderSourceChips();
                    document.getElementById('editSource').value = (Array.isArray(fullItem.sources) && fullItem.sources.length && fullItem.sources[0].text)
                        ? fullItem.sources[0].text
                        : (fullItem.source || '');
                    document.getElementById('editAnswerMarkdown').value = fullItem.answer_markdown || '';
                    document.getElementById('editReview').value = fullItem.review || '';

                    window.hydrateTikzState(fullItem);
                    const allStoredImages = Array.isArray(fullItem.image_paths)
                        ? fullItem.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                        : [];
                    uploadedAnswerImages = window.collectAnswerImagePaths(fullItem.answer_markdown || '');
                    const hiddenTikzReferencePaths = new Set(TikzState.referencePaths());
                    uploadedImages = allStoredImages.filter(path => {
                        return !uploadedAnswerImages.includes(path)
                            && !hiddenTikzReferencePaths.has(path);
                    });
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
                    
                    // Cascade bindings
                    document.getElementById('editQType').value = fullItem.question_type;
                    document.getElementById('editDifficulty').value = fullItem.difficulty;
                    if (document.getElementById('editTags')) {
                        document.getElementById('editTags').value = fullItem.tags || '';
                    }
                    applyExtendedMeta(fullItem);
                    
                    const compSelect = document.getElementById('editCompulsory');
                    const chapSelect = document.getElementById('editChapter');
                    const knowSelect = document.getElementById('editKnowledge');
                    
                    // In case the categories in item are not in tree yet, add them temporarily
                    // Repopulate with clean categoryTree
                    populateCategoryDropdowns();
                    
                    compSelect.value = fullItem.category_compulsory || '';
                    compSelect.onchange();
                    chapSelect.value = fullItem.category_chapter || '';
                    chapSelect.onchange();
                    knowSelect.value = fullItem.category_knowledge || '';
                    
                    // Dispatch input previews or update synchronously
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
                    
                    // Render editor metadata through the single shared preview path.
                    renderEditorPaperMeta();
                    
                    // Load associated questions list and handle group selection
                    loadAssociatedQuestionsInList(fullItem.id);
                    
                    // Scroll editor
                    document.getElementById('editorSection').scrollTop = 0;
                    
                    // Scroll to card active or highlight in current view
                    showToast(`题目 #${fullItem.seq_num} 载入成功`);
         
                    // Backup the original loaded question state directly from the DOM!
                    backupEditorState(fullItem.id, null);
                })
                .catch(err => {
                    if (loadSequence !== questionDetailLoadSequence) return;
                    console.error('Failed to load full question details:', err);
                    showToast('获取题目详情失败: ' + err.message, 'error');
                })
                .finally(() => {
                    if (loadSequence !== questionDetailLoadSequence) return;
                    questionDetailLoading = false;
                    updateQuestionSaveButtonState();
                });
        }

        window.reloadCurrentQuestionSilently = function() {
            if (!EditorState.questionId) return false;
            if (typeof window.isEditorModified === 'function' && window.isEditorModified()) {
                showToast('配置已更新；当前未保存的编辑内容已保留，保存或重新打开题目后即可同步', 'info');
                return false;
            }
            selectQuestion({
                id: EditorState.questionId,
                seq_num: EditorState.seqNum,
                created_at: EditorState.createdAt
            });
            return true;
        };

        // 扩展字段（知识点 / 考试元数据 / 教育测量维度）：单一来源，读写/清空共用，
        // 避免同一个字段在 save / load / 草稿 / 脏检查里各写一遍。
        const EXTENDED_META_FIELDS = Object.freeze({
            paper_question_no: 'editPaperQuestionNo',
            knowledge_points: 'editKnowledgePoints',
            exam_year: 'editExamYear',
            exam_region: 'editExamRegion',
            exam_stage: 'editExamStage',
            video_url: 'editVideoUrl',
            option_columns: 'editOptionColumns',
            analysis_methods: 'editAnalysisMethods',
            difficulty_index: 'editDifficultyIndex',
            cognitive_level: 'editCognitiveLevel',
            core_competency: 'editCoreCompetency',
            error_prone_points: 'editErrorPronePoints'
        });

        function readExtendedMeta() {
            const out = {};
            Object.keys(EXTENDED_META_FIELDS).forEach(function (key) {
                const el = document.getElementById(EXTENDED_META_FIELDS[key]);
                out[key] = el ? String(el.value).trim() : '';
            });
            return out;
        }

        function applyExtendedMeta(source) {
            Object.keys(EXTENDED_META_FIELDS).forEach(function (key) {
                const el = document.getElementById(EXTENDED_META_FIELDS[key]);
                if (!el) return;
                let value = source ? source[key] : '';
                if (Array.isArray(value)) value = value.join(',');
                el.value = (value === null || value === undefined) ? '' : value;
            });
        }

        window.MathBankExtendedMeta = {
            fields: EXTENDED_META_FIELDS,
            read: readExtendedMeta,
            apply: applyExtendedMeta,
            clear: function () { applyExtendedMeta(null); }
        };

        // Save/Update Question in SQLite (returns Promise)
        function _clearExamFields() {
            applyExtendedMeta(null);
        }
        window._clearExamFields = _clearExamFields;

        function saveQuestion(skipCheck = false) {
            if (questionDetailLoading) {
                showToast('题目详情仍在加载，请稍候再保存', 'info');
                return Promise.resolve(false);
            }
            if (saveQuestionInFlight) {
                return saveQuestionInFlight;
            }

            const saveOperation = (async () => {
                const editorSession = EditorState.snapshot();
                const content = document.getElementById('editContent').value;
                const qtype = document.getElementById('editQType').value;
                const compulsory = document.getElementById('editCompulsory').value;
                const chapter = document.getElementById('editChapter').value;
                const knowledge = document.getElementById('editKnowledge').value;
                const difficulty = document.getElementById('editDifficulty').value;
                const source = document.getElementById('editSource').value;
                const answerMarkdown = document.getElementById('editAnswerMarkdown').value;
                const review = document.getElementById('editReview').value;
                let relatedQuestionId = document.getElementById('editRelatedQuestion').value;
                const contentTikzAssets = Array.isArray(TikzState.contentAssets)
                    ? TikzState.contentAssets
                    : [];
                const firstContentTikzAsset = contentTikzAssets[0] || null;
                const tikzCode = firstContentTikzAsset ? firstContentTikzAsset.tikz_code : '';
                const tikzReferencePath = firstContentTikzAsset
                    ? (window.MathBankSafe.safeImageUrl(firstContentTikzAsset.reference_image_path) || '')
                    : '';
                let rawTags = document.getElementById('editTags') ? document.getElementById('editTags').value : '';
                let tags = rawTags.trim();
                const meta = readExtendedMeta();
                
                if (!content.trim()) {
                    showToast('保存失败：题干内容不能为空！', 'error');
                    return false;
                }
                
                // Check if Compulsory or Chapter classifications are missing
                if (!skipCheck && (!compulsory || !chapter)) {
                    const choice = await showMissingCompulsoryModal();
                    if (choice === 'manual') {
                        if (!compulsory) {
                            const compSelect = document.getElementById('editCompulsory');
                            if (compSelect) {
                                compSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                // Add premium temporary focus highlight (using brand color ring)
                                compSelect.classList.remove('border-slate-200');
                                compSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                                setTimeout(() => {
                                    compSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                    compSelect.classList.add('border-slate-200');
                                }, 2500);
                                compSelect.focus();
                            }
                        } else if (!chapter) {
                            const chapSelect = document.getElementById('editChapter');
                            if (chapSelect) {
                                chapSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                // Add premium temporary focus highlight (using brand color ring)
                                chapSelect.classList.remove('border-slate-200');
                                chapSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                                setTimeout(() => {
                                    chapSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                    chapSelect.classList.add('border-slate-200');
                                }, 2500);
                                chapSelect.focus();
                            }
                        }
                    } else if (choice === 'ai') {
                        // Automatically open AI classify modal and trigger AI analysis
                        openClassifyModal();
                        runAIClassify();
                    }
                    return false;
                }

                let duplicateDecision = {
                    allowed: true,
                    duplicateOverride: '',
                    duplicateSnapshotHash: ''
                };
                if (typeof checkEditorDuplicateBeforeSave === 'function') {
                    duplicateDecision = await checkEditorDuplicateBeforeSave(editorSession);
                    if (!duplicateDecision.allowed) return false;
                    if (!EditorState.isCurrent(editorSession)) {
                        showToast('题目编辑会话已变化，本次保存已取消。', 'info');
                        return false;
                    }
                    // Persist the marker into the visible tag field, then re-read it
                    // before the snapshot is taken so what is submitted matches the
                    // backup snapshot exactly.
                    if (duplicateDecision.autoTag) {
                        const tagInput = document.getElementById('editTags');
                        if (tagInput) {
                            tagInput.value = appendDuplicateTagToTags(tagInput.value, duplicateDecision.autoTag);
                        }
                        rawTags = tagInput ? tagInput.value : rawTags;
                        tags = rawTags.trim();
                    }
                }
                relatedQuestionId = document.getElementById('editRelatedQuestion').value;

                const requestBackupSnapshot = Object.freeze({
                    content: content,
                    answer_markdown: answerMarkdown,
                    review: review,
                    question_type: qtype,
                    difficulty: difficulty,
                    source: source,
                    category_compulsory: compulsory,
                    category_chapter: chapter,
                    category_knowledge: knowledge,
                    applicable_grade: document.getElementById('editApplicableGrade') ? document.getElementById('editApplicableGrade').value : '',
                    topic: document.getElementById('editTopic') ? document.getElementById('editTopic').value : '',
                    extra_topics: JSON.stringify(typeof window.collectExtraTopics === 'function' ? window.collectExtraTopics() : []),
                    related_question_id: relatedQuestionId,
                    image_paths: JSON.stringify(Array.from(uploadedImages)),
                    tikz_code: tikzCode,
                    tikz_reference_image_path: tikzReferencePath,
                    content_tikz_assets: JSON.stringify(contentTikzAssets),
                    answer_tikz_assets: JSON.stringify(TikzState.answerAssets),
                    tags: rawTags,
                    ...meta
                });
                if (typeof window.editorMatchesBackupSnapshot === 'function' &&
                    !window.editorMatchesBackupSnapshot(requestBackupSnapshot)) {
                    showToast('题目在查重期间已发生变化，请重新保存。', 'info');
                    return false;
                }
                
                const formData = new FormData();
                formData.append('content', content);
                formData.append('question_type', qtype);
                formData.append('category_compulsory', compulsory);
                formData.append('category_chapter', chapter);
                formData.append('category_knowledge', knowledge);
                formData.append('applicable_grade', document.getElementById('editApplicableGrade') ? document.getElementById('editApplicableGrade').value : '');
                formData.append('topic', document.getElementById('editTopic') ? document.getElementById('editTopic').value : '');
                formData.append('extra_topics', JSON.stringify(typeof window.collectExtraTopics === 'function' ? window.collectExtraTopics() : []));
                formData.append('difficulty', difficulty);
                formData.append('source', source);
                formData.append('sources', JSON.stringify(Array.isArray(window.EditorSources) ? window.EditorSources : []));
                formData.append('answer_markdown', answerMarkdown);
                formData.append('review', review);
                formData.append('related_question_id', relatedQuestionId);
                formData.append('tikz_code', tikzCode);
                formData.append('tikz_reference_image_path', tikzReferencePath);
                formData.append('content_tikz_assets', JSON.stringify(contentTikzAssets));
                formData.append('answer_tikz_assets', JSON.stringify(TikzState.answerAssets));
                formData.append('tags', tags);
                Object.keys(meta).forEach(function (key) {
                    formData.append(key, meta[key]);
                });
                const combinedImages = Array.from(new Set([
                    ...uploadedImages,
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : []),
                    ...TikzState.referencePaths()
                ].map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)));
                formData.append('image_paths', JSON.stringify(combinedImages));
                if (duplicateDecision.duplicateSnapshotHash) {
                    formData.append('duplicate_snapshot_hash', duplicateDecision.duplicateSnapshotHash);
                }
                if (duplicateDecision.duplicateOverride === 'independent') {
                    formData.append('duplicate_override', 'independent');
                }
                
                let url = '/api/questions';
                let method = 'POST';
                
                if (editorSession.questionId) {
                    url = `/api/questions/${editorSession.questionId}`;
                    method = 'PUT';
                }

                try {
                    let response = await fetch(url, {
                        method: method,
                        body: formData
                    });
                    let responseData = null;
                    try {
                        responseData = await response.json();
                    } catch (parseError) {
                        responseData = null;
                    }
                    if (response.status === 409 && responseData &&
                        responseData.code === 'duplicate_review_required' &&
                        Array.isArray(responseData.candidates) && responseData.candidates.length > 0) {
                        const conflictResult = {
                            level: 'exact',
                            snapshot_hash: String(responseData.snapshot_hash || ''),
                            candidates: Array.isArray(responseData.candidates)
                                ? responseData.candidates.map(candidate => ({
                                    ...candidate,
                                    level: 'exact',
                                    score: 1,
                                    reasons: ['EXACT_TEXT'],
                                    needs_visual_review: true
                                }))
                                : [],
                            batch_matches: [],
                            needs_visual_review: true
                        };
                        const action = await openParsedDuplicateReviewModal([{
                            label: editorSession.questionId ? '当前编辑题目' : '当前待录入题目',
                            result: conflictResult
                        }], 'single');
                        if (action !== 'independent') return false;
                        if (!window.editorMatchesBackupSnapshot(requestBackupSnapshot)) {
                            showToast('题目在复核期间已发生变化，请重新保存。', 'info');
                            return false;
                        }
                        formData.set('duplicate_snapshot_hash', String(responseData.snapshot_hash || ''));
                        formData.set('duplicate_override', 'independent');
                        response = await fetch(url, { method: method, body: formData });
                        try {
                            responseData = await response.json();
                        } catch (parseError) {
                            responseData = null;
                        }
                    }
                    if (!response.ok) {
                        let message = `服务器返回错误 HTTP ${response.status}`;
                        message = responseData && (responseData.detail || responseData.message) || message;
                        throw new Error(message);
                    }

                    const data = responseData;
                    if (data.status === 'success') {
                        if (data.warning) {
                            showToast(String(data.warning), 'warning');
                        } else {
                            showToast(editorSession.questionId ? '题目已成功更新！' : '题目已成功保存！');
                        }
                        const editorSessionStillCurrent = EditorState.isCurrent(editorSession);
                        const requestStillVisible = editorSessionStillCurrent &&
                            window.editorMatchesBackupSnapshot(requestBackupSnapshot);

                        // Never clear OCR or overwrite fields belonging to a newer
                        // edit/switch that happened after this request started.
                        if (requestStillVisible) {
                            clearContentOcrPreview();
                            clearOcrPreview();
                        }

                        // Delete draft if it was saved from a draft
                        if (editorSession.draftId) {
                            let drafts = getLocalStorageDrafts();
                            drafts = drafts.filter(d => d.id !== editorSession.draftId);
                            setLocalStorageDrafts(drafts);
                            updateDraftCountBadge();
                            if (EditorState.draftId === editorSession.draftId) {
                                EditorState.clearDraft();
                            }
                        }

                        // Reload list, dropdown, and autocomplete selectors
                        loadCategories();
                        refreshRelatedDropdown(relatedQuestionId);

                        if (!editorSession.questionId && editorSessionStillCurrent) {
                            // The POST response already contains the complete saved
                            // question. Commit only its identity and saved baseline;
                            // never start a second detail request that could overwrite
                            // input typed after the POST response arrived.
                            EditorState.useQuestion(data.question);
                            backupEditorState(data.question.id, null, requestBackupSnapshot);
                            document.getElementById('editorTitle').textContent = '编辑数学题';
                            if (!requestStillVisible) {
                                showToast('题目已保存；保存后继续输入的内容仍待再次保存', 'info');
                            }
                        } else if (editorSessionStillCurrent) {
                            backupEditorState(data.question.id, null, requestBackupSnapshot);
                        }
                        return true;
                    } else {
                        showToast('保存题目失败: ' + (data.detail || data.message || '未知错误'), 'error');
                        return false;
                    }
                } catch (err) {
                    showToast('保存数据出错: ' + err.message, 'error');
                    return false;
                }
            })();

            saveQuestionInFlight = saveOperation.finally(() => {
                saveQuestionInFlight = null;
                updateQuestionSaveButtonState();
            });
            updateQuestionSaveButtonState();
            return saveQuestionInFlight;
        }

        // AI classification modal handlers
        let temporaryClassifyData = null;
        let temporaryClassifyQuestionType = null;

        function setClassifyApplyEnabled(enabled) {
            const applyBtn = document.getElementById('classifyApplyButton');
            if (!applyBtn) return;
            applyBtn.disabled = !enabled;
            applyBtn.setAttribute('aria-disabled', enabled ? 'false' : 'true');
        }

        function resetClassifiedChoiceType() {
            temporaryClassifyQuestionType = null;
            ['classifySingleChoiceBtn', 'classifyMultiChoiceBtn'].forEach(id => {
                const button = document.getElementById(id);
                if (!button) return;
                button.setAttribute('aria-checked', 'false');
            });
        }

        function selectClassifiedChoiceType(questionType) {
            if (questionType !== 'single_choice' && questionType !== 'multi_choice') return;
            temporaryClassifyQuestionType = questionType;
            const selectedId = questionType === 'single_choice'
                ? 'classifySingleChoiceBtn'
                : 'classifyMultiChoiceBtn';
            ['classifySingleChoiceBtn', 'classifyMultiChoiceBtn'].forEach(id => {
                const button = document.getElementById(id);
                if (!button) return;
                const selected = id === selectedId;
                button.setAttribute('aria-checked', selected ? 'true' : 'false');
            });
            setClassifyApplyEnabled(true);
        }

        function openClassifyModal() {
            const modal = document.getElementById('aiClassifyModal');
            modal.classList.remove('hidden');
            window.MathBankModal.open(modal, { onEscape: closeClassifyModal });
            
            // Reset modal states
            document.getElementById('classifyLoading').classList.add('hidden');
            document.getElementById('classifyResult').classList.add('hidden');
            document.getElementById('classifyAIButton').classList.remove('hidden');
            document.getElementById('classifyApplyButton').classList.add('hidden');
            document.getElementById('choiceTypeConfirm').classList.add('hidden');
            document.getElementById('unknownQuestionFormNotice').classList.add('hidden');
            temporaryClassifyData = null;
            resetClassifiedChoiceType();
            setClassifyApplyEnabled(true);
            
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closeClassifyModal() {
            const modal = document.getElementById('aiClassifyModal');
            window.MathBankModal.close(modal);
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        function runAIClassify() {
            const content = document.getElementById('editContent').value;
            const loading = document.getElementById('classifyLoading');
            const resultBox = document.getElementById('classifyResult');
            const aiBtn = document.getElementById('classifyAIButton');
            const applyBtn = document.getElementById('classifyApplyButton');
            
            loading.classList.remove('hidden');
            aiBtn.classList.add('hidden');
            resultBox.classList.add('hidden');
            
            const formData = new FormData();
            formData.append('content', content);
            
            fetch('/api/ai/classify', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                loading.classList.add('hidden');
                
                if (data.status === 'success') {
                    temporaryClassifyData = data;
                    document.getElementById('recCompulsory').textContent = data.compulsory;
                    document.getElementById('recChapter').textContent = data.chapter;
                    const formLabels = {
                        'choice': '选择题',
                        'fill_in_blank': '填空题',
                        'detailed_answer': '解答题',
                        'unknown': '待手动确认'
                    };
                    const questionForm = formLabels[data.question_form] ? data.question_form : 'unknown';
                    document.getElementById('recQuestionForm').textContent = formLabels[questionForm];
                    document.getElementById('recQuestionFormSource').textContent = data.question_form_source === 'structure'
                        ? '结构规则识别'
                        : 'AI 建议';

                    const choiceConfirm = document.getElementById('choiceTypeConfirm');
                    const unknownNotice = document.getElementById('unknownQuestionFormNotice');
                    choiceConfirm.classList.toggle('hidden', questionForm !== 'choice');
                    unknownNotice.classList.toggle('hidden', questionForm !== 'unknown');
                    resetClassifiedChoiceType();

                    if (questionForm === 'fill_in_blank') {
                        temporaryClassifyQuestionType = 'fill_in_blank';
                        setClassifyApplyEnabled(true);
                    } else if (questionForm === 'detailed_answer') {
                        temporaryClassifyQuestionType = 'detailed_answer';
                        setClassifyApplyEnabled(true);
                    } else if (questionForm === 'choice') {
                        setClassifyApplyEnabled(false);
                    } else {
                        setClassifyApplyEnabled(true);
                    }
                    
                    resultBox.classList.remove('hidden');
                    applyBtn.classList.remove('hidden');
                } else {
                    showToast(data.message || 'AI 智能分类分析失败！', 'error');
                    aiBtn.classList.remove('hidden');
                }
            })
            .catch(err => {
                loading.classList.add('hidden');
                aiBtn.classList.remove('hidden');
                showToast('AI 分类出错: ' + err, 'error');
            });
        }

        function applyClassifyRecommendation() {
            if (!temporaryClassifyData) return;
            if (temporaryClassifyData.question_form === 'choice' && !temporaryClassifyQuestionType) {
                showToast('请先确认此题是单选题还是多选题！', 'error');
                return;
            }
            
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            const qtypeSelect = document.getElementById('editQType');
            
            const comp = temporaryClassifyData.compulsory;
            const chap = temporaryClassifyData.chapter;
            
            // Ensure nodes exist in local dictionary structure
            if (!categoryTree[comp]) {
                categoryTree[comp] = {};
            }
            if (!categoryTree[comp][chap]) {
                categoryTree[comp][chap] = [];
            }
            
            populateCategoryDropdowns();
            
            compSelect.value = comp;
            compSelect.onchange();
            chapSelect.value = chap;
            chapSelect.onchange();
            knowSelect.value = chap; // Default empty third level (小节) to chapter name

            if (temporaryClassifyQuestionType && qtypeSelect) {
                qtypeSelect.value = temporaryClassifyQuestionType;
                qtypeSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }
            
            closeClassifyModal();
            showToast('教材章节及题型已确认！');
            
            // Save question now with skipCheck = true
            setTimeout(() => {
                saveQuestion(true);
            }, 250);
        }

        window.selectClassifiedChoiceType = selectClassifiedChoiceType;

        // Delete Question
        function deleteQuestion(id) {
            if (blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认要在本地库中彻底删除此题目吗？不可恢复！')) {
                fetch(`/api/questions/${id}`, {
                    method: 'DELETE'
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        showToast('题目已成功删除！');
                        if (EditorState.questionId === id) {
                            startNewQuestion();
                        } else {
                            loadCategories();
                        }
                        refreshRelatedDropdown();
                    } else {
                        showToast(data.message, 'error');
                    }
                })
                .catch(err => {
                    showToast('删除题目出错: ' + err, 'error');
                });
            }
        }

        // ==========================================
        // LaTeX BATCH IMPORT & AI PARSE JS LOGIC
        // ==========================================
        let batchSelectedImages = [];
        let parsedQuestionsData = [];
        let parsedQuestionsGeneration = 0;
        // 转 TikZ 的**瞬时**状态（'converting' / 'failed'）。「已转成功」不进这张表——
        // 成功是持久事实，由题目上的 tikz_assets 判定。键 = 题号::规范化参考图路径，
        // 让同一张图在「题干条 / 解答区 / 选项区」三个入口共享同一状态。
        const parsedCardTikzStates = new Map();
        const parsedQuestionSaveInFlight = new Map();
        let parsedBatchSaveInFlight = null;
        const parsedDuplicateResults = new Map();
        const parsedDuplicateSnapshots = new Map();
        const parsedBatchAutoTags = new Map();
        let parsedDuplicateCheckSerial = 0;
        let parsedDuplicateCheckStatus = 'idle';
        let parsedDuplicateIndexStatus = null;
        let parsedDuplicateSummaryVisible = false;
        let parsedDuplicateActiveIndices = [];
        let parsedDuplicateReviewResolver = null;
        let parsedDuplicateCandidateObserver = null;
        let parsedDuplicateCandidateRenderJobs = new WeakMap();
        let parsedDuplicateCandidateFallbackQueue = [];
        let parsedDuplicateCandidateFallbackFrame = null;
        let parsedDuplicateCandidateRenderGeneration = 0;
        let allSourcesList = [];

        function replaceParsedQuestions(nextQuestions) {
            parsedQuestionsGeneration += 1;
            parsedQuestionsData = Array.isArray(nextQuestions) ? nextQuestions : [];
            // 新一批结果（从空变为非空）才生成新 token，避免同一次导入被拆成多批。
            // 挂在 window 上（带存在性守卫），以兼容前端切片测试的严格模式沙箱。
            if (typeof window !== 'undefined') {
                if (parsedQuestionsData.length === 0) {
                    window.__importBatchToken = '';
                } else if (!window.__importBatchToken) {
                    window.__importBatchToken = 'imp-' + Date.now().toString(36)
                        + '-' + Math.random().toString(36).slice(2, 8);
                }
            }
            // 新一批结果必须丢掉上一批的转 TikZ 瞬时状态（题号会被复用，否则
            // 上一批的「转换失败」会挂到新批次同题号的图上）。
            // typeof 守卫：前端切片测试可能只抽出本函数、没有那条 const 声明。
            if (typeof parsedCardTikzStates !== 'undefined' && parsedCardTikzStates
                && typeof parsedCardTikzStates.clear === 'function') {
                parsedCardTikzStates.clear();
            }
            if (typeof resetParsedDuplicateCheckState === 'function') {
                resetParsedDuplicateCheckState();
            }
            return parsedQuestionsGeneration;
        }

        function isParsedQuestionSaveContextCurrent(generation, index, question) {
            return generation === parsedQuestionsGeneration &&
                   parsedQuestionsData[index] === question;
        }

        function blockImportResetWhileSaving() {
            if (parsedQuestionSaveInFlight.size === 0 && !parsedBatchSaveInFlight) return false;
            const activeCount = Math.max(parsedQuestionSaveInFlight.size, parsedBatchSaveInFlight ? 1 : 0);
            showToast(`仍有 ${activeCount} 个导入任务正在处理，请等待完成后再重置或关闭`, 'info');
            return true;
        }

        function openImportModal() {
            // 独立工作台模式：直接切换到试卷导入工作台
            if (window.selectWorkspace) {
                window.selectWorkspace('paperImport', '试卷导入');
            }
        }

        function closeImportModal() {
            if (blockImportResetWhileSaving()) {
                return;
            }
            if (typeof performOrphanedTempCropsCleanup === 'function') {
                performOrphanedTempCropsCleanup();
            }
            // 独立工作台模式：切换回录题入口
            if (window.selectWorkspace) {
                window.selectWorkspace('entry', '录题入口');
            }
        }

        // PDF & Crop Global States
        window.currentPdfFile = null;
        window.pdfPageImages = [];
        // 本任务临时资产清单（含裁剪图），供审图面板算「未归属图」。
        window.pdfTempAssets = [];
        window.currentPdfTaskId = null;
        window.activeCropQuestionIndex = null;
        window.tempCroppedPathsThisSession = [];
        // 手动裁图的归属：'content'（题干，默认）| 'answer'（解答）
        window.pdfCropTargetSlot = 'content';

        // 切换手动裁图的投放位置；题干图与解答图在预览与保存时走不同槽位。
        window.setPdfCropTarget = function(slot) {
            window.pdfCropTargetSlot = slot === 'answer' ? 'answer' : 'content';
            const active = 'px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors '
                + 'bg-brand-600 text-white';
            const idle = 'px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors '
                + 'text-slate-300 hover:text-white';
            [
                ['pdfCropTargetContent', 'content'],
                ['pdfCropTargetAnswer', 'answer']
            ].forEach(([elementId, elementSlot]) => {
                const button = document.getElementById(elementId);
                if (!button) return;
                button.className = window.pdfCropTargetSlot === elementSlot ? active : idle;
                button.setAttribute(
                    'aria-pressed',
                    window.pdfCropTargetSlot === elementSlot ? 'true' : 'false'
                );
            });
        };

        // Crop Selection variables
        let isDrawing = false;
        let startX = 0;
        let startY = 0;
        let rectLeft = 0;
        let rectTop = 0;
        let rectWidth = 0;
        let rectHeight = 0;
        let activePageIndex = 0;
        let baseWidth = 0;
        let baseHeight = 0;
        let zoomFactor = 1.0;

        window.zoomPdfCropIn = function() {
            zoomFactor = Math.min(3.0, zoomFactor + 0.2);
            applyZoom();
        };

        window.zoomPdfCropOut = function() {
            zoomFactor = Math.max(0.5, zoomFactor - 0.2);
            applyZoom();
        };

        window.resetPdfCropZoom = function() {
            zoomFactor = 1.0;
            applyZoom();
        };

        function applyZoom() {
            const img = document.getElementById('pdfCropActiveImage');
            const container = document.getElementById('pdfCropImageContainer');
            const zoomText = document.getElementById('pdfZoomFactorText');
            if (!img || !container || baseWidth === 0) return;
            
            const w = baseWidth * zoomFactor;
            const h = baseHeight * zoomFactor;
            
            img.style.width = `${w}px`;
            img.style.height = `${h}px`;
            img.style.maxWidth = 'none';
            img.style.maxHeight = 'none';
            
            container.style.width = `${w}px`;
            container.style.height = `${h}px`;
            
            if (zoomText) {
                zoomText.textContent = `${Math.round(zoomFactor * 100)}%`;
            }
            
            clearPdfCropSelection();
        }

        function handleManualScreenshotForQuestion(questionIndex) {
            if (!window.pdfPageImages || window.pdfPageImages.length === 0) {
                showToast('当前没有可用的 PDF 页面，无法使用手动截图。请先上传 PDF 或从 PDF 导入试卷。', 'warning');
                return;
            }
            openPdfCropModalForQuestion(questionIndex);
        }

        function openPdfCropModalForQuestion(questionIndex) {
            window.activeCropQuestionIndex = questionIndex;
            activePageIndex = 0;
            zoomFactor = 1.0;
            baseWidth = 0;
            baseHeight = 0;
            window.lastCropLoadedSrc = '';
            
            // Render sidebar page thumbnails
            renderPdfPagesThumbnails();
            
            // Setup drawing listeners FIRST to avoid load race conditions
            setupPdfCropDrawListeners();
            
            // Load the first page (triggers src change and onload cleanly)
            loadPdfCropPage(0);
            
            // Show modal
            const modal = document.getElementById('pdfCropModal');
            modal.classList.remove('hidden');
            // 每次打开都回到默认「题干」，避免上一次的选择意外投放到解答。
            window.setPdfCropTarget('content');
            window.MathBankModal.open(modal, { onEscape: closePdfCropModal });
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closePdfCropModal() {
            const modal = document.getElementById('pdfCropModal');
            window.MathBankModal.close(modal);
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
                clearPdfCropSelection();
            }, 300);
        }

        function renderPdfPagesThumbnails() {
            const container = document.getElementById('pdfPagesThumbnailsContainer');
            container.innerHTML = '';
            
            window.pdfPageImages.forEach((url, i) => {
                const safeUrl = window.MathBankSafe.safeImageUrl(url);
                if (!safeUrl) return;
                const thumb = document.createElement('div');
                thumb.className = `cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white ${i === activePageIndex ? 'border-brand-500 shadow-md ring-2 ring-brand-500/20' : 'border-slate-200'}`;
                thumb.innerHTML = `
                    <img src="${window.MathBankSafe.escapeAttribute(safeUrl)}" class="w-full h-full object-cover" loading="lazy" decoding="async">
                    <div class="absolute bottom-1 right-1 bg-black/60 text-white text-[8px] px-1 rounded font-bold">P${i + 1}</div>
                `;
                thumb.onclick = () => {
                    loadPdfCropPage(i);
                };
                container.appendChild(thumb);
            });
        }

        function loadPdfCropPage(pageIdx) {
            activePageIndex = pageIdx;
            
            // Update active thumbnail border class
            const thumbnails = document.getElementById('pdfPagesThumbnailsContainer').children;
            for (let i = 0; i < thumbnails.length; i++) {
                if (i === pageIdx) {
                    thumbnails[i].className = 'cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white border-brand-500 shadow-md ring-2 ring-brand-500/20';
                } else {
                    thumbnails[i].className = 'cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white border-slate-200';
                }
            }
            
            document.getElementById('pdfCropPageIndicator').textContent = `第 ${pageIdx + 1} / ${window.pdfPageImages.length} 页`;
            
            const img = document.getElementById('pdfCropActiveImage');
            const safePageUrl = window.MathBankSafe.safeImageUrl(window.pdfPageImages[pageIdx]);
            img.src = safePageUrl || '';
            
            clearPdfCropSelection();
        }

        function setupPdfCropDrawListeners() {
            const wrapper = document.getElementById('pdfCropCanvasWrapper');
            if (!wrapper) return;
            
            // Recreate wrapper to drop old listeners clean
            const newWrapper = wrapper.cloneNode(true);
            wrapper.parentNode.replaceChild(newWrapper, wrapper);
            
            const activeWrapper = document.getElementById('pdfCropCanvasWrapper');
            const activeContainer = document.getElementById('pdfCropImageContainer');
            const activeOverlay = document.getElementById('pdfCropOverlayRect');
            const activeImg = document.getElementById('pdfCropActiveImage');
            
            // Bind trackpad pinch zoom
            activeWrapper.addEventListener('wheel', (e) => {
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    const zoomSpeed = 0.03;
                    if (e.deltaY < 0) {
                        zoomFactor = Math.min(3.0, zoomFactor + zoomSpeed);
                    } else {
                        zoomFactor = Math.max(0.5, zoomFactor - zoomSpeed);
                    }
                    applyZoom();
                }
            }, { passive: false });
            
            // Bind image onload
            activeImg.onload = function() {
                if (baseWidth === 0 || activeImg.src !== window.lastCropLoadedSrc) {
                    // Reset style to read original viewport-fitted size
                    activeImg.style.width = '';
                    activeImg.style.height = '';
                    activeImg.style.maxWidth = '';
                    activeImg.style.maxHeight = '';
                    
                    baseWidth = activeImg.clientWidth || 600;
                    baseHeight = activeImg.clientHeight || 800;
                    window.lastCropLoadedSrc = activeImg.src;
                }
                applyZoom();
            };
            
            // Bind drawing select listeners
            activeContainer.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return; // Only left click
                isDrawing = true;
                
                const rect = activeContainer.getBoundingClientRect();
                startX = e.clientX - rect.left;
                startY = e.clientY - rect.top;
                
                rectLeft = startX;
                rectTop = startY;
                rectWidth = 0;
                rectHeight = 0;
                
                activeOverlay.style.left = `${rectLeft}px`;
                activeOverlay.style.top = `${rectTop}px`;
                activeOverlay.style.width = '0px';
                activeOverlay.style.height = '0px';
                activeOverlay.classList.remove('hidden');
                
                e.preventDefault();
            });
            
            window.addEventListener('mousemove', (e) => {
                if (!isDrawing) return;
                
                const rect = activeContainer.getBoundingClientRect();
                let currentX = e.clientX - rect.left;
                let currentY = e.clientY - rect.top;
                
                currentX = Math.max(0, Math.min(currentX, rect.width));
                currentY = Math.max(0, Math.min(currentY, rect.height));
                
                rectLeft = Math.min(startX, currentX);
                rectTop = Math.min(startY, currentY);
                rectWidth = Math.abs(startX - currentX);
                rectHeight = Math.abs(startY - currentY);
                
                activeOverlay.style.left = `${rectLeft}px`;
                activeOverlay.style.top = `${rectTop}px`;
                activeOverlay.style.width = `${rectWidth}px`;
                activeOverlay.style.height = `${rectHeight}px`;
            });
            
            window.addEventListener('mouseup', () => {
                if (!isDrawing) return;
                isDrawing = false;
                
                if (rectWidth > 15 && rectHeight > 15) {
                    document.getElementById('pdfCropConfirmBtn').disabled = false;
                    document.getElementById('pdfCropClearBtn').disabled = false;
                } else {
                    clearPdfCropSelection();
                }
            });
        }

        function clearPdfCropSelection() {
            const overlay = document.getElementById('pdfCropOverlayRect');
            if (overlay) {
                overlay.classList.add('hidden');
                overlay.style.width = '0px';
                overlay.style.height = '0px';
            }
            rectWidth = 0;
            rectHeight = 0;
            
            const confirmBtn = document.getElementById('pdfCropConfirmBtn');
            if (confirmBtn) confirmBtn.disabled = true;
            
            const clearBtn = document.getElementById('pdfCropClearBtn');
            if (clearBtn) clearBtn.disabled = true;
        }

        function submitPdfCropCoordinates() {
            const img = document.getElementById('pdfCropActiveImage');
            const container = document.getElementById('pdfCropImageContainer');
            
            // Adjust coords relative to base (un-zoomed) dimensions
            const containerRect = container.getBoundingClientRect();
            
            const xmin = (rectLeft / containerRect.width) * 100.0;
            const ymin = (rectTop / containerRect.height) * 100.0;
            const xmax = ((rectLeft + rectWidth) / containerRect.width) * 100.0;
            const ymax = ((rectTop + rectHeight) / containerRect.height) * 100.0;
            
            const confirmBtn = document.getElementById('pdfCropConfirmBtn');
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>正在裁剪...</span>';
            
            fetch('/api/ai/manual-crop-pdf', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': localStorage.getItem('local_token') || ''
                },
                body: JSON.stringify({
                    task_id: window.currentPdfTaskId,
                    page_index: activePageIndex,
                    ymin: ymin,
                    xmin: xmin,
                    ymax: ymax,
                    xmax: xmax
                })
            })
            .then(r => {
                if (!r.ok) throw new Error("裁剪失败");
                return r.json();
            })
            .then(data => {
                if (data.status === 'success') {
                    const croppedUrl = window.MathBankSafe.safeImageUrl(data.image_path);
                    if (!croppedUrl) throw new Error('裁剪接口返回了无效的图片路径');
                    window.tempCroppedPathsThisSession.push(croppedUrl);

                    const qIdx = window.activeCropQuestionIndex;
                    if (qIdx !== null && parsedQuestionsData[qIdx]) {
                        const q = parsedQuestionsData[qIdx];
                        if (!q.image_paths) q.image_paths = [];

                        if (!q.image_paths.includes(croppedUrl)) {
                            q.image_paths.push(croppedUrl);
                        }

                        // 归属：默认题干；选了「解答」就写进解答 markdown，
                        // 于是它会由解答预览内联呈现、且不再出现在题干配图里。
                        const targetSlot = window.pdfCropTargetSlot === 'answer'
                            ? 'answer'
                            : 'content';
                        const targetTextareaClass = targetSlot === 'answer'
                            ? '.card-answer-textarea'
                            : '.card-content-textarea';
                        const card = document.getElementById(`parsed-card-${qIdx}`);
                        if (card) {
                            const contentInput = card.querySelector('.card-content-textarea');
                            const answerInput = card.querySelector('.card-answer-textarea');
                            if (targetSlot === 'answer' && answerInput) {
                                answerInput.value = answerInput.value.trim() + `\n\n![插图](${croppedUrl})\n\n`;
                                answerInput.dispatchEvent(new Event('input'));
                            } else if (targetSlot === 'content' && contentInput) {
                                // 题干截图不内联到 markdown：内联图会被 sanitize 后失去
                                // 来源角标与「转 TikZ」按钮。保留在 q.image_paths 中，
                                // 由 appendParsedCardImageStrip 统一以配图条形式展示。
                                renderParsedCardPreview(
                                    card,
                                    contentInput.value,
                                    answerInput ? answerInput.value : ''
                                );
                            }
                        }

                        const badgesContainer = document.getElementById(`card-images-badges-${qIdx}`);
                        if (badgesContainer) {
                            badgesContainer.innerHTML = '';
                            const answerOwned = new Set(parsedCardAnswerImagePaths(q.answer_markdown));
                            q.image_paths
                                .filter(path => !answerOwned.has(path))
                                .forEach(path => appendSafeImageBadge(badgesContainer, path));
                        }
                        showToast(
                            targetSlot === 'answer'
                                ? '配图已截取到「解答」，保存后归入解答插图。'
                                : '裁剪并生成配图成功！已自动关联至此题卡。'
                        );
                    }

                    closePdfCropModal();
                } else {
                    throw new Error(data.message || "裁剪错误");
                }
            })
            .catch(err => {
                console.error(err);
                showToast(`手动截图报错: ${err.message}`, 'error');
            })
            .finally(() => {
                confirmBtn.innerHTML = '<i class="fa-solid fa-crop-simple mr-1.5"></i><span>确认截取配图</span>';
            });
        }

        function performOrphanedTempCropsCleanup() {
            const tempPaths = [];
            parsedQuestionsData.forEach(q => {
                if (!q.saved && q.image_paths) {
                    q.image_paths.forEach(p => {
                        if (p.includes('/tmp/')) {
                            tempPaths.push(p);
                        }
                    });
                }
            });
            
            if (window.tempCroppedPathsThisSession && window.tempCroppedPathsThisSession.length > 0) {
                window.tempCroppedPathsThisSession.forEach(p => {
                    if (!tempPaths.includes(p)) {
                        tempPaths.push(p);
                    }
                });
            }
            
            if (tempPaths.length === 0) return;
            
            fetch('/api/ai/clear-temp-crops', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': localStorage.getItem('local_token') || ''
                },
                body: JSON.stringify({ paths: tempPaths })
            })
            .then(r => r.json())
            .then(res => {
                console.log("[Storage Cleanup] Server cleaned temporary crops:", res);
                window.tempCroppedPathsThisSession = [];
            })
            .catch(err => {
                console.error("[Storage Cleanup] Error:", err);
            });
        }


        function setupImportFileHandlers() {
            const texDrop = document.getElementById('texDropzone');
            const texInput = document.getElementById('texFileInput');
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            const latexTextarea = document.getElementById('importLatexContent');

            const imagesDrop = document.getElementById('imagesDropzone');
            const imagesInput = document.getElementById('imagesFileInput');
            const imagesCountName = document.getElementById('imagesCountName');
            const imagesFileIcon = document.getElementById('imagesFileIcon');
            const imagesListContainer = document.getElementById('importImagesList');

            // LaTeX / PDF File drag & select
            if (!texDrop || !texInput || !texFileName || !texFileIcon || !latexTextarea) {
                console.error('[Import] 试卷文件上传控件不完整，无法初始化文件选择。');
                return;
            }
            texDrop.addEventListener('click', () => texInput.click());
            texInput.addEventListener('change', (e) => handleTexFileSelect(e.target.files[0]));
            // 作用域内导出，供录题入口拖拽等全局入口复用
            window.handleTexFileSelect = handleTexFileSelect;

            ['dragenter', 'dragover'].forEach(eventName => {
                texDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    texDrop.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                texDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    texDrop.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            texDrop.addEventListener('drop', (e) => {
                const file = e.dataTransfer.files[0];
                const lowerFileName = file ? file.name.toLowerCase() : '';
                if (file && (lowerFileName.endsWith('.tex') || lowerFileName.endsWith('.md') || lowerFileName.endsWith('.markdown') || lowerFileName.endsWith('.txt') || lowerFileName.endsWith('.pdf') || lowerFileName.endsWith('.docx'))) {
                    handleTexFileSelect(file);
                } else {
                    showToast('请拖入有效的 .tex、.md/.markdown/.txt、.pdf 或 .docx 试卷文件！', 'warning');
                }
            });

            function readFileAsArrayBuffer(file) {
                if (file && typeof file.arrayBuffer === 'function') {
                    return file.arrayBuffer();
                }
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = () => reject(reader.error || new Error('无法读取本地文件'));
                    reader.readAsArrayBuffer(file);
                });
            }

            async function decodeTexFileLocally(file) {
                const buffer = await readFileAsArrayBuffer(file);
                const bytes = new Uint8Array(buffer || new ArrayBuffer(0));
                if (!bytes.length) throw new Error('TeX 文件为空');
                if (bytes.length > 5 * 1024 * 1024) throw new Error('TeX 文件超过 5MB 上限');

                const attempts = [];
                if ((bytes[0] === 0xFF && bytes[1] === 0xFE) || (bytes[0] === 0xFE && bytes[1] === 0xFF)) {
                    attempts.push('utf-16');
                } else if (bytes.length >= 8) {
                    let evenNuls = 0;
                    let oddNuls = 0;
                    const sampleLength = Math.min(bytes.length, 200);
                    for (let i = 0; i < sampleLength; i++) {
                        if (bytes[i] === 0) {
                            if (i % 2 === 0) evenNuls++;
                            else oddNuls++;
                        }
                    }
                    if (oddNuls > Math.max(2, evenNuls * 3)) attempts.push('utf-16le');
                    if (evenNuls > Math.max(2, oddNuls * 3)) attempts.push('utf-16be');
                }
                attempts.push('utf-8', 'gb18030');

                for (const encoding of attempts) {
                    try {
                        const decoded = new TextDecoder(encoding, {fatal: true}).decode(bytes);
                        return decoded
                            .replace(/^\uFEFF/, '')
                            .replace(/\u0000/g, '')
                            .replace(/\r\n?/g, '\n');
                    } catch (_error) {
                        // Continue through the explicit safe encoding fallbacks.
                    }
                }
                throw new Error('无法识别文件编码，请将 TeX 另存为 UTF-8 后重试');
            }

            function applyLocallyReadTex(file, source, diagnostics = null, serverTitle = '') {
                latexTextarea.value = source || '';
                latexTextarea.disabled = false;
                window.currentTexDiagnostics = diagnostics;
                const titleInput = document.getElementById('importPaperTitle');
                const autoTitle = serverTitle || extractTitleFromLatex(source || '');
                if (autoTitle) {
                    titleInput.value = autoTitle;
                } else if (!titleInput.value.trim()) {
                    titleInput.value = file.name.replace(/\.[^/.]+$/, '');
                }
                return autoTitle;
            }

            function handleTexFileSelect(file) {
                if (!file) return;
                const lowerFileName = file.name.toLowerCase();
                const isMarkdown = lowerFileName.endsWith('.md') || lowerFileName.endsWith('.markdown') || lowerFileName.endsWith('.txt');
                if (!lowerFileName.endsWith('.tex') && !isMarkdown && !lowerFileName.endsWith('.pdf') && !lowerFileName.endsWith('.docx')) {
                    showToast('仅支持 .tex、.md/.markdown/.txt、.pdf 或 .docx 试卷文件。', 'warning');
                    texInput.value = '';
                    return;
                }
                window.currentTexReadToken = null;
                const texImagesSection = document.getElementById('texImagesSection');
                
                if (lowerFileName.endsWith('.docx')) {
                    window.currentDocxFile = file;
                    window.currentPdfFile = null;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-word text-blue-600 text-xl mb-1.5 animate-bounce";
                    latexTextarea.value = `[Word (.docx) 试卷已成功载入: ${file.name}]\n系统将安全提取 OMML 公式与高清插图；MathType 公式无法可靠转换时会保留原预览图并标记人工核对。`;
                    latexTextarea.disabled = true;
                    
                    const titleInput = document.getElementById('importPaperTitle');
                    if (!titleInput.value) {
                        titleInput.value = file.name.replace(/\.[^/.]+$/, "");
                    }
                    if (typeof prefillSourceFromFilename === 'function') prefillSourceFromFilename(file.name);
                    if (typeof applyStandardPaperTitle === 'function') applyStandardPaperTitle();
                    
                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
                    if (texImagesSection) texImagesSection.classList.add('hidden');
                } else if (isMarkdown) {
                    window.currentDocxFile = null;
                    window.currentPdfFile = null;
                    window.currentTexDiagnostics = null;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-lines text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.disabled = true;
                    latexTextarea.value = '正在安全读取 Markdown 源码...';

                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
                    if (texImagesSection) texImagesSection.classList.remove('hidden');

                    decodeTexFileLocally(file)
                        .then(source => {
                            latexTextarea.value = source;
                            latexTextarea.disabled = false;
                            const titleInput = document.getElementById('importPaperTitle');
                            if (titleInput && !titleInput.value) {
                                const heading = source.match(/^\s*#\s+(.+)$/m);
                                titleInput.value = heading
                                    ? heading[1].trim()
                                    : file.name.replace(/\.[^/.]+$/, "");
                            }
                            if (typeof prefillSourceFromFilename === 'function') prefillSourceFromFilename(file.name);
                            if (typeof applyStandardPaperTitle === 'function') applyStandardPaperTitle();
                            showToast('Markdown 试卷已读入编辑框，可继续配图后一键拆解。', 'info');
                        })
                        .catch(error => {
                            latexTextarea.value = '';
                            latexTextarea.disabled = false;
                            texInput.value = '';
                            texFileName.textContent = 'Markdown 文件读取失败，请重新选择';
                            texFileName.className = 'text-xs text-red-500 font-bold';
                            showToast(`Markdown 文件读取失败：${error.message}`, 'error');
                        });
                } else if (lowerFileName.endsWith('.pdf')) {
                    window.currentDocxFile = null;
                    window.currentPdfFile = file;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-pdf text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.value = `[PDF 试卷已成功载入: ${file.name}]\n总页数、高清转换与插图定位将会在点击“一键 AI 智能拆解并关联”后于后台异步执行。`;
                    latexTextarea.disabled = true;
                    
                    const titleInput = document.getElementById('importPaperTitle');
                    if (!titleInput.value) {
                        titleInput.value = file.name.replace(/\.[^/.]+$/, "");
                    }
                    if (typeof prefillSourceFromFilename === 'function') prefillSourceFromFilename(file.name);
                    if (typeof applyStandardPaperTitle === 'function') applyStandardPaperTitle();
                    
                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.remove('hidden');
                    if (texImagesSection) texImagesSection.classList.add('hidden');
                } else {
                    window.currentDocxFile = null;
                    window.currentPdfFile = null;
                    window.currentTexDiagnostics = null;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-circle-check text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.disabled = true;
                    latexTextarea.value = '正在安全读取并检查 TeX 源码编码与结构...';
                    
                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
                    if (texImagesSection) texImagesSection.classList.remove('hidden');
                    
                    const runBtn = document.getElementById('runParseBtn');
                    if (runBtn) {
                        runBtn.disabled = true;
                        runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>正在读取 TeX...</span>';
                    }
                    const readToken = `${Date.now()}-${Math.random()}`;
                    window.currentTexReadToken = readToken;
                    let localTitle = '';
                    decodeTexFileLocally(file)
                    .then(localSource => {
                        if (window.currentTexReadToken !== readToken) return null;
                        localTitle = applyLocallyReadTex(file, localSource);

                        const formData = new FormData();
                        formData.append('file', file);
                        const controller = typeof AbortController === 'function' ? new AbortController() : null;
                        const timeoutId = controller ? setTimeout(() => controller.abort(), 8000) : null;
                        return fetch('/api/upload/tex-source', {
                            method: 'POST',
                            headers: {'X-Local-Token': localStorage.getItem('local_token') || ''},
                            body: formData,
                            signal: controller ? controller.signal : undefined
                        })
                        .then(async response => {
                            const data = await response.json().catch(() => ({}));
                            if (!response.ok || data.status !== 'success') {
                                throw new Error(data.message || data.detail || `HTTP ${response.status}`);
                            }
                            return data;
                        })
                        .then(data => {
                            if (window.currentTexReadToken !== readToken) return;
                            const autoTitle = applyLocallyReadTex(
                                file,
                                data.source || localSource,
                                data.diagnostics || null,
                                data.title || ''
                            );
                            if (autoTitle && autoTitle !== localTitle) {
                                showToast(`已自动从 TeX 文件中读取试卷标题：${autoTitle}`);
                            }
                            const diagnostics = data.diagnostics || {};
                            if (diagnostics.encoding_fallback) {
                                showToast(`已按 ${diagnostics.encoding} 编码安全读取该 TeX 文件。`, 'info');
                            }
                            if (Array.isArray(diagnostics.warnings) && diagnostics.warnings.length) {
                                showToast(`TeX 预检发现 ${diagnostics.warnings.length} 项需留意的结构，拆分后会继续提示。`, 'warning');
                            }
                        })
                        .catch(error => {
                            if (window.currentTexReadToken !== readToken) return;
                            console.warn('TeX 后端预检不可用，已保留浏览器本地读取结果:', error);
                            window.currentTexDiagnostics = {
                                local_read_fallback: true,
                                warnings: ['后端 TeX 预检暂不可用，已使用浏览器本地读取结果']
                            };
                            showToast('TeX 文件已读取；后端预检暂不可用，不影响继续编辑。', 'warning');
                        })
                        .finally(() => {
                            if (timeoutId) clearTimeout(timeoutId);
                        });
                    })
                    .catch(error => {
                        if (window.currentTexReadToken !== readToken) return;
                        latexTextarea.value = '';
                        latexTextarea.disabled = false;
                        texInput.value = '';
                        texFileName.textContent = 'TeX 文件读取失败，请重新选择';
                        texFileName.className = 'text-xs text-red-500 font-bold';
                        showToast(`TeX 文件读取失败：${error.message}`, 'error');
                    })
                    .finally(() => {
                        if (runBtn) {
                            runBtn.disabled = false;
                            runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i><span>一键 AI 智能拆解并关联</span>';
                        }
                    });
                }
            }

            // 旧版独立配图区在部分页面布局中不存在；仅在整套控件齐全时绑定，
            // 避免空元素让试卷文件选择和后续初始化一起中断。
            if (imagesDrop && imagesInput && imagesCountName && imagesFileIcon && imagesListContainer) {
                imagesDrop.addEventListener('click', () => imagesInput.click());
                imagesInput.addEventListener('change', (e) => handleImagesSelect(e.target.files));

                ['dragenter', 'dragover'].forEach(eventName => {
                    imagesDrop.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        imagesDrop.classList.add('border-brand-500', 'bg-brand-50/20');
                    }, false);
                });

                ['dragleave', 'drop'].forEach(eventName => {
                    imagesDrop.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        imagesDrop.classList.remove('border-brand-500', 'bg-brand-50/20');
                    }, false);
                });

                imagesDrop.addEventListener('drop', (e) => {
                    handleImagesSelect(e.dataTransfer.files);
                });
            }

            function handleImagesSelect(files) {
                if (!files || files.length === 0) return;
                const allowedExtensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp'];
                let rejected = 0;
                for (let i = 0; i < files.length; i++) {
                    const f = files[i];
                    const lowerName = (f.name || '').toLowerCase();
                    const allowed = f.type.startsWith('image/') && allowedExtensions.some(ext => lowerName.endsWith(ext));
                    if (allowed && batchSelectedImages.length < 20) {
                        if (!batchSelectedImages.some(img => img.name === f.name)) {
                            batchSelectedImages.push(f);
                        }
                    } else {
                        rejected++;
                    }
                }
                renderImagesList();
                if (rejected) {
                    showToast(`有 ${rejected} 个文件不是受支持的图片，或已超过 20 张上限。`, 'warning');
                }
            }

            // Input event listener for pasted LaTeX or manual edits
            latexTextarea.addEventListener('input', () => {
                const autoTitle = extractTitleFromLatex(latexTextarea.value);
                if (autoTitle) {
                    const titleInput = document.getElementById('importPaperTitle');
                    if (titleInput.value.trim() === '') {
                        titleInput.value = autoTitle;
                        showToast(`已从输入中自动读取试卷标题: ${autoTitle}`);
                    }
                }
            });

            // ---- 全局拖放：试卷文件 / 配套图片拖到窗口任意位置即可导入 ----
            let globalDragDepth = 0;
            let globalDropOverlay = null;

            function paperImportWorkspaceActive() {
                const ws = document.getElementById('paperImportWorkspace');
                return !!ws && !ws.classList.contains('hidden');
            }

            function ensureGlobalDropOverlay() {
                if (globalDropOverlay) return globalDropOverlay;
                const ov = document.createElement('div');
                ov.id = 'globalImportDropOverlay';
                ov.className = 'fixed inset-0 z-[85] hidden items-center justify-center pointer-events-none';
                ov.style.background = 'rgba(79, 70, 229, 0.10)';
                ov.style.border = '3px dashed rgba(79, 70, 229, 0.65)';
                ov.style.borderRadius = '16px';
                ov.innerHTML =
                    '<div style="pointer-events:none;background:rgba(255,255,255,0.92);backdrop-filter:blur(8px);border:1px solid rgba(79,70,229,0.3);border-radius:16px;padding:28px 40px;text-align:center;box-shadow:0 20px 60px rgba(79,70,229,0.25);">' +
                    '<div style="font-size:34px;color:#4f46e5;margin-bottom:10px;"><i class="fa-solid fa-file-import"></i></div>' +
                    '<div style="font-size:16px;font-weight:700;color:#1e1b4b;letter-spacing:0.5px;">松开鼠标，导入试卷文件</div>' +
                    '<div style="font-size:12px;color:#64748b;margin-top:6px;">支持 .tex / .md / .pdf / .docx，图片自动归入配套图片</div>' +
                    '</div>';
                document.body.appendChild(ov);
                globalDropOverlay = ov;
                return ov;
            }

            function hideGlobalDropOverlay() {
                if (!globalDropOverlay) return;
                globalDropOverlay.classList.add('hidden');
                globalDropOverlay.classList.remove('flex');
            }

            function globalHasFiles(e) {
                if (!e.dataTransfer) return false;
                const types = Array.from(e.dataTransfer.types || []);
                return types.some(t => t && t.toLowerCase() === 'files');
            }

            window.addEventListener('dragenter', (e) => {
                if (!paperImportWorkspaceActive() || !globalHasFiles(e)) return;
                e.preventDefault();
                globalDragDepth++;
                const ov = ensureGlobalDropOverlay();
                ov.classList.remove('hidden');
                ov.classList.add('flex');
            }, false);

            window.addEventListener('dragover', (e) => {
                if (!paperImportWorkspaceActive() || !globalHasFiles(e)) return;
                e.preventDefault();
                if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
            }, false);

            window.addEventListener('dragleave', (e) => {
                if (!paperImportWorkspaceActive()) return;
                globalDragDepth = Math.max(0, globalDragDepth - 1);
                if (globalDragDepth === 0) hideGlobalDropOverlay();
            }, false);

            window.addEventListener('drop', (e) => {
                if (!paperImportWorkspaceActive()) return;
                e.preventDefault();
                globalDragDepth = 0;
                hideGlobalDropOverlay();
                const files = e.dataTransfer && e.dataTransfer.files;
                if (!files || files.length === 0) return;
                const first = files[0];
                const n = (first.name || '').toLowerCase();
                if (n.endsWith('.tex') || n.endsWith('.md') || n.endsWith('.markdown') || n.endsWith('.txt') || n.endsWith('.pdf') || n.endsWith('.docx')) {
                    handleTexFileSelect(first);
                } else if (first.type && first.type.startsWith('image/')) {
                    handleImagesSelect(files);
                } else {
                    showToast('请拖入 .tex / .md / .pdf / .docx 试卷文件或图片', 'warning');
                }
            }, false);
        }

        // 左栏输入栏折叠/展开：拆完卷后给题目卡片让出宽度
        window.toggleImportInputPane = function () {
            const pane = document.getElementById('importInputPane');
            const btn = document.getElementById('toggleImportInputPaneBtn');
            if (!pane || !btn) return;
            const willHide = !pane.classList.contains('hidden');
            pane.classList.toggle('hidden', willHide);
            const icon = btn.querySelector('i');
            const label = btn.querySelector('span');
            if (willHide) {
                if (icon) icon.className = 'fa-solid fa-angles-right text-[9px]';
                if (label) label.textContent = '展开输入栏';
            } else {
                if (icon) icon.className = 'fa-solid fa-angles-left text-[9px]';
                if (label) label.textContent = '收起输入栏';
            }
        };

        function demoteCurrentImportLog(consoleDiv) {
            consoleDiv.querySelectorAll('[data-import-log-state="current"]').forEach(logEl => {
                logEl.dataset.importLogState = 'completed';
                logEl.className = 'text-slate-400 py-0.5';
                logEl.removeAttribute('aria-current');
            });
        }

        function appendImportLog(message, type = 'info') {
            const consoleDiv = document.getElementById('importLogsConsole');
            if (!consoleDiv) return;
            const logEl = document.createElement('div');

            const isCurrentStep = type === 'current' || type === 'success';
            if (isCurrentStep || type === 'error') {
                demoteCurrentImportLog(consoleDiv);
            }

            // 日志一律不加粗（用户明确要求），仅靠颜色区分状态。
            let colorClass = 'text-slate-500';
            if (isCurrentStep) {
                colorClass = 'text-emerald-600';
                logEl.dataset.importLogState = 'current';
                logEl.setAttribute('aria-current', 'step');
            } else if (type === 'completed') {
                colorClass = 'text-slate-400';
                logEl.dataset.importLogState = 'completed';
            } else if (type === 'error') {
                colorClass = 'text-red-500';
                logEl.dataset.importLogState = 'error';
            } else if (type === 'warning') {
                colorClass = 'text-amber-500';
                logEl.dataset.importLogState = 'warning';
            }

            logEl.className = `${colorClass} py-1`;
            logEl.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            consoleDiv.appendChild(logEl);
            consoleDiv.scrollTop = consoleDiv.scrollHeight;
        }

        // MinerU 结果缓存开关：默认复用本机缓存（不烧云额度），勾选后才强制重跑。
        // 复用的是既有 pdfStrategy 单选组，不新增第二套状态来源。
        function setupMineruCacheControls() {
            const row = document.getElementById('mineruForceFreshRow');
            const checkbox = document.getElementById('mineruForceFresh');
            if (!row || !checkbox) return;
            const sync = () => {
                const radio = document.querySelector('input[name="pdfStrategy"]:checked');
                const isCloud = !!radio && radio.value === 'mineru_cloud';
                row.classList.toggle('hidden', !isCloud);
                if (!isCloud) checkbox.checked = false;
            };
            document.querySelectorAll('input[name="pdfStrategy"]').forEach((input) => {
                input.addEventListener('change', sync);
            });
            sync();
        }

        function runAIPaperParse() {
            const titleInput = document.getElementById('importPaperTitle');
            const title = titleInput.value.trim();
            const latex = document.getElementById('importLatexContent').value.trim();

            if (!latex && !window.currentPdfFile) {
                showToast('请粘贴或上传 LaTeX 试卷内容，或拖入 PDF 文件！', 'warning');
                return;
            }

            if (!title) {
                if (!confirm('试卷标题为空，导入后题目来源将显示为空。\n确定继续吗？')) {
                    titleInput.focus();
                    return;
                }
            }

            // One generation owns task creation, polling and terminal UI. A
            // reset or a newer import makes every older callback inert.
            const importTaskGeneration = beginDocumentImportTask();

            // Hide placeholder & results, show loading skeleton
            document.getElementById('importPlaceholder').classList.add('hidden');
            document.getElementById('parsedQuestionsWrapper').classList.add('hidden');
            const loadingState = document.getElementById('importLoadingState');
            loadingState.classList.remove('hidden');

            const loadingIcon = loadingState.querySelector('.fa-circle-notch, .fa-spinner, .fa-circle-exclamation');
            if (loadingIcon) {
                loadingIcon.className = 'fa-solid fa-circle-notch fa-spin text-brand-600 text-3xl inline-block';
            }
            
            // Clear logs
            const consoleDiv = document.getElementById('importLogsConsole');
            consoleDiv.innerHTML = '<div>[SYSTEM] 初始化 AI 拆解任务...</div>';
            
            const runBtn = document.getElementById('runParseBtn');
            runBtn.disabled = true;
            runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin inline-block"></i> <span>正在全力拆解中...</span>';

            const generateAnswersCheckbox = document.getElementById('importGenerateAnswers');
            const generateAnswers = generateAnswersCheckbox ? generateAnswersCheckbox.checked : false;

            // Handle Word (.docx) branch
            if (window.currentDocxFile) {
                document.getElementById('importLoadingText').textContent = '正在上传 Word 试卷并安全提取数学公式与高清插图...';
                appendImportLog('开始上传 Word (.docx) 试卷文件...', 'current');
                document.getElementById('importProgressBarContainer').classList.remove('hidden');
                document.getElementById('importProgressBar').style.width = '0%';

                const docxFormData = new FormData();
                docxFormData.append('file', window.currentDocxFile);
                docxFormData.append('generate_answers', generateAnswers ? "true" : "false");
                docxFormData.append('paper_title', title);

                fetch('/api/upload/docx-task', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: docxFormData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(taskData => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    if (taskData.status === 'success') {
                        const taskId = taskData.task_id;
                        appendImportLog(`Word 任务已成功创建！任务 ID: ${taskId}，开始轮询分析切片进度...`, 'success');
                        pollPdfTaskStatus(taskId, importTaskGeneration);
                    } else {
                        throw new Error(taskData.message || '创建 Word 解析任务失败');
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    console.error(err);
                    appendImportLog(`Word 任务创建失败: ${err.message}`, 'error');
                    
                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = 'Word 上传解析出错！';

                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
                return;
            }

            // Handle PDF branch
            if (window.currentPdfFile) {
                document.getElementById('importLoadingText').textContent = '正在上传 PDF 试卷并创建处理任务...';
                appendImportLog('开始上传 PDF 试卷文件...', 'current');
                document.getElementById('importProgressBarContainer').classList.remove('hidden');
                document.getElementById('importProgressBar').style.width = '0%';

                const pdfFormData = new FormData();
                pdfFormData.append('file', window.currentPdfFile);
                pdfFormData.append('generate_answers', generateAnswers ? "true" : "false");
                pdfFormData.append('paper_title', title);
                
                const pdfPageRangeInput = document.getElementById('pdfPageRange');
                const pageRange = pdfPageRangeInput ? pdfPageRangeInput.value.trim() : '';
                if (pageRange) {
                    pdfFormData.append('page_range', pageRange);
                }

                const pdfStrategyRadio = document.querySelector('input[name="pdfStrategy"]:checked');
                const pdfStrategy = pdfStrategyRadio ? pdfStrategyRadio.value : 'native_preferred';
                // 只有云端路线有结果缓存可言；其余路线不发送 force_fresh。
                const mineruForceFresh = pdfStrategy === 'mineru_cloud'
                    && (document.getElementById('mineruForceFresh') || {}).checked === true;

                // 录入路线：原生直提 / 全图视觉 OCR / MinerU 云端解析，
                // 三者统一走 pdf-task 接口，由 pdf_strategy 决定后端分支。
                // （本地 MinerU 结果包 zip 导入已下线：包里没有源 PDF，渲染不了整页原图，
                //  也无法推断题目来源页，与「对照原页核对配图」的能力冲突。）
                const endpoint = '/api/upload/pdf-task';
                pdfFormData.append('pdf_strategy', pdfStrategy);
                if (mineruForceFresh) {
                    pdfFormData.append('force_fresh', 'true');
                }

                fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: pdfFormData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(taskData => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    if (taskData.status === 'success') {
                        const taskId = taskData.task_id;
                        appendImportLog(`任务已成功创建！任务 ID: ${taskId}，开始轮询后台分析进度...`, 'success');
                        pollPdfTaskStatus(taskId, importTaskGeneration);
                    } else {
                        throw new Error(taskData.message || '创建 PDF 解析任务失败');
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    console.error(err);
                    appendImportLog(`PDF 任务创建失败: ${err.message}`, 'error');
                    
                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = 'PDF 上传解析出错！';

                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
                return;
            }

            // Normal LaTeX branch
            document.getElementById('importLoadingText').textContent = '正在上传配套图片并整理文件名映射...';
            appendImportLog('开始检查配套图片...', 'current');
            document.getElementById('importProgressBarContainer').classList.add('hidden');

            let uploadPromise = Promise.resolve({});
            if (batchSelectedImages.length > 0) {
                appendImportLog(`检测到 ${batchSelectedImages.length} 张配图，开始多线程上传中...`, 'current');
                const imgFormData = new FormData();
                batchSelectedImages.forEach(file => {
                    imgFormData.append('files', file);
                });

                uploadPromise = fetch('/api/upload/batch', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: imgFormData
                })
                .then(r => r.json())
                .then(data => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return null;
                    if (data.status === 'success') {
                        appendImportLog('批量配图上传成功！已成功建立本地重命名路径映射。', 'success');
                        return data.mapping;
                    } else {
                        throw new Error(data.message || '图片上传失败');
                    }
                });
            } else {
                appendImportLog('无插图需关联，直接运行大文本 AI 拆解。', 'current');
            }

            uploadPromise
                .then(imageMapping => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return null;
                    let parseModelFriendly = systemPreferParseModel.includes('/') ? systemPreferParseModel.split('/').pop() : systemPreferParseModel;
                    let parseBrand = 'AI';
                    document.getElementById('importLoadingText').textContent = `${parseBrand} 正在智能分析并拆解试卷，请稍候...`;
                    appendImportLog(`正在调用 ${parseModelFriendly} 教研大模型进行试题智能分割与属性匹配...`, 'current');
                    appendImportLog('大纲映射范围：高中人教版A 必修一至选择性必修三。请耐心等候...', 'info');

                    const parseFormData = new FormData();
                    parseFormData.append('latex_content', latex);
                    parseFormData.append('paper_title', title);
                    parseFormData.append('image_mapping_json', JSON.stringify(imageMapping));
                    parseFormData.append('generate_answers', generateAnswers ? "true" : "false");

                    return fetch('/api/ai/parse-paper', {
                        method: 'POST',
                        headers: {
                            'X-Local-Token': localStorage.getItem('local_token') || ''
                        },
                        body: parseFormData
                    });
                })
                .then(r => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration) || !r) return null;
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration) || !data) return;
                    if (data.status === 'success') {
                        replaceParsedQuestions(data.questions);
                        applyUnifiedGradeToParsed();
                        appendImportLog(`试卷成功拆解完成！共提取出 ${parsedQuestionsData.length} 道高定数学题。`, 'success');
                        const texDiagnostics = data.tex_diagnostics || {};
                        const estimatedCount = texDiagnostics.question_count_estimate || 0;
                        const actualCount = texDiagnostics.question_count_actual || parsedQuestionsData.length;
                        if (estimatedCount) {
                            appendImportLog(`TeX 题数核对：源码约 ${estimatedCount} 题，实际拆分 ${actualCount} 题。`, estimatedCount === actualCount ? 'info' : 'warning');
                        }
                        if (texDiagnostics.math_locks_created) {
                            appendImportLog(`TeX 公式保真校验：${texDiagnostics.math_locks_restored || 0}/${texDiagnostics.math_locks_created} 个公式已按原源码恢复` + (texDiagnostics.math_locks_implicit ? `，另有 ${texDiagnostics.math_locks_implicit} 个模型已原样带回` : '') + '。', 'info');
                        }
                        const texWarnings = Array.isArray(texDiagnostics.warnings) ? texDiagnostics.warnings : [];
                        texWarnings.forEach(message => appendImportLog(`TeX 预检：${message}`, 'warning'));
                        if (texWarnings.length) {
                            showToast(`TeX 拆分完成，但有 ${texWarnings.length} 项结构提示需要核对。`, 'warning');
                        }
                        
                        renderParsedQuestionsList(parsedQuestionsData);
                        
                        document.getElementById('importLoadingState').classList.add('hidden');
                        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');
                        // auto-collapse input pane after parse
                        setTimeout(function(){
                            var pane = document.getElementById('importInputPane');
                            if(pane && pane.dataset.collapsed !== '1' && typeof window.toggleImportInputPane === 'function'){
                                window.toggleImportInputPane();
                            }
                        }, 300);

                        if (generateAnswers) {
                            processAsyncAnswerGeneration(parsedQuestionsData, parsedQuestionsGeneration);
                        }
                    } else {
                        throw new Error(data.message || '拆解失败');
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    console.error(err);
                    appendImportLog(`拆解出错: ${err.message}`, 'error');

                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = '试卷拆解中断！';

                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');

                    showToast(`试卷拆解失败: ${err.message}`, 'error');
                })
                .finally(() => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
        }

        let documentImportTaskGeneration = 0;
        let activeDocumentPoll = null;

        function stopCurrentDocumentPoll() {
            if (activeDocumentPoll?.intervalId) clearInterval(activeDocumentPoll.intervalId);
            activeDocumentPoll = null;
        }

        function beginDocumentImportTask() {
            documentImportTaskGeneration += 1;
            stopCurrentDocumentPoll();
            window.currentPdfTaskId = null;
            return documentImportTaskGeneration;
        }

        function isCurrentDocumentImportTask(generation) {
            return generation === documentImportTaskGeneration;
        }

        function isCurrentDocumentPoll(identity) {
            return activeDocumentPoll === identity &&
                isCurrentDocumentImportTask(identity.generation) &&
                window.currentPdfTaskId === identity.taskId;
        }

        function finishDocumentPoll(identity) {
            if (!isCurrentDocumentPoll(identity)) return false;
            clearInterval(identity.intervalId);
            activeDocumentPoll = null;
            return true;
        }

        // 上一次 PDF 解析任务的任务 ID，持久化到 localStorage。用途：当页面在
        // 解析过程中刷新、或前端渲染异常导致结果未能呈现时，可以“找回上次解析”
        // 直接重新拉取已完成的拆解结果，避免再次把整份试卷送进 MinerU 花时间。
        const LAST_PDF_TASK_KEY = 'mathbank_last_pdf_task';
        const LAST_PDF_TASK_TTL_MS = 24 * 60 * 60 * 1000;

        function rememberPdfTask(taskId) {
            try {
                localStorage.setItem(LAST_PDF_TASK_KEY, JSON.stringify({ id: taskId, at: Date.now() }));
            } catch (e) { /* 隐私模式等场景忽略 */ }
        }

        function readRememberedPdfTask() {
            try {
                const raw = localStorage.getItem(LAST_PDF_TASK_KEY);
                if (!raw) return null;
                const parsed = JSON.parse(raw);
                if (!parsed || !parsed.id) return null;
                if (parsed.at && Date.now() - parsed.at > LAST_PDF_TASK_TTL_MS) return null;
                return parsed;
            } catch (e) {
                return null;
            }
        }

        function refreshPdfTaskRecoveryButton() {
            const btn = document.getElementById('recoverPdfTaskBtn');
            if (!btn) return;
            // 始终保持入口可见：即使 localStorage 与服务端都没有记录，用户仍可
            // 手动粘贴任务 ID 找回（例如这次卡住的任务）。
            btn.classList.remove('hidden');
            if (readRememberedPdfTask()) return;
            // 服务端最近一条已完成任务，可一键找回而无需手动粘贴 ID。
            fetch('/api/tasks/latest?document_type=pdf')
                .then(r => (r.ok ? r.json() : null))
                .then(task => {
                    if (task && task.task_id) {
                        btn.dataset.latestTaskId = task.task_id;
                    }
                })
                .catch(() => { /* 老服务端没有该接口时忽略 */ });
        }

        // 界面兜底：无论渲染成功还是异常，都必须把加载态收起来，绝不让
        // “正在拆解”的转圈永久停在那里。
        function revealParsedQuestionsWrapper() {
            const loadingState = document.getElementById('importLoadingState');
            if (loadingState) loadingState.classList.add('hidden');
            const wrapper = document.getElementById('parsedQuestionsWrapper');
            if (wrapper) wrapper.classList.remove('hidden');
            const runBtn = document.getElementById('runParseBtn');
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
            }
        }

        // 从已完成任务恢复结果：不重新上传、不调用 MinerU，仅重新拉取任务快照。
        async function recoverLastPdfTask(taskIdOverride) {
            let taskId = (taskIdOverride && String(taskIdOverride).trim()) || null;
            const remembered = readRememberedPdfTask();
            if (remembered && remembered.id) {
                taskId = remembered.id;
            } else {
                const btn = document.getElementById('recoverPdfTaskBtn');
                if (btn && btn.dataset.latestTaskId) {
                    taskId = btn.dataset.latestTaskId;
                }
            }
            if (!taskId) {
                try {
                    const resp = await fetch('/api/tasks/latest?document_type=pdf');
                    if (resp.ok) {
                        const task = await resp.json();
                        if (task && task.task_id) taskId = task.task_id;
                    }
                } catch (e) { /* 忽略，走下面的手动输入兜底 */ }
            }
            if (!taskId && typeof window.prompt === 'function') {
                const manual = window.prompt('请输入要找回的任务 ID（可在解析日志中复制）：', '');
                if (manual && manual.trim()) taskId = manual.trim();
            }
            if (!taskId) {
                if (typeof showToast === 'function') showToast('没有可找回的历史解析任务。', 'info');
                refreshPdfTaskRecoveryButton();
                return;
            }

            const generation = beginDocumentImportTask();
            const loadingState = document.getElementById('importLoadingState');
            if (loadingState) loadingState.classList.remove('hidden');
            const placeholder = document.getElementById('importPlaceholder');
            if (placeholder) placeholder.classList.add('hidden');
            const wrapper = document.getElementById('parsedQuestionsWrapper');
            if (wrapper) wrapper.classList.add('hidden');
            const runBtn = document.getElementById('runParseBtn');
            if (runBtn) {
                runBtn.disabled = true;
                runBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> <span>正在找回上次解析结果...</span>';
            }
            appendImportLog('正在找回上一次已完成的解析任务，不会重新调用 MinerU...', 'current');
            pollPdfTaskStatus(taskId, generation);
        }
        window.recoverLastPdfTask = recoverLastPdfTask;

        function cancelCurrentImportTask() {
            const taskId = window.currentPdfTaskId;
            beginDocumentImportTask();
            if (taskId) {
                fetch(`/api/tasks/${taskId}/cancel`, {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    }
                }).catch(() => {});
            }
            
            appendImportLog('[USER] 用户已手动中止当前拆分任务。', 'info');
            
            const loadingState = document.getElementById('importLoadingState');
            if (loadingState) {
                loadingState.classList.add('hidden');
            }
            
            const runBtn = document.getElementById('runParseBtn');
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
            }
            
            if (typeof showToast === 'function') {
                showToast('已为您安全中止当前拆分流程', 'info');
            }
        }
        window.cancelCurrentImportTask = cancelCurrentImportTask;

        // 全局监听 ESC 键中止拆分流程
        window.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' || e.keyCode === 27) {
                const loadingState = document.getElementById('importLoadingState');
                if (loadingState && !loadingState.classList.contains('hidden')) {
                    cancelCurrentImportTask();
                }
            }
        });

        function pollPdfTaskStatus(taskId, generation) {
            if (!isCurrentDocumentImportTask(generation)) return;
            let lastLog = '';
            const runBtn = document.getElementById('runParseBtn');

            stopCurrentDocumentPoll();
            window.currentPdfTaskId = taskId;
            rememberPdfTask(taskId);
            refreshPdfTaskRecoveryButton();
            const identity = { generation, taskId, intervalId: null };
            activeDocumentPoll = identity;
            identity.intervalId = setInterval(() => {
                fetch(`/api/tasks/${taskId}/status`)
                .then(r => {
                    if (!r.ok) throw new Error("获取任务进度失败");
                    return r.json();
                })
                .then(task => {
                    if (!isCurrentDocumentPoll(identity)) return;
                    if (task.progress !== undefined) {
                        document.getElementById('importProgressBar').style.width = `${task.progress}%`;
                    }
                    
                    if (task.log && task.log !== lastLog) {
                        lastLog = task.log;
                        appendImportLog(task.log, 'current');
                        document.getElementById('importLoadingText').textContent = task.log;
                        
                        const subText = document.getElementById('importSubLoadingText');
                        if (subText) {
                            if (task.status === 'extracting_docx' || (task.log && task.log.includes('OMML'))) {
                                subText.textContent = '正在安全提取 OMML 公式与高清配图，不可靠的公式将保留预览图...';
                            } else if (task.status === 'ocr_extraction' || (task.log && task.log.includes('多模态'))) {
                                subText.textContent = '正在通过多模态视觉引擎并行转译图文与公式，请稍候...';
                            } else if (task.status === 'ai_splitting' || (task.log && task.log.includes('大模型') || task.log.includes('pdf-inspector') || task.log.includes('Word 原生'))) {
                                subText.textContent = '文本与公式已提取完毕，正在通过大模型进行题目切片与属性匹配...';
                            } else if (task.status === 'completed') {
                                subText.textContent = '拆解完成，正在呈现题目审查列表...';
                            }
                        }
                    }
                    
                    if (task.page_images && task.page_images.length > 0) {
                        window.pdfPageImages = task.page_images;
                    }

                    // 任务的临时资产清单：审图面板靠它列出「未归属图」
                    // （已裁剪但没有任何题目引用到的图）。缺失时保持空数组，
                    // 面板会自己提示「本任务没有可用的原页/资产清单」。
                    if (Array.isArray(task.temp_assets) && task.temp_assets.length > 0) {
                        window.pdfTempAssets = task.temp_assets;
                    }
                    
                    if (task.status === 'completed') {
                        if (!finishDocumentPoll(identity)) return;
                        // 先收起加载态并显示结果容器，再做任何后续处理。这样即使
                        // 日志或渲染抛异常，也不会把“正在拆解”的转圈永久卡住。
                        revealParsedQuestionsWrapper();
                        replaceParsedQuestions(task.data || []);
                        applyUnifiedGradeToParsed();
                        const isWordTask = task.document_type === 'docx';
                        const documentLabel = isWordTask ? 'Word' : 'PDF';
                        appendImportLog(`${documentLabel} 试卷分析并拆解成功！共分析出 ${parsedQuestionsData.length} 道数学题。`, 'success');
                        if (isWordTask && task.diagnostics) {
                            const report = task.diagnostics;
                            const converted = (report.omml_converted || 0) + (report.mtef_converted || 0);
                            const reviewCount = report.review_required || 0;
                            appendImportLog(`Word 提取报告：${converted} 个公式已转换，${report.images_extracted || 0} 张图片已保留，${reviewCount} 处需人工核对。`, reviewCount > 0 ? 'warning' : 'info');
                            const structuralMathType = report.mtef_structural_converted || 0;
                            const annotatedMathType = report.mtef_annotation_converted || 0;
                            const compatibleMathType = report.mtef_compatibility_converted || 0;
                            if (structuralMathType || annotatedMathType || compatibleMathType) {
                                appendImportLog(`MathType 明细：${structuralMathType} 个按公式结构转换，${annotatedMathType} 个使用内嵌 LaTeX，${compatibleMathType} 个使用有限文本兼容。`, compatibleMathType > 0 ? 'warning' : 'info');
                            }
                            const restoredNumbers = report.numbering_converted || 0;
                            const restoredFormatting = (report.superscripts_converted || 0)
                                + (report.subscripts_converted || 0)
                                + (report.underlines_converted || 0)
                                + (report.text_styles_converted || 0);
                            if (restoredNumbers || restoredFormatting) {
                                appendImportLog(`Word 排版语义：已恢复 ${restoredNumbers} 个自动编号、${restoredFormatting} 处上下标/下划线/强调格式。`, 'info');
                            }
                            const lockedMath = report.math_locks_created || 0;
                            if (lockedMath) {
                                appendImportLog(`公式保真校验：${(report.math_locks_restored || 0) + (report.math_locks_implicit || 0)}/${lockedMath} 个公式与 Word 原文一致` + (report.math_locks_implicit ? `（其中 ${report.math_locks_implicit} 个模型已原样带回，未走定位标记）` : '') + '，拆卷模型未改写最终公式。', 'info');
                            }
                            if (reviewCount > 0) {
                                showToast(`Word 中有 ${reviewCount} 处公式、字符、图片或表格需人工核对，已保留提示标记。`, 'warning');
                            }
                        }
                        
                        try {
                            renderParsedQuestionsList(parsedQuestionsData);
                        } catch (renderError) {
                            console.error('渲染解析结果失败:', renderError);
                            if (typeof showToast === 'function') {
                                showToast(`结果已取回，但渲染题目卡片时出错：${renderError && renderError.message ? renderError.message : renderError}`, 'warning');
                            }
                        }
                    } else if (task.status === 'cancelled') {
                        if (!finishDocumentPoll(identity)) return;
                        revealParsedQuestionsWrapper();
                    } else if (task.status === 'error') {
                        if (!finishDocumentPoll(identity)) return;
                        appendImportLog(`分析失败: ${task.error || '未知错误'}`, 'error');
                        
                        const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                        if (loadingIcon) {
                            loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                            loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                        }
                        const documentLabel = task.document_type === 'docx' ? 'Word' : 'PDF';
                        document.getElementById('importLoadingText').textContent = `${documentLabel} 试卷分析中断！`;

                        const loadingState = document.getElementById('importLoadingState');
                        let resetBtn = document.getElementById('resetImportBtn');
                        if (!resetBtn) {
                            resetBtn = document.createElement('button');
                            resetBtn.id = 'resetImportBtn';
                            resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                            resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                            resetBtn.onclick = resetImportState;
                            loadingState.appendChild(resetBtn);
                        }
                        resetBtn.classList.remove('hidden');
                        
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                        showToast(`${documentLabel} 拆解分析失败: ${task.error || '未知错误'}`, 'error');
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentPoll(identity)) return;
                    console.error('轮询任务状态失败:', err);
                    // 网络抖动或响应异常时不要无休止地空转，收起重试并让用户
                    // 可以用“找回上次解析”重新拉取已完成的结果。
                    finishDocumentPoll(identity);
                    const loadingState = document.getElementById('importLoadingState');
                    if (loadingState) loadingState.classList.add('hidden');
                    const placeholder = document.getElementById('importPlaceholder');
                    if (placeholder) placeholder.classList.remove('hidden');
                    if (typeof appendImportLog === 'function') {
                        appendImportLog(`获取解析进度失败：${err && err.message ? err.message : err}`, 'error');
                    }
                    if (typeof showToast === 'function') {
                        showToast('获取解析进度失败，可点击“找回上次解析”重新取回已完成的结果。', 'warning');
                    }
                });
            }, 1500);
        }

        function renderImagesList() {
            const imagesListContainer = document.getElementById('importImagesList');
            const imagesCountName = document.getElementById('imagesCountName');
            const imagesFileIcon = document.getElementById('imagesFileIcon');
            
            if (!imagesListContainer || !imagesCountName || !imagesFileIcon) return;

            imagesListContainer.innerHTML = '';
            if (batchSelectedImages.length === 0) {
                imagesListContainer.classList.add('hidden');
                imagesCountName.textContent = "点击或多选拖入试卷引用的所有图片";
                imagesCountName.className = "text-xs text-slate-600 font-medium";
                imagesFileIcon.className = "fa-solid fa-images text-slate-400 text-xl mb-1.5";
                return;
            }

            imagesListContainer.classList.remove('hidden');
            imagesCountName.textContent = `已选择 ${batchSelectedImages.length} 张图片`;
            imagesCountName.className = "text-xs text-brand-600 font-bold";
            imagesFileIcon.className = "fa-solid fa-images text-brand-500 text-xl mb-1.5 animate-pulse";

            batchSelectedImages.forEach((file, index) => {
                const item = document.createElement('div');
                item.className = "relative group flex items-center justify-between bg-white border border-slate-200 rounded-lg px-2 py-0.5 text-[10px] text-slate-600 space-x-1.5 shrink-0 max-w-[140px]";
                const name = document.createElement('span');
                name.className = 'truncate font-semibold max-w-[90px]';
                name.textContent = file.name;
                name.dataset.tooltip = file.name;
                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'text-slate-400 hover:text-red-500 transition-colors';
                removeButton.dataset.tooltip = '移除';
                removeButton.setAttribute('aria-label', `移除图片 ${file.name}`);
                const icon = document.createElement('i');
                icon.className = 'fa-solid fa-circle-xmark';
                removeButton.appendChild(icon);
                item.append(name, removeButton);
                removeButton.addEventListener('click', (e) => {
                    e.stopPropagation();
                    batchSelectedImages.splice(index, 1);
                    renderImagesList();
                });
                imagesListContainer.appendChild(item);
            });
        }

        function clearAllImportInputs() {
            if (blockImportResetWhileSaving()) {
                return false;
            }
            // 清空左侧输入栏
            const titleInput = document.getElementById('importPaperTitle');
            if (titleInput) titleInput.value = '';

            const latexTextarea = document.getElementById('importLatexContent');
            if (latexTextarea) {
                latexTextarea.value = '';
                latexTextarea.disabled = false;
            }

            const texFileInput = document.getElementById('texFileInput');
            if (texFileInput) texFileInput.value = '';

            const imagesFileInput = document.getElementById('imagesFileInput');
            if (imagesFileInput) imagesFileInput.value = '';

            // 重置 .tex 拖拽显示样式
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            if (texFileName) {
                texFileName.textContent = "点击或拖放 .tex / .pdf / .docx 试卷文件";
                texFileName.className = "text-xs text-slate-600 font-medium";
            }
            if (texFileIcon) {
                texFileIcon.className = "fa-solid fa-file-pdf text-slate-400 text-xl mb-1.5";
            }

            // 清空批量配图
            batchSelectedImages = [];
            // 重置图片展示列表与状态
            renderImagesList();
            
            // 重置 PDF 状态
            window.currentPdfFile = null;
            window.currentDocxFile = null;
            window.currentTexDiagnostics = null;
            window.currentTexReadToken = null;
            window.pdfPageImages = [];
            window.pdfTempAssets = [];
            window.currentPdfTaskId = null;
            window.activeCropQuestionIndex = null;
            window.tempCroppedPathsThisSession = [];
            
            const pdfRange = document.getElementById('pdfPageRange');
            if (pdfRange) pdfRange.value = '';
            const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
            if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
            const texImagesSection = document.getElementById('texImagesSection');
            if (texImagesSection) texImagesSection.classList.remove('hidden');
        }

        function resetImportState(showToastMessage = true) {
            if (blockImportResetWhileSaving()) {
                return false;
            }
            beginDocumentImportTask();
            // 隐藏加载状态和结果视图
            document.getElementById('importLoadingState').classList.add('hidden');
            document.getElementById('parsedQuestionsWrapper').classList.add('hidden');

            // 显示占位视图
            document.getElementById('importPlaceholder').classList.remove('hidden');

            // 恢复加载状态的原始图标
            const loadingIcon = document.querySelector('#importLoadingState .fa-circle-notch, #importLoadingState .fa-spinner, #importLoadingState .fa-circle-exclamation');
            if (loadingIcon) {
                loadingIcon.className = 'fa-solid fa-circle-notch fa-spin text-brand-600 text-3xl inline-block';
            }

            // 重置加载文本
            document.getElementById('importLoadingText').textContent = '正在整理插图映射并预备上传...';

            // 隐藏重置按钮
            const resetBtn = document.getElementById('resetImportBtn');
            if (resetBtn) {
                resetBtn.classList.add('hidden');
            }

            // 清空日志控制台
            const consoleDiv = document.getElementById('importLogsConsole');
            consoleDiv.innerHTML = '<div>[SYSTEM] 准备就绪，等待上传图片...</div>';

            // 重置按钮状态
            const runBtn = document.getElementById('runParseBtn');
            runBtn.disabled = false;
            runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';

            // 清空解析结果数据
            replaceParsedQuestions([]);
            if (typeof updateSelectedCount === 'function') {
                updateSelectedCount();
            }

            // 清空右侧解析题目卡片 DOM
            const container = document.getElementById('parsedCardsContainer');
            if (container) {
                container.innerHTML = '';
            }
            const countBadge = document.getElementById('parsedCountBadge');
            if (countBadge) {
                countBadge.textContent = '共 0 题';
            }

            const shouldShow = (showToastMessage === true || typeof showToastMessage !== 'boolean');
            if (shouldShow) {
                showToast('已重置，可以重新开始拆解', 'success');
            }
        }

        function appendSafeImageBadge(container, imagePath) {
            if (!container) return;
            const safePath = window.MathBankSafe.safeImageUrl(imagePath);
            if (!safePath) return;

            let filename = safePath.split('/').pop() || '题目配图';
            try {
                filename = decodeURIComponent(filename.split('?')[0]);
            } catch (error) { }
            filename = window.MathBankSafe.sanitizePlainText(filename);

            const badge = document.createElement('div');
            badge.className = 'flex items-center space-x-1 px-2 py-0.5 bg-slate-100 border rounded-full text-[9px] font-semibold text-slate-500 hover:bg-white transition-colors cursor-pointer select-none';
            const icon = document.createElement('i');
            icon.className = 'fa-solid fa-image text-slate-400';
            const label = document.createElement('span');
            label.className = 'truncate max-w-[80px]';
            label.title = filename;
            label.textContent = filename;
            badge.append(icon, label);
            container.appendChild(badge);
        }

        function safeDuplicateImagePaths(paths) {
            if (!Array.isArray(paths)) return [];
            return Array.from(new Set(
                paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
            ));
        }

        function safeDuplicateTikzAssets(assets) {
            if (!Array.isArray(assets)) return [];
            return assets.map(asset => {
                if (!asset || typeof asset !== 'object') return null;
                return {
                    id: String(asset.id || ''),
                    tikz_code: String(asset.tikz_code || ''),
                    image_path: window.MathBankSafe.safeImageUrl(asset.image_path) || '',
                    reference_image_path: window.MathBankSafe.safeImageUrl(asset.reference_image_path) || ''
                };
            }).filter(Boolean);
        }

        function safePersistedTikzAssets(assets) {
            if (!Array.isArray(assets)) return [];
            return assets.map(asset => {
                if (!asset || typeof asset !== 'object') return null;
                return {
                    id: String(asset.id || ''),
                    tikz_code: String(asset.tikz_code || ''),
                    instruction: String(asset.instruction || ''),
                    image_path: window.MathBankSafe.safeImageUrl(asset.image_path) || '',
                    reference_image_path: window.MathBankSafe.safeImageUrl(asset.reference_image_path) || ''
                };
            }).filter(Boolean);
        }

        function collectTikzAssetImagePaths(...assetGroups) {
            const paths = [];
            assetGroups.forEach(assets => {
                if (!Array.isArray(assets)) return;
                assets.forEach(asset => {
                    if (!asset || typeof asset !== 'object') return;
                    paths.push(asset.image_path, asset.reference_image_path);
                });
            });
            return safeDuplicateImagePaths(paths);
        }

        function serializeQuestionDuplicateItem(item) {
            return JSON.stringify({
                content: String(item && item.content || ''),
                answer_markdown: String(item && item.answer_markdown || ''),
                question_type: String(item && item.question_type || ''),
                image_paths: Array.isArray(item && item.image_paths) ? item.image_paths : [],
                content_tikz_assets: Array.isArray(item && item.content_tikz_assets)
                    ? item.content_tikz_assets
                    : [],
                answer_tikz_assets: Array.isArray(item && item.answer_tikz_assets)
                    ? item.answer_tikz_assets
                    : [],
                tikz_code: String(item && item.tikz_code || ''),
                tikz_reference_image_path: String(item && item.tikz_reference_image_path || ''),
                exclude_id: item && item.exclude_id ? Number(item.exclude_id) : null
            });
        }

        function buildParsedQuestionDuplicateItem(index, generation = parsedQuestionsGeneration) {
            const q = parsedQuestionsData[index];
            const card = document.getElementById(`parsed-card-${index}`);
            if (!q || !card) return null;
            const contentInput = card.querySelector('.card-content-textarea');
            const answerInput = card.querySelector('.card-answer-textarea');
            const typeInput = card.querySelector('.card-qtype');
            const contentAssets = safeDuplicateTikzAssets(q.content_tikz_assets);
            const answerAssets = safeDuplicateTikzAssets(q.answer_tikz_assets);
            const legacyReference = window.MathBankSafe.safeImageUrl(q.tikz_reference_image_path) || '';
            return {
                client_key: `parsed-${generation}-${index}`,
                content: contentInput ? contentInput.value : String(q.content || ''),
                answer_markdown: answerInput ? answerInput.value : String(q.answer_markdown || ''),
                question_type: typeInput ? typeInput.value : String(q.question_type || ''),
                image_paths: safeDuplicateImagePaths([
                    ...(Array.isArray(q.image_paths) ? q.image_paths : []),
                    ...collectTikzAssetImagePaths(contentAssets, answerAssets),
                    legacyReference
                ]),
                content_tikz_assets: contentAssets,
                answer_tikz_assets: answerAssets,
                tikz_code: String(q.tikz_code || ''),
                tikz_reference_image_path: legacyReference,
                // Parsed paper items are always new drafts.  An AI-provided
                // numeric `id` may be a paper question number, never a local DB id.
                exclude_id: null
            };
        }

        function buildEditorQuestionDuplicateItem(editorSession) {
            const contentInput = document.getElementById('editContent');
            const answerInput = document.getElementById('editAnswerMarkdown');
            const typeInput = document.getElementById('editQType');
            const contentAssets = typeof TikzState !== 'undefined' && Array.isArray(TikzState.contentAssets)
                ? TikzState.contentAssets
                : [];
            const answerAssets = typeof TikzState !== 'undefined' && Array.isArray(TikzState.answerAssets)
                ? TikzState.answerAssets
                : [];
            return {
                client_key: 'editor-question',
                content: contentInput ? contentInput.value : '',
                answer_markdown: answerInput ? answerInput.value : '',
                question_type: typeInput ? typeInput.value : '',
                image_paths: safeDuplicateImagePaths([
                    ...(typeof uploadedImages !== 'undefined' ? uploadedImages : []),
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : []),
                    ...(typeof TikzState !== 'undefined' ? TikzState.referencePaths() : []),
                    ...collectTikzAssetImagePaths(contentAssets, answerAssets)
                ]),
                content_tikz_assets: safeDuplicateTikzAssets(contentAssets),
                answer_tikz_assets: safeDuplicateTikzAssets(
                    answerAssets
                ),
                tikz_code: contentAssets[0] ? String(contentAssets[0].tikz_code || '') : '',
                tikz_reference_image_path: contentAssets[0]
                    ? (window.MathBankSafe.safeImageUrl(contentAssets[0].reference_image_path) || '')
                    : '',
                exclude_id: editorSession && editorSession.questionId ? editorSession.questionId : null
            };
        }

        async function requestQuestionDuplicateCheck(items) {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 12000);
            let response;
            try {
                response = await fetch('/api/questions/check-duplicates', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: JSON.stringify({ items: items, max_candidates: 5 }),
                    signal: controller.signal
                });
            } catch (error) {
                if (error && error.name === 'AbortError') {
                    throw new Error('查重超时（12 秒）');
                }
                throw error;
            } finally {
                clearTimeout(timeoutId);
            }
            if (response.ok === false) {
                let message = `HTTP ${response.status}`;
                try {
                    const errorData = await response.json();
                    message = errorData.detail || errorData.message || message;
                } catch (error) { }
                const requestError = new Error(message);
                requestError.httpStatus = response.status;
                requestError.isValidationError = response.status >= 400 && response.status < 500;
                throw requestError;
            }
            const data = await response.json();
            if (!data || (data.status && data.status !== 'success')) {
                throw new Error((data && (data.detail || data.message)) || '查重服务返回了无效结果');
            }
            if (!Array.isArray(data.items)) {
                throw new Error('查重服务未返回题目结果');
            }
            return data;
        }

        function duplicateBatchMatchCount(result) {
            if (!result) return 0;
            if (Array.isArray(result.batch_matches)) return result.batch_matches.length;
            const count = Number(result.batch_matches || 0);
            return Number.isFinite(count) ? Math.max(0, count) : 0;
        }

        function duplicateResultHasSignal(result) {
            if (!result) return false;
            const level = String(result.level || '').toLowerCase();
            const clearLevels = new Set(['', 'none', 'clear', 'unique', 'no_match']);
            return !clearLevels.has(level) ||
                (Array.isArray(result.candidates) && result.candidates.length > 0) ||
                duplicateBatchMatchCount(result) > 0 ||
                result.needs_visual_review === true;
        }

        function duplicateResultIsExact(result) {
            const level = String(result && result.level || '').toLowerCase();
            return level.includes('exact') || level === 'duplicate' || level === 'same';
        }

        function duplicateResultBadge(result) {
            if (!duplicateResultHasSignal(result)) {
                return {
                    text: '未发现重复',
                    className: 'card-duplicate-badge hidden min-h-11 px-3 text-[9px] font-semibold rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400'
                };
            }
            if (duplicateResultIsExact(result)) {
                return {
                    text: '疑似已收录',
                    className: 'card-duplicate-badge min-h-11 px-3 text-[9px] font-semibold rounded border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400 dark:border-rose-500/40 dark:bg-rose-500/15 dark:text-rose-200 dark:hover:bg-rose-500/25'
                };
            }
            if (result && result.needs_visual_review) {
                return {
                    text: '配图待核对',
                    className: 'card-duplicate-badge min-h-11 px-3 text-[9px] font-semibold rounded border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200 dark:hover:bg-amber-500/25'
                };
            }
            return {
                text: '可能重复',
                className: 'card-duplicate-badge min-h-11 px-3 text-[9px] font-semibold rounded border border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-200 dark:hover:bg-amber-500/25'
            };
        }

        function updateParsedDuplicateBadge(index) {
            const card = document.getElementById(`parsed-card-${index}`);
            if (!card) return;
            const badge = card.querySelector('.card-duplicate-badge');
            if (!badge) return;
            const result = parsedDuplicateResults.get(index);
            if (!result) {
                const isChecking = parsedDuplicateCheckStatus === 'checking';
                badge.textContent = isChecking ? '查重中' : '';
                badge.className = isChecking
                    ? 'card-duplicate-badge min-h-11 px-3 text-[9px] font-semibold rounded border border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-300'
                    : 'card-duplicate-badge hidden min-h-11 px-3 text-[9px] font-semibold rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400';
                badge.disabled = isChecking;
                badge.setAttribute('aria-busy', isChecking ? 'true' : 'false');
                badge.setAttribute('aria-label', isChecking ? '正在查重' : '');
                return;
            }
            const presentation = duplicateResultBadge(result);
            badge.textContent = presentation.text;
            badge.className = presentation.className;
            badge.disabled = false;
            badge.setAttribute('aria-busy', 'false');
            badge.setAttribute('aria-label', `${presentation.text}，点击查看查重依据`);
        }

        function updateParsedDuplicateSummary() {
            const summary = document.getElementById('parsedDuplicateSummary');
            const textNode = document.getElementById('parsedDuplicateSummaryText');
            const icon = document.getElementById('parsedDuplicateSummaryIcon');
            if (!summary || !textNode || !icon) return;

            if (!parsedDuplicateSummaryVisible) {
                summary.classList.add('hidden');
                summary.classList.remove('flex');
                return;
            }

            summary.classList.remove('hidden');
            summary.classList.add('flex');
            if (parsedQuestionsData.length === 0) {
                summary.classList.add('hidden');
                summary.classList.remove('flex');
                return;
            }
            if (parsedDuplicateCheckStatus === 'checking') {
                textNode.textContent = '正在为本批题目查重…';
                icon.className = 'fa-solid fa-spinner animate-spin text-slate-500';
                return;
            }
            if (parsedDuplicateCheckStatus === 'error') {
                textNode.textContent = '本次查重未完成；如继续保存，请稍后人工核对。';
                icon.className = 'fa-solid fa-triangle-exclamation text-amber-600';
                return;
            }
            if (parsedDuplicateCheckStatus === 'idle' || parsedDuplicateCheckStatus === 'stale') {
                textNode.textContent = parsedDuplicateCheckStatus === 'stale'
                    ? '题目已编辑，查重结果已失效；导入前将重新检查。'
                    : '等待查重。';
                icon.className = 'fa-solid fa-magnifying-glass text-slate-500';
                return;
            }

            let exactCount = 0;
            let possibleCount = 0;
            let clearCount = 0;
            parsedDuplicateResults.forEach(result => {
                if (!duplicateResultHasSignal(result)) clearCount += 1;
                else if (duplicateResultIsExact(result)) exactCount += 1;
                else possibleCount += 1;
            });
            let coverageNote = '';
            let completionLabel = '查重完成';
            let clearLabel = '未发现重复';
            if (parsedDuplicateIndexStatus && parsedDuplicateIndexStatus.ready === false) {
                const coverage = Number(parsedDuplicateIndexStatus.coverage || 0);
                const warning = String(parsedDuplicateIndexStatus.warning || '');
                const coveragePercent = Math.min(99, Math.floor(coverage * (coverage <= 1 ? 100 : 1)));
                coverageNote = warning
                    ? ` · ${warning}`
                    : ` · 索引覆盖 ${coveragePercent}%`;
                completionLabel = '查重完成（结果可能不完整）';
                clearLabel = '当前覆盖内未发现重复';
            }
            textNode.textContent = `${completionLabel}：疑似已收录 ${exactCount} 题，可能重复 ${possibleCount} 题，${clearLabel} ${clearCount} 题${coverageNote}`;
            icon.className = exactCount || possibleCount
                ? 'fa-solid fa-code-compare text-amber-600'
                : 'fa-solid fa-circle-check text-emerald-600';
        }

        function resetParsedDuplicateCheckState() {
            parsedDuplicateCheckSerial += 1;
            parsedDuplicateResults.clear();
            parsedDuplicateSnapshots.clear();
            parsedDuplicateCheckStatus = 'idle';
            parsedDuplicateIndexStatus = null;
            parsedDuplicateSummaryVisible = false;
            parsedDuplicateActiveIndices = [];
            const summary = document.getElementById('parsedDuplicateSummary');
            if (summary) {
                summary.classList.add('hidden');
                summary.classList.remove('flex');
            }
        }

        function invalidateParsedDuplicateCheck(index) {
            const hadVisibleCheck = parsedDuplicateCheckStatus !== 'idle' ||
                parsedDuplicateResults.size > 0 || parsedDuplicateSnapshots.size > 0;
            if (!hadVisibleCheck) return;
            parsedDuplicateCheckSerial += 1;
            parsedDuplicateResults.delete(index);
            parsedDuplicateSnapshots.delete(index);
            parsedDuplicateCheckStatus = 'stale';
            const affectedIndices = parsedDuplicateActiveIndices.length
                ? parsedDuplicateActiveIndices.slice()
                : [index];
            parsedDuplicateActiveIndices = [];
            affectedIndices.forEach(updateParsedDuplicateBadge);
            updateParsedDuplicateSummary();
        }

        async function precheckParsedQuestionDuplicates(options = {}) {
            const generation = parsedQuestionsGeneration;
            const showSummary = options.showSummary !== false;
            const indices = Array.isArray(options.indices)
                ? options.indices.slice()
                : parsedQuestionsData.map((q, index) => q && !q.saved ? index : -1).filter(index => index >= 0);
            const items = [];
            const localSnapshots = new Map();
            indices.forEach(index => {
                const item = buildParsedQuestionDuplicateItem(index, generation);
                if (!item) return;
                items.push(item);
                localSnapshots.set(index, serializeQuestionDuplicateItem(item));
            });
            if (items.length === 0) {
                return { ok: true, generation: generation, results: new Map() };
            }
            if (items.length > 500) {
                const error = new Error('单次最多查重 500 道题，请分批勾选');
                error.isValidationError = true;
                if (options.notifyFailure !== false) showToast(error.message, 'warning');
                return { ok: false, invalid: true, error: error, generation: generation };
            }

            const checkedIndexSet = new Set(indices);
            Array.from(parsedDuplicateResults.keys()).forEach(previousIndex => {
                if (checkedIndexSet.has(previousIndex)) return;
                const previousCard = document.getElementById(`parsed-card-${previousIndex}`);
                const previousBadge = previousCard && previousCard.querySelector('.card-duplicate-badge');
                if (previousBadge) {
                    previousBadge.textContent = '';
                    previousBadge.className = 'card-duplicate-badge hidden min-h-11 px-3 text-[9px] font-semibold rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400';
                    previousBadge.disabled = false;
                    previousBadge.setAttribute('aria-busy', 'false');
                    previousBadge.setAttribute('aria-label', '');
                }
            });
            parsedDuplicateResults.clear();
            parsedDuplicateSnapshots.clear();
            const requestSerial = ++parsedDuplicateCheckSerial;
            parsedDuplicateCheckStatus = 'checking';
            parsedDuplicateSummaryVisible = showSummary;
            parsedDuplicateActiveIndices = indices.slice();
            indices.forEach(updateParsedDuplicateBadge);
            updateParsedDuplicateSummary();

            try {
                const data = await requestQuestionDuplicateCheck(items);
                if (generation !== parsedQuestionsGeneration || requestSerial !== parsedDuplicateCheckSerial) {
                    return { ok: false, stale: true, generation: generation };
                }
                const currentItems = new Map();
                indices.forEach(index => {
                    const current = buildParsedQuestionDuplicateItem(index, generation);
                    if (current) currentItems.set(index, serializeQuestionDuplicateItem(current));
                });
                const snapshotsAreCurrent = indices.every(index =>
                    currentItems.get(index) === localSnapshots.get(index)
                );
                if (!snapshotsAreCurrent) {
                    parsedDuplicateCheckStatus = 'stale';
                    parsedDuplicateActiveIndices = [];
                    indices.forEach(updateParsedDuplicateBadge);
                    if (showSummary) updateParsedDuplicateSummary();
                    return { ok: false, stale: true, generation: generation };
                }

                const responseByKey = new Map(data.items.map(item => [String(item.client_key), item]));
                if (items.some(item => !responseByKey.has(String(item.client_key)))) {
                    throw new Error('查重结果与本批题目无法一一对应');
                }
                const currentResults = new Map();
                items.forEach(item => {
                    const index = Number(String(item.client_key).split('-').pop());
                    const result = responseByKey.get(String(item.client_key));
                    parsedDuplicateResults.set(index, result);
                    parsedDuplicateSnapshots.set(index, localSnapshots.get(index));
                    currentResults.set(index, result);
                    updateParsedDuplicateBadge(index);
                });
                parsedDuplicateCheckStatus = 'ready';
                parsedDuplicateIndexStatus = data.index || null;
                parsedDuplicateActiveIndices = [];
                if (showSummary) updateParsedDuplicateSummary();
                if (!showSummary && data.index && data.index.ready === false) {
                    const coverage = Math.min(99, Math.floor(Number(data.index.coverage || 0) * 100));
                    const warning = String(data.index.warning || '');
                    showToast(warning || `查重索引仍在构建，当前覆盖约 ${coverage}% 题库。`, 'warning');
                }
                return { ok: true, generation: generation, results: currentResults, index: data.index || null };
            } catch (error) {
                if (generation !== parsedQuestionsGeneration || requestSerial !== parsedDuplicateCheckSerial) {
                    return { ok: false, stale: true, generation: generation };
                }
                parsedDuplicateCheckStatus = 'error';
                parsedDuplicateActiveIndices = [];
                indices.forEach(updateParsedDuplicateBadge);
                if (showSummary) updateParsedDuplicateSummary();
                if (options.notifyFailure !== false) {
                    const message = error && error.isValidationError
                        ? `查重请求内容无效：${error.message}。请修正后重试。`
                        : `查重暂不可用：${error.message}。本次仍可继续导入。`;
                    showToast(message, 'warning');
                }
                return {
                    ok: false,
                    invalid: Boolean(error && error.isValidationError),
                    error: error,
                    generation: generation
                };
            }
        }

        function duplicateReasonLabel(reason) {
            const labels = {
                EXACT_CONTENT: '题干规范化后完全一致',
                EXACT_TEXT: '题干规范化后完全一致',
                SAFE_NORMALIZATION_ONLY: '差异仅来自空格或排版写法',
                HIGH_TEXT_SIMILARITY: '题干文本高度相似',
                SAME_MATH_STRUCTURE: '数学结构高度相似',
                SAME_IMAGES: '可见配图一致',
                IMAGE_REVIEW_REQUIRED: '配图仍需人工核对',
                VISUAL_SIGNATURE_PENDING: '配图指纹尚未完整，需人工核对',
                FIGURE_EXACT: '可见配图一致',
                FIGURE_DIFF: '题干相似，但可见配图不同',
                TIKZ_DIFF: '题干相似，但 TikZ 图形不同',
                TYPE_DIFF: '题型不同',
                OPTION_ORDER_CHANGED: '选项顺序已变化',
                CRITICAL_MATH_DIFF: '数字、符号或数学条件不同',
                CRITICAL_MATH_MATCH: '关键数学条件一致',
                NUMBER_CHANGED: '数字或区间端点不同，可能是变式题',
                RELATION_CHANGED: '等号或不等号等关系符不同',
                QUANTIFIER_CHANGED: '任意、存在等量词条件不同',
                VARIABLE_CHANGED: '变量或几何对象名称不同',
                OPERATOR_CHANGED: '运算符或幂、下标结构不同',
                MATH_STRUCTURE_CHANGED: '关键数学结构不同',
                OPTION_COUNT_DIFF: '选项数量不同',
                FIGURE_MISSING: '其中一题缺少可见配图',
                FIGURE_COUNT_DIFF: '可见配图数量不同',
                ANSWER_DIFF: '题干疑似相同，但答案或解析不同',
                BATCH_EXACT: '与本批其他题目完全一致',
                BATCH_SIMILAR: '与本批其他题目高度相似'
            };
            return labels[String(reason || '')] || window.MathBankSafe.sanitizePlainText(String(reason || ''));
        }

        function resetParsedDuplicateCandidateRendering() {
            parsedDuplicateCandidateRenderGeneration += 1;
            if (parsedDuplicateCandidateObserver) {
                parsedDuplicateCandidateObserver.disconnect();
                parsedDuplicateCandidateObserver = null;
            }
            parsedDuplicateCandidateRenderJobs = new WeakMap();
            parsedDuplicateCandidateFallbackQueue = [];
            parsedDuplicateCandidateFallbackFrame = null;
        }

        function drainParsedDuplicateCandidateFallbackQueue(generation) {
            if (generation !== parsedDuplicateCandidateRenderGeneration) return;
            parsedDuplicateCandidateFallbackFrame = null;
            const job = parsedDuplicateCandidateFallbackQueue.shift();
            if (job) job();
            if (parsedDuplicateCandidateFallbackQueue.length > 0) {
                parsedDuplicateCandidateFallbackFrame = window.requestAnimationFrame(() => {
                    drainParsedDuplicateCandidateFallbackQueue(generation);
                });
            }
        }

        function prepareParsedDuplicateCandidateRendering(root) {
            resetParsedDuplicateCandidateRendering();
            if (typeof window.IntersectionObserver !== 'function') return;
            const observer = new window.IntersectionObserver(entries => {
                entries.forEach(entry => {
                    if (!entry.isIntersecting && entry.intersectionRatio <= 0) return;
                    observer.unobserve(entry.target);
                    const job = parsedDuplicateCandidateRenderJobs.get(entry.target);
                    parsedDuplicateCandidateRenderJobs.delete(entry.target);
                    if (job) job();
                });
            }, {
                root: root,
                rootMargin: '120px 0px'
            });
            parsedDuplicateCandidateObserver = observer;
        }

        function scheduleParsedDuplicateCandidateRender(container, text) {
            if (!container) return;
            const generation = parsedDuplicateCandidateRenderGeneration;
            const render = () => {
                if (generation !== parsedDuplicateCandidateRenderGeneration) return;
                try {
                    window.renderQuestionPreviewContent(container, text, { includeImages: false });
                } catch (error) {
                    container.textContent = '题干渲染失败，请稍后重试。';
                } finally {
                    container.setAttribute('aria-busy', 'false');
                }
            };
            if (parsedDuplicateCandidateObserver) {
                parsedDuplicateCandidateRenderJobs.set(container, render);
                parsedDuplicateCandidateObserver.observe(container);
                return;
            }
            parsedDuplicateCandidateFallbackQueue.push(render);
            if (parsedDuplicateCandidateFallbackFrame === null) {
                parsedDuplicateCandidateFallbackFrame = window.requestAnimationFrame(() => {
                    drainParsedDuplicateCandidateFallbackQueue(generation);
                });
            }
        }

        function appendDuplicateCandidate(container, candidate) {
            if (!candidate || typeof candidate !== 'object') return;
            const row = document.createElement('div');
            row.className = 'rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900/80';
            const title = document.createElement('div');
            title.className = 'flex flex-wrap items-center gap-2 text-[10px] font-bold text-slate-600 dark:text-slate-200';
            const seq = candidate.seq_num || candidate.id || '?';
            const source = window.MathBankSafe.sanitizePlainText(String(candidate.source || '来源未标注'));
            title.textContent = `已收录 #${seq} · ${source} · 相似度 ${Math.round(Number(candidate.score || 0) * 100)}%`;
            row.appendChild(title);
            if (candidate.content_truncated) {
                const details = document.createElement('details');
                details.className = 'mt-2 rounded-lg border border-slate-200 p-2 dark:border-slate-700';
                const summary = document.createElement('summary');
                summary.className = 'min-h-11 cursor-pointer py-3 text-[10px] font-bold text-brand-600 dark:text-brand-200';
                summary.textContent = '点击加载并渲染完整题干';
                const fullContent = document.createElement('div');
                fullContent.className = 'mt-2 break-words text-[11px] leading-5 text-slate-700 dark:text-slate-200';
                fullContent.textContent = '展开后加载完整题干…';
                let mathRendered = false;
                details.addEventListener('toggle', async () => {
                    if (!details.open || mathRendered) return;
                    mathRendered = true;
                    try {
                        if (!candidate.id) throw new Error('候选题标识缺失');
                        const response = await fetch(`/api/questions/${Number(candidate.id)}`);
                        if (!response.ok) throw new Error(`HTTP ${response.status}`);
                        const fullQuestion = await response.json();
                        window.renderQuestionPreviewContent(
                            fullContent,
                            String(fullQuestion.content || ''),
                            { includeImages: false }
                        );
                    } catch (error) {
                        mathRendered = false;
                        fullContent.textContent = '完整题干加载失败，请稍后重试。';
                    }
                });
                details.append(summary, fullContent);
                row.appendChild(details);
            } else {
                const renderedContent = document.createElement('div');
                renderedContent.className = 'mt-2 break-words text-[11px] leading-5 text-slate-700 dark:text-slate-200';
                renderedContent.setAttribute('aria-busy', 'true');
                renderedContent.textContent = '进入可见区域后自动渲染题干…';
                scheduleParsedDuplicateCandidateRender(
                    renderedContent,
                    String(candidate.content || '')
                );
                row.appendChild(renderedContent);
            }
            const imagePaths = Array.isArray(candidate.image_paths)
                ? candidate.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                : [];
            if (imagePaths.length) {
                const imageStrip = document.createElement('div');
                imageStrip.className = 'mt-3 flex flex-wrap gap-2';
                imagePaths.slice(0, 4).forEach((path, imageIndex) => {
                    const imageButton = document.createElement('button');
                    imageButton.type = 'button';
                    imageButton.className = 'flex min-h-11 min-w-11 items-center justify-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';
                    imageButton.setAttribute('aria-label', `放大查看已收录候选题配图 ${imageIndex + 1}`);
                    const image = document.createElement('img');
                    image.src = path;
                    image.alt = '已收录候选题配图';
                    image.loading = 'lazy';
                    image.decoding = 'async';
                    image.setAttribute('data-safe-image-open', 'true');
                    image.className = 'h-20 w-24 rounded-lg border border-slate-200 bg-white object-contain dark:border-slate-700 dark:bg-slate-950';
                    imageButton.appendChild(image);
                    imageButton.addEventListener('click', event => {
                        if (event.target === imageButton) image.click();
                    });
                    imageStrip.appendChild(imageButton);
                });
                row.appendChild(imageStrip);
            }
            const reasons = Array.isArray(candidate.reasons) ? candidate.reasons.filter(Boolean) : [];
            if (reasons.length) {
                const reasonText = document.createElement('p');
                reasonText.className = 'mt-2 text-[9px] font-semibold text-amber-700 dark:text-amber-200';
                reasonText.textContent = reasons.map(duplicateReasonLabel).join(' · ');
                row.appendChild(reasonText);
            }
            container.appendChild(row);
        }

        function renderParsedDuplicateReview(entries) {
            const list = document.getElementById('parsedDuplicateReviewList');
            if (!list) return;
            prepareParsedDuplicateCandidateRendering(list);
            list.textContent = '';
            entries.forEach(entry => {
                const section = document.createElement('section');
                section.className = 'rounded-2xl border border-amber-200/80 bg-amber-50/50 p-4 dark:border-amber-500/35 dark:bg-amber-500/10';
                const heading = document.createElement('h4');
                heading.className = 'text-xs font-bold text-slate-800 dark:text-slate-100';
                heading.textContent = entry.label;
                section.appendChild(heading);

                const result = entry.result || {};
                const candidates = Array.isArray(result.candidates) ? result.candidates : [];
                const batchMatches = Array.isArray(result.batch_matches) ? result.batch_matches : [];
                const meta = document.createElement('p');
                meta.className = 'mt-1 text-[10px] leading-5 text-slate-600 dark:text-slate-300';
                const visualNote = result.needs_visual_review ? '；配图需人工核对' : '';
                meta.textContent = `题库候选 ${candidates.length} 道，本批重复 ${batchMatches.length || duplicateBatchMatchCount(result)} 道${visualNote}`;
                section.appendChild(meta);

                candidates.forEach(candidate => appendDuplicateCandidate(section, candidate));
                batchMatches.forEach(match => {
                    const note = document.createElement('p');
                    note.className = 'mt-2 rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-[10px] font-semibold text-indigo-700 dark:border-indigo-500/30 dark:bg-indigo-500/10 dark:text-indigo-200';
                    let targetLabel = '本批另一道题';
                    const targetKey = typeof match === 'string'
                        ? match
                        : String(match.client_key || match.other_client_key || '');
                    const targetIndex = Number(targetKey.split('-').pop());
                    if (Number.isInteger(targetIndex) && targetIndex >= 0) {
                        targetLabel = `本批第 ${targetIndex + 1} 题`;
                    }
                    note.textContent = `与${targetLabel}疑似重复，请一并核对。`;
                    section.appendChild(note);
                });
                list.appendChild(section);
            });
        }

        function openMergeSourceModal(candidateId, candidateSeq) {
            const overlay = document.createElement('div');
            overlay.id = 'mergeSourceModal';
            overlay.className = 'fixed inset-0 z-[96] hidden items-center justify-center bg-slate-950/55 p-4 opacity-0 backdrop-blur-sm transition-opacity duration-200';
            overlay.setAttribute('role', 'dialog');
            overlay.setAttribute('aria-modal', 'true');
            overlay.innerHTML = '<div data-modal-surface class="glass-modal flex max-h-[88vh] w-full max-w-lg scale-95 flex-col overflow-hidden rounded-3xl border border-white/50 shadow-2xl transition-transform duration-200">' +
                '<div class="flex items-start gap-4 border-b border-slate-200/70 p-5 dark:border-slate-700 sm:p-6">' +
                '<span class="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-emerald-50 text-lg text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-300"><i class="fa-solid fa-circle-plus" aria-hidden="true"></i></span>' +
                '<div class="min-w-0 flex-1"><h3 tabindex="-1" data-modal-initial-focus="true" class="text-base font-bold text-slate-800 dark:text-slate-100">并入已有题 · 追加来源</h3>' +
                '<p class="mt-1.5 text-xs leading-5 text-slate-600 dark:text-slate-300">题目正文与解析保持不变，仅把本次录题的来源记录追加到题库第 ' + escapeHtml(String(candidateSeq == null ? candidateId : candidateSeq)) + ' 题。</p></div>' +
                '<button type="button" data-modal-close aria-label="关闭" class="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-brand-500/30 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>' +
                '</div>' +
                '<div class="min-h-0 flex-1 space-y-3 overflow-y-auto p-5 custom-scrollbar sm:p-6">' +
                '<div><label class="mb-1 block text-xs font-bold text-slate-600 dark:text-slate-300">年份</label><input id="mergeSrcYear" type="text" placeholder="如 2026" class="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"></div>' +
                '<div><label class="mb-1 block text-xs font-bold text-slate-600 dark:text-slate-300">学校 / 地区</label><input id="mergeSrcRegion" type="text" placeholder="如 哈三中、省实验、全国甲卷" class="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"></div>' +
                '<div><label class="mb-1 block text-xs font-bold text-slate-600 dark:text-slate-300">考试类型</label><input id="mergeSrcExamType" type="text" placeholder="如 三模、期末、月考、高考" class="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"></div>' +
                '<div><label class="mb-1 block text-xs font-bold text-slate-600 dark:text-slate-300">整卷出处（试卷标题，可空）</label><input id="mergeSrcPaperTitle" type="text" placeholder="如 2026届哈三中高三三模数学试卷" class="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"></div>' +
                '</div>' +
                '<div class="flex flex-col-reverse gap-2 border-t border-slate-200/70 bg-slate-50/70 p-4 dark:border-slate-700 dark:bg-slate-900/70 sm:flex-row sm:items-center sm:justify-end sm:p-5">' +
                '<button type="button" id="mergeSrcCancel" class="glass-btn min-h-11 rounded-xl px-4 py-2 text-xs font-bold text-slate-600 dark:text-slate-200">取消</button>' +
                '<button type="button" id="mergeSrcSubmit" class="min-h-11 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-bold text-white shadow-sm transition-colors hover:bg-emerald-700">确认并入</button>' +
                '</div></div>';
            document.body.appendChild(overlay);
            const modal = overlay;
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modal.setAttribute('aria-hidden', 'false');
            if (window.MathBankModal) {
                window.MathBankModal.open(modal, { onEscape: () => closeMergeSourceModal(modal) });
            }
            setTimeout(function () {
                modal.classList.remove('opacity-0');
                const surface = modal.querySelector('[data-modal-surface]');
                if (surface) surface.classList.remove('scale-95');
                if (surface) surface.classList.add('scale-100');
            }, 20);
            document.getElementById('mergeSrcCancel').onclick = function () { closeMergeSourceModal(modal); };
            document.getElementById('mergeSrcSubmit').onclick = function () {
                const year = (document.getElementById('mergeSrcYear').value || '').trim();
                const region = (document.getElementById('mergeSrcRegion').value || '').trim();
                const examType = (document.getElementById('mergeSrcExamType').value || '').trim();
                const paperTitle = (document.getElementById('mergeSrcPaperTitle').value || '').trim();
                if (!year && !region && !examType) {
                    showToast('年份 / 学校地区 / 考试类型至少填写一项。', 'warning');
                    return;
                }
                const btn = document.getElementById('mergeSrcSubmit');
                btn.disabled = true;
                btn.setAttribute('aria-busy', 'true');
                btn.textContent = '正在并入…';
                fetch('/api/questions/' + encodeURIComponent(candidateId) + '/sources', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: JSON.stringify({
                        year: year,
                        region: region,
                        exam_type: examType,
                        paper_title: paperTitle
                    })
                }).then(function (resp) {
                    if (!resp.ok) {
                        return resp.json().then(function (d) {
                            throw new Error(d.detail || ('HTTP ' + resp.status));
                        });
                    }
                    return resp.json();
                }).then(function () {
                    closeMergeSourceModal(modal);
                    showToast('已并入题库第 ' + escapeHtml(String(candidateSeq == null ? candidateId : candidateSeq)) + ' 题，来源已追加。', 'success');
                    closeParsedDuplicateReviewModal('merged');
                }).catch(function (err) {
                    btn.disabled = false;
                    btn.removeAttribute('aria-busy');
                    btn.textContent = '确认并入';
                    showToast('并入失败：' + (err && err.message ? err.message : err), 'error');
                });
            };
        }

        function closeMergeSourceModal(modal) {
            if (!modal) return;
            if (window.MathBankModal) window.MathBankModal.close(modal);
            if (modal.parentNode) modal.parentNode.removeChild(modal);
        }

        function openParsedDuplicateReviewModal(entries, mode = 'batch') {
            const modal = document.getElementById('parsedDuplicateReviewModal');
            if (!modal) return Promise.resolve('review');
            if (parsedDuplicateReviewResolver) {
                parsedDuplicateReviewResolver('review');
                parsedDuplicateReviewResolver = null;
            }
            renderParsedDuplicateReview(entries);
            const title = document.getElementById('parsedDuplicateReviewTitle');
            const returnBtn = document.getElementById('duplicateReviewReturnBtn');
            const skipBtn = document.getElementById('duplicateReviewSkipBtn');
            const independentBtn = document.getElementById('duplicateReviewIndependentBtn');
            const mergeBtn = document.getElementById('duplicateReviewMergeBtn');
            if (mergeBtn) {
                mergeBtn.classList.toggle('hidden', mode !== 'single');
                mergeBtn.onclick = function () {
                    const entry = (entries || [])[0];
                    const result = entry && entry.result ? entry.result : {};
                    const candidates = Array.isArray(result.candidates) ? result.candidates : [];
                    const target = candidates[0] || (Array.isArray(result.batch_matches) && result.batch_matches[0]);
                    if (!target || !target.id) {
                        showToast('未找到可并入的候选题目，请先核对查重结果。', 'warning');
                        return;
                    }
                    openMergeSourceModal(target.id, target.seq_num);
                };
            }
            if (title) title.textContent = mode === 'inspect' ? '查重依据' : '发现疑似已收录题目';
            if (returnBtn) returnBtn.textContent = mode === 'inspect' ? '关闭' : '返回审查';
            if (skipBtn) skipBtn.classList.toggle('hidden', mode !== 'batch');
            if (independentBtn) {
                independentBtn.classList.toggle('hidden', mode === 'inspect');
                independentBtn.textContent = mode === 'single' ? '仍作为独立题保存' : '全部独立保存';
            }

            modal.classList.remove('hidden');
            modal.classList.add('flex');
            modal.setAttribute('aria-hidden', 'false');
            window.MathBankModal.open(modal, {
                onEscape: () => closeParsedDuplicateReviewModal('review')
            });
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                const surface = modal.querySelector('[data-modal-surface]');
                if (surface) {
                    surface.classList.remove('scale-95');
                    surface.classList.add('scale-100');
                }
            }, 20);
            return new Promise(resolve => {
                parsedDuplicateReviewResolver = resolve;
            });
        }

        function closeParsedDuplicateReviewModal(action = 'review') {
            const modal = document.getElementById('parsedDuplicateReviewModal');
            resetParsedDuplicateCandidateRendering();
            if (modal && !modal.classList.contains('hidden')) {
                window.MathBankModal.close(modal);
                modal.classList.add('opacity-0');
                modal.setAttribute('aria-hidden', 'true');
                const surface = modal.querySelector('[data-modal-surface]');
                if (surface) {
                    surface.classList.remove('scale-100');
                    surface.classList.add('scale-95');
                }
                setTimeout(() => {
                    modal.classList.add('hidden');
                    modal.classList.remove('flex');
                }, 200);
            }
            const resolve = parsedDuplicateReviewResolver;
            parsedDuplicateReviewResolver = null;
            if (resolve) resolve(action);
        }

        function parsedDuplicateReviewEntries(indices, results = parsedDuplicateResults) {
            return indices.map(index => ({
                label: `拆解结果第 ${index + 1} 题`,
                result: results.get(index) || parsedDuplicateResults.get(index) || {}
            }));
        }

        function openParsedDuplicateReview(index) {
            const result = parsedDuplicateResults.get(index);
            if (!result) return;
            openParsedDuplicateReviewModal(parsedDuplicateReviewEntries([index]), 'inspect');
        }

        function applySaveTimeDuplicateConflict(index, data) {
            const candidates = Array.isArray(data && data.candidates)
                ? data.candidates.map(candidate => ({
                    ...candidate,
                    level: 'exact',
                    score: 1,
                    reasons: ['EXACT_TEXT'],
                    needs_visual_review: true
                }))
                : [];
            const result = {
                client_key: `save-conflict-${index}`,
                snapshot_hash: String(data && data.snapshot_hash || ''),
                level: 'exact',
                candidates: candidates,
                batch_matches: [],
                needs_visual_review: true
            };
            parsedDuplicateResults.set(index, result);
            const currentItem = buildParsedQuestionDuplicateItem(index, parsedQuestionsGeneration);
            if (currentItem) {
                parsedDuplicateSnapshots.set(index, serializeQuestionDuplicateItem(currentItem));
            }
            parsedDuplicateCheckStatus = 'ready';
            parsedDuplicateSummaryVisible = true;
            updateParsedDuplicateBadge(index);
            updateParsedDuplicateSummary();
            return result;
        }

        function focusFirstParsedDuplicate(indices) {
            const firstIndex = indices[0];
            const card = document.getElementById(`parsed-card-${firstIndex}`);
            if (!card) return;
            card.scrollIntoView({ behavior: 'smooth', block: 'center' });
            card.classList.add('ring-2', 'ring-amber-400');
            setTimeout(() => card.classList.remove('ring-2', 'ring-amber-400'), 2200);
            const badge = card.querySelector('.card-duplicate-badge');
            if (badge) badge.focus({ preventScroll: true });
        }

        async function checkEditorDuplicateBeforeSave(editorSession) {
            const item = buildEditorQuestionDuplicateItem(editorSession);
            const localSnapshot = serializeQuestionDuplicateItem(item);
            try {
                const data = await requestQuestionDuplicateCheck([item]);
                const result = data.items.find(entry => String(entry.client_key) === item.client_key);
                if (!result) throw new Error('查重结果与当前题目无法对应');
                if (data.index && data.index.ready === false) {
                    const coverage = Math.min(99, Math.floor(Number(data.index.coverage || 0) * 100));
                    const warning = String(data.index.warning || '');
                    showToast(warning || `查重索引仍在构建，当前覆盖约 ${coverage}% 题库。`, 'warning');
                }
                let duplicateOverride = '';
                let autoTag = '';
                if (duplicateResultHasSignal(result)) {
                    const action = await openParsedDuplicateReviewModal([{
                        label: editorSession && editorSession.questionId ? '当前编辑题目' : '当前待录入题目',
                        result: result
                    }], 'single');
                    if (action === 'merged') {
                        return { allowed: false, localSnapshot: localSnapshot, merged: true };
                    }
                    if (action !== 'independent') {
                        return { allowed: false, localSnapshot: localSnapshot };
                    }
                    duplicateOverride = 'independent';
                    autoTag = buildDuplicateAutoTag(result);
                }
                const currentSnapshot = serializeQuestionDuplicateItem(
                    buildEditorQuestionDuplicateItem(editorSession)
                );
                if (currentSnapshot !== localSnapshot) {
                    showToast('题目在查重期间已发生变化，请重新保存以再次查重。', 'info');
                    return { allowed: false, localSnapshot: localSnapshot };
                }
                return {
                    allowed: true,
                    localSnapshot: localSnapshot,
                    duplicateOverride: duplicateOverride,
                    duplicateSnapshotHash: String(result.snapshot_hash || ''),
                    autoTag: autoTag
                };
            } catch (error) {
                if (error && error.isValidationError) {
                    showToast(`查重请求内容无效：${error.message}。请修正后重试。`, 'warning');
                    return { allowed: false, checkFailed: true, localSnapshot: localSnapshot };
                }
                showToast(`查重暂不可用：${error.message}。本次将继续保存，请稍后人工核对。`, 'warning');
                return { allowed: true, checkFailed: true, localSnapshot: localSnapshot };
            }
        }

        // 只读查看某题某个槽位的 TikZ 源码。
        // 源码存在 content/answer_tikz_assets 里，导出时 paper_helper 直接取它内联编译成
        // 矢量绘图，所以它不需要也不应该出现在题干/解答正文中。这里只负责「让人核对」；
        // 要修改请到单题编辑的 TikZ 工作台（那边有编译预览与插入流程）。
        function openParsedCardTikzSource(index, slot) {
            const q = parsedQuestionsData[index];
            if (!q) return;
            const key = slot === 'answer' ? 'answer_tikz_assets' : 'content_tikz_assets';
            const assets = Array.isArray(q[key]) ? q[key] : [];
            const blocks = assets
                .map(asset => String((asset && asset.tikz_code) || '').trim())
                .filter(Boolean);
            if (!blocks.length) {
                showToast('本题该位置还没有 TikZ 源码。', 'info');
                return;
            }

            const slotLabel = slot === 'answer' ? '解答' : '题干';
            const modal = document.createElement('div');
            modal.id = 'parsedTikzSourceModal';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-label', '第 ' + (index + 1) + ' 题 TikZ 源码');
            modal.className = 'fixed inset-0 glass-modal-overlay z-50 flex items-center justify-center';

            const panel = document.createElement('div');
            panel.className = 'glass-modal rounded-2xl w-[92vw] max-w-3xl max-h-[85vh] '
                + 'flex flex-col overflow-hidden';

            const header = document.createElement('div');
            header.className = 'flex items-center justify-between border-b border-slate-200/60 '
                + 'px-5 py-3 shrink-0';
            const title = document.createElement('h3');
            title.className = 'text-sm font-bold text-slate-700';
            const totalChars = blocks.reduce((sum, code) => sum + code.length, 0);
            title.textContent = '第 ' + (index + 1) + ' 题 · ' + slotLabel + ' TikZ 源码'
                + '（' + blocks.length + ' 幅 · ' + totalChars + ' 字）';
            header.appendChild(title);
            const closeBtn = document.createElement('button');
            closeBtn.type = 'button';
            closeBtn.className = 'w-8 h-8 flex items-center justify-center rounded-xl '
                + 'text-slate-400 hover:text-slate-600 transition-all';
            closeBtn.setAttribute('aria-label', '关闭 TikZ 源码');
            closeBtn.textContent = '✕';
            header.appendChild(closeBtn);
            panel.appendChild(header);

            // 「存储事实」信息条：直接回答「源码到底存没存」。
            // 用户会反复怀疑转换是假的 —— 因为点图只能看到 PNG（那是给人看的预览），
            // 而源码是文本、没有图片 URL，永远不会出现在图片里。所以必须在这儿把事实摊开：
            // 存在哪个字段、多少字、编译产物与原参考图分别是什么。
            const facts = document.createElement('div');
            facts.className = 'px-5 py-3 border-b border-slate-200/60 shrink-0 space-y-1';

            const headline = document.createElement('p');
            headline.className = 'text-[11px] font-bold text-emerald-700';
            headline.textContent = '✓ 源码已存于本题资产（' + key + '）';
            facts.appendChild(headline);

            assets.forEach((asset, i) => {
                const code = String((asset && asset.tikz_code) || '').trim();
                if (!code) return;
                const parts = [];
                if (blocks.length > 1) parts.push('第 ' + (i + 1) + ' 幅');
                parts.push('源码 ' + code.length + ' 字');
                const compiled = parsedImageBasename(asset.image_path);
                if (compiled) parts.push('编译产物 ' + compiled);
                const reference = parsedImageBasename(asset.reference_image_path);
                if (reference) parts.push('原参考图 ' + reference);
                const line = document.createElement('p');
                line.className = 'text-[10px] text-slate-500 leading-relaxed break-all';
                line.textContent = parts.join(' · ');
                facts.appendChild(line);
            });

            const note = document.createElement('p');
            note.className = 'text-[10px] text-slate-400 leading-relaxed';
            note.textContent = '导出时使用的是源码（矢量）；点开大图看到的始终是编译产物 PNG '
                + '—— 那不是「没存源码」。要修改请到单题编辑的 TikZ 工作台。';
            facts.appendChild(note);
            panel.appendChild(facts);

            const body = document.createElement('div');
            body.className = 'flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-3 custom-scrollbar';
            blocks.forEach((code, i) => {
                const wrap = document.createElement('div');
                wrap.className = 'space-y-1';
                if (blocks.length > 1) {
                    const label = document.createElement('div');
                    label.className = 'text-[10px] font-bold text-slate-500';
                    label.textContent = '第 ' + (i + 1) + ' 幅';
                    wrap.appendChild(label);
                }
                const pre = document.createElement('pre');
                pre.className = 'text-[10px] leading-relaxed font-mono whitespace-pre-wrap '
                    + 'break-all bg-slate-50 border border-slate-200 rounded-lg p-3 select-text';
                // textContent 赋值：源码原样呈现，不会被当成 HTML 解析。
                pre.textContent = code;
                wrap.appendChild(pre);
                body.appendChild(wrap);
            });
            panel.appendChild(body);

            const footer = document.createElement('div');
            footer.className = 'flex items-center justify-end gap-2 border-t '
                + 'border-slate-200/60 px-5 py-3 shrink-0';
            const copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'px-3 py-1.5 rounded-lg text-[10px] font-bold border '
                + 'border-slate-200 text-slate-600 hover:bg-slate-50 transition-colors';
            copyBtn.textContent = '复制全部源码';
            copyBtn.addEventListener('click', function () {
                const text = blocks.join('\n\n');
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(
                        () => showToast('源码已复制到剪贴板。', 'success'),
                        () => showToast('复制失败，请手动选择文本复制。', 'warning')
                    );
                } else {
                    showToast('当前环境不支持自动复制，请手动选择文本复制。', 'info');
                }
            });
            footer.appendChild(copyBtn);
            panel.appendChild(footer);

            modal.appendChild(panel);
            document.body.appendChild(modal);

            const close = () => {
                if (window.MathBankModal) window.MathBankModal.close(modal);
                if (modal.isConnected) modal.remove();
            };
            closeBtn.addEventListener('click', close);
            if (window.MathBankModal) {
                window.MathBankModal.open(modal, { onEscape: close });
            }
        }
        window.openParsedCardTikzSource = openParsedCardTikzSource;

        function renderParsedQuestionsList(questions) {
            const container = document.getElementById('parsedCardsContainer');
            container.innerHTML = '';
            document.getElementById('parsedCountBadge').textContent = `共 ${questions.length} 题`;

            if (questions.length === 0) {
                container.innerHTML = '<div class="p-12 text-center text-slate-400 text-xs">AI 未能拆解出任何有效的题目，请检查 LaTeX 格式是否规整。</div>';
                if (typeof updateSelectedCount === 'function') updateSelectedCount();
                return;
            }

            questions.forEach((q, index) => {
                let qTypeOptionsHtml = '';
                if (window.systemMetadata && window.systemMetadata.question_types) {
                    window.systemMetadata.question_types.forEach(item => {
                        qTypeOptionsHtml += `<option value="${window.MathBankSafe.escapeAttribute(item.value)}" ${q.question_type === item.value ? 'selected' : ''}>${window.MathBankSafe.escapeText(item.label)}</option>`;
                    });
                } else {
                    qTypeOptionsHtml = `
                        <option value="single_choice" ${q.question_type === 'single_choice' ? 'selected' : ''}>单选题</option>
                        <option value="multi_choice" ${q.question_type === 'multi_choice' ? 'selected' : ''}>多选题</option>
                        <option value="fill_in_blank" ${q.question_type === 'fill_in_blank' ? 'selected' : ''}>填空题</option>
                        <option value="detailed_answer" ${q.question_type === 'detailed_answer' ? 'selected' : ''}>解答题</option>
                    `;
                }

                let difficultyOptionsHtml = '';
                if (window.systemMetadata && window.systemMetadata.difficulties) {
                    window.systemMetadata.difficulties.forEach(item => {
                        difficultyOptionsHtml += `<option value="${window.MathBankSafe.escapeAttribute(item.value)}" ${q.difficulty === item.value ? 'selected' : ''}>${window.MathBankSafe.escapeText(item.label)}</option>`;
                    });
                } else {
                    difficultyOptionsHtml = `
                        <option value="easy_error" ${q.difficulty === 'easy_error' ? 'selected' : ''}>易错题</option>
                        <option value="normal" ${q.difficulty === 'normal' ? 'selected' : ''}>常规题</option>
                        <option value="challenge" ${q.difficulty === 'challenge' ? 'selected' : ''}>挑战题</option>
                        <option value="qiangji" ${q.difficulty === 'qiangji' ? 'selected' : ''}>强基题</option>
                    `;
                }

                const card = document.createElement('div');
                card.className = "glass-card rounded-xl p-3 space-y-2.5 flex flex-col relative";
                card.id = `parsed-card-${index}`;
                
                card.innerHTML = `
                    <!-- Card Top Configs Bar -->
                    <!-- Header: selection + global index + status -->
                    <div class="flex items-center justify-between gap-2 border-b pb-2 shrink-0">
                        <div class="flex items-center space-x-2 select-none text-slate-700 text-xs font-bold">
                            <input type="checkbox" data-index="${index}" class="card-select-checkbox h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500 cursor-pointer transition-colors" ${q.saved ? 'disabled opacity-50' : 'checked'} onclick="event.stopPropagation()">
                            <span class="h-5 w-5 bg-brand-50 text-brand-600 rounded-full flex items-center justify-center text-[10px] font-bold border border-brand-100">${index + 1}</span>
                            <span>第 ${index + 1} / 共 ${questions.length} 题</span>
                            ${q.paper_question_no ? '<span class="ml-2 px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 text-[10px] font-bold border border-indigo-100">原卷 ' + q.paper_question_no + ' 题</span>' : ''}
                        </div>
                        <div class="flex flex-wrap items-center justify-end gap-1.5">
                            ${(typeof q._source_page === 'number' && q._source_page >= 0)
                                ? '<button type="button" onclick="openParsedQuestionSourcePage(' + index + ')" class="card-source-page min-h-11 px-3 text-[9px] font-semibold rounded border border-slate-200 text-slate-500 hover:text-brand-600 hover:border-brand-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 transition-colors" title="跳到本卷原页对照核对：这道题是否本该有图">源自第 ' + (q._source_page + 1) + ' 页</button>'
                                : ''}
                            <button type="button" onclick="openParsedDuplicateReview(${index})" class="card-duplicate-badge hidden min-h-11 px-3 text-[9px] font-semibold rounded border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400"></button>
                            <span class="card-status-badge text-[10px] font-bold px-2 py-0.5 rounded ${q.saved ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-slate-100 text-slate-500'}">${q.saved ? '已导入' : '待导入'}</span>
                        </div>
                    </div>

                    <!-- Meta row: 题号 → 来源 → 题型 → 难度 → 册次 → 章节 → 小节 → 考点 -->
                    <div class="grid grid-cols-8 gap-1.5 border-b pb-2 shrink-0">
                        <input type="text" class="card-question-no glass-input px-2 py-1.5 rounded-lg text-xs font-semibold" placeholder="题号">
                        <input type="text" class="card-source glass-input px-2.5 py-1.5 rounded-lg text-xs font-semibold" placeholder="来源（留空继承试卷名）">
                        <select class="card-qtype glass-select px-2 py-1.5 rounded-lg text-xs font-semibold">
                            ${qTypeOptionsHtml}
                        </select>
                        <select class="card-difficulty glass-select px-2 py-1.5 rounded-lg text-xs font-semibold">
                            ${difficultyOptionsHtml}
                        </select>
                        <select class="card-compulsory glass-select px-2 py-1.5 rounded-lg text-xs font-semibold">
                            <option value="">所有学段</option>
                        </select>
                        <select class="card-chapter glass-select px-2 py-1.5 rounded-lg text-xs font-semibold">
                            <option value="">所有章节</option>
                        </select>
                        <select class="card-knowledge glass-select px-2 py-1.5 rounded-lg text-xs font-semibold">
                            <option value="">所有小节</option>
                        </select>
                        <select class="card-topic glass-select px-2 py-1.5 rounded-lg text-xs font-semibold">
                            <option value="">所有考向</option>
                        </select>
                    </div>

                    <!-- Body Content Split -->
                    <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 flex-1 min-h-[200px]">
                        <!-- Left Side: Inputs -->
                        <div class="flex flex-col gap-2 min-h-0">
                            <div class="flex flex-col flex-1 min-h-0 space-y-1">
                                <div class="flex items-center justify-between gap-2">
                                    <label class="text-[10px] font-bold text-slate-500 tracking-wider">题干编辑</label>
                                    <button type="button" class="card-content-tikz-source hidden min-h-11 px-2 text-[9px] font-bold rounded border border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 transition-colors" title="查看本题的 TikZ 绘图源码（源码存在资产里，不写入题干正文）">TikZ 源码</button>
                                </div>
                                <textarea class="card-content-textarea glass-input w-full flex-1 min-h-[80px] p-2.5 rounded-lg font-mono text-xs resize-none custom-scrollbar"></textarea>
                            </div>
                            <div class="flex flex-col flex-1 min-h-0 space-y-1">
                                <div class="flex items-center justify-between gap-2">
                                    <label class="text-[10px] font-bold text-slate-500 tracking-wider">答案与解析编辑</label>
                                    <button type="button" class="card-answer-tikz-source hidden min-h-11 px-2 text-[9px] font-bold rounded border border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 transition-colors" title="查看本题解答的 TikZ 绘图源码（源码存在资产里，不写入解答正文）">TikZ 源码</button>
                                </div>
                                <textarea class="card-answer-textarea glass-input w-full flex-1 min-h-[80px] p-2.5 rounded-lg font-mono text-xs resize-none custom-scrollbar"></textarea>
                            </div>
                        </div>

                        <!-- Right Side: realtime preview -->
                        <div class="flex flex-col gap-2 min-h-0">
                            <div class="border border-slate-200 rounded-xl bg-slate-50/60 p-3 overflow-y-auto flex-1 min-h-[140px] space-y-2.5 text-xs font-serif leading-relaxed custom-scrollbar flex flex-col justify-start relative select-text">
                                <span class="absolute top-2 right-2 text-[8px] font-bold text-slate-400 bg-white/80 px-1.5 py-0.5 rounded border tracking-wider select-none">实时渲染</span>
                                <div class="card-content-preview border-b border-slate-200/60 pb-2 text-slate-800"></div>
                                <div class="card-answer-preview text-slate-700"></div>
                            </div>
                        </div>
                    </div>

                    <!-- Card Actions Footer -->
                    <div class="flex justify-between items-center border-t border-slate-100 pt-2 shrink-0">
                        <div class="flex flex-wrap gap-1.5 items-center max-w-[70%]" id="card-images-badges-${index}">
                            <!-- Thumbnail labels of images selected -->
                        </div>
                        <div class="flex items-center space-x-2">
                            <button onclick="handleManualScreenshotForQuestion(${index})" class="glass-btn text-amber-700 font-bold px-3 py-1.5 rounded-lg text-xs flex items-center space-x-1" title="查看 PDF 页面并拖拽框选截图">
                                <i class="fa-solid fa-scissors"></i>
                                <span>手动截图</span>
                            </button>
                            <button onclick="generateTikZForQuestion(${index})" class="card-tikz-draw-btn glass-btn text-teal-700 font-bold px-3 py-1.5 rounded-lg text-xs flex items-center space-x-1 shrink-0" title="根据本题内容让 AI 生成 TikZ 矢量示意图">
                                <i class="fa-solid fa-pen-ruler text-teal-500"></i>
                                <span>TikZ 绘图</span>
                            </button>
                            <button onclick="generateSingleAnswer(${index})" class="card-solve-btn glass-btn text-indigo-700 font-bold px-3 py-1.5 rounded-lg text-xs flex items-center space-x-1 shrink-0" title="对本题单独调用 AI 生成详细解答与解析">
                                <i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i>
                                <span>${q.answer_markdown ? '重生成解析' : 'AI 生成解析'}</span>
                            </button>
                            <button onclick="saveParsedQuestion(${index})" class="card-save-btn px-4 py-1.5 rounded-lg text-xs flex items-center space-x-1 shrink-0 ${q.saved ? 'bg-emerald-50 text-emerald-700 font-bold border border-emerald-300 hover:bg-emerald-100' : 'glass-btn text-brand-700 font-bold'}">
                                <i class="fa-solid ${q.saved ? 'fa-rotate-right' : 'fa-file-arrow-up'}"></i>
                                <span>${q.saved ? '再次导入' : '导入此题'}</span>
                            </button>
                        </div>
                    </div>
                `;

                // Never interpolate AI/import values into attributes or textarea
                // HTML. Property assignment preserves LaTeX verbatim and prevents
                // attribute/textarea breakout payloads.
                card.querySelector('.card-source').value = window.MathBankSafe.sanitizePlainText(q.source || '');
                // 题号：优先用解析结果的原卷题号，缺省回落到本题在卷中的顺序号。
                const cardQuestionNo = card.querySelector('.card-question-no');
                if (cardQuestionNo) {
                    cardQuestionNo.value = window.MathBankSafe.sanitizePlainText(
                        String(q.paper_question_no || (index + 1))
                    );
                }
                // 题干编辑区**不拼接** TikZ 源码。
                // 源码已存在 content_tikz_assets 里，导出时 paper_helper 会直接取它内联成
                // 矢量绘图，所以题干里再放一份是纯多余的副本；而它的害处很实在：
                //   ① 预览是从编辑框 value 渲染的 → 整段代码被当正文显示；
                //   ② 保存时 content 直接取编辑框 value → 源码被**永久写进题干 markdown**，
                //      后续每次 AI 调用都要重读这堆代码，导出时一个括号不匹配就编译失败。
                // 需要核对源码时用卡片上的「TikZ 源码」入口（见 appendParsedCardTikzSourceEntry）。
                card.querySelector('.card-content-textarea').value = String(q.content || '');
                card.querySelector('.card-answer-textarea').value = String(q.answer_markdown || '');

                // 卡片上的「TikZ 源码 (N)」入口：源码留在 assets 里，点开只读核对。
                // 只有真的存了源码才显示，避免每道题都挂一个空按钮。
                try {
                    [['content', '.card-content-tikz-source'],
                        ['answer', '.card-answer-tikz-source']].forEach(pair => {
                        const btn = card.querySelector(pair[1]);
                        if (!btn) return;
                        const assets = Array.isArray(q[pair[0] + '_tikz_assets'])
                            ? q[pair[0] + '_tikz_assets']
                            : [];
                        const usable = assets.filter(
                            asset => asset && String(asset.tikz_code || '').trim()
                        );
                        if (!usable.length) return;
                        // 字符数是「确实存了东西」最直接的凭据 —— 空的就是 0 字。
                        const chars = usable.reduce(
                            (total, asset) => total
                                + String(asset.tikz_code || '').trim().length,
                            0
                        );
                        btn.classList.remove('hidden');
                        btn.textContent = '✓ TikZ 源码已存 · ' + usable.length + ' 幅 · '
                            + chars + ' 字';
                        btn.title = '查看 / 复制本题的 TikZ 源码原文（导出时用的就是它）';
                        btn.addEventListener('click', function () {
                            openParsedCardTikzSource(index, pair[0]);
                        });
                    });
                } catch (tikzSourceEntryError) {
                    console.error('TikZ 源码入口初始化失败:', tikzSourceEntryError);
                }

                container.appendChild(card);
                // 单张卡片出问题不应该中断其余题目的渲染。
                try {
                    setupCardCategoryLinkage(card, q);
                } catch (linkageError) {
                    console.error('题目分类联动初始化失败:', linkageError);
                }

                // Populate image badges（解答图归解答预览，不在这里重复当成题干配图）
                try {
                    const badgesContainer = document.getElementById(`card-images-badges-${index}`);
                    const mappedImgs = Array.isArray(q.image_paths)
                        ? q.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                        : [];
                    q.image_paths = Array.from(new Set(mappedImgs));
                    const answerOwned = new Set(parsedCardAnswerImagePaths(q.answer_markdown));
                    q.image_paths
                        .filter(path => !answerOwned.has(path))
                        .forEach(path => appendSafeImageBadge(badgesContainer, path));
                } catch (imageBadgeError) {
                    console.error('题目插图徽章渲染失败:', imageBadgeError);
                }

                // Set up checkbox listener
                const selectCb = card.querySelector('.card-select-checkbox');
                selectCb.addEventListener('change', () => {
                    if (typeof updateSelectedCount === 'function') updateSelectedCount();
                });

                // Set up preview
                const textInput = card.querySelector('.card-content-textarea');
                const ansInput = card.querySelector('.card-answer-textarea');
                
                const triggerPreview = () => {
                    try {
                        renderParsedCardPreview(card, textInput.value, ansInput.value);
                    } catch (previewError) {
                        console.error('题目实时预览渲染失败:', previewError);
                    }
                };
                const debouncedPreview = debounce(triggerPreview, 200);

                textInput.addEventListener('input', () => {
                    invalidateParsedDuplicateCheck(index);
                    debouncedPreview();
                });
                ansInput.addEventListener('input', () => {
                    invalidateParsedDuplicateCheck(index);
                    debouncedPreview();
                });
                const typeInput = card.querySelector('.card-qtype');
                if (typeInput) {
                    typeInput.addEventListener('change', () => invalidateParsedDuplicateCheck(index));
                }

                try {
                    triggerPreview();
                } catch (previewError) {
                    console.error('题目实时预览渲染失败:', previewError);
                }
            });

            if (typeof updateSelectedCount === 'function') updateSelectedCount();
        }

        function setupCardCategoryLinkage(card, q) {
            const compSelect = card.querySelector('.card-compulsory');
            const chapSelect = card.querySelector('.card-chapter');
            const knowSelect = card.querySelector('.card-knowledge');
            const topicSelect = card.querySelector('.card-topic');

            const fuzzyMatchName = (aiName, names) => {
                const s = String(aiName || '').trim();
                if (!s) return '';
                if (names.indexOf(s) !== -1) return s;
                const strip = (x) => String(x).trim().replace(/^\s*\d+(\.\d+)*\s*/, '');
                const sn = strip(s);
                if (sn) {
                    for (let i = 0; i < names.length; i++) {
                        if (strip(names[i]) === sn) return names[i];
                    }
                    for (let i = 0; i < names.length; i++) {
                        const nn = strip(names[i]);
                        if (nn && (sn.indexOf(nn) !== -1 || nn.indexOf(sn) !== -1)) return names[i];
                    }
                }
                return '';
            };

            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            Object.keys(categoryTree).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                if (c === q.category_compulsory) opt.selected = true;
                compSelect.appendChild(opt);
            });

            const updateTopics = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                const know = knowSelect.value;
                topicSelect.innerHTML = '<option value="">-- 选择考向 --</option>';
                topicSelect.disabled = true;
                if (!comp || !chap || !know) return;
                const topics = (categoryTree[comp] && categoryTree[comp][chap] && Array.isArray(categoryTree[comp][chap][know])) ? categoryTree[comp][chap][know] : [];
                if (topics.length === 0) return;
                topicSelect.disabled = false;
                const tNames = [];
                topics.forEach(t => {
                    const tObj = (typeof t === 'object' && t) ? t : { name: String(t), level: '' };
                    if (tObj.name) tNames.push(tObj.name);
                });
                const matchedTopic = fuzzyMatchName(q.topic, tNames);
                topics.forEach(t => {
                    const tObj = (typeof t === 'object' && t) ? t : { name: String(t), level: '' };
                    const tName = tObj.name || '';
                    if (!tName) return;
                    const opt = document.createElement('option');
                    opt.value = tName;
                    opt.dataset.level = tObj.level || '';
                    opt.textContent = tName + (tObj.level ? ' (' + tObj.level + ')' : '');
                    if (tName === matchedTopic) opt.selected = true;
                    topicSelect.appendChild(opt);
                });
                // 选中考向 → 难度联动为该考向的 L 级别；考向没匹配上时清空难度，不保留 AI 预填
                const diffSelect = card.querySelector('.card-difficulty');
                const selOpt = topicSelect.selectedOptions && topicSelect.selectedOptions[0];
                const level = selOpt ? (selOpt.dataset.level || '') : '';
                if (diffSelect) {
                    if (level && diffSelect.querySelector('option[value="' + level + '"]')) {
                        diffSelect.value = level;
                    } else if (!matchedTopic) {
                        diffSelect.value = '';
                    }
                }
            };

            const updateChapters = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;
                topicSelect.innerHTML = '<option value="">-- 选择考向 --</option>';
                topicSelect.disabled = true;

                if (comp && categoryTree[comp]) {
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        const opt = document.createElement('option');
                        opt.value = ch;
                        opt.textContent = ch;
                        if (ch === q.category_chapter) opt.selected = true;
                        chapSelect.appendChild(opt);
                    });
                } else {
                    chapSelect.disabled = true;
                }
            };

            const updateKnowledge = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (默认整章) --</option>';
                topicSelect.innerHTML = '<option value="">-- 选择考向 --</option>';
                topicSelect.disabled = true;

                if (comp && chap && categoryTree[comp][chap]) {
                    knowSelect.disabled = false;
                    const kNames = Object.keys(categoryTree[comp][chap]);
                    const matchedKnow = fuzzyMatchName(q.category_knowledge, kNames);
                    kNames.forEach(kName => {
                        const opt = document.createElement('option');
                        opt.value = kName;
                        opt.textContent = kName;
                        if (kName === matchedKnow) opt.selected = true;
                        knowSelect.appendChild(opt);
                    });
                } else {
                    knowSelect.disabled = true;
                }
            };

            compSelect.addEventListener('change', () => {
                updateChapters();
                updateKnowledge();
                updateTopics();
            });

            chapSelect.addEventListener('change', () => {
                updateKnowledge();
                updateTopics();
            });

            knowSelect.addEventListener('change', () => {
                updateTopics();
            });

            topicSelect.addEventListener('change', () => {
                const diffSelect = card.querySelector('.card-difficulty');
                const selOpt = topicSelect.selectedOptions && topicSelect.selectedOptions[0];
                const level = selOpt ? (selOpt.dataset.level || '') : '';
                if (diffSelect && level && diffSelect.querySelector('option[value="' + level + '"]')) {
                    diffSelect.value = level;
                }
            });

            updateChapters();
            updateKnowledge();
            updateTopics();
        }

        // ---- 插图来源标记 + 一键转 TikZ（拆题预览区）----
        // 后端裁图管线产出的文件名形如 pdf_crop_{task}_{page}_{idx}.png（页码从 0 起），
        // 据此把"原题位图"与"AI 重绘的 TikZ"在预览里明确区分开，避免再出现
        // "这张图到底是不是 TikZ" 的反复确认。
        function describeQuestionImageSource(path) {
            const raw = String(path || '');
            const base = raw.split('?')[0].split('#')[0].split('/').pop() || '';
            const cropMatch = base.match(/^pdf_crop_[^_]+_(\d+)_(\d+)\./i);
            if (cropMatch) {
                const pageLabel = String(parseInt(cropMatch[1], 10) + 1);
                return {
                    kind: 'crop',
                    label: '位图 · 第' + pageLabel + '页',
                    title: '从 PDF 第 ' + pageLabel + ' 页按坐标裁剪出的原题位图（不是 TikZ）'
                };
            }
            if (/^tikz_/i.test(base)) {
                return { kind: 'tikz', label: 'AI · TikZ', title: 'AI 重绘的 TikZ 矢量图' };
            }
            return { kind: 'bitmap', label: '位图', title: '普通位图（不是 TikZ）' };
        }

        // 角标底色：位图=深灰，已转 TikZ=靛蓝，转换中=琥珀，转换失败=玫红。
        function tikzBadgeToneClass(tone) {
            if (tone === 'tikz') return 'bg-indigo-600/90 text-white';
            if (tone === 'converting') return 'bg-amber-500/95 text-white';
            if (tone === 'failed') return 'bg-rose-600/95 text-white';
            return 'bg-slate-700/85 text-white';
        }

        function parsedCardTikzStateKey(index, safePath) {
            return String(index) + '::' + String(safePath || '');
        }

        // 下面三个访问器都带 typeof 守卫：前端切片测试可能把调用方单独抽出去执行，
        // 那时 parsedCardTikzStates 并不存在，不能让角标渲染整体炸掉。
        function parsedCardTikzStore() {
            if (typeof parsedCardTikzStates === 'undefined' || !parsedCardTikzStates) return null;
            return typeof parsedCardTikzStates.get === 'function' ? parsedCardTikzStates : null;
        }

        function setParsedCardTikzState(index, safePath, state) {
            const store = parsedCardTikzStore();
            if (store && typeof store.set === 'function') {
                store.set(parsedCardTikzStateKey(index, safePath), state);
            }
        }

        function clearParsedCardTikzState(index, safePath) {
            const store = parsedCardTikzStore();
            if (store && typeof store.delete === 'function') {
                store.delete(parsedCardTikzStateKey(index, safePath));
            }
        }

        // 汇总一张图在界面上的四态：位图 / 已转 TikZ / 转换中 / 转换失败。
        // 三处角标渲染点共用它，避免「哪张图到底转没转」的规则各写一遍而漂移。
        // canRetry = 是否给出「转 TikZ / 重新转换」入口。
        //
        // 优先级刻意是：转换中 > 已转 TikZ > 转换失败 > 位图。
        //   · 「转换中」代表此刻确有任务在跑，进度必须让用户看见；
        //   · 「已转 TikZ」是**持久事实**（assets 里真的存在这张参考图的 TikZ），
        //     绝不能被一条陈旧的「失败」记录盖住——否则用户会以为转好的图反而坏了。
        function describeQuestionImageBadge(index, safePath, alreadyTikz) {
            const source = describeQuestionImageSource(safePath);
            const store = parsedCardTikzStore();
            const state = store
                ? store.get(parsedCardTikzStateKey(index, safePath))
                : null;
            if (state && state.status === 'converting') {
                return {
                    tone: 'converting',
                    label: '转换中…',
                    title: '正在让 AI 重绘为 TikZ 矢量图，请稍候',
                    canRetry: false
                };
            }
            if (alreadyTikz || source.kind === 'tikz') {
                return {
                    tone: 'tikz',
                    label: 'AI · TikZ',
                    title: '该位图已按参考图重绘为 TikZ 矢量图',
                    canRetry: false
                };
            }
            if (state && state.status === 'failed') {
                return {
                    tone: 'failed',
                    label: '转换失败',
                    title: '转 TikZ 失败：' + (state.message || '未知错误') + '（可点「重新转换」再试）',
                    canRetry: true
                };
            }
            return {
                tone: 'bitmap',
                label: source.label,
                title: source.title,
                canRetry: true
            };
        }

        function parsedCardLocalToken() {
            if (typeof window.getLocalToken === 'function') {
                return window.getLocalToken() || '';
            }
            try {
                return localStorage.getItem('local_token') || '';
            } catch (err) {
                return '';
            }
        }

        // 图片归属判定。DB 只有一个扁平的 image_paths（题干图与解答图混存），
        // 所以归属必须在渲染/登记时派生。规则与编辑器、导入保存路径完全一致：
        // 出现在解答 markdown 里的图属于「解答」，其余属于「题干」
        // （post_process 已把题干图从 content 剥离、只留在 image_paths 里）。
        function parsedCardAnswerImagePaths(answerText) {
            if (typeof window === 'undefined'
                || typeof window.collectAnswerImagePaths !== 'function') {
                return [];
            }
            return window.collectAnswerImagePaths(String(answerText || ''));
        }

        // 选项标号行：整行只有标号（`A` / `A．`），或以「标号+标点」开头（`A．106`）。
        // ⚠️ 必须容忍**不带标点的裸标号行** —— AI 常输出「A」单独一行、图形另起一行。
        const PARSED_CHOICE_LABEL_RE = /^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*(?:[.．、,，:：)）][^\n]*)?$/;

        // 「整行只有图片」判据：用于「标号一行、图形另起一行」的形态。
        // 少了它，紧跟选项行之后的普通配图（`如图 ![](url) 所示` 那种）会被误判成选项图。
        const PARSED_IMAGE_ONLY_RE = /^[ \t]*(?:!\[[^\]]*\]\([^)]*\)[ \t]*)+$/;

        // **裸标号行**：整行只有标号（`A` / `A．`），后面没有正文。
        // 「标号在上一行 → 图片属于该选项」只允许用裸标号行做依据：
        // `A．106 B．105 C．104 D．103` 这种整行选项虽以 `A．` 开头，但跟在它
        // 后面的图片是普通题干配图（与后端 _BARE_CHOICE_LABEL_RE 同口径）。
        const PARSED_BARE_CHOICE_LABEL_RE = /^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*[.．、,，:：)）]?[ \t]*$/;

        // choices 环境内的 `\item` 行：OCR 提示词（COMMON_OCR_PROMPT 第 3 条）要求
        // 剥离 A/B/C/D 标号，产出 `\item ![插图](...)`，行首没有标号 —— 只认标号的
        // 判据认不出来，选项图会被错标成「题干图」（与后端同口径的第三种形态）。
        const PARSED_CHOICES_ITEM_RE = /\\item\b/;

        // 选项图：`A．![](url)` 同行直接认；或是「裸标号一行 + 下一行**只有图片**」；
        // 或是 choices 环境内的 `\item` 行。
        // 必须与后端 `parsing_pipeline._strip_image_reference` 同口径，否则会出现
        // 「后端保留了选项图、前端却标成题干图」的两边打架。
        function parsedCardChoiceImagePaths(contentText) {
            const text = String(contentText || '');
            if (!text) return [];
            const found = [];
            const imgRe = /!\[[^\]]*\]\(([^)]+)\)/g;
            let lastMeaningful = '';
            let inChoices = false;
            text.split('\n').forEach(line => {
                const stripped = line.trim();
                // 「是否在 choices 环境内」按行推进；一行同时含起止时以靠后者为准。
                const lastBegin = stripped.lastIndexOf('\\begin{choices}');
                const lastEnd = stripped.lastIndexOf('\\end{choices}');
                if (lastBegin >= 0 || lastEnd >= 0) inChoices = lastBegin > lastEnd;
                const sameLine = PARSED_CHOICE_LABEL_RE.test(stripped);
                const belowLabel = PARSED_BARE_CHOICE_LABEL_RE.test(lastMeaningful);
                const onlyImage = PARSED_IMAGE_ONLY_RE.test(stripped);
                const choicesItem = inChoices && PARSED_CHOICES_ITEM_RE.test(stripped);
                if (sameLine || choicesItem || (belowLabel && onlyImage)) {
                    let match;
                    imgRe.lastIndex = 0;
                    while ((match = imgRe.exec(line)) !== null) {
                        const safePath = window.MathBankSafe.safeImageUrl(match[1]);
                        if (safePath && found.indexOf(safePath) < 0) found.push(safePath);
                    }
                }
                const remainder = line.replace(/!\[[^\]]*\]\([^)]*\)/g, '').trim();
                if (remainder) lastMeaningful = remainder;
            });
            return found;
        }

        // 选项横排（仅预览层，不改入库源码）：
        // MinerU/拆题产出的选项是「A 一段 / 图形一段 / B 一段 / …」的竖排段落，
        // 渲染成 <p> 后一行一个选项，在 A4 和预览里都极其浪费纵向空间。
        // 项目里本就有 .choices-grid 四列自适应网格（exam-zh choices 环境的
        // 预览等价物）+ 选项图角标装饰，但它们只认 \begin{choices} 源——
        // 这里在预览 DOM 上把连续的选项段落**重组**进同一个网格，让既有
        // 机器（adaptChoicesGridLayout / decorateParsedCardChoiceImages）接管。
        // 只重组 ≥3 个连续选项段（A/B/C 缺一不可），避免把「A、B 两点」这类
        // 普通正文误判成选项。
        function groupParsedCardOptionParagraphs(container) {
            if (!container || !container.children || container.children.length < 2) return;
            const classify = (el) => {
                if (el.tagName !== 'P' && el.tagName !== 'DIV') return 'other';
                const hasImg = !!(el.querySelector && el.querySelector('img'));
                const clone = el.cloneNode(true);
                Array.prototype.forEach.call(clone.querySelectorAll('img'), img => img.remove());
                const text = (clone.textContent || '').replace(/\s+/g, ' ').trim();
                if (hasImg && !text) return 'image-only';
                if (PARSED_BARE_CHOICE_LABEL_RE.test(text)) return 'bare-label';
                if (/^[ \t]*(?:[A-D]|[Ａ-Ｄ])[ \t]*[.．、,，:：)）]/.test(text)) return 'option';
                return 'other';
            };

            const runs = [];
            let run = [];
            let pendingLabel = null;
            const flush = () => {
                if (run.length >= 3) runs.push(run);
                run = [];
            };

            Array.prototype.slice.call(container.children).forEach(el => {
                const kind = classify(el);
                if (kind === 'bare-label') {
                    // ⚠️ 这里**不能** flush()：标号 A/B/C/D 是逐个遇到的，
                    // 每次遇到新标号就清空 run 会把刚攒下的选项全丢掉，
                    // 永远凑不满 3 个（node 沙箱测试抓出来的）。
                    pendingLabel = el;
                    return;
                }
                if (kind === 'image-only') {
                    if (pendingLabel) {
                        run.push({ labelEl: pendingLabel, bodyEl: el });
                        pendingLabel = null;
                        return;
                    }
                    const last = run[run.length - 1];
                    if (last) {
                        (last.extraEls = last.extraEls || []).push(el);
                        return;
                    }
                    flush();
                    return;
                }
                if (kind === 'option') {
                    pendingLabel = null;
                    run.push({ bodyEl: el });
                    return;
                }
                flush();
                pendingLabel = null;
            });
            flush();
            if (!runs.length) return;

            runs.forEach(items => {
                const anchor = items[0].labelEl || items[0].bodyEl;
                if (!anchor || !anchor.parentNode) return;
                const grid = document.createElement('div');
                grid.className = 'grid gap-2 my-2 select-none choices-grid items-baseline';
                grid.setAttribute('data-preferred-columns', '4');
                anchor.parentNode.insertBefore(grid, anchor);
                items.forEach(item => {
                    const cell = document.createElement('div');
                    if (item.labelEl) {
                        const label = (item.labelEl.textContent || '').trim();
                        cell.textContent = /[.．、,，:：)）]$/.test(label) ? label : label + '．';
                        item.labelEl.parentNode.removeChild(item.labelEl);
                    }
                    if (item.bodyEl) {
                        while (item.bodyEl.firstChild) cell.appendChild(item.bodyEl.firstChild);
                        item.bodyEl.parentNode.removeChild(item.bodyEl);
                    }
                    (item.extraEls || []).forEach(el => {
                        while (el.firstChild) cell.appendChild(el.firstChild);
                        el.parentNode.removeChild(el);
                    });
                    grid.appendChild(cell);
                });
            });
        }

        // 三槽归属：解答图 / 选项图 / 题干图。
        // 判定顺序有意义——解答优先（图在解答里就是解答图），其次选项图。
        function parsedCardImageSlot(path, answerText, contentText) {
            const safePath = window.MathBankSafe.safeImageUrl(path);
            if (!safePath) return 'content';
            if (parsedCardAnswerImagePaths(answerText).includes(safePath)) return 'answer';
            if (parsedCardChoiceImagePaths(contentText).includes(safePath)) return 'choice';
            return 'content';
        }

        // 把预览里的位图交给绘图模型重绘成 TikZ，编译通过后登记为**对应槽位**的 TikZ 资产
        // （题干 -> content_tikz_assets，解答 -> answer_tikz_assets）。
        // 原图会作为该资产的 reference_image_path 一并保存（生命周期由后端 image_paths 统一管理）。
        async function convertParsedCardImageToTikz(index, imagePath, slot) {
            const q = parsedQuestionsData[index];
            const card = document.getElementById('parsed-card-' + index);
            if (!q || !card) return;
            const safePath = window.MathBankSafe.safeImageUrl(imagePath);
            if (!safePath) {
                showToast('插图路径无效，无法转换。', 'warning');
                return;
            }
            const targetSlot = slot === 'answer' ? 'answer' : 'content';
            const assetsKey = targetSlot === 'answer'
                ? 'answer_tikz_assets'
                : 'content_tikz_assets';
            const slotLabel = targetSlot === 'answer' ? '解答' : '题干';

            const contentInput = card.querySelector('.card-content-textarea');
            const context = contentInput ? String(contentInput.value || '') : String(q.content || '');
            const instruction = '按参考图重绘为矢量 TikZ 几何图：忠实保留点名、线段、实线/虚线、'
                + '直角与等长标记以及已知条件标注，不要臆造参考图中不存在的信息。';

            const refreshPreview = () => {
                const contentNow = card.querySelector('.card-content-textarea');
                const answerNow = card.querySelector('.card-answer-textarea');
                renderParsedCardPreview(
                    card,
                    contentNow ? contentNow.value : '',
                    answerNow ? answerNow.value : ''
                );
            };

            // 先落「转换中」并立刻重绘：角标切成琥珀色「转换中…」、按钮消失，
            // 用户能看见确实在跑，也无法重复点击。
            setParsedCardTikzState(index, safePath, { status: 'converting' });
            refreshPreview();

            try {
                const drawForm = new FormData();
                drawForm.append('instruction', instruction);
                drawForm.append('context', context);
                drawForm.append('reference_image_path', imagePath);
                drawForm.append('image_kind', 'auto');
                const drawRes = await fetch('/api/ai/draw_tikz', {
                    method: 'POST',
                    headers: { 'X-Local-Token': parsedCardLocalToken() },
                    body: drawForm
                });
                const drawData = await drawRes.json().catch(() => ({}));
                if (!drawRes.ok || drawData.status !== 'success' || !drawData.tikz_code) {
                    throw new Error(
                        drawData.detail || drawData.message || ('HTTP ' + drawRes.status)
                    );
                }

                const compileForm = new FormData();
                compileForm.append('tikz_code', String(drawData.tikz_code));
                const compileRes = await fetch('/api/render_tikz', {
                    method: 'POST',
                    headers: { 'X-Local-Token': parsedCardLocalToken() },
                    body: compileForm
                });
                const compileData = await compileRes.json().catch(() => ({}));
                const compiledPath = window.MathBankSafe.safeImageUrl(compileData.image_path);
                if (!compileRes.ok || !compiledPath) {
                    throw new Error(
                        compileData.detail || 'TikZ 编译失败，请检查 XeLaTeX 运行时是否可用。'
                    );
                }

                const assets = Array.isArray(q[assetsKey])
                    ? q[assetsKey].slice()
                    : [];
                // 同一张位图被重复点击（题干条与解答区各有一个入口）时原地更新，
                // 避免登记出多份指向同一参考图的重复资产。
                const existingIndex = assets.findIndex(
                    asset => window.MathBankSafe.safeImageUrl(
                        asset && asset.reference_image_path
                    ) === safePath
                );
                const updatedAsset = {
                    id: (existingIndex >= 0 && assets[existingIndex].id)
                        ? assets[existingIndex].id
                        : (targetSlot === 'answer' ? 'answer_tikz_' : 'content_tikz_')
                            + Date.now().toString(36)
                            + Math.random().toString(36).slice(2, 6),
                    tikz_code: String(drawData.tikz_code),
                    instruction: instruction,
                    image_path: compiledPath,
                    reference_image_path: safePath
                };
                if (existingIndex >= 0) {
                    assets[existingIndex] = updatedAsset;
                } else {
                    assets.push(updatedAsset);
                }
                q[assetsKey] = assets;
                // 成功是持久事实（assets 里已有这张参考图），瞬时状态可以撤掉。
                clearParsedCardTikzState(index, safePath);
                showToast(
                    '已重绘为 TikZ 矢量图（登记到' + slotLabel + '），随本题保存后一并入库。',
                    'success'
                );
            } catch (err) {
                // 失败必须留下可见痕迹：否则角标退回「位图」，用户会以为从没转过。
                const message = err && err.message ? err.message : '未知错误';
                setParsedCardTikzState(index, safePath, { status: 'failed', message: message });
                showToast('转 TikZ 失败：' + message, 'error');
            } finally {
                refreshPreview();
            }
        }
        window.convertParsedCardImageToTikz = convertParsedCardImageToTikz;
        window.describeQuestionImageSource = describeQuestionImageSource;

        // 根据当前题卡的内容与解析，直接让 AI 生成一幅合适的 TikZ 示意图/几何图。
        // 无参考图，纯文本上下文驱动；生成成功后登记为 content_tikz_assets。
        async function generateTikZForQuestion(index) {
            const q = parsedQuestionsData[index];
            const card = document.getElementById('parsed-card-' + index);
            if (!q || !card) return;

            const contentInput = card.querySelector('.card-content-textarea');
            const answerInput = card.querySelector('.card-answer-textarea');
            const content = contentInput ? String(contentInput.value || '') : String(q.content || '');
            const answer = answerInput ? String(answerInput.value || '') : String(q.answer_markdown || '');

            const contextParts = ['【题干】\n' + content];
            if (answer.trim()) contextParts.push('\n【解析】\n' + answer);
            const context = contextParts.join('\n');
            const instruction = '请根据上述题干与解析，绘制一幅清晰、准确、符合题意的几何示意图或函数图像。'
                + '只输出可直接用 XeLaTeX 编译的 TikZ 代码（包含 \\begin{tikzpicture}...\\end{tikzpicture}），不要添加任何额外说明。';

            const btn = card.querySelector('.card-tikz-draw-btn');
            const originalHtml = btn ? btn.innerHTML : '';
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>绘图中…</span>';
            }

            try {
                const drawForm = new FormData();
                drawForm.append('instruction', instruction);
                drawForm.append('context', context);
                drawForm.append('image_kind', 'auto');
                const drawRes = await fetch('/api/ai/draw_tikz', {
                    method: 'POST',
                    headers: { 'X-Local-Token': parsedCardLocalToken() },
                    body: drawForm
                });
                const drawData = await drawRes.json().catch(() => ({}));
                if (!drawRes.ok || drawData.status !== 'success' || !drawData.tikz_code) {
                    throw new Error(drawData.detail || drawData.message || ('HTTP ' + drawRes.status));
                }

                const compileForm = new FormData();
                compileForm.append('tikz_code', String(drawData.tikz_code));
                const compileRes = await fetch('/api/render_tikz', {
                    method: 'POST',
                    headers: { 'X-Local-Token': parsedCardLocalToken() },
                    body: compileForm
                });
                const compileData = await compileRes.json().catch(() => ({}));
                const compiledPath = window.MathBankSafe.safeImageUrl(compileData.image_path);
                if (!compileRes.ok || !compiledPath) {
                    throw new Error(compileData.detail || 'TikZ 编译失败，请检查 XeLaTeX 运行时是否可用。');
                }

                // 「TikZ 绘图」是为本题重新绘制一幅示意图。
                // 这里只把源码登记到 content_tikz_assets，题干文本中只替换/追加 markdown
                // 图片引用；绝不在题干里塞入 \begin{tikzpicture} 源码（否则预览会把它
                // 当普通文字显示）。同时必须识别 A/B/C/D 选项里的图片，避免把选项图误删。
                const tikzSource = String(drawData.tikz_code || '').trim();

                function _isChoiceImageRef(text, matchIndex) {
                    const lineStart = text.lastIndexOf('\n', matchIndex - 1) + 1;
                    const lineEnd = text.indexOf('\n', matchIndex);
                    const line = text.slice(lineStart, lineEnd === -1 ? undefined : lineEnd);
                    // 图片所在整行以 A./B./C./D. 开头，即视为选项图，绝不替换
                    return /^\s*[A-D][.．、]/.test(line);
                }

                let currentText = contentInput ? String(contentInput.value || '') : String(q.content || '');
                const imgRegex = /!\[[^\]]*\]\(([^)]+)\)/g;
                let firstNonChoiceImg = null;
                let m;
                while ((m = imgRegex.exec(currentText)) !== null) {
                    if (!_isChoiceImageRef(currentText, m.index)) {
                        firstNonChoiceImg = m;
                        break;
                    }
                }

                const newImgRef = `![AI 重绘图](${compiledPath})`;
                const oldPath = firstNonChoiceImg ? firstNonChoiceImg[1] : null;
                const oldSafePath = oldPath ? window.MathBankSafe.safeImageUrl(oldPath) : null;

                if (firstNonChoiceImg) {
                    currentText = currentText.slice(0, firstNonChoiceImg.index)
                        + newImgRef
                        + currentText.slice(firstNonChoiceImg.index + firstNonChoiceImg[0].length);
                } else {
                    currentText = currentText.trimEnd() + '\n\n' + newImgRef;
                }

                // 双轨原则：原图作为保底资产永不删除，仅追加 TikZ 渲染结果。
                // image_paths 是图片文件生命周期唯一依据，保留原图可保证随时回退显示。
                q.image_paths = Array.isArray(q.image_paths) ? q.image_paths.map(p => window.MathBankSafe.safeImageUrl(p)) : [];
                if (oldSafePath && !q.image_paths.includes(oldSafePath)) {
                    q.image_paths.push(oldSafePath);
                }
                if (!q.image_paths.includes(compiledPath)) {
                    q.image_paths.push(compiledPath);
                }

                // 登记/更新 TikZ 资产
                const assets = Array.isArray(q.content_tikz_assets) ? q.content_tikz_assets.slice() : [];
                const existingIndex = oldSafePath
                    ? assets.findIndex(asset => window.MathBankSafe.safeImageUrl(
                        asset && asset.reference_image_path
                    ) === oldSafePath)
                    : -1;
                const updatedAsset = {
                    id: (existingIndex >= 0 && assets[existingIndex].id)
                        ? assets[existingIndex].id
                        : 'content_tikz_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                    tikz_code: tikzSource,
                    instruction: instruction,
                    image_path: compiledPath,
                    reference_image_path: oldSafePath || ''
                };
                if (existingIndex >= 0) {
                    assets[existingIndex] = updatedAsset;
                } else {
                    assets.push(updatedAsset);
                }
                q.content_tikz_assets = assets;

                if (contentInput) {
                    contentInput.value = currentText;
                }
                q.content = currentText;
                showToast('TikZ 绘图成功，已替换本题题干图。', 'success');
            } catch (err) {
                showToast('TikZ 绘图失败：' + (err && err.message ? err.message : '未知错误'), 'error');
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = originalHtml;
                }
                renderParsedCardPreview(
                    card,
                    contentInput ? contentInput.value : '',
                    answerInput ? answerInput.value : ''
                );
            }
        }
        window.generateTikZForQuestion = generateTikZForQuestion;

        // ---- 本卷配图总览：批量删水印 + 批量转 TikZ ----
        //
        // 删除一律是**软删除**：只把引用摘掉，不物理删文件。`image_paths` 是文件生命
        // 周期的唯一依据，摘掉引用后文件由后端既有的孤儿清理回收（临时图随任务过期、
        // 已入库的按 >1h 孤儿规则），用户因此保留反悔窗口。

        // 按「文件名」匹配并删掉正文里的图片引用。用 basename 而非整条路径，是因为
        // 同一张图在正文里可能写成 `/static/...`、`tmp/...` 或带 query 的不同形态。
        function stripParsedImageReference(text, safePath) {
            if (typeof text !== 'string' || !text || !safePath) return text;
            const base = String(safePath).split('?')[0].split('#')[0].split('/').pop();
            if (!base) return text;
            const escaped = base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            let out = text;
            out = out.replace(
                new RegExp('!\\[[^\\]]*\\]\\([^)]*' + escaped + '[^)]*\\)', 'g'),
                ''
            );
            out = out.replace(
                new RegExp('\\\\includegraphics(\\[[^\\]]*\\])?\\{[^}]*' + escaped + '\\}', 'g'),
                ''
            );
            return out.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+$/gm, '');
        }

        function dropParsedTikzAssetsFor(safePath, assets) {
            return (Array.isArray(assets) ? assets : []).filter(asset => (
                window.MathBankSafe.safeImageUrl(asset && asset.reference_image_path) !== safePath
            ));
        }

        function parsedCardImageHasTikz(index, safePath) {
            const q = parsedQuestionsData[index];
            if (!q) return false;
            return ['content_tikz_assets', 'answer_tikz_assets'].some(key => (
                (Array.isArray(q[key]) ? q[key] : []).some(asset => (
                    window.MathBankSafe.safeImageUrl(asset && asset.reference_image_path) === safePath
                ))
            ));
        }

        function parsedCardTikzStateSnapshot(index, safePath) {
            const store = parsedCardTikzStore();
            if (!store) return null;
            return store.get(parsedCardTikzStateKey(index, safePath)) || null;
        }

        // 软删除一张配图。三处必须同步，少一处就留破绽：
        //   ① image_paths —— 配图条展示与保存时的数据来源；
        //   ② 题干/解析 markdown（含编辑框）—— 否则解析里留下破图
        //      （后端 post_process 只清题干，解答里的引用是特意保留的）；
        //   ③ 指向它的 TikZ 资产 —— 否则资产的 reference_image_path 会悬空。
        function softDeleteParsedCardImage(index, safePath) {
            const q = parsedQuestionsData[index];
            if (!q || !safePath) return false;
            const paths = (Array.isArray(q.image_paths) ? q.image_paths : [])
                .map(p => window.MathBankSafe.safeImageUrl(p));
            if (paths.indexOf(safePath) < 0) return false;

            q.image_paths = paths.filter(p => p !== safePath);
            ['content', 'answer_markdown'].forEach(key => {
                if (typeof q[key] === 'string') {
                    q[key] = stripParsedImageReference(q[key], safePath);
                }
            });
            if (Array.isArray(q.referenced_images)) {
                q.referenced_images = q.referenced_images.filter(
                    ref => window.MathBankSafe.safeImageUrl(ref) !== safePath
                );
            }
            q.content_tikz_assets = dropParsedTikzAssetsFor(safePath, q.content_tikz_assets);
            q.answer_tikz_assets = dropParsedTikzAssetsFor(safePath, q.answer_tikz_assets);
            clearParsedCardTikzState(index, safePath);

            // 文本框才是保存时读取的数据来源，必须一起改，否则入库又把图带回来。
            const card = document.getElementById('parsed-card-' + index);
            if (card) {
                const contentInput = card.querySelector('.card-content-textarea');
                const answerInput = card.querySelector('.card-answer-textarea');
                if (contentInput) {
                    contentInput.value = stripParsedImageReference(contentInput.value, safePath);
                }
                if (answerInput) {
                    answerInput.value = stripParsedImageReference(answerInput.value, safePath);
                }
            }
            return true;
        }

        function parsedImageBasename(safePath) {
            if (!safePath) return '';
            return String(safePath).split('?')[0].split('#')[0].split('/').pop();
        }

        // 裁剪图文件名自带定位信息（pdf_crop_{task}_{page}_{idx}.png），
        // 用「页序_序号」数值排序才能得到稳定的文档顺序。
        function parsedImageDocumentOrder(safePath) {
            const hit = /_(\d+)_(\d+)\.\w+$/.exec(parsedImageBasename(safePath));
            return hit
                ? [parseInt(hit[1], 10), parseInt(hit[2], 10)]
                : [0, 0];
        }

        // 收集「已经裁出来、但没有任何题目引用」的临时插图。
        //   它们唯一登记在册的地方是任务快照的 temp_assets，所以只能靠轮询时缓存的
        //   window.pdfTempAssets 列出来；若该任务没渲染整页图（如 MinerU 结果包导入），
        //   这里会是空的 —— 面板会提示而不是假装「没有未归属图」。
        function collectParsedUnassignedImages() {
            const assets = Array.isArray(window.pdfTempAssets) ? window.pdfTempAssets : [];
            if (!assets.length) return [];
            const owned = {};
            parsedQuestionsData.forEach(q => {
                (Array.isArray(q && q.image_paths) ? q.image_paths : []).forEach(p => {
                    const base = parsedImageBasename(window.MathBankSafe.safeImageUrl(p));
                    if (base) owned[base] = true;
                });
            });
            const seen = {};
            const out = [];
            assets.forEach(raw => {
                const safe = window.MathBankSafe.safeImageUrl(raw);
                const base = parsedImageBasename(safe);
                // 排除整页原图（pdf_page_*）——它归「本卷原页」页签。
                // 其余已落盘图片（pdf_crop_* 裁剪图、mineru_* 云端抽图）都是候选插图。
                if (!base || base.indexOf('pdf_page_') === 0) return;
                if (owned[base] || seen[base]) return;
                seen[base] = true;
                out.push(safe);
            });
            return out.sort((a, b) => {
                const oa = parsedImageDocumentOrder(a);
                const ob = parsedImageDocumentOrder(b);
                return (oa[0] - ob[0]) || (oa[1] - ob[1]);
            });
        }

        function collectParsedPageImages() {
            const pages = Array.isArray(window.pdfPageImages) ? window.pdfPageImages : [];
            return pages
                .map(raw => window.MathBankSafe.safeImageUrl(raw))
                .filter(Boolean);
        }

        // 把「未归属」的临时插图指派给某一道题：只加进 q.image_paths，
        // 保存时沿用既有的「tmp → static/uploads 晋升」流程，无需新后端接口。
        function assignParsedUnassignedImages(index, safePaths) {
            const q = parsedQuestionsData[index];
            if (!q || !safePaths || !safePaths.length) return 0;
            const list = (Array.isArray(q.image_paths) ? q.image_paths : [])
                .map(p => window.MathBankSafe.safeImageUrl(p))
                .filter(Boolean);
            let added = 0;
            safePaths.forEach(raw => {
                const safe = window.MathBankSafe.safeImageUrl(raw);
                if (safe && list.indexOf(safe) < 0) {
                    list.push(safe);
                    added += 1;
                }
            });
            if (!added) return 0;
            q.image_paths = list;
            return added;
        }

        // 放弃「未归属图」：把它们从本次会话的资产清单里摘掉。
        // 它们没有被任何题目引用，摘掉即等于不再过问；遵循项目一贯的软删除原则，
        // **不物理删文件**，交给既有的孤儿清理回收（任务过期 / 根目录 >1h 孤儿规则）。
        function discardParsedUnassignedImages(safePaths) {
            const assets = Array.isArray(window.pdfTempAssets) ? window.pdfTempAssets : [];
            if (!assets.length || !safePaths || !safePaths.length) return 0;
            const doomed = {};
            safePaths.forEach(raw => {
                const base = parsedImageBasename(window.MathBankSafe.safeImageUrl(raw));
                if (base) doomed[base] = true;
            });
            const kept = assets.filter(raw => {
                const base = parsedImageBasename(window.MathBankSafe.safeImageUrl(raw));
                return !base || !doomed[base];
            });
            const removed = assets.length - kept.length;
            if (removed) window.pdfTempAssets = kept;
            return removed;
        }

        function collectParsedImageOverview() {
            const groups = [];
            parsedQuestionsData.forEach((q, index) => {
                const answerText = (q && typeof q.answer_markdown === 'string')
                    ? q.answer_markdown
                    : '';
                const contentText = (q && typeof q.content === 'string') ? q.content : '';
                const paths = (Array.isArray(q && q.image_paths) ? q.image_paths : [])
                    .map(p => window.MathBankSafe.safeImageUrl(p))
                    .filter(Boolean);
                groups.push({
                    index: index,
                    label: '第 ' + (index + 1) + ' 题',
                    sourcePage: (q && typeof q._source_page === 'number') ? q._source_page : -1,
                    items: paths.map(safePath => ({
                        index: index,
                        safePath: safePath,
                        slot: parsedCardImageSlot(safePath, answerText, contentText),
                        converted: parsedCardImageHasTikz(index, safePath)
                    }))
                });
            });
            return groups;
        }

        function parsedCardAssetFor(index, safePath) {
            const q = parsedQuestionsData[index];
            if (!q) return null;
            const keys = ['content_tikz_assets', 'answer_tikz_assets'];
            for (let i = 0; i < keys.length; i += 1) {
                const list = Array.isArray(q[keys[i]]) ? q[keys[i]] : [];
                const hit = list.find(asset => (
                    window.MathBankSafe.safeImageUrl(asset && asset.reference_image_path) === safePath
                ));
                if (hit) return hit;
            }
            return null;
        }

        let parsedImageOverviewEl = null;
        let parsedOverviewDeleteArmed = false;
        // 当前页签：gallery（所有配图一屏铺开）/ pages（本卷整页原图）
        let parsedOverviewTab = 'gallery';
        // 图墙下方默认把「没有配图的题」也列出来——用户的核心痛点是
        // 「我不知道这道题到底有没有图」，藏起来就没法一眼扫出缺图的题。
        let parsedOverviewShowEmpty = true;

        const OVERVIEW_DELETE_IDLE = 'px-3 py-1.5 rounded-lg text-[10px] font-bold border '
            + 'border-rose-200 bg-rose-50 text-rose-600 hover:bg-rose-100 transition-colors';
        const OVERVIEW_DELETE_ARMED = 'px-3 py-1.5 rounded-lg text-[10px] font-bold border '
            + 'border-rose-500 bg-rose-600 text-white hover:bg-rose-700 transition-colors';

        function closeParsedImageOverview() {
            const modal = parsedImageOverviewEl;
            parsedImageOverviewEl = null;
            parsedOverviewDeleteArmed = false;
            if (!modal) return;
            if (window.MathBankModal) window.MathBankModal.close(modal);
            if (modal.isConnected) modal.remove();
        }

        function parsedOverviewPicked(modal) {
            if (!modal) return [];
            return Array.prototype.slice
                .call(modal.querySelectorAll('input[data-overview-pick]'))
                .filter(box => box.checked && !box.disabled);
        }

        // 未归属图页签的「指派目标」下拉：列出全部题号，并顺带标出该题现有配图数。
        function fillParsedOverviewAssignTargets(select) {
            if (!select) return;
            const previous = select.value;
            select.innerHTML = '';
            parsedQuestionsData.forEach((q, index) => {
                const option = document.createElement('option');
                option.value = String(index);
                const count = (Array.isArray(q && q.image_paths) ? q.image_paths : []).length;
                option.textContent = '第 ' + (index + 1) + ' 题'
                    + (count ? '（现有 ' + count + ' 张）' : '（无配图）');
                select.appendChild(option);
            });
            if (previous && select.querySelector('option[value="' + previous + '"]')) {
                select.value = previous;
            }
        }

        function refreshParsedImageOverviewSelection(modal, options) {
            if (!modal) return;
            const keepHint = !!(options && options.keepHint);
            // 任何一次勾选变化都退回未武装状态，避免「上一步的确认」被误继承到新选择上。
            parsedOverviewDeleteArmed = false;
            const picked = parsedOverviewPicked(modal);
            const boxCount = modal.querySelectorAll('input[data-overview-pick]').length;
            const isPages = parsedOverviewTab === 'pages';
            // 图墙里「已归属」与「未归属」的图混在一起，按**勾选内容**决定给哪些操作：
            // 已归属 → 删除 / 转 TikZ；未归属 → 指派给某题（它没有归属题，转 TikZ 无处安放）。
            const pickedIndexes = picked.map(box => parseInt(box.getAttribute('data-index'), 10));
            const assignedCount = pickedIndexes.filter(idx => idx >= 0).length;
            const unassignedCount = pickedIndexes.filter(idx => idx < 0).length;
            const hasAssigned = assignedCount > 0;
            const hasUnassigned = unassignedCount > 0;

            const stat = modal.querySelector('#parsedOverviewStat');
            if (stat) {
                stat.textContent = '已选 ' + picked.length + ' 张';
                stat.classList.toggle('hidden', isPages);
            }
            const selectAllWrap = modal.querySelector('#parsedOverviewSelectAllWrap');
            if (selectAllWrap) {
                selectAllWrap.classList.toggle('hidden', isPages || boxCount === 0);
            }
            const showEmptyWrap = modal.querySelector('#parsedOverviewShowEmptyWrap');
            if (showEmptyWrap) {
                showEmptyWrap.classList.toggle('hidden', isPages);
            }
            const note = modal.querySelector('#parsedOverviewToolbarNote');
            if (note) {
                note.textContent = isPages
                    ? '点缩略图看大图；对照原页核对每道题的配图有没有漏'
                    : '删除是软删除：只摘引用、不物理删文件';
            }

            const deleteBtn = modal.querySelector('#parsedOverviewDeleteBtn');
            if (deleteBtn) {
                deleteBtn.disabled = !hasAssigned && !hasUnassigned;
                deleteBtn.className = (!hasAssigned && !hasUnassigned)
                    ? OVERVIEW_DELETE_IDLE + ' opacity-40 cursor-not-allowed'
                    : OVERVIEW_DELETE_IDLE;
                deleteBtn.textContent = '删除选中';
                deleteBtn.classList.toggle('hidden', isPages);
            }
            const convertBtn = modal.querySelector('#parsedOverviewConvertBtn');
            if (convertBtn) {
                convertBtn.disabled = !hasAssigned;
                convertBtn.classList.toggle('opacity-40', !hasAssigned);
                convertBtn.textContent = hasAssigned
                    ? ('批量转 TikZ (' + assignedCount + ')')
                    : '批量转 TikZ';
                convertBtn.classList.toggle('hidden', isPages || !hasAssigned);
            }
            const assignSelect = modal.querySelector('#parsedOverviewAssignTarget');
            if (assignSelect) {
                assignSelect.classList.toggle('hidden', !hasUnassigned);
                if (hasUnassigned) fillParsedOverviewAssignTargets(assignSelect);
            }
            const assignBtn = modal.querySelector('#parsedOverviewAssignBtn');
            if (assignBtn) {
                assignBtn.disabled = !hasUnassigned;
                assignBtn.classList.toggle('opacity-40', !hasUnassigned);
                assignBtn.textContent = hasUnassigned
                    ? ('指派给该题 (' + unassignedCount + ')')
                    : '指派给该题';
                assignBtn.classList.toggle('hidden', isPages || !hasUnassigned);
            }

            if (!keepHint) {
                const hint = modal.querySelector('#parsedOverviewHint');
                if (hint) {
                    hint.className = 'text-[10px] font-semibold text-slate-500';
                    hint.textContent = '';
                }
            }
        }

        function openParsedImageOverview() {
            const hasImages = collectParsedImageOverview().some(group => group.items.length);
            const hasUnassigned = collectParsedUnassignedImages().length > 0;
            const hasPages = collectParsedPageImages().length > 0;
            if (!hasImages && !hasUnassigned && !hasPages) {
                showToast('本卷没有识别到任何图片，无需审图。', 'info');
                return;
            }
            // 打开时落在最有信息的页签：有图就铺图墙，否则直接看原页。
            parsedOverviewTab = (hasImages || hasUnassigned) ? 'gallery' : 'pages';
            closeParsedImageOverview();
            const modal = document.createElement('div');
            modal.id = 'parsedImageOverviewModal';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-label', '本卷配图总览');
            modal.className = 'fixed inset-0 glass-modal-overlay z-50 flex items-center justify-center';
            modal.innerHTML = `
                <div class="glass-modal rounded-2xl w-[94vw] max-w-5xl max-h-[88vh] flex flex-col overflow-hidden">
                    <div class="flex items-center justify-between border-b border-slate-200/60 px-5 py-3">
                        <h3 class="text-sm font-bold text-slate-700 flex items-center gap-2">
                            <i class="fa-solid fa-images text-indigo-500"></i>
                            <span>本卷配图总览</span>
                            <span id="parsedOverviewCount" class="text-[10px] font-bold px-2 py-0.5 rounded bg-indigo-50 text-indigo-600 border border-indigo-100">共 0 张</span>
                        </h3>
                        <button type="button" id="parsedOverviewCloseBtn" aria-label="关闭配图总览" class="w-8 h-8 flex items-center justify-center rounded-xl text-slate-400 hover:text-slate-600 transition-all">
                            <i class="fa-solid fa-xmark text-lg"></i>
                        </button>
                    </div>
                    <div id="parsedOverviewTabs" class="flex items-center gap-1 border-b border-slate-200/60 px-5 pt-2">
                        <button type="button" data-overview-tab="gallery" class="px-2.5 py-1.5 rounded-t-lg text-[10px] font-bold border-b-2 transition-colors">全部配图</button>
                        <button type="button" data-overview-tab="pages" class="px-2.5 py-1.5 rounded-t-lg text-[10px] font-bold border-b-2 transition-colors">本卷原页</button>
                    </div>
                    <div id="parsedOverviewToolbar" class="flex flex-wrap items-center gap-3 border-b border-slate-200/60 px-5 py-2">
                        <label id="parsedOverviewSelectAllWrap" class="flex items-center gap-1.5 text-[10px] font-semibold text-slate-500 cursor-pointer select-none">
                            <input type="checkbox" id="parsedOverviewSelectAll" class="h-3.5 w-3.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500">
                            <span>全选</span>
                        </label>
                        <span id="parsedOverviewStat" class="text-[10px] font-bold text-slate-400 bg-slate-100/80 px-1.5 py-0.5 rounded border border-slate-200">已选 0 张</span>
                        <label id="parsedOverviewShowEmptyWrap" class="flex items-center gap-1.5 text-[10px] font-semibold text-slate-500 cursor-pointer select-none">
                            <input type="checkbox" id="parsedOverviewShowEmpty" class="h-3.5 w-3.5 rounded border-slate-300 text-brand-600 focus:ring-brand-500" checked>
                            <span>列出没有配图的题</span>
                        </label>
                        <span id="parsedOverviewToolbarNote" class="text-[10px] text-slate-400">删除是软删除：只摘引用、不物理删文件</span>
                    </div>
                    <div id="parsedOverviewList" class="flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-4 custom-scrollbar"></div>
                    <div class="flex items-center justify-between gap-3 border-t border-slate-200/60 px-5 py-3">
                        <p id="parsedOverviewHint" class="text-[10px] font-semibold text-slate-500"></p>
                        <div class="flex items-center gap-2 shrink-0">
                            <select id="parsedOverviewAssignTarget" class="hidden h-7 px-2 rounded-lg text-[10px] border border-slate-200 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 transition-all"></select>
                            <button type="button" id="parsedOverviewAssignBtn" class="hidden px-3 py-1.5 rounded-lg text-[10px] font-bold border border-brand-200 bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors">指派给该题</button>
                            <button type="button" id="parsedOverviewDeleteBtn" class="px-3 py-1.5 rounded-lg text-[10px] font-bold border border-rose-200 bg-rose-50 text-rose-600 hover:bg-rose-100 transition-colors">删除选中</button>
                            <button type="button" id="parsedOverviewConvertBtn" class="px-3 py-1.5 rounded-lg text-[10px] font-bold border border-indigo-200 bg-indigo-50 text-indigo-600 hover:bg-indigo-100 transition-colors">批量转 TikZ</button>
                        </div>
                    </div>
                </div>`;
            document.body.appendChild(modal);
            if (window.MathBankModal) {
                window.MathBankModal.open(modal, { onEscape: closeParsedImageOverview });
            }
            parsedImageOverviewEl = modal;

            modal.querySelector('#parsedOverviewCloseBtn')
                .addEventListener('click', closeParsedImageOverview);
            modal.querySelector('#parsedOverviewSelectAll')
                .addEventListener('change', function () {
                    Array.prototype.forEach.call(
                        modal.querySelectorAll('input[data-overview-pick]'),
                        box => { if (!box.disabled) box.checked = this.checked; }
                    );
                    refreshParsedImageOverviewSelection(modal);
                });
            modal.querySelector('#parsedOverviewShowEmpty')
                .addEventListener('change', function () {
                    parsedOverviewShowEmpty = this.checked;
                    refreshParsedImageOverviewSelection(modal);
                });
            Array.prototype.forEach.call(
                modal.querySelectorAll('[data-overview-tab]'),
                tabBtn => tabBtn.addEventListener('click', function () {
                    renderParsedImageOverviewGroups(
                        modal,
                        this.getAttribute('data-overview-tab')
                    );
                })
            );
            modal.querySelector('#parsedOverviewAssignBtn')
                .addEventListener('click', () => handleParsedUnassignedAssign(modal));
            modal.querySelector('#parsedOverviewDeleteBtn')
                .addEventListener('click', () => handleParsedImageOverviewDelete(modal));
            modal.querySelector('#parsedOverviewConvertBtn')
                .addEventListener('click', () => handleParsedImageOverviewConvert(modal));

            renderParsedImageOverviewGroups(modal, parsedOverviewTab);
        }
        window.openParsedImageOverview = openParsedImageOverview;

        // 从题目卡片直接跳到该题「疑似来源页」的原图。
        // 这是回答「我不知道这道题到底有没有图」最直接的一步：一键看到原页，
        // 就能判断是模型漏配了图，还是这道题本来就没图。
        function openParsedQuestionSourcePage(index) {
            const q = parsedQuestionsData[index];
            const pageIdx = (q && typeof q._source_page === 'number') ? q._source_page : -1;
            if (pageIdx < 0) {
                showToast('这道题没能定位到原页（本次导入没有逐页文本可依据）。', 'info');
                return;
            }
            if (!collectParsedPageImages().length) {
                showToast('本任务没有可用的整页原图，无法对照。', 'info');
                return;
            }
            openParsedImageOverview();
            const modal = parsedImageOverviewEl;
            if (!modal) return;
            focusParsedOverviewPage(pageIdx);
        }
        window.openParsedQuestionSourcePage = openParsedQuestionSourcePage;

        function buildParsedOverviewTile(item, options) {
            const opts = options || {};
            const asset = parsedCardAssetFor(item.index, item.safePath);
            const convertedPath = window.MathBankSafe.safeImageUrl(asset && asset.image_path);
            const alreadyTikz = Boolean(convertedPath);
            const info = describeQuestionImageBadge(item.index, item.safePath, alreadyTikz);
            const inFlight = info.tone === 'converting';
            // 未归属图没有归属题，转 TikZ 也无处安放 → 不给转换入口。
            const allowConvert = opts.allowConvert !== false;

            const tile = document.createElement('div');
            // 宽度交给图墙的 grid 控制（自适应列数），未归属用琥珀边框一眼区分。
            tile.className = 'w-full rounded-lg border bg-white overflow-hidden shadow-sm '
                + 'flex flex-col '
                + (opts.unassigned ? 'border-amber-300' : 'border-slate-200');

            const head = document.createElement('label');
            head.className = 'flex items-center gap-1 px-1.5 py-1 text-[9px] font-semibold '
                + 'text-slate-500 cursor-pointer select-none';
            const box = document.createElement('input');
            box.type = 'checkbox';
            box.className = 'h-3 w-3 rounded border-slate-300 text-brand-600 focus:ring-brand-500';
            box.setAttribute('data-overview-pick', '1');
            box.setAttribute('data-index', String(item.index));
            box.setAttribute('data-path', item.safePath);
            box.setAttribute('data-slot', item.slot);
            box.disabled = inFlight;
            box.addEventListener('change', function () {
                refreshParsedImageOverviewSelection(this.closest('#parsedImageOverviewModal'));
            });
            head.appendChild(box);
            // 图墙上直接标出这张图属于哪道题、哪个槽位 —— 用户要的就是「一眼看出哪张是哪题的」。
            // 选项图必须和题干图区分开：它属于某个选项，不是题干的配图。
            const slotName = item.slot === 'answer'
                ? '解答图'
                : (item.slot === 'choice' ? '选项图' : '题干图');
            const slotText = document.createElement('span');
            slotText.className = 'truncate';
            slotText.textContent = item.index >= 0
                ? ('第 ' + (item.index + 1) + ' 题 · ' + slotName)
                : (opts.slotLabel || '未归属');
            slotText.title = slotText.textContent;
            head.appendChild(slotText);
            tile.appendChild(head);

            const figure = document.createElement('div');
            figure.className = 'relative aspect-[4/3] flex items-center justify-center bg-white';
            const badge = document.createElement('span');
            badge.className = 'absolute top-0.5 left-0.5 px-1 py-[1px] rounded text-[8px] font-bold '
                + (opts.badgeLabel ? 'bg-amber-600/90 text-white' : tikzBadgeToneClass(info.tone));
            badge.textContent = opts.badgeLabel || info.label;
            badge.title = opts.badgeLabel ? (opts.badgeTitle || opts.badgeLabel) : info.title;
            figure.appendChild(badge);
            const img = document.createElement('img');
            img.src = alreadyTikz ? convertedPath : item.safePath;
            img.className = 'max-h-full max-w-full object-contain cursor-zoom-in';
            img.setAttribute('data-safe-image-open', 'true');
            img.title = '点击在新标签页中查看大图';
            figure.appendChild(img);
            tile.appendChild(figure);

            const action = document.createElement('div');
            action.className = 'px-1 py-0.5 text-[8px] font-semibold text-center';
            if (!allowConvert) {
                action.className += ' text-amber-700';
                action.textContent = '未归属（无题目引用）';
            } else if (alreadyTikz) {
                action.className += ' text-indigo-600';
                action.textContent = '已转为 TikZ';
            } else if (inFlight) {
                action.className += ' text-amber-600';
                action.textContent = '转换中…';
            } else {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'w-full py-1 text-[9px] font-bold text-indigo-600 bg-indigo-50 '
                    + 'hover:bg-indigo-100 rounded transition-colors';
                button.textContent = (info.tone === 'failed') ? '重新转换' : '转 TikZ';
                button.title = (info.tone === 'failed')
                    ? info.title
                    : '用 AI 把这张图重绘成 TikZ 矢量图';
                button.addEventListener('click', function () {
                    const modal = button.closest('#parsedImageOverviewModal');
                    convertParsedCardImageToTikz(item.index, item.safePath, item.slot).then(() => {
                        renderParsedQuestionsList(parsedQuestionsData);
                        renderParsedImageOverviewGroups(modal);
                    });
                });
                action.appendChild(button);
            }
            tile.appendChild(action);
            return tile;
        }

        function renderParsedImageOverviewGroups(modal, tab) {
            if (!modal) return;
            if (tab) parsedOverviewTab = tab;
            const list = modal.querySelector('#parsedOverviewList');
            if (!list) return;
            list.innerHTML = '';

            Array.prototype.forEach.call(
                modal.querySelectorAll('[data-overview-tab]'),
                btn => {
                    const active = btn.getAttribute('data-overview-tab') === parsedOverviewTab;
                    btn.className = 'px-2.5 py-1.5 rounded-t-lg text-[10px] font-bold '
                        + 'border-b-2 transition-colors '
                        + (active
                            ? 'border-brand-500 text-brand-700 bg-brand-50/60'
                            : 'border-transparent text-slate-400 hover:text-slate-600');
                }
            );

            let total = 0;
            if (parsedOverviewTab === 'pages') {
                total = renderParsedOverviewPagesTab(list);
            } else {
                total = renderParsedOverviewGalleryTab(list);
            }

            const countBadge = modal.querySelector('#parsedOverviewCount');
            if (countBadge) countBadge.textContent = '共 ' + total + ' 张';
            refreshParsedImageOverviewSelection(modal, { keepHint: true });
            applyParsedOverviewHint(modal);
        }

        function applyParsedOverviewHint(modal) {
            const hint = modal.querySelector('#parsedOverviewHint');
            if (!hint) return;
            hint.className = 'text-[10px] font-semibold text-slate-500';
            hint.textContent = parsedOverviewTab === 'pages'
                ? '整页原图来自本次导入所渲染的 PDF 页面；若这里为空，'
                    + '说明本次走的导入方式不含源 PDF。'
                : '未归属图不影响入库结果：勾选后可指派给某道题，或直接删除放弃。';
        }

        // 「全部配图」页签：把所有配图（含未归属）铺成**一面图墙**，一屏扫完。
        // 用户明确要求过：不要按题分组的列表（得逐个点开），要一眼看清全部图。
        // 每张图下/上直接标「第 N 题 · 题干图」或「未归属」，未归属用琥珀边区分。
        function renderParsedOverviewGalleryTab(list) {
            const groups = collectParsedImageOverview();
            const assigned = [];
            groups.forEach(group => {
                group.items.forEach(item => assigned.push(item));
            });
            const unassigned = collectParsedUnassignedImages();
            const withImages = groups.filter(group => group.items.length).length;
            const without = groups.length - withImages;
            const total = assigned.length + unassigned.length;

            const summary = document.createElement('p');
            summary.className = 'text-[10px] font-semibold text-slate-500 bg-slate-50 '
                + 'border border-slate-200 rounded-lg px-3 py-2 leading-relaxed';
            summary.textContent = '共 ' + total + ' 张图（含 ' + unassigned.length + ' 张未归属）'
                + '；' + groups.length + ' 题中有配图 ' + withImages + ' 题，无配图 ' + without + ' 题。'
                + '点缩略图可看大图。';
            list.appendChild(summary);

            if (!total) {
                const empty = document.createElement('p');
                empty.className = 'py-10 text-center text-xs italic text-slate-400';
                empty.textContent = '本卷没有任何配图。';
                list.appendChild(empty);
            } else {
                // 自适应列宽：窗口越宽铺得越多；200px 下限保证缩略图能看清图形。
                const wall = document.createElement('div');
                wall.className = 'grid gap-3';
                wall.style.gridTemplateColumns = 'repeat(auto-fill, minmax(200px, 1fr))';
                assigned.forEach(item => wall.appendChild(buildParsedOverviewTile(item)));
                unassigned.forEach(safePath => wall.appendChild(buildParsedOverviewTile(
                    { index: -1, safePath: safePath, slot: 'content', converted: false },
                    {
                        allowConvert: false,
                        unassigned: true,
                        badgeLabel: '未归属',
                        badgeTitle: '已裁出/抽出，但没有任何题目引用这张图'
                    }
                )));
                list.appendChild(wall);
            }

            // 没有配图的题单独收在图墙下方（保留「查看第 N 页」入口），
            // 这样「一屏看全所有图」和「一眼扫出缺图的题」都不牺牲。
            if (without && parsedOverviewShowEmpty) {
                const head = document.createElement('p');
                head.className = 'text-[10px] font-bold text-slate-500 pt-1';
                head.textContent = '以下 ' + without + ' 道题没有配图，可对照原页确认是否漏图：';
                list.appendChild(head);
                const rows = document.createElement('div');
                rows.className = 'grid gap-2';
                rows.style.gridTemplateColumns = 'repeat(auto-fill, minmax(260px, 1fr))';
                groups.forEach(group => {
                    if (!group.items.length) rows.appendChild(buildParsedOverviewEmptyRow(group));
                });
                list.appendChild(rows);
            }
            return total;
        }

        // 没有配图的题：标出拆题阶段推断的来源页，并给一个直接跳到原页的入口。
        // 这是回答「我不知道这道题到底有没有图」最省事的一步。
        function buildParsedOverviewEmptyRow(group) {
            const row = document.createElement('div');
            row.className = 'flex flex-wrap items-center gap-2 text-[10px] rounded-lg '
                + 'border border-dashed border-amber-200 bg-amber-50/50 px-2.5 py-1.5';
            const label = document.createElement('span');
            label.className = 'font-bold text-amber-700';
            label.textContent = '无配图';
            row.appendChild(label);

            const note = document.createElement('span');
            note.className = 'text-slate-500';
            const pages = collectParsedPageImages();
            const pageIdx = group.sourcePage;
            if (pageIdx >= 0 && pages[pageIdx]) {
                note.textContent = '疑似来自第 ' + (pageIdx + 1) + ' 页';
                row.appendChild(note);
                const jump = document.createElement('button');
                jump.type = 'button';
                jump.className = 'px-2 py-0.5 rounded font-bold border border-brand-200 '
                    + 'bg-white text-brand-700 hover:bg-brand-50 transition-colors';
                jump.textContent = '查看第 ' + (pageIdx + 1) + ' 页';
                jump.title = '跳到「本卷原页」页签的对应页，对照原页确认这题是否本该有图';
                jump.addEventListener('click', () => focusParsedOverviewPage(pageIdx));
                row.appendChild(jump);
            } else {
                note.textContent = pages.length
                    ? '未能定位到具体页（本次导入没有逐页文本），请到「本卷原页」自行翻页核对'
                    : '与原页文本对不上，拆题阶段未能定位来源页';
                row.appendChild(note);
            }
            return row;
        }

        // 从「配图」页签跳到「本卷原页」的指定页，并高亮一下。
        function focusParsedOverviewPage(pageIdx) {
            const modal = parsedImageOverviewEl;
            if (!modal) return;
            renderParsedImageOverviewGroups(modal, 'pages');
            const target = modal.querySelector('[data-overview-page="' + pageIdx + '"]');
            if (!target) return;
            if (typeof target.scrollIntoView === 'function') {
                target.scrollIntoView({ block: 'start' });
            }
            target.classList.add('ring-2', 'ring-brand-400');
            setTimeout(() => target.classList.remove('ring-2', 'ring-brand-400'), 1800);
        }

        // 「本卷原页」页签：整页原图 + 每页疑似归属的题号，供人工逐页核对。
        function renderParsedOverviewPagesTab(list) {
            const pages = collectParsedPageImages();
            if (!pages.length) {
                const box = document.createElement('div');
                box.className = 'py-10 text-center space-y-1';
                const line1 = document.createElement('p');
                line1.className = 'text-xs font-semibold text-slate-500';
                line1.textContent = '本任务没有可用的整页原图';
                box.appendChild(line1);
                const line2 = document.createElement('p');
                line2.className = 'text-[11px] text-slate-400 leading-relaxed';
                line2.textContent = '整页图只在「上传 PDF 解析」时渲染（三种录入方式都支持）。'
                    + '若这里为空，说明本次任务较旧、整页图已过期清理，重新导入一次即可。';
                box.appendChild(line2);
                list.appendChild(box);
                return 0;
            }

            const byPage = {};
            collectParsedImageOverview().forEach(group => {
                if (group.sourcePage >= 0) {
                    if (!byPage[group.sourcePage]) byPage[group.sourcePage] = [];
                    byPage[group.sourcePage].push(group.index + 1);
                }
            });

            const summary = document.createElement('p');
            summary.className = 'text-[10px] font-semibold text-slate-500 bg-slate-50 '
                + 'border border-slate-200 rounded-lg px-3 py-2 leading-relaxed';
            summary.textContent = '共 ' + pages.length + ' 页。页下标注「疑似含第 N 题」，'
                + '可与页面上的图形逐个对照，判断有没有哪道题被漏配图。';
            list.appendChild(summary);

            pages.forEach((safePath, pageIdx) => {
                const section = document.createElement('div');
                section.className = 'space-y-1.5 rounded-xl transition-all';
                section.setAttribute('data-overview-page', String(pageIdx));
                const title = document.createElement('div');
                title.className = 'text-[11px] font-bold text-slate-600';
                const who = byPage[pageIdx] || [];
                title.textContent = '第 ' + (pageIdx + 1) + ' 页'
                    + (who.length ? ' · 疑似含第 ' + who.join('、') + ' 题' : ' · 未定位到题目');
                section.appendChild(title);
                const figure = document.createElement('div');
                figure.className = 'rounded-lg border border-slate-200 bg-white overflow-hidden';
                const img = document.createElement('img');
                img.src = safePath;
                img.className = 'w-full cursor-zoom-in';
                img.setAttribute('data-safe-image-open', 'true');
                img.loading = 'lazy';
                img.title = '点击在新标签页中查看大图';
                figure.appendChild(img);
                section.appendChild(figure);
                list.appendChild(section);
            });
            return pages.length;
        }

        function handleParsedUnassignedAssign(modal) {
            const picked = parsedOverviewPicked(modal);
            if (!picked.length) return;
            const select = modal.querySelector('#parsedOverviewAssignTarget');
            const index = select ? parseInt(select.value, 10) : NaN;
            if (!(index >= 0)) {
                showToast('请先在右下角选择要指派到哪一道题。', 'warning');
                return;
            }
            const paths = picked
                .map(box => box.getAttribute('data-path'))
                .filter(Boolean);
            const added = assignParsedUnassignedImages(index, paths);
            if (!added) {
                showToast('这些图已经在该题的配图里了。', 'info');
                return;
            }
            renderParsedQuestionsList(parsedQuestionsData);
            renderParsedImageOverviewGroups(modal, 'gallery');
            showToast(
                '已把 ' + added + ' 张图指派给第 ' + (index + 1) + ' 题；保存该题后一并入库。',
                'success'
            );
        }

        function handleParsedImageOverviewDelete(modal) {
            const picked = parsedOverviewPicked(modal);
            if (!picked.length) return;

            const each = picked.map(box => ({
                index: parseInt(box.getAttribute('data-index'), 10),
                safePath: box.getAttribute('data-path')
            })).filter(item => item.safePath);

            // 图墙里两类图混排：未归属（index < 0）没有题目引用，删除 = 放弃，
            // 从本次会话的资产清单里摘掉；已归属的走软删除摘引用。两者都不物理删文件。
            const unassigned = each.filter(item => item.index < 0);
            const items = each.filter(item => item.index >= 0).map(item => ({
                index: item.index,
                safePath: item.safePath,
                converted: parsedCardImageHasTikz(item.index, item.safePath)
            }));

            const convertedCount = items.filter(item => item.converted).length;
            const hint = modal.querySelector('#parsedOverviewHint');
            const deleteBtn = modal.querySelector('#parsedOverviewDeleteBtn');

            // 已重绘为 TikZ 的图必须二次确认：删掉位图后 TikZ 资产的参考图会悬空，
            // 因此会连带移除这些 TikZ 资产——这一步不可逆。
            if (convertedCount > 0 && !parsedOverviewDeleteArmed) {
                parsedOverviewDeleteArmed = true;
                if (hint) {
                    hint.className = 'text-[10px] font-semibold text-rose-600';
                    hint.textContent = '其中 ' + convertedCount + ' 张已重绘为 TikZ，'
                        + '删除会连带移除这些 TikZ 资产。再点一次「确认删除」继续。';
                }
                if (deleteBtn) {
                    deleteBtn.textContent = '确认删除';
                    deleteBtn.className = OVERVIEW_DELETE_ARMED;
                }
                return;
            }

            parsedOverviewDeleteArmed = false;
            let removed = 0;
            items.forEach(item => {
                if (softDeleteParsedCardImage(item.index, item.safePath)) removed += 1;
            });
            const dropped = unassigned.length
                ? discardParsedUnassignedImages(unassigned.map(item => item.safePath))
                : 0;

            if (!removed && !dropped) {
                showToast('没有可移除的图片。', 'info');
            } else {
                const parts = [];
                if (removed) parts.push('移除 ' + removed + ' 张配图引用');
                if (dropped) parts.push('放弃 ' + dropped + ' 张未归属图');
                showToast(
                    '已' + parts.join('、') + '（软删除，文件随后由孤儿清理回收）。',
                    'success'
                );
            }
            renderParsedQuestionsList(parsedQuestionsData);
            renderParsedImageOverviewGroups(modal, 'gallery');
        }
            
        async function handleParsedImageOverviewConvert(modal) {
            const targets = parsedOverviewPicked(modal).map(box => ({
                index: parseInt(box.getAttribute('data-index'), 10),
                safePath: box.getAttribute('data-path'),
                slot: box.getAttribute('data-slot') || 'content'
            })).filter(item => {
                const state = parsedCardTikzStateSnapshot(item.index, item.safePath);
                if (state && state.status === 'converting') return false;
                return !parsedCardImageHasTikz(item.index, item.safePath);
            });
            if (!targets.length) {
                showToast('选中的图片都已经是 TikZ 矢量图了。', 'info');
                return;
            }

            const hint = modal.querySelector('#parsedOverviewHint');
            const convertBtn = modal.querySelector('#parsedOverviewConvertBtn');
            if (convertBtn) convertBtn.disabled = true;
            const failed = [];
            let done = 0;
            const total = targets.length;
            const paint = () => {
                if (!hint) return;
                hint.className = 'text-[10px] font-semibold text-indigo-600';
                hint.textContent = '正在批量重绘 TikZ… ' + done + '/' + total
                    + (failed.length ? '（失败 ' + failed.length + '）' : '');
            };
            paint();

            // 每次重绘都要跑一次 AI 调用 + XeLaTeX 编译，很重，限制 2 个并发。
            const queue = targets.slice();
            const worker = async () => {
                while (queue.length) {
                    const item = queue.shift();
                    try {
                        await convertParsedCardImageToTikz(item.index, item.safePath, item.slot);
                    } catch (err) {
                        failed.push(item);
                    }
                    const state = parsedCardTikzStateSnapshot(item.index, item.safePath);
                    if (state && state.status === 'failed') {
                        failed.push(item);
                    }
                    done += 1;
                    paint();
                }
            };
            await Promise.all([worker(), worker()]);

            if (convertBtn) convertBtn.disabled = false;
            renderParsedQuestionsList(parsedQuestionsData);
            renderParsedImageOverviewGroups(modal);
            showToast(
                failed.length
                    ? ('批量转 TikZ 完成：成功 ' + (total - failed.length) + ' 张，失败 '
                        + failed.length + ' 张（卡片上已标红，可单独重试）。')
                    : ('批量转 TikZ 完成：' + total + ' 张全部成功。'),
                failed.length ? 'warning' : 'success'
            );
        }

        // 单张配图卡片：来源角标 + 「转 TikZ」按钮（已转的显示 TikZ 渲染结果）。
        function buildParsedCardImageTile(safePath, convertedAsset, index, slot) {
            const convertedPath = window.MathBankSafe.safeImageUrl(
                convertedAsset && convertedAsset.image_path
            );
            const source = describeQuestionImageSource(safePath);
            const alreadyTikz = Boolean(convertedPath);

            const tile = document.createElement('div');
            tile.className = 'border border-slate-200 rounded-lg overflow-hidden bg-white w-[124px] '
                + 'shadow-sm flex flex-col';

            const figure = document.createElement('div');
            figure.className = 'relative aspect-[4/3] flex items-center justify-center bg-white';

            const badgeInfo = describeQuestionImageBadge(index, safePath, alreadyTikz);
            const badge = document.createElement('span');
            badge.className = 'absolute top-0.5 left-0.5 px-1 py-[1px] rounded text-[8px] font-bold '
                + tikzBadgeToneClass(badgeInfo.tone);
            badge.textContent = badgeInfo.label;
            badge.title = badgeInfo.title;
            figure.appendChild(badge);

            const img = document.createElement('img');
            img.src = alreadyTikz ? convertedPath : safePath;
            img.className = 'max-h-full max-w-full object-contain cursor-zoom-in '
                + 'hover:scale-105 transition-transform duration-300';
            img.setAttribute('data-safe-image-open', 'true');
            img.title = alreadyTikz
                ? 'TikZ 渲染结果（点击在新标签页中查看大图）'
                : '点击在新标签页中查看大图';
            figure.appendChild(img);
            tile.appendChild(figure);

            const action = document.createElement('div');
            action.className = 'px-1 py-0.5 text-[8px] font-semibold text-center';
            if (alreadyTikz) {
                action.className += ' text-indigo-600';
                action.textContent = '已转为 TikZ';
            } else if (badgeInfo.tone === 'converting') {
                action.className += ' text-amber-600';
                action.textContent = '转换中…';
            } else {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'card-img-to-tikz w-full py-1 text-[9px] font-bold text-indigo-600 '
                    + 'bg-indigo-50 hover:bg-indigo-100 rounded transition-colors '
                    + 'flex items-center justify-center gap-1';
                button.innerHTML = (badgeInfo.tone === 'failed')
                    ? '<i class="fa-solid fa-rotate-right"></i>重新转换'
                    : '<i class="fa-solid fa-pen-ruler"></i>转 TikZ';
                button.addEventListener('click', function () {
                    convertParsedCardImageToTikz(index, safePath, slot);
                });
                action.appendChild(button);
            }
            tile.appendChild(action);
            return tile;
        }

        function appendParsedCardImageStrip(container, q, index, answerText) {
            if (!container || !q || !Array.isArray(q.image_paths) || !q.image_paths.length) return;
            const rendered = container.innerHTML || '';
            // 解答图已由解答预览内联呈现，不再重复出现在题干配图条里。
            const answerOwned = new Set(parsedCardAnswerImagePaths(answerText));
            const assetByRef = {};
            ['content_tikz_assets', 'answer_tikz_assets'].forEach(key => {
                ((q && Array.isArray(q[key])) ? q[key] : []).forEach(asset => {
                    const ref = window.MathBankSafe.safeImageUrl(
                        asset && asset.reference_image_path
                    );
                    if (ref && !assetByRef[ref]) assetByRef[ref] = asset;
                });
            });
            const pending = q.image_paths
                .map(path => window.MathBankSafe.safeImageUrl(path))
                .filter(safePath => safePath
                    && !rendered.includes(safePath)
                    && !answerOwned.has(safePath));
            if (!pending.length) return;

            const strip = document.createElement('div');
            strip.className = 'flex flex-wrap gap-2 mt-3 pt-2.5 border-t '
                + 'border-dashed border-slate-200/60';
            // 配图自检角标：由「页级兜底」推断来的配图、或数量明显偏多的题，
            // 直接标在卡片上，避免逐题翻看才发现归属错了。
            const reviewReasons = (q && Array.isArray(q._image_review)) ? q._image_review : [];
            if (reviewReasons.length) {
                const warn = document.createElement('span');
                warn.className = 'self-center rounded-md border border-amber-200 bg-amber-50 '
                    + 'px-2 py-0.5 text-[10px] font-bold text-amber-700';
                // 三种待核对形态各说各话：兜底推断 / 选项重复引用同一张图 / 数量偏多。
                // 「选项重复」最危险（卷面看着完整、答案却会错），必须单列一条。
                if (reviewReasons.indexOf('duplicate') >= 0) {
                    warn.textContent = '选项重复引用同一张图，请核对';
                    warn.title = '视觉模型漏识别了部分图形，本题选项重复用了同一张图；请对照原页【手动截图】补图';
                } else if (reviewReasons.indexOf('failsafe') >= 0) {
                    warn.textContent = '配图由兜底推断，请核对';
                    warn.title = '系统按页面文本重合度自动推断出这批配图的归属，建议人工确认后再入库';
                } else {
                    warn.textContent = '配图偏多，请核对';
                    warn.title = '本题有配图未出现在题干或解析文字里，可能是别题的插图被一并挂了过来';
                }
                strip.appendChild(warn);
            }
            // 内容守恒角标：本题正文/解析里的选项数、表格数、填空下划线数或插图占位符
            // 与原卷对不上。这类改写不会报错、卷面也看着正常，只能靠后端数量对账发现。
            const fidelityReasons = (q && Array.isArray(q._fidelity_review)) ? q._fidelity_review : [];
            if (fidelityReasons.length) {
                const mismatch = document.createElement('span');
                mismatch.className = 'self-center rounded-md border border-rose-200 bg-rose-50 '
                    + 'px-2 py-0.5 text-[10px] font-bold text-rose-700';
                mismatch.textContent = '内容与原卷不符，请核对';
                mismatch.title = fidelityReasons.join('；');
                strip.appendChild(mismatch);
            }
            // 转换进度小结：本题共几张图、其中几张已经是 TikZ 矢量图。
            // 只在「已经转过至少一张」时显示——否则会给每道题都挂一个无意义的 0/0 标签。
            const allSafePaths = q.image_paths
                .map(path => window.MathBankSafe.safeImageUrl(path))
                .filter(Boolean);
            const convertedCount = allSafePaths.filter(p => assetByRef[p]).length;
            if (convertedCount > 0) {
                const allDone = convertedCount >= allSafePaths.length;
                const progress = document.createElement('span');
                progress.className = 'self-center rounded-md border px-2 py-0.5 text-[10px] '
                    + 'font-bold '
                    + (allDone
                        ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
                        : 'border-slate-200 bg-slate-50 text-slate-600');
                progress.textContent = 'TikZ ' + convertedCount + '/' + allSafePaths.length;
                progress.title = allDone
                    ? '本题全部配图都已重绘为 TikZ 矢量图'
                    : '本题还有位图未转成 TikZ，可点各图上的「转 TikZ」';
                strip.appendChild(progress);
            }
            pending.forEach(safePath => {
                strip.appendChild(
                    buildParsedCardImageTile(safePath, assetByRef[safePath], index, 'content')
                );
            });
            container.appendChild(strip);
        }

        // 解答正文里的插图：包一层相对定位容器，补来源角标与「转 TikZ」入口。
        // 已重绘过的位图只显示 AI·TikZ 角标（不再给按钮，避免重复重绘）。
        function decorateParsedCardAnswerImages(container, q, index) {
            if (!container || !q) return;
            const assetByRef = {};
            const renderedPaths = new Set();
            ['content_tikz_assets', 'answer_tikz_assets'].forEach(key => {
                ((Array.isArray(q[key])) ? q[key] : []).forEach(asset => {
                    const ref = window.MathBankSafe.safeImageUrl(
                        asset && asset.reference_image_path
                    );
                    const rendered = window.MathBankSafe.safeImageUrl(asset && asset.image_path);
                    if (ref) assetByRef[ref] = asset;
                    if (rendered) renderedPaths.add(rendered);
                });
            });

            Array.prototype.forEach.call(container.querySelectorAll('img'), img => {
                const src = window.MathBankSafe.safeImageUrl(img.getAttribute('src'));
                if (!src) return;
                const asset = assetByRef[src];
                const convertedPath = window.MathBankSafe.safeImageUrl(asset && asset.image_path);
                const alreadyTikz = Boolean(convertedPath) || renderedPaths.has(src);
                const source = describeQuestionImageSource(src);

                const wrapper = document.createElement('span');
                wrapper.className = 'relative inline-block align-top';
                wrapper.setAttribute('data-parsed-img-tile', '1');
                if (img.parentNode) {
                    img.parentNode.insertBefore(wrapper, img);
                }
                wrapper.appendChild(img);

                const badgeInfo = describeQuestionImageBadge(index, src, alreadyTikz);
                const badge = document.createElement('span');
                badge.className = 'absolute top-0 left-0 px-1 py-[1px] rounded text-[8px] '
                    + 'font-bold pointer-events-none '
                    + tikzBadgeToneClass(badgeInfo.tone);
                badge.textContent = badgeInfo.label;
                badge.title = badgeInfo.title;
                wrapper.appendChild(badge);

                if (badgeInfo.canRetry) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'card-img-to-tikz absolute bottom-0 right-0 px-1 py-[1px] '
                        + 'rounded text-[8px] font-bold text-indigo-700 bg-indigo-100/95 '
                        + 'hover:bg-indigo-200 transition-colors';
                    button.textContent = (badgeInfo.tone === 'failed') ? '重新转换' : '转 TikZ';
                    button.title = (badgeInfo.tone === 'failed')
                        ? badgeInfo.title
                        : '用 AI 把这张位图重绘成 TikZ 矢量图';
                    button.addEventListener('click', function () {
                        convertParsedCardImageToTikz(index, src, 'answer');
                    });
                    wrapper.appendChild(button);
                }
            });
        }

        // 选项图（A./B./C./D. 行内的图）是题干内容的一部分，没有题干图条 / 解答区那样的
        // 装饰管线，会沦为「转 TikZ」盲区。这里专门扫 choices-grid 里的 <img>，补上角标
        // 与「转 TikZ」入口；重绘结果登记进 content_tikz_assets（与「转 TikZ」按钮一致）。
        function decorateParsedCardChoiceImages(container, q, index) {
            if (!container || !q) return;
            const assetByRef = {};
            const renderedPaths = new Set();
            (Array.isArray(q.content_tikz_assets) ? q.content_tikz_assets : []).forEach(asset => {
                const ref = window.MathBankSafe.safeImageUrl(asset && asset.reference_image_path);
                const rendered = window.MathBankSafe.safeImageUrl(asset && asset.image_path);
                if (ref) assetByRef[ref] = asset;
                if (rendered) renderedPaths.add(rendered);
            });

            const grids = container.querySelectorAll('.choices-grid');
            Array.prototype.forEach.call(grids, grid => {
                Array.prototype.forEach.call(grid.querySelectorAll('img'), img => {
                    const src = window.MathBankSafe.safeImageUrl(img.getAttribute('src'));
                    if (!src) return;
                    const asset = assetByRef[src];
                    const convertedPath = window.MathBankSafe.safeImageUrl(asset && asset.image_path);
                    const alreadyTikz = Boolean(convertedPath) || renderedPaths.has(src);
                    const source = describeQuestionImageSource(src);

                    const wrapper = document.createElement('span');
                    wrapper.className = 'relative inline-block align-top';
                    wrapper.setAttribute('data-parsed-img-tile', '1');
                    if (img.parentNode) {
                        img.parentNode.insertBefore(wrapper, img);
                    }
                    wrapper.appendChild(img);

                    const badgeInfo = describeQuestionImageBadge(index, src, alreadyTikz);
                    const badge = document.createElement('span');
                    badge.className = 'absolute top-0 left-0 px-1 py-[1px] rounded text-[8px] '
                        + 'font-bold pointer-events-none '
                        + tikzBadgeToneClass(badgeInfo.tone);
                    badge.textContent = badgeInfo.label;
                    badge.title = badgeInfo.title;
                    wrapper.appendChild(badge);

                    if (badgeInfo.canRetry) {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'card-img-to-tikz absolute bottom-0 right-0 px-1 py-[1px] '
                            + 'rounded text-[8px] font-bold text-indigo-700 bg-indigo-100/95 '
                            + 'hover:bg-indigo-200 transition-colors';
                        button.textContent = (badgeInfo.tone === 'failed') ? '重新转换' : '转 TikZ';
                        button.title = (badgeInfo.tone === 'failed')
                            ? badgeInfo.title
                            : '用 AI 把这个选项图重绘成 TikZ 矢量图';
                        button.setAttribute('data-image-path', src);
                        button.addEventListener('click', function () {
                            convertParsedCardImageToTikz(index, src, 'content');
                        });
                        wrapper.appendChild(button);
                    }
                });
            });
        }

        function renderParsedCardPreview(card, contentText, answerText) {
            const contentPrev = card.querySelector('.card-content-preview');
            const answerPrev = card.querySelector('.card-answer-preview');
            
            // Extract card index to find its image_paths dynamically
            const indexStr = card.id ? card.id.replace('parsed-card-', '') : '';
            const index = indexStr ? parseInt(indexStr) : null;
            const q = (index !== null && !isNaN(index)) ? parsedQuestionsData[index] : null;

            // 彻底 LaTeX 化：把本题 image_paths（含 referenced_images）登记进全局
            // basename->URL 表，预览里 \includegraphics{文件名} 才能找回真实地址。
            if (q) {
                if (Array.isArray(q.image_paths)) window.registerMathbankImages(q.image_paths);
                if (Array.isArray(q.referenced_images)) window.registerMathbankImages(q.referenced_images);
            }
            
            // For content
            if (!contentText.trim()) {
                contentPrev.innerHTML = '<span class="text-slate-400 italic text-[10px]">题干预览将在此实时渲染...</span>';
            } else {
                try {
                    let processedContent = contentText;
                    if (typeof window.cleanChoiceStemParentheses === 'function') {
                        processedContent = window.cleanChoiceStemParentheses(processedContent);
                    }
                    // 未落地的占位图（如 placeholder.png）在预览里换成明确的“插图待补”，
                    // 避免显示成破图；真实的裁剪插图会以缩略图形式追加在下方。
                    processedContent = processedContent.replace(
                        /!\[[^\]]*\]\(([^)]*placeholder[^)]*)\)/gi,
                        '【插图待补】'
                    );
                    let html = parseMarkdownWithMath(processedContent);

                    contentPrev.innerHTML = window.MathBankSafe.sanitizeRichHtml(html);
                    renderMathInElement(contentPrev, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                    // 选项横排：先在 DOM 上把竖排的选项段落重组进 .choices-grid，
                    // 让下面的网格自适应与选项图角标装饰一起接管。
                    groupParsedCardOptionParagraphs(contentPrev);
                    if (typeof window.adaptChoicesGridLayout === 'function') {
                        window.adaptChoicesGridLayout(contentPrev);
                    }
                    // 配图条用真实 DOM 追加（不经 sanitizeRichHtml：<button> 与
                    // data-* 属性不在白名单内，内联 onclick 也会被 DOMPurify 剥掉）。
                    appendParsedCardImageStrip(contentPrev, q, index, answerText);
                    // 选项图（A./B./C./D. 行内）同样补角标与「转 TikZ」入口，避免成为盲区
                    decorateParsedCardChoiceImages(contentPrev, q, index);
                } catch(e) {
                    contentPrev.textContent = contentText;
                }
            }

            // For answer
            if (!answerText.trim()) {
                answerPrev.innerHTML = '<span class="text-slate-400 italic text-[10px]">解析预览将在此实时渲染...</span>';
            } else {
                try {
                    // AI 生成的解析常带过多空行，压缩为正常段落间距，让预览更紧凑
                    const compactAnswer = String(answerText).replace(/\n{3,}/g, '\n\n');
                    answerPrev.innerHTML = parseMarkdownWithMath(compactAnswer);
                    renderMathInElement(answerPrev, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                    // 解答里的插图同样标注来源并可一键转 TikZ。
                    decorateParsedCardAnswerImages(answerPrev, q, index);
                } catch(e) {
                    answerPrev.textContent = answerText;
                }
            }
        }

        function validateParsedQuestionBeforeImport(index, options = {}) {
            const card = document.getElementById(`parsed-card-${index}`);
            const q = parsedQuestionsData[index];
            if (!card || !q) return false;
            const content = card.querySelector('.card-content-textarea')?.value.trim() || '';
            const questionType = card.querySelector('.card-qtype')?.value || '';
            const compulsory = card.querySelector('.card-compulsory')?.value || '';
            const chapter = card.querySelector('.card-chapter')?.value || '';
            let message = '';
            let target = null;
            if (!content) {
                message = `第 ${index + 1} 题的题干内容不能为空！`;
                target = card.querySelector('.card-content-textarea');
            } else if (!questionType) {
                message = `请确认第 ${index + 1} 题的题型！`;
                target = card.querySelector('.card-qtype');
            } else if (!compulsory || !chapter) {
                message = `请选择第 ${index + 1} 题的学段与所属章节！`;
                target = !compulsory
                    ? card.querySelector('.card-compulsory')
                    : card.querySelector('.card-chapter');
            }
            if (!message) return true;
            if (options.notify !== false) showToast(message, 'warning');
            if (options.focus !== false && target) {
                target.scrollIntoView({ behavior: 'smooth', block: 'center' });
                target.focus();
            }
            return false;
        }

        // 双向细目表挑题（动态弹窗）：按 题型 × 知识点 × 难度系数 逐行找候选题
        function openBlueprintModal() {
            const typeOptions = [
                ['single_choice', '单选题'], ['multi_choice', '多选题'],
                ['fill_in_blank', '填空题'], ['detailed_answer', '解答题']
            ];
            const rowHtml = (row) => {
                const opts = typeOptions.map(([v, t]) =>
                    `<option value="${v}"${row && row.question_type === v ? ' selected' : ''}>${t}</option>`
                ).join('');
                return `<div class="bp-row grid grid-cols-12 gap-2 items-center">
                  <select data-k="question_type" class="col-span-3 glass-select px-2 py-1.5 rounded-lg text-xs">${opts}</select>
                  <input data-k="knowledge" class="col-span-4 glass-input px-2 py-1.5 rounded-lg text-xs" placeholder="知识点关键词">
                  <input data-k="difficulty_min" type="number" min="0" max="1" step="0.05" class="col-span-2 glass-input px-2 py-1.5 rounded-lg text-xs" placeholder="难度≥">
                  <input data-k="difficulty_max" type="number" min="0" max="1" step="0.05" class="col-span-2 glass-input px-2 py-1.5 rounded-lg text-xs" placeholder="难度≤">
                  <input data-k="count" type="number" min="1" max="50" value="5" class="col-span-1 glass-input px-1 py-1.5 rounded-lg text-xs text-center">
                </div>`;
            };
            const backdrop = document.createElement('div');
            backdrop.className = 'fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-[70] flex items-center justify-center p-4';
            backdrop.innerHTML = `
              <div class="bg-white dark:bg-slate-900 rounded-2xl w-full max-w-2xl shadow-2xl border border-slate-100 dark:border-slate-700 flex flex-col max-h-[85vh]">
                <div class="flex items-center justify-between px-5 py-3 border-b border-slate-100 dark:border-slate-700">
                  <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">双向细目表 · 挑题</h3>
                  <button type="button" data-close class="text-slate-400 hover:text-slate-600 cursor-pointer"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div class="p-4 space-y-3 overflow-y-auto">
                  <div class="grid grid-cols-12 gap-2 text-[10px] font-bold text-slate-400 px-1">
                    <span class="col-span-3">题型</span><span class="col-span-4">知识点</span>
                    <span class="col-span-2">难度≥</span><span class="col-span-2">难度≤</span><span class="col-span-1 text-center">数量</span>
                  </div>
                  <div data-rows class="space-y-2">${rowHtml(null)}</div>
                  <div class="flex items-center justify-between">
                    <button type="button" data-add class="text-[11px] font-bold text-brand-600 hover:text-brand-700 cursor-pointer"><i class="fa-solid fa-plus mr-1"></i>添加一行</button>
                    <button type="button" data-run class="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-xs font-bold cursor-pointer">开始挑题</button>
                  </div>
                  <div data-result class="text-xs text-slate-600 dark:text-slate-300 space-y-2"></div>
                </div>
              </div>`;
            document.body.appendChild(backdrop);
            backdrop.addEventListener('click', function (e) {
                if (e.target === backdrop || e.target.closest('[data-close]')) backdrop.remove();
            });
            const rowsEl = backdrop.querySelector('[data-rows]');
            backdrop.querySelector('[data-add]').addEventListener('click', () => {
                rowsEl.insertAdjacentHTML('beforeend', rowHtml(null));
            });
            const resultEl = backdrop.querySelector('[data-result]');
            backdrop.querySelector('[data-run]').addEventListener('click', async function () {
                const rows = Array.from(rowsEl.querySelectorAll('.bp-row')).map(function (el) {
                    const read = (k) => {
                        const node = el.querySelector('[data-k="' + k + '"]');
                        return node ? String(node.value || '').trim() : '';
                    };
                    const toFloat = (v) => (v === '' ? null : Number(v));
                    return {
                        question_type: read('question_type'),
                        knowledge: read('knowledge'),
                        difficulty_min: toFloat(read('difficulty_min')),
                        difficulty_max: toFloat(read('difficulty_max')),
                        count: Number(read('count')) || 5
                    };
                });
                resultEl.innerHTML = '<div class="text-slate-400 py-3 text-center">挑题中...</div>';
                const localToken = (typeof window.getLocalToken === 'function') ? window.getLocalToken() : '';
                const headers = { 'Content-Type': 'application/json' };
                if (localToken) headers['X-Local-Token'] = localToken;
                try {
                    const resp = await fetch('/api/questions/blueprint-match', {
                        method: 'POST', headers, body: JSON.stringify({ rows })
                    });
                    const data = await resp.json();
                    if (!resp.ok) throw new Error(data.detail || '挑题失败');
                    if (!data.rows || !data.rows.length) { resultEl.innerHTML = '<div class="text-slate-400 py-3 text-center">无结果</div>'; return; }
                    resultEl.innerHTML = data.rows.map(function (r, i) {
                        const qs = r.questions || [];
                        const head = `<div class="font-bold text-slate-700 dark:text-slate-200 mt-1">第 ${i + 1} 行 · 命中 ${qs.length} 题</div>`;
                        if (!qs.length) return head + '<div class="text-slate-400 pl-2">（无匹配）</div>';
                        return head + qs.map(function (q) {
                            const snippet = window.MathBankSafe.escapeText(String(q.content || '').replace(/\s+/g, ' ').slice(0, 60));
                            return `<div class="flex items-center gap-2 pl-2"><span class="text-[10px] text-slate-400 shrink-0">#${q.id}</span><span class="truncate">${snippet}</span></div>`;
                        }).join('');
                    }).join('');
                } catch (err) {
                    resultEl.innerHTML = '<div class="text-rose-500 py-3 text-center">' + window.MathBankSafe.escapeText(String(err.message || err)) + '</div>';
                }
            });
        }
        window.openBlueprintModal = openBlueprintModal;

        // 导入批次列表 + 整批回滚（动态弹窗，避免新增静态 DOM）
        function openImportBatchesModal() {
            const backdrop = document.createElement('div');
            backdrop.className = 'fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-[70] flex items-center justify-center p-4';
            backdrop.innerHTML = `
              <div class="bg-white dark:bg-slate-900 rounded-2xl w-full max-w-xl shadow-2xl border border-slate-100 dark:border-slate-700 flex flex-col max-h-[80vh]">
                <div class="flex items-center justify-between px-5 py-3 border-b border-slate-100 dark:border-slate-700">
                  <h3 class="text-sm font-bold text-slate-800 dark:text-slate-100">导入批次 · 回滚</h3>
                  <button type="button" data-close class="text-slate-400 hover:text-slate-600 cursor-pointer"><i class="fa-solid fa-xmark"></i></button>
                </div>
                <div data-list class="p-4 overflow-y-auto space-y-2 text-xs text-slate-600 dark:text-slate-300">加载中...</div>
              </div>`;
            document.body.appendChild(backdrop);
            backdrop.addEventListener('click', function (e) {
                if (e.target === backdrop || e.target.closest('[data-close]')) backdrop.remove();
            });
            const list = backdrop.querySelector('[data-list]');
            const localToken = (typeof window.getLocalToken === 'function') ? window.getLocalToken() : '';
            const headers = localToken ? { 'X-Local-Token': localToken } : {};
            const render = (batches) => {
                if (!batches.length) {
                    list.innerHTML = '<div class="text-slate-400 py-6 text-center">暂无导入批次</div>';
                    return;
                }
                list.innerHTML = batches.map(function (b) {
                    const name = window.MathBankSafe.escapeText(b.name || b.source_name || ('批次 #' + b.id));
                    const when = b.created_at ? window.MathBankSafe.escapeText(String(b.created_at).slice(0, 19).replace('T', ' ')) : '';
                    const disabled = b.rolled_back || !b.question_count;
                    return `<div class="flex items-center justify-between gap-3 rounded-xl border border-slate-100 dark:border-slate-700 px-3 py-2">
                      <div class="min-w-0">
                        <div class="font-semibold truncate">${name}</div>
                        <div class="text-[10px] text-slate-400">${b.question_count} 题 · ${when}${b.rolled_back ? ' · 已回滚' : ''}</div>
                      </div>
                      <button type="button" data-rollback="${b.id}" ${disabled ? 'disabled' : ''}
                        class="shrink-0 px-3 py-1.5 rounded-lg text-[11px] font-bold ${disabled ? 'bg-slate-100 text-slate-400' : 'bg-rose-50 text-rose-600 hover:bg-rose-100 cursor-pointer'}">回滚</button>
                    </div>`;
                }).join('');
            };
            fetch('/api/import-batches', { headers })
                .then(r => r.json())
                .then(d => render(d.batches || []))
                .catch(() => { list.innerHTML = '<div class="text-rose-500 py-6 text-center">加载失败</div>'; });
            list.addEventListener('click', async function (e) {
                const btn = e.target.closest('[data-rollback]');
                if (!btn || btn.disabled) return;
                const id = btn.getAttribute('data-rollback');
                if (!window.confirm('确定回滚该批次？将删除该批次导入的题目（已导出的试卷快照不受影响）。')) return;
                btn.disabled = true;
                try {
                    const resp = await fetch('/api/import-batches/' + id + '/rollback', { method: 'POST', headers });
                    const data = await resp.json();
                    if (!resp.ok) throw new Error(data.detail || '回滚失败');
                    showToast('已回滚 ' + data.deleted + ' 道题。', 'success');
                    const fresh = await fetch('/api/import-batches', { headers }).then(r => r.json());
                    render(fresh.batches || []);
                    if (typeof loadQuestions === 'function') loadQuestions();
                } catch (err) {
                    btn.disabled = false;
                    showToast('回滚失败：' + (err.message || err), 'error');
                }
            });
        }
        window.openImportBatchesModal = openImportBatchesModal;

        // ===== 左侧输入面板折叠 =====
        window.toggleImportInputPane = function () {
            const pane = document.getElementById('importInputPane');
            const btn = document.getElementById('toggleImportInputPane');
            if (!pane || !btn) return;
            const collapsed = pane.dataset.collapsed === '1';
            if (collapsed) {
                pane.dataset.collapsed = '0';
                pane.classList.remove('w-0', 'overflow-hidden', 'border-r-0');
                pane.classList.add('w-[360px]', 'border-r', 'p-3');
                pane.style.width = '';
                pane.style.padding = '';
                btn.style.left = '360px';
                const ic = btn.querySelector('i');
                if (ic) ic.className = 'fa-solid fa-angles-left text-[10px]';
                btn.title = '折叠左侧输入面板';
            } else {
                pane.dataset.collapsed = '1';
                pane.classList.add('w-0', 'overflow-hidden', 'border-r-0');
                pane.classList.remove('w-[360px]', 'p-3');
                pane.style.width = '0px';
                pane.style.padding = '0';
                btn.style.left = '0px';
                const ic = btn.querySelector('i');
                if (ic) ic.className = 'fa-solid fa-angles-right text-[10px]';
                btn.title = '展开左侧输入面板';
            }
            try { localStorage.setItem('mathbank_import_pane_collapsed', collapsed ? '0' : '1'); } catch (e) {}
        };
        // 启动时恢复折叠状态
        (function restoreImportPaneState() {
            // 进入整卷录入界面默认展开输入面板，不自动恢复折叠
            try {
                const saved = localStorage.getItem('mathbank_import_pane_collapsed');
                if (false && saved === '1') {
                    setTimeout(() => {
                        const pane = document.getElementById('importInputPane');
                        const btn = document.getElementById('toggleImportInputPane');
                        if (!pane) return;
                        pane.dataset.collapsed = '1';
                        pane.classList.add('w-0', 'overflow-hidden', 'border-r-0');
                        pane.classList.remove('w-[360px]', 'p-3');
                        pane.style.width = '0px';
                        pane.style.padding = '0';
                        if (btn) {
                            btn.style.left = '0px';
                            const ic = btn.querySelector('i');
                            if (ic) ic.className = 'fa-solid fa-angles-right text-[10px]';
                            btn.title = '展开左侧输入面板';
                        }
                    }, 50);
                }
            } catch (e) {}
        })();

        // ===== 来源字段智能联想 + 文件名预填 =====
        window._schoolsDict = [];
        window._regionsTree = [];  // [{province, cities:[]}]
        window._lastSrcProvince = '';
        window._lastSrcCity = '';
        let _schoolDebounceTimer = null;

        async function loadSchoolDict() {
            try {
                const resp = await fetch('/api/school-suggestions?limit=500', {
                    headers: { 'X-Local-Token': window.LOCAL_TOKEN || '' }
                });
                if (!resp.ok) return;
                const data = await resp.json();
                window._schoolsDict = (data && data.suggestions) || [];
            } catch (e) { /* 离线时静默 */ }
            try {
                const resp = await fetch('/api/school-regions', {
                    headers: { 'X-Local-Token': window.LOCAL_TOKEN || '' }
                });
                if (!resp.ok) return;
                const data = await resp.json();
                window._regionsTree = (data && data.regions) || [];
                fillProvinceSelect();
            } catch (e) { /* 静默 */ }
        }

        function fillProvinceSelect() {
            const sel = document.getElementById('importSrcProvince');
            if (!sel) return;
            const cur = sel.value;
            sel.innerHTML = '<option value="">不选（全国）</option>';
            for (const r of window._regionsTree) {
                const opt = document.createElement('option');
                opt.value = r.province;
                opt.textContent = r.province;
                sel.appendChild(opt);
            }
            if (cur) sel.value = cur;
            fillCitySelect();
        }

        function fillCitySelect() {
            const provSel = document.getElementById('importSrcProvince');
            const citySel = document.getElementById('importSrcCity');
            if (!provSel || !citySel) return;
            const prov = provSel.value;
            const cur = citySel.value;
            citySel.innerHTML = '<option value="">不选</option>';
            const node = window._regionsTree.find(r => r.province === prov);
            if (node) {
                for (const c of node.cities) {
                    const opt = document.createElement('option');
                    opt.value = c;
                    opt.textContent = c;
                    citySel.appendChild(opt);
                }
            }
            if (cur && prov) {
                // 选了省后若当前市不在该省里则清空
                const still = Array.from(citySel.options).some(o => o.value === cur);
                citySel.value = still ? cur : '';
            } else {
                citySel.value = '';
            }
            // 同步 lastSrcProvince / lastSrcCity
            window._lastSrcProvince = provSel.value || '';
            window._lastSrcCity = citySel.value || '';
            refreshSchoolDatalist();
        }

        function refreshSchoolDatalist() {
            const prov = (document.getElementById('importSrcProvince') || {}).value || '';
            const city = (document.getElementById('importSrcCity') || {}).value || '';
            const regionEl = document.getElementById('importSrcRegion');
            const q = regionEl ? regionEl.value.trim() : '';
            const dl = document.getElementById('dlSrcRegions');
            if (!dl) return;
            let items = window._schoolsDict.slice();
            if (prov) items = items.filter(s => (s.province || '') === prov);
            if (city) items = items.filter(s => (s.city || '') === city);
            if (q) items = items.filter(s => s.name && s.name.includes(q));
            items = items.slice(0, 80);
            dl.innerHTML = '';
            for (const s of items) {
                const opt = document.createElement('option');
                opt.value = s.name;
                opt.dataset.province = s.province || '';
                opt.dataset.city = s.city || '';
                dl.appendChild(opt);
            }
        }

        function tryMatchSchoolToGeo() {
            const regionEl = document.getElementById('importSrcRegion');
            if (!regionEl) return;
            const val = regionEl.value.trim();
            const provSel = document.getElementById('importSrcProvince');
            const citySel = document.getElementById('importSrcCity');
            if (!val) {
                if (provSel) provSel.value = '';
                if (citySel) citySel.value = '';
                fillCitySelect();
                return;
            }
            // 已选省时只在该省匹配；否则全表匹配
            const provFilter = provSel ? provSel.value : '';
            let cands = window._schoolsDict.filter(s => s.name === val);
            if (provFilter) cands = cands.filter(s => (s.province || '') === provFilter);
            if (cands.length === 1) {
                // 唯一命中：回填省/市下拉
                if (provSel) provSel.value = cands[0].province || '';
                fillCitySelect();
                if (citySel) citySel.value = cands[0].city || '';
                window._lastSrcProvince = cands[0].province || '';
                window._lastSrcCity = cands[0].city || '';
            } else if (cands.length > 1) {
                // 多义：只回填省下拉到第一个候选，市留空让用户选
                if (provSel && !provSel.value) {
                    provSel.value = cands[0].province || '';
                    fillCitySelect();
                }
            }
        }

        function prefillSourceFromFilename(name) {
            if (!name) return;
            const n = String(name).replace(/\.[^.]+$/, '');
            const yearEl = document.getElementById('importSrcYear');
            const gradeEl = document.getElementById('importApplicableGrade');
            const semEl = document.getElementById('importSrcSemester');
            const examEl = document.getElementById('importSrcExamType');
            const regionEl = document.getElementById('importSrcRegion');
            const provSel = document.getElementById('importSrcProvince');
            // 学年度对（如 2023-2024 学年）：上学期→第一年，下学期→第二年
            const ymPair = n.match(/(20\d{2})\s*[-—–~]\s*(20\d{2})\s*学年/);
            window._srcYearPair = ymPair ? [ymPair[1], ymPair[2]] : null;
            // 年份
            const ym = n.match(/(20\d{2})/);
            if (ym && yearEl && !yearEl.value.trim()) yearEl.value = ym[1];
            // 年级学期：高二下 / 高2下
            const gm = n.match(/高\s*([一二三123])\s*([上下])/);
            if (gm) {
                const gMap = { '一': 'g1', '二': 'g2', '三': 'g3', '1': 'g1', '2': 'g2', '3': 'g3' };
                const g = gMap[gm[1]] || '';
                if (g && gradeEl && !gradeEl.value) gradeEl.value = g;
                if (semEl && !semEl.value) semEl.value = gm[2];
            }
            // 考试类型
            const em = n.match(/(期末|期中|月考|一模|二模|三模|联考|高考|模考|学考|会考)/);
            if (em && examEl && !examEl.value.trim()) examEl.value = em[1];
            // 学校：词库最长匹配
            if (window._schoolsDict && window._schoolsDict.length && regionEl && !regionEl.value.trim()) {
                let best = null;
                for (const s of window._schoolsDict) {
                    if (s.name && n.includes(s.name) && (!best || s.name.length > best.name.length)) {
                        best = s;
                    }
                }
                if (best) {
                    regionEl.value = best.name;
                    if (provSel) provSel.value = best.province || '';
                    fillCitySelect();
                    const citySel = document.getElementById('importSrcCity');
                    if (citySel) citySel.value = best.city || '';
                    window._lastSrcProvince = best.province || '';
                    window._lastSrcCity = best.city || '';
                }
            }
            // 学年联动：上学期→学年度第一年，下学期→学年度第二年
            if (window._srcYearPair && yearEl && semEl && semEl.value) {
                if (semEl.value === '下' && window._srcYearPair[1]) {
                    yearEl.value = window._srcYearPair[1];
                } else if (semEl.value === '上' && window._srcYearPair[0]) {
                    yearEl.value = window._srcYearPair[0];
                }
            }
        }

        function buildStandardPaperTitle() {
            const yearEl = document.getElementById('importSrcYear');
            const regionEl = document.getElementById('importSrcRegion');
            const examTypeEl = document.getElementById('importSrcExamType');
            const semesterEl = document.getElementById('importSrcSemester');
            const gradeEl = document.getElementById('importApplicableGrade');
            const year = yearEl ? String(yearEl.value || '').trim() : '';
            const region = regionEl ? String(regionEl.value || '').trim() : '';
            const examType = examTypeEl ? String(examTypeEl.value || '').trim() : '';
            const semester = semesterEl ? String(semesterEl.value || '').trim() : '';
            const gradeText = { g1: '高一', g2: '高二', g3: '高三' }[gradeEl ? gradeEl.value : ''] || '';
            const parts = [];
            if (year) parts.push(year + '年');
            if (region) parts.push(region);
            if (gradeText) parts.push(gradeText);
            if (semester === '上') parts.push('上学期');
            else if (semester === '下') parts.push('下学期');
            if (examType) parts.push(examType);
            return parts.length >= 2 ? parts.join('') : '';
        }

        function applyStandardPaperTitle() {
            const titleInput = document.getElementById('importPaperTitle');
            if (!titleInput) return;
            const std = buildStandardPaperTitle();
            if (std) titleInput.value = std;
        }

        function initSourceSmartFields() {
            loadSchoolDict();
            const regionEl = document.getElementById('importSrcRegion');
            const provSel = document.getElementById('importSrcProvince');
            const citySel = document.getElementById('importSrcCity');
            if (provSel) provSel.addEventListener('change', () => { fillCitySelect(); refreshSchoolDatalist(); });
            if (citySel) citySel.addEventListener('change', () => {
                window._lastSrcProvince = provSel ? provSel.value : '';
                window._lastSrcCity = citySel.value;
                refreshSchoolDatalist();
            });
            if (!regionEl) return;
            regionEl.addEventListener('input', () => {
                clearTimeout(_schoolDebounceTimer);
                _schoolDebounceTimer = setTimeout(refreshSchoolDatalist, 150);
            });
            regionEl.addEventListener('change', tryMatchSchoolToGeo);
            // 文件名预填：监听试卷标题自动赋值
            const titleEl = document.getElementById('importPaperTitle');
            if (titleEl) {
                titleEl.addEventListener('change', () => prefillSourceFromFilename(titleEl.value));
            }
        }

function saveParsedQuestion(index) {
            const q = parsedQuestionsData[index];
            if (!q) return Promise.resolve(true);
            const options = arguments.length > 1 && arguments[1] ? arguments[1] : {};
            if (parsedBatchSaveInFlight && options.fromBatch !== true) {
                showToast('批量入库正在进行，请等待完成后再单独导入。', 'info');
                return Promise.resolve(false);
            }
            const existingSave = parsedQuestionSaveInFlight.get(q);
            if (existingSave) return existingSave;

            const saveGeneration = parsedQuestionsGeneration;
            const card = document.getElementById(`parsed-card-${index}`);
            if (!card) return Promise.reject(new Error('Card element not found'));
            const saveBtn = card.querySelector('.card-save-btn');

            const restoreButton = () => {
                if (!isParsedQuestionSaveContextCurrent(saveGeneration, index, q) || !saveBtn) return;
                saveBtn.disabled = Boolean(parsedBatchSaveInFlight);
                saveBtn.innerHTML = q.saved
                    ? '<i class="fa-solid fa-rotate-right"></i> <span>再次导入</span>'
                    : '<i class="fa-solid fa-file-arrow-up"></i> <span>导入此题</span>';
            };

            const saveOperation = (async () => {
                if (!validateParsedQuestionBeforeImport(index)) return false;
                let duplicateOverride = options.duplicateOverride || '';
                let duplicateSnapshotHash = options.duplicateSnapshotHash || '';
                let expectedLocalSnapshot = options.localSnapshot || '';
                let autoTag = options.autoTag || '';

                if (!options.duplicateDecisionResolved && typeof precheckParsedQuestionDuplicates === 'function') {
                    if (saveBtn) {
                        saveBtn.disabled = true;
                        saveBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>查重中...</span>';
                    }
                    const outcome = await precheckParsedQuestionDuplicates({
                        indices: [index],
                        showSummary: false,
                        notifyFailure: true
                    });
                    if (!isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) return false;
                    if (outcome.stale) {
                        showToast(`第 ${index + 1} 题在查重期间已修改，请再次点击导入。`, 'info');
                        return false;
                    }
                    if (outcome.invalid) return false;
                    if (outcome.ok) {
                        const result = outcome.results.get(index);
                        expectedLocalSnapshot = parsedDuplicateSnapshots.get(index) || '';
                        duplicateSnapshotHash = String(result && result.snapshot_hash || '');
                        if (duplicateResultHasSignal(result)) {
                            const action = await openParsedDuplicateReviewModal(
                                parsedDuplicateReviewEntries([index], outcome.results),
                                'single'
                            );
                            if (action !== 'independent') {
                                focusFirstParsedDuplicate([index]);
                                return false;
                            }
                            duplicateOverride = 'independent';
                            autoTag = buildDuplicateAutoTag(result);
                        }
                    }
                    // A failed check is explicitly allowed to continue without a
                    // fabricated snapshot or an implicit duplicate override.
                }

                if (!isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) return false;
                if (expectedLocalSnapshot) {
                    const currentItem = buildParsedQuestionDuplicateItem(index, saveGeneration);
                    if (!currentItem || serializeQuestionDuplicateItem(currentItem) !== expectedLocalSnapshot) {
                        showToast(`第 ${index + 1} 题的内容已变化，本次未保存，请重新导入。`, 'info');
                        return false;
                    }
                }

                const content = card.querySelector('.card-content-textarea').value.trim();
                const answer_markdown = card.querySelector('.card-answer-textarea').value.trim();
                const question_type = card.querySelector('.card-qtype').value;
                const difficulty = card.querySelector('.card-difficulty').value;
                const source = card.querySelector('.card-source').value.trim();
                const category_compulsory = card.querySelector('.card-compulsory').value;
                const category_chapter = card.querySelector('.card-chapter').value;
                const category_knowledge = card.querySelector('.card-knowledge').value;
                const topicSelectEl = card.querySelector('.card-topic');
                const topic = topicSelectEl ? topicSelectEl.value : '';

                if (!content) {
                    showToast(`第 ${index + 1} 题的题干内容不能为空！`, 'warning');
                    throw new Error('Content empty');
                }
                if (!category_compulsory || !category_chapter) {
                    showToast(`请选择第 ${index + 1} 题的学段与所属章节！`, 'warning');
                    const compSelect = card.querySelector('.card-compulsory');
                    const chapSelect = card.querySelector('.card-chapter');
                    const targetSelect = !category_compulsory ? compSelect : chapSelect;
                    if (targetSelect) {
                        targetSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        targetSelect.classList.remove('border-slate-200');
                        targetSelect.classList.add('ring-2', 'ring-red-400', 'border-red-400');
                        setTimeout(() => {
                            targetSelect.classList.remove('ring-2', 'ring-red-400', 'border-red-400');
                            targetSelect.classList.add('border-slate-200');
                        }, 2500);
                        targetSelect.focus();
                    }
                    throw new Error('Curriculum empty');
                }

                if (saveBtn) {
                    saveBtn.disabled = true;
                    saveBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>保存中...</span>';
                }
                const formData = new FormData();
                formData.append('content', content);
                formData.append('question_type', question_type);
                formData.append('category_compulsory', category_compulsory);
                formData.append('category_chapter', category_chapter);
                formData.append('category_knowledge', category_knowledge);
                if (topic) formData.append('topic', topic);
                formData.append('difficulty', difficulty);
                formData.append('source', source);
                // 题号以卡片输入框为准（缺省已回落为本题顺序号），保证写入元数据。
                const cardQuestionNoInput = card ? card.querySelector('.card-question-no') : null;
                const paperQuestionNo = cardQuestionNoInput
                    ? String(cardQuestionNoInput.value || '').trim()
                    : String(q.paper_question_no || '').trim();
                if (paperQuestionNo) formData.append('paper_question_no', paperQuestionNo);
                formData.append('answer_markdown', answer_markdown);
                const applicableGrade = (q && q.applicable_grade)
                    || (typeof getUnifiedApplicableGrade === 'function' ? getUnifiedApplicableGrade() : '')
                    || '';
                formData.append('applicable_grade', applicableGrade);
                const parsedContentTikzAssets = safePersistedTikzAssets(q.content_tikz_assets);
                const parsedAnswerTikzAssets = safePersistedTikzAssets(q.answer_tikz_assets);
                const firstContentTikzAsset = parsedContentTikzAssets[0] || null;
                if (parsedContentTikzAssets.length > 0) {
                    formData.append('content_tikz_assets', JSON.stringify(parsedContentTikzAssets));
                }
                formData.append('answer_tikz_assets', JSON.stringify(parsedAnswerTikzAssets));
                formData.append('tikz_code', firstContentTikzAsset
                    ? String(firstContentTikzAsset.tikz_code || '')
                    : String(q.tikz_code || ''));
                formData.append('tikz_reference_image_path', firstContentTikzAsset
                    ? String(firstContentTikzAsset.reference_image_path || '')
                    : String(q.tikz_reference_image_path || ''));
                const currentDuplicatePayload = buildParsedQuestionDuplicateItem(index, saveGeneration);
                const safeImagePaths = currentDuplicatePayload
                    ? currentDuplicatePayload.image_paths
                    : safeDuplicateImagePaths(q.image_paths);
                formData.append('image_paths', JSON.stringify(safeImagePaths));
                const parsedTags = appendDuplicateTagToTags(q.tags || '', autoTag);
                if (parsedTags) {
                    formData.append('tags', parsedTags);
                }
                const batchToken = (typeof window !== 'undefined' && window.__importBatchToken)
                    ? window.__importBatchToken
                    : '';
                if (batchToken) {
                    formData.append('import_batch', batchToken);
                    const titleEl = (typeof document !== 'undefined' && document.getElementById)
                        ? document.getElementById('importPaperTitle')
                        : null;
                    const batchName = titleEl ? String(titleEl.value || '').trim() : '';
                    if (batchName) formData.append('import_batch_name', batchName);
                }
                if (duplicateSnapshotHash) {
                    formData.append('duplicate_snapshot_hash', duplicateSnapshotHash);
                }
                if (duplicateOverride === 'independent') {
                    formData.append('duplicate_override', 'independent');
                }

                const response = await fetch('/api/questions', {
                    method: 'POST',
                    body: formData
                });
                let data;
                try {
                    data = await response.json();
                } catch (error) {
                    throw new Error(`服务器返回错误 HTTP ${response.status || ''}`.trim());
                }
                if (response.ok === false) {
                    if (response.status === 409 && data && data.code === 'duplicate_review_required' &&
                        Array.isArray(data.candidates) && data.candidates.length > 0) {
                        applySaveTimeDuplicateConflict(index, data);
                        focusFirstParsedDuplicate([index]);
                    }
                    throw new Error(data.detail || data.message || `HTTP ${response.status}`);
                }
                if (data.status !== 'success') {
                    throw new Error(data.message || data.detail || '保存失败');
                }

                // The request may finish after a new paper has replaced this
                // index. The backend save remains valid, but stale callbacks
                // must never mutate the new import session or its card.
                if (!isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) return true;
                q.saved = true;
                q.duplicateIndexWarning = String(data.warning || '');
                const statusBadge = card.querySelector('.card-status-badge');
                statusBadge.textContent = '已导入';
                statusBadge.className = 'card-status-badge text-[10px] font-bold px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 animate-pulse';
                saveBtn.className = 'card-save-btn px-4 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 font-bold text-[10px] border border-emerald-300 hover:bg-emerald-100 transition-colors';
                saveBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> <span>再次导入</span>';
                saveBtn.disabled = false;
                const cb = card.querySelector('.card-select-checkbox');
                if (cb) {
                    cb.disabled = true;
                    cb.checked = false;
                    cb.classList.add('opacity-50');
                }
                if (typeof updateSelectedCount === 'function') updateSelectedCount();
                if (options.suppressToast !== true) {
                    if (data.warning) {
                        showToast(String(data.warning), 'warning');
                    } else {
                        showToast(`第 ${index + 1} 题导入成功！`);
                    }
                }
                if (options.suppressRefresh !== true) {
                    loadCategories();
                }
                return true;
            })().catch(err => {
                if (isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) {
                    if (err.message !== 'Content empty' && err.message !== 'Curriculum empty') {
                        showToast(`第 ${index + 1} 题保存出错: ${err.message}`, 'error');
                    }
                }
                throw err;
            }).finally(restoreButton);

            const trackedSave = saveOperation.finally(() => {
                if (parsedQuestionSaveInFlight.get(q) === trackedSave) {
                    parsedQuestionSaveInFlight.delete(q);
                }
            });
            parsedQuestionSaveInFlight.set(q, trackedSave);
            return trackedSave;
        }

        function confirmClearAllParsed() {
            if (blockImportResetWhileSaving()) {
                return;
            }
            if (confirm('确定要清空输入的试卷源码及拆解出的所有草稿题目吗？\n清空后，当前列表中的草稿题目及文件映射将恢复初始状态。')) {
                if (typeof performOrphanedTempCropsCleanup === 'function') {
                    performOrphanedTempCropsCleanup();
                }
                clearAllImportInputs();
                resetImportState(true);
                showToast('已成功清空所有录入数据与拆解草稿！', 'info');
            }
        }

        function clearAllParsedSources() {
            const inputs = document.querySelectorAll('#parsedCardsContainer .card-source');
            if (inputs.length === 0) {
                showToast('当前拆解列表为空！', 'warning');
                return;
            }
            inputs.forEach(input => {
                input.value = '';
            });
            showToast('已成功一键清空所有拆解题目的试卷标题来源！', 'success');
        }

        // 统一适用年级：左侧导入面板与右侧结果头部的两个下拉双向同步，
        // 并即时写入所有已拆解题目，保存时随题目一起提交。
        function getUnifiedApplicableGrade() {
            const left = document.getElementById('importApplicableGrade');
            const batch = document.getElementById('parsedBatchGrade');
            return (left && left.value) || (batch && batch.value) || '';
        }

        function applyUnifiedGradeToParsed() {
            const grade = getUnifiedApplicableGrade();
            (parsedQuestionsData || []).forEach(q => {
                if (q) q.applicable_grade = grade;
            });
        }
        window.applyUnifiedGradeToParsed = applyUnifiedGradeToParsed;

        function setupUnifiedGradeSelectors() {
            const left = document.getElementById('importApplicableGrade');
            const batch = document.getElementById('parsedBatchGrade');
            if (left && !left.dataset.unifiedGradeBound) {
                left.dataset.unifiedGradeBound = '1';
                left.addEventListener('change', () => {
                    if (batch) batch.value = left.value;
                    applyUnifiedGradeToParsed();
                });
            }
            if (batch && !batch.dataset.unifiedGradeBound) {
                batch.dataset.unifiedGradeBound = '1';
                batch.addEventListener('change', () => {
                    if (left) left.value = batch.value;
                    applyUnifiedGradeToParsed();
                });
            }
        }
        window.setupUnifiedGradeSelectors = setupUnifiedGradeSelectors;

        function getCheckedUnsavedIndices() {
            const indices = [];
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !q.saved && cb.checked) {
                    indices.push(idx);
                }
            });
            return indices;
        }

        function updateSelectedCount() {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            let unsavedCount = 0;
            let checkedCount = 0;
            
            checkboxes.forEach(cb => {
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !q.saved) {
                    unsavedCount++;
                    if (cb.checked) {
                        checkedCount++;
                    }
                }
            });
            
            const badge = document.getElementById('selectedCountBadge');
            if (badge) {
                badge.textContent = `已选 ${checkedCount} / ${unsavedCount} 题`;
            }
            
            const selectAllCb = document.getElementById('selectAllCheckbox');
            if (selectAllCb) {
                if (unsavedCount === 0) {
                    selectAllCb.checked = false;
                    selectAllCb.indeterminate = false;
                    selectAllCb.disabled = true;
                } else {
                    selectAllCb.disabled = false;
                    if (checkedCount === unsavedCount) {
                        selectAllCb.checked = true;
                        selectAllCb.indeterminate = false;
                    } else if (checkedCount === 0) {
                        selectAllCb.checked = false;
                        selectAllCb.indeterminate = false;
                    } else {
                        selectAllCb.checked = false;
                        selectAllCb.indeterminate = true;
                    }
                }
            }
            
            const btnText = document.getElementById('saveAllParsedBtnText');
            if (btnText) {
                btnText.textContent = checkedCount > 0 ? `导入选中 (${checkedCount})` : `导入选中题目`;
            }

            const saveAllBtn = document.getElementById('saveAllParsedBtn');
            if (saveAllBtn) {
                if (checkedCount === 0 || parsedBatchSaveInFlight) {
                    saveAllBtn.disabled = true;
                    saveAllBtn.className = "flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-slate-100 text-slate-400 font-bold text-xs border border-slate-200 shadow-sm cursor-not-allowed transition-all";
                } else {
                    saveAllBtn.disabled = false;
                    saveAllBtn.className = "flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-brand-600/80 hover:bg-brand-600 text-white font-bold text-xs backdrop-blur-sm border border-brand-500/20 shadow-sm transition-all active:scale-95 cursor-pointer";
                }
            }
        }

        function toggleSelectAllParsed(checked) {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                if (!cb.disabled) {
                    cb.checked = checked;
                }
            });
            updateSelectedCount();
        }

        function invertSelectParsed() {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                if (!cb.disabled) {
                    cb.checked = !cb.checked;
                }
            });
            updateSelectedCount();
        }

        async function runParsedSavePool(indices, worker, concurrency = 3) {
            const results = new Array(indices.length).fill(null);
            let pointer = 0;
            const workerCount = Math.min(Math.max(1, concurrency), indices.length);
            const workers = Array.from({ length: workerCount }, async () => {
                while (pointer < indices.length) {
                    const position = pointer++;
                    try {
                        results[position] = await worker(indices[position]);
                    } catch (error) {
                        results[position] = null;
                    }
                }
            });
            await Promise.all(workers);
            return results;
        }

        function saveAllParsedQuestions() {
            if (parsedBatchSaveInFlight) return parsedBatchSaveInFlight;
            if (parsedQuestionSaveInFlight.size > 0) {
                showToast('仍有单题正在入库，请等待完成后再启动批量导入。', 'info');
                return Promise.resolve(false);
            }
            const selectedIndices = getCheckedUnsavedIndices();
            const batchGeneration = parsedQuestionsGeneration;

            if (selectedIndices.length === 0) {
                const unsavedCount = parsedQuestionsData.filter(q => !q.saved).length;
                if (unsavedCount === 0) {
                    showToast('所有题目已成功导入！', 'info');
                } else {
                    showToast('请先勾选需要导入的题目！', 'warning');
                }
                return Promise.resolve(false);
            }
            if (selectedIndices.length > 500) {
                showToast('单次最多查重并导入 500 道题，请分批勾选。', 'warning');
                return Promise.resolve(false);
            }
            const invalidIndex = selectedIndices.find(index =>
                !validateParsedQuestionBeforeImport(index, { notify: false, focus: false })
            );
            if (invalidIndex !== undefined) {
                validateParsedQuestionBeforeImport(invalidIndex);
                return Promise.resolve(false);
            }

            const mainBtn = document.getElementById('saveAllParsedBtn');
            if (!mainBtn) return Promise.resolve(false);
            const btnText = document.getElementById('saveAllParsedBtnText');
            const originalText = btnText ? btnText.textContent : '导入选中题目';
            const icon = mainBtn.querySelector('i');
            const originalIconClass = icon ? icon.className : 'fa-solid fa-cloud-arrow-up';

            const operation = (async () => {
                mainBtn.disabled = true;
                document.querySelectorAll('.card-save-btn').forEach(button => {
                    button.disabled = true;
                });
                if (btnText) btnText.textContent = '整批查重中...';
                if (icon) icon.className = 'fa-solid fa-spinner animate-spin';

                // Every batch click performs one fresh, current-DOM check before
                // any question-save POST is allowed to start.
                const outcome = await precheckParsedQuestionDuplicates({
                    indices: selectedIndices,
                    notifyFailure: true
                });
                if (batchGeneration !== parsedQuestionsGeneration) return false;
                if (outcome.stale) {
                    showToast('题目在整批查重期间已修改，请重新点击导入。', 'info');
                    return false;
                }
                if (outcome.invalid) return false;

                let indicesToSave = selectedIndices.slice();
                const independentOverrideIndices = new Set();
                if (outcome.ok) {
                    parsedBatchAutoTags.clear();
                    selectedIndices.forEach(index => {
                        const autoTag = buildDuplicateAutoTag(outcome.results.get(index));
                        if (autoTag) parsedBatchAutoTags.set(index, autoTag);
                    });
                    const suspectIndices = selectedIndices.filter(index =>
                        duplicateResultHasSignal(outcome.results.get(index))
                    );
                    if (suspectIndices.length > 0) {
                        const action = await openParsedDuplicateReviewModal(
                            parsedDuplicateReviewEntries(suspectIndices, outcome.results),
                            'batch'
                        );
                        if (batchGeneration !== parsedQuestionsGeneration) return false;
                        if (action === 'review') {
                            focusFirstParsedDuplicate(suspectIndices);
                            return false;
                        }
                        if (action === 'skip') {
                            const suspectSet = new Set(suspectIndices);
                            indicesToSave = selectedIndices.filter(index => !suspectSet.has(index));
                            if (indicesToSave.length === 0) {
                                showToast('所选题目均需要审查，本次没有执行导入。', 'info');
                                focusFirstParsedDuplicate(suspectIndices);
                                return false;
                            }
                        } else if (action === 'independent') {
                            suspectIndices.forEach(index => independentOverrideIndices.add(index));
                        }
                    }
                }
                // When the check endpoint fails, outcome.ok is false and the
                // operation deliberately continues without claiming uniqueness.

                if (btnText) btnText.textContent = `批量入库中 (0/${indicesToSave.length})...`;
                showToast(`正在以最多 3 个并发任务导入 ${indicesToSave.length} 道题目，请稍候...`);
                let completedCount = 0;
                const results = await runParsedSavePool(indicesToSave, async index => {
                    if (batchGeneration !== parsedQuestionsGeneration) return false;
                    const result = outcome.ok ? outcome.results.get(index) : null;
                    const saved = await saveParsedQuestion(index, {
                        fromBatch: true,
                        suppressRefresh: true,
                        suppressToast: true,
                        duplicateDecisionResolved: true,
                        duplicateOverride: independentOverrideIndices.has(index) ? 'independent' : '',
                        duplicateSnapshotHash: String(result && result.snapshot_hash || ''),
                        autoTag: parsedBatchAutoTags.get(index) || '',
                        localSnapshot: outcome.ok ? (parsedDuplicateSnapshots.get(index) || '') : ''
                    });
                    completedCount += 1;
                    if (btnText && batchGeneration === parsedQuestionsGeneration) {
                        btnText.textContent = `批量入库中 (${completedCount}/${indicesToSave.length})...`;
                    }
                    return saved;
                }, 3);

                if (batchGeneration !== parsedQuestionsGeneration) return false;
                const successCount = results.filter(result => result === true).length;
                const fingerprintWarningCount = indicesToSave.filter(index =>
                    parsedQuestionsData[index] && parsedQuestionsData[index].duplicateIndexWarning
                ).length;
                const fingerprintWarningNote = fingerprintWarningCount > 0
                    ? `；其中 ${fingerprintWarningCount} 道题的查重指纹待后台补建`
                    : '';
                if (successCount > 0) {
                    loadCategories();
                }
                updateSelectedCount();
                const remainingUnsavedCount = parsedQuestionsData.filter(q => !q.saved).length;
                if (remainingUnsavedCount === 0) {
                    showToast(`批量导入完成！共 ${successCount} 道题目已全部成功导入本地库${fingerprintWarningNote}！`, fingerprintWarningCount ? 'warning' : 'success');
                    setTimeout(() => {
                        if (batchGeneration !== parsedQuestionsGeneration) return;
                        if (blockImportResetWhileSaving()) return;
                        clearAllImportInputs();
                        resetImportState(false);
                        closeImportModal();
                    }, 1500);
                } else {
                    showToast(`批量导入已完成！成功: ${successCount}/${indicesToSave.length}${fingerprintWarningNote}。疑似题或失败题已保留供继续审查。`, 'warning');
                }
                return true;
            })().catch(error => {
                if (batchGeneration === parsedQuestionsGeneration) {
                    showToast(`批量导入时发生错误: ${error.message}`, 'error');
                }
                return false;
            }).finally(() => {
                if (parsedBatchSaveInFlight === operation) parsedBatchSaveInFlight = null;
                if (batchGeneration !== parsedQuestionsGeneration) return;
                mainBtn.disabled = false;
                document.querySelectorAll('.card-save-btn').forEach(button => {
                    button.disabled = false;
                });
                if (icon) icon.className = originalIconClass;
                if (btnText) btnText.textContent = originalText;
                updateSelectedCount();
            });
            parsedBatchSaveInFlight = operation;
            updateSelectedCount();
            return operation;
        }


        // ==========================================
        // SIDEBAR QUESTION SOURCE AUTOCOMPLETE FILTER
        // ==========================================
        function setupSourceFilterAutocomplete() {
            const sourceInput = document.getElementById('filterSource');
            const suggestionsDiv = document.getElementById('filterSourceSuggestions');
            const toggleBtn = document.getElementById('toggleFilterSourceBtn');
            const clearBtn = document.getElementById('clearFilterSourceBtn');
            const chevronIcon = document.getElementById('chevronFilterSourceIcon');
            
            if (!sourceInput || !suggestionsDiv) return;

            function fetchSources(callback) {
                fetch('/api/sources')
                    .then(r => r.json())
                    .then(sources => {
                        allSourcesList = sources;
                        if (callback) callback(sources);
                    })
                    .catch(err => {
                        console.error('Failed to fetch sources:', err);
                    });
            }
            
            function renderSuggestions(list) {
                suggestionsDiv.innerHTML = '';
                if (list.length === 0) {
                    suggestionsDiv.innerHTML = '<div class="px-3 py-2 text-[10px] text-slate-400 italic text-center select-none">无匹配来源</div>';
                    suggestionsDiv.classList.remove('hidden');
                    chevronIcon.classList.add('rotate-180');
                    return;
                }

                list.forEach(src => {
                    const item = document.createElement('div');
                    item.className = "px-3 py-2 hover:bg-slate-50 text-xs text-slate-700 cursor-pointer select-none truncate font-medium transition-colors border-b border-slate-100/50 last:border-b-0";
                    item.textContent = src;
                    item.addEventListener('click', () => {
                        sourceInput.value = src;
                        suggestionsDiv.classList.add('hidden');
                        chevronIcon.classList.remove('rotate-180');
                        updateClearButtonVisibility();
                    });
                    suggestionsDiv.appendChild(item);
                });
                suggestionsDiv.classList.remove('hidden');
                chevronIcon.classList.add('rotate-180');
            }
            
            function updateClearButtonVisibility() {
                if (sourceInput.value.trim() !== '') {
                    clearBtn.classList.remove('hidden');
                } else {
                    clearBtn.classList.add('hidden');
                }
            }
            
            sourceInput.addEventListener('focus', () => {
                fetchSources(sources => {
                    const val = sourceInput.value.trim().toLowerCase();
                    if (val === '') {
                        renderSuggestions(sources);
                    } else {
                        const filtered = sources.filter(s => s.toLowerCase().includes(val));
                        renderSuggestions(filtered);
                    }
                });
            });
            
            sourceInput.addEventListener('input', () => {
                updateClearButtonVisibility();
                const val = sourceInput.value.trim().toLowerCase();
                if (val === '') {
                    renderSuggestions(allSourcesList);
                } else {
                    const filtered = allSourcesList.filter(s => s.toLowerCase().includes(val));
                    renderSuggestions(filtered);
                }
            });
            
            sourceInput.addEventListener('change', () => {
            });
            
            sourceInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                }
            });
            
            toggleBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!suggestionsDiv.classList.contains('hidden')) {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                } else {
                    sourceInput.focus();
                }
            });
            
            clearBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                sourceInput.value = '';
                updateClearButtonVisibility();
                suggestionsDiv.classList.add('hidden');
                chevronIcon.classList.remove('rotate-180');
            });
            
            document.addEventListener('click', (e) => {
                if (!e.target.closest('#filterSourceContainer')) {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                }
            });
        }

        // ==========================================
        // LaTeX TITLE AUTO-EXTRACTION HELPERS
        // ==========================================
        function extractLatexBraceGroup(latex, start) {
            if (!latex || start < 0 || latex[start] !== '{') return null;
            let depth = 0;
            for (let i = start; i < latex.length; i++) {
                let slashCount = 0;
                for (let j = i - 1; j >= 0 && latex[j] === '\\'; j--) slashCount++;
                const escaped = slashCount % 2 === 1;
                if (latex[i] === '{' && !escaped) depth++;
                if (latex[i] === '}' && !escaped) {
                    depth--;
                    if (depth === 0) return {content: latex.slice(start + 1, i), end: i + 1};
                }
            }
            return null;
        }

        function findLatexCommandGroup(latex, commands) {
            for (const command of commands) {
                const pattern = new RegExp('\\\\' + command + '\\b', 'g');
                let match;
                while ((match = pattern.exec(latex)) !== null) {
                    let cursor = match.index + match[0].length;
                    while (cursor < latex.length && /\s/.test(latex[cursor])) cursor++;
                    const group = extractLatexBraceGroup(latex, cursor);
                    if (group) return {command, content: group.content};
                }
            }
            return null;
        }

        function extractTitleFromLatex(latex) {
            if (!latex) return "";
            const commandTitle = findLatexCommandGroup(latex, ['title', 'chead', 'lhead', 'rhead']);
            if (commandTitle) {
                const clean = cleanLatexFormatting(commandTitle.content);
                if (clean && !clean.includes('页') && !clean.includes('绝密')) return clean;
            }

            const topPart = latex.slice(0, 1500);
            const match = topPart.match(/\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}/);
            if (match && match[1]) {
                let content = match[1].trim();
                content = content.replace(/\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt)/g, '');
                content = content.replace(/\\textbf\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/\\heiti\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/\\kt\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/[\{\}]/g, '');
                
                const lines = content.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('%') && !l.includes('\\includegraphics') && !l.includes('\\chead') && !l.includes('\\lhead'));
                if (lines.length > 0) {
                    for (let line of lines) {
                        line = cleanLatexFormatting(line);
                        if (line.includes("中学") || line.includes("试卷") || line.includes("试题") || line.includes("考试") || line.includes("期") || line.includes("测试") || line.includes("年")) {
                            return line;
                        }
                    }
                    return cleanLatexFormatting(lines[0]);
                }
            }
            
            return "";
        }

        function cleanLatexFormatting(str) {
            if (!str) return "";
            let cleaned = str;
            for (let i = 0; i < 5; i++) {
                const next = cleaned
                    .replace(/\\text(?:bf|it|sf|tt)\s*\{([^{}]*)\}/g, '$1')
                    .replace(/\\(?:heiti|kt|kaishu|songti|fangsong)\s*\{([^{}]*)\}/g, '$1');
                if (next === cleaned) break;
                cleaned = next;
            }
            return cleaned
                .replace(/\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt)/g, '')
                .replace(/\\sffamily/g, '')
                .replace(/\\centering/g, '')
                .replace(/[\{\}]/g, '')
                .replace(/\\\\/g, '')
                .trim();
        }

        // 解析日志面板的显隐。
        // 规则：只要"跑过一次"就留在左侧导航栏底部，**结果出来也不收起**——
        // 用户要在审查阶段回看每步耗时与总耗时。只有初始空态（什么都没跑）才隐藏。
        // 直接观察加载态与结果容器的 class 变化，避免在散落各处的开关点逐一补同步调用。
        function setupImportLogPanelSync() {
            const loadingState = document.getElementById('importLoadingState');
            const resultsWrapper = document.getElementById('parsedQuestionsWrapper');
            const logPanel = document.getElementById('importLogPanel');
            const logsConsole = document.getElementById('importLogsConsole');
            const chevron = document.getElementById('importLogPanelChevron');
            if (!loadingState || !logPanel) return;
            const sync = () => {
                const isLoading = !loadingState.classList.contains('hidden');
                const hasResults = Boolean(
                    resultsWrapper && !resultsWrapper.classList.contains('hidden')
                );
                const visible = isLoading || hasResults;
                logPanel.classList.toggle('hidden', !visible);
                logPanel.classList.toggle('flex-col', visible);
                // 任务运行时自动展开，方便看实时日志；结果出来后保持用户可手动折叠
                if (visible && isLoading && logsConsole) {
                    logsConsole.classList.remove('hidden');
                    if (chevron) chevron.classList.remove('rotate-180');
                }
            };
            sync();
            if (typeof MutationObserver === 'function') {
                const observer = new MutationObserver(sync);
                observer.observe(loadingState, { attributes: true, attributeFilter: ['class'] });
                if (resultsWrapper) {
                    observer.observe(
                        resultsWrapper, { attributes: true, attributeFilter: ['class'] }
                    );
                }
            }
        }

        function toggleImportLogPanel() {
            const consoleDiv = document.getElementById('importLogsConsole');
            const chevron = document.getElementById('importLogPanelChevron');
            if (!consoleDiv || !chevron) return;
            const expanding = consoleDiv.classList.contains('hidden');
            consoleDiv.classList.toggle('hidden', !expanding);
            chevron.classList.toggle('rotate-180', !expanding);
        }

        // App Initialization
        document.addEventListener('DOMContentLoaded', () => {
            // Check configs
            fetchConfigStatus();

            // 解析日志面板跟随加载态显隐
            setupImportLogPanelSync();
            
            // Load and update drafts count
            updateDraftCountBadge();

            // Load Cascade Category Tree
            loadCategories();

            // 若存在 24 小时内完成过的解析任务，显示“找回上次解析”入口
            refreshPdfTaskRecoveryButton();

            // 「忽略缓存」只对云端路线有意义，非云路线时整行收起
            setupMineruCacheControls();

            // 统一适用年级下拉：左右两个入口双向同步
            setupUnifiedGradeSelectors();

            // 来源字段智能联想 + 文件名预填
            initSourceSmartFields();
            
            // Load saved questions list
            
            // Load and populate related questions dropdown
            refreshRelatedDropdown();

            // Set up related question display number input two-way synchronization
            const relatedNumInput = document.getElementById('editRelatedQuestionNum');
            const relatedSelect = document.getElementById('editRelatedQuestion');
            if (relatedNumInput && relatedSelect) {
                relatedNumInput.addEventListener('input', () => {
                    const val = relatedNumInput.value.trim();
                    if (!val) {
                        relatedSelect.value = '';
                    } else {
                        let found = false;
                        for (let i = 0; i < relatedSelect.options.length; i++) {
                            const opt = relatedSelect.options[i];
                            if (opt.getAttribute('data-seq-num') === val) {
                                relatedSelect.value = opt.value;
                                found = true;
                                break;
                            }
                        }
                        if (!found) {
                            relatedSelect.value = '';
                        }
                    }
                });

                relatedSelect.addEventListener('change', () => {
                    const selectedOpt = relatedSelect.options[relatedSelect.selectedIndex];
                    if (selectedOpt && selectedOpt.value) {
                        relatedNumInput.value = selectedOpt.getAttribute('data-seq-num') || '';
                    } else {
                        relatedNumInput.value = '';
                    }
                });
            }

            // Set up debounced event listeners for realtime markdown preview
            setupRealtimePreviews();

            // Setup drag-and-drop & clipboard listeners for illustrations & OCR
            setupUploadHandlers();
            
            // Setup resizers
            initResizers();

            // Setup searchable source filter autocomplete
            setupSourceFilterAutocomplete();

            // Setup LaTeX batch import handlers
            setupImportFileHandlers();

            // Initialize empty original state
            backupEditorState(null, null);

            // ================== Shared on-demand multimodal TikZ workbench ==================
            const tikzWorkbenchState = {
                target: 'answer',
                referenceFile: null,
                referenceObjectUrl: '',
                referencePath: '',
                compiledPath: '',
                compiledCode: '',
                editingAssetId: null,
                caretStart: 0,
                caretEnd: 0,
                busy: false,
                editorSession: null
            };

            function setTikzWorkbenchStatus(text, tone = 'idle') {
                const status = document.getElementById('answerTikzWorkbenchStatus');
                if (!status) return;
                status.textContent = text;
                const tones = {
                    idle: 'bg-slate-100 text-slate-500',
                    busy: 'bg-indigo-50 text-indigo-700',
                    success: 'bg-emerald-50 text-emerald-700',
                    error: 'bg-red-50 text-red-700',
                    warning: 'bg-amber-50 text-amber-700'
                };
                status.className = `rounded-md px-2 py-1 text-[10px] font-semibold ${tones[tone] || tones.idle}`;
            }

            function setTikzWorkbenchBusy(isBusy, message = '') {
                tikzWorkbenchState.busy = isBusy;
                const loading = document.getElementById('answerTikzWorkbenchLoading');
                const loadingText = document.getElementById('answerTikzWorkbenchLoadingText');
                const generateButton = document.getElementById('generateAnswerTikzBtn');
                const compileButton = document.getElementById('compileTikzWorkbenchBtn');
                const insertButton = document.getElementById('insertAnswerTikzWorkbenchBtn');
                const source = document.getElementById('answerTikzWorkbenchCode');
                if (loading) {
                    loading.classList.toggle('hidden', !isBusy);
                    loading.classList.toggle('flex', isBusy);
                }
                if (loadingText && message) loadingText.textContent = message;
                for (const button of [generateButton, compileButton]) {
                    if (!button) continue;
                    button.disabled = isBusy;
                    button.setAttribute('aria-busy', String(isBusy));
                }
                if (insertButton) {
                    const compiledSourceIsCurrent = Boolean(
                        tikzWorkbenchState.compiledPath
                        && source
                        && source.value.trim() === tikzWorkbenchState.compiledCode
                    );
                    insertButton.disabled = isBusy || !compiledSourceIsCurrent;
                }
                if (isBusy) setTikzWorkbenchStatus(message || '处理中…', 'busy');
            }

            function resetTikzWorkbenchPreview() {
                tikzWorkbenchState.compiledPath = '';
                tikzWorkbenchState.compiledCode = '';
                const preview = document.getElementById('answerTikzWorkbenchPreviewImage');
                const placeholder = document.getElementById('answerTikzWorkbenchPlaceholder');
                const insertButton = document.getElementById('insertAnswerTikzWorkbenchBtn');
                if (preview) {
                    preview.src = '';
                    preview.classList.add('hidden');
                }
                if (placeholder) placeholder.classList.remove('hidden');
                if (insertButton) insertButton.disabled = true;
            }

            function clearTikzReference() {
                if (tikzWorkbenchState.referenceObjectUrl) {
                    URL.revokeObjectURL(tikzWorkbenchState.referenceObjectUrl);
                }
                tikzWorkbenchState.referenceFile = null;
                tikzWorkbenchState.referenceObjectUrl = '';
                tikzWorkbenchState.referencePath = '';
                const input = document.getElementById('answerTikzReferenceInput');
                const preview = document.getElementById('answerTikzReferencePreview');
                const previewWrap = document.getElementById('answerTikzReferencePreviewWrap');
                const placeholder = document.getElementById('answerTikzReferencePlaceholder');
                if (input) input.value = '';
                if (preview) preview.src = '';
                if (previewWrap) {
                    previewWrap.classList.add('hidden');
                    previewWrap.classList.remove('flex');
                }
                if (placeholder) placeholder.classList.remove('hidden');
            }

            function setTikzReferencePath(path, sourceLabel = '已载入题目参考图') {
                const safePath = window.MathBankSafe.safeImageUrl(path);
                if (!safePath) return;
                clearTikzReference();
                tikzWorkbenchState.referencePath = safePath;
                const preview = document.getElementById('answerTikzReferencePreview');
                const previewWrap = document.getElementById('answerTikzReferencePreviewWrap');
                const placeholder = document.getElementById('answerTikzReferencePlaceholder');
                if (preview) preview.src = safePath;
                if (previewWrap) {
                    previewWrap.classList.remove('hidden');
                    previewWrap.classList.add('flex');
                }
                if (placeholder) placeholder.classList.add('hidden');
                setTikzWorkbenchStatus(sourceLabel, 'success');
            }

            function setTikzReferenceFile(file) {
                if (!file) return;
                if (!String(file.type || '').startsWith('image/')) {
                    showToast('参考文件必须是图片。', 'error');
                    return;
                }
                if (file.size > 10 * 1024 * 1024) {
                    showToast('参考图不能超过 10MB。', 'error');
                    return;
                }
                clearTikzReference();
                resetTikzWorkbenchPreview();
                tikzWorkbenchState.referenceFile = file;
                tikzWorkbenchState.referenceObjectUrl = URL.createObjectURL(file);
                const preview = document.getElementById('answerTikzReferencePreview');
                const previewWrap = document.getElementById('answerTikzReferencePreviewWrap');
                const placeholder = document.getElementById('answerTikzReferencePlaceholder');
                if (preview) preview.src = tikzWorkbenchState.referenceObjectUrl;
                if (previewWrap) {
                    previewWrap.classList.remove('hidden');
                    previewWrap.classList.add('flex');
                }
                if (placeholder) placeholder.classList.add('hidden');
                setTikzWorkbenchStatus('已添加参考图，请生成绘图', 'success');
            }

            function tikzContextText() {
                const useContext = document.getElementById('answerTikzUseContext');
                if (useContext && !useContext.checked) return '';
                const content = document.getElementById('editContent').value.trim();
                const answer = document.getElementById('editAnswerMarkdown').value.trim();
                if (tikzWorkbenchState.target === 'content') {
                    return `【题干】\n${content || '暂无'}`;
                }
                return `【题干】\n${content || '暂无'}\n\n【当前解答】\n${answer || '暂无'}`;
            }

            function normalizedTikzAssets(target) {
                const assets = target === 'content'
                    ? TikzState.contentAssets
                    : TikzState.answerAssets;
                if (!Array.isArray(assets)) return [];
                return assets.filter(asset => {
                    return asset && typeof asset === 'object'
                        && typeof asset.id === 'string'
                        && window.MathBankSafe.safeImageUrl(asset.image_path)
                        && typeof asset.tikz_code === 'string'
                        && (!asset.reference_image_path || window.MathBankSafe.safeImageUrl(asset.reference_image_path));
                });
            }

            function normalizedContentTikzAssets() {
                return normalizedTikzAssets('content');
            }

            function normalizedAnswerTikzAssets() {
                return normalizedTikzAssets('answer');
            }

            function registerAutoTikzAsset(target, { tikzCode, imagePath, referenceImagePath }) {
                const safeImagePath = window.MathBankSafe.safeImageUrl(imagePath);
                const safeReferencePath = window.MathBankSafe.safeImageUrl(referenceImagePath);
                if (!tikzCode || !safeImagePath) return;
                const assets = target === 'content'
                    ? TikzState.contentAssets
                    : TikzState.answerAssets;
                const existingIndex = assets.findIndex(
                    asset => asset.image_path === safeImagePath
                );
                const asset = {
                    id: existingIndex >= 0
                        ? assets[existingIndex].id
                        : `tikz_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                    image_path: safeImagePath,
                    tikz_code: tikzCode,
                    instruction: ''
                };
                if (safeReferencePath) asset.reference_image_path = safeReferencePath;
                if (existingIndex >= 0) assets.splice(existingIndex, 1, asset);
                else assets.push(asset);
                const uploadedPaths = target === 'content' ? uploadedImages : uploadedAnswerImages;
                if (!uploadedPaths.includes(safeImagePath)) {
                    uploadedPaths.push(safeImagePath);
                }
                if (typeof renderIllustrationBadges === 'function') renderIllustrationBadges();
                if (target === 'content') window.renderContentTikzAssets();
                else window.renderAnswerTikzAssets();
            }

            window.registerAutoContentTikzAsset = function(payload) {
                registerAutoTikzAsset('content', payload);
            };

            window.registerAutoAnswerTikzAsset = function(payload) {
                registerAutoTikzAsset('answer', payload);
            };

            function renderTikzAssetCollection(target) {
                const isContent = target === 'content';
                const assets = normalizedTikzAssets(target);
                if (isContent) TikzState.contentAssets = assets;
                else TikzState.answerAssets = assets;
                const panel = document.getElementById(
                    isContent ? 'contentTikzAssetsPanel' : 'answerTikzAssetsPanel'
                );
                const list = document.getElementById(
                    isContent ? 'contentTikzAssetsList' : 'answerTikzAssetsList'
                );
                const count = document.getElementById(
                    isContent ? 'contentTikzAssetsCount' : 'answerTikzAssetsCount'
                );
                if (!panel || !list || !count) return;
                list.replaceChildren();
                count.textContent = `${assets.length} 幅`;
                panel.classList.toggle('hidden', assets.length === 0);

                assets.forEach((asset, index) => {
                    const safePath = window.MathBankSafe.safeImageUrl(asset.image_path);
                    const card = document.createElement('div');
                    card.className = `answer-tikz-asset-card${isContent ? ' content-tikz-asset-card' : ''}`;

                    const image = document.createElement('img');
                    image.src = safePath;
                    image.alt = `${isContent ? '题干' : '解答'} TikZ 插图 ${index + 1} 缩略图`;
                    image.loading = 'lazy';
                    image.decoding = 'async';

                    const label = document.createElement('span');
                    label.className = 'min-w-0 max-w-[140px]';
                    const labelTitle = document.createElement('span');
                    labelTitle.className = 'block truncate text-[11px] font-semibold';
                    labelTitle.textContent = `${isContent ? '题干' : '解答'} TikZ 绘图 ${index + 1}`;
                    const labelMeta = document.createElement('span');
                    labelMeta.className = 'mt-0.5 block truncate text-[9px] text-slate-500';
                    labelMeta.textContent = asset.reference_image_path
                        ? (isContent ? '已绑定题目参考图' : '已绑定解答参考图')
                        : '可继续编辑';
                    label.append(labelTitle, labelMeta);

                    const editButton = document.createElement('button');
                    editButton.type = 'button';
                    editButton.className = 'flex min-h-[44px] min-w-[44px] cursor-pointer items-center justify-center text-indigo-600 transition-colors hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-500/25';
                    editButton.setAttribute('aria-label', `修改${isContent ? '题干' : '解答'} TikZ 绘图 ${index + 1}`);
                    editButton.innerHTML = '<i class="fa-solid fa-pen" aria-hidden="true"></i>';
                    editButton.addEventListener('click', () => {
                        if (isContent) window.openContentTikzWorkbench(asset.id);
                        else window.openAnswerTikzWorkbench(asset.id);
                    });

                    const deleteButton = document.createElement('button');
                    deleteButton.type = 'button';
                    deleteButton.className = 'flex min-h-[44px] min-w-[44px] cursor-pointer items-center justify-center text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600 focus:outline-none focus:ring-2 focus:ring-red-500/25';
                    deleteButton.setAttribute('aria-label', `删除${isContent ? '题干' : '解答'} TikZ 绘图 ${index + 1}`);
                    deleteButton.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
                    deleteButton.addEventListener('click', () => {
                        if (isContent) window.deleteContentTikzAsset(asset.id);
                        else window.deleteAnswerTikzAsset(asset.id);
                    });

                    card.append(image, label, editButton, deleteButton);
                    list.appendChild(card);
                });
            }

            window.renderContentTikzAssets = function() {
                renderTikzAssetCollection('content');
            };

            window.renderAnswerTikzAssets = function() {
                renderTikzAssetCollection('answer');
            };

            window.openTikzWorkbench = function(target = 'answer', assetId = null, referencePath = null) {
                const modal = document.getElementById('answerTikzWorkbenchModal');
                const surface = modal && modal.querySelector('[data-modal-surface]');
                const normalizedTarget = target === 'content' ? 'content' : 'answer';
                const targetInput = document.getElementById(
                    normalizedTarget === 'content' ? 'editContent' : 'editAnswerMarkdown'
                );
                if (!modal || !surface || !targetInput) return;

                const asset = assetId
                    ? normalizedTikzAssets(normalizedTarget).find(item => item.id === assetId)
                    : null;
                tikzWorkbenchState.editorSession = EditorState.snapshot();
                tikzWorkbenchState.target = normalizedTarget;
                tikzWorkbenchState.editingAssetId = asset ? asset.id : null;
                tikzWorkbenchState.caretStart = targetInput.selectionStart || 0;
                tikzWorkbenchState.caretEnd = targetInput.selectionEnd || tikzWorkbenchState.caretStart;
                clearTikzReference();
                resetTikzWorkbenchPreview();

                // 从已有题目图片卡片打开时，直接把该图作为参考图载入，
                // 用户点"AI 生成"即可用这张图重绘 TikZ。
                if (referencePath && !asset) {
                    setTikzReferencePath(referencePath, '已载入题目原图作为参考');
                }

                document.getElementById('answerTikzInstruction').value = asset ? (asset.instruction || '') : '';
                document.getElementById('answerTikzWorkbenchCode').value = asset ? asset.tikz_code : '';
                const targetLabel = normalizedTarget === 'content' ? '题干' : '解答';
                document.getElementById('answerTikzWorkbenchHeading').textContent = asset
                    ? `修改${targetLabel} TikZ 绘图`
                    : `新增${targetLabel} TikZ 绘图`;
                document.getElementById('tikzWorkbenchTargetBadge').textContent = asset
                    ? `修改${targetLabel}绘图`
                    : `新增到${targetLabel}`;
                document.getElementById('insertAnswerTikzWorkbenchLabel').textContent = asset
                    ? `更新${targetLabel}中的绘图`
                    : `新增到${targetLabel}当前光标位置`;
                document.getElementById('tikzWorkbenchDescription').textContent = asset
                    ? `修改当前${targetLabel}绘图；预览通过后只更新这一幅图。`
                    : `创建一幅新的${targetLabel}绘图；不会覆盖已有绘图。`;
                document.getElementById('tikzWorkbenchContextHint').textContent = normalizedTarget === 'content'
                    ? '向 AI 提供当前题干，用于校正点名和几何关系。'
                    : '向 AI 提供题干与已输入解答，用于校正点名和几何关系。';
                document.getElementById('tikzWorkbenchInsertHint').textContent = asset
                    ? `预览通过后只更新当前${targetLabel}绘图，不会新增副本。`
                    : `预览通过后新增到${targetLabel}当前光标位置，不会覆盖已有绘图。`;
                document.getElementById('tikzWorkbenchReferenceHint').textContent = normalizedTarget === 'content'
                    ? '如果该图由 OCR 自动生成，此处会直接载入当时的原题图。'
                    : '建议只截取几何插图区域，AI 将参考其拓扑、标注与实虚线。';

                if (asset && asset.reference_image_path) {
                    setTikzReferencePath(
                        asset.reference_image_path,
                        normalizedTarget === 'content' ? '已载入 OCR 原题参考图' : '已载入参考图'
                    );
                }

                if (asset) {
                    const safePath = window.MathBankSafe.safeImageUrl(asset.image_path);
                    if (safePath) {
                        const preview = document.getElementById('answerTikzWorkbenchPreviewImage');
                        document.getElementById('answerTikzWorkbenchPlaceholder').classList.add('hidden');
                        preview.src = safePath;
                        preview.classList.remove('hidden');
                        tikzWorkbenchState.compiledPath = safePath;
                        tikzWorkbenchState.compiledCode = asset.tikz_code;
                        document.getElementById('insertAnswerTikzWorkbenchBtn').disabled = false;
                        setTikzWorkbenchStatus(
                            asset.reference_image_path ? '已载入源码与参考图' : '已载入可编辑源码',
                            'success'
                        );
                    } else {
                        setTikzWorkbenchStatus('已载入源码，请编译预览', 'warning');
                    }
                } else {
                    setTikzWorkbenchStatus('等待输入', 'idle');
                }

                modal.classList.remove('hidden');
                modal.classList.add('flex');
                window.MathBankModal.open(modal, { onEscape: window.closeTikzWorkbench });
                requestAnimationFrame(() => {
                    modal.classList.remove('opacity-0');
                    surface.classList.remove('scale-95');
                    surface.classList.add('scale-100');
                });
            };

            window.openContentTikzWorkbench = function(assetId = null) {
                window.openTikzWorkbench('content', assetId);
            };

            window.openAnswerTikzWorkbench = function(assetId = null) {
                window.openTikzWorkbench('answer', assetId);
            };

            window.closeTikzWorkbench = function() {
                if (tikzWorkbenchState.busy) {
                    showToast('TikZ 正在生成或编译，请稍候。', 'info');
                    return;
                }
                const modal = document.getElementById('answerTikzWorkbenchModal');
                const surface = modal && modal.querySelector('[data-modal-surface]');
                if (!modal || modal.classList.contains('hidden')) return;
                window.MathBankModal.close(modal);
                modal.classList.add('opacity-0');
                if (surface) {
                    surface.classList.remove('scale-100');
                    surface.classList.add('scale-95');
                }
                setTimeout(() => {
                    modal.classList.add('hidden');
                    modal.classList.remove('flex');
                    clearTikzReference();
                }, 200);
            };

            async function renderTikzWorkbenchCode(tikzCode) {
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                const response = await fetch('/api/render_tikz', { method: 'POST', body: formData });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || data.status !== 'success') {
                    throw new Error(data.detail || data.message || 'TikZ 编译失败');
                }
                if (!EditorState.isCurrent(tikzWorkbenchState.editorSession)) {
                    throw new Error('当前编辑题目已变更，本次绘图结果已丢弃。');
                }
                const safePath = window.MathBankSafe.safeImageUrl(data.image_path);
                if (!safePath) throw new Error('服务器返回了无效的预览图路径。');

                const preview = document.getElementById('answerTikzWorkbenchPreviewImage');
                document.getElementById('answerTikzWorkbenchPlaceholder').classList.add('hidden');
                preview.src = `${safePath}?t=${Date.now()}`;
                preview.classList.remove('hidden');
                tikzWorkbenchState.compiledPath = safePath;
                tikzWorkbenchState.compiledCode = tikzCode;
                document.getElementById('insertAnswerTikzWorkbenchBtn').disabled = false;
                setTikzWorkbenchStatus(
                    tikzWorkbenchState.editingAssetId ? '编译成功，可更新' : '编译成功，可新增',
                    'success'
                );
                return safePath;
            }

            window.generateTikzWithAI = async function() {
                const instruction = document.getElementById('answerTikzInstruction').value.trim();
                const existingTikz = document.getElementById('answerTikzWorkbenchCode').value.trim();
                if (
                    !instruction
                    && !existingTikz
                    && !tikzWorkbenchState.referenceFile
                    && !tikzWorkbenchState.referencePath
                ) {
                    showToast('请输入绘图要求、添加参考图或填入 TikZ 源码。', 'error');
                    return;
                }
                setTikzWorkbenchBusy(true, 'AI 正在构造几何关系…');
                try {
                    const formData = new FormData();
                    formData.append('instruction', instruction);
                    formData.append('context', tikzContextText());
                    formData.append('existing_tikz', existingTikz);
                    if (tikzWorkbenchState.referenceFile) {
                        formData.append('reference_image', tikzWorkbenchState.referenceFile);
                    } else if (tikzWorkbenchState.referencePath) {
                        formData.append('reference_image_path', tikzWorkbenchState.referencePath);
                    }
                    const response = await fetch('/api/ai/draw_tikz', { method: 'POST', body: formData });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok || data.status !== 'success' || !data.tikz_code) {
                        throw new Error(data.detail || data.message || 'AI 未返回可用的 TikZ 源码');
                    }
                    if (!EditorState.isCurrent(tikzWorkbenchState.editorSession)) return;
                    if (data.reference_image_path) {
                        setTikzReferencePath(
                            data.reference_image_path,
                            '已保留新参考图，保存题目后将清理旧图'
                        );
                    }
                    document.getElementById('answerTikzWorkbenchCode').value = data.tikz_code;
                    document.getElementById('answerTikzSourceDetails').open = false;
                    setTikzWorkbenchBusy(true, '源码已生成，正在安全编译…');
                    await renderTikzWorkbenchCode(data.tikz_code);
                } catch (error) {
                    setTikzWorkbenchStatus('生成或编译失败', 'error');
                    showToast(error.message || 'AI TikZ 绘图失败', 'error');
                } finally {
                    setTikzWorkbenchBusy(false);
                }
            };

            window.compileTikzWorkbench = async function() {
                const tikzCode = document.getElementById('answerTikzWorkbenchCode').value.trim();
                if (!tikzCode) {
                    showToast('请先输入或生成 TikZ 源码。', 'error');
                    return;
                }
                setTikzWorkbenchBusy(true, '正在安全编译 TikZ…');
                try {
                    await renderTikzWorkbenchCode(tikzCode);
                } catch (error) {
                    setTikzWorkbenchStatus('编译失败', 'error');
                    document.getElementById('answerTikzSourceDetails').open = true;
                    showToast(error.message || 'TikZ 编译失败', 'error');
                } finally {
                    setTikzWorkbenchBusy(false);
                }
            };

            function insertMarkdownAtSavedCursor(textarea, markdown) {
                const start = Math.min(tikzWorkbenchState.caretStart, textarea.value.length);
                const end = Math.min(tikzWorkbenchState.caretEnd, textarea.value.length);
                const before = textarea.value.slice(0, start);
                const after = textarea.value.slice(end);
                const prefix = before && !before.endsWith('\n\n') ? (before.endsWith('\n') ? '\n' : '\n\n') : '';
                const suffix = after && !after.startsWith('\n\n') ? (after.startsWith('\n') ? '\n' : '\n\n') : '';
                const insertion = `${prefix}${markdown}${suffix}`;
                textarea.value = before + insertion + after;
                const nextPosition = before.length + insertion.length;
                textarea.setSelectionRange(nextPosition, nextPosition);
            }

            window.applyTikzWorkbench = function() {
                const tikzCode = document.getElementById('answerTikzWorkbenchCode').value.trim();
                const imagePath = window.MathBankSafe.safeImageUrl(tikzWorkbenchState.compiledPath);
                if (!imagePath || !tikzCode || tikzCode !== tikzWorkbenchState.compiledCode) {
                    showToast('源码已改动或尚未编译，请先点击“编译预览”。', 'error');
                    return;
                }
                if (!EditorState.isCurrent(tikzWorkbenchState.editorSession)) {
                    showToast('当前编辑题目已变更，请重新打开绘图工作台。', 'error');
                    return;
                }

                const instruction = document.getElementById('answerTikzInstruction').value.trim();
                const referencePath = window.MathBankSafe.safeImageUrl(
                    tikzWorkbenchState.referencePath
                );
                if (tikzWorkbenchState.target === 'content') {
                    const contentInput = document.getElementById('editContent');
                    if (!contentInput) return;
                    const editingIndex = TikzState.contentAssets.findIndex(
                        asset => asset.id === tikzWorkbenchState.editingAssetId
                    );
                    const assetId = editingIndex >= 0
                        ? TikzState.contentAssets[editingIndex].id
                        : `tikz_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
                    const nextAsset = {
                        id: assetId,
                        image_path: imagePath,
                        tikz_code: tikzCode,
                        instruction: instruction
                    };
                    if (referencePath) nextAsset.reference_image_path = referencePath;

                    let oldPath = '';
                    if (editingIndex >= 0) {
                        oldPath = TikzState.contentAssets[editingIndex].image_path;
                        if (oldPath && contentInput.value.includes(oldPath)) {
                            contentInput.value = contentInput.value.split(oldPath).join(imagePath);
                        } else if (!contentInput.value.includes(imagePath)) {
                            insertMarkdownAtSavedCursor(contentInput, `![TikZ 几何图](${imagePath})`);
                        }
                        TikzState.contentAssets.splice(editingIndex, 1, nextAsset);
                    } else {
                        insertMarkdownAtSavedCursor(contentInput, `![TikZ 几何图](${imagePath})`);
                        TikzState.contentAssets.push(nextAsset);
                    }
                    if (!uploadedImages.includes(imagePath)) uploadedImages.push(imagePath);
                    contentInput.dispatchEvent(new Event('input'));
                    if (oldPath && oldPath !== imagePath) {
                        removeUploadedPathIfUnused(oldPath);
                    }
                    renderIllustrationBadges();
                    window.renderContentTikzAssets();
                    window.closeTikzWorkbench();
                    contentInput.focus({ preventScroll: true });
                    showToast(editingIndex >= 0
                        ? 'TikZ 绘图已更新，请保存题目。'
                        : '已新增一幅 TikZ 绘图到题干。');
                    return;
                }

                const answerInput = document.getElementById('editAnswerMarkdown');
                const editingIndex = TikzState.answerAssets.findIndex(
                    asset => asset.id === tikzWorkbenchState.editingAssetId
                );
                const assetId = editingIndex >= 0
                    ? TikzState.answerAssets[editingIndex].id
                    : `tikz_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
                const nextAsset = {
                    id: assetId,
                    image_path: imagePath,
                    tikz_code: tikzCode,
                    instruction: instruction
                };
                if (referencePath) nextAsset.reference_image_path = referencePath;

                if (editingIndex >= 0) {
                    const oldPath = TikzState.answerAssets[editingIndex].image_path;
                    if (answerInput.value.includes(oldPath)) {
                        answerInput.value = answerInput.value.split(oldPath).join(imagePath);
                    } else {
                        insertMarkdownAtSavedCursor(answerInput, `![TikZ 几何图](${imagePath})`);
                    }
                    TikzState.answerAssets.splice(editingIndex, 1, nextAsset);
                } else {
                    insertMarkdownAtSavedCursor(answerInput, `![TikZ 几何图](${imagePath})`);
                    TikzState.answerAssets.push(nextAsset);
                }

                answerInput.dispatchEvent(new Event('input'));
                if (typeof window.syncAnswerImagesFromMarkdown === 'function') {
                    window.syncAnswerImagesFromMarkdown();
                }
                window.renderAnswerTikzAssets();
                window.closeTikzWorkbench();
                answerInput.focus({ preventScroll: true });
                showToast(editingIndex >= 0 ? 'TikZ 绘图已更新，请保存题目。' : '已新增一幅 TikZ 绘图到解答。');
            };

            function removeUploadedPathIfUnused(path, excludedAsset = null) {
                const safePath = window.MathBankSafe.safeImageUrl(path);
                if (!safePath) return;
                const content = document.getElementById('editContent').value;
                const answer = document.getElementById('editAnswerMarkdown').value;
                if (content.includes(safePath) || answer.includes(safePath)) return;
                const usedByContentAsset = normalizedContentTikzAssets().some(asset => {
                    return !(excludedAsset && excludedAsset.target === 'content' && asset.id === excludedAsset.id)
                        && (asset.image_path === safePath || asset.reference_image_path === safePath);
                });
                if (usedByContentAsset) return;
                const usedByAnswerAsset = normalizedAnswerTikzAssets().some(asset => {
                    return !(excludedAsset && excludedAsset.target === 'answer' && asset.id === excludedAsset.id)
                        && (asset.image_path === safePath || asset.reference_image_path === safePath);
                });
                if (usedByAnswerAsset) return;
                uploadedImages = uploadedImages.filter(item => item !== safePath);
            }

            window.deleteContentTikzAsset = function(assetId) {
                const asset = normalizedContentTikzAssets().find(item => item.id === assetId);
                if (!asset || !confirm('确定从题干中删除这幅 TikZ 绘图吗？')) return;
                const contentInput = document.getElementById('editContent');
                if (asset.image_path) {
                    const escapedPath = asset.image_path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                    contentInput.value = contentInput.value
                        .replace(new RegExp(`!\\[[^\\]]*\\]\\(${escapedPath}\\)`, 'g'), '')
                        .replace(/\n{3,}/g, '\n\n');
                }
                const referencePath = asset.reference_image_path;
                TikzState.contentAssets = TikzState.contentAssets.filter(item => item.id !== assetId);
                contentInput.dispatchEvent(new Event('input'));
                removeUploadedPathIfUnused(asset.image_path, { target: 'content', id: assetId });
                removeUploadedPathIfUnused(referencePath, { target: 'content', id: assetId });
                renderIllustrationBadges();
                window.renderContentTikzAssets();
                showToast('TikZ 绘图已从题干中移除。', 'info');
            };

            window.deleteAnswerTikzAsset = function(assetId) {
                const asset = normalizedAnswerTikzAssets().find(item => item.id === assetId);
                if (!asset || !confirm('确定从解答中删除这幅 TikZ 绘图吗？')) return;
                const answerInput = document.getElementById('editAnswerMarkdown');
                const escapedPath = asset.image_path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                answerInput.value = answerInput.value
                    .replace(new RegExp(`!\\[[^\\]]*\\]\\(${escapedPath}\\)`, 'g'), '')
                    .replace(/\n{3,}/g, '\n\n');
                TikzState.answerAssets = TikzState.answerAssets.filter(item => item.id !== assetId);
                answerInput.dispatchEvent(new Event('input'));
                if (typeof window.syncAnswerImagesFromMarkdown === 'function') window.syncAnswerImagesFromMarkdown();
                removeUploadedPathIfUnused(asset.image_path, { target: 'answer', id: assetId });
                removeUploadedPathIfUnused(asset.reference_image_path, { target: 'answer', id: assetId });
                renderIllustrationBadges();
                window.renderAnswerTikzAssets();
                showToast('TikZ 绘图已从解答中移除。', 'info');
            };

            const referenceInput = document.getElementById('answerTikzReferenceInput');
            const referenceDropZone = document.getElementById('answerTikzReferenceDropZone');
            const removeReferenceButton = document.getElementById('removeAnswerTikzReferenceBtn');
            const workbenchModal = document.getElementById('answerTikzWorkbenchModal');
            referenceInput.addEventListener('change', () => setTikzReferenceFile(referenceInput.files[0]));
            referenceDropZone.addEventListener('click', event => {
                if (!event.target.closest('#removeAnswerTikzReferenceBtn')) referenceInput.click();
            });
            referenceDropZone.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    referenceInput.click();
                }
            });
            referenceDropZone.addEventListener('dragover', event => {
                event.preventDefault();
                referenceDropZone.classList.add('border-indigo-500', 'bg-indigo-50');
            });
            referenceDropZone.addEventListener('dragleave', () => {
                referenceDropZone.classList.remove('border-indigo-500', 'bg-indigo-50');
            });
            referenceDropZone.addEventListener('drop', event => {
                event.preventDefault();
                referenceDropZone.classList.remove('border-indigo-500', 'bg-indigo-50');
                setTikzReferenceFile(event.dataTransfer.files[0]);
            });
            removeReferenceButton.addEventListener('click', event => {
                event.stopPropagation();
                clearTikzReference();
                setTikzWorkbenchStatus('参考图已移除', 'idle');
            });
            workbenchModal.addEventListener('paste', event => {
                const imageItem = Array.from(event.clipboardData && event.clipboardData.items || [])
                    .find(item => item.type && item.type.startsWith('image/'));
                if (!imageItem) return;
                event.preventDefault();
                setTikzReferenceFile(imageItem.getAsFile());
            });
            document.getElementById('answerTikzWorkbenchCode').addEventListener('input', event => {
                if (event.target.value.trim() !== tikzWorkbenchState.compiledCode) {
                    document.getElementById('insertAnswerTikzWorkbenchBtn').disabled = true;
                    setTikzWorkbenchStatus('源码已修改，请重新编译', 'warning');
                }
            });
            window.renderAnswerTikzAssets();

            window.extractTikzCodeFromTextarea = function(textareaId) {
                const textarea = document.getElementById(textareaId);
                if (!textarea) return;
                
                let text = textarea.value;
                const tikzRegex = /(\\begin\s*\{\s*tikzpicture\s*\}[\s\S]*?\\end\s*\{\s*tikzpicture\s*\})/i;
                const match = text.match(tikzRegex);
                
                if (match) {
                    const tikzBlock = match[1].trim();
                    const isContent = (textareaId === 'editContent');

                    // Clear from textarea
                    text = text.replace(tikzRegex, '').trim();
                    textarea.value = text;
                    textarea.dispatchEvent(new Event('input'));
                    
                    if (typeof window.openTikzWorkbench === 'function') {
                        const editorSession = EditorState.snapshot();
                        const targetName = isContent ? '题干' : '解答';
                        showToast(`🎉 检测到${targetName}中的 TikZ 代码！已自动提取并开始编译。`, 'info');
                        setTimeout(() => {
                            if (!EditorState.isCurrent(editorSession)) return;
                            window.openTikzWorkbench(isContent ? 'content' : 'answer');
                            const workbenchCode = document.getElementById('answerTikzWorkbenchCode');
                            if (workbenchCode) workbenchCode.value = tikzBlock;
                            window.compileTikzWorkbench();
                        }, 200);
                    }
                }
            };
        });

        // 单题一键补全/重生成 AI 解答
        async function generateSingleAnswer(index) {
            const answerGeneration = parsedQuestionsGeneration;
            const q = parsedQuestionsData[index];
            if (!q) return;
            const card = document.getElementById(`parsed-card-${index}`);
            if (!card) return;
            const requestIsCurrent = () => isParsedQuestionSaveContextCurrent(
                answerGeneration,
                index,
                q
            );

            const btn = card.querySelector('.card-solve-btn');
            const answerTextarea = card.querySelector('.card-answer-textarea');
            const answerPrev = card.querySelector('.card-answer-preview');
            
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>AI 解答中...</span>';
            }
            if (answerPrev) {
                answerPrev.innerHTML = '<div class="flex items-center space-x-2 text-indigo-600 font-bold text-xs py-2"><i class="fa-solid fa-brain animate-bounce"></i><span>AI 正在深入推导解答步骤，请稍候...</span></div>';
            }

            try {
                const formData = new FormData();
                formData.append('content', q.content || '');
                formData.append('question_type', q.question_type || 'detailed_answer');
                formData.append('stream', 'false');

                const res = await fetch('/api/ai/solve', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: formData
                });
                if (!requestIsCurrent()) return;
                
                if (!res.ok) {
                    const err = await res.json();
                    if (!requestIsCurrent()) return;
                    throw new Error(err.message || `HTTP ${res.status}`);
                }

                const data = await res.json();
                if (!requestIsCurrent()) return;
                if (data.status === 'success' && data.solution) {
                    q.answer_markdown = data.solution;
                    if (answerTextarea) answerTextarea.value = data.solution;
                    invalidateParsedDuplicateCheck(index);
                    renderParsedCardPreview(card, q.content || '', q.answer_markdown);
                    showToast(`第 ${index + 1} 题 AI 解析生成成功！`, 'success');
                } else {
                    throw new Error(data.message || '生成解答失败');
                }
            } catch (err) {
                if (!requestIsCurrent()) return;
                console.error(err);
                showToast(`生成第 ${index + 1} 题解答失败: ${err.message}`, 'error');
                renderParsedCardPreview(card, q.content || '', q.answer_markdown || '');
            } finally {
                if (requestIsCurrent() && btn) {
                    btn.disabled = false;
                    btn.innerHTML = q.answer_markdown ? '<i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i><span>重生成解析</span>' : '<i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i><span>AI 生成解析</span>';
                }
            }
        }

        // 智能并发队列解答生成器
        async function processAsyncAnswerGeneration(questions, generation = parsedQuestionsGeneration) {
            if (!questions || questions.length === 0) return;
            const requestIsCurrent = () => generation === parsedQuestionsGeneration &&
                questions === parsedQuestionsData;
            if (!requestIsCurrent()) return;

            const needAnswersIndices = [];
            questions.forEach((q, idx) => {
                const ans = (q.answer_markdown || '').trim();
                // 仅对未包含解答且未被打上原版提取标记的题目自动推导
                if (!ans || (!ans.includes('[EXTRACTED_ORIGINAL]') && ans.length < 5)) {
                    needAnswersIndices.push(idx);
                }
            });

            if (needAnswersIndices.length === 0) {
                appendImportLog('试卷成功提取到所有原版参考答案/解析，无须额外推导。', 'success');
                return;
            }

            appendImportLog(`已开启 AI 自动解析，正在为 ${needAnswersIndices.length} 道题目并发推导解答步骤 (并发上限: 3)...`, 'info');

            // 对应卡片设置加载排队 UI
            needAnswersIndices.forEach(idx => {
                const card = document.getElementById(`parsed-card-${idx}`);
                if (card) {
                    const answerPrev = card.querySelector('.card-answer-preview');
                    if (answerPrev) {
                        answerPrev.innerHTML = '<div class="flex items-center space-x-1.5 text-indigo-600 font-bold text-[10px] py-1 animate-pulse"><i class="fa-solid fa-spinner animate-spin"></i><span>AI 队列排队中，准备推导解答...</span></div>';
                    }
                }
            });

            // 控制并发池 (Concurrency Limit: 3)
            const MAX_CONCURRENCY = 3;
            let finishedCount = 0;
            let currentPointer = 0;

            async function worker() {
                while (currentPointer < needAnswersIndices.length) {
                    if (!requestIsCurrent()) return;
                    const taskIdx = needAnswersIndices[currentPointer++];
                    const q = questions[taskIdx];
                    if (!q) continue;

                    const card = document.getElementById(`parsed-card-${taskIdx}`);
                    if (card) {
                        const answerPrev = card.querySelector('.card-answer-preview');
                        if (answerPrev) {
                            answerPrev.innerHTML = '<div class="flex items-center space-x-1.5 text-indigo-600 font-bold text-[10px] py-1"><i class="fa-solid fa-brain animate-bounce"></i><span>AI 正在深入推导解答...</span></div>';
                        }
                    }

                    try {
                        const formData = new FormData();
                        formData.append('content', q.content || '');
                        formData.append('question_type', q.question_type || 'detailed_answer');
                        formData.append('stream', 'false');

                        const res = await fetch('/api/ai/solve', {
                            method: 'POST',
                            headers: {
                                'X-Local-Token': localStorage.getItem('local_token') || ''
                            },
                            body: formData
                        });
                        if (!requestIsCurrent()) return;

                        if (res.ok) {
                            const data = await res.json();
                            if (!requestIsCurrent()) return;
                            if (data.status === 'success' && data.solution) {
                                q.answer_markdown = data.solution;
                                invalidateParsedDuplicateCheck(taskIdx);
                                finishedCount++;
                                appendImportLog(`[解答进度 ${finishedCount}/${needAnswersIndices.length}] 第 ${taskIdx + 1} 题 AI 解析生成完毕。`, 'success');
                                if (card) {
                                    const answerTextarea = card.querySelector('.card-answer-textarea');
                                    if (answerTextarea) answerTextarea.value = data.solution;
                                    renderParsedCardPreview(card, q.content || '', q.answer_markdown);
                                    const btn = card.querySelector('.card-solve-btn');
                                    if (btn) btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i><span>重生成解析</span>';
                                }
                            }
                        }
                    } catch (e) {
                        if (!requestIsCurrent()) return;
                        console.error(`第 ${taskIdx + 1} 题推导解答失败:`, e);
                        if (card) {
                            renderParsedCardPreview(card, q.content || '', q.answer_markdown || '');
                        }
                    }
                }
            }

            const workers = [];
            for (let i = 0; i < Math.min(MAX_CONCURRENCY, needAnswersIndices.length); i++) {
                workers.push(worker());
            }
            await Promise.all(workers);
            if (!requestIsCurrent()) return;
            appendImportLog(`🎉 试卷所有空缺题目（共 ${needAnswersIndices.length} 题）的 AI 解答推导全部完成！`, 'success');
        }

        window.generateSingleAnswer = generateSingleAnswer;
        window.processAsyncAnswerGeneration = processAsyncAnswerGeneration;
        window.openParsedDuplicateReview = openParsedDuplicateReview;
        window.closeParsedDuplicateReviewModal = closeParsedDuplicateReviewModal;
        window.associateRelatedQuestion = associateRelatedQuestion;
        window.clearRelatedQuestion = clearRelatedQuestion;

if (typeof handleTexFileSelect === 'function') { window.handleTexFileSelect = handleTexFileSelect; }
