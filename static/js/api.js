        // Shared frontend trust boundary. All later cascade modules must use this
        // helper before inserting database, OCR, import or AI output into HTML.
        const MathBankSafe = (() => {
            const RICH_HTML_CONFIG = Object.freeze({
                ALLOWED_TAGS: [
                    'div', 'span', 'p', 'br', 'strong', 'b', 'em', 'i',
                    'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tfoot',
                    'tr', 'th', 'td', 'img', 'sub', 'sup', 'code', 'pre', 'hr'
                ],
                ALLOWED_ATTR: [
                    'class', 'src', 'alt', 'title', 'colspan', 'rowspan',
                    'role', 'aria-label', 'aria-hidden',
                    'data-preferred-columns', 'data-choice-columns',
                    'data-safe-image-open', 'data-figure-align-qid'
                ],
                ALLOW_DATA_ATTR: false,
                ALLOW_ARIA_ATTR: true,
                KEEP_CONTENT: true,
                RETURN_TRUSTED_TYPE: false
            });
            function asString(value) {
                return value == null ? '' : String(value);
            }

            function escapeHtmlRaw(value) {
                return asString(value)
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#039;');
            }

            function purify(value, config) {
                if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
                    return String(window.DOMPurify.sanitize(asString(value), config));
                }
                // DOMPurify is bundled locally. If it ever fails to load, render
                // inert text instead of falling back to unsafe HTML.
                return escapeHtmlRaw(value);
            }

            function sanitizePlainText(value) {
                // Plain-text sinks (.value, .textContent, title) do not parse
                // HTML and therefore need no HTML sanitizer. Returning the exact
                // string also preserves mathematical comparison signs.
                return asString(value);
            }

            function escapeText(value) {
                // This helper is used while constructing HTML. Encode the raw
                // characters directly: parsing "plain text" as HTML first would
                // treat expressions such as x<y>z as tags and silently drop them.
                return escapeHtmlRaw(value);
            }

            function escapeAttribute(value) {
                return escapeText(value).replace(/\r?\n/g, ' ');
            }

            function safeClassList(value, fallback = '') {
                const tokens = sanitizePlainText(value).split(/\s+/).filter(Boolean);
                const safeTokens = tokens.filter((token) => /^[A-Za-z0-9_:\[\]()./%#-]+$/.test(token));
                return safeTokens.length > 0 ? safeTokens.join(' ') : fallback;
            }

            function safeImageUrl(value) {
                const raw = asString(value).trim();
                if (!raw || /[\u0000-\u001F\u007F\\]/.test(raw)) return '';

                try {
                    const url = new URL(raw, window.location.href);
                    if (url.origin !== window.location.origin) return '';
                    if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';

                    const decodedPath = decodeURIComponent(url.pathname);
                    const allowedPrefix = decodedPath.startsWith('/static/uploads/')
                        || decodedPath.startsWith('/static/test_uploads/');
                    if (!allowedPrefix || decodedPath.split('/').includes('..')) return '';

                    // Only passive raster assets may be opened under the app's
                    // origin. In particular, never turn a legacy uploaded
                    // HTML/SVG file into a same-origin window via the preview
                    // click handler.
                    if (!/\.(?:png|jpe?g|gif|webp)$/i.test(decodedPath)) return '';

                    return url.pathname;
                } catch (error) {
                    return '';
                }
            }

            function sanitizeRichHtml(value) {
                const purified = purify(value, RICH_HTML_CONFIG);
                if (typeof document === 'undefined' || !document.createElement) {
                    return purified;
                }

                const template = document.createElement('template');
                template.innerHTML = purified;
                template.content.querySelectorAll('img').forEach((img) => {
                    const safeUrl = safeImageUrl(img.getAttribute('src'));
                    if (!safeUrl) {
                        img.remove();
                        return;
                    }
                    img.setAttribute('src', safeUrl);
                    img.setAttribute('loading', 'lazy');
                    img.setAttribute('decoding', 'async');
                });
                return template.innerHTML;
            }

            return Object.freeze({
                escapeText,
                escapeAttribute,
                sanitizePlainText,
                sanitizeRichHtml,
                safeImageUrl,
                safeClassList
            });
        })();
        window.MathBankSafe = MathBankSafe;

        // Shared HTML escaping for every feature module. Routing through
        // MathBankSafe keeps a single escaping implementation, so untrusted
        // question, OCR or AI text can never reach innerHTML unescaped.
        function escapeHtml(str) {
            return window.MathBankSafe.escapeText(str);
        }
        window.escapeHtml = escapeHtml;

// 考点条目归一：兼容字符串与 {id, name} 对象（考点 ID 化后大纲 topics 为对象）
window.topicName = function(t) {
    if (t && typeof t === 'object') {
        return (typeof t.name === 'string' ? t.name : String(t.name || ''));
    }
    return (t == null ? '' : String(t));
};

        // Safe image previews use one delegated listener rather than interpolated
        // inline handlers containing untrusted URLs.
        document.addEventListener('click', (event) => {
            const image = event.target && event.target.closest
                ? event.target.closest('img[data-safe-image-open]')
                : null;
            if (!image) return;
            const safeUrl = MathBankSafe.safeImageUrl(image.getAttribute('src'));
            if (!safeUrl) return;
            const opened = window.open(safeUrl, '_blank', 'noopener,noreferrer');
            if (opened) opened.opener = null;
        });

        // Shared, build-free dialog accessibility manager. Feature modules keep
        // their visual transitions while this layer owns focus trapping, Escape
        // handling, background isolation and focus restoration.
        const MathBankModal = (() => {
            const dialogStack = [];
            const focusableSelector = [
                'a[href]:not([tabindex="-1"])',
                'button:not([disabled]):not([tabindex="-1"])',
                'input:not([disabled]):not([type="hidden"]):not([tabindex="-1"])',
                'select:not([disabled]):not([tabindex="-1"])',
                'textarea:not([disabled]):not([tabindex="-1"])',
                '[tabindex]:not([tabindex="-1"])',
                '[contenteditable="true"]'
            ].join(',');

            function getFocusable(dialog) {
                return Array.from(dialog.querySelectorAll(focusableSelector)).filter((element) => {
                    return !element.hidden && element.getAttribute('aria-hidden') !== 'true'
                        && element.getClientRects().length > 0;
                });
            }

            function isolateBackground(isIsolated) {
                ['header', 'main'].forEach((selector) => {
                    const element = document.querySelector(selector);
                    if (!element) return;
                    element.inert = isIsolated;
                    if (isIsolated) {
                        element.setAttribute('aria-hidden', 'true');
                    } else {
                        element.removeAttribute('aria-hidden');
                    }
                });
            }

            function resolveInitialFocusTarget(dialog, options = {}) {
                const requested = options.initialFocus;
                if (requested instanceof Element && dialog.contains(requested)) {
                    return requested;
                }
                if (typeof requested === 'string') {
                    const matched = dialog.querySelector(requested);
                    if (matched) return matched;
                }

                const explicitAutofocus = dialog.querySelector('[autofocus]');
                if (explicitAutofocus) return explicitAutofocus;

                const labelledBy = dialog.getAttribute('aria-labelledby');
                if (labelledBy) {
                    const title = document.getElementById(labelledBy);
                    if (title && dialog.contains(title)) return title;
                }

                return dialog.querySelector('[data-modal-surface], .glass-modal') || dialog;
            }

            function focusDialogContext(dialog, options = {}) {
                dialog.querySelectorAll('[data-modal-initial-focus="true"]').forEach((element) => {
                    element.removeAttribute('data-modal-initial-focus');
                });
                const target = resolveInitialFocusTarget(dialog, options);
                const isInteractiveTarget = target.matches(focusableSelector);
                if (!target.hasAttribute('tabindex') && !isInteractiveTarget) {
                    target.setAttribute('tabindex', '-1');
                }
                if (!isInteractiveTarget) {
                    target.setAttribute('data-modal-initial-focus', 'true');
                }
                target.focus({ preventScroll: true });
            }

            function open(dialog, options = {}) {
                if (!dialog) return;
                const existingIndex = dialogStack.findIndex((entry) => entry.dialog === dialog);
                if (existingIndex !== -1) dialogStack.splice(existingIndex, 1);

                const previousDialog = dialogStack.length > 0 ? dialogStack[dialogStack.length - 1].dialog : null;
                if (previousDialog) previousDialog.setAttribute('aria-hidden', 'true');

                dialog.setAttribute('role', dialog.getAttribute('role') || 'dialog');
                dialog.setAttribute('aria-modal', 'true');
                dialog.setAttribute('aria-hidden', 'false');
                dialogStack.push({
                    dialog,
                    previousFocus: document.activeElement,
                    onEscape: typeof options.onEscape === 'function' ? options.onEscape : null
                });
                isolateBackground(true);

                requestAnimationFrame(() => {
                    focusDialogContext(dialog, options);
                });
            }

            function close(dialog) {
                if (!dialog) return;
                const index = dialogStack.findIndex((entry) => entry.dialog === dialog);
                if (index === -1) {
                    dialog.setAttribute('aria-hidden', 'true');
                    return;
                }

                const [entry] = dialogStack.splice(index, 1);
                dialog.setAttribute('aria-hidden', 'true');
                const currentEntry = dialogStack.length > 0 ? dialogStack[dialogStack.length - 1] : null;
                if (currentEntry) {
                    currentEntry.dialog.setAttribute('aria-hidden', 'false');
                } else {
                    isolateBackground(false);
                }

                requestAnimationFrame(() => {
                    const restoreTarget = entry.previousFocus;
                    if (restoreTarget && restoreTarget.isConnected && typeof restoreTarget.focus === 'function') {
                        restoreTarget.focus({ preventScroll: true });
                    } else if (currentEntry) {
                        focusDialogContext(currentEntry.dialog);
                    }
                });
            }

            document.addEventListener('keydown', (event) => {
                const entry = dialogStack.length > 0 ? dialogStack[dialogStack.length - 1] : null;
                if (!entry) return;

                if (event.key === 'Escape' || event.key === 'Esc') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    if (entry.onEscape) {
                        entry.onEscape();
                    } else {
                        const closeButton = entry.dialog.querySelector('[data-modal-close]');
                        if (closeButton) closeButton.click();
                    }
                    return;
                }

                if (event.key !== 'Tab') return;
                const focusable = getFocusable(entry.dialog);
                if (focusable.length === 0) {
                    event.preventDefault();
                    entry.dialog.focus({ preventScroll: true });
                    return;
                }
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (!entry.dialog.contains(document.activeElement)) {
                    event.preventDefault();
                    (event.shiftKey ? last : first).focus();
                } else if (!focusable.includes(document.activeElement)) {
                    event.preventDefault();
                    (event.shiftKey ? last : first).focus();
                } else if (event.shiftKey && document.activeElement === first) {
                    event.preventDefault();
                    last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                    event.preventDefault();
                    first.focus();
                }
            }, true);

            return Object.freeze({ open, close });
        })();
        window.MathBankModal = MathBankModal;

        // ==================== 「全屏遮罩 + opacity 过渡」弹窗的统一开关 ====================
        // 教训来自「+ 添加厂商」整页假死事故：弹窗根节点 class 带 fixed inset-0 + opacity-0，
        // 打开逻辑只 remove('hidden') 而没人清 opacity-0 —— 遮罩隐形却吞掉全屏所有点击，
        // 叠加 MathBankModal 把 header/main 置 inert，页面看起来"瞬间卡死"。
        // 此类弹窗今后一律走这两个函数开关，业务代码不要再手写 hidden/opacity 的切换。
        function findModalTransitionSurface(modal, surface) {
            if (surface instanceof Element) return surface;
            return modal.querySelector('.glass-modal, [data-modal-surface]');
        }

        function revealModalWithTransition(modal) {
            const surface = modal.__transitionSurface || findModalTransitionSurface(modal);
            modal.__transitionSurface = surface;
            const show = () => {
                modal.classList.remove('opacity-0');
                if (surface) {
                    surface.classList.remove('scale-95');
                    surface.classList.add('scale-100');
                }
            };
            // 同帧改 opacity 会被浏览器合并样式、过渡不跑，必须下一帧再改；
            // 后台标签页/无头环境 rAF 会被冻结，所以再挂一个 setTimeout 兜底。
            requestAnimationFrame(() => setTimeout(show, 0));
            setTimeout(show, 60);
        }

        function openModalWithTransition(modalOrId, options) {
            const opts = options || {};
            const modal = typeof modalOrId === 'string' ? document.getElementById(modalOrId) : modalOrId;
            if (!modal) return null;
            modal.classList.remove('hidden');
            modal.classList.add('flex');   // displayOrder/pandoc 这类运行时补 flex 的弹窗用；markup 自带 flex 时无操作
            modal.__transitionSurface = findModalTransitionSurface(modal, opts.surface);
            if (window.MathBankModal) window.MathBankModal.open(modal, opts);
            revealModalWithTransition(modal);
            return modal;
        }

        function closeModalWithTransition(modalOrId) {
            const modal = typeof modalOrId === 'string' ? document.getElementById(modalOrId) : modalOrId;
            if (!modal) return;
            if (window.MathBankModal) window.MathBankModal.close(modal);
            const surface = modal.__transitionSurface || findModalTransitionSurface(modal);
            modal.classList.add('opacity-0');
            if (surface) {
                surface.classList.remove('scale-100');
                surface.classList.add('scale-95');
            }
            // hidden 立刻归位、不等淡出动画跑完：淡出到 hidden 生效之间存在一段
            // "看不见但吞点击"的窗口期。防点击吞噬优先于淡出观感。
            modal.classList.add('hidden');
            modal.classList.remove('flex');
        }
        window.revealModalWithTransition = revealModalWithTransition;
        window.openModalWithTransition = openModalWithTransition;
        window.closeModalWithTransition = closeModalWithTransition;

        // Duplicate-hit auto tagging. All three entry points (plain editor save,
        // single parsed-card import and batch import) must share this wording so a
        // question never ends up with two different "suspected duplicate" tags.
        // exact / probable -> 疑似重复#<seq>, possible_variant -> 疑似变式#<seq>,
        // always quoting the highest-level candidate's question number.
        function duplicateLevelRank(level) {
            const normalized = String(level || '').toLowerCase();
            if (normalized.indexOf('exact') !== -1) return 3;
            if (normalized.indexOf('probable') !== -1 || normalized.indexOf('likely') !== -1) return 2;
            if (normalized.indexOf('variant') !== -1) return 1;
            return 0;
        }

        function buildDuplicateAutoTag(result) {
            if (!result) return '';
            const pool = [];
            if (Array.isArray(result.candidates)) {
                result.candidates.forEach(function (candidate) { pool.push(candidate); });
            }
            if (Array.isArray(result.batch_matches)) {
                result.batch_matches.forEach(function (match) { pool.push(match); });
            }
            let bestItem = null;
            let bestRank = 0;
            let bestSeq = null;
            pool.forEach(function (item) {
                if (!item) return;
                const rank = duplicateLevelRank(item.level);
                if (rank <= 0) return;
                // In-batch matches only carry the sibling card index, so they are
                // reported with the same 1-based numbering the cards display.
                const seq = (item.seq_num !== null && item.seq_num !== undefined && item.seq_num !== '')
                    ? Number(item.seq_num)
                    : (item.other_index !== null && item.other_index !== undefined
                        ? Number(item.other_index) + 1
                        : null);
                if (seq === null || !isFinite(seq)) return;
                if (!bestItem || rank > bestRank || (rank === bestRank && seq < bestSeq)) {
                    bestItem = item;
                    bestRank = rank;
                    bestSeq = seq;
                }
            });
            if (!bestItem) return '';
            const prefix = bestRank === 1 ? '疑似变式#' : '疑似重复#';
            return prefix + bestSeq;
        }

        function appendDuplicateTagToTags(currentTags, tag) {
            const normalizedTag = String(tag || '').trim();
            const source = String(currentTags === null || currentTags === undefined ? '' : currentTags);
            if (!normalizedTag) return source;
            const parts = source.split(',').map(function (part) {
                return part.trim();
            }).filter(Boolean);
            // Idempotent: re-saving a question must not stack the same tag twice.
            if (parts.indexOf(normalizedTag) !== -1) return parts.join(',');
            parts.push(normalizedTag);
            return parts.join(',');
        }

        window.buildDuplicateAutoTag = buildDuplicateAutoTag;
        window.appendDuplicateTagToTags = appendDuplicateTagToTags;

        // Mirror native disabled/loading state to assistive technology. This also
        // covers dynamically rendered import and paper buttons without requiring
        // each feature module to duplicate ARIA bookkeeping.
        function syncButtonAccessibility(button) {
            if (!(button instanceof HTMLButtonElement)) return;
            if (button.disabled) {
                button.setAttribute('aria-disabled', 'true');
            } else {
                button.removeAttribute('aria-disabled');
            }
            const hasSpinner = Boolean(button.querySelector('.animate-spin, .fa-spin, .apple-spinner'));
            if (button.disabled && hasSpinner) {
                button.setAttribute('aria-busy', 'true');
            } else if (button.id !== 'saveQuestionBtn') {
                button.removeAttribute('aria-busy');
            }
        }

        const buttonStateObserver = new MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                const target = mutation.target instanceof HTMLButtonElement
                    ? mutation.target
                    : mutation.target.closest && mutation.target.closest('button');
                if (target) syncButtonAccessibility(target);
                mutation.addedNodes.forEach((node) => {
                    if (!(node instanceof Element)) return;
                    if (node.matches('button')) syncButtonAccessibility(node);
                    node.querySelectorAll('button').forEach(syncButtonAccessibility);
                });
            });
        });
        buttonStateObserver.observe(document.documentElement, {
            subtree: true,
            childList: true,
            attributes: true,
            attributeFilter: ['disabled']
        });
        document.querySelectorAll('button').forEach(syncButtonAccessibility);

        // 本地令牌统一入口：令牌存在 window.__localToken / cookie / localStorage('local_token')。
        // 之前业务代码里散落着一个从未被写入过的旧键名，取到空串后又被原样塞进
        // 请求头，导致写接口被 403（教案保存曾因此“无法新建”）。所有业务代码
        // 一律走这里，不要再各自拼键名。
        window.getLocalToken = function () {
            let token = window.__localToken || '';
            if (!token) {
                const cookies = document.cookie.split(';');
                for (let cookie of cookies) {
                    const trimmed = cookie.trim();
                    const eqIndex = trimmed.indexOf('=');
                    if (eqIndex === -1) continue;
                    if (trimmed.substring(0, eqIndex) === 'local_token') {
                        token = trimmed.substring(eqIndex + 1);
                        break;
                    }
                }
            }
            if (!token) {
                try {
                    token = localStorage.getItem('local_token') || '';
                } catch (e) {
                    token = '';
                }
            }
            return token;
        };

        // CSRF Token protection & window.fetch Monkey Patch
        (() => {
            const originalFetch = window.fetch;
            window.fetch = async function (input, init) {
                const requestInit = init || {};
                const isRequest = typeof Request !== 'undefined' && input instanceof Request;
                const requestMethod = String(requestInit.method || (isRequest ? input.method : 'GET')).toUpperCase();
                const requestUrlValue = isRequest ? input.url : input;
                let requestUrl = null;
                try {
                    requestUrl = new URL(String(requestUrlValue), window.location.href);
                } catch (error) {
                    // Preserve native fetch error behaviour for malformed inputs.
                }
                
                // 1. Try to read local token from window global variable (injected directly into HTML)
                let localToken = window.getLocalToken ? window.getLocalToken() : (window.__localToken || '');
                
                // 2. Try to read local_token from cookie as fallback
                if (!localToken) {
                    const cookies = document.cookie.split(';');
                    for (let cookie of cookies) {
                        const trimmed = cookie.trim();
                        if (!trimmed) continue;
                        const eqIndex = trimmed.indexOf('=');
                        if (eqIndex === -1) continue;
                        const name = trimmed.substring(0, eqIndex);
                        const value = trimmed.substring(eqIndex + 1);
                        if (name === 'local_token') {
                            localToken = value;
                            break;
                        }
                    }
                }
                
                // 3. Fallback to/from localStorage to prevent losing token on cookie clearing/block
                if (!localToken) {
                    try {
                        localToken = localStorage.getItem('local_token') || '';
                    } catch (e) {
                        console.error('Failed to read localToken from localStorage:', e);
                    }
                } else {
                    try {
                        localStorage.setItem('local_token', localToken);
                    } catch (e) {
                        console.error('Failed to save localToken to localStorage:', e);
                    }
                }
                
                const isSameOriginApi = requestUrl
                    && requestUrl.origin === window.location.origin
                    && requestUrl.pathname.startsWith('/api/');
                const isWriteRequest = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(requestMethod);

                if (localToken && isSameOriginApi && isWriteRequest) {
                    const headers = new Headers(isRequest ? input.headers : undefined);
                    new Headers(requestInit.headers || undefined).forEach((value, name) => {
                        headers.set(name, value);
                    });
                    // 空字符串也要覆盖：调用方可能填了空的 X-Local-Token，
                    // 那样服务端会直接 403（教案保存曾因此失败）。
                    if (!headers.get('X-Local-Token')) {
                        headers.set('X-Local-Token', localToken);
                    }
                    return originalFetch.call(this, input, { ...requestInit, headers });
                }
                
                return originalFetch.call(this, input, init);
            };
        })();

        // Single source of truth for the current editor session identity.
        // Editable form values continue to live in the DOM during this low-risk phase.
        const EditorState = (() => {
            const state = {
                questionId: null,
                seqNum: null,
                createdAt: null,
                draftId: null,
                mode: 'new',
                generation: 0
            };

            const invalidateAsyncWork = () => {
                state.generation += 1;
                if (typeof window.invalidateEditorSessionAsyncWork === 'function') {
                    window.invalidateEditorSessionAsyncWork();
                }
            };

            const reset = () => {
                invalidateAsyncWork();
                state.questionId = null;
                state.seqNum = null;
                state.createdAt = null;
                state.draftId = null;
                state.mode = 'new';
            };

            const useQuestion = (question) => {
                invalidateAsyncWork();
                state.questionId = question && question.id != null ? question.id : null;
                state.seqNum = question && question.seq_num != null ? question.seq_num : null;
                state.createdAt = question && question.created_at ? question.created_at : null;
                state.draftId = null;
                state.mode = state.questionId == null ? 'new' : 'question';
            };

            const useDraft = (draft) => {
                invalidateAsyncWork();
                state.questionId = null;
                state.seqNum = null;
                state.createdAt = null;
                state.draftId = draft && draft.id != null ? draft.id : null;
                state.mode = state.draftId == null ? 'new' : 'draft';
            };

            const beginTransition = () => {
                invalidateAsyncWork();
            };

            const setDraftId = (draftId) => {
                state.draftId = draftId == null ? null : draftId;
                if (state.questionId == null) {
                    state.mode = state.draftId == null ? 'new' : 'draft';
                }
            };

            const clearDraft = () => {
                state.draftId = null;
                state.mode = state.questionId == null ? 'new' : 'question';
            };

            const snapshot = () => ({ ...state });
            const isCurrent = (candidate) => Boolean(candidate) &&
                state.generation === candidate.generation &&
                state.questionId === candidate.questionId &&
                state.draftId === candidate.draftId &&
                state.mode === candidate.mode;

            return Object.freeze({
                get questionId() { return state.questionId; },
                get seqNum() { return state.seqNum; },
                get createdAt() { return state.createdAt; },
                get draftId() { return state.draftId; },
                get mode() { return state.mode; },
                get generation() { return state.generation; },
                reset,
                useQuestion,
                useDraft,
                beginTransition,
                setDraftId,
                clearDraft,
                snapshot,
                isCurrent
            });
        })();
        window.EditorState = EditorState;

        // Global variables
        let categoryTree = {};
        // 统一大纲结构为字典：册次→章节→{小节名: [考点]}
        function normalizeCategoryTree(data) {
            const result = {};
            Object.keys(data || {}).forEach(function(comp) {
                result[comp] = {};
                const chapters = data[comp] || {};
                Object.keys(chapters).forEach(function(chap) {
                    const chapData = chapters[chap];
                    if (Array.isArray(chapData)) {
                        // 列表结构：[{name, topics}] → {小节名: [考点]}
                        result[comp][chap] = {};
                        chapData.forEach(function(sec) {
                            if (sec && sec.name) {
                                result[comp][chap][sec.name] = sec.topics || [];
                            }
                        });
                    } else if (typeof chapData === 'object') {
                        // 已经是字典结构
                        result[comp][chap] = chapData;
                    } else {
                        result[comp][chap] = {};
                    }
                });
            });
            return result;
        }
        // 一题多考点（附加考向）状态与考向组合筛选状态
        let extraTopicSlots = [];
        let extraTopicFilter = { slots: [], mode: 'and' };
        function cloneTopicSlot(s) {
            return {
                compulsory: (s && s.compulsory) || '',
                chapter: (s && s.chapter) || '',
                knowledge: (s && s.knowledge) || '',
                topic: (s && s.topic) || ''
            };
        }
        window.ExtraTopicsState = {
            get list() { return extraTopicSlots; },
            setList(items) {
                extraTopicSlots = Array.isArray(items)
                    ? items.map(cloneTopicSlot).filter(function(slot) {
                          return slot.compulsory || slot.chapter || slot.knowledge || slot.topic;
                      }).slice(0, 3)
                    : [];
            },
            add(slot) {
                if (extraTopicSlots.length >= 3) {
                    showToast('附加考向最多添加 3 个', 'info');
                    return false;
                }
                extraTopicSlots.push(cloneTopicSlot(slot));
                return true;
            },
            removeAt(i) {
                if (i >= 0 && i < extraTopicSlots.length) extraTopicSlots.splice(i, 1);
            },
            clear() { extraTopicSlots = []; }
        };
        window.extraTopicFilter = extraTopicFilter;
        window.isGCurriculum = function() {
            const m = window.systemMetadata;
            if (!m || !m.curriculum) return false;
            return Boolean(m.curriculum['不等式·比较大小·选填技巧'] || m.curriculum['集合·复数·逻辑']);
        };
        function topicSlotLabel(slot) {
            if (!slot) return '';
            return [slot.compulsory, slot.chapter, slot.knowledge, slot.topic]
                .filter(function(part) { return part && String(part).trim(); })
                .join(' / ');
        }
        function renderExtraKaoxiangList() {
            const container = document.getElementById('extraKaoxiangList');
            if (!container) return;
            if (!extraTopicSlots.length) {
                container.innerHTML = '<span class="text-[10px] text-slate-400/80 italic">暂无附加考向（主分类即第一考向）</span>';
                return;
            }
            container.innerHTML = extraTopicSlots.map(function(slot, i) {
                var slotLevel = (slot && slot.level) ? slot.level : '';
                var levelBadge = '';
                if (slotLevel) {
                    var levelColors = {'L1':'bg-green-100 text-green-700','L2':'bg-blue-100 text-blue-700','L3':'bg-orange-100 text-orange-700','L4':'bg-red-100 text-red-700'};
                    levelBadge = '<span class="text-[8px] font-bold rounded-full px-1 py-px ' + (levelColors[slotLevel] || 'bg-slate-100 text-slate-600') + '">' + slotLevel + '</span>';
                }
                return '<span class="inline-flex items-center gap-1 text-[10px] font-bold text-indigo-600 bg-indigo-50 border border-indigo-300/60 px-1.5 py-0.5 rounded-full" title="' +
                    window.MathBankSafe.escapeAttribute(topicSlotLabel(slot)) + '">' +
                    levelBadge +
                    '<i class="fa-solid fa-bullseye text-[7px] text-indigo-400"></i>' +
                    '<span class="max-w-[140px] truncate">' + window.MathBankSafe.escapeText(topicSlotLabel(slot)) + '</span>' +
                    '<button type="button" aria-label="移除考向" onclick="removeExtraTopic(' + i + ')" class="text-indigo-300 hover:text-red-500 transition-colors cursor-pointer"><i class="fa-solid fa-xmark text-[9px]"></i></button>' +
                    '</span>';
            }).join('');
        }
        function removeExtraTopic(i) {
            window.ExtraTopicsState.removeAt(i);
            renderExtraKaoxiangList();
        }
        window.removeExtraTopic = removeExtraTopic;
        function renderTopicFilterSummary() {
            const bar = document.getElementById('topicFilterBar');
            const countEl = document.getElementById('topicFilterCount');
            const summary = document.getElementById('topicFilterSummary');
            if (!bar || !countEl || !summary) return;
            const slots = extraTopicFilter.slots || [];
            if (!slots.length) {
                countEl.classList.add('hidden');
                summary.innerHTML = '';
                return;
            }
            bar.classList.remove('hidden');
            countEl.textContent = String(slots.length);
            countEl.classList.remove('hidden');
            const modeText = extraTopicFilter.mode === 'or' ? '任一命中(OR)' : '全部命中(AND)';
            summary.innerHTML = '<span class="text-[9px] font-bold text-slate-500 bg-slate-100 border border-slate-200 px-1.5 py-0.5 rounded-full">' + modeText + '</span>' +
                slots.map(function(slot, i) {
                    return '<span class="inline-flex items-center gap-1 text-[9px] font-bold text-brand-600 bg-brand-50 border border-brand-200/70 px-1.5 py-0.5 rounded-full" title="' +
                        window.MathBankSafe.escapeAttribute(topicSlotLabel(slot)) + '">' +
                        '<span class="max-w-[120px] truncate">' + window.MathBankSafe.escapeText(topicSlotLabel(slot)) + '</span>' +
                        '<button type="button" aria-label="移除" onclick="removeTopicFilterSlot(' + i + ')" class="text-brand-500 hover:text-red-500 transition-colors cursor-pointer"><i class="fa-solid fa-xmark text-[8px]"></i></button>' +
                        '</span>';
                }).join('') +
                '<button type="button" onclick="clearTopicFilter()" class="text-[9px] font-bold text-red-400 hover:text-red-600 underline underline-offset-2 cursor-pointer">清除</button>';
        }
        function removeTopicFilterSlot(i) {
            extraTopicFilter.slots.splice(i, 1);
            renderTopicFilterSummary();
        }
        function clearTopicFilter() {
            extraTopicFilter.slots = [];
            renderTopicFilterSummary();
        }
        window.removeTopicFilterSlot = removeTopicFilterSlot;
        window.clearTopicFilter = clearTopicFilter;

        function buildExtraKaoxiangPicker(onPick, onCancel) {
            const backdrop = document.createElement('div');
            backdrop.id = 'extraTopicPicker';
            backdrop.className = 'fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center select-none p-4';
            backdrop.setAttribute('role', 'dialog');
            backdrop.setAttribute('aria-modal', 'true');
            backdrop.setAttribute('aria-labelledby', 'extraTopicPickerTitle');

            const tree = window.categoryTree || {};
            const mkSelect = function(id, placeholder, disabled) {
                const sel = document.createElement('select');
                sel.id = id;
                sel.className = 'glass-select w-full px-2.5 py-2 text-xs rounded-xl disabled:opacity-50';
                sel.innerHTML = '<option value="">' + placeholder + '</option>';
                sel.disabled = !!disabled;
                return sel;
            };
            const compSel = mkSelect('extPickCompulsory', '-- 选择册次 --', false);
            const chapSel = mkSelect('extPickChapter', '-- 先选册次 --', true);
            const knowSel = mkSelect('extPickKnowledge', '-- 先选章节 --', true);
            const topicSel = document.createElement('input');
            topicSel.id = 'extPickTopic';
            topicSel.setAttribute('list', 'extPickTopicList');
            topicSel.placeholder = '选择或输入考点';
            topicSel.className = 'glass-select w-full px-2.5 py-2 text-xs rounded-xl disabled:opacity-50';
            topicSel.disabled = true;
            const topicListEl = document.createElement('datalist');
            topicListEl.id = 'extPickTopicList';

            Object.keys(tree).forEach(function(c) {
                compSel.innerHTML += '<option value="' + window.MathBankSafe.escapeAttribute(c) + '">' + window.MathBankSafe.escapeText(c) + '</option>';
            });
            compSel.onchange = function() {
                const comp = compSel.value;
                chapSel.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSel.innerHTML = '<option value="">-- 先选章节 --</option>';
                topicSel.value = '';
                if (topicListEl) topicListEl.innerHTML = '';
                knowSel.disabled = true;
                topicSel.disabled = true;
                chapSel.disabled = true;
                if (comp && tree[comp]) {
                    chapSel.disabled = false;
                    Object.keys(tree[comp]).forEach(function(ch) {
                        chapSel.innerHTML += '<option value="' + window.MathBankSafe.escapeAttribute(ch) + '">' + window.MathBankSafe.escapeText(ch) + '</option>';
                    });
                }
            };
            chapSel.onchange = function() {
                const comp = compSel.value;
                const chap = chapSel.value;
                knowSel.innerHTML = '<option value="">-- 选择小节（可不选） --</option>';
                topicSel.value = '';
                if (topicListEl) topicListEl.innerHTML = '';
                topicSel.disabled = true;
                knowSel.disabled = true;
                if (comp && chap && tree[comp] && tree[comp][chap]) {
                    knowSel.disabled = false;
                    (tree[comp][chap] || []).forEach(function(k) {
                        const kName = (k && typeof k === 'object') ? k.name : k;
                        knowSel.innerHTML += '<option value="' + window.MathBankSafe.escapeAttribute(kName) + '">' + window.MathBankSafe.escapeText(kName) + '</option>';
                    });
                }
            };
            knowSel.onchange = function() {
                const comp = compSel.value;
                const chap = chapSel.value;
                const know = knowSel.value;
                topicSel.value = '';
                if (topicListEl) topicListEl.innerHTML = '';
                topicSel.disabled = true;
                if (!know) return;
                let section = null;
                const list = tree[comp] && tree[comp][chap];
                if (Array.isArray(list)) {
                    section = list.find(function(k) {
                        const kName = (k && typeof k === 'object') ? k.name : k;
                        return kName === know;
                    }) || null;
                }
                const topics = (section && Array.isArray(section.topics)) ? section.topics : [];
                const isG = typeof window.isGCurriculum === 'function' && window.isGCurriculum();
                topicSel.disabled = !know || isG;
                if (topics.length > 0 && !isG) {
                    topics.forEach(function(t) {
                        const opt = document.createElement('option');
                        opt.value = window.topicName(t);
                        topicListEl.appendChild(opt);
                    });
                }
            };

            const card = document.createElement('div');
            card.className = 'glass-card bg-white/95 backdrop-blur-xl rounded-2xl shadow-2xl w-full max-w-md border border-slate-200/80 p-5';
            card.innerHTML =
                '<div class="flex items-center justify-between mb-4">' +
                    '<h3 id="extraTopicPickerTitle" class="text-sm font-bold text-slate-800">选择考向</h3>' +
                    '<button type="button" aria-label="关闭" onclick="closeExtraTopicPicker()" class="text-slate-400 hover:text-slate-600 text-lg leading-none cursor-pointer"><i class="fa-solid fa-xmark"></i></button>' +
                '</div>' +
                '<div class="space-y-2.5 mb-4">' +
                    '<div class="text-[10px] font-medium text-slate-500 mb-1">册次 / 章节 / 小节 / 考点</div>' +
                    '<div id="extPickSlots"></div>' +
                '</div>' +
                '<div class="flex justify-end space-x-2">' +
                    '<button type="button" onclick="closeExtraTopicPicker()" class="px-3.5 py-1.5 text-xs font-bold text-slate-500 bg-slate-100 hover:bg-slate-200 rounded-xl transition-all cursor-pointer">取消</button>' +
                    '<button type="button" onclick="confirmExtraTopicPick()" class="px-3.5 py-1.5 text-xs font-bold text-white bg-brand-500 hover:bg-brand-600 rounded-xl transition-all cursor-pointer">确认添加</button>' +
                '</div>';
            const slotBox = card.querySelector('#extPickSlots');
            slotBox.appendChild(compSel);
            slotBox.appendChild(chapSel);
            slotBox.appendChild(knowSel);
            slotBox.appendChild(topicSel);
            slotBox.appendChild(topicListEl);
            backdrop.appendChild(card);
            backdrop.addEventListener('mousedown', function(ev) {
                if (ev.target === backdrop) onCancel && onCancel();
            });
            window.__extraTopicPickerOnPick = onPick;
            document.body.appendChild(backdrop);
            const closeBtn = backdrop.querySelector('button[aria-label="关闭"]');
            const cancelBtn = backdrop.querySelectorAll('button')[0];
            if (compSel) compSel.focus();
            return backdrop;
        }
        function closeExtraTopicPicker() {
            const el = document.getElementById('extraTopicPicker');
            if (el) el.remove();
            window.__extraTopicPickerOnPick = null;
        }
        window.closeExtraTopicPicker = closeExtraTopicPicker;
        function confirmExtraTopicPick() {
            const slot = {
                compulsory: document.getElementById('extPickCompulsory') ? document.getElementById('extPickCompulsory').value : '',
                chapter: document.getElementById('extPickChapter') ? document.getElementById('extPickChapter').value : '',
                knowledge: document.getElementById('extPickKnowledge') ? document.getElementById('extPickKnowledge').value : '',
                topic: document.getElementById('extPickTopic') ? document.getElementById('extPickTopic').value : ''
            };
            if (!slot.compulsory) {
                showToast('请至少选择册次', 'info');
                return;
            }
            if (typeof window.__extraTopicPickerOnPick === 'function') {
                window.__extraTopicPickerOnPick(slot);
            }
            closeExtraTopicPicker();
        }
        function openExtraTopicModal() {
            buildExtraKaoxiangPicker(function(slot) {
                const ok = window.ExtraTopicsState.add(slot);
                if (ok) renderExtraKaoxiangList();
            });
        }
        window.openExtraTopicModal = openExtraTopicModal;
        function openTopicFilterModal() {
            buildExtraKaoxiangPicker(function(slot) {
                const dup = (extraTopicFilter.slots || []).some(function(s) {
                    return s.compulsory === slot.compulsory && s.chapter === slot.chapter &&
                           s.knowledge === slot.knowledge && s.topic === slot.topic;
                });
                if (dup) {
                    showToast('该考向已在筛选中', 'info');
                    return;
                }
                if (extraTopicFilter.slots.length >= 3) {
                    showToast('考向组合筛选最多 3 个', 'info');
                    return;
                }
                extraTopicFilter.slots.push(cloneTopicSlot(slot));
                renderTopicFilterSummary();
                });
            setTimeout(function() {
                const modeWrap = document.createElement('div');
                modeWrap.id = 'extPickModeWrap';
                modeWrap.className = 'flex items-center justify-between bg-slate-50 border border-slate-200/80 rounded-xl px-3 py-2 mb-3';
                modeWrap.innerHTML =
                    '<span class="text-[10px] font-bold text-slate-500">命中方式</span>' +
                    '<div class="flex items-center space-x-3 text-[10px] font-bold">' +
                        '<label class="flex items-center space-x-1 cursor-pointer"><input type="radio" name="extPickMode" value="and" ' + (extraTopicFilter.mode !== 'or' ? 'checked' : '') + ' class="accent-brand-500"><span>AND（全部命中）</span></label>' +
                        '<label class="flex items-center space-x-1 cursor-pointer"><input type="radio" name="extPickMode" value="or" ' + (extraTopicFilter.mode === 'or' ? 'checked' : '') + ' class="accent-brand-500"><span>OR（任一命中）</span></label>' +
                    '</div>';
                const card = document.getElementById('extraTopicPicker');
                const firstChild = card ? card.firstElementChild : null;
                const confirmBtn = card ? card.querySelector('button[onclick="confirmExtraTopicPick()"]') : null;
                if (card && firstChild) {
                    card.insertBefore(modeWrap, firstChild.nextSibling);
                }
                const radios = document.querySelectorAll('input[name="extPickMode"]');
                radios.forEach(function(r) {
                    r.addEventListener('change', function() {
                        extraTopicFilter.mode = document.querySelector('input[name="extPickMode"]:checked').value;
                    });
                });
                if (confirmBtn) confirmBtn.textContent = '确认加入筛选';
            }, 0);
        }
        window.openTopicFilterModal = openTopicFilterModal;
        let systemMetadata = { question_types: [], difficulties: [], curriculum: {} };
        let uploadedImages = [];
        let uploadedAnswerImages = [];
        const TikzState = {
            contentAssets: [],
            answerAssets: [],
            reset() {
                this.contentAssets = [];
                this.answerAssets = [];
            },
            referencePaths() {
                return [
                    ...this.contentAssets.map(asset => asset && asset.reference_image_path),
                    ...this.answerAssets.map(asset => asset && asset.reference_image_path)
                ].map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean);
            },
            // 绘图资产自己认领的物理文件：渲染图 image_path + 原参考图 reference_image_path。
            // 渲染图按设计不出现在正文里（正文仍指向原图，只有展示/导出才换成 TikZ 成品），
            // 所以保存时必须由资产这一侧兜底登记进 image_paths：后端 asset_security 要求
            // 「TikZ 插图路径必须同时存在于题目 image_paths 中」，漏登记直接 400 卡住保存，
            // 文件还会掉回孤儿队列被回收（2026-10-19 事故）。
            assetImagePaths() {
                const paths = [];
                [this.contentAssets, this.answerAssets].forEach(assets => {
                    (Array.isArray(assets) ? assets : []).forEach(asset => {
                        if (!asset || typeof asset !== 'object') return;
                        paths.push(asset.image_path, asset.reference_image_path);
                    });
                });
                return paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean);
            }
        };
        window.TikzState = TikzState;

        // 「这张图本身就是 TikZ 渲染结果」的唯一判据：文件名以 tikz_ 开头。
        // 后端 paper_helper 与前端 describeQuestionImageSource 早已用同一条规则，
        // 但 decoratePreviewIllustrationButtons 原先只认内存资产表（TikzState），
        // 资产记录一丢（老数据 / 刷新后 hydrate 到空 content_tikz_assets），
        // 一张早就转好的图又会挂回「转 TikZ」入口。文件名是物理事实，比内存态可靠。
        function isTikzRenderImage(value) {
            const raw = String(value === null || value === undefined ? '' : value).trim();
            const withoutFragment = raw.split('#')[0];
            const base = withoutFragment.split('?')[0].split('/').pop() || '';
            return /^tikz_/i.test(base);
        }
        window.isTikzRenderImage = isTikzRenderImage;

        let originalQuestionState = null;
        let contentOcrAbortController = null;
        let answerOcrAbortController = null;
        let aiSolveAbortController = null;

        // Global debounce utility
        const debounce = (func, delay) => {
            let timer;
            return (...args) => {
                clearTimeout(timer);
                timer = setTimeout(() => func.apply(this, args), delay);
            };
        };

        // Initialize Split Screen Resizers
        function showToast(message, type = 'success') {
            const toast = document.getElementById('toast');
            const msgEl = document.getElementById('toastMessage');
            const iconContainer = document.getElementById('toastIconContainer');
            
            msgEl.textContent = message;
            
            if (type === 'success') {
                iconContainer.className = "h-6 w-6 rounded-full flex items-center justify-center text-xs bg-green-500/20 text-green-400";
                iconContainer.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            } else if (type === 'error') {
                iconContainer.className = "h-6 w-6 rounded-full flex items-center justify-center text-xs bg-red-500/20 text-red-400";
                iconContainer.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i>';
            } else {
                iconContainer.className = "h-6 w-6 rounded-full flex items-center justify-center text-xs bg-brand-500/20 text-brand-500";
                iconContainer.innerHTML = '<i class="fa-solid fa-circle-info"></i>';
            }
            
            // Show toast
            toast.classList.remove('translate-y-12', 'opacity-0');
            toast.classList.add('translate-y-0', 'opacity-100');
            
            // Hide after 3.5 seconds
            setTimeout(() => {
                toast.classList.add('translate-y-12', 'opacity-0');
                toast.classList.remove('translate-y-0', 'opacity-100');
            }, 3500);
        }

        let systemPreferEngine = 'siliconflow';
        let systemPreferSolveModel = 'deepseek-flash';
        let systemPreferParseModel = 'deepseek-flash';

        // Fetch Environment Config Settings Status
        function fetchConfigStatus() {
            // Fetch preferred engine configuration from backend to resolve defaults
            fetch('/api/settings')
                .then(r => r.json())
                .then(settings => {
                    systemPreferEngine = settings.prefer_engine || 'siliconflow';
                    systemPreferSolveModel = settings.prefer_solve_model || 'deepseek-flash';
                    systemPreferParseModel = settings.prefer_parse_model || 'deepseek-flash';
                    
                    // Update main page model selector to match preference
                    const mainModelSelect = document.getElementById('aiModelSelect');
                    if (mainModelSelect) {
                        mainModelSelect.value = systemPreferSolveModel;
                    }
                    
                    // Update dropdown descriptions to reflect current default engine
                    updateOcrPlaceholder('content');
                    updateOcrPlaceholder('answer');

                    // Populate settings modal selectors on start as well
                    const solveCfg = parseModelConfig(settings.prefer_solve_model, 'deepseek', 'deepseek-flash');
                    const solveProv = document.getElementById('solveModelProvider');
                    if (solveProv) {
                        solveProv.value = solveCfg.provider;
                        renderModelSelector('solve', solveCfg.provider, solveCfg.model);
                    }
                    
                    const parseCfg = parseModelConfig(settings.prefer_parse_model, 'deepseek', 'deepseek-flash');
                    const parseProv = document.getElementById('parseModelProvider');
                    if (parseProv) {
                        parseProv.value = parseCfg.provider;
                        renderModelSelector('parse', parseCfg.provider, parseCfg.model);
                    }
                    
                    const classifyCfg = parseModelConfig(settings.prefer_classify_model, 'deepseek', 'deepseek-flash');
                    const classifyProv = document.getElementById('classifyModelProvider');
                    if (classifyProv) {
                        classifyProv.value = classifyCfg.provider;
                        renderModelSelector('classify', classifyCfg.provider, classifyCfg.model);
                    }
                    
                    let ocrProvider = settings.prefer_engine || 'siliconflow';
                    if (ocrProvider === 'ali_bailian') ocrProvider = 'bailian';
                    if (ocrProvider === 'zhongzhan') ocrProvider = 'zhongzhan_gpt';
                    const ocrProv = document.getElementById('ocrModelProvider');
                    if (ocrProv) {
                        ocrProv.value = ocrProvider;
                        let ocrModel = "";
                        if (ocrProvider === 'siliconflow') ocrModel = settings.siliconflow_model || 'Qwen/Qwen3-VL-8B-Instruct';
                        else if (ocrProvider === 'bailian') ocrModel = settings.ali_bailian_model || 'qwen3.7-flash';
                        else if (ocrProvider === 'zhongzhan_gpt') ocrModel = settings.zhongzhan_gpt_ocr_model || 'gpt-4o';
                        else if (ocrProvider === 'zhongzhan_claude') ocrModel = settings.zhongzhan_claude_ocr_model || 'claude-3-5-sonnet';
                        renderModelSelector('ocr', ocrProvider, ocrModel);
                    }
                    
                    const drawCfg = parseModelConfig(settings.prefer_draw_model, 'siliconflow', 'Qwen/Qwen3-VL-32B-Instruct');
                    const drawProv = document.getElementById('drawModelProvider');
                    if (drawProv) {
                        drawProv.value = drawCfg.provider;
                        renderModelSelector('draw', drawCfg.provider, drawCfg.model);
                    }
                })
                .catch(err => {
                    console.error('获取偏好识图引擎设置失败:', err);
                });

            // We just call the API settings check or hit general lists to see if dotenv loaded keys
            // In case keys are missing, backend returns descriptive headers
            const indicator = document.getElementById('apiStatusIndicator');
            
            // Simple check by hitting backend
            fetch('/api/questions')
                .then(r => r.json())
                .then(() => {
                    // Config status check done. We can't query secret directly so we inspect indicator.
                    indicator.className = "flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-green-50 text-green-700 border border-green-200";
                    indicator.innerHTML = '<span class="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse"></span><span>题库就绪</span>';
                })
                .catch(() => {
                    indicator.className = "flex items-center space-x-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-red-50 text-red-700 border border-red-200";
                    indicator.innerHTML = '<span class="h-1.5 w-1.5 rounded-full bg-red-500"></span><span>后台连接中断</span>';
                });
        }

        // 百炼模型按任务隔离，避免分类/OCR 等轻量任务误选高价旗舰模型。
        const BAILIAN_MODEL_PRESETS_BY_TASK = {
            solve: [
                "qwen3.7-plus",
                "qwen3.8-max",
                "qwen3.7-flash"
            ],
            parse: [
                "qwen3.7-flash",
                "qwen3.7-plus",
                "qwen3.8-max"
            ],
            classify: [
                "qwen3.7-flash",
                "qwen3.7-plus"
            ],
            ocr: [
                "qwen3.7-flash",
                "qwen3.7-plus"
            ],
            draw: [
                "qwen3.7-plus",
                "qwen3.8-max",
                "qwen3.7-flash"
            ]
        };

        const BAILIAN_MODEL_DEFAULTS_BY_TASK = {
            solve: "qwen3.7-plus",
            parse: "qwen3.7-flash",
            classify: "qwen3.7-flash",
            ocr: "qwen3.7-flash",
            draw: "qwen3.7-plus"
        };

        // 默认预设模型列表
        // DeepSeek 官方当前主推 deepseek-flash（= DeepSeek-V4.1-Flash，1M 上下文、
        // 支持思考模式与原生视觉）；deepseek-v4-pro 仍在服务，保留可选。
        // 上一代 Flash 模型名已下线（仅保留兼容路由），不再作为预设。
        const MODEL_PRESETS = {
            deepseek: [
                "deepseek-flash",
                "deepseek-v4-pro"
            ],
            siliconflow: [
                "Qwen/Qwen3-VL-32B-Instruct",
                "Qwen/Qwen3-VL-8B-Instruct",
                "deepseek-ai/DeepSeek-V4-Pro",
                "deepseek-ai/DeepSeek-V4.1-Flash"
            ],
            bailian: BAILIAN_MODEL_PRESETS_BY_TASK,
            // 中转站 A（智元 token.sensenova.cn）—— 与 /v1/models 实测列表保持一致。
            zhongzhan_gpt: [
                "sensenova-6.8-flash-lite",
                "sensenova-6.7-flash-lite",
                "sensenova-u1.5-lite",
                "sensenova-u1-fast",
                "deepseek-v4-flash",
                "deepseek-v4-pro",
                "glm-5.2",
                "kimi-k3"
            ],
            // 中转站 B（agnes-ai.cn）—— 与 /v1/models 实测列表保持一致；
            // 模型名可拼接 ":reasoning" 后缀（如 agnes-2.5-pro-alpha:max），后缀在输入框内手工加。
            zhongzhan_claude: [
                "agnes-2.5-pro-alpha",
                "agnes-2.5-pro",
                "agnes-2.5-flash",
                "agnes-2.0-flash",
                "agnes-3.0-flash",
                "agnes-2.5-pro-beta",
                "agnes-image-2.5-flash",
                "agnes-video-2.5-flash"
            ]
        };

        function getPresetModels(provider, typeKey = "") {
            const configured = MODEL_PRESETS[provider];
            if (Array.isArray(configured)) return configured;
            if (configured && typeof configured === 'object') {
                return configured[typeKey] || [];
            }
            return [];
        }

        function isPresetModel(provider, typeKey, modelValue) {
            return getPresetModels(provider, typeKey).includes(modelValue);
        }

        // 从 localStorage 恢复自定义模型，或者初始化
        function getModelListForProvider(provider, typeKey = "") {
            const presets = getPresetModels(provider, typeKey);
            let customs = [];
            try {
                const customStr = localStorage.getItem(`custom_models_${provider}`);
                if (customStr) {
                    const parsed = JSON.parse(customStr);
                    if (Array.isArray(parsed)) {
                        customs = parsed;
                    }
                }
            } catch (e) {
                console.error(`解析自定义模型列表失败 for ${provider}:`, e);
            }
            let list = Array.from(new Set([...presets, ...customs]));
            if (provider === 'siliconflow' && typeKey === 'ocr') {
                list = list.filter(m => !m.toLowerCase().includes('deepseek'));
            }
            return list;
        }

        function addCustomModelName(provider, typeKey) {
            const newName = prompt(`请输入要为 [${provider}] 新增的模型名称 (如 qwen3.7-flash):`);
            if (!newName || !newName.trim()) return;
            
            const trimmed = newName.trim();
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            const customs = customStr ? JSON.parse(customStr) : [];
            
            if (isPresetModel(provider, typeKey, trimmed)) {
                alert("该预设模型已存在于列表中！");
                return;
            }
            if (customs.includes(trimmed)) {
                alert("该自定义模型已存在于列表中！");
                return;
            }
            
            customs.push(trimmed);
            localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
            
            // 重新渲染选择器并选中新值
            renderModelSelector(typeKey, provider, trimmed);
        }

        function removeCustomModelName(provider, typeKey, modelValue) {
            if (isPresetModel(provider, typeKey, modelValue)) {
                alert("预设的核心模型不支持删除，只能删除自定义追加的模型。");
                return;
            }
            if (!confirm(`确认要从列表中删除自定义模型 "${modelValue}" 吗？`)) return;
            
            const customStr = localStorage.getItem(`custom_models_${provider}`);
            if (!customStr) return;
            
            let customs = JSON.parse(customStr);
            customs = customs.filter(m => m !== modelValue);
            localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
            
            // 重新渲染选择器，默认选中第一个
            renderModelSelector(typeKey, provider);
        }

        // 中转站独享：记忆保存当前输入的模型（保留旧入口，内部仍操作当前选择器的 select）
        function saveCustomZhongzhanModel(provider, typeKey) {
            const selectEl = document.getElementById(`settings_${typeKey}_model_select`);
            if (!selectEl) return;
            const newName = selectEl.value.trim();
            if (!newName) {
                alert("请先在模型下拉中选择一个模型！");
                return;
            }

            const customStr = localStorage.getItem(`custom_models_${provider}`);
            const customs = customStr ? JSON.parse(customStr) : [];

            if (!customs.includes(newName)) {
                customs.push(newName);
                localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
                showToast(`已将模型 "${newName}" 记录至下拉历史列表`, "success");
            } else {
                showToast("该模型已存在于下拉历史中", "info");
            }

            // 刷新渲染
            renderModelSelector(typeKey, provider, newName);
        }

        // 中转站独享：从 localStorage 中删除该选项（保留旧入口，内部仍操作当前选择器的 select）
        function deleteCustomZhongzhanModel(provider, typeKey) {
            const selectEl = document.getElementById(`settings_${typeKey}_model_select`);
            if (!selectEl) return;
            const newName = selectEl.value.trim();
            if (!newName) return;

            const customStr = localStorage.getItem(`custom_models_${provider}`);
            if (!customStr) return;

            let customs = JSON.parse(customStr);
            if (!customs.includes(newName)) {
                alert(`未在下拉历史中找到模型 "${newName}"`);
                return;
            }

            if (!confirm(`确认要将模型 "${newName}" 从下拉历史列表中移除吗？`)) return;

            customs = customs.filter(m => m !== newName);
            localStorage.setItem(`custom_models_${provider}`, JSON.stringify(customs));
            showToast(`已从下拉历史中移除模型 "${newName}"`, "success");

            // 刷新并清空
            renderModelSelector(typeKey, provider, "");
        }

        // 各提供商的 Key / Base 输入框（用于「未保存也能拉取」）
        function providerCredentialInputs(provider) {
            const map = {
                deepseek: ['settingsDeepseekKey', ''],
                siliconflow: ['settingsSiliconflowKey', ''],
                bailian: ['settingsAliBailianKey', ''],
                zhongzhan_gpt: ['settingsZhongzhanGptKey', 'settingsZhongzhanGptBaseUrl'],
                zhongzhan_claude: ['settingsZhongzhanClaudeKey', 'settingsZhongzhanClaudeBaseUrl']
            };
            const entry = map[provider];
            if (!entry) return { keyEl: null, baseEl: null };
            return {
                keyEl: document.getElementById(entry[0]),
                baseEl: entry[1] ? document.getElementById(entry[1]) : null
            };
        }

        // 从提供商官网拉取真实模型列表，并入本地可选列表
        async function refreshModelsFromProvider(provider, typeKey) {
            const { keyEl, baseEl } = providerCredentialInputs(provider);
            const body = { provider: provider };
            // 输入框显示掩码（•）时不要作为 override 传过去，否则 U+2022 非 ASCII 会炸
            const keyVal = keyEl && keyEl.value ? keyEl.value.trim() : '';
            if (keyVal && !keyVal.includes('•')) body.api_key = keyVal;
            if (baseEl && baseEl.value.trim()) body.api_base = baseEl.value.trim();

            showToast('正在从官网拉取模型列表…', 'info');
            try {
                const res = await fetch('/api/ai/models', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Local-Token': window.getLocalToken ? window.getLocalToken() : ''
                    },
                    body: JSON.stringify(body)
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.status !== 'success') {
                    throw new Error(data.message || ('HTTP ' + res.status));
                }
                const models = Array.isArray(data.models) ? data.models : [];
                if (!models.length) {
                    showToast('官网未返回任何模型', 'warning');
                    return;
                }

                const storageKey = `custom_models_${provider}`;
                let customs = [];
                try {
                    const parsed = JSON.parse(localStorage.getItem(storageKey) || '[]');
                    if (Array.isArray(parsed)) customs = parsed;
                } catch (err) {
                    customs = [];
                }
                const merged = Array.from(new Set([...customs, ...models]));
                localStorage.setItem(storageKey, JSON.stringify(merged));

                const selectEl = document.getElementById(`settings_${typeKey}_model_select`);
                const inputEl = document.getElementById(`settings_${typeKey}_model_input`);
                const current = (selectEl && selectEl.value) || (inputEl && inputEl.value) || '';
                renderModelSelector(typeKey, provider, current || models[0]);
                showToast(`已拉取 ${models.length} 个模型，共 ${merged.length} 个可选`, 'success');
            } catch (error) {
                console.error('[Models] 拉取模型列表失败:', error);
                showToast('拉取失败：' + error.message, 'error');
            }
        }

        // 辅助解析解析 "PROVIDER/model" 前缀
        function parseModelConfig(val, defaultProvider, defaultModel) {
            if (val && val.includes("/")) {
                const idx = val.indexOf("/");
                const prov = val.substring(0, idx).toLowerCase();
                const name = val.substring(idx + 1);
                // 校验前缀合理性
                if (['deepseek', 'siliconflow', 'bailian', 'zhongzhan', 'zhongzhan_gpt', 'zhongzhan_claude'].includes(prov) || prov.indexOf('cp_') === 0) {
                    let targetProv = prov;
                    if (targetProv === 'zhongzhan') targetProv = 'zhongzhan_gpt'; // 兼容老数据
                    return { provider: targetProv, model: name };
                }
            }
            return { provider: defaultProvider, model: val || defaultModel };
        }

        // 动态装载模型选择/手写输入区域
        function renderModelSelector(typeKey, provider, selectedValue = "") {
            const container = document.getElementById(`${typeKey}ModelValueContainer`);
            if (!container) return;
            
            const isZhongzhan = provider === 'zhongzhan' || provider === 'zhongzhan_gpt' || provider === 'zhongzhan_claude';
            
            if (provider.indexOf('cp_') === 0) {
                // 自定义厂商（瘦版）：无预设模型列表，模型名手填。
                const inputId = `settings_${typeKey}_model_input`;
                container.innerHTML = `
                    <input id="${inputId}" type="text"
                           class="glass-input flex-1 px-2.5 py-1.5 rounded-lg text-xs font-mono min-w-0"
                           placeholder="输入模型名（如 gpt-4o）"
                           value="${MathBankSafe.escapeAttribute(selectedValue || '')}">
                `;
            } else if (isZhongzhan) {
                // 中转站模式：与官方渠道统一使用真正的 <select> 下拉，避免 datalist 仅自动填充无展开列表。
                // 模型名中可拼接的推理强度（如 :max）单独用一个下拉控制。
                let rawModel = selectedValue || "";
                let reasoningEffort = "default";

                if (rawModel.includes(":")) {
                    const parts = rawModel.split(":");
                    const lastPart = parts[parts.length - 1].toLowerCase();
                    if (['default', 'low', 'medium', 'high', 'xhigh', 'max'].includes(lastPart)) {
                        reasoningEffort = lastPart;
                        rawModel = parts.slice(0, parts.length - 1).join(":");
                    }
                } else if (rawModel.endsWith(")") && rawModel.includes("(")) {
                    const match = rawModel.match(/^(.*?)\((default|low|medium|high|xhigh|max)\)$/i);
                    if (match) {
                        rawModel = match[1].trim();
                        reasoningEffort = match[2].toLowerCase();
                    }
                }

                const selectId = `settings_${typeKey}_model_select`;
                const models = getModelListForProvider(provider, typeKey);
                const strippedModels = Array.from(new Set(models.map(m => {
                    if (!m.includes(":")) return m;
                    const parts = m.split(":");
                    const last = parts[parts.length - 1].toLowerCase();
                    return ['default', 'low', 'medium', 'high', 'xhigh', 'max'].includes(last)
                        ? parts.slice(0, parts.length - 1).join(":")
                        : m;
                })));
                if (rawModel && !strippedModels.includes(rawModel)) {
                    strippedModels.unshift(rawModel);
                }
                let optionsHtml = strippedModels.map(m => {
                    const isSel = m === rawModel ? 'selected' : '';
                    return `<option value="${MathBankSafe.escapeAttribute(m)}" ${isSel}>${MathBankSafe.escapeText(m)}</option>`;
                }).join('');
                if (!optionsHtml) {
                    optionsHtml = `<option value="" disabled>请先点击右侧刷新按钮拉取模型列表</option>`;
                }

                container.innerHTML = `
                    <select id="${selectId}" class="glass-select flex-1 px-2.5 py-1.5 rounded-lg text-xs min-w-0">
                        ${optionsHtml}
                    </select>
                    <select id="settings_${typeKey}_reasoning_select" class="glass-select w-[72px] shrink-0 px-1.5 py-1.5 rounded-lg text-xs font-mono text-slate-700 dark:text-slate-200" title="选择中转站模型的推理强度 (Reasoning Effort)">
                        <option value="default" ${reasoningEffort === 'default' ? 'selected' : ''}></option>
                        <option value="low" ${reasoningEffort === 'low' ? 'selected' : ''}>low</option>
                        <option value="medium" ${reasoningEffort === 'medium' ? 'selected' : ''}>medium</option>
                        <option value="high" ${reasoningEffort === 'high' ? 'selected' : ''}>high</option>
                        <option value="xhigh" ${reasoningEffort === 'xhigh' ? 'selected' : ''}>xhigh</option>
                        <option value="max" ${reasoningEffort === 'max' ? 'selected' : ''}>max</option>
                    </select>
                    <button type="button" onclick="refreshModelsFromProvider('${provider}', '${typeKey}')"
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-brand-500 hover:text-brand-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="从官网拉取最新模型列表">
                        <i class="fa-solid fa-arrows-rotate"></i>
                    </button>
                    <button type="button" onclick="addCustomModelName('${provider}', '${typeKey}')"
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-brand-500 hover:text-brand-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="新增自定义模型">
                        <i class="fa-solid fa-plus"></i>
                    </button>
                    <button type="button" onclick="const val = document.getElementById('${selectId}').value; removeCustomModelName('${provider}', '${typeKey}', val)"
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-red-500 hover:text-red-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="删除当前选中的自定义模型">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
            } else {
                // 下拉菜单列表模式
                let models = getModelListForProvider(provider, typeKey);
                // 旧版预设或手工写入的当前配置必须继续可见，避免打开设置即被首项覆盖。
                if (selectedValue && !models.includes(selectedValue)) {
                    models = [selectedValue, ...models];
                }
                const selectId = `settings_${typeKey}_model_select`;
                
                let optionsHtml = models.map(m => {
                    const isSel = m === selectedValue ? 'selected' : '';
                    return `<option value="${MathBankSafe.escapeAttribute(m)}" ${isSel}>${MathBankSafe.escapeText(m)}</option>`;
                }).join('');
                
                container.innerHTML = `
                    <select id="${selectId}" class="glass-select flex-1 px-2.5 py-1.5 rounded-lg text-xs min-w-0">
                        ${optionsHtml}
                    </select>
                    <button type="button" onclick="refreshModelsFromProvider('${provider}', '${typeKey}')" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-brand-500 hover:text-brand-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="从官网拉取最新模型列表">
                        <i class="fa-solid fa-arrows-rotate"></i>
                    </button>
                    <button type="button" onclick="addCustomModelName('${provider}', '${typeKey}')" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-brand-500 hover:text-brand-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="新增自定义模型">
                        <i class="fa-solid fa-plus"></i>
                    </button>
                    <button type="button" onclick="const val = document.getElementById('${selectId}').value; removeCustomModelName('${provider}', '${typeKey}', val)" 
                            class="h-7 w-7 rounded-lg border border-slate-200 hover:border-red-500 hover:text-red-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white" title="删除当前选中的自定义模型">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                `;
            }
        }

        // 全局挂载联动 onchange
        window.onModelProviderChange = function(typeKey) {
            const providerSelect = document.getElementById(`${typeKey}ModelProvider`);
            if (!providerSelect) return;
            const provider = providerSelect.value;
            
            // 默认取个常用模型初始化
            let defVal = "";
            if (provider === 'deepseek') defVal = "deepseek-flash";
            else if (provider === 'siliconflow') {
                defVal = typeKey === 'ocr' ? "Qwen/Qwen3-VL-8B-Instruct" : "deepseek-ai/DeepSeek-V4.1-Flash";
            } else if (provider === 'bailian') {
                defVal = BAILIAN_MODEL_DEFAULTS_BY_TASK[typeKey] || "qwen3.7-flash";
            } else if (provider === 'zhongzhan_gpt') {
                defVal = ""; // 默认空白，供用户填写
            } else if (provider === 'zhongzhan_claude') {
                defVal = ""; // 默认空白，供用户填写
            } else if (provider.indexOf('cp_') === 0) {
                defVal = ""; // 自定义厂商：模型名由用户手填
            }
            
            renderModelSelector(typeKey, provider, defVal);
        };
        
        // 挂载辅助方法到全局
        window.addCustomModelName = addCustomModelName;
        window.removeCustomModelName = removeCustomModelName;
        window.saveCustomZhongzhanModel = saveCustomZhongzhanModel;
        window.deleteCustomZhongzhanModel = deleteCustomZhongzhanModel;
        window.refreshModelsFromProvider = refreshModelsFromProvider;

        // Settings Modal Controls

        // 常见厂商默认 Base URL（含中转站代理），选中后自动填入隐藏输入框，避免用户手动填写 /v1/chat/completions 导致 token 浪费。
        const VENDOR_DEFAULT_BASE_URLS = Object.freeze({
            'DeepSeek': 'https://api.deepseek.com/v1',
            'SiliconFlow': 'https://api.siliconflow.cn/v1',
            '阿里百炼': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'MinerU': '',
            '智元/SenseNova': 'https://token.sensenova.cn/v1',
            'Agnes AI': 'https://apihub.agnes-ai.cn/v1',
            'OpenRouter': 'https://openrouter.ai/api/v1',
            'Together AI': 'https://api.together.xyz/v1'
        });

        // 常见厂商控制台/获取 Key 地址
        const VENDOR_CONSOLE_URLS = Object.freeze({
            'DeepSeek': 'https://platform.deepseek.com/',
            'SiliconFlow': 'https://cloud.siliconflow.cn/',
            '阿里百炼': 'https://bailian.console.aliyun.com/',
            'MinerU': 'https://mineru.net/',
            '智元/SenseNova': 'https://token.sensenova.cn',
            'Agnes AI': 'https://apihub.agnes-ai.cn',
            'OpenRouter': 'https://openrouter.ai/keys',
            'Together AI': 'https://api.together.xyz/settings/api-keys'
        });

        function vendorRootUrl(baseUrl) {
            const raw = String(baseUrl || '').trim();
            if (!raw) return '';
            let url = raw;
            if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
            try {
                const parsed = new URL(url);
                parsed.pathname = '';
                parsed.search = '';
                parsed.hash = '';
                return parsed.toString().replace(/\/$/, '');
            } catch (error) {
                return '';
            }
        }

        // 规范化为 /v1 结尾的 base URL，避免用户误填 /v1/chat/completions 导致 token 浪费。
        function normalizeApiBase(baseUrl) {
            const raw = String(baseUrl || '').trim();
            if (!raw) return raw;
            let url = raw;
            if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
            try {
                const parsed = new URL(url);
                const pathParts = parsed.pathname.split('/').filter(Boolean);
                // 找到 /v1 或 /v2 这类 API 版本根，后面多余路径截掉
                const versionIdx = pathParts.findIndex(p => /^v\d+$/i.test(p));
                if (versionIdx !== -1) {
                    parsed.pathname = '/' + pathParts.slice(0, versionIdx + 1).join('/');
                } else if (pathParts.length > 0) {
                    // 没有标准版本号时只保留第一级路径（如 /api）
                    parsed.pathname = '/' + pathParts[0];
                } else {
                    parsed.pathname = '';
                }
                parsed.search = '';
                parsed.hash = '';
                return parsed.toString().replace(/\/$/, '');
            } catch (error) {
                return raw;
            }
        }

        function applyZhongzhanVendorHref(link, baseUrl) {
            if (!link) return;
            const root = vendorRootUrl(baseUrl);
            if (root) {
                link.href = root;
                link.removeAttribute('aria-disabled');
                link.classList.remove('opacity-50', 'pointer-events-none');
                link.setAttribute('title', '打开厂商控制台: ' + root);
            } else {
                link.href = '#';
                link.setAttribute('aria-disabled', 'true');
                link.classList.add('opacity-50', 'pointer-events-none');
                link.setAttribute('title', '请先填写 Base URL，将自动跳转到厂商控制台');
            }
        }

        function updateZhongzhanVendorLinks() {
            const gptBase = document.getElementById('settingsZhongzhanGptBaseUrl');
            const claudeBase = document.getElementById('settingsZhongzhanClaudeBaseUrl');
            document.querySelectorAll('.zhongzhan-gpt-link').forEach(link => {
                applyZhongzhanVendorHref(link, gptBase ? gptBase.value : '');
            });
            document.querySelectorAll('.zhongzhan-claude-link').forEach(link => {
                applyZhongzhanVendorHref(link, claudeBase ? claudeBase.value : '');
            });
        }

        // 三家内置厂商「接口地址」输入框的默认值（与设置页 placeholder 一致）。
        // 注：DeepSeek 官方用 https://api.deepseek.com（不带 /v1），与下方
        // VENDOR_DEFAULT_BASE_URLS 的中转站映射不同，这里单独维护。
        const VENDOR_BASE_URL_DEFAULTS = Object.freeze({
            'DeepSeek': 'https://api.deepseek.com',
            'SiliconFlow': 'https://api.siliconflow.cn/v1',
            '阿里百炼': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
            'MinerU': 'https://mineru.net/api/v4',
            '智元/SenseNova': 'https://token.sensenova.cn/v1',
            'Agnes AI': 'https://apihub.agnes-ai.cn/v1'
        });

        // 反向识别：在「接口地址」框输入时，按 hostname 自动推断厂商名。
        // 命中 → 把左侧厂商名下拉切到对应项（但不回写地址框，避免覆盖用户手填的地址）。
        const BASE_URL_VENDOR_HOST_MAP = Object.freeze([
            { host: 'dashscope.aliyuncs.com', vendor: '阿里百炼' },
            { host: 'token-plan.maas.qianwenai.com', vendor: '阿里百炼' },
            { host: 'api.deepseek.com', vendor: 'DeepSeek' },
            { host: 'api.siliconflow.cn', vendor: 'SiliconFlow' },
            { host: 'mineru.net', vendor: 'MinerU' },
            { host: 'token.sensenova.cn', vendor: '智元/SenseNova' },
            { host: 'apihub.agnes-ai.cn', vendor: 'Agnes AI' }
        ]);

        function onBaseUrlInput(inputEl) {
            if (!inputEl) return;
            const row = inputEl.closest('.api-key-row');
            const select = row ? row.querySelector('.vendor-select') : null;
            // 前四行（DeepSeek/SiliconFlow/阿里百炼/MinerU）厂商名已 disabled 不可切换：
            // 反查命中别的厂商时只改显示会变成「写着百炼、实际还是 DeepSeek 那一行」的误导，
            // 因此固定行一律不跟随地址反查（地址仍可手填，保存目标不变）。
            if (!select || select.disabled) return;
            const url = (inputEl.value || '').trim().toLowerCase();
            if (!url) return;
            let matched = '';
            for (const m of BASE_URL_VENDOR_HOST_MAP) {
                if (url.indexOf(m.host) !== -1) {
                    matched = m.vendor;
                    break;
                }
            }
            // 命中且当前选择不同：只切厂商名下拉（触发右侧模型候选同步），
            // 不调用 autoFillVendorBaseUrl，以免把用户刚手填的自定义地址冲掉。
            if (matched && select.value !== matched) {
                select.value = matched;
                syncModelProviderOptions();
            }
        }
        window.onBaseUrlInput = onBaseUrlInput;

        // 根据厂商名自动填充厂商行的 Base URL 与控制台链接（兼容中转站隐藏框 + 三家内置可见框）
        function autoFillVendorBaseUrl(selectEl) {
            if (!selectEl) return;
            const row = selectEl.closest('.api-key-row');
            if (!row) return;
            const vendor = selectEl.value || '';
            const hiddenBaseInput = row.querySelector('input[type="hidden"][id$="BaseUrl"]');
            const visibleBaseInput = row.querySelector('input[id$="BaseUrl"]:not([type="hidden"])');
            const consoleLink = row.querySelector('a[href]');
            const keyInput = row.querySelector('input[type="password"]');

            if (vendor && VENDOR_DEFAULT_BASE_URLS[vendor] !== undefined) {
                if (hiddenBaseInput) hiddenBaseInput.value = VENDOR_DEFAULT_BASE_URLS[vendor];
                // 三家内置厂商的可见「接口地址」框：选中厂商名时自动填回默认地址
                if (visibleBaseInput && VENDOR_BASE_URL_DEFAULTS[vendor] !== undefined) {
                    visibleBaseInput.value = VENDOR_BASE_URL_DEFAULTS[vendor];
                }
                if (consoleLink) {
                    consoleLink.href = VENDOR_CONSOLE_URLS[vendor] || '#';
                    consoleLink.removeAttribute('aria-disabled');
                    consoleLink.classList.remove('opacity-50', 'pointer-events-none');
                    consoleLink.setAttribute('title', '打开厂商控制台: ' + (VENDOR_CONSOLE_URLS[vendor] || ''));
                }
                if (keyInput) {
                    const suffix = vendor === 'MinerU' ? ' Token' : ' API Key';
                    keyInput.placeholder = '输入 ' + vendor + suffix;
                }
            }
            updateZhongzhanVendorLinks();
        }

        // 厂商名下拉切换后，让同行 Key 输入框的「标签 + placeholder」跟着新厂商名走。
        // 注意：只改显示文字，绝不改 input 的 id —— 保存链路（saveSettings /
        // providerCredentialInputs）全靠固定 id 定位，动 id 会把.env 写入错位。
        // 后缀规则与 autoFillVendorBaseUrl 保持一致（MinerU 是 Token 不是 API Key）。
        function syncVendorRowLabels(rowEl) {
            if (!rowEl) return;
            const select = rowEl.querySelector('.vendor-select');
            const keyInput = rowEl.querySelector('input[type="password"]');
            if (!select || !keyInput) return;
            const vendor = (select.value || '').trim();
            // 「其他…」（__custom__）时输入框已被换成自由文本厂商名，保持上一个厂商名不动
            if (!vendor || vendor === '__custom__') return;
            const keyLabel = keyInput.parentElement ? keyInput.parentElement.querySelector('label') : null;
            if (!keyLabel) return;
            const suffix = vendor === 'MinerU' ? ' Token' : ' API Key';
            keyLabel.textContent = vendor + suffix;
            keyInput.placeholder = '输入 ' + vendor + suffix;
        }

        // 批量同步所有已标记的厂商行（设置弹窗打开时做初始化）
        function syncAllVendorRowLabels() {
            document.querySelectorAll('#settings-tab-api [data-vendor-row]')
                .forEach(function (row) { syncVendorRowLabels(row); });
        }

        // 左侧厂商名选择「其他…」时变成可手动输入的文本框，并暴露 Base URL 输入框供自定义
        function onVendorSelectChange(selectEl) {
            if (!selectEl || selectEl.disabled) return;
            // 用 [data-vendor-row] 而不是 [data-provider-slot]：后者只有中转站两行带，
            // 其余四行会取到 null（旧写法留下的暗坑）。
            const row = selectEl.closest('[data-vendor-row]');
            if (selectEl.value === '__custom__') {
                // 把隐藏 Base URL 输入框换成可见输入框，让用户填写自定义地址
                if (row) {
                    const baseInput = row.querySelector('input[type="hidden"][id$="BaseUrl"]');
                    if (baseInput) {
                        const visible = document.createElement('input');
                        visible.type = 'text';
                        visible.id = baseInput.id;
                        visible.className = 'glass-input w-full px-3 py-2 rounded-lg text-xs font-mono mt-1';
                        visible.placeholder = '输入自定义 Base URL（如 https://api.example.com/v1）';
                        visible.value = baseInput.value || '';
                        baseInput.parentNode.replaceChild(visible, baseInput);
                    }
                }
                const input = document.createElement('input');
                input.type = 'text';
                input.className = selectEl.className;
                input.placeholder = '输入厂商名';
                input.value = '';
                input.dataset.wasSelect = '1';
                input.addEventListener('change', () => { syncModelProviderOptions(); autoFillVendorBaseUrl(input); });
                selectEl.parentNode.replaceChild(input, selectEl);
                input.focus();
            } else if (row) {
                // 切回已知厂商：确保 Base URL 输入框为隐藏态并自动填入默认值
                let baseInput = row.querySelector('input[type="text"][id$="BaseUrl"]');
                if (baseInput) {
                    const hidden = document.createElement('input');
                    hidden.type = 'hidden';
                    hidden.id = baseInput.id;
                    hidden.value = VENDOR_DEFAULT_BASE_URLS[selectEl.value] || '';
                    baseInput.parentNode.replaceChild(hidden, baseInput);
                }
                autoFillVendorBaseUrl(selectEl);
            }
            // 无论走哪个分支，Key 输入框的标签与 placeholder 都要跟上当前厂商名
            syncVendorRowLabels(selectEl.closest('[data-vendor-row]'));
        }

        // 根据左侧 API 密钥配置里出现的厂商，同步右侧「首选模型」的 provider 下拉可选项。
        function syncModelProviderOptions() {
            const vendorEls = document.querySelectorAll('#settings-tab-api .vendor-select');
            const labels = new Set();
            vendorEls.forEach(el => {
                const v = (el.value || '').trim();
                if (v && v !== '__custom__') labels.add(v);
            });
            // 左侧「其他…」手动输入框也要收集
            document.querySelectorAll('#settings-tab-api input[data-was-select="1"]').forEach(el => {
                const v = (el.value || '').trim();
                if (v) labels.add(v);
            });

            const vendorToProvider = {
                'DeepSeek': 'deepseek',
                'SiliconFlow': 'siliconflow',
                '阿里百炼': 'bailian',
                'MinerU': 'mineru',
                '智元/SenseNova': 'zhongzhan_gpt',
                'Agnes AI': 'zhongzhan_claude',
                'OpenRouter': 'openrouter',
                'Together AI': 'together'
            };
            const providerDisplay = {
                'deepseek': 'DeepSeek',
                'siliconflow': '硅基流动',
                'bailian': '阿里百炼',
                'zhongzhan_gpt': '中转站 A',
                'zhongzhan_claude': '中转站 B'
            };
            const allowedByType = {
                solve: ['deepseek', 'siliconflow', 'bailian', 'zhongzhan_gpt', 'zhongzhan_claude'],
                parse: ['deepseek', 'siliconflow', 'bailian', 'zhongzhan_gpt', 'zhongzhan_claude'],
                classify: ['deepseek', 'siliconflow', 'bailian', 'zhongzhan_gpt', 'zhongzhan_claude'],
                ocr: ['siliconflow', 'bailian', 'zhongzhan_gpt', 'zhongzhan_claude'],
                draw: ['deepseek', 'siliconflow', 'bailian', 'zhongzhan_gpt', 'zhongzhan_claude']
            };

            let activeProviders = [];
            labels.forEach(label => {
                const p = vendorToProvider[label];
                if (p && !activeProviders.includes(p)) activeProviders.push(p);
            });
            // 若左侧未配置任何可映射厂商，则保留全部候选项兜底
            const fallbackProviders = ['deepseek', 'siliconflow', 'bailian', 'zhongzhan_gpt', 'zhongzhan_claude'];
            const source = activeProviders.length ? activeProviders : fallbackProviders;

            Object.keys(allowedByType).forEach(typeKey => {
                const sel = document.getElementById(`${typeKey}ModelProvider`);
                if (!sel) return;
                const cur = sel.value;
                const allowed = allowedByType[typeKey];
                const target = source.filter(p => allowed.includes(p));
                if (!target.length) return;
                sel.innerHTML = target.map(p => `<option value="${p}">${providerDisplay[p] || p}</option>`).join('');
                if (target.includes(cur)) {
                    sel.value = cur;
                } else {
                    sel.value = target[0];
                    // provider 改变后必须重新渲染模型选择器
                    onModelProviderChange(typeKey);
                }
            });
            // 重建下拉后重新把自定义厂商（cp_ 前缀）选项补回（仅文本类任务）
            syncCustomProviderOptions();
        }

        // ===== 自定义厂商（瘦版：仅 OpenAI 兼容的第三方文本厂商） =====
        // 状态保存在模块级数组；openSettingsModal 时从后端回填，saveSettings 时序列化提交。
        let customProviders = [];

        // 把厂商名压成稳定大写 slug（与后端 ai_providers._slugify 保持一致：去非 A-Z0-9）
        function slugifyVendorName(name) {
            return String(name || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
        }

        function getCustomProviderOptions() {
            // 返回 [{value:'cp_<slug>', label: name}]，供首选模型 provider 下拉使用。
            // value 用小写 slug，与 parseModelConfig 解析出来的小写 provider 一致；
            // 保存时会被 toUpperCase 成 CP_<SLUG> 后再交给后端 resolve_text_provider。
            return customProviders
                .map(p => {
                    const slug = slugifyVendorName(p.name);
                    return slug ? { value: 'cp_' + slug.toLowerCase(), label: p.name } : null;
                })
                .filter(Boolean);
        }

        // 渲染自定义厂商行到 #customProvidersContainer
        function renderCustomProviders() {
            const container = document.getElementById('customProvidersContainer');
            if (!container) return;
            if (!customProviders.length) {
                container.innerHTML = '';
                return;
            }
            container.innerHTML = customProviders.map(p => {
                const slug = slugifyVendorName(p.name);
                return `
                    <div class="api-key-row space-y-1" data-cp-slug="${MathBankSafe.escapeAttribute(slug)}">
                        <div class="glass-card rounded-xl p-3 space-y-2 shadow-sm">
                            <div class="flex items-center justify-between">
                                <div class="flex items-center gap-2 min-w-0">
                                    <span class="text-[11px] font-bold text-slate-700">${MathBankSafe.escapeText(p.name)}</span>
                                    <span class="text-[9px] font-mono text-slate-400">cp_${MathBankSafe.escapeText(slug)}</span>
                                </div>
                                <button type="button" onclick="removeCustomProvider('${MathBankSafe.escapeAttribute(slug)}')"
                                        class="h-7 w-7 rounded-lg border border-slate-200 hover:border-red-500 hover:text-red-600 flex items-center justify-center text-xs transition-colors shrink-0 bg-white"
                                        title="删除该自定义厂商">
                                    <i class="fa-solid fa-trash-can"></i>
                                </button>
                            </div>
                            <div class="grid grid-cols-[minmax(0,2fr)_minmax(0,2fr)_minmax(0,2fr)] gap-2">
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 mb-0.5">Base URL</label>
                                    <input type="text" class="glass-input w-full px-2 py-1.5 rounded-lg text-xs font-mono"
                                           value="${MathBankSafe.escapeAttribute(p.base_url)}" readonly>
                                </div>
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 mb-0.5">API Key</label>
                                    <input type="password" class="glass-input w-full px-2 py-1.5 rounded-lg text-xs font-mono"
                                           value="${MathBankSafe.escapeAttribute(p.api_key)}" readonly>
                                </div>
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 mb-0.5">默认模型</label>
                                    <input type="text" class="glass-input w-full px-2 py-1.5 rounded-lg text-xs font-mono"
                                           value="${MathBankSafe.escapeAttribute(p.model)}" readonly>
                                </div>
                            </div>
                        </div>
                    </div>`;
            }).join('');
        }

        // 把自定义厂商选项同步进右侧「首选模型」的 provider 下拉（仅文本类任务：解题/拆解/分类/绘图；不含 OCR）。
        function syncCustomProviderOptions() {
            const cpOptions = getCustomProviderOptions();
            if (!cpOptions.length) return;
            const providerSelects = ['solveModelProvider', 'parseModelProvider', 'classifyModelProvider', 'drawModelProvider'];
            providerSelects.forEach(id => {
                const sel = document.getElementById(id);
                if (!sel) return;
                cpOptions.forEach(opt => {
                    if (!Array.from(sel.options).some(o => o.value === opt.value)) {
                        const o = document.createElement('option');
                        o.value = opt.value;
                        o.textContent = opt.label;
                        sel.appendChild(o);
                    }
                });
            });
        }

        // 打开添加自定义厂商的小窗
        function openCustomVendorModal() {
            ['cvName', 'cvBaseUrl', 'cvApiKey', 'cvModel'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = '';
            });
            // 一律走公共开关助手：下一帧清 opacity-0（rAF+setTimeout 双保险），
            // 关闭时立刻复位 hidden，杜绝"看不见的点击吞噬层"这类整页假死。
            window.openModalWithTransition('customVendorModal', { onEscape: closeCustomVendorModal });
        }

        function closeCustomVendorModal() {
            window.closeModalWithTransition('customVendorModal');
        }

        function submitCustomVendor() {
            const nameEl = document.getElementById('cvName');
            const baseEl = document.getElementById('cvBaseUrl');
            const keyEl = document.getElementById('cvApiKey');
            const modelEl = document.getElementById('cvModel');
            const name = (nameEl && nameEl.value || '').trim();
            if (!name) {
                showToast('请填写厂商名', 'error');
                return;
            }
            const slug = slugifyVendorName(name);
            if (!slug) {
                showToast('厂商名需包含至少一个字母或数字', 'error');
                return;
            }
            if (customProviders.some(p => slugifyVendorName(p.name) === slug)) {
                showToast('已存在同名（或同 slug）的自定义厂商', 'error');
                return;
            }
            customProviders.push({
                name: name,
                base_url: (baseEl && baseEl.value || '').trim(),
                api_key: (keyEl && keyEl.value || '').trim(),
                model: (modelEl && modelEl.value || '').trim()
            });
            renderCustomProviders();
            syncCustomProviderOptions();
            closeCustomVendorModal();
            showToast('已添加自定义厂商：' + name, 'success');
        }

        // 删除自定义厂商行
        function removeCustomProvider(slug) {
            if (!confirm('确认删除该自定义厂商？')) return;
            customProviders = customProviders.filter(p => slugifyVendorName(p.name) !== slug);
            renderCustomProviders();
            syncCustomProviderOptions();
        }

        window.openCustomVendorModal = openCustomVendorModal;
        window.closeCustomVendorModal = closeCustomVendorModal;
        window.submitCustomVendor = submitCustomVendor;
        window.removeCustomProvider = removeCustomProvider;

        function openSettingsModal() {
            if (window.switchSettingsTab) {
                window.switchSettingsTab('api');
            }
            const modal = document.getElementById('settingsModal');
            document.body.classList.add('modal-active');
            
            // 暂时移除状态灯的呼吸闪烁，防止在半透明模糊遮罩后面晃眼闪烁
            const indicatorDot = document.querySelector('#apiStatusIndicator span');
            if (indicatorDot) {
                indicatorDot.classList.remove('animate-pulse');
            }
            
            // Prefill inputs with current active settings from backend
            fetch('/api/settings')
                .then(r => r.json())
                .then(settings => {
                    document.getElementById('settingsDeepseekKey').value = settings.deepseek_key || '';
                    document.getElementById('settingsSiliconflowKey').value = settings.siliconflow_key || '';
                    document.getElementById('settingsAliBailianKey').value = settings.ali_bailian_key || '';
                    document.getElementById('settingsMineruToken').value = settings.mineru_token || '';
                    // 回填三家内置厂商的自定义接口地址（场景C：重启后重开仍显示自定义地址）
                    document.getElementById('settingsDeepseekBaseUrl').value = settings.deepseek_base_url || '';
                    document.getElementById('settingsSiliconflowBaseUrl').value = settings.siliconflow_base_url || '';
                    document.getElementById('settingsAliBailianBaseUrl').value = settings.ali_bailian_base_url || '';
                    document.getElementById('settingsMineruApiBase').value = settings.mineru_api_base || '';
                    
                    document.getElementById('settingsZhongzhanGptKey').value = settings.zhongzhan_gpt_key || '';
                    document.getElementById('settingsZhongzhanGptBaseUrl').value = settings.zhongzhan_gpt_base_url || '';
                    document.getElementById('settingsZhongzhanClaudeKey').value = settings.zhongzhan_claude_key || '';
                    document.getElementById('settingsZhongzhanClaudeBaseUrl').value = settings.zhongzhan_claude_base_url || '';

                    // 根据已保存的 Base URL 回推中转站行的厂商名；未知的走「其他…」并显示 Base URL 输入框
                    function restoreZhongzhanVendorSlot(slotProvider, defaultVendor) {
                        const row = document.querySelector('[data-provider-slot="' + slotProvider + '"]');
                        if (!row) return;
                        const baseInput = document.getElementById(slotProvider === 'zhongzhan_gpt' ? 'settingsZhongzhanGptBaseUrl' : 'settingsZhongzhanClaudeBaseUrl');
                        const savedBase = (baseInput ? baseInput.value : '') || '';
                        const normalizedSaved = savedBase ? normalizeApiBase(savedBase).replace(/\/$/, '') : '';
                        let matchedVendor = defaultVendor;
                        if (savedBase) {
                            for (const [vendor, base] of Object.entries(VENDOR_DEFAULT_BASE_URLS)) {
                                if (!base) continue;
                                const normalizedDefault = base.replace(/\/$/, '');
                                if (normalizedSaved === normalizedDefault || savedBase.indexOf(normalizedDefault) === 0) {
                                    matchedVendor = vendor;
                                    break;
                                }
                            }
                            if (!matchedVendor) matchedVendor = '__custom__';
                        }
                        const select = row.querySelector('.vendor-select');
                        if (select) {
                            select.value = matchedVendor || defaultVendor;
                            // 如果选中的是未知厂商，需要把隐藏 Base URL 换成可见输入框
                            if (select.value === '__custom__') {
                                onVendorSelectChange(select);
                                const customInput = row.querySelector('input[data-was-select="1"]');
                                if (customInput) customInput.value = '';
                            } else {
                                autoFillVendorBaseUrl(select);
                            }
                        }
                    }
                    restoreZhongzhanVendorSlot('zhongzhan_gpt', '智元/SenseNova');
                    restoreZhongzhanVendorSlot('zhongzhan_claude', 'Agnes AI');
                    // 厂商名回填后，Key 输入框的标签/placeholder 同步成当前厂商名
                    syncAllVendorRowLabels();

                    // 回填自定义厂商（瘦版）并渲染动态行
                    customProviders = Array.isArray(settings.custom_providers)
                        ? settings.custom_providers.map(p => ({
                            name: String(p.name || ''),
                            base_url: String(p.base_url || ''),
                            api_key: String(p.api_key || ''),
                            model: String(p.model || '')
                        }))
                        : [];
                    renderCustomProviders();

                    updateZhongzhanVendorLinks();
                    syncModelProviderOptions();

                    // 1. AI 智能解题模型
                    const solveCfg = parseModelConfig(settings.prefer_solve_model, 'deepseek', 'deepseek-flash');
                    document.getElementById('solveModelProvider').value = solveCfg.provider;
                    renderModelSelector('solve', solveCfg.provider, solveCfg.model);
                    
                    // 2. 试卷智能拆解模型
                    const parseCfg = parseModelConfig(settings.prefer_parse_model, 'deepseek', 'deepseek-flash');
                    document.getElementById('parseModelProvider').value = parseCfg.provider;
                    renderModelSelector('parse', parseCfg.provider, parseCfg.model);
                    
                    // 3. 题目智能分类模型
                    const classifyCfg = parseModelConfig(settings.prefer_classify_model, 'deepseek', 'deepseek-flash');
                    document.getElementById('classifyModelProvider').value = classifyCfg.provider;
                    renderModelSelector('classify', classifyCfg.provider, classifyCfg.model);
                    
                    // 4. 默认公式识图模型
                    let ocrProvider = settings.prefer_engine || 'siliconflow';
                    if (ocrProvider === 'ali_bailian') ocrProvider = 'bailian'; // 前后端对齐
                    if (ocrProvider === 'zhongzhan') ocrProvider = 'zhongzhan_gpt'; // 兼容老数据
                    
                    let ocrModel = "";
                    if (ocrProvider === 'siliconflow') ocrModel = settings.siliconflow_model || 'Qwen/Qwen3-VL-8B-Instruct';
                    else if (ocrProvider === 'bailian') ocrModel = settings.ali_bailian_model || 'qwen3.7-flash';
                    else if (ocrProvider === 'zhongzhan_gpt') ocrModel = settings.zhongzhan_gpt_ocr_model || 'gpt-4o';
                    else if (ocrProvider === 'zhongzhan_claude') ocrModel = settings.zhongzhan_claude_ocr_model || 'claude-3-5-sonnet';
                    
                    document.getElementById('ocrModelProvider').value = ocrProvider;
                    renderModelSelector('ocr', ocrProvider, ocrModel);
                    
                    // 4. 高级 TikZ 绘图模型
                    const drawCfg = parseModelConfig(settings.prefer_draw_model, 'siliconflow', 'Qwen/Qwen3-VL-32B-Instruct');
                    document.getElementById('drawModelProvider').value = drawCfg.provider;
                    renderModelSelector('draw', drawCfg.provider, drawCfg.model);
                })
                .catch(err => {
                    console.error('获取系统配置失败:', err);
                });

            // 同时拉取最新的自定义维度配置 JSON 并填入，防止直接保存时由于未切换 Tab 导致值为空引发校验错误
            fetch('/api/config/metadata')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('settingsMetadataJson').value = JSON.stringify(data, null, 2);
                })
                .catch(err => {
                    console.error('获取元数据配置失败:', err);
                });
                
            window.openModalWithTransition(modal, { onEscape: closeSettingsModal });
        }

        function closeSettingsModal() {
            const modal = document.getElementById('settingsModal');
            window.closeModalWithTransition(modal);
            document.body.classList.remove('modal-active');
            // 弹窗关闭后，如果状态灯是绿色的，恢复呼吸闪烁
            const indicatorDot = document.querySelector('#apiStatusIndicator span.bg-green-500');
            if (indicatorDot) {
                indicatorDot.classList.add('animate-pulse');
            }
        }

        // Graceful server shutdown trigger
        function confirmShutdown() {
            if (confirm("确定要关闭本地题库系统吗？关闭后网页端服务将停止运行，您需要重新双击运行桌面的启动脚本才能再次使用。")) {
                showToast("正在关闭后端服务，请稍候...", "info");
                
                // Show a beautiful premium full-screen overlay to block user interactions cleanly
                const overlay = document.createElement('div');
                overlay.className = "fixed inset-0 bg-slate-900/95 backdrop-blur-md flex flex-col items-center justify-center text-white z-[9999] transition-all duration-500 opacity-0";
                overlay.setAttribute('role', 'alertdialog');
                overlay.setAttribute('aria-modal', 'true');
                overlay.setAttribute('aria-live', 'assertive');
                overlay.setAttribute('aria-label', '题库系统正在关闭');
                overlay.innerHTML = `
                    <div class="p-8 max-w-md text-center space-y-5">
                        <div class="h-16 w-16 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-400 mx-auto text-3xl shadow-lg">
                            <i class="fa-solid fa-power-off"></i>
                        </div>
                        <div class="space-y-2">
                            <h2 class="text-xl font-bold tracking-wide font-['Outfit']">本地题库系统已关闭</h2>
                            <p class="text-xs text-slate-400 leading-relaxed">
                                后端服务进程已成功终止退出。现在您可以安全地关闭此浏览器标签页。
                            </p>
                        </div>
                        <div class="border-t border-slate-800 pt-4 text-left">
                            <p class="text-[11px] text-slate-500 mb-2 font-semibold">🔄 如何重新启动系统？</p>
                            <p class="text-[10px] text-slate-400 leading-normal">
                                如果需要重新开始研讨与录题，请再次双击执行工作空间下的 <code class="bg-slate-900 px-1.5 py-0.5 rounded text-red-300 font-mono text-[9px]">启动题库系统.command</code> 脚本即可。
                            </p>
                        </div>
                    </div>
                `;
                document.body.appendChild(overlay);
                window.revealModalWithTransition(overlay);

                // Send shutdown request to backend
                fetch('/api/shutdown', {
                    method: 'POST',
                    headers: {
                        'X-MathBank-Launch-ID': window.__serverInstanceId || ''
                    }
                })
                    .then(async response => {
                        const data = await response.json();
                        if (!response.ok) {
                            overlay.remove();
                            showToast("当前页面属于已重启前的旧实例，请刷新页面后再关闭。", "error");
                            const error = new Error(data.detail || data.message || 'Shutdown rejected');
                            error.shutdownRejected = true;
                            throw error;
                        }
                        return data;
                    })
                    .then(data => {
                    })
                    .catch(err => {
                        if (err && err.shutdownRejected) {
                            console.warn("Server shutdown rejected:", err);
                        } else {
                        }
                    });
            }
        }

        // --- TIKU DATABASE STATISTICS PANEL CONTROLLERS ---
        let globalStatsData = null;

        function saveSettings(e) {
            e.preventDefault();
            const key = document.getElementById('settingsDeepseekKey').value;
            const siliconflowKey = document.getElementById('settingsSiliconflowKey').value;
            const aliBailianKey = document.getElementById('settingsAliBailianKey').value;
            const mineruToken = document.getElementById('settingsMineruToken').value;
            const mineruApiBase = document.getElementById('settingsMineruApiBase').value.trim();

            // 三家内置厂商的自定义接口地址（Base URL）
            const deepseekBaseUrl = document.getElementById('settingsDeepseekBaseUrl').value.trim();
            const siliconflowBaseUrl = document.getElementById('settingsSiliconflowBaseUrl').value.trim();
            const aliBailianBaseUrl = document.getElementById('settingsAliBailianBaseUrl').value.trim();
            
            const zhongzhanGptKey = document.getElementById('settingsZhongzhanGptKey').value;
            const zhongzhanGptBaseUrl = normalizeApiBase(document.getElementById('settingsZhongzhanGptBaseUrl').value);
            const zhongzhanClaudeKey = document.getElementById('settingsZhongzhanClaudeKey').value;
            const zhongzhanClaudeBaseUrl = normalizeApiBase(document.getElementById('settingsZhongzhanClaudeBaseUrl').value);
            
            // 辅助获取二级选择结果
            function getSelectedModelValue(typeKey, provider) {
                if (provider === 'zhongzhan' || provider === 'zhongzhan_gpt' || provider === 'zhongzhan_claude') {
                    const select = document.getElementById(`settings_${typeKey}_model_select`);
                    const reasoningSelect = document.getElementById(`settings_${typeKey}_reasoning_select`);
                    let mVal = select ? select.value.trim() : '';
                    if (mVal && reasoningSelect) {
                        const effort = reasoningSelect.value;
                        if (effort && effort !== 'default') {
                            mVal = `${mVal}:${effort}`;
                        }
                    }
                    return mVal;
            } else {
                const select = document.getElementById(`settings_${typeKey}_model_select`);
                if (select) return select.value;
                // 自定义厂商（cp_）渲染的是手填输入框而非下拉
                const input = document.getElementById(`settings_${typeKey}_model_input`);
                return input ? input.value.trim() : '';
            }
            }
            
            // 1. AI 智能解题模型
            const solveProvider = document.getElementById('solveModelProvider').value;
            const solveModel = getSelectedModelValue('solve', solveProvider);
            const preferSolveModel = `${solveProvider.toUpperCase()}/${solveModel}`;
            
            // 2. 试卷智能拆解模型
            const parseProvider = document.getElementById('parseModelProvider').value;
            const parseModel = getSelectedModelValue('parse', parseProvider);
            const preferParseModel = `${parseProvider.toUpperCase()}/${parseModel}`;
            
            // 3. 题目智能分类模型
            const classifyProvider = document.getElementById('classifyModelProvider').value;
            const classifyModel = getSelectedModelValue('classify', classifyProvider);
            const preferClassifyModel = `${classifyProvider.toUpperCase()}/${classifyModel}`;
            
            // 4. 默认公式识图模型 (后端以 prefer_engine + siliconflow_model/ali_bailian_model/zhongzhan_gpt_ocr_model/zhongzhan_claude_ocr_model 区分)
            const ocrProvider = document.getElementById('ocrModelProvider').value;
            const ocrModel = getSelectedModelValue('ocr', ocrProvider);
            let preferEngine = ocrProvider;
            if (preferEngine === 'bailian') preferEngine = 'ali_bailian'; // 与后端对齐
            
            let siliconflowModel = "";
            let aliBailianModel = "";
            let zhongzhanGptOcrModel = "";
            let zhongzhanClaudeOcrModel = "";
            
            if (ocrProvider === 'siliconflow') siliconflowModel = ocrModel;
            else if (ocrProvider === 'bailian') aliBailianModel = ocrModel;
            else if (ocrProvider === 'zhongzhan_gpt') zhongzhanGptOcrModel = ocrModel;
            else if (ocrProvider === 'zhongzhan_claude') zhongzhanClaudeOcrModel = ocrModel;
            
            // 5. 高级 TikZ 绘图模型
            const drawProvider = document.getElementById('drawModelProvider').value;
            const drawModel = getSelectedModelValue('draw', drawProvider);
            const preferDrawModel = `${drawProvider.toUpperCase()}/${drawModel}`;
            
            const formData = new FormData();
            formData.append('deepseek_key', key);
            formData.append('siliconflow_key', siliconflowKey);
            formData.append('ali_bailian_key', aliBailianKey);

            formData.append('deepseek_base_url', deepseekBaseUrl);
            formData.append('siliconflow_base_url', siliconflowBaseUrl);
            formData.append('ali_bailian_base_url', aliBailianBaseUrl);

            formData.append('mineru_token', mineruToken);
            formData.append('mineru_api_base', mineruApiBase);

            formData.append('zhongzhan_gpt_key', zhongzhanGptKey);
            formData.append('zhongzhan_gpt_base_url', zhongzhanGptBaseUrl);
            formData.append('zhongzhan_gpt_ocr_model', zhongzhanGptOcrModel);
            formData.append('zhongzhan_claude_key', zhongzhanClaudeKey);
            formData.append('zhongzhan_claude_base_url', zhongzhanClaudeBaseUrl);
            formData.append('zhongzhan_claude_ocr_model', zhongzhanClaudeOcrModel);
            
            formData.append('prefer_engine', preferEngine);
            formData.append('siliconflow_model', siliconflowModel);
            formData.append('ali_bailian_model', aliBailianModel);
            formData.append('prefer_solve_model', preferSolveModel);
            formData.append('prefer_parse_model', preferParseModel);
            formData.append('prefer_classify_model', preferClassifyModel);
            formData.append('prefer_draw_model', preferDrawModel);

            // 自定义厂商（瘦版）序列化提交
            formData.append('custom_providers', JSON.stringify(customProviders || []));
            
            // Chain both saves: metadata JSON and ENV settings parameters
            let metaPayload = null;
            const metadataStr = document.getElementById('settingsMetadataJson').value.trim();
            try {
                metaPayload = JSON.parse(metadataStr);
            } catch (err) {
                showToast('元数据配置 JSON 格式错误，请检查括号与逗号！', 'error');
                return;
            }

            fetch('/api/config/metadata', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(metaPayload)
            })
            .then(r => {
                if (!r.ok) {
                    return r.json().then(d => { throw new Error(d.detail || '保存元数据配置失败') });
                }
                return r.json();
            })
            .then(() => {
                return fetch('/api/settings/save', {
                    method: 'POST',
                    body: formData
                });
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'success') {
                    showToast('所有配置（含自定义维度）保存成功！');
                    closeSettingsModal();
                    fetchConfigStatus();
                    loadCategories({ reloadCurrentQuestion: true });
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                showToast('保存接口设置出错: ' + (err.message || err), 'error');
            });
        }

        // 通用厂商连接测试（AI 服务商走 /api/ai/models，MinerU 走 /api/mineru/test）
        function testProviderConnection(provider, keyInputId, baseInputId, btnId, resultId) {
            const providerLabelMap = {
                deepseek: 'DeepSeek',
                siliconflow: 'SiliconFlow',
                bailian: '阿里百炼',
                mineru: 'MinerU',
                zhongzhan_gpt: '中转站 A',
                zhongzhan_claude: '中转站 B'
            };
            const label = providerLabelMap[provider] || provider;
            const btn = document.getElementById(btnId);
            const resultEl = resultId ? document.getElementById(resultId) : null;
            const keyInput = keyInputId ? document.getElementById(keyInputId) : null;
            const baseInput = baseInputId ? document.getElementById(baseInputId) : null;

            if (btn) {
                btn.disabled = true;
                btn.textContent = '测试中...';
            }
            if (resultEl) {
                resultEl.textContent = '正在测试连接...';
                resultEl.className = 'text-[10px] text-slate-400 leading-normal test-result min-h-[1rem]';
            }

            async function runTest() {
                if (provider === 'mineru') {
                    const res = await fetch('/api/mineru/test');
                    const data = await res.json().catch(() => ({}));
                    if (data.status === 'success') {
                        return { ok: true, message: data.message || '连接成功' };
                    }
                    throw new Error(data.message || 'MinerU 连接失败');
                }

                const keyVal = keyInput ? keyInput.value.trim() : '';
                const rawBase = baseInput ? baseInput.value.trim() : '';
                const baseVal = rawBase ? normalizeApiBase(rawBase) : '';
                if (!keyVal) {
                    throw new Error('请先填写 API Key');
                }
                // 输入框显示掩码（•）时不要作为 override 传过去
                const body = { provider: provider };
                if (keyVal && !keyVal.includes('•')) body.api_key = keyVal;
                if (baseVal) body.api_base = baseVal;

                const res = await fetch('/api/ai/models', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Local-Token': window.getLocalToken ? window.getLocalToken() : ''
                    },
                    body: JSON.stringify(body)
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.status !== 'success') {
                    throw new Error(data.message || ('HTTP ' + res.status));
                }
                const count = Array.isArray(data.models) ? data.models.length : 0;
                return { ok: true, message: `连接成功，可拉取 ${count} 个模型` };
            }

            runTest()
                .then(({ message }) => {
                    if (resultEl) {
                        resultEl.textContent = '✓ ' + message;
                        resultEl.className = 'text-[10px] text-green-600 leading-normal test-result min-h-[1rem]';
                    }
                    showToast(label + ' 连接成功', 'success');
                })
                .catch(err => {
                    const msg = err.message || '请求失败';
                    if (resultEl) {
                        resultEl.textContent = '✗ ' + msg;
                        resultEl.className = 'text-[10px] text-red-500 leading-normal test-result min-h-[1rem]';
                    }
                    showToast(label + ' 连接失败: ' + msg, 'error');
                })
                .finally(() => {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = '测试连接';
                    }
                });
        }
        window.testProviderConnection = testProviderConnection;
        window.onVendorSelectChange = onVendorSelectChange;
        window.syncVendorRowLabels = syncVendorRowLabels;
        window.syncAllVendorRowLabels = syncAllVendorRowLabels;
        window.syncModelProviderOptions = syncModelProviderOptions;
        window.normalizeApiBase = normalizeApiBase;

        // 测试 MinerU API Token 是否可用（不真正解析文件）
        function testMineruConnection() {
            const btn = document.getElementById('btnTestMineru');
            const resultEl = document.getElementById('mineruTestResult');
            if (btn) {
                btn.disabled = true;
                btn.textContent = '测试中...';
            }
            if (resultEl) {
                resultEl.textContent = '正在校验 MinerU Token...';
                resultEl.className = 'text-[10px] text-slate-400 leading-normal';
            }
            fetch('/api/mineru/test')
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        if (resultEl) {
                            resultEl.textContent = '✓ ' + (data.message || '连接成功');
                            resultEl.className = 'text-[10px] text-green-600 leading-normal';
                        }
                        showToast('MinerU 连接成功，可启用云端解析。', 'success');
                    } else {
                        if (resultEl) {
                            resultEl.textContent = '✗ ' + (data.message || '连接失败');
                            resultEl.className = 'text-[10px] text-red-500 leading-normal';
                        }
                        showToast(data.message || 'MinerU 连接失败', 'error');
                    }
                })
                .catch(err => {
                    if (resultEl) {
                        resultEl.textContent = '✗ 请求失败: ' + (err.message || err);
                        resultEl.className = 'text-[10px] text-red-500 leading-normal';
                    }
                })
                .finally(() => {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = '测试连接';
                    }
                });
        }
        window.testMineruConnection = testMineruConnection;

        // Load Categories from Database to Autocomplete Selects
        function loadCategories(options = {}) {
            const reloadCurrentQuestion = options.reloadCurrentQuestion === true;
            const retryCount = Number.isInteger(options.retryCount) ? options.retryCount : 0;
            return fetch('/api/config/metadata')
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`获取元数据状态异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(meta => {
                    systemMetadata = meta;
                    window.systemMetadata = meta; // Global export
                    populateMetadataDropdowns();
                    
                    return fetch('/api/categories');
                })
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`HTTP 状态码异常: ${r.status}`);
                    }
                    return r.json();
                })
                .then(data => {
                    // 统一转换为字典结构：册次→章节→小节→[考点]
                    categoryTree = normalizeCategoryTree(data);
                    window.categoryTree = categoryTree;
                    window.systemMetadata = systemMetadata;
                    populateCategoryDropdowns();
                    
                    // 组卷界面下拉框需要重新渲染
                    if (typeof renderPart2FilterSection === 'function') {
                        const paperSection = document.getElementById('paperWorkspaceSection');
                        if (paperSection && !paperSection.classList.contains('hidden')) {
                            renderPart2FilterSection();
                        }
                    }
                    
                    // Most callers only need fresh dropdown data. Reloading the
                    // editor is an explicit settings/curriculum operation because
                    // it can replace text typed after a save or batch import.
                    if (reloadCurrentQuestion && typeof window.reloadCurrentQuestionSilently === 'function') {
                        window.reloadCurrentQuestionSilently();
                    }
                })
                .catch(err => {
                    console.error('加载分类目录树或元数据配置发生异常:', err);
                    if (retryCount < 3) {
                        console.warn(`[Auto-Retry] 正在尝试第 ${retryCount + 1} 次自适应重新加载数据...`);
                        setTimeout(() => loadCategories({ ...options, retryCount: retryCount + 1 }), 1500);
                    } else {
                        showToast('系统正在连接或初始化后台，加载分类及大纲数据失败，请刷新重试', 'error');
                    }
                });
        }

        window.loadCategories = loadCategories;

        // Populate Metadata Select Option Lists Dynamically
        function populateMetadataDropdowns() {
            if (!systemMetadata || !systemMetadata.question_types || !systemMetadata.difficulties) return;

            // 1. Edit Question Type select
            const editQType = document.getElementById('editQType');
            if (editQType) {
                const currentVal = editQType.value || 'single_choice';
                editQType.innerHTML = '';
                systemMetadata.question_types.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    editQType.appendChild(opt);
                });
                // Restore selected value if matches
                if (systemMetadata.question_types.some(t => t.value === currentVal)) {
                    editQType.value = currentVal;
                } else if (systemMetadata.question_types.length > 0) {
                    editQType.value = systemMetadata.question_types[0].value;
                }
            }

            // 2. Edit Difficulty select
            const editDiff = document.getElementById('editDifficulty');
            if (editDiff) {
                const currentVal = editDiff.value || 'basic';
                editDiff.innerHTML = '';
                systemMetadata.difficulties.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    editDiff.appendChild(opt);
                });
                if (systemMetadata.difficulties.some(d => d.value === currentVal)) {
                    editDiff.value = currentVal;
                } else if (systemMetadata.difficulties.length > 0) {
                    editDiff.value = systemMetadata.difficulties[0].value;
                }
            }

            // 3. Sidebar Filter Question Type select
            const filterQType = document.getElementById('filterQType');
            if (filterQType) {
                const currentVal = filterQType.value || '';
                filterQType.innerHTML = '<option value="">全部题型</option>';
                systemMetadata.question_types.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    filterQType.appendChild(opt);
                });
                filterQType.value = currentVal;
            }

            // 4. Sidebar Filter Difficulty select
            const filterDiff = document.getElementById('filterDifficulty');
            if (filterDiff) {
                const currentVal = filterDiff.value || '';
                filterDiff.innerHTML = '<option value="">全部难度</option>';
                systemMetadata.difficulties.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    filterDiff.appendChild(opt);
                });
                filterDiff.value = currentVal;
            }

            // 5. Editor Applicable Grade select
            const editGrade = document.getElementById('editApplicableGrade');
            if (editGrade) {
                const gradeList = (systemMetadata && Array.isArray(systemMetadata.applicable_grades))
                    ? systemMetadata.applicable_grades
                    : [];
                const currentGrade = editGrade.value || '';
                editGrade.innerHTML = '<option value="">选择适用年级</option>';
                gradeList.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    editGrade.appendChild(opt);
                });
                if (gradeList.some(g => g.value === currentGrade)) {
                    editGrade.value = currentGrade;
                }
            }

            // 5b. Paper import unified grade (left pane) + batch grade (result header)
            const importGradeList = (systemMetadata && Array.isArray(systemMetadata.applicable_grades))
                ? systemMetadata.applicable_grades
                : [];
            [
                { id: 'importApplicableGrade', emptyLabel: '留空（不设置）' },
                { id: 'parsedBatchGrade', emptyLabel: '不设置（每题单独选）' }
            ].forEach(({ id, emptyLabel }) => {
                const el = document.getElementById(id);
                if (!el) return;
                const current = el.value || '';
                el.innerHTML = `<option value="">${emptyLabel}</option>`;
                importGradeList.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    el.appendChild(opt);
                });
                if (importGradeList.some(g => g.value === current)) {
                    el.value = current;
                }
            });

            // 6. Sidebar Filter Applicable Grade select
            const filterGrade = document.getElementById('filterApplicableGrade');
            if (filterGrade) {
                const gradeList = (systemMetadata && Array.isArray(systemMetadata.applicable_grades))
                    ? systemMetadata.applicable_grades
                    : [];
                const currentGrade = filterGrade.value || '';
                filterGrade.innerHTML = '<option value="">所有适用年级</option>';
                gradeList.forEach(item => {
                    const opt = document.createElement('option');
                    opt.value = item.value;
                    opt.textContent = item.label;
                    filterGrade.appendChild(opt);
                });
                filterGrade.value = currentGrade;
            }
        }

        // ---- Visual editors for metadata (applicable grades + topics) ----
        function metadataCurSection() {
            const compSel = document.getElementById('metaTopicCompulsory');
            const chapSel = document.getElementById('metaTopicChapter');
            const knowSel = document.getElementById('metaTopicKnowledge');
            if (!compSel || !chapSel || !knowSel || !systemMetadata) return null;
            const comp = compSel.value;
            const chap = chapSel.value;
            const know = knowSel.value;
            if (!comp || !chap || !know) return null;
            const curriculum = systemMetadata.curriculum || {};
            const chapters = curriculum[comp] || {};
            const sections = chapters[chap] || [];
            const found = sections.find(s => s && s.name === know);
            return found || null;
        }

        function syncMetadataVisualToJson() {
            const el = document.getElementById('settingsMetadataJson');
            if (el && systemMetadata) {
                el.value = JSON.stringify(systemMetadata, null, 2);
            }
        }

        function renderMetadataTopicChips() {
            const box = document.getElementById('metaTopicEditor');
            if (!box) return;
            box.innerHTML = '';
            const section = metadataCurSection();
            if (!section) {
                box.innerHTML = '<span class="text-[10px] text-slate-400">请先在上方选择 册次 → 章节 → 小节，即可管理该小节下的考点。</span>';
                return;
            }
            const topics = Array.isArray(section.topics) ? section.topics : [];
            if (topics.length === 0) {
                box.innerHTML = '<span class="text-[10px] text-slate-400">当前小节暂无考点，可在下方添加。</span>';
                return;
            }
            topics.forEach((t, idx) => {
                const chip = document.createElement('span');
                chip.className = 'inline-flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200/70';
                const label = document.createElement('span');
                label.textContent = window.topicName(t);
                const del = document.createElement('button');
                del.type = 'button';
                del.setAttribute('aria-label', '删除考点 ' + window.topicName(t));
                del.className = 'text-indigo-400 hover:text-red-500 transition-colors';
                del.innerHTML = '<i class="fa-solid fa-xmark text-[9px]"></i>';
                del.onclick = function() { removeMetadataTopic(idx); };
                chip.appendChild(label);
                chip.appendChild(del);
                box.appendChild(chip);
            });
        }

        function renderMetadataTopicSelectors() {
            if (!systemMetadata) return;
            const compSel = document.getElementById('metaTopicCompulsory');
            const chapSel = document.getElementById('metaTopicChapter');
            const knowSel = document.getElementById('metaTopicKnowledge');
            if (!compSel || !chapSel || !knowSel) return;
            const curriculum = systemMetadata.curriculum || {};
            const selectedComp = compSel.value;
            compSel.innerHTML = '<option value="">选择册次</option>';
            Object.keys(curriculum).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                compSel.appendChild(opt);
            });
            if (selectedComp && curriculum[selectedComp]) {
                compSel.value = selectedComp;
            } else {
                chapSel.innerHTML = '<option value="">选择章节</option>';
                chapSel.disabled = true;
                knowSel.innerHTML = '<option value="">选择小节</option>';
                knowSel.disabled = true;
                renderMetadataTopicChips();
                return;
            }
            const chapters = curriculum[selectedComp] || {};
            const selectedChap = chapSel.value;
            chapSel.disabled = false;
            chapSel.innerHTML = '<option value="">选择章节</option>';
            Object.keys(chapters).forEach(ch => {
                const opt = document.createElement('option');
                opt.value = ch;
                opt.textContent = ch;
                chapSel.appendChild(opt);
            });
            if (selectedChap && chapters[selectedChap]) {
                chapSel.value = selectedChap;
            } else {
                knowSel.innerHTML = '<option value="">选择小节</option>';
                knowSel.disabled = true;
                renderMetadataTopicChips();
                return;
            }
            const sections = chapters[selectedChap] || [];
            const selectedKnow = knowSel.value;
            knowSel.disabled = false;
            knowSel.innerHTML = '<option value="">选择小节</option>';
            sections.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                opt.textContent = s.name;
                knowSel.appendChild(opt);
            });
            if (sections.some(s => s.name === selectedKnow)) {
                knowSel.value = selectedKnow;
            }
            renderMetadataTopicChips();
        }

        function bindMetadataTopicSelectors() {
            const compSel = document.getElementById('metaTopicCompulsory');
            const chapSel = document.getElementById('metaTopicChapter');
            const knowSel = document.getElementById('metaTopicKnowledge');
            if (compSel) {
                compSel.onchange = function() {
                    if (chapSel) chapSel.value = '';
                    if (knowSel) knowSel.value = '';
                    renderMetadataTopicSelectors();
                };
            }
            if (chapSel) {
                chapSel.onchange = function() {
                    if (knowSel) knowSel.value = '';
                    renderMetadataTopicSelectors();
                };
            }
        }

        window.renderMetadataVisualEditors = function() {
            if (!systemMetadata) return;
            bindMetadataTopicSelectors();
            // 大纲树已改由双栏编辑器（treeRender / loadBothTrees）渲染到
            // metadataTreeRef + metadataTree；此处不再渲染旧版单栏树，
            // 否则会覆盖右侧「高三专题复习」树。
        };

        window.removeMetadataTopic = function(idx) {
            const section = metadataCurSection();
            if (!section || !Array.isArray(section.topics)) return;
            section.topics.splice(idx, 1);
            renderMetadataTopicChips();
            syncMetadataVisualToJson();
        };

        // ===================== 大纲目录树编辑器 =====================
        window.metadataTreeDraft = null;
        window.metadataTreeOpen = {};

        function metadataTreeKey(comp, ch, secName) {
            if (secName !== undefined && secName !== null) return comp + '\u0001' + ch + '\u0001' + secName;
            if (ch !== undefined && ch !== null) return comp + '\u0001' + ch;
            return comp;
        }
        function metadataTreeIsOpen(k) {
            // 默认全部收起；用户手动展开后显式记录
            if (window.metadataTreeOpen[k] === undefined) return false;
            return window.metadataTreeOpen[k];
        }
        function metadataTreeToggle(k) {
            window.metadataTreeOpen[k] = !metadataTreeIsOpen(k);
            metadataTreeRender();
        }

        function metadataTreeIconBtn(cls, title, fn) {
            const b = document.createElement('button');
            b.type = 'button';
            b.title = title || '';
            b.className = 'w-5 h-5 flex items-center justify-center rounded-md text-[9px] text-slate-400 hover:text-brand-600 hover:bg-brand-50 dark:hover:text-brand-200 dark:hover:bg-slate-700/50 transition-colors cursor-pointer shrink-0';
            b.innerHTML = '<i class="fa-solid ' + cls + '"></i>';
            b.onclick = fn;
            return b;
        }
        function metadataTreeAddBtn(title, fn) {
            const b = document.createElement('button');
            b.type = 'button';
            b.title = title;
            b.className = 'w-5 h-5 flex items-center justify-center rounded-md text-[11px] font-bold text-brand-500 hover:bg-brand-50 dark:hover:bg-slate-700/50 transition-colors cursor-pointer shrink-0';
            b.textContent = '+';
            b.onclick = fn;
            return b;
        }

        function metadataTreeDraftRow(draft, onOk) {
            // 这个助手原本只服务设置页那套已不可达的单栏树；拆卷卡片的考向改名也复用它。
            // 因此不能再无条件调 metadataTreeRender()（调用方可能不在大纲树上下文里）。
            const redraw = function() {
                if (typeof window.metadataTreeRender === 'function') {
                    try { metadataTreeRender(); } catch (err) { /* 非树上下文：忽略 */ }
                }
            };
            const row = document.createElement('div');
            row.className = 'flex items-center gap-1.5 py-1';
            const inp = document.createElement('input');
            inp.type = 'text';
            inp.className = 'glass-input flex-1 px-2 py-1 text-[11px] rounded-lg min-w-0';
            inp.placeholder = '输入名称，回车确认，Esc 取消';
            inp.value = draft.value || '';
            inp.autocomplete = 'off';
            const ok = document.createElement('button');
            ok.type = 'button'; ok.title = '确认';
            ok.className = 'w-6 h-6 flex items-center justify-center rounded-md bg-brand-500/10 text-brand-600 hover:bg-brand-500/20 text-[11px] cursor-pointer shrink-0';
            ok.innerHTML = '<i class="fa-solid fa-check"></i>';
            const cancel = document.createElement('button');
            cancel.type = 'button'; cancel.title = '取消';
            cancel.className = 'w-6 h-6 flex items-center justify-center rounded-md text-slate-400 hover:text-red-500 text-[11px] cursor-pointer shrink-0';
            cancel.innerHTML = '<i class="fa-solid fa-xmark"></i>';
            const commit = function() {
                const v = inp.value.trim();
                window.metadataTreeDraft = null;
                if (v) onOk(v);
                else redraw();
            };
            ok.onclick = commit;
            cancel.onclick = function() { window.metadataTreeDraft = null; redraw(); };
            inp.onkeydown = function(e) {
                if (e.key === 'Enter') { e.preventDefault(); commit(); }
                else if (e.key === 'Escape') { e.preventDefault(); window.metadataTreeDraft = null; redraw(); }
            };
            row.appendChild(inp); row.appendChild(ok); row.appendChild(cancel);
            setTimeout(function() { try { inp.focus(); inp.select(); } catch (err) {} }, 0);
            return row;
        }
        // 拆卷卡片的考向改名要就地展开同一款编辑行（api.js 先加载，import.js 直接用）。
        window.metadataTreeDraftRow = metadataTreeDraftRow;

        function metadataTreeCounts(curriculum) {
            let chapters = 0, topics = 0;
            Object.keys(curriculum || {}).forEach(function(ch) {
                chapters += 1;
                (curriculum[ch] || []).forEach(function(s) {
                    if (s && Array.isArray(s.topics)) topics += s.topics.length;
                });
            });
            return { chapters: chapters, topics: topics };
        }

        function metadataTreeDraftMatches(draft, kind, comp, ch, secIdx, tIdx) {
            if (!draft || draft.kind !== kind) return false;
            if (draft.comp !== comp || draft.ch !== ch) return false;
            if (kind.indexOf('Sec') !== -1 || kind.indexOf('Topic') !== -1) {
                if (draft.secIdx !== secIdx) return false;
            }
            if (kind.indexOf('Topic') !== -1 && draft.tIdx !== tIdx) return false;
            return true;
        }

        // 考点拖动排序状态
        window.metadataTreeDrag = null;

        function metadataTreeClearDropIndicators() {
            var tree = document.getElementById('metadataTree');
            if (!tree) return;
            var rows = tree.querySelectorAll('[data-topic-drag]');
            for (var i = 0; i < rows.length; i++) {
                rows[i].style.borderTop = '';
                rows[i].style.borderBottom = '';
            }
        }

        function metadataTreeMoveTopic(src, dstComp, dstCh, dstSecIdx, dstTIdx, insertAfter) {
            var curriculum = (systemMetadata && systemMetadata.curriculum) || {};
            var srcSec = curriculum[src.comp] && curriculum[src.comp][src.ch] && curriculum[src.comp][src.ch][src.secIdx];
            var dstSec = curriculum[dstComp] && curriculum[dstComp][dstCh] && curriculum[dstComp][dstCh][dstSecIdx];
            if (!srcSec || !dstSec || !Array.isArray(srcSec.topics)) return;
            if (!Array.isArray(dstSec.topics)) dstSec.topics = [];
            var item = srcSec.topics.splice(src.tIdx, 1)[0];
            if (item === undefined) return;
            // 计算目标插入索引（源删除后索引可能偏移）
            var insertIdx = dstTIdx;
            if (src.comp === dstComp && src.ch === dstCh && src.secIdx === dstSecIdx && src.tIdx < dstTIdx) {
                insertIdx = dstTIdx - 1;
            }
            if (insertAfter) insertIdx = insertIdx + 1;
            insertIdx = Math.max(0, Math.min(insertIdx, dstSec.topics.length));
            dstSec.topics.splice(insertIdx, 0, item);
            syncMetadataVisualToJson();
            metadataTreeRender();
        }

        function metadataTreeTopicRow(comp, ch, secIdx, tIdx) {
            const curriculum = (systemMetadata && systemMetadata.curriculum) || {};
            const sec = (curriculum[comp] && curriculum[comp][ch] && curriculum[comp][ch][secIdx]) || null;
            if (!sec) return null;
            const topics = Array.isArray(sec.topics) ? sec.topics : [];
            const t = topics[tIdx];
            if (t === undefined) return null;
            const row = document.createElement('div');
            row.className = 'flex items-center gap-1.5 pl-2 py-0.5 cursor-move';
            row.setAttribute('draggable', 'true');
            row.setAttribute('data-topic-drag', comp + '||' + ch + '||' + secIdx + '||' + tIdx);
            row.addEventListener('dragstart', function(ev) {
                window.metadataTreeDrag = { comp: comp, ch: ch, secIdx: secIdx, tIdx: tIdx };
                row.style.opacity = '0.4';
                ev.dataTransfer.effectAllowed = 'move';
                try { ev.dataTransfer.setData('text/plain', 'topic'); } catch(e) {}
            });
            row.addEventListener('dragend', function() {
                row.style.opacity = '';
                window.metadataTreeDrag = null;
                metadataTreeClearDropIndicators();
            });
            row.addEventListener('dragover', function(ev) {
                if (!window.metadataTreeDrag) return;
                ev.preventDefault();
                ev.dataTransfer.dropEffect = 'move';
                metadataTreeClearDropIndicators();
                var rect = row.getBoundingClientRect();
                var isAfter = (ev.clientY - rect.top) > (rect.height / 2);
                if (isAfter) row.style.borderBottom = '2px solid rgb(124, 58, 237)';
                else row.style.borderTop = '2px solid rgb(124, 58, 237)';
            });
            row.addEventListener('dragleave', function() {
                row.style.borderTop = '';
                row.style.borderBottom = '';
            });
            row.addEventListener('drop', function(ev) {
                ev.preventDefault();
                ev.stopPropagation();
                var src = window.metadataTreeDrag;
                if (!src) return;
                var rect = row.getBoundingClientRect();
                var isAfter = (ev.clientY - rect.top) > (rect.height / 2);
                metadataTreeMoveTopic(src, comp, ch, secIdx, tIdx, isAfter);
                window.metadataTreeDrag = null;
                metadataTreeClearDropIndicators();
            });
            const dot = document.createElement('span');
            dot.className = 'w-1 h-1 rounded-full bg-brand-500 shrink-0';
            dot.setAttribute('draggable', 'false');
            row.appendChild(dot);
            if (metadataTreeDraftMatches(window.metadataTreeDraft, 'renameTopic', comp, ch, secIdx, tIdx)) {
                const wrap = document.createElement('div');
                wrap.className = 'flex-1 min-w-0';
                wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                    const old = topics[tIdx];
                    if (old && typeof old === 'object' && old.id) {
                        topics[tIdx] = { id: old.id, name: v };
                    } else {
                        topics[tIdx] = v;
                    }
                    syncMetadataVisualToJson();
                    metadataTreeRender();
                }));
                row.appendChild(wrap);
                return row;
            }
            const nameSpan = document.createElement('span');
            nameSpan.className = 'text-[11px] text-slate-600 dark:text-slate-300 flex-1 min-w-0 truncate';
            nameSpan.textContent = window.topicName(t);
            nameSpan.title = window.topicName(t);
            row.appendChild(nameSpan);
            const btns = document.createElement('span');
            btns.className = 'flex items-center gap-0.5 shrink-0 opacity-60 hover:opacity-100';
            btns.appendChild(metadataTreeIconBtn('fa-pen', '重命名考点', function() {
                window.metadataTreeDraft = { kind: 'renameTopic', comp: comp, ch: ch, secIdx: secIdx, tIdx: tIdx, value: window.topicName(t) };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-trash', '删除考点', function() { metadataTreeDeleteTopic(comp, ch, secIdx, tIdx); }));
            row.appendChild(btns);
            return row;
        }

        function metadataTreeSectionRow(comp, ch, secIdx) {
            const curriculum = (systemMetadata && systemMetadata.curriculum) || {};
            const sec = (curriculum[comp] && curriculum[comp][ch] && curriculum[comp][ch][secIdx]) || null;
            if (!sec) return null;
            const secName = sec.name || ('小节 ' + (secIdx + 1));
            const key = metadataTreeKey(comp, ch, secName);
            const open = metadataTreeIsOpen(key);
            const topics = Array.isArray(sec.topics) ? sec.topics : [];
            const box = document.createElement('div');
            box.className = 'rounded-lg border border-slate-200/60 bg-white/70 dark:bg-slate-800/50 overflow-hidden';
            const head = document.createElement('div');
            head.className = 'flex items-center gap-1.5 px-2 py-1.5';
            head.appendChild(metadataTreeIconBtn(open ? 'fa-chevron-down' : 'fa-chevron-right', open ? '收起' : '展开', function() { metadataTreeToggle(key); }));
            head.appendChild(metadataTreeIconBtn('fa-layer-group', '', function() {}));
            const nameSpan = document.createElement('span');
            nameSpan.className = 'text-[11px] font-semibold text-slate-700 dark:text-slate-200 min-w-0 truncate';
            nameSpan.textContent = secName;
            nameSpan.title = secName;
            head.appendChild(nameSpan);
            const cnt = document.createElement('span');
            cnt.className = 'text-[9px] text-slate-400 shrink-0';
            cnt.textContent = topics.length > 0 ? '(' + topics.length + ' 考点)' : '(无考点)';
            head.appendChild(cnt);
            const btns = document.createElement('span');
            btns.className = 'ml-auto flex items-center gap-0.5 shrink-0 opacity-60 hover:opacity-100';
            btns.appendChild(metadataTreeAddBtn('新增考点', function() {
                window.metadataTreeDraft = { kind: 'addTopic', comp: comp, ch: ch, secIdx: secIdx };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-pen', '重命名小节', function() {
                window.metadataTreeDraft = { kind: 'renameSec', comp: comp, ch: ch, secIdx: secIdx, value: secName };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-trash', '删除小节', function() { metadataTreeDeleteSec(comp, ch, secIdx); }));
                        head.classList.add('cursor-pointer');
            head.addEventListener('click', function(ev) {
                if (ev.target && ev.target.closest && ev.target.closest('button')) return;
                metadataTreeToggle(key);
            });
head.appendChild(btns);
            box.appendChild(head);
            const bodyOpen = open || metadataTreeDraftMatches(window.metadataTreeDraft, 'addTopic', comp, ch, secIdx) || metadataTreeDraftMatches(window.metadataTreeDraft, 'renameSec', comp, ch, secIdx);
            if (bodyOpen) {
                const body = document.createElement('div');
                body.className = 'pl-6 pr-2 pb-2 space-y-1';
                if (metadataTreeDraftMatches(window.metadataTreeDraft, 'renameSec', comp, ch, secIdx)) {
                    const wrap = document.createElement('div');
                    wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                        const s = systemMetadata.curriculum[comp][ch][secIdx];
                        s.name = v;
                        syncMetadataVisualToJson();
                        metadataTreeRender();
                    }));
                    body.appendChild(wrap);
                }
                if (metadataTreeDraftMatches(window.metadataTreeDraft, 'addTopic', comp, ch, secIdx)) {
                    const wrap = document.createElement('div');
                    wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                        const s = systemMetadata.curriculum[comp][ch][secIdx];
                        if (!Array.isArray(s.topics)) s.topics = [];
                        if (!s.topics.some(function(t) { return window.topicName(t) === v; })) {
                            s.topics.push(v);
                        }
                        syncMetadataVisualToJson();
                        metadataTreeRender();
                    }));
                    body.appendChild(wrap);
                }
                if (topics.length === 0) {
                    const empty = document.createElement('div');
                    empty.className = 'text-[9px] text-slate-400 pl-2';
                    empty.textContent = '（暂无考点）';
                    body.appendChild(empty);
                }
                topics.forEach(function(t, tIdx) {
                    const row = metadataTreeTopicRow(comp, ch, secIdx, tIdx);
                    if (row) body.appendChild(row);
                });
                box.appendChild(body);
            }
            return box;
        }

        function metadataTreeChapterRow(comp, ch) {
            const curriculum = (systemMetadata && systemMetadata.curriculum) || {};
            const sections = (curriculum[comp] && curriculum[comp][ch]) || [];
            const key = metadataTreeKey(comp, ch);
            const open = metadataTreeIsOpen(key);
            const topicCount = sections.reduce(function(acc, s) { return acc + (Array.isArray(s.topics) ? s.topics.length : 0); }, 0);
            const box = document.createElement('div');
            box.className = 'rounded-xl border border-slate-200/60 bg-white/50 dark:bg-slate-800/30 overflow-hidden';
            const head = document.createElement('div');
            head.className = 'flex items-center gap-1.5 px-2.5 py-1.5';
            head.appendChild(metadataTreeIconBtn(open ? 'fa-chevron-down' : 'fa-chevron-right', open ? '收起' : '展开', function() { metadataTreeToggle(key); }));
            head.appendChild(metadataTreeIconBtn('fa-folder-open', '', function() {}));
            const nameSpan = document.createElement('span');
            nameSpan.className = 'text-[11px] font-semibold text-slate-700 dark:text-slate-200 min-w-0 truncate';
            nameSpan.textContent = ch;
            nameSpan.title = ch;
            head.appendChild(nameSpan);
            const cnt = document.createElement('span');
            cnt.className = 'text-[9px] text-slate-400 shrink-0';
            cnt.textContent = '(' + sections.length + ' 节 · ' + topicCount + ' 考点)';
            head.appendChild(cnt);
            const btns = document.createElement('span');
            btns.className = 'ml-auto flex items-center gap-0.5 shrink-0 opacity-60 hover:opacity-100';
            btns.appendChild(metadataTreeAddBtn('新增小节', function() {
                window.metadataTreeDraft = { kind: 'addSec', comp: comp, ch: ch };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-pen', '重命名章节', function() {
                window.metadataTreeDraft = { kind: 'renameChap', comp: comp, ch: ch, value: ch };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-trash', '删除章节', function() { metadataTreeDeleteChap(comp, ch); }));
                        head.classList.add('cursor-pointer');
            head.addEventListener('click', function(ev) {
                if (ev.target && ev.target.closest && ev.target.closest('button')) return;
                metadataTreeToggle(key);
            });
head.appendChild(btns);
            box.appendChild(head);
            const bodyOpen = open || metadataTreeDraftMatches(window.metadataTreeDraft, 'addSec', comp, ch) || metadataTreeDraftMatches(window.metadataTreeDraft, 'renameChap', comp, ch);
            if (bodyOpen) {
                const body = document.createElement('div');
                body.className = 'pl-4 pr-2 pb-2 space-y-1.5';
                if (metadataTreeDraftMatches(window.metadataTreeDraft, 'renameChap', comp, ch)) {
                    const wrap = document.createElement('div');
                    wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                        const cur = systemMetadata.curriculum;
                        if (cur[comp][v] !== undefined && v !== ch) {
                            showToast('已存在同名章节', 'warning');
                            metadataTreeRender();
                            return;
                        }
                        cur[comp][v] = cur[comp][ch];
                        delete cur[comp][ch];
                        syncMetadataVisualToJson();
                        metadataTreeRender();
                    }));
                    body.appendChild(wrap);
                }
                if (metadataTreeDraftMatches(window.metadataTreeDraft, 'addSec', comp, ch)) {
                    const wrap = document.createElement('div');
                    wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                        const arr = systemMetadata.curriculum[comp][ch];
                        if (!Array.isArray(arr)) return;
                        if (arr.some(function(s) { return s.name === v; })) {
                            showToast('已存在同名小节', 'warning');
                            metadataTreeRender();
                            return;
                        }
                        arr.push({ name: v, topics: [] });
                        syncMetadataVisualToJson();
                        metadataTreeRender();
                    }));
                    body.appendChild(wrap);
                }
                sections.forEach(function(s, secIdx) {
                    const row = metadataTreeSectionRow(comp, ch, secIdx);
                    if (row) body.appendChild(row);
                });
                box.appendChild(body);
            }
            return box;
        }

        function metadataTreeCompRow(comp) {
            const curriculum = (systemMetadata && systemMetadata.curriculum) || {};
            const chapters = curriculum[comp] || {};
            const key = comp;
            const open = metadataTreeIsOpen(key);
            const counts = metadataTreeCounts(chapters);
            const box = document.createElement('div');
            box.className = 'rounded-xl border border-brand-200/70 bg-brand-50/40 dark:bg-slate-800/40 overflow-hidden';
            const head = document.createElement('div');
            head.className = 'flex items-center gap-1.5 px-2.5 py-2';
            head.appendChild(metadataTreeIconBtn(open ? 'fa-chevron-down' : 'fa-chevron-right', open ? '收起' : '展开', function() { metadataTreeToggle(key); }));
            head.appendChild(metadataTreeIconBtn('fa-book', '', function() {}));
            const nameSpan = document.createElement('span');
            nameSpan.className = 'text-xs font-bold text-slate-800 dark:text-slate-100 min-w-0 truncate';
            nameSpan.textContent = comp;
            nameSpan.title = comp;
            head.appendChild(nameSpan);
            const cnt = document.createElement('span');
            cnt.className = 'text-[9px] text-slate-400 shrink-0';
            cnt.textContent = '(' + counts.chapters + ' 章 · ' + counts.topics + ' 考点)';
            head.appendChild(cnt);
            const btns = document.createElement('span');
            btns.className = 'ml-auto flex items-center gap-0.5 shrink-0 opacity-60 hover:opacity-100';
            btns.appendChild(metadataTreeAddBtn('新增章节', function() {
                window.metadataTreeDraft = { kind: 'addChap', comp: comp };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-pen', '重命名册次', function() {
                window.metadataTreeDraft = { kind: 'renameComp', comp: comp, value: comp };
                metadataTreeRender();
            }));
            btns.appendChild(metadataTreeIconBtn('fa-trash', '删除册次', function() { metadataTreeDeleteComp(comp); }));
                        head.classList.add('cursor-pointer');
            head.addEventListener('click', function(ev) {
                if (ev.target && ev.target.closest && ev.target.closest('button')) return;
                metadataTreeToggle(key);
            });
head.appendChild(btns);
            box.appendChild(head);
            const bodyOpen = open || metadataTreeDraftMatches(window.metadataTreeDraft, 'addChap', comp) || metadataTreeDraftMatches(window.metadataTreeDraft, 'renameComp', comp);
            if (bodyOpen) {
                const body = document.createElement('div');
                body.className = 'pl-4 pr-2 pb-2 space-y-1.5';
                if (metadataTreeDraftMatches(window.metadataTreeDraft, 'renameComp', comp)) {
                    const wrap = document.createElement('div');
                    wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                        const cur = systemMetadata.curriculum;
                        if (cur[v] !== undefined && v !== comp) {
                            showToast('已存在同名册次', 'warning');
                            metadataTreeRender();
                            return;
                        }
                        cur[v] = cur[comp];
                        delete cur[comp];
                        syncMetadataVisualToJson();
                        metadataTreeRender();
                    }));
                    body.appendChild(wrap);
                }
                if (metadataTreeDraftMatches(window.metadataTreeDraft, 'addChap', comp)) {
                    const wrap = document.createElement('div');
                    wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                        const cur = systemMetadata.curriculum;
                        if (cur[comp][v] !== undefined) {
                            showToast('已存在同名章节', 'warning');
                            metadataTreeRender();
                            return;
                        }
                        cur[comp][v] = [];
                        syncMetadataVisualToJson();
                        metadataTreeRender();
                    }));
                    body.appendChild(wrap);
                }
                Object.keys(chapters).forEach(function(ch) {
                    const row = metadataTreeChapterRow(comp, ch);
                    if (row) body.appendChild(row);
                });
                box.appendChild(body);
            }
            return box;
        }

        // 通用双树编辑器：左A大纲（高一高二同步），右G大纲（高三专题）
        var treeEditors = {
            left:  { curriculum: null, open: {}, drag: null, container: 'metadataTreeRef', version: 'A', label: '高一高二同步' },
            right: { curriculum: null, open: {}, drag: null, container: 'metadataTree',    version: 'G', label: '高三专题复习' }
        };

        function treeIconBtn(icon, title, onclick, cls) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = (cls || 'text-slate-400 hover:text-brand-600') + ' p-1.5 rounded transition-colors';
            b.title = title;
            b.innerHTML = '<i class="fa-solid ' + icon + ' text-[11px]"></i>';
            b.onclick = function(e) { e.stopPropagation(); onclick(); };
            return b;
        }

        // ---- 拖拽排序：册次 / 章节 / 小节 / 考点 均可拖动重排 ----

        function treeReorderObject(obj, order) {
            var out = {};
            order.forEach(function(k) {
                if (Object.prototype.hasOwnProperty.call(obj, k)) out[k] = obj[k];
            });
            Object.keys(obj).forEach(function(k) { if (!(k in out)) out[k] = obj[k]; });
            return out;
        }

        function treeDropBox(side) {
            return document.getElementById(treeEditors[side].container);
        }

        function treeClearDropMarks(side) {
            var box = treeDropBox(side);
            if (!box) return;
            var rows = box.querySelectorAll('[data-drop-row]');
            for (var i = 0; i < rows.length; i++) rows[i].style.boxShadow = '';
        }

        function treeDropMark(el, after) {
            el.style.boxShadow = after
                ? 'inset 0 -2px 0 0 rgb(124, 58, 237)'
                : 'inset 0 2px 0 0 rgb(124, 58, 237)';
        }

        function treeBindDrop(el, side, accept, onDrop) {
            el.setAttribute('data-drop-row', '1');
            el.addEventListener('dragover', function(ev) {
                var d = treeEditors[side].drag;
                if (!d || accept.indexOf(d.kind) === -1) return;
                ev.preventDefault();
                ev.dataTransfer.dropEffect = 'move';
                var rect = el.getBoundingClientRect();
                var after = (ev.clientY - rect.top) > (rect.height / 2);
                treeClearDropMarks(side);
                treeDropMark(el, after);
            });
            el.addEventListener('dragleave', function() { el.style.boxShadow = ''; });
            el.addEventListener('drop', function(ev) {
                var d = treeEditors[side].drag;
                if (!d || accept.indexOf(d.kind) === -1) return;
                ev.preventDefault();
                ev.stopPropagation();
                var rect = el.getBoundingClientRect();
                var after = (ev.clientY - rect.top) > (rect.height / 2);
                treeEditors[side].drag = null;
                treeClearDropMarks(side);
                onDrop(d, after);
            });
        }

        function treeGrip(side, payload) {
            var g = document.createElement('span');
            g.className = 'text-[8px] text-slate-300 hover:text-slate-500 cursor-grab shrink-0 select-none';
            g.innerHTML = '<i class="fa-solid fa-grip-vertical"></i>';
            g.title = '拖动排序';
            g.setAttribute('draggable', 'true');
            g.addEventListener('dragstart', function(ev) {
                treeEditors[side].drag = payload;
                ev.dataTransfer.effectAllowed = 'move';
                try { ev.dataTransfer.setData('text/plain', payload.kind); } catch(e) {}
                ev.stopPropagation();
            });
            g.addEventListener('dragend', function() {
                treeEditors[side].drag = null;
                treeClearDropMarks(side);
            });
            return g;
        }

        function treeMoveComp(side, srcComp, dstComp, after) {
            var ed = treeEditors[side];
            var keys = Object.keys(ed.curriculum);
            var si = keys.indexOf(srcComp), di = keys.indexOf(dstComp);
            if (si < 0 || di < 0) return;
            keys.splice(si, 1);
            di = keys.indexOf(dstComp);
            keys.splice(after ? di + 1 : di, 0, srcComp);
            ed.curriculum = treeReorderObject(ed.curriculum, keys);
            treeRender(side);
        }

        function treeMoveChap(side, srcComp, srcCh, dstComp, dstCh, after) {
            var ed = treeEditors[side];
            var srcChapters = ed.curriculum[srcComp] || {};
            var dstChapters = ed.curriculum[dstComp] || {};
            if (!srcChapters[srcCh]) return;
            if (srcComp === dstComp) {
                var keys = Object.keys(srcChapters);
                var si = keys.indexOf(srcCh), di = keys.indexOf(dstCh);
                if (si < 0 || di < 0) return;
                keys.splice(si, 1);
                di = keys.indexOf(dstCh);
                keys.splice(after ? di + 1 : di, 0, srcCh);
                ed.curriculum[srcComp] = treeReorderObject(srcChapters, keys);
                treeRender(side);
                return;
            }
            var item = srcChapters[srcCh];
            delete srcChapters[srcCh];
            var dkeys = Object.keys(dstChapters);
            var di2 = dkeys.indexOf(dstCh);
            var out = {};
            dkeys.forEach(function(k) {
                if (!after && k === dstCh) out[srcCh] = item;
                out[k] = dstChapters[k];
                if (after && k === dstCh) out[srcCh] = item;
            });
            if (!(srcCh in out) && di2 < 0) out[srcCh] = item;
            ed.curriculum[dstComp] = out;
            treeRender(side);
        }

        function treeMoveSec(side, srcComp, srcCh, srcSi, dstComp, dstCh, dstSi, after) {
            var ed = treeEditors[side];
            var srcSecs = (ed.curriculum[srcComp] || {})[srcCh] || [];
            var dstSecs = (ed.curriculum[dstComp] || {})[dstCh] || [];
            var item = srcSecs[srcSi];
            if (item === undefined) return;
            srcSecs.splice(srcSi, 1);
            var idx = dstSi;
            if (srcComp === dstComp && srcCh === dstCh && srcSi < dstSi) idx = dstSi - 1;
            if (after) idx = idx + 1;
            idx = Math.max(0, Math.min(idx, dstSecs.length));
            dstSecs.splice(idx, 0, item);
            treeRender(side);
        }

        function treeMoveTopic(side, srcComp, srcCh, srcSi, srcTi, dstComp, dstCh, dstSi, dstTi, after) {
            var ed = treeEditors[side];
            var srcSec = ((ed.curriculum[srcComp] || {})[srcCh] || [])[srcSi];
            var dstSec = ((ed.curriculum[dstComp] || {})[dstCh] || [])[dstSi];
            if (!srcSec || !dstSec) return;
            if (!Array.isArray(srcSec.topics)) srcSec.topics = [];
            if (!Array.isArray(dstSec.topics)) dstSec.topics = [];
            var item = srcSec.topics[srcTi];
            if (item === undefined) return;
            srcSec.topics.splice(srcTi, 1);
            var idx = dstTi;
            if (srcComp === dstComp && srcCh === dstCh && srcSi === dstSi && srcTi < dstTi) idx = dstTi - 1;
            if (after) idx = idx + 1;
            idx = Math.max(0, Math.min(idx, dstSec.topics.length));
            dstSec.topics.splice(idx, 0, item);
            treeRender(side);
        }

        function treeRender(side) {
            var ed = treeEditors[side];
            var box = document.getElementById(ed.container);
            if (!box) return;
            box.innerHTML = '';
            if (!ed.curriculum || Object.keys(ed.curriculum).length === 0) {
                box.innerHTML = '<div class="text-[10px] text-slate-400 p-2">（加载中...）</div>';
                return;
            }
            Object.keys(ed.curriculum).forEach(function(comp) {
                box.appendChild(treeCompRow(side, comp));
            });
        }

        function treeCountTopics(chapters) {
            var c = 0;
            Object.keys(chapters).forEach(function(ch) {
                (chapters[ch] || []).forEach(function(sec) {
                    if (sec && sec.topics) c += sec.topics.length;
                });
            });
            return c;
        }

        function treeCompRow(side, comp) {
            var ed = treeEditors[side];
            var chapters = ed.curriculum[comp] || {};
            var chapKeys = Object.keys(chapters);
            var topicCount = treeCountTopics(chapters);
            var row = document.createElement('div');
            row.className = 'select-none';
            var head = document.createElement('div');
            head.className = 'flex items-center gap-1 py-1.5 px-2 rounded-lg bg-brand-50/50 hover:bg-brand-50 cursor-pointer';
            head.appendChild(treeGrip(side, { kind: 'comp', comp: comp }));
            var arrow = document.createElement('span');
            arrow.className = 'text-[8px] text-slate-400 w-3 shrink-0';
            arrow.textContent = ed.open[comp] === true ? '\u25bc' : '\u25b6';
            head.appendChild(arrow);
            var name = document.createElement('span');
            name.className = 'font-bold text-slate-700 text-[13px]';
            name.textContent = comp;
            head.appendChild(name);
            var cnt = document.createElement('span');
            cnt.className = 'text-[10px] text-slate-400 ml-auto shrink-0';
            cnt.textContent = chapKeys.length + ' 章 · ' + topicCount + ' 考点';
            head.appendChild(cnt);
            var btns = document.createElement('span');
            btns.className = 'flex items-center gap-0.5 opacity-60 hover:opacity-100 shrink-0';
            btns.appendChild(treeIconBtn('fa-plus', '新增章节', function() {
                var v = prompt('新增章节名称（' + comp + '）');
                if (v && v.trim() && !chapters[v.trim()]) { chapters[v.trim()] = []; treeRender(side); }
            }));
            btns.appendChild(treeIconBtn('fa-pen', '重命名册次', function() {
                var v = prompt('重命名册次', comp);
                if (v && v.trim() && v.trim() !== comp && !ed.curriculum[v.trim()]) {
                    ed.curriculum[v.trim()] = ed.curriculum[comp];
                    delete ed.curriculum[comp];
                    ed.open[v.trim()] = ed.open[comp]; delete ed.open[comp];
                    treeRender(side);
                }
            }));
            btns.appendChild(treeIconBtn('fa-trash', '删除册次', function() {
                if (confirm('删除册次「' + comp + '」及其全部内容？')) {
                    delete ed.curriculum[comp]; delete ed.open[comp]; treeRender(side);
                }
            }, 'text-slate-400 hover:text-red-500'));
            head.appendChild(btns);
            head.onclick = function() { ed.open[comp] = !(ed.open[comp] === true); treeRender(side); };
            treeBindDrop(head, side, ['comp'], function(d, after) { treeMoveComp(side, d.comp, comp, after); });
            row.appendChild(head);
            if (ed.open[comp] === true) {
                var body = document.createElement('div');
                body.className = 'pl-3 pr-1 pb-1.5 space-y-1';
                chapKeys.forEach(function(ch) { body.appendChild(treeChapRow(side, comp, ch)); });
                row.appendChild(body);
            }
            return row;
        }

        function treeChapRow(side, comp, ch) {
            var ed = treeEditors[side];
            var chapters = ed.curriculum[comp] || {};
            var secs = chapters[ch] || [];
            var st = treeCountTopics((function() { var o = {}; o[ch] = secs; return o; })());
            var key = comp + '||' + ch;
            var row = document.createElement('div');
            row.className = 'select-none';
            var head = document.createElement('div');
            head.className = 'flex items-center gap-1 py-1.5 px-2 rounded hover:bg-slate-50 cursor-pointer';
            head.appendChild(treeGrip(side, { kind: 'chap', comp: comp, ch: ch }));
            var arrow = document.createElement('span');
            arrow.className = 'text-[7px] text-slate-400 w-3 shrink-0';
            arrow.textContent = ed.open[key] === true ? '\u25bc' : '\u25b6';
            head.appendChild(arrow);
            var name = document.createElement('span');
            name.className = 'text-slate-600 font-medium text-[12px]';
            name.textContent = ch;
            head.appendChild(name);
            var cnt = document.createElement('span');
            cnt.className = 'text-[10px] text-slate-400 ml-auto shrink-0';
            cnt.textContent = secs.length + ' 节 · ' + st + ' 考点';
            head.appendChild(cnt);
            var btns = document.createElement('span');
            btns.className = 'flex items-center gap-0.5 opacity-60 hover:opacity-100 shrink-0';
            btns.appendChild(treeIconBtn('fa-plus', '新增小节', function() {
                var v = prompt('新增小节名称（' + ch + '）');
                if (v && v.trim()) { secs.push({name: v.trim(), topics: []}); treeRender(side); }
            }));
            btns.appendChild(treeIconBtn('fa-pen', '重命名章节', function() {
                var v = prompt('重命名章节', ch);
                if (v && v.trim() && v.trim() !== ch && !chapters[v.trim()]) {
                    chapters[v.trim()] = chapters[ch]; delete chapters[ch]; treeRender(side);
                }
            }));
            btns.appendChild(treeIconBtn('fa-trash', '删除章节', function() {
                if (confirm('删除章节「' + ch + '」？')) { delete chapters[ch]; treeRender(side); }
            }, 'text-slate-400 hover:text-red-500'));
            head.appendChild(btns);
            head.onclick = function() { ed.open[key] = !(ed.open[key] === true); treeRender(side); };
            treeBindDrop(head, side, ['chap'], function(d, after) { treeMoveChap(side, d.comp, d.ch, comp, ch, after); });
            row.appendChild(head);
            if (ed.open[key] === true) {
                var body = document.createElement('div');
                body.className = 'pl-3 space-y-0.5';
                secs.forEach(function(sec, si) { if (sec) body.appendChild(treeSecRow(side, comp, ch, si)); });
                row.appendChild(body);
            }
            return row;
        }

        function treeSecRow(side, comp, ch, si) {
            var ed = treeEditors[side];
            var secs = ed.curriculum[comp][ch];
            var sec = secs[si] || {};
            var topics = sec.topics || [];
            var key = comp + '||' + ch + '||' + si;
            var row = document.createElement('div');
            row.className = 'select-none';
            var head = document.createElement('div');
            head.className = 'flex items-center gap-1 py-1.5 px-2 rounded hover:bg-slate-50 cursor-pointer';
            head.appendChild(treeGrip(side, { kind: 'sec', comp: comp, ch: ch, si: si }));
            var arrow = document.createElement('span');
            arrow.className = 'text-[7px] text-slate-400 w-3 shrink-0';
            arrow.textContent = ed.open[key] === true ? '\u25bc' : '\u25b6';
            head.appendChild(arrow);
            var name = document.createElement('span');
            name.className = 'text-slate-500 text-[12px]';
            name.textContent = sec.name || '';
            head.appendChild(name);
            var cnt = document.createElement('span');
            cnt.className = 'text-[10px] text-slate-400 ml-auto shrink-0';
            cnt.textContent = topics.length + ' 考点';
            head.appendChild(cnt);
            var btns = document.createElement('span');
            btns.className = 'flex items-center gap-0.5 opacity-60 hover:opacity-100 shrink-0';
            btns.appendChild(treeIconBtn('fa-plus', '新增考点', function() {
                var v = prompt('新增考点名称（' + (sec.name || '') + '）');
                if (v && v.trim()) { topics.push({id: '', name: v.trim()}); treeRender(side); }
            }));
            btns.appendChild(treeIconBtn('fa-pen', '重命名小节', function() {
                var v = prompt('重命名小节', sec.name || '');
                if (v && v.trim()) { sec.name = v.trim(); treeRender(side); }
            }));
            btns.appendChild(treeIconBtn('fa-trash', '删除小节', function() {
                if (confirm('删除小节「' + (sec.name || '') + '」？')) { secs.splice(si, 1); treeRender(side); }
            }, 'text-slate-400 hover:text-red-500'));
            head.appendChild(btns);
            head.onclick = function() { ed.open[key] = !(ed.open[key] === true); treeRender(side); };
            treeBindDrop(head, side, ['sec'], function(d, after) { treeMoveSec(side, d.comp, d.ch, d.si, comp, ch, si, after); });
            row.appendChild(head);
            if (ed.open[key] === true) {
                var body = document.createElement('div');
                body.className = 'pl-4 space-y-0.5';
                topics.forEach(function(t, ti) {
                    var tn = typeof t === 'string' ? t : (t.name || '');
                    var lv = typeof t === 'string' ? '' : (t.level || '');
                    var tr = document.createElement('div');
                    tr.className = 'flex items-center gap-1 py-1 px-2 rounded hover:bg-slate-50 cursor-move';
                    tr.setAttribute('draggable', 'true');
                    tr.addEventListener('dragstart', function(ev) {
                        treeEditors[side].drag = { kind: 'topic', comp: comp, ch: ch, si: si, ti: ti };
                        ev.dataTransfer.effectAllowed = 'move';
                        try { ev.dataTransfer.setData('text/plain', 'topic'); } catch(e) {}
                        tr.style.opacity = '0.4';
                    });
                    tr.addEventListener('dragend', function() {
                        tr.style.opacity = '';
                        treeEditors[side].drag = null;
                        treeClearDropMarks(side);
                    });
                    var dot = document.createElement('span');
                    dot.className = 'text-[6px] text-emerald-400 shrink-0';
                    dot.textContent = '\u25cf';
                    tr.appendChild(dot);
                    // level标签，点击循环切换 L1→L2→L3→L4→空→L1
                    var levelBtn = document.createElement('button');
                    levelBtn.type = 'button';
                    var levelColors = {'L1':'bg-emerald-100 text-emerald-700','L2':'bg-blue-100 text-blue-700','L3':'bg-amber-100 text-amber-700','L4':'bg-red-100 text-red-700','L5':'bg-purple-100 text-purple-700'};
                    levelBtn.className = 'text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0 ' + (levelColors[lv] || 'bg-slate-100 text-slate-400');
                    levelBtn.textContent = lv || 'L?';
                    levelBtn.title = '点击切换难度层次（当前：' + (lv || '未设置') + '）';
                    levelBtn.onclick = function(e) {
                        e.stopPropagation();
                        var order = ['', 'L1', 'L2', 'L3', 'L4'];
                        var idx = order.indexOf(lv);
                        var next = order[(idx + 1) % order.length];
                        if (typeof topics[ti] === 'string') topics[ti] = {id: '', name: tn, level: next};
                        else topics[ti].level = next;
                        treeRender(side);
                    };
                    tr.appendChild(levelBtn);
                    var label = document.createElement('span');
                    label.className = 'text-[12px] text-slate-500 flex-1';
                    var noTxt = (topics[ti] && typeof topics[ti] === 'object' && topics[ti].no) ? ('考向' + topics[ti].no + '：') : '';
                    var exTxt = (topics[ti] && typeof topics[ti] === 'object' && topics[ti].example) ? topics[ti].example : '';
                    label.textContent = noTxt + tn;
                    if (exTxt) label.title = '例：' + exTxt;
                    tr.appendChild(label);
                    var tbtns = document.createElement('span');
                    tbtns.className = 'flex items-center gap-0.5 opacity-60 hover:opacity-100 shrink-0';
                    tbtns.appendChild(treeIconBtn('fa-pen', '重命名考点', function() {
                        var v = prompt('重命名考点', tn);
                        if (v && v.trim()) {
                            if (typeof topics[ti] === 'string') topics[ti] = {id: '', name: v.trim(), level: lv};
                            else topics[ti].name = v.trim();
                            treeRender(side);
                        }
                    }));
                    tbtns.appendChild(treeIconBtn('fa-trash', '删除考点', function() {
                        topics.splice(ti, 1); treeRender(side);
                    }, 'text-slate-400 hover:text-red-500'));
                    tr.appendChild(tbtns);
                    treeBindDrop(tr, side, ['topic'], function(d, after) {
                        treeMoveTopic(side, d.comp, d.ch, d.si, d.ti, comp, ch, si, ti, after);
                    });
                    body.appendChild(tr);
                });
                row.appendChild(body);
            }
            return row;
        }

        window.treeAddComp = function(side) {
            var ed = treeEditors[side];
            if (!ed.curriculum) return;
            var v = prompt('新增册次名称');
            if (v && v.trim() && !ed.curriculum[v.trim()]) {
                ed.curriculum[v.trim()] = {}; treeRender(side);
            }
        };

        window.treeSave = function(side) {
            var ed = treeEditors[side];
            if (!ed.curriculum) return;
            var token = window.__localToken || '';
            if (!token) { try { token = localStorage.getItem('local_token') || ''; } catch(e) {} }
            fetch('/api/config/curriculum-presets/' + ed.version, {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-Local-Token': token},
                body: JSON.stringify({curriculum: ed.curriculum})
            }).then(function(r) { return r.json(); })
              .then(function(data) {
                  if (data && data.ok) showToast(ed.label + '大纲已保存', 'success');
                  else showToast('保存失败', 'error');
              }).catch(function() { showToast('保存失败', 'error'); });
        };

        function loadBothTrees() {
            ['left', 'right'].forEach(function(side) {
                var ed = treeEditors[side];
                fetch('/api/config/curriculum-presets/' + ed.version)
                    .then(function(r) { return r.json(); })
                    .then(function(data) {
                        if (data && data.metadata && data.metadata.curriculum) {
                            ed.curriculum = JSON.parse(JSON.stringify(data.metadata.curriculum));
                            treeRender(side);
                        }
                    })
                    .catch(function() {});
            });
        }

        window.metadataTreeRender = function() {
            const box = document.getElementById('metadataTree');
            if (!box || !systemMetadata) return;
            box.innerHTML = '';
            const curriculum = systemMetadata.curriculum || {};
            const comps = Object.keys(curriculum);
            if (comps.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'text-[10px] text-slate-400';
                empty.textContent = '（暂无大纲，可点击上方「添加册次」开始构建，或使用预设模板一键载入）';
                box.appendChild(empty);
                return;
            }
            comps.forEach(function(comp) {
                const row = metadataTreeCompRow(comp);
                if (row) box.appendChild(row);
            });
            // 册次层新增草稿行
            if (window.metadataTreeDraft && window.metadataTreeDraft.kind === 'addComp') {
                const wrap = document.createElement('div');
                wrap.appendChild(metadataTreeDraftRow(window.metadataTreeDraft, function(v) {
                    const cur = systemMetadata.curriculum;
                    if (cur[v] !== undefined) {
                        showToast('已存在同名册次', 'warning');
                        metadataTreeRender();
                        return;
                    }
                    cur[v] = {};
                    syncMetadataVisualToJson();
                    metadataTreeRender();
                }));
                box.appendChild(wrap);
            }
        };

        window.metadataTreeDeleteTopic = function(comp, ch, secIdx, tIdx) {
            const sec = systemMetadata.curriculum[comp][ch][secIdx];
            const name = window.topicName(sec.topics[tIdx]);
            if (!confirm('确定删除考点「' + name + '」吗？\n已录题目若引用该考点，将显示为空占位。')) return;
            sec.topics.splice(tIdx, 1);
            syncMetadataVisualToJson();
            metadataTreeRender();
        };

        window.metadataTreeDeleteSec = function(comp, ch, secIdx) {
            const sec = systemMetadata.curriculum[comp][ch][secIdx];
            if (!confirm('确定删除小节「' + sec.name + '」及其全部考点吗？\n已录题目的分类不受影响，但考点显示将变为空占位。')) return;
            systemMetadata.curriculum[comp][ch].splice(secIdx, 1);
            syncMetadataVisualToJson();
            metadataTreeRender();
        };

        window.metadataTreeDeleteChap = function(comp, ch) {
            if (!confirm('确定删除章节「' + ch + '」及其下所有小节/考点吗？')) return;
            delete systemMetadata.curriculum[comp][ch];
            syncMetadataVisualToJson();
            metadataTreeRender();
        };

        window.metadataTreeDeleteComp = function(comp) {
            if (!confirm('确定删除册次「' + comp + '」及其中全部内容吗？\n建议先确认该册次下没有需要的题目。')) return;
            delete systemMetadata.curriculum[comp];
            syncMetadataVisualToJson();
            metadataTreeRender();
        };

        // Settings tab switcher
        let activeSettingsTab = 'api';
        window.switchSettingsTab = function(tabName) {
            activeSettingsTab = tabName;
            const btnApi = document.getElementById('btn-settings-api');
            const btnMeta = document.getElementById('btn-settings-metadata');
            const btnJson = document.getElementById('btn-settings-json');
            const btnAbout = document.getElementById('btn-settings-about');
            const btnDiff = document.getElementById('btn-settings-difficulty');
            const tabApi = document.getElementById('settings-tab-api');
            const tabMeta = document.getElementById('settings-tab-metadata');
            const tabJson = document.getElementById('settings-tab-json');
            const tabAbout = document.getElementById('settings-tab-about');
            const tabDiff = document.getElementById('settings-tab-difficulty');
            const btnSave = document.getElementById('btnSettingsSave');

            // Reset all buttons
            [btnApi, btnMeta, btnJson, btnAbout, btnDiff].forEach(b => {
                if (b) {
                    b.classList.remove('border-brand-500', 'text-brand-600', 'font-semibold');
                    b.classList.add('border-transparent', 'text-slate-500');
                }
            });
            // Hide all tabs
            [tabApi, tabMeta, tabJson, tabAbout, tabDiff].forEach(t => {
                if (t) t.classList.add('hidden');
            });
            // 预设模板仅在 metadata tab 显示
            const _presetsSidebar = document.getElementById('settingsSidebarPresets');
            if (_presetsSidebar) _presetsSidebar.classList.add('hidden');

            if (tabName === 'api') {
                if (btnApi) {
                    btnApi.classList.add('border-brand-500', 'text-brand-600', 'font-semibold');
                    btnApi.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabApi) tabApi.classList.remove('hidden');
                if (btnSave) btnSave.classList.remove('hidden');
            } else if (tabName === 'metadata') {
                if (btnMeta) {
                    btnMeta.classList.add('border-brand-500', 'text-brand-600', 'font-semibold');
                    btnMeta.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabMeta) tabMeta.classList.remove('hidden');
                const presetsSidebar = document.getElementById('settingsSidebarPresets');
                if (presetsSidebar) presetsSidebar.classList.remove('hidden');
                if (btnSave) btnSave.classList.remove('hidden');
                if (typeof renderMetadataVisualEditors === 'function') {
                    renderMetadataVisualEditors();
                }
                if (typeof loadBothTrees === 'function') loadBothTrees();
            } else if (tabName === 'json') {
                if (btnJson) {
                    btnJson.classList.add('border-brand-500', 'text-brand-600', 'font-semibold');
                    btnJson.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabJson) tabJson.classList.remove('hidden');
                if (btnSave) btnSave.classList.remove('hidden');
                // Load latest JSON from API
                fetch('/api/config/metadata')
                    .then(r => r.json())
                    .then(data => {
                        const el = document.getElementById('settingsMetadataJson');
                        if (el) el.value = JSON.stringify(data, null, 2);
                    })
                    .catch(err => {
                        showToast('加载元数据配置失败: ' + err, 'error');
                    });
            } else if (tabName === 'about') {
                if (btnAbout) {
                    btnAbout.classList.add('border-brand-500', 'text-brand-600', 'font-semibold');
                    btnAbout.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabAbout) tabAbout.classList.remove('hidden');
                if (btnSave) btnSave.classList.add('hidden');
                refreshAboutTabUpdateInfo();
            } else if (tabName === 'difficulty') {
                if (btnDiff) {
                    btnDiff.classList.add('border-brand-500', 'text-brand-600', 'font-semibold');
                    btnDiff.classList.remove('border-transparent', 'text-slate-500');
                }
                if (tabDiff) tabDiff.classList.remove('hidden');
                if (btnSave) btnSave.classList.add('hidden');
            }
        };

        // 加载预设大纲到JSON编辑框（不影响左侧树和当前活跃大纲）
        window.loadPresetToJson = function(version) {
            if (!version) {
                // 空值：恢复当前活跃大纲的JSON
                if (typeof window.systemMetadata !== 'undefined' && window.systemMetadata) {
                    document.getElementById('settingsMetadataJson').value = JSON.stringify(window.systemMetadata, null, 2);
                    showToast('已恢复当前活跃大纲配置');
                }
                return;
            }
            const versionName = version === 'B' ? '人教B版' : (version === 'S' ? '苏教版' : (version === 'H' ? '沪教版' : (version === 'G' ? '高三专题' : '人教A版')));
            fetch('/api/config/curriculum-presets/' + encodeURIComponent(version))
                .then(function(response) {
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    return response.json();
                })
                .then(function(data) {
                    if (!data || !data.metadata || !data.metadata.curriculum) throw new Error('响应格式错误');
                    document.getElementById('settingsMetadataJson').value = JSON.stringify(data.metadata, null, 2);
                    showToast('已加载【' + versionName + '】配置到编辑框（仅查看，保存后才会切换活跃大纲）');
                })
                .catch(function(error) {
                    showToast('加载大纲配置失败: ' + error.message, 'error');
                });
        };

        // Reset Metadata to High school math template
        window.resetMetadataToDefault = function(version = 'A') {
            const versionName = version === 'B' ? '人教B版' : (version === 'S' ? '苏教版' : (version === 'H' ? '沪教版' : (version === 'G' ? '高三专题复习' : '人教A版')));
            if (!confirm(`确认要将所有题型、难度和学段重置为默认的【${versionName}】配置模板吗？这不会修改您的数据库题目，但会替换下方编辑框的内容（需点击保存后生效）。`)) {
                return;
            }

            fetch('/api/config/curriculum-presets/' + encodeURIComponent(version))
                .then(response => {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.json();
                })
                .then(data => {
                    if (!data || !data.metadata || !data.metadata.curriculum) {
                        throw new Error('教材大纲预设响应格式错误');
                    }
                    document.getElementById('settingsMetadataJson').value = JSON.stringify(data.metadata, null, 2);
                    showToast(`已加载默认【${data.name || versionName}】配置模板，请点击最下方的 [保存配置] 按钮进行保存并应用。`);
                })
                .catch(error => {
                    showToast('加载教材大纲预设失败: ' + error.message, 'error');
                });
        };

        // ----------------- App Version & Update Management -----------------

        let latestReleaseCache = null;
        const IGNORED_UPDATE_KEY = 'mathbank_ignored_update_version';
        const LAST_CHECK_TIME_KEY = 'mathbank_last_update_check_time';

        // Check if user is on Mac or Windows
        function getClientOS() {
            const ua = navigator.userAgent || '';
            if (/macintosh|mac os x/i.test(ua)) return 'macOS';
            if (/windows|win32/i.test(ua)) return 'Windows';
            return 'Other';
        }

        // Open update modal with data
        window.openUpdateModal = function(releaseData) {
            if (!releaseData) return;
            const modal = document.getElementById('updateModal');
            if (!modal) return;

            const titleEl = document.getElementById('updateModalTitle');
            const badgeEl = document.getElementById('updateModalBadge');
            const dateEl = document.getElementById('updateModalDate');
            const bodyEl = document.getElementById('updateModalBody');
            const btnGithub = document.getElementById('btnViewOnGithub');
            const btnIgnoreText = document.getElementById('btnIgnoreVersionText');
            const gitTip = document.getElementById('updateGitTip');

            const latestVer = releaseData.latest_version || releaseData.current_version || 'v2.0.1';
            if (titleEl) titleEl.innerText = `发现新版本 ${latestVer}`;
            if (badgeEl) badgeEl.innerText = releaseData.release_title || 'NEW RELEASE';
            
            if (dateEl) {
                if (releaseData.published_at) {
                    const d = new Date(releaseData.published_at);
                    dateEl.innerText = `发布于 ${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
                } else {
                    dateEl.innerText = '官方正式发布';
                }
            }

            if (bodyEl) {
                bodyEl.innerText = releaseData.release_body || '本版本包含多项性能与体验优化，推荐升级。';
            }

            if (btnGithub && releaseData.release_url) {
                btnGithub.href = releaseData.release_url;
            }

            if (btnIgnoreText) {
                btnIgnoreText.innerText = `忽略此版本 (${latestVer})`;
            }

            if (gitTip) {
                if (releaseData.is_git_repo) {
                    gitTip.classList.remove('hidden');
                } else {
                    gitTip.classList.add('hidden');
                }
            }

            // Bind assets download URLs
            const clientOS = getClientOS();
            const btnMac = document.getElementById('btnDownloadMac');
            const btnWin = document.getElementById('btnDownloadWin');
            const macSize = document.getElementById('macDownloadSize');
            const winSize = document.getElementById('winDownloadSize');

            const macAsset = releaseData.assets && releaseData.assets.macOS;
            const winAsset = releaseData.assets && releaseData.assets.Windows;

            if (btnMac) {
                if (macAsset && macAsset.url) {
                    btnMac.href = macAsset.url;
                    btnMac.classList.remove('opacity-50', 'pointer-events-none');
                    if (macSize) macSize.innerText = `${macAsset.size_mb} MB • .zip 格式`;
                } else {
                    btnMac.href = releaseData.release_url || 'https://github.com/JudgePeach/math-question-bank/releases';
                    if (macSize) macSize.innerText = '前往 Releases 下载';
                }
                if (clientOS === 'macOS') {
                    btnMac.classList.add('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                } else {
                    btnMac.classList.remove('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                }
            }

            if (btnWin) {
                if (winAsset && winAsset.url) {
                    btnWin.href = winAsset.url;
                    btnWin.classList.remove('opacity-50', 'pointer-events-none');
                    if (winSize) winSize.innerText = `${winAsset.size_mb} MB • .zip 格式`;
                } else {
                    btnWin.href = releaseData.release_url || 'https://github.com/JudgePeach/math-question-bank/releases';
                    if (winSize) winSize.innerText = '前往 Releases 下载';
                }
                if (clientOS === 'Windows') {
                    btnWin.classList.add('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                } else {
                    btnWin.classList.remove('ring-2', 'ring-brand-500', 'bg-brand-50/50');
                }
            }

            // Show modal
            window.openModalWithTransition(modal, { onEscape: window.closeUpdateModal });
        };

        window.closeUpdateModal = function() {
            window.closeModalWithTransition('updateModal');
        };

        // Ignore current version update
        window.ignoreCurrentVersionUpdate = function() {
            if (latestReleaseCache && latestReleaseCache.latest_version) {
                localStorage.setItem(IGNORED_UPDATE_KEY, latestReleaseCache.latest_version);
                const badge = document.getElementById('updateBadge');
                if (badge) badge.classList.add('hidden');
                showToast(`已忽略版本 ${latestReleaseCache.latest_version} 的更新提醒。后续若有更高版本将再次通知您。`);
            }
            closeUpdateModal();
            refreshAboutTabUpdateInfo();
        };

        // Clear ignored version
        window.clearIgnoredVersion = function() {
            localStorage.removeItem(IGNORED_UPDATE_KEY);
            const badge = document.getElementById('updateBadge');
            if (badge && latestReleaseCache && latestReleaseCache.has_update) {
                badge.classList.remove('hidden');
            }
            showToast('已恢复版本更新提醒。');
            refreshAboutTabUpdateInfo();
        };

        // Toggle ignore current version directly from Settings panel
        window.toggleIgnoreCurrentVersionFromSettings = function() {
            if (!latestReleaseCache || !latestReleaseCache.latest_version) return;
            const currentIgnored = localStorage.getItem(IGNORED_UPDATE_KEY);
            const latestVer = latestReleaseCache.latest_version;
            const badge = document.getElementById('updateBadge');

            if (currentIgnored === latestVer) {
                // Was ignored -> unignore
                localStorage.removeItem(IGNORED_UPDATE_KEY);
                if (badge && latestReleaseCache.has_update) {
                    badge.classList.remove('hidden');
                }
                showToast(`已恢复版本 ${latestVer} 的更新提醒。`);
            } else {
                // Ignore it
                localStorage.setItem(IGNORED_UPDATE_KEY, latestVer);
                if (badge) {
                    badge.classList.add('hidden');
                }
                showToast(`已忽略版本 ${latestVer} 的更新提醒。若后续发布更高版本将再次通知您。`);
            }
            refreshAboutTabUpdateInfo();
        };

        // Refresh update info in About Tab
        window.refreshAboutTabUpdateInfo = function() {
            const textEl = document.getElementById('aboutUpdateStatusText');
            const badgeVer = document.getElementById('aboutCurrentVersionBadge');
            const ignoredRow = document.getElementById('ignoredVersionRow');
            const ignoredText = document.getElementById('ignoredVersionText');
            const ignoredVer = localStorage.getItem(IGNORED_UPDATE_KEY);
            const btnToggleIgnore = document.getElementById('btnToggleIgnoreVersion');
            const iconToggleIgnore = document.getElementById('iconToggleIgnore');
            const textToggleIgnore = document.getElementById('textToggleIgnore');

            if (ignoredVer && ignoredRow && ignoredText) {
                ignoredRow.classList.remove('hidden');
                ignoredText.innerText = `您已忽略版本 ${ignoredVer} 的更新提醒。`;
            } else if (ignoredRow) {
                ignoredRow.classList.add('hidden');
            }

            if (latestReleaseCache) {
                if (badgeVer) badgeVer.innerText = `v${latestReleaseCache.current_version.replace(/^v/i, '')}`;
                if (textEl) {
                    if (latestReleaseCache.has_update) {
                        textEl.innerHTML = `<span class="text-brand-600 font-bold">发现新版本 ${MathBankSafe.escapeText(latestReleaseCache.latest_version)}</span>（当前为 v${MathBankSafe.escapeText(latestReleaseCache.current_version)}）`;
                    } else {
                        textEl.innerText = `当前已是最新版本 (v${latestReleaseCache.current_version}) • 运行环境正常`;
                    }
                }

                // Update the quick ignore button on Settings card
                if (latestReleaseCache.has_update && btnToggleIgnore) {
                    btnToggleIgnore.classList.remove('hidden');
                    if (ignoredVer === latestReleaseCache.latest_version) {
                        if (iconToggleIgnore) iconToggleIgnore.className = 'fa-solid fa-bell text-[11px] text-amber-500';
                        if (textToggleIgnore) textToggleIgnore.innerText = '恢复提醒';
                        btnToggleIgnore.title = '恢复该版本的更新提醒与呼吸徽章';
                    } else {
                        if (iconToggleIgnore) iconToggleIgnore.className = 'fa-solid fa-bell-slash text-[11px] text-slate-400';
                        if (textToggleIgnore) textToggleIgnore.innerText = '忽略此版本';
                        btnToggleIgnore.title = '忽略当前版本的更新提醒';
                    }
                } else if (btnToggleIgnore) {
                    btnToggleIgnore.classList.add('hidden');
                }
            }
        };

        // Check update manually
        window.checkAppUpdateManually = async function(isUserTriggered = false) {
            const spinIcon = document.getElementById('checkUpdateSpinIcon');
            const textEl = document.getElementById('aboutUpdateStatusText');
            if (spinIcon) spinIcon.classList.add('fa-spin');
            if (textEl) textEl.innerText = '正在连接 GitHub 检测最新版本...';

            try {
                const res = await fetch('/api/version/check-update');
                const data = await res.json();
                latestReleaseCache = data;
                localStorage.setItem(LAST_CHECK_TIME_KEY, Date.now().toString());

                if (spinIcon) spinIcon.classList.remove('fa-spin');
                refreshAboutTabUpdateInfo();

                if (data.status === 'success') {
                    const ignoredVer = localStorage.getItem(IGNORED_UPDATE_KEY);
                    const badge = document.getElementById('updateBadge');

                    if (data.has_update) {
                        if (badge && (!ignoredVer || ignoredVer !== data.latest_version)) {
                            badge.classList.remove('hidden');
                        }
                        if (isUserTriggered) {
                            openUpdateModal(data);
                        } else if (!ignoredVer || ignoredVer !== data.latest_version) {
                            showToast(`🎉 发现新版本 ${data.latest_version}，可前往设置查看更新内容。`, 'info');
                        }
                    } else {
                        if (badge) badge.classList.add('hidden');
                        if (isUserTriggered) {
                            showToast(`✨ 当前已是最新版本 (v${data.current_version})！`, 'success');
                        }
                    }
                } else if (isUserTriggered) {
                    showToast(data.message || '检查更新失败，请稍后重试', 'warning');
                }
            } catch (err) {
                if (spinIcon) spinIcon.classList.remove('fa-spin');
                if (textEl) textEl.innerText = '检查更新连接超时，请检查网络';
                if (isUserTriggered) {
                    showToast('连接 GitHub 检查更新失败: ' + err.message, 'warning');
                }
            }
        };

        // Developer / user demo preview
        window.previewUpdateModalDemo = function() {
            if (latestReleaseCache) {
                openUpdateModal(latestReleaseCache);
            } else {
                fetch('/api/version/check-update')
                    .then(r => r.json())
                    .then(data => {
                        latestReleaseCache = data;
                        openUpdateModal(data);
                    })
                    .catch(err => {
                        // Fallback demo data
                        const demoData = {
                            current_version: "2.0.1",
                            latest_version: "v2.0.1",
                            release_title: "v2.0.1 正式发布",
                            release_body: "1. 新增 Word (.docx) 试卷 OMML 公式与插图 0 损直提智能拆解。\n2. 全新升级 KaTeX 填空题 \\fillin 宏与数学比较符号防撕裂转义自愈。\n3. 优化拖放区域 UI 与拆分进度动画盒模型。\n4. 内置版本自动检测、忽略更新与便携包一键直链更新体系。",
                            release_url: "https://github.com/JudgePeach/math-question-bank/releases",
                            published_at: new Date().toISOString(),
                            is_git_repo: true,
                            assets: {
                                macOS: { name: "MathBank-macOS.zip", size_mb: 11.7, downloads: 40, url: "https://github.com/JudgePeach/math-question-bank/releases" },
                                Windows: { name: "MathBank-Windows-x64.zip", size_mb: 51.2, downloads: 171, url: "https://github.com/JudgePeach/math-question-bank/releases" }
                            }
                        };
                        openUpdateModal(demoData);
                    });
            }
        };

        // Silent background check on app startup
        window.silentCheckAppUpdateOnStart = function() {
            setTimeout(() => {
                checkAppUpdateManually(false);
            }, 1000);
        };


        // Populate Categories in Editor Selects
        function formatChineseDate(isoStr) {
            if (!isoStr) return '未知时间';
            try {
                const date = new Date(isoStr);
                if (isNaN(date.getTime())) return '未知时间';
                const y = date.getFullYear();
                const m = String(date.getMonth() + 1).padStart(2, '0');
                const d = String(date.getDate()).padStart(2, '0');
                const h = String(date.getHours()).padStart(2, '0');
                const min = String(date.getMinutes()).padStart(2, '0');
                return `${y}年${m}月${d}日 ${h}时${min}分`;
            } catch (e) {
                return '未知时间';
            }
        }

        // Helper string mappings
        function getDifficultyColor(val) {
            if (!val) return 'text-slate-600 bg-slate-100 border border-slate-200/60';
            if (systemMetadata && Array.isArray(systemMetadata.difficulties) && systemMetadata.difficulties.length > 0) {
                const found = systemMetadata.difficulties.find(d => d.value === val);
                if (found && found.color) {
                    return MathBankSafe.safeClassList(found.color, 'text-slate-600 bg-slate-100 border border-slate-200/60');
                }
            }
            if (val === 'L1') return 'text-green-600 bg-green-50 border border-green-200/60';
            if (val === 'L2') return 'text-blue-600 bg-blue-50 border border-blue-200/60';
            if (val === 'L3') return 'text-orange-600 bg-orange-50 border border-orange-200/60';
            if (val === 'L4') return 'text-red-600 bg-red-50 border border-red-200/60';
            return 'text-slate-600 bg-slate-100 border border-slate-200/60';
        }
        window.getDifficultyColor = getDifficultyColor;

        // Single difficulty-badge implementation shared by the sidebar and the
        // paper workspace. Pass { size: 'regular' } for the roomier card layout.
        function getDifficultyBadge(diff, options = {}) {
            const sizeClass = (options && options.size === 'regular')
                ? 'px-2 py-0.5 rounded-lg text-xs font-semibold'
                : 'px-1.5 py-0.5 rounded text-[9px] font-bold';
            const label = String(diff || '').trim();
            if (!systemMetadata || !systemMetadata.difficulties || systemMetadata.difficulties.length === 0) {
                // Offline fallback keeps the L1..L4 ladder readable.
                const fallbackColors = {
                    L1: 'text-green-600 bg-green-50',
                    L2: 'text-blue-600 bg-blue-50',
                    L3: 'text-orange-600 bg-orange-50',
                    L4: 'text-red-600 bg-red-50'
                };
                const colorClass = fallbackColors[label] || 'text-slate-600 bg-slate-100';
                return `<span class="${sizeClass} ${colorClass}">${MathBankSafe.escapeText(label || '未定')}</span>`;
            }
            const found = systemMetadata.difficulties.find(d => d.value === diff);
            if (found) {
                // Strip emojis (symbols) from label to keep badge clean and neat
                const cleanLabel = String(found.label || '').replace(/[\u2700-\u27BF]|[\uE000-\uF8FF]|\uD83C[\uDC00-\uDFFF]|\uD83D[\uDC00-\uDFFF]|[\u2011-\u26FF]|\uD83E[\uDD00-\uDFFF]/g, '').trim();
                const colorClass = MathBankSafe.safeClassList(found.color, 'text-slate-600 bg-slate-100');
                return `<span class="${sizeClass} ${colorClass}">${MathBankSafe.escapeText(cleanLabel)}</span>`;
            }
            return `<span class="${sizeClass} text-slate-600 bg-slate-100">${MathBankSafe.escapeText(diff)}</span>`;
        }

        function getTypeText(type) {
            if (!systemMetadata || !systemMetadata.question_types || systemMetadata.question_types.length === 0) {
                if (type === 'single_choice') return '单选题';
                if (type === 'multi_choice') return '多选题';
                if (type === 'fill_in_blank') return '填空题';
                if (type === 'detailed_answer') return '解答题';
                return '数学题';
            }
            const found = systemMetadata.question_types.find(t => t.value === type);
            return found ? found.label : type;
        }

        function getDifficultyText(val) {
            if (!systemMetadata || !systemMetadata.difficulties || systemMetadata.difficulties.length === 0) {
                if (val === 'L1') return 'L1 概念定义';
                if (val === 'L2') return 'L2 基本运算';
                if (val === 'L3') return 'L3 性质应用';
                if (val === 'L4') return 'L4 综合拓展';
                return '未定';
            }
            const found = systemMetadata.difficulties.find(d => d.value === val);
            return found ? found.label : val;
        }

        // Tab Switching for 3-in-1 workflow
        function escapeRegExp(string) {
            return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        }

        // Global Theme Color and Dark Mode Management
        window.changeTheme = function(themeName, save = true) {
            const themes = ['theme-obsidian', 'theme-violet', 'theme-emerald', 'theme-ocean', 'theme-amber', 'theme-crimson'];
            
            // Remove all themes from document root and body
            themes.forEach(t => {
                document.documentElement.classList.remove(t);
                document.body.classList.remove(t);
            });
            
            // Apply selected theme
            const newThemeClass = `theme-${themeName}`;
            document.documentElement.classList.add(newThemeClass);
            document.body.classList.add(newThemeClass);

            // Update dropdown UI (Dot, Text, Checkmarks)
            const dot = document.getElementById('currentThemeDot');
            const nameSpan = document.getElementById('currentThemeName');
            if (dot && nameSpan) {
                const colorMap = {
                    'obsidian': '#0f172a',
                    'violet': '#8b5cf6',
                    'ocean': '#0ea5e9',
                    'emerald': '#10b981',
                    'amber': '#f59e0b',
                    'crimson': '#f43f5e'
                };
                dot.style.backgroundColor = colorMap[themeName] || '#0f172a';
                nameSpan.textContent = getThemeChineseName(themeName);
            }
            
            // Update check marks
            const themesOnly = ['obsidian', 'violet', 'ocean', 'emerald', 'amber', 'crimson'];
            themesOnly.forEach(t => {
                const check = document.getElementById(`check-${t}`);
                if (check) {
                    if (t === themeName) {
                        check.classList.remove('hidden');
                    } else {
                        check.classList.add('hidden');
                    }
                }
            });
            
            if (save) {
                localStorage.setItem('theme-color', themeName);
                if (typeof showToast === 'function') {
                    showToast(`已切换至 ${getThemeChineseName(themeName)} 配色`);
                }
            }
        };

        // Dropdown toggle logic

        window.toggleThemeDropdown = function(event) {
            event.stopPropagation();
            const menu = document.getElementById('themeDropdownMenu');
            const arrow = document.getElementById('themeDropdownArrow');
            if (!menu) return;
            
            const isOpen = !menu.classList.contains('pointer-events-none');
            if (isOpen) {
                closeThemeDropdown();
            } else {
                menu.classList.remove('scale-90', 'opacity-0', 'pointer-events-none');
                menu.classList.add('scale-100', 'opacity-100');
                const themeDropdownButton = document.getElementById('themeDropdownBtn');
                if (themeDropdownButton) themeDropdownButton.setAttribute('aria-expanded', 'true');
                if (arrow) arrow.classList.add('rotate-180');
                
                // Add click listener to close when clicking outside
                document.addEventListener('click', closeThemeDropdownOnce);
            }
        };
        
        function closeThemeDropdown() {
            const menu = document.getElementById('themeDropdownMenu');
            const arrow = document.getElementById('themeDropdownArrow');
            if (!menu) return;
            menu.classList.add('scale-90', 'opacity-0', 'pointer-events-none');
            menu.classList.remove('scale-100', 'opacity-100');
            const themeDropdownButton = document.getElementById('themeDropdownBtn');
            if (themeDropdownButton) themeDropdownButton.setAttribute('aria-expanded', 'false');
            if (arrow) arrow.classList.remove('rotate-180');
            document.removeEventListener('click', closeThemeDropdownOnce);
        }
        
        function closeThemeDropdownOnce() {
            closeThemeDropdown();
        }

        window.selectTheme = function(themeName) {
            window.changeTheme(themeName);
            closeThemeDropdown();
        };

        // Workspace Dropdown toggle logic
        window.toggleWorkspaceDropdown = function(event) {
            event.stopPropagation();
            const menu = document.getElementById('workspaceDropdownMenu');
            const arrow = document.getElementById('workspaceDropdownArrow');
            if (!menu) return;
            
            const isOpen = !menu.classList.contains('pointer-events-none');
            if (isOpen) {
                closeWorkspaceDropdown();
            } else {
                if (typeof closeThemeDropdown === 'function') closeThemeDropdown();
                menu.classList.remove('scale-90', 'opacity-0', 'pointer-events-none');
                menu.classList.add('scale-100', 'opacity-100');
                const workspaceDropdownButton = document.getElementById('workspaceDropdownBtn');
                if (workspaceDropdownButton) workspaceDropdownButton.setAttribute('aria-expanded', 'true');
                if (arrow) arrow.classList.add('rotate-180');
                document.addEventListener('click', closeWorkspaceDropdownOnce);
            }
        };

        function closeWorkspaceDropdown() {
            const menu = document.getElementById('workspaceDropdownMenu');
            const arrow = document.getElementById('workspaceDropdownArrow');
            if (!menu) return;
            menu.classList.add('scale-90', 'opacity-0', 'pointer-events-none');
            menu.classList.remove('scale-100', 'opacity-100');
            const workspaceDropdownButton = document.getElementById('workspaceDropdownBtn');
            if (workspaceDropdownButton) workspaceDropdownButton.setAttribute('aria-expanded', 'false');
            if (arrow) arrow.classList.remove('rotate-180');
            document.removeEventListener('click', closeWorkspaceDropdownOnce);
        }

        function closeWorkspaceDropdownOnce() {
            closeWorkspaceDropdown();
        }

        window.selectWorkspace = function(workspaceId, workspaceName) {
            const currentWorkspaceName = document.getElementById('currentWorkspaceName');
            if (currentWorkspaceName) {
                currentWorkspaceName.textContent = workspaceName;
            }

            const toggleSidebarBtn = document.getElementById('toggleSidebarBtn');
            if (toggleSidebarBtn) {
                if (workspaceId === 'paper') {
                    toggleSidebarBtn.classList.add('hidden');
                } else {
                    toggleSidebarBtn.classList.remove('hidden');
                }
            }

            const checkBank = document.getElementById('ws-check-bank');
            const checkPaper = document.getElementById('ws-check-paper');
            const checkLesson = document.getElementById('ws-check-lesson');
            const btnBank = document.getElementById('ws-btn-bank');
            const btnPaper = document.getElementById('ws-btn-paper');
            const btnLesson = document.getElementById('ws-btn-lesson');

            [checkBank, checkPaper, checkLesson].forEach(el => el && el.classList.add('hidden'));
            [btnBank, btnPaper, btnLesson].forEach(el => el && el.classList.remove('font-medium'));
            if (workspaceId === 'bank' && checkBank) { checkBank.classList.remove('hidden'); btnBank && btnBank.classList.add('font-medium'); }
            if (workspaceId === 'paper' && checkPaper) { checkPaper.classList.remove('hidden'); btnPaper && btnPaper.classList.add('font-medium'); }
            if (workspaceId === 'lesson' && checkLesson) { checkLesson.classList.remove('hidden'); btnLesson && btnLesson.classList.add('font-medium'); }

            // 工作台显示控制
            const bankWs = document.getElementById('bankWorkspaceSection');
            const bankListWs = document.getElementById('bankListWorkspace');
            const paperWs = document.getElementById('paperWorkspaceSection');
            const lessonWs = document.getElementById('lessonWorkspace');
            const lectureWs = document.getElementById('lectureWorkspace');
            const entryWs = document.getElementById('entryWorkspace');
            if (bankWs) bankWs.classList.toggle('hidden', workspaceId !== 'bank');
            if (bankListWs) bankListWs.classList.toggle('hidden', workspaceId !== 'bankList');
            if (paperWs) paperWs.classList.toggle('hidden', workspaceId !== 'paper');
            if (lessonWs) lessonWs.classList.toggle('hidden', workspaceId !== 'lesson');
            if (lectureWs) {
                lectureWs.classList.toggle('hidden', workspaceId !== 'lecture');
                if (workspaceId === 'lecture' && window.LectureManager && typeof window.LectureManager.refresh === 'function') {
                    setTimeout(() => window.LectureManager.refresh(), 60);
                }
            }
            if (workspaceId === 'lesson' && window.LessonManager && typeof window.LessonManager.refresh === 'function') {
                setTimeout(() => window.LessonManager.refresh(), 60);
            }
            if (entryWs) entryWs.classList.toggle('hidden', workspaceId !== 'entry');
            const draftWs = document.getElementById('draftWorkspace');
            if (draftWs) {
                draftWs.classList.toggle('hidden', workspaceId !== 'drafts');
                if (workspaceId === 'drafts' && typeof window.renderDraftList === 'function') {
                    setTimeout(() => window.renderDraftList(), 100);
                }
            }
            // 试卷导入工作台
            const paperImportWs = document.getElementById('paperImportWorkspace');
            if (paperImportWs) {
                paperImportWs.classList.toggle('hidden', workspaceId !== 'paperImport');
            }
            // 控制中止拆分按钮的显示（只在试卷导入界面显示）
            const cancelImportContainer = document.getElementById('cancelImportActionContainer');
            if (cancelImportContainer) {
                cancelImportContainer.classList.toggle('hidden', workspaceId !== 'paperImport');
            }
            

            // 本地题库工作台：独立筛选条件和题目列表
            if (workspaceId === 'bankList') {
                // 延迟加载题目列表（等待DOM渲染完成）
                setTimeout(() => {
                    if (typeof loadQuestions === 'function') {
                        currentBankPage = 1;
                        loadQuestions();
                    }
                }, 100);
            }

            // 全局导航栏状态
            const navBank = document.getElementById('nav-bank');
            const navBankList = document.getElementById('nav-bankList');
            const navPaper = document.getElementById('nav-paper');
            const navLesson = document.getElementById('nav-lesson');
            const navs = [{el: navBank, id: 'bank'}, {el: navBankList, id: 'bankList'}, {el: navPaper, id: 'paper'}, {el: navLesson, id: 'lesson'}];
            navs.forEach(t => {
                if (t.el) {
                    if (t.id === workspaceId) {
                        t.el.classList.add('active');
                    } else {
                        t.el.classList.remove('active');
                    }
                }
            });

            closeWorkspaceDropdown();
        };

        let isSidebarCollapsed = false;
        let lastSidebarWidth = 320;
        let lastPreviewWidth = 450;

        window.toggleDarkMode = function() {
            const isDark = document.documentElement.classList.toggle('dark');
            document.body.classList.toggle('dark', isDark);
            
            localStorage.setItem('dark-mode', isDark);
            updateDarkModeIcon(isDark);
            
            if (typeof showToast === 'function') {
                showToast(isDark ? '已开启优雅夜间模式 🌙' : '已返回亮色模式 ☀️');
            }
        };

        function getThemeChineseName(themeName) {
            const names = {
                'obsidian': '曜石黑',
                'violet': '罗兰紫',
                'emerald': '翡翠绿',
                'ocean': '深海蓝',
                'amber': '琥珀橙',
                'crimson': '玫瑰红'
            };
            return names[themeName] || themeName;
        }

        function updateDarkModeIcon(isDark) {
            const btn = document.getElementById('darkModeBtn');
            if (btn) {
                btn.innerHTML = isDark 
                    ? '<i class="fa-solid fa-sun text-xs text-amber-500"></i>' 
                    : '<i class="fa-solid fa-moon text-xs"></i>';
                btn.title = isDark ? '切换亮色模式' : '切换夜间模式';
            }
        }

        function initTheme() {
            const savedTheme = localStorage.getItem('theme-color') || 'violet';
            const savedDarkMode = localStorage.getItem('dark-mode') === 'true';
            
            // Apply saved theme color without triggering toast
            window.changeTheme(savedTheme, false);
            
            // Apply saved dark mode status
            if (savedDarkMode) {
                document.documentElement.classList.add('dark');
                document.body.classList.add('dark');
                updateDarkModeIcon(true);
            } else {
                document.documentElement.classList.remove('dark');
                document.body.classList.remove('dark');
                updateDarkModeIcon(false);
            }
        }

        // Initialize theme and check update on DOMContentLoaded
        document.addEventListener('DOMContentLoaded', () => {
            initTheme();
            if (window.silentCheckAppUpdateOnStart) {
                window.silentCheckAppUpdateOnStart();
            }
        });

        /**
         * =========================================================================
         * 全局极速自定义 Tooltip 悬浮提示系统 (Global Fast Tooltip System)
         * =========================================================================
         * 自动接管全局所有带有 title 与 data-tooltip 属性的按钮与元素：
         * 1. 移入按钮稳定停顿 0.5 秒 (500ms) 后平滑浮现提示文字。
         * 2. 移出按钮瞬间 0ms 立即消失隐藏，杜绝旧按钮文字滞留与残影。
         * 3. 滑入新按钮重新独立计时 0.5 秒，鼠标快速扫过不闪烁。
         * 4. 自动移除原生 title 属性暂存至 dataset.tooltip，防止 2 秒后原生浏览器气泡重叠打架。
         * 5. 智能自适应视口边缘碰撞与上下翻转，点击、滚动与按键时即刻淡出隐藏。
         */
        (function initGlobalFastTooltip() {
            let tooltipEl = null;
            let showTimer = null;
            let activeTarget = null;
            const SHOW_DELAY = 500; // 首次悬停停顿 0.5 秒 (500ms) 后显示
            const OFFSET_Y = 6;     // 与目标元素的垂直微间距

            function ensureTooltipElement() {
                if (!tooltipEl) {
                    tooltipEl = document.createElement('div');
                    tooltipEl.className = 'global-tooltip';
                    tooltipEl.setAttribute('role', 'tooltip');
                    tooltipEl.setAttribute('aria-hidden', 'true');
                    document.body.appendChild(tooltipEl);
                }
                return tooltipEl;
            }

            function getTooltipText(element) {
                if (!element || element.nodeType !== 1) return '';
                // 1. 优先读取已缓存或声明的 data-tooltip
                if (element.dataset && element.dataset.tooltip) {
                    return element.dataset.tooltip.trim();
                }
                // 2. 自动捕获原生 title 并抹除原生属性以防原生 2s 延迟黑框弹出
                if (element.hasAttribute('title')) {
                    const rawTitle = element.getAttribute('title') || '';
                    if (rawTitle.trim()) {
                        element.dataset.tooltip = rawTitle.trim();
                        element.removeAttribute('title');
                        return rawTitle.trim();
                    }
                }
                return '';
            }

            function positionTooltip(target, el) {
                const rect = target.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return;

                // 强制测量当前 tooltip 尺寸
                el.style.left = '0px';
                el.style.top = '0px';
                const tipWidth = el.offsetWidth || 100;
                const tipHeight = el.offsetHeight || 28;
                const viewportWidth = window.innerWidth;
                const viewportHeight = window.innerHeight;

                // 水平居中计算并进行安全边缘 Clamp (左右保留 8px 安全边距)
                let left = rect.left + (rect.width - tipWidth) / 2;
                left = Math.max(8, Math.min(left, viewportWidth - tipWidth - 8));

                // 垂直定位计算：优先放置在下方，若下方空间不足则放置在上方
                let top = rect.bottom + OFFSET_Y;
                if (top + tipHeight > viewportHeight - 8) {
                    top = rect.top - tipHeight - OFFSET_Y;
                }
                if (top < 8) {
                    top = 8;
                }

                el.style.left = Math.round(left) + 'px';
                el.style.top = Math.round(top) + 'px';
            }

            function hideImmediately() {
                if (showTimer) {
                    clearTimeout(showTimer);
                    showTimer = null;
                }
                if (tooltipEl) {
                    tooltipEl.classList.remove('is-visible');
                    tooltipEl.setAttribute('aria-hidden', 'true');
                }
                activeTarget = null;
            }

            function scheduleShow(target, text) {
                hideImmediately();
                activeTarget = target;
                showTimer = setTimeout(() => {
                    if (!target || !target.isConnected || activeTarget !== target) return;
                    const el = ensureTooltipElement();
                    el.textContent = text;
                    positionTooltip(target, el);
                    el.classList.add('is-visible');
                    el.setAttribute('aria-hidden', 'false');
                }, SHOW_DELAY);
            }

            // 全局捕获 mouseover 事件代理
            document.addEventListener('mouseover', (e) => {
                const target = e.target && e.target.closest ? e.target.closest('[title], [data-tooltip]') : null;
                if (!target) {
                    if (activeTarget && !e.target.closest('.global-tooltip')) {
                        hideImmediately();
                    }
                    return;
                }

                const text = getTooltipText(target);
                if (!text) {
                    hideImmediately();
                    return;
                }

                if (activeTarget === target) {
                    return;
                }

                scheduleShow(target, text);
            }, true);

            // 鼠标移出目标：立即瞬间隐藏，绝不滞留
            document.addEventListener('mouseout', (e) => {
                const target = e.target && e.target.closest ? e.target.closest('[title], [data-tooltip]') : null;
                if (target && target === activeTarget) {
                    if (!e.relatedTarget || !target.contains(e.relatedTarget)) {
                        hideImmediately();
                    }
                }
            }, true);

            // 点击、触控、滚动或按键时瞬间隐藏，绝不遮挡操作
            ['mousedown', 'pointerdown', 'touchstart', 'wheel', 'scroll'].forEach((evtName) => {
                window.addEventListener(evtName, hideImmediately, { passive: true, capture: true });
            });

            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') hideImmediately();
            });

            window.addEventListener('blur', hideImmediately);
        })();


// ==================== 左侧导航栏功能 ====================
window.toggleNavTheme = function(event) {
    event.stopPropagation();
    const menu = document.getElementById('navThemeMenu');
    if (!menu) return;
    menu.classList.toggle('hidden');
    if (!menu.classList.contains('hidden')) {
        setTimeout(() => {
            document.addEventListener('click', function closeOnce(e) {
                if (!menu.contains(e.target)) {
                    menu.classList.add('hidden');
                    document.removeEventListener('click', closeOnce);
                }
            });
        }, 10);
    }
};

// 重写selectTheme，同步导航栏主题名
const _origSelectTheme = window.selectTheme;
window.selectTheme = function(theme) {
    if (typeof _origSelectTheme === 'function') _origSelectTheme(theme);
};



// ==================== 考向标签筛选（选册次后显示标签） ====================
function renderBankListTopics() {
    const area = document.getElementById('bankListTopicArea');
    const tagsContainer = document.getElementById('bankListTopicTags');
    if (!area || !tagsContainer) return;
    
    // 获取当前筛选的册次
    const compSelect = document.getElementById('filterCompulsory');
    const selectedComp = compSelect ? compSelect.value : '';
    
    if (!selectedComp) {
        area.classList.add('hidden');
        return;
    }
    
    area.classList.remove('hidden');
    const tree = window.categoryTree || {};
    const chapters = tree[selectedComp] || {};
    const slots = (window.extraTopicFilter && window.extraTopicFilter.slots) || [];
    
    tagsContainer.innerHTML = '';
    
    // 收集该册次下所有考向
    let allTopics = [];
    Object.keys(chapters).forEach(function(chapter) {
        const knowledges = chapters[chapter] || {};
        Object.keys(knowledges).forEach(function(knowledge) {
            const topics = knowledges[knowledge] || [];
            topics.forEach(function(t) {
                const name = typeof t === 'object' ? t.name : t;
                allTopics.push({name: name, chapter: chapter, knowledge: knowledge, compulsory: selectedComp});
            });
        });
    });
    
    // 去重
    const seen = {};
    allTopics = allTopics.filter(function(t) {
        if (seen[t.name]) return false;
        seen[t.name] = true;
        return true;
    });
    
    // 渲染标签
    allTopics.forEach(function(t) {
        const isSelected = slots.some(function(s) { return s.topic === t.name; });
        const tag = document.createElement('button');
        tag.className = 'px-2 py-0.5 text-[11px] rounded-md transition-colors ' + 
            (isSelected ? 'bg-brand-500 text-white' : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600');
        tag.textContent = t.name;
        tag.title = t.chapter + ' / ' + t.knowledge;
        tag.onclick = function() {
            toggleTopicFilterSlot(t.compulsory, t.chapter, t.knowledge, t.name);
            renderBankListTopics();
        };
        tagsContainer.appendChild(tag);
    });
    
    if (allTopics.length === 0) {
        tagsContainer.innerHTML = '<span class="text-xs text-slate-400">该册次暂无考向</span>';
    }
}

// 监听册次筛选变化
document.addEventListener('change', function(e) {
    if (e.target.id === 'filterCompulsory') {
        renderBankListTopics();
    }
});

// 切换到本地题库时渲染
const origSelectWorkspace2 = window.selectWorkspace;
window.selectWorkspace = function(id) {
    if (origSelectWorkspace2) origSelectWorkspace2(id);
    if (id === 'bankList') {
        setTimeout(function() {
            renderBankListTopics();
        }, 100);
    }
};


// ==================== 录题界面考点标签选择 ====================
function renderEditTopicTags() {
    const select = document.getElementById('editTopic');
    if (!select) return;
    const compSel = document.getElementById('editCompulsory');
    const chapSel = document.getElementById('editChapter');
    const knowSel = document.getElementById('editKnowledge');
    
    const currentValue = select.value;
    
    if (!compSel || !chapSel || !knowSel || !compSel.value || !chapSel.value || !knowSel.value) {
        select.innerHTML = '<option value="">先选小节</option>';
        select.disabled = true;
        return;
    }
    
    const tree = window.categoryTree || {};
    const topics = (tree[compSel.value] && tree[compSel.value][chapSel.value] && tree[compSel.value][chapSel.value][knowSel.value]) || [];
    
    select.disabled = false;
    select.innerHTML = '<option value="">选择考点</option>';
    
    topics.forEach(function(t) {
        const name = typeof t === 'object' ? t.name : t;
        const level = typeof t === 'object' ? (t.level || '') : '';
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name + (level ? ' (' + level + ')' : '');
        if (name === currentValue) opt.selected = true;
        select.appendChild(opt);
    });
    
    // 启用新增/微调按钮
    const addBtn = document.getElementById('addTopicBtn');
    const editBtn = document.getElementById('editTopicsBtn');
    if (addBtn) addBtn.disabled = false;
    if (editBtn) editBtn.disabled = false;
}

// 小节选择变化时渲染考点标签
document.addEventListener('change', function(e) {
    if (e.target.id === 'editKnowledge') {
        renderEditTopicTags();
    }
});

// 加载题目时渲染考点标签
const origLoadQuestionForEdit = window.loadQuestionForEdit;
window.loadQuestionForEdit = function(id) {
    if (origLoadQuestionForEdit) origLoadQuestionForEdit(id);
    setTimeout(function() {
        renderEditTopicTags();
    }, 200);
};


// ========== 附加考向（行式级联选择） ==========
window.addExtraTopicRow = function() {
    const container = document.getElementById('extraTopicsContainer');
    const template = document.getElementById('extraTopicRowTemplate');
    if (!container || !template) return;
    
    const rows = container.querySelectorAll('.extra-topic-row');
    if (rows.length >= 3) {
        if (window.showToast) window.showToast('最多添加3个附加考向', 'warning');
        return;
    }
    
    const row = template.content.cloneNode(true);
    const rowEl = row.querySelector('.extra-topic-row');
    
    // 填充册次下拉框
    const compSel = rowEl.querySelector('.extra-comp');
    const tree = window.categoryTree || {};
    Object.keys(tree).forEach(function(comp) {
        const opt = document.createElement('option');
        opt.value = comp;
        opt.textContent = comp;
        compSel.appendChild(opt);
    });
    
    // 册次变化 → 填充章节
    compSel.addEventListener('change', function() {
        const chapSel = rowEl.querySelector('.extra-chap');
        const knowSel = rowEl.querySelector('.extra-know');
        const topicSel = rowEl.querySelector('.extra-topic');
        chapSel.innerHTML = '<option value="">先选册次</option>';
        knowSel.innerHTML = '<option value="">先选章节</option>';
        topicSel.innerHTML = '<option value="">先选小节</option>';
        knowSel.disabled = true;
        topicSel.disabled = true;
        
        if (compSel.value && tree[compSel.value]) {
            chapSel.disabled = false;
            Object.keys(tree[compSel.value]).forEach(function(chap) {
                const opt = document.createElement('option');
                opt.value = chap;
                opt.textContent = chap;
                chapSel.appendChild(opt);
            });
        } else {
            chapSel.disabled = true;
        }
    });
    
    // 章节变化 → 填充小节
    const chapSel = rowEl.querySelector('.extra-chap');
    chapSel.addEventListener('change', function() {
        const knowSel = rowEl.querySelector('.extra-know');
        const topicSel = rowEl.querySelector('.extra-topic');
        knowSel.innerHTML = '<option value="">先选章节</option>';
        topicSel.innerHTML = '<option value="">先选小节</option>';
        topicSel.disabled = true;
        
        if (compSel.value && chapSel.value && tree[compSel.value][chapSel.value]) {
            knowSel.disabled = false;
            Object.keys(tree[compSel.value][chapSel.value]).forEach(function(know) {
                const opt = document.createElement('option');
                opt.value = know;
                opt.textContent = know;
                knowSel.appendChild(opt);
            });
        } else {
            knowSel.disabled = true;
        }
    });
    
    // 小节变化 → 填充考点
    const knowSel = rowEl.querySelector('.extra-know');
    knowSel.addEventListener('change', function() {
        const topicSel = rowEl.querySelector('.extra-topic');
        topicSel.innerHTML = '<option value="">先选小节</option>';
        
        if (compSel.value && chapSel.value && knowSel.value && 
            tree[compSel.value][chapSel.value][knowSel.value]) {
            topicSel.disabled = false;
            const topics = tree[compSel.value][chapSel.value][knowSel.value] || [];
            topics.forEach(function(t) {
                const name = typeof t === 'object' ? t.name : t;
                const level = typeof t === 'object' ? (t.level || '') : '';
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name + (level ? ' (' + level + ')' : '');
                topicSel.appendChild(opt);
            });
        } else {
            topicSel.disabled = true;
        }
    });
    
    container.appendChild(rowEl);
    window.updateExtraTopicBtn();
};

window.removeExtraTopicRow = function(btn) {
    const row = btn.closest('.extra-topic-row');
    if (row) row.remove();
    window.updateExtraTopicBtn();
};

window.updateExtraTopicBtn = function() {
    const btn = document.getElementById('addExtraTopicBtn');
    const container = document.getElementById('extraTopicsContainer');
    if (!btn || !container) return;
    const rows = container.querySelectorAll('.extra-topic-row');
    btn.disabled = rows.length >= 3;
    btn.style.opacity = rows.length >= 3 ? '0.5' : '1';
};

// 收集附加考向数据（保存时调用）
window.collectExtraTopics = function() {
    const container = document.getElementById('extraTopicsContainer');
    if (!container) return [];
    const result = [];
    container.querySelectorAll('.extra-topic-row').forEach(function(row) {
        const comp = row.querySelector('.extra-comp').value;
        const chap = row.querySelector('.extra-chap').value;
        const know = row.querySelector('.extra-know').value;
        const topic = row.querySelector('.extra-topic').value;
        if (comp && chap && know && topic) {
            result.push({ compulsory: comp, chapter: chap, knowledge: know, topic: topic });
        }
    });
    return result;
};

// 加载题目时渲染附加考向行
window.renderExtraTopicRows = function(extraTopics) {
    const container = document.getElementById('extraTopicsContainer');
    if (!container) return;
    container.innerHTML = '';
    if (!Array.isArray(extraTopics)) return;
    extraTopics.forEach(function(et) {
        window.addExtraTopicRow();
        const rows = container.querySelectorAll('.extra-topic-row');
        const row = rows[rows.length - 1];
        if (!row) return;
        const compSel = row.querySelector('.extra-comp');
        const chapSel = row.querySelector('.extra-chap');
        const knowSel = row.querySelector('.extra-know');
        const topicSel = row.querySelector('.extra-topic');
        
        if (et.compulsory) {
            compSel.value = et.compulsory;
            compSel.dispatchEvent(new Event('change'));
        }
        if (et.chapter) {
            chapSel.value = et.chapter;
            chapSel.dispatchEvent(new Event('change'));
        }
        if (et.knowledge) {
            knowSel.value = et.knowledge;
            knowSel.dispatchEvent(new Event('change'));
        }
        if (et.topic) {
            setTimeout(function() { topicSel.value = et.topic; }, 50);
        }
    });
};


// ========== 录题入口跳转 ==========
window.enterSingleQuestion = function() {
    selectWorkspace('bank', '录题工作台');
    setTimeout(function() {
        if (typeof startNewQuestion === 'function') {
            startNewQuestion();
        }
    }, 100);
};

window.enterPaperImport = function() {
    selectWorkspace('paperImport', '试卷导入');
    if (typeof window.setPaperImportMode === 'function') window.setPaperImportMode('single');
};

// 批量整册入口：共用试卷录入工作台，只是切到批量面板
window.enterBatchImport = function() {
    selectWorkspace('paperImport', '试卷导入');
    if (typeof window.setPaperImportMode === 'function') window.setPaperImportMode('batch');
};

// ==================== 独立草稿箱工作台 ====================
window.renderDraftList = function() {
    const container = document.getElementById('draftListContainer');
    const searchInput = document.getElementById('draftSearch');
    const totalCount = document.getElementById('draftTotalCount');
    if (!container) return;

    let drafts = [];
    try {
        drafts = JSON.parse(localStorage.getItem('mathbank_local_drafts') || '[]');
    } catch(e) { drafts = []; }

    // 搜索过滤
    const q = searchInput ? searchInput.value.trim().toLowerCase() : '';
    if (q) {
        drafts = drafts.filter(item => {
            return (item.content || '').toLowerCase().includes(q) ||
                   (item.source || '').toLowerCase().includes(q) ||
                   (item.category_chapter || '').toLowerCase().includes(q) ||
                   (item.tags || '').toLowerCase().includes(q);
        });
    }

    // 按时间倒序
    drafts.sort((a, b) => {
        const dateA = a.updated_at ? new Date(a.updated_at).getTime() : 0;
        const dateB = b.updated_at ? new Date(b.updated_at).getTime() : 0;
        return dateB - dateA;
    });

    if (totalCount) totalCount.textContent = `(${drafts.length})`;

    if (drafts.length === 0) {
        container.innerHTML = `
            <div class="col-span-full p-12 text-center text-slate-400">
                <i class="fa-solid fa-box-open text-4xl mb-3 text-slate-300"></i>
                <p class="text-sm">草稿箱空空如也</p>
                <p class="text-xs mt-1 text-slate-300">录题时未保存的题目会自动暂存到这里</p>
            </div>`;
        return;
    }

    container.innerHTML = '';
    drafts.forEach(item => {
        const typeMap = {single_choice:'单选题', multi_choice:'多选题', fill_in_blank:'填空题', detailed_answer:'解答题', unknown:'未知'};
        const diffMap = {basic:'L1 概念定义', medium:'L2 基本运算', hard:'L3 性质应用', novel:'L4 综合拓展'};
        const typeText = typeMap[item.question_type] || '未知';
        const diffText = diffMap[item.difficulty] || item.difficulty || '';
        const timeText = item.updated_at ? new Date(item.updated_at).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '';
        const preview = (item.content || '').replace(/\$[^$]*\$/g, '[公式]').replace(/\n/g, ' ').substring(0, 100);

        const card = document.createElement('div');
        card.className = 'bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 p-4 hover:shadow-lg hover:border-brand-400 cursor-pointer transition-all duration-200 group';
        card.innerHTML = `
            <div class="flex items-start justify-between mb-2">
                <div class="flex items-center gap-2">
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">${typeText}</span>
                    ${diffText ? `<span class="text-[10px] font-bold px-2 py-0.5 rounded bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">${diffText}</span>` : ''}
                </div>
                <button class="delete-draft-btn text-slate-300 hover:text-red-500 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity" title="删除草稿">
                    <i class="fa-solid fa-trash-can text-xs"></i>
                </button>
            </div>
            <div class="text-sm text-slate-700 dark:text-slate-200 leading-relaxed line-clamp-3 mb-3">${preview || '[未填题干]'}</div>
            <div class="flex items-center justify-between text-[11px] text-slate-400 border-t border-slate-100 dark:border-slate-700 pt-2">
                <span class="truncate max-w-[150px]"><i class="fa-solid fa-folder mr-1"></i>${item.category_knowledge || item.category_chapter || '未分类'}</span>
                <span class="font-mono">${timeText}</span>
            </div>
            <div class="mt-2 text-[11px] text-brand-500 font-medium opacity-0 group-hover:opacity-100 transition-opacity">
                <i class="fa-solid fa-pen-to-square mr-1"></i>单击继续编辑
            </div>
        `;

        // 删除按钮
        const delBtn = card.querySelector('.delete-draft-btn');
        if (delBtn) {
            delBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (confirm('确认删除这个草稿吗？')) {
                    let allDrafts = [];
                    try { allDrafts = JSON.parse(localStorage.getItem('mathbank_local_drafts') || '[]'); } catch(err) {}
                    allDrafts = allDrafts.filter(d => d.id !== item.id);
                    localStorage.setItem('mathbank_local_drafts', JSON.stringify(allDrafts));
                    window.renderDraftList();
                }
            });
        }

        // 单击跳转录题界面并加载草稿
        card.addEventListener('click', () => {
            window.selectWorkspace('bank', '录题工作台');
            setTimeout(() => {
                if (typeof window.selectDraft === 'function') {
                    window.selectDraft(item);
                } else if (typeof selectDraft === 'function') {
                    selectDraft(item);
                }
            }, 300);
        });

        container.appendChild(card);
    });
};

// 清空所有草稿
// 清空所有草稿
window.clearAllDrafts = function() {
    if (!confirm('确认清空所有草稿吗？此操作无法撤销。')) return;
    localStorage.removeItem('mathbank_local_drafts');
    window.renderDraftList();
};

// 草稿箱搜索框监听
document.addEventListener('DOMContentLoaded', function() {
    const draftSearch = document.getElementById('draftSearch');
    if (draftSearch) {
        draftSearch.addEventListener('input', function() {
            if (typeof window.renderDraftList === 'function') {
                window.renderDraftList();
            }
        });
    }
});


// ===== 来源智能联想 =====
// 从后端拉 question_sources 里已用过的年份/学校/考试类型/卷名，填充全局 datalist，
// 录题与拆卷的来源输入框输入前缀时浏览器自动弹出候选。
window.loadSourceSuggestions = function(force) {
    if (!force && window.__srcSuggestLoaded) return;
    fetch('/api/source-suggestions')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data || data.detail) return;
            window.__srcSuggestLoaded = true;
            var map = {
                dlSrcYears: data.years || [],
                dlSrcExamTypes: data.exam_types || [],
                dlSrcPaperTitles: data.paper_titles || []
            };
            Object.keys(map).forEach(function(listId) {
                var dl = document.getElementById(listId);
                if (!dl) return;
                dl.innerHTML = '';
                (map[listId] || []).forEach(function(v) {
                    if (!v) return;
                    var opt = document.createElement('option');
                    opt.value = v;
                    dl.appendChild(opt);
                });
            });
        })
        .catch(function() { /* 静默失败，不影响录入 */ });
};

// 页面加载后静默拉一次来源联想候选（不 force，只拉一次）
document.addEventListener('DOMContentLoaded', function() {
    if (typeof window.loadSourceSuggestions === 'function') {
        window.loadSourceSuggestions(false);
    }
});

// ── 公式结构自检（KaTeX 严格模式）────────────────────────────────────────
// 拆题卡片预览用 throwOnError:false 渲染，公式写坏了只会「看着像一段普通文字」，
// 既不报错也不提示，要到组卷或 XeLaTeX 编译才暴露——那时已经白花钱解析过一次。
// 这里给每道题补一次严格模式检测，只打角标、绝不阻断导入。
//
// ⚠️ 误报比漏报更糟：用户被无关红角标轰炸几次之后就会彻底无视它，这个功能就死了。
// 所以两条降噪规则是硬要求：
//   1) 自有宏白名单——存储链会往正文里写 \fillin / \paren，KaTeX 一律不认，
//      不放行则**每一道填空题**都亮红角标；
//   2) 「未定义命令」只在成对出现（>=2）时才上报，因为单个未知命令多半是我们的宏
//      或存量自定义写法，而 `\dfrac`+`\frac` 这种混写才是模型真写坏了。
//      括号/环境不闭合那类结构性报错不受此限制，它们没有合法的「单次出现」形态。
window.MathBankFormulaReview = (() => {
    const OUR_MACROS = new Set([
        'fillin', 'paren', 'quad', 'qquad', 'hspace', 'vphantom', 'strut',
        'noindent', 'textrm', 'text', 'mbox', 'arabic', 'label', 'ref'
    ]);
    // 定界符按「长优先」匹配，否则 `$$…$$` 会被 `$…$` 抢先切碎。
    const SPAN_RE = /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g;
    const STRUCTURAL_RE = /Unexpected end of input|Expected group|Missing end|extra \}|Mismatched/;

    function mathSpans(value) {
        const spans = [];
        const text = String(value || '');
        SPAN_RE.lastIndex = 0;
        let match;
        while ((match = SPAN_RE.exec(text))) {
            const inner = [match[1], match[2], match[3], match[4]].find(v => v !== undefined);
            if (inner !== undefined) spans.push(inner);
        }
        return spans;
    }

    function strictError(span) {
        try {
            window.katex.renderToString(span, { throwOnError: true });
            return '';
        } catch (error) {
            return String((error && error.message) || error || '').split('\n')[0];
        }
    }

    function isStructural(span, message) {
        if (!message) return false;
        if (!/Undefined control sequence/.test(message)) return STRUCTURAL_RE.test(message);
        // 只数真正的反斜杠命令：`\\` 是换行，长度偶数的转义序列不算命令名。
        const commands = (span.match(/\\+([A-Za-z]+)/g) || []).filter(raw => raw.length % 2 === 1);
        const unknown = commands.filter(raw => !OUR_MACROS.has(raw.slice(1)));
        return unknown.length >= 2;
    }

    // 禁用后行断言（旧 Safari / 移动端 WebView 会 SyntaxError 中断整个级联加载），
    // 所以数「未转义的 $」只能自己扫：连续反斜杠把下一个 `$` 转义掉时不计入。
    function countUnescapedDollars(value) {
        let count = 0;
        let backslashes = 0;
        for (const ch of String(value || '')) {
            if (ch === '\\') {
                backslashes += 1;
                continue;
            }
            if (ch === '$' && backslashes % 2 === 0) count += 1;
            backslashes = 0;
        }
        return count;
    }

    /**
     * 返回本题「公式结构待核对」的理由数组（最多 3 条，供角标 title 使用）。
     * 空数组表示没问题或未安装 KaTeX——检测失败必须安静放行，不得变成新的报错源。
     */
    function inspect() {
        const reasons = [];
        const texts = Array.prototype.slice.call(arguments);
        // 必须查 `window.katex`：裸标识符在严格模式下会先抛 ReferenceError，被调用点的
        // try/catch 吞成「没问题」——实测踩过，整个自检静默空转且没有任何报错。
        if (!window.katex || typeof window.katex.renderToString !== 'function') {
            return reasons;
        }
        const joined = texts.map(t => String(t || '')).join('\n');
        if (!joined.trim()) return reasons;

        const seen = new Set();
        mathSpans(joined).forEach(span => {
            const message = strictError(span);
            if (!message || !isStructural(span, message)) return;
            const label = span.length > 28 ? span.slice(0, 28) + '…' : span;
            const key = label + '|' + message;
            if (seen.has(key)) return;
            seen.add(key);
            reasons.push('$' + label + '$ → ' + message.replace('KaTeX parse error: ', ''));
        });

        // 定界符落单：奇数个未转义 `$` 意味着模型把一对美元符号写丢了，此时上面的
        // 逐片段检测根本看不到损坏（切片切不出完整公式），只能靠计数发现。
        const dollars = countUnescapedDollars(joined);
        if (dollars % 2 === 1) {
            reasons.push('数学定界符 $ 数量为奇数，公式边界已断裂');
        }
        return reasons.slice(0, 3);
    }

    return { inspect: inspect, mathSpans: mathSpans };
})();
