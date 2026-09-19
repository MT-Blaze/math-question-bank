// 筛选面板独立化
(function() {
    'use strict';

    let filterPanelOpen = false;

    window.toggleFilterPanel = function() {
        const panel = document.getElementById('filterPanel');
        const overlay = document.getElementById('filterPanelOverlay');
        if (!panel || !overlay) return;

        filterPanelOpen = !filterPanelOpen;
        if (filterPanelOpen) {
            panel.classList.remove('-translate-x-full');
            overlay.classList.remove('hidden');
            // 移动筛选内容到面板
            moveFiltersToPanel();
        } else {
            panel.classList.add('-translate-x-full');
            overlay.classList.add('hidden');
        }
    };

    window.resetFilters = function() {
        // 重置所有筛选条件
        const selects = ['filterType', 'filterApplicableGrade', 'filterDifficulty', 'filterPaperCategory', 'filterCompulsory', 'filterChapter', 'filterKnowledge', 'filterSource', 'filterSort'];
        selects.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.value = '';
        });
        const checkbox = document.getElementById('filterIncludeHigherGrades');
        if (checkbox) checkbox.checked = false;
        const search = document.getElementById('searchInput');
        if (search) search.value = '';
        // 触发筛选
        if (window.applyFilters) window.applyFilters();
        updateFilterCount();
    };

    function moveFiltersToPanel() {
        const panelContent = document.getElementById('filterPanelContent');
        const sidebarFilters = document.querySelector('#sidebarTopPanel .p-3.border-b');
        if (!panelContent || !sidebarFilters || panelContent.children.length > 0) return;

        // 复制筛选内容到面板
        panelContent.innerHTML = sidebarFilters.innerHTML;
        // 隐藏原侧边栏筛选（保留占位但不显示）
        sidebarFilters.style.display = 'none';

        // 重新绑定事件（简单起见，触发change事件）
        panelContent.querySelectorAll('select, input').forEach(el => {
            el.addEventListener('change', () => {
                // 同步到原侧边栏的对应元素
                const original = document.getElementById(el.id);
                if (original && original !== el) {
                    original.value = el.value;
                    original.dispatchEvent(new Event('change'));
                }
                updateFilterCount();
            });
            el.addEventListener('input', () => {
                const original = document.getElementById(el.id);
                if (original && original !== el) {
                    original.value = el.value;
                    original.dispatchEvent(new Event('input'));
                }
            });
        });
    }

    function updateFilterCount() {
        const countEl = document.getElementById('filterActiveCount');
        if (!countEl) return;
        let count = 0;
        const selects = ['filterType', 'filterApplicableGrade', 'filterDifficulty', 'filterPaperCategory', 'filterCompulsory', 'filterChapter', 'filterKnowledge'];
        selects.forEach(id => {
            const el = document.getElementById(id);
            if (el && el.value) count++;
        });
        const search = document.getElementById('searchInput');
        if (search && search.value.trim()) count++;
        if (count > 0) {
            countEl.textContent = count;
            countEl.classList.remove('hidden');
        } else {
            countEl.classList.add('hidden');
        }
    }

    // 页面加载后更新筛选计数
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', updateFilterCount);
    } else {
        updateFilterCount();
    }
})();
