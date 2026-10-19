/**
 * tailwind_gate.js - 给 Tailwind Play CDN 的全站 DOM 观察器加一道「有界闸门」
 *
 * 为什么必须有它：/static/lib/tailwind/tailwind.min.js 是 Play CDN 的本地副本，
 * 它会在 documentElement 上挂一个 MutationObserver（attributes: class + childList + subtree），
 * 之后**任何一次 DOM 变更**都要把整棵文档重扫一遍来生成 CSS。组卷页题库流改成虚拟滚动之后
 * 每帧都在挂行/摘行，10,411 题实测：光是这个重扫就占主线程 self-time 的 23%
 * （751ms / 3.3s 采样），p95 帧时长 183ms；而且页面基础 DOM 有 1.8 万个节点，
 * 重扫成本还随它线性放大——这正是「虚拟滚动省下的 DOM，被样式引擎又赚回去了」。
 *
 * 闸门的做法：把 MutationObserver 包一层，只登记「挂在 documentElement 上、
 * 且盯着 class / childList」的那类观察器（KaTeX 和业务代码自己的观察器一概不动）。
 * 滚动或批量水合期间 disconnect，停下之后重新 observe，并用一次 class 增删补回
 * 漏掉的样式——增删 class 正是 CDN 认定的「该重扫了」信号，不留任何痕迹。
 *
 * 暂停时长有硬上限（调用方负责），而且虚拟行的类名全部来自同一套模板：首屏渲染过一次
 * 之后就不再产生新类名，暂停期内不会出现「没样式的卡片」。
 *
 * 加载位置是硬要求：**必须在 tailwind.min.js 之前**，它要在 CDN 构造观察器之前把
 * MutationObserver 换掉。晚于 CDN 加载时 watched 为空，pause/resume 自动变成空操作。
 */

(function () {
    'use strict';

    const NativeObserver = window.MutationObserver;
    if (!NativeObserver) return;

    const watched = [];
    let paused = false;

    function isDomScanner(target, options) {
        if (!target || target !== document.documentElement) return false;
        if (!options || !options.subtree || !options.attributes) return false;
        const filter = options.attributeFilter;
        // 只认 Tailwind Play CDN 的精确签名：整棵子树 + class 属性。
        // 别的 documentElement 级观察器一律不关——尤其 api.js 里同步按钮无障碍态的那个
        // （盯的是 disabled）：它没有「开闸补扫」这一步，被关掉就会真的漏同步。
        return Boolean(filter) && filter.indexOf('class') !== -1;
    }

    function dropFor(observer) {
        for (let index = watched.length - 1; index >= 0; index -= 1) {
            if (watched[index].observer === observer) watched.splice(index, 1);
        }
    }

    function flushStyles() {
        const root = document.documentElement;
        root.classList.add('tw-gate-flush');
        root.classList.remove('tw-gate-flush');
    }

    const GatedObserver = function (callback) {
        const observer = new NativeObserver(callback);
        const originalObserve = observer.observe.bind(observer);
        const originalDisconnect = observer.disconnect.bind(observer);

        observer.observe = function (target, options) {
            if (!isDomScanner(target, options)) return originalObserve(target, options);
            // 闸门关着时新建的全站观察器先别启动，等 resume 再挂上，
            // 否则「暂停」会被后续 new 出来的观察器悄悄绕过。
            const handle = {
                observer: observer,
                target: target,
                options: options,
                start: originalObserve,
                // 暂停时必须走原生 disconnect：包装过的那一个会把这条登记一起删掉，
                // 于是 resume 再无对象可重挂——表现是 CDN 的重扫被永久关停，样式静默缺失，
                // 而压测数字好看得离谱。这个坑真踩过一次，留着注释当防复发钉。
                stop: originalDisconnect,
                active: !paused
            };
            watched.push(handle);
            if (handle.active) originalObserve(target, options);
            return undefined;
        };
        observer.disconnect = function () {
            dropFor(observer);
            return originalDisconnect();
        };
        return observer;
    };
    GatedObserver.prototype = NativeObserver.prototype;

    window.MutationObserver = GatedObserver;

    window.MathBankTailwindGate = {
        // 压测与排障用：watcherCount 为 0 说明加载顺序错了（闸门没抢到 CDN 前面）。
        watcherCount: function () { return watched.length; },
        isPaused: function () { return paused; },
        pause: function () {
            if (paused) return false;
            paused = true;
            for (let index = 0; index < watched.length; index += 1) {
                if (!watched[index].active) continue;
                watched[index].active = false;
                try { watched[index].stop(); } catch (e) { }
            }
            return true;
        },
        resume: function (options) {
            if (!paused) return false;
            paused = false;
            for (let index = 0; index < watched.length; index += 1) {
                const handle = watched[index];
                if (handle.active) continue;
                handle.active = true;
                try { handle.start(handle.target, handle.options); } catch (e) { }
            }
            if (options && options.flush === false) return true;
            flushStyles();
            return true;
        }
    };
})();
