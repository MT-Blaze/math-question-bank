
        // ===== 素材通道：单题录入的唯一贴图入口 =====
        // 旧版有 4 个外观一致的截图区（题干 OCR / 插图 / 解析 OCR / 图片作答），
        // 用户要靠位置猜「这张图最后落到哪一格」。现在合并成一条通道 + 目的地选择：
        // 先讲清给谁、要不要识别成文字，再拖拽 / 点击 / Ctrl+V 贴图。
        const MEDIA_TARGET_LABELS = {
            'stem-ocr': '题干 · 识别成文字',
            'stem-image': '题干 · 存为配图',
            'answer-ocr': '解析 · 识别成文字',
            'answer-image': '解析 · 存为图片作答'
        };
        const MEDIA_TARGET_STORAGE_KEY = 'mediaChannelTarget';
        // 识别类目的地一次只吃首图：多张图并发会互相打断，反而更 confusing
        const MEDIA_OCR_TARGETS = ['stem-ocr', 'answer-ocr'];

        function currentMediaTarget() {
            const saved = localStorage.getItem(MEDIA_TARGET_STORAGE_KEY);
            return MEDIA_TARGET_LABELS[saved] ? saved : 'stem-ocr';
        }

        function mediaTargetHandler(target) {
            if (target === 'stem-ocr') return (file) => runContentOcr(file);
            if (target === 'stem-image') return (file) => uploadIllustration(file);
            if (target === 'answer-ocr') return (file) => runOcr(file);
            return (file) => uploadAnswerImage(file);
        }

        function applyMediaTarget(target) {
            if (!MEDIA_TARGET_LABELS[target]) target = 'stem-ocr';
            const group = document.getElementById('mediaTargetGroup');
            if (group) {
                group.querySelectorAll('.media-target-btn').forEach((btn) => {
                    const active = btn.getAttribute('data-media-target') === target;
                    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
                });
            }
            const label = MEDIA_TARGET_LABELS[target];
            const hint = document.getElementById('mediaTargetHint');
            if (hint) hint.textContent = `目的地：${label}`;
            const zone = document.getElementById('mediaDropZone');
            if (zone) zone.setAttribute('aria-label', `素材通道，当前目的地：${label}`);
            localStorage.setItem(MEDIA_TARGET_STORAGE_KEY, target);
        }

        // 按当前目的地投递图片：识别类走 OCR，存图类原图入库
        function submitMediaFiles(files) {
            const images = Array.prototype.slice.call(files || [])
                .filter((file) => file && file.type && file.type.indexOf('image/') === 0);
            if (!images.length) {
                showToast('请拖入或选择有效的图片文件！', 'error');
                return;
            }
            // 记下最近一张图：切图工具在没有更合适的源时用它兜底
            window.lastMediaFile = images[0];
            const target = currentMediaTarget();
            const handler = mediaTargetHandler(target);
            if (MEDIA_OCR_TARGETS.indexOf(target) !== -1) {
                handler(images[0]);
            } else {
                images.forEach((file) => handler(file));
            }
        }

        // 通道内共享的截图预览：贴图立刻回显，识别期间显示进度，结束后恢复可点
        function renderMediaPreview(file) {
            const previewImg = document.getElementById('mediaPreviewImg');
            const previewContainer = document.getElementById('mediaPreviewContainer');
            const placeholder = document.getElementById('mediaPlaceholder');
            const statusBadge = document.getElementById('mediaStatusBadge');
            const loadingText = document.getElementById('mediaStatusLoadingText');

            const reader = new FileReader();
            reader.onload = (e) => {
                if (previewImg && previewContainer && placeholder) {
                    previewImg.src = e.target.result;
                    placeholder.classList.add('hidden');
                    previewContainer.classList.remove('hidden');
                    if (statusBadge) statusBadge.classList.add('hidden');
                    if (loadingText) loadingText.classList.remove('hidden');
                }
            };
            reader.readAsDataURL(file);
        }

        function finishMediaPreview(statusText) {
            const statusBadge = document.getElementById('mediaStatusBadge');
            const loadingText = document.getElementById('mediaStatusLoadingText');
            if (loadingText) loadingText.classList.add('hidden');
            if (statusBadge) {
                statusBadge.classList.remove('hidden');
                statusBadge.textContent = statusText || '已加载截图预览（点击可更换图片）';
            }
        }

        // 两枚「禁用 TikZ 绘图」合成一枚全局开关，并迁移历史偏好
        function setupGlobalSkipTikz() {
            const box = document.getElementById('globalSkipTikz');
            if (!box) return;
            const legacyKeys = ['contentOcrSkipTikz', 'answerOcrSkipTikz'];
            let saved = localStorage.getItem('skipTikzDisabled');
            if (saved === null) {
                // 旧的两枚开关只要有一枚勾过「禁用」，合并后保持禁用，别偷偷打开绘图
                saved = legacyKeys.some((key) => localStorage.getItem(key) === 'true') ? 'true' : 'false';
                localStorage.setItem('skipTikzDisabled', saved);
                legacyKeys.forEach((key) => localStorage.removeItem(key));
            }
            box.checked = saved === 'true';
            box.addEventListener('change', () => {
                localStorage.setItem('skipTikzDisabled', String(box.checked));
            });
        }

        function isTikzGenerationDisabled() {
            const box = document.getElementById('globalSkipTikz');
            return box ? box.checked : false;
        }

        function setupUploadHandlers() {
            const group = document.getElementById('mediaTargetGroup');
            if (group) {
                group.querySelectorAll('.media-target-btn').forEach((btn) => {
                    btn.addEventListener('click', () => applyMediaTarget(btn.getAttribute('data-media-target')));
                });
            }
            applyMediaTarget(currentMediaTarget());
            setupGlobalSkipTikz();

            const dropZone = document.getElementById('mediaDropZone');
            const fileInput = document.getElementById('mediaFileInput');
            if (dropZone && fileInput) {
                dropZone.addEventListener('click', () => fileInput.click());
                fileInput.addEventListener('change', () => {
                    if (fileInput.files.length > 0) {
                        submitMediaFiles(fileInput.files);
                        fileInput.value = ''; // 复位，同一张图还能再贴一次
                    }
                });
                ['dragenter', 'dragover'].forEach((eventName) => {
                    dropZone.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        dropZone.classList.add('is-active');
                    }, false);
                });
                ['dragleave', 'drop'].forEach((eventName) => {
                    dropZone.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        dropZone.classList.remove('is-active');
                        if (eventName === 'drop' && e.dataTransfer && e.dataTransfer.files.length > 0) {
                            submitMediaFiles(e.dataTransfer.files);
                        }
                    }, false);
                });
                // 键盘可达：Tab 聚焦通道后用 Enter/Space 打开文件选择
                dropZone.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        fileInput.click();
                    }
                });
                setupPasteListener(dropZone, (file) => submitMediaFiles([file]));
            }

            // 题干编辑框即上传窗口：拖拽/粘贴题目截图 → 题干 OCR
            const contentEditorEl = document.getElementById('editContent');
            if (contentEditorEl) {
                setupDragDropListeners(contentEditorEl, (file) => runContentOcr(file));
                setupPasteListener(contentEditorEl, (file) => runContentOcr(file));
            }
            // 解析编辑框即上传窗口：拖拽/粘贴答案截图 → 解析 OCR
            const answerEditorEl = document.getElementById('editAnswerMarkdown');
            if (answerEditorEl) {
                setupDragDropListeners(answerEditorEl, (file) => runOcr(file));
                setupPasteListener(answerEditorEl, (file) => runOcr(file));
            }

            setupGlobalPasteRouting();
            updateOcrPlaceholder('content');
        }

        // 全局 Ctrl+V：焦点不在编辑框/通道里时，按当前目的地投递。
        // 焦点已经在编辑框或通道内时必须让位给各自的 paste 监听器，
        // 否则同一张图会被识别两次（旧版「离视口中心最近的 dropzone」路由随合并作废）。
        function setupGlobalPasteRouting() {
            window.addEventListener('paste', (e) => {
                const clipboard = e.clipboardData || (e.originalEvent && e.originalEvent.clipboardData);
                if (!clipboard || !clipboard.items) return;

                let imageFile = null;
                for (let index in clipboard.items) {
                    const item = clipboard.items[index];
                    if (item.kind === 'file' && item.type && item.type.indexOf('image/') === 0) {
                        imageFile = item.getAsFile();
                        break;
                    }
                }
                if (!imageFile) return;

                const node = e.target;
                if (node && node.closest && node.closest('#editContent, #editAnswerMarkdown, #mediaDropZone')) return;

                e.preventDefault();
                submitMediaFiles([imageFile]);
            });
        }
        function setupDragDropListeners(zone, onFileReceived) {
            ['dragenter', 'dragover'].forEach(eventName => {
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    zone.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });
            
            ['dragleave', 'drop'].forEach(eventName => {
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    zone.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });
            
            zone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                const files = dt.files;
                if (files.length > 0) {
                    for (let i = 0; i < files.length; i++) {
                        onFileReceived(files[i]);
                    }
                }
            }, false);
        }

        // Paste clipboard screen captures handler
        function setupPasteListener(element, onFileReceived) {
            element.addEventListener('paste', (e) => {
                const items = (e.clipboardData || e.originalEvent.clipboardData).items;
                for (let index in items) {
                    const item = items[index];
                    if (item.kind === 'file') {
                        const blob = item.getAsFile();
                        onFileReceived(blob);
                        e.preventDefault();
                        e.stopPropagation();
                    }
                }
            });
        }

        // Clean and strip leading question numbers and exclamation noise from OCR LaTeX results
        function cleanMathOcrText(text) {
            if (!text) return '';
            
            // 1. Strip LaTeX thin space \!, \, and literal ! / ！
            let cleaned = text.replace(/\\!/g, '');
            cleaned = cleaned.replace(/\\,/g, ''); // Remove all \, thin spaces
             // Protect markdown image starting indicator ![, replace other exclamation marks, and restore
             cleaned = cleaned.replace(/!\[/g, '___MARKDOWN_IMG_START___');
             cleaned = cleaned.replace(/[!！]/g, '');
             cleaned = cleaned.replace(/___MARKDOWN_IMG_START___/g, '![');
            
            // 2. Strip leading question numbers recursively (e.g., "一、 1. " -> "1. " -> "")
            let prev = '';
            while (cleaned !== prev) {
                prev = cleaned;
                cleaned = cleaned.trim();
                
                // Pattern 1: "第 1 题", "第1题", "第1题、" etc.
                cleaned = cleaned.replace(/^第\s*\d+\s*题[\s\.\,，、．\:\：\-\—\~]*/i, '');
                
                // Pattern 2: Chinese numbers "一、", "十一．", etc.
                cleaned = cleaned.replace(/^[一二三四五六七八九十百]+[\s、．\.\,，\:\：\-\—\~]+/i, '');
                
                // Pattern 3: parenthesized or bracketed numbers: (1), （2）, [3], 【4】
                cleaned = cleaned.replace(/^[\(（\[【]\s*\d+\s*[\)）\]】][\s\.\,，、．\:\：\-\—\~]*/i, '');
                
                // Pattern 4: normal digits followed by punctuation: 1., 12、, 3, 4．, etc.
                cleaned = cleaned.replace(/^\d+[\s\.\,，、．\:\：\-\—\~]+/, '');
                
                // Pattern 5: "例 1:", "例题 1:", "例1", "例题1：", etc.
                cleaned = cleaned.replace(/^例(?:题)?\s*\d+[\s\.\,，、．\:\：\-\—\~]*/i, '');
            }
            
            return cleaned.trim();
        }

        let aiSolveRequestSequence = 0;
        let aiSolveCompletionTimer = null;

        function isAiSolveRequestCurrent(sequence, controller, editorSnapshot) {
            return sequence === aiSolveRequestSequence &&
                controller === aiSolveAbortController &&
                !controller.signal.aborted &&
                EditorState.isCurrent(editorSnapshot);
        }

        function resetAiSolveUi(resetProgress = false) {
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const progressBar = document.getElementById('aiSolveProgressBar');
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('opacity-50', 'pointer-events-none');
            }
            if (loader) loader.classList.add('hidden');
            if (resetProgress && progressBar) progressBar.style.width = '0%';
        }

        function abortActiveAiSolve() {
            const controller = aiSolveAbortController;
            const hadActiveRequest = Boolean(controller || aiSolveCompletionTimer);
            aiSolveRequestSequence += 1;
            aiSolveAbortController = null;
            if (aiSolveCompletionTimer) {
                clearTimeout(aiSolveCompletionTimer);
                aiSolveCompletionTimer = null;
            }
            if (controller) controller.abort();
            if (hadActiveRequest) resetAiSolveUi(true);
            return hadActiveRequest;
        }

        // EditorState calls this one boundary whenever a question, draft or new
        // editor session takes ownership of the form.
        window.invalidateEditorSessionAsyncWork = () => cancelAllOcr(false);


        // 取消全部在飞的识别：ESC 第一次取消请求并保留预览，再按一次彻底清空
        function cancelAllOcr(clearWhenIdle = true) {
            let aborted = false;

            if (contentOcrAbortController) {
                contentOcrAbortController.abort();
                contentOcrAbortController = null;
                aborted = true;
            }
            if (answerOcrAbortController) {
                answerOcrAbortController.abort();
                answerOcrAbortController = null;
                aborted = true;
            }
            if (abortActiveAiSolve()) {
                aborted = true;
            }

            if (aborted) {
                const contentLoading = document.getElementById('contentOcrLoadingIndicator');
                const answerLoading = document.getElementById('ocrLoadingIndicator');
                if (contentLoading) contentLoading.classList.add('hidden');
                if (answerLoading) answerLoading.classList.add('hidden');
                finishMediaPreview('已取消识别（点击可更换图片）');
                showToast('OCR 识别已取消，按 ESC 可清除图片', 'info');
            } else if (clearWhenIdle) {
                clearContentOcrPreview();
                clearOcrPreview();
                showToast('已清除当前识图状态与图片', 'info');
            }
        }
        // Global Esc key listener for canceling OCR & closing lightbox
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.key === 'Esc') {
                const lightbox = document.getElementById('imageLightbox');
                if (lightbox && !lightbox.classList.contains('hidden')) {
                    closeLightbox();
                } else {
                    cancelAllOcr();
                }
            }
        });

        // 1. Upload Illustration handler
        function uploadIllustration(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            const editorSession = EditorState.snapshot();
            
            showToast('正在上传插图...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (!EditorState.isCurrent(editorSession)) return;
                if (data.status === 'success') {
                    showToast('图片上传成功！');
                    uploadedImages.push(data.file_path);
                    renderIllustrationBadges();
                    
                    // Insert image markdown tag into textarea where cursor is
                    insertImageTag(data.file_path);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (!EditorState.isCurrent(editorSession)) return;
                showToast('上传图片出错: ' + err, 'error');
            });
        }

        function insertImageTag(filePath) {
            const textarea = document.getElementById('editContent');
            const markdownTag = `\n\n![插图](${filePath})\n\n`;
            
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + markdownTag + originalVal.substring(endPos);
            
            // Dispatch input event to refresh preview
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            // Put cursor right after inserted image
            const newCursorPos = startPos + markdownTag.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
        }

        function renderIllustrationBadges() {
            const listContainer = document.getElementById('illustrationsList');
            if (!listContainer) return;
            listContainer.innerHTML = '';

            const hiddenReferencePaths = new Set(TikzState.referencePaths());

            uploadedImages.forEach((path, idx) => {
                if (hiddenReferencePaths.has(path)) return;
                // TikZ 渲染结果（tikz_*.png）不是待转位图：它已经在「TikZ 绘图」
                // 面板里列一条；再挂一个「TikZ」按钮等于让用户拿转好的图重转一次。
                if (typeof window.isTikzRenderImage === 'function'
                    && window.isTikzRenderImage(path)) return;
                const filename = path.split('/').pop();
                const safePath = (window.MathBankSafe && window.MathBankSafe.safeImageUrl)
                    ? window.MathBankSafe.safeImageUrl(path) : path;
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        <i class="fa-solid fa-file-image text-brand-500"></i>
                        <span class="truncate max-w-[100px]" title="${filename}">${filename}</span>
                        <button type="button" title="以此图为参考，自动生成 TikZ 绘图" onclick="autoTikzFromIllustration('${safePath}')" class="text-brand-500 hover:text-brand-700 transition-all font-semibold pl-1" style="font-size:11px;">
                            <i class="fa-solid fa-pen-ruler"></i><span class="ml-0.5">TikZ</span>
                        </button>
                        <button type="button" onclick="deleteUploadedIllustration(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
            if (typeof window.renderContentTikzAssets === 'function') {
                window.renderContentTikzAssets();
            }
        }

        window.autoTikzFromIllustration = function(referencePath) {
            if (!referencePath) { showToast('未胷取图路径，请重试。', 'error'); return; }
            if (typeof window.openTikzWorkbench !== 'function') { showToast('TikZ 工作台未加载。', 'error'); return; }
            window.openTikzWorkbench('content', null, referencePath);
        }

        function deleteUploadedIllustration(idx) {
            // We just remove it from active images array
            const deletedPath = uploadedImages[idx];
            uploadedImages.splice(idx, 1);
            renderIllustrationBadges();
            
            // Remove markdown code from editor if user wants
            const textarea = document.getElementById('editContent');
            textarea.value = textarea.value.replace(new RegExp(`\\!\\[插图\\]\\(${deletedPath}\\)`, 'g'), '');
            textarea.dispatchEvent(new Event('input'));
            
            showToast('插图已移除');
        }

        // 1.5 Image Answer (No OCR) handler
        function uploadAnswerImage(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            const editorSession = EditorState.snapshot();
            
            showToast('正在上传图片解答...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (!EditorState.isCurrent(editorSession)) return;
                if (data.status === 'success') {
                    showToast('图片解答上传成功！');
                    insertAnswerImageTag(data.file_path);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (!EditorState.isCurrent(editorSession)) return;
                showToast('上传图片出错: ' + err, 'error');
            });
        }

        function insertAnswerImageTag(filePath) {
            const textarea = document.getElementById('editAnswerMarkdown');
            const markdownTag = `\n\n![图片解答](${filePath})\n\n`;
            
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + markdownTag + originalVal.substring(endPos);
            
            // Dispatch input event to refresh preview
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            // Put cursor right after inserted image
            const newCursorPos = startPos + markdownTag.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
            
            // Sync answer images array & badges
            syncAnswerImagesFromMarkdown();
        }

        function renderAnswerImageBadges() {
            const listContainer = document.getElementById('imageAnswersList');
            if (!listContainer) return;
            
            listContainer.innerHTML = '';
            
            if (uploadedAnswerImages.length === 0) {
                listContainer.innerHTML = '<p id="noImageAnswerPlaceholder" class="text-xs text-slate-400 italic w-full text-center py-2">暂无已上传的图片解答</p>';
                return;
            }
            
            uploadedAnswerImages.forEach((path, idx) => {
                const filename = path.split('/').pop();
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        <i class="fa-solid fa-file-image text-brand-500"></i>
                        <span class="truncate max-w-[100px]" title="${filename}">${filename}</span>
                        <button type="button" onclick="deleteUploadedAnswerImage(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
        }

        function deleteUploadedAnswerImage(idx) {
            const deletedPath = uploadedAnswerImages[idx];
            const textarea = document.getElementById('editAnswerMarkdown');
            
            // Remove markdown code from editor
            const escaped = escapeRegExp(deletedPath);
            textarea.value = textarea.value.replace(new RegExp(`\\\\!\\\\\\[.*?\\\\\\]\\\\(${escaped}\\\\)`, 'g'), '');
            textarea.value = textarea.value.replace(new RegExp(`\\!\\[.*?\\]\\(${escaped}\\)`, 'g'), '');
            textarea.dispatchEvent(new Event('input'));
            
            showToast('图片解答已从解析中移除');
            
            // Sync badges
            syncAnswerImagesFromMarkdown();
        }

        function syncAnswerImagesFromMarkdown() {
            const val = document.getElementById('editAnswerMarkdown').value || '';
            const regex = /!\[.*?\]\((.*?)\)/g;
            let match;
            const foundImages = [];
            while ((match = regex.exec(val)) !== null) {
                if (match[1] && match[1].includes('/static/uploads/')) {
                    foundImages.push(match[1]);
                }
            }
            uploadedAnswerImages = foundImages;
            renderAnswerImageBadges();
        }


        // 通道底部提示：识别引擎跟随系统设置（原来两枚 dropzone 各写一行，现合并为一行）
        function updateOcrPlaceholder(type) {
            const hint = document.getElementById('mediaEngineHint');
            if (!hint) return;
            let name = systemPreferEngine || '';
            if (name === 'siliconflow') {
                name = 'SiliconFlow 硅基流动云端';
            } else if (name === 'ali_bailian') {
                name = '阿里百炼';
            }
            hint.textContent = name ? `识别引擎：${name}` : '识别引擎：读取系统设置';
        }

        // 2. 解析截图 OCR：结果落「解析识别结果」，并自动载入终审框
        function runOcr(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }

            const ocrOutput = document.getElementById('ocrOutputBox');
            const ocrResult = document.getElementById('ocrResultText');
            const ocrConf = document.getElementById('ocrConfBadge');

            renderMediaPreview(file);

            if (ocrOutput) ocrOutput.classList.add('hidden');

            // Abort previous running controller if any
            if (answerOcrAbortController) {
                answerOcrAbortController.abort();
            }
            answerOcrAbortController = new AbortController();
            const signal = answerOcrAbortController.signal;

            const engine = 'default';
            const skipTikz = isTikzGenerationDisabled();

            const formData = new FormData();
            formData.append('file', file);
            formData.append('engine', engine);
            formData.append('skip_tikz', skipTikz ? "true" : "false");

            fetch('/api/ocr', {
                method: 'POST',
                body: formData,
                signal: signal
            })
            .then(r => r.json())
            .then(data => {
                finishMediaPreview();
                answerOcrAbortController = null;

                if (data.status === 'success') {
                    showToast('OCR 识别成功并已自动填入！');
                    ocrOutput.classList.remove('hidden');
                    ocrResult.textContent = cleanMathOcrText(data.latex);
                    ocrConf.textContent = `置信度: ${(data.confidence * 100).toFixed(1)}%`;

                    if (data.tikz_code) {
                        if (data.tikz_image_path) {
                            if (typeof window.registerAutoAnswerTikzAsset === 'function') {
                                window.registerAutoAnswerTikzAsset({
                                    tikzCode: data.tikz_code,
                                    imagePath: data.tikz_image_path,
                                    referenceImagePath: data.image_path || ''
                                });
                            }
                        }
                    }

                    // Automatically load OCR results into final review editor silently
                    loadToFinalReview('ocr');
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    return; // 手动取消不报错弹提示
                }
                finishMediaPreview();
                answerOcrAbortController = null;
                showToast('OCR 识别出错: ' + err, 'error');
            });
        }

        // 2.2 题干截图 OCR：结果落「题干识别结果」，并自动载入题干编辑框
        function runContentOcr(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }

            const contentOcrOutput = document.getElementById('contentOcrOutputBox');
            const contentOcrResult = document.getElementById('contentOcrResultText');
            const contentOcrConf = document.getElementById('contentOcrConfBadge');

            renderMediaPreview(file);

            if (contentOcrOutput) contentOcrOutput.classList.add('hidden');

            // Abort previous running controller if any
            if (contentOcrAbortController) {
                contentOcrAbortController.abort();
            }
            contentOcrAbortController = new AbortController();
            const signal = contentOcrAbortController.signal;

            const engine = 'default';
            const skipTikz = isTikzGenerationDisabled();

            const formData = new FormData();
            formData.append('file', file);
            formData.append('engine', engine);
            formData.append('skip_tikz', skipTikz ? "true" : "false");

            fetch('/api/ocr', {
                method: 'POST',
                body: formData,
                signal: signal
            })
            .then(r => r.json())
            .then(data => {
                finishMediaPreview();
                contentOcrAbortController = null;

                if (data.status === 'success') {
                    showToast('题干 OCR 识别成功并已自动填入！');
                    contentOcrOutput.classList.remove('hidden');

                    const cleanLatex = cleanMathOcrText(data.latex);
                    contentOcrResult.textContent = cleanLatex;
                    contentOcrConf.textContent = `置信度: ${(data.confidence * 100).toFixed(1)}%`;

                    if (data.tikz_code) {
                        if (data.tikz_image_path) {
                            if (typeof window.registerAutoContentTikzAsset === 'function') {
                                window.registerAutoContentTikzAsset({
                                    tikzCode: data.tikz_code,
                                    imagePath: data.tikz_image_path,
                                    referenceImagePath: data.image_path || ''
                                });
                            } else if (typeof renderIllustrationBadges === 'function') {
                                renderIllustrationBadges();
                            }
                        }
                    }

                    // 自动载入常驻的题干编辑框
                    loadToContentEditor('ocr');
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    return; // 手动取消不报错弹提示
                }
                finishMediaPreview();
                contentOcrAbortController = null;
                showToast('题干 OCR 识别出错: ' + err, 'error');
            });
        }
        // Load content OCR result into the persistent editor textarea
        function loadToContentEditor(source, isAppend = false) {
            const textarea = document.getElementById('editContent');
            let contentToImport = '';
            
            if (source === 'ocr') {
                contentToImport = document.getElementById('contentOcrResultText').textContent;
                
                // 1. Auto-detect if it's a choice question with options A, B, C, D
                const hasA = /[\s,，、]*\bA(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                const hasB = /[\s,，、]*\bB(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                const hasC = /[\s,，、]*\bC(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                const hasD = /[\s,，、]*\bD(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                
                if (hasA && hasB && hasC && hasD) {
                    const editQType = document.getElementById('editQType');
                    if (editQType) {
                        editQType.value = 'single_choice';
                        // Trigger change listener to update paper badges immediately
                        editQType.dispatchEvent(new Event('change'));
                    }
                }
                
                // 2. Automatically format the OCR content to break choice options onto separate lines beautifully
                contentToImport = formatQuestionContent(contentToImport);
            }
            
            if (!contentToImport.trim()) {
                showToast('导入内容为空！', 'error');
                return;
            }
            
            if (isAppend) {
                if (textarea.value.trim()) {
                    textarea.value += '\n' + contentToImport;
                } else {
                    textarea.value = contentToImport;
                }
            } else {
                if (textarea.value.trim()) {
                    const replace = confirm('题干编辑框中已有内容，点击"确定"将覆盖替换，点击"取消"将追加在后面。');
                    if (replace) {
                        textarea.value = contentToImport;
                    } else {
                        textarea.value += '\n' + contentToImport;
                    }
                } else {
                    textarea.value = contentToImport;
                }
            }
            
            // Refresh previews
            textarea.dispatchEvent(new Event('input'));
            showToast('已载入至题干编辑框！');
            if (typeof window.extractTikzCodeFromTextarea === 'function') {
                window.extractTikzCodeFromTextarea('editContent');
            }
        }


        // 通道预览复位：贴图区回到「等待拖图」状态
        function clearSharedMediaPreview() {
            const previewContainer = document.getElementById('mediaPreviewContainer');
            const placeholder = document.getElementById('mediaPlaceholder');
            const previewImg = document.getElementById('mediaPreviewImg');
            const statusBadge = document.getElementById('mediaStatusBadge');
            const loadingText = document.getElementById('mediaStatusLoadingText');

            if (previewImg) previewImg.src = '';
            if (previewContainer) previewContainer.classList.add('hidden');
            if (placeholder) placeholder.classList.remove('hidden');
            if (loadingText) loadingText.classList.add('hidden');
            if (statusBadge) statusBadge.classList.add('hidden');
        }

        // 清题干识别结果（通道预览 + 结果框）；函数名保留给 import.js 调用
        function clearContentOcrPreview() {
            clearSharedMediaPreview();
            const contentOcrOutput = document.getElementById('contentOcrOutputBox');
            if (contentOcrOutput) contentOcrOutput.classList.add('hidden');
        }

        // 清解析识别结果（通道预览 + 结果框）；函数名保留给 import.js 调用
        function clearOcrPreview() {
            clearSharedMediaPreview();
            const ocrOutput = document.getElementById('ocrOutputBox');
            if (ocrOutput) ocrOutput.classList.add('hidden');
        }
        // Lightbox Zoom Functions
        function zoomImage(src) {
            const lightbox = document.getElementById('imageLightbox');
            const lightboxImg = document.getElementById('lightboxImg');
            if (lightbox && lightboxImg) {
                lightboxImg.src = src;
                // 统一走 api.js 的公共开关助手：灯箱的 scale 过渡面是 img 本身，显式传入；
                // 揭示自带 rAF+setTimeout 双保险，杜绝"透明全屏层吞点击"的整页假死。
                window.openModalWithTransition(lightbox, {
                    onEscape: closeLightbox,
                    surface: lightboxImg
                });
            }
        }

        function closeLightbox() {
            const lightbox = document.getElementById('imageLightbox');
            const lightboxImg = document.getElementById('lightboxImg');
            if (lightbox && lightboxImg) {
                window.closeModalWithTransition(lightbox);
                lightboxImg.src = '';
            }
        }

        // Quick math inserting helper
        function insertContentHelper(code) {
            const textarea = document.getElementById('editContent');
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + code + originalVal.substring(endPos);
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            const newCursorPos = startPos + code.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
        }

        // Toggle thinking style micro-interactions
        function toggleThinkingStyle() {
            const toggle = document.getElementById('aiThinkingToggle');
            const icon = document.getElementById('thinkingIcon');
            const label = document.getElementById('thinkingLabel');
            if (toggle.checked) {
                icon.className = "fa-solid fa-brain text-brand-500 animate-pulse";
                label.textContent = "深度思考";
            } else {
                icon.className = "fa-solid fa-bolt text-amber-500";
                label.textContent = "极速解答";
            }
        }

        // 3. AI Intelligent Solve handler
        function triggerAISolve() {
            const content = document.getElementById('editContent').value;
            const qtype = document.getElementById('editQType').value;
            const customPrompt = document.getElementById('aiCustomPrompt').value;
            
            // Use globally configured preferred solve model
            const model = typeof systemPreferSolveModel !== 'undefined' ? systemPreferSolveModel : 'deepseek-flash';
            const thinkingToggle = document.getElementById('aiThinkingToggle');
            const thinking = (thinkingToggle && thinkingToggle.checked) ? 'enabled' : 'disabled';
            
            if (!content.trim()) {
                showToast('请先在上方输入题干内容，AI需要读取题干生成解答步骤！', 'error');
                return;
            }
            
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const outputBox = document.getElementById('aiOutputBox');
            const resultBox = document.getElementById('aiResultText');
            const loadingText = document.getElementById('aiLoadingText');
            
            const ocrResultTextEl = document.getElementById('ocrResultText');
            const ocrResult = ocrResultTextEl ? ocrResultTextEl.textContent.trim() : '';

            // Set dynamic loading explanation depending on thinking mode and model
            let modelFriendly = model.includes('/') ? model.split('/').pop() : model;

            if (ocrResult) {
                if (thinking === 'enabled') {
                    loadingText.textContent = `${modelFriendly} 正在结合题干与 OCR 结果进行深度思考并构建 LaTeX 解析... (大约需要 15-90 秒)`;
                } else {
                    loadingText.textContent = `${modelFriendly} 正在结合题干与 OCR 结果极速生成 LaTeX 解析... (预计 3-10 秒)`;
                }
            } else {
                if (thinking === 'enabled') {
                    loadingText.textContent = `${modelFriendly} 正在进行深度思考并构建 LaTeX 解析步骤... (思考与生成可能需要 15-90 秒，请耐心等待)`;
                } else {
                    loadingText.textContent = `${modelFriendly} 正在极速生成简要 LaTeX 解析步骤... (预计 3-10 秒即可完成，请稍后)`;
                }
            }
            
            btn.disabled = true;
            btn.classList.add('opacity-50', 'pointer-events-none');
            loader.classList.remove('hidden');
            outputBox.classList.add('hidden');
            
            // Initialize progress bar
            const progressBar = document.getElementById('aiSolveProgressBar');
            if (progressBar) {
                progressBar.style.width = '0%';
            }
            
            const formData = new FormData();
            formData.append('content', content);
            formData.append('question_type', qtype);
            formData.append('ocr_result', ocrResult);
            formData.append('custom_prompt', customPrompt);
            formData.append('thinking', thinking);
            formData.append('model', model);
            formData.append('stream', 'true'); // Opt-in to real-time streaming
            
            abortActiveAiSolve();
            const editorSnapshot = EditorState.snapshot();
            const requestSequence = ++aiSolveRequestSequence;
            const requestController = new AbortController();
            aiSolveAbortController = requestController;

            const requestIsCurrent = () => isAiSolveRequestCurrent(
                requestSequence,
                requestController,
                editorSnapshot
            );

            const finishCurrentRequest = (resetProgress = false) => {
                if (!requestIsCurrent()) return false;
                aiSolveAbortController = null;
                if (aiSolveCompletionTimer) {
                    clearTimeout(aiSolveCompletionTimer);
                    aiSolveCompletionTimer = null;
                }
                resetAiSolveUi(resetProgress);
                return true;
            };
            
            fetch('/api/ai/solve', {
                method: 'POST',
                body: formData,
                signal: requestController.signal
            })
            .then(response => {
                if (!requestIsCurrent()) return;
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                if (progressBar) progressBar.style.width = '3%';
                
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let accumulatedSolution = '';
                let accumulatedReasoning = '';
                
                function read() {
                    return reader.read().then(({ done, value }) => {
                        if (!requestIsCurrent()) return;
                        if (done) {
                            throw new Error('AI 流式响应意外中断');
                        }
                        
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop(); // Keep last incomplete line
                        
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (trimmed.startsWith('data:')) {
                                try {
                                    const eventData = JSON.parse(trimmed.slice(5).trim());
                                    if (!requestIsCurrent()) return;
                                    if (eventData.status === 'processing') {
                                        if (eventData.reasoning) {
                                            accumulatedReasoning += eventData.reasoning;
                                            const rCount = eventData.reasoning_count || 0;
                                            const rPct = Math.min(50, rCount * 0.08); // Up to 50%
                                            if (progressBar) progressBar.style.width = `${3 + rPct}%`;
                                            if (loadingText) {
                                                loadingText.textContent = `${modelFriendly} 正在进行深度推理思考 (已生成 ${rCount} 个 Token)...`;
                                            }
                                        }
                                        if (eventData.content) {
                                            accumulatedSolution += eventData.content;
                                            const cCount = eventData.content_count || 0;
                                            const rCount = eventData.reasoning_count || 0;
                                            let pct = 3;
                                            if (rCount > 0) {
                                                const rPct = Math.min(50, rCount * 0.08);
                                                const cPct = Math.min(45, cCount * 0.05);
                                                pct += rPct + cPct;
                                            } else {
                                                pct += Math.min(92, cCount * 0.08);
                                            }
                                            if (progressBar) progressBar.style.width = `${pct}%`;
                                            if (loadingText) {
                                                loadingText.textContent = `${modelFriendly} 正在生成 LaTeX 解析步骤 (已输出 ${cCount} 个 Token)...`;
                                            }
                                        }
                                    } else if (eventData.status === 'error') {
                                        return Promise.reject(new Error(eventData.message || 'AI 服务返回错误'));
                                    } else if (eventData.status === 'done') {
                                        if (progressBar) progressBar.style.width = '100%';
                                        aiSolveCompletionTimer = setTimeout(() => {
                                            aiSolveCompletionTimer = null;
                                            // The editor can change during the 300ms
                                            // completion animation. Recheck before
                                            // touching either result field.
                                            if (!requestIsCurrent()) return;
                                            outputBox.classList.remove('hidden');
                                            
                                            let finalOutput = '';
                                            if (accumulatedReasoning.trim()) {
                                                finalOutput += `【深度思考推理过程】\n${accumulatedReasoning.trim()}\n\n【参考解析】\n`;
                                            }
                                            finalOutput += accumulatedSolution;
                                            resultBox.textContent = finalOutput;
                                            
                                            loadToFinalReview('ai');
                                            showToast('AI 解析生成成功！');
                                            finishCurrentRequest();
                                        }, 300);
                                        return;
                                    }
                                } catch (e) {
                                    console.error('Failed to parse SSE line:', line, e);
                                }
                            }
                        }
                        return read();
                    });
                }
                
                return read();
            })
            .catch(err => {
                // A superseded request must not clear or re-enable the controls
                // owned by the new request.
                if (!requestIsCurrent()) return;
                finishCurrentRequest(true);
                if (err.name !== 'AbortError') {
                    showToast('AI 生成解析出错: ' + err.message, 'error');
                }
            });
        }

        // Import Tab results into persistent Final Review Textbox
        function loadToFinalReview(source) {
            const finalEdit = document.getElementById('editAnswerMarkdown');
            let contentToImport = '';
            
            if (source === 'ai') {
                contentToImport = document.getElementById('aiResultText').textContent;
                // Exclude reasoning block from importing into the final review editor
                if (contentToImport.includes('【参考解析】')) {
                    const parts = contentToImport.split('【参考解析】');
                    contentToImport = parts[1] || parts[0];
                }
            } else if (source === 'ocr') {
                contentToImport = document.getElementById('ocrResultText').textContent;
            }
            
            // Clean up LaTeX spacing, formula noise and leading question numbers
            if (source === 'ai' || source === 'ocr') {
                contentToImport = cleanMathOcrText(contentToImport);
            }
            
            if (!contentToImport.trim()) {
                showToast('导入内容为空！', 'error');
                return;
            }
            
            // Ask user whether to replace or append if there is already content
            if (finalEdit.value.trim()) {
                const replace = confirm('终审编辑框中已有内容，点击"确定"将替换已有内容，点击"取消"将追加在后面。');
                if (replace) {
                    finalEdit.value = contentToImport;
                } else {
                    finalEdit.value += '\n\n' + contentToImport;
                }
            } else {
                finalEdit.value = contentToImport;
            }
            
            // Refresh preview
            finalEdit.dispatchEvent(new Event('input'));
            showToast('已成功载入至终审编辑框！');
            if (typeof window.extractTikzCodeFromTextarea === 'function') {
                window.extractTikzCodeFromTextarea('editAnswerMarkdown');
            }
        }

        // Clear Draft
