/** Resize the mobile workspace without changing its active tool or result. */
(() => {
    'use strict';
    window.AppModules = window.AppModules || {};

    const positions = ['peek', 'half', 'full'];
    const movementThreshold = 6;
    const snapThreshold = 32;
    let panel, handle, content, mobile, resize, userReveal, resizeFrame, gesture;
    let skipClick = false;

    function isPeek() {
        return Boolean(mobile?.matches && panel?.dataset.position === 'peek');
    }

    function notifyResize() {
        if (!resize || resizeFrame) return;
        resizeFrame = window.requestAnimationFrame(() => {
            resizeFrame = null;
            resize();
        });
    }

    function updateAccessibility() {
        const peek = isPeek();
        if (peek && content.contains(document.activeElement)) handle.focus({ preventScroll: true });
        content.inert = peek;
        handle.setAttribute('aria-expanded', String(!peek));
        handle.setAttribute('aria-label', peek ? '하단 패널 펼치기' : '하단 패널 접기');
    }

    function cancelGesture() {
        if (!gesture) return;
        const { pointerId, moved } = gesture;
        gesture = null;
        if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
        if (moved) {
            skipClick = true;
            delete panel.dataset.dragging;
            panel.style.removeProperty('--sheet-drag-height');
            notifyResize();
        }
    }

    function setPosition(position) {
        if (!panel || !positions.includes(position)) return;
        cancelGesture();
        if (panel.dataset.position === position) return;
        panel.dataset.position = position;
        updateAccessibility();
        notifyResize();
    }

    function reveal(position = 'half') {
        const target = positions.indexOf(position);
        if (panel && target > positions.indexOf(panel.dataset.position)) setPosition(position);
    }

    function choosePosition(position) {
        const opens = isPeek() && position !== 'peek';
        setPosition(position);
        if (opens) userReveal?.(position);
    }

    function snapHeights() {
        // Resolve CSS lengths containing viewport units or clamp() in the sheet's context.
        const ruler = document.createElement('div');
        ruler.setAttribute('aria-hidden', 'true');
        Object.assign(ruler.style, { position: 'absolute', top: '0', width: '0', visibility: 'hidden', pointerEvents: 'none' });
        panel.appendChild(ruler);
        const heights = positions.map(position => {
            ruler.style.height = `var(--sheet-${position}-height)`;
            return ruler.getBoundingClientRect().height;
        });
        ruler.remove();
        return heights;
    }

    function startDrag(event) {
        if (!mobile.matches || gesture || event.button !== 0 || !event.isPrimary) return;
        skipClick = false;
        gesture = {
            pointerId: event.pointerId,
            startY: event.clientY,
            startHeight: panel.getBoundingClientRect().height,
            startPosition: positions.indexOf(panel.dataset.position),
            heights: snapHeights(),
            moved: false,
        };
        try { handle.setPointerCapture(event.pointerId); }
        catch (error) { if (error.name !== 'NotFoundError') throw error; }
    }

    function heightAt(delta) {
        const { startHeight, heights } = gesture;
        return Math.max(heights[0], Math.min(heights.at(-1), startHeight + delta));
    }

    function moveDrag(event) {
        if (gesture?.pointerId !== event.pointerId) return;
        const delta = gesture.startY - event.clientY;
        if (!gesture.moved && Math.abs(delta) < movementThreshold) return;
        gesture.moved = true;
        panel.dataset.dragging = 'true';
        panel.style.setProperty('--sheet-drag-height', `${heightAt(delta)}px`);
        notifyResize();
    }

    function finishDrag(event) {
        if (gesture?.pointerId !== event.pointerId) return;
        if (!gesture.moved) {
            cancelGesture();
            return;
        }
        const { heights, startY, startPosition } = gesture;
        const delta = startY - event.clientY;
        const height = heightAt(delta);
        let target = startPosition;
        heights.forEach((snap, index) => {
            if (Math.abs(snap - height) < Math.abs(heights[target] - height)) target = index;
        });
        if (target === startPosition && Math.abs(delta) >= snapThreshold) target += Math.sign(delta);
        cancelGesture();
        choosePosition(positions[Math.max(0, Math.min(positions.length - 1, target))]);
    }

    function handleClick(event) {
        if (!mobile.matches) return;
        if (skipClick && event.detail > 0) {
            skipClick = false;
            return;
        }
        choosePosition(isPeek() ? 'half' : 'peek');
    }

    function handleKey(event) {
        if (!mobile.matches) return;
        const index = positions.indexOf(panel.dataset.position);
        const targets = { ArrowUp: index + 1, ArrowDown: index - 1, Home: 0, End: positions.length - 1 };
        if (!Object.hasOwn(targets, event.key)) return;
        event.preventDefault();
        choosePosition(positions[Math.max(0, Math.min(positions.length - 1, targets[event.key]))]);
    }

    function onViewportChange() {
        cancelGesture();
        updateAccessibility();
        notifyResize();
    }

    function init({ onResize, onReveal } = {}) {
        if (typeof onResize === 'function') resize = onResize;
        if (typeof onReveal === 'function') userReveal = onReveal;
        if (panel) return;
        const workspace = document.getElementById('workspace-panel');
        handle = document.getElementById('sheet-handle');
        content = document.getElementById('workspace-content');
        if (!workspace || !handle || !content) return;
        panel = workspace;
        mobile = window.matchMedia('(max-width: 799px)');
        if (!positions.includes(panel.dataset.position)) panel.dataset.position = 'half';
        handle.addEventListener('pointerdown', startDrag);
        handle.addEventListener('pointermove', moveDrag);
        handle.addEventListener('pointerup', finishDrag);
        ['pointercancel', 'lostpointercapture'].forEach(type => handle.addEventListener(type, event => {
            if (gesture?.pointerId === event.pointerId) cancelGesture();
        }));
        handle.addEventListener('click', handleClick);
        handle.addEventListener('keydown', handleKey);
        mobile.addEventListener('change', onViewportChange);
        window.addEventListener('resize', onViewportChange);
        window.visualViewport?.addEventListener('resize', onViewportChange);
        updateAccessibility();
    }

    window.AppModules.workspaceSheet = { init, setPosition, reveal, isPeek };
})();
