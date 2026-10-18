"""前端 Node DOM 桩共用的「弹窗过渡助手」同步替身。

static/js/api.js 里的 openModalWithTransition / closeModalWithTransition /
revealModalWithTransition 是全站「fixed inset-0 + opacity-0」全屏遮罩弹窗的统一开关
（根因来自「+ 添加厂商」整页假死事故：打开时只 remove('hidden') 却没清 opacity-0，
遮罩隐形却吞掉全屏所有点击）。真实实现要下一帧才揭示，而各前端桩测试里没有可用的
requestAnimationFrame/定时器，断言又必须在「打开」调用之后立刻检查可见性，
所以桩一律同步切换 hidden/flex/opacity-0，其余契约（入栈、surface 缩放）保持一致。

用法：把 MODAL_TRANSITION_STUB_JS 拼到桩脚本里 window 替身声明之后即可。
"""

MODAL_TRANSITION_STUB_JS = r"""
(function () {
    // 与 static/js/api.js 的公共助手同名同契约，但**同步**清掉 opacity-0：
    // Node 桩里没有真实的 requestAnimationFrame/定时器，异步揭示会让
    // 「打开后立即可见」的断言永远对不上。
    function resolve(modalOrId) {
        if (typeof modalOrId === 'string') {
            return (typeof document !== 'undefined' && document.getElementById)
                ? document.getElementById(modalOrId)
                : null;
        }
        return modalOrId || null;
    }
    function findSurface(modal) {
        if (!modal || typeof modal.querySelector !== 'function') return null;
        try {
            return modal.querySelector('[data-modal-surface]') || modal.querySelector('.glass-modal');
        } catch (err) {
            return null;
        }
    }
    function reveal(modal) {
        if (!modal || !modal.classList) return;
        var surface = modal.__transitionSurface || findSurface(modal);
        modal.__transitionSurface = surface;
        modal.classList.remove('opacity-0');
        if (surface && surface.classList) {
            surface.classList.remove('scale-95');
            surface.classList.add('scale-100');
        }
    }
    window.revealModalWithTransition = reveal;
    window.openModalWithTransition = function (modalOrId, options) {
        var modal = resolve(modalOrId);
        if (!modal) return null;
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        if (options && options.surface && options.surface.classList) {
            modal.__transitionSurface = options.surface;
        }
        if (window.MathBankModal) window.MathBankModal.open(modal, options || {});
        reveal(modal);
        return modal;
    };
    window.closeModalWithTransition = function (modalOrId) {
        var modal = resolve(modalOrId);
        if (!modal) return;
        if (window.MathBankModal) window.MathBankModal.close(modal);
        modal.classList.add('opacity-0');
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    };
})();
"""


def as_js_literal() -> str:
    """把替身包成 JS 字符串字面量，供 vm 沙箱拼进待执行脚本时使用。"""
    return repr(MODAL_TRANSITION_STUB_JS)
