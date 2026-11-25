/** Map point selection and widget movement; results use the shared route workflow. */
(() => {
    'use strict';
    let initialized = false, origin = null, destination = null, armed = null;
    let schoolPlaceCallback = null;
    let requestVersion = 0;
    let widgetMoved = false, drag = null;
    const byId = id => document.getElementById(id);
    const modules = () => window.AppModules;
    const valid = point => point && window.AppState.isValidPosition(point.lat, point.lon);
    function status(message, failed = false) {
        const target = byId('map-journey-status');
        if (target) { target.textContent = message; target.classList.toggle('status-error', failed); }
    }

    function renderSelection() {
        const ready = modules().map?.isReady() === true;
        ['origin', 'destination'].forEach(kind => {
            const point = kind === 'origin' ? origin : destination;
            const button = byId('map-pick-' + kind), label = byId('map-' + kind + '-label');
            if (button) {
                button.disabled = !ready;
                button.setAttribute('aria-pressed', String(armed === kind));
                button.title = ready ? (kind === 'origin' ? '출발' : '도착') + ' 위치를 지도에서 선택' : '지도 연결을 기다리고 있어요';
            }
            if (label) label.textContent = point ? (kind === 'origin' ? '출발지' : '목적지') + ' 선택 완료' : '지도에서 선택';
        });
        byId('map-stage')?.classList.toggle('is-map-picking', Boolean(armed));
    }

    function invalidate() { requestVersion++; }

    function showPanel(visible, moveFocus = false) {
        const launcher = byId('map-journey-toggle'), widget = byId('map-journey-widget');
        if (!launcher || !widget) return;
        const focusInside = widget.contains(document.activeElement);
        if (visible) {
            modules().commuteAdvice?.closePanel?.();
            modules().renderer?.closeStationInfo?.();
            modules().shell?.fitMapPanel?.();
        } else if (armed) {
            armed = null;
            schoolPlaceCallback = null;
            renderSelection();
            status('출발·도착지를 지도에서 고르면 자동으로 길을 찾아요.');
        }
        widget.hidden = !visible;
        launcher.setAttribute('aria-expanded', String(visible));
        if (visible) constrainWidget();
        if (moveFocus || (!visible && focusInside)) {
            (visible ? widget.querySelector('.map-journey-heading') : launcher)?.focus({ preventScroll: true });
        }
    }

    function reset(options = {}) {
        invalidate();
        armed = null;
        schoolPlaceCallback = null;
        if (options.clearSelection !== false) { origin = null; destination = null; }
        modules().map?.setSelectionPoints(null, null);
        renderSelection();
        status('출발·도착지를 지도에서 고르면 자동으로 길을 찾아요.');
    }

    function armSelection(kind, callback = null) {
        if (!['origin', 'destination'].includes(kind)) return;
        if (!modules().map?.isReady()) {
            status('지도가 연결되면 출발지와 도착지를 선택할 수 있어요.');
            return;
        }
        modules().api?.cancelPendingRequests({ keepMapJourney: true });
        invalidate();
        armed = kind;
        schoolPlaceCallback = callback;
        modules().map.setSelectionPoints(origin, destination);
        modules().shell?.showMap();
        showPanel(true);
        renderSelection();
        status((kind === 'origin' ? '출발할' : '도착할') + ' 곳을 지도에서 누르세요. Esc로 선택 취소.');
        return true;
    }

    function pickPoint(lat, lon) {
        if (!armed || !window.AppState.isValidPosition(lat, lon)) return false;
        invalidate();
        const point = { lat: Number(lat), lon: Number(lon), name: armed === 'origin' ? '지도에서 선택한 출발지' : '지도에서 선택한 도착지' };
        if (schoolPlaceCallback) {
            const callback = schoolPlaceCallback;
            schoolPlaceCallback = null;
            armed = null;
            renderSelection();
            showPanel(false);
            callback(point);
            return true;
        }
        if (armed === 'origin') origin = point; else destination = point;
        armed = !origin ? 'origin' : !destination ? 'destination' : null;
        modules().map?.setSelectionPoints(origin, destination);
        renderSelection();
        if (armed) status((armed === 'origin' ? '출발할' : '도착할') + ' 곳도 지도에서 누르세요. 자동으로 길을 찾아요.');
        else retry();
        return true;
    }

    async function retry() {
        if (!valid(origin) || !valid(destination)) return;
        invalidate();
        armed = null;
        renderSelection();
        modules().map?.setSelectionPoints(origin, destination);
        const version = requestVersion;
        const payload = { campus: window.AppState.getSelectedCampus(), selection_source: 'map',
            location: origin.name, lat: origin.lat, lon: origin.lon,
            dest_name: destination.name, dest_lat: destination.lat, dest_lon: destination.lon };
        showPanel(false);
        modules().shell?.select('journey', { source: 'map' });
        status('경로를 조회하고 있어요. 결과 패널에서 확인해 주세요.');
        try {
            await modules().api.findRoutesFromMap(payload);
            if (version === requestVersion) status('결과 패널에서 경로를 확인하거나 지점을 다시 선택해 주세요.');
        } catch (_) {
            if (version === requestVersion) status('경로를 조회하지 못했어요. 지점을 다시 선택해 주세요.', true);
        }
    }

    function onMapReady() {
        renderSelection();
        if (!origin && !destination) status('출발지를 누른 뒤 지도에서 위치를 선택해 보세요.');
    }

    function onMapUnavailable() {
        armed = null;
        schoolPlaceCallback = null;
        renderSelection();
        status('지도 연결 실패 · 기존 장소 검색으로 길찾기를 이용해 주세요.', true);
    }

    function moveWidget(left, top) {
        const widget = byId('map-journey-widget'), stage = byId('map-stage');
        const area = stage.getBoundingClientRect(), box = widget.getBoundingClientRect();
        const style = window.getComputedStyle?.(widget);
        const clearance = parseFloat(style?.getPropertyValue?.('--map-journey-weather-clearance')) || 0;
        const right = parseFloat(style?.getPropertyValue?.('--map-journey-right-clearance')) || 0;
        const bottom = parseFloat(style?.getPropertyValue?.('--map-journey-bottom-clearance')) || 0;
        const gap = 8;
        const maxLeft = Math.max(0, area.width - box.width - gap - right);
        const maxTop = Math.max(0, area.height - box.height - gap - bottom);
        const minLeft = Math.min(gap, maxLeft);
        const minTop = Math.min(Math.max(gap, clearance), maxTop);
        left = Math.max(minLeft, Math.min(maxLeft, left));
        top = Math.max(minTop, Math.min(maxTop, top));
        const weather = ['commute-advice-panel', 'commute-advice-details'].map(byId)
            .filter(panel => panel && !panel.hidden && window.getComputedStyle?.(panel)?.position === 'fixed')
            .map(panel => panel.getBoundingClientRect());
        const isClear = ([x, y]) => weather.every(bounds =>
            area.left + x + box.width <= bounds.left - gap || area.left + x >= bounds.right + gap
            || area.top + y + box.height <= bounds.top - gap || area.top + y >= bounds.bottom + gap);
        if (!isClear([left, top])) {
            const candidates = weather.flatMap(bounds => [
                [bounds.left - area.left - box.width - gap, top],
                [left, bounds.top - area.top - box.height - gap],
                [left, bounds.bottom - area.top + gap],
            ]).filter(([x, y]) => x >= minLeft && x <= maxLeft && y >= minTop && y <= maxTop)
                .filter(isClear);
            candidates.sort(([ax, ay], [bx, by]) =>
                Math.abs(ax - left) + Math.abs(ay - top) - Math.abs(bx - left) - Math.abs(by - top));
            if (candidates.length) [left, top] = candidates[0];
        }
        widget.style.left = left + 'px';
        widget.style.top = top + 'px';
        widgetMoved = true;
    }

    function constrainWidget() {
        const widget = byId('map-journey-widget');
        if (!widgetMoved || !widget || widget.hidden) return;
        const box = widget.getBoundingClientRect();
        const area = byId('map-stage').getBoundingClientRect();
        moveWidget(box.left - area.left, box.top - area.top);
    }

    function initWidgetMovement() {
        const widget = byId('map-journey-widget'), handle = widget.querySelector('.map-journey-heading');
        if (!handle) return;
        handle.setAttribute('tabindex', '0');
        handle.setAttribute('role', 'group');
        handle.setAttribute('aria-label', '길찾기 위젯 이동. 드래그 또는 방향키로 이동, Home 키로 원래 위치');
        handle.title = '제목 부분을 드래그해서 이동 · 방향키로 이동 · Home으로 원래 위치';

        function finishDrag(event) {
            if (!drag || (event && event.pointerId !== drag.id)) return;
            const id = drag.id;
            drag = null;
            widget.classList.remove('is-dragging');
            if (handle.hasPointerCapture?.(id)) handle.releasePointerCapture(id);
        }
        handle.addEventListener('pointerdown', event => {
            if (drag || event.isPrimary === false || (event.button !== undefined && event.button !== 0)
                || byId('map-journey-reset').contains(event.target) || byId('map-journey-close')?.contains(event.target)) return;
            const box = widget.getBoundingClientRect(), area = byId('map-stage').getBoundingClientRect();
            drag = { id: event.pointerId, x: event.clientX, y: event.clientY,
                left: box.left - area.left, top: box.top - area.top };
            widget.classList.add('is-dragging');
            handle.setPointerCapture?.(event.pointerId);
            handle.focus({ preventScroll: true });
            event.preventDefault();
            event.stopPropagation();
        });
        document.addEventListener('pointermove', event => {
            if (!drag || event.pointerId !== drag.id) return;
            if (widget.hidden) return finishDrag(event);
            moveWidget(drag.left + event.clientX - drag.x, drag.top + event.clientY - drag.y);
            event.preventDefault();
            event.stopPropagation();
        }, { passive: false });
        document.addEventListener('pointerup', finishDrag);
        document.addEventListener('pointercancel', finishDrag);
        handle.addEventListener('lostpointercapture', finishDrag);
        window.addEventListener('blur', () => finishDrag());
        handle.addEventListener('keydown', event => {
            if (event.target !== handle) return;
            const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
            if (event.key === 'Home') {
                widgetMoved = false;
                widget.style.left = '';
                widget.style.top = '';
            } else if (directions[event.key]) {
                const box = widget.getBoundingClientRect(), area = byId('map-stage').getBoundingClientRect();
                const step = event.shiftKey ? 30 : 10, delta = directions[event.key];
                moveWidget(box.left - area.left + delta[0] * step, box.top - area.top + delta[1] * step);
            } else return;
            event.preventDefault();
            event.stopPropagation();
        });
        window.addEventListener('resize', constrainWidget);
        if (typeof window.ResizeObserver === 'function') {
            const observer = new window.ResizeObserver(constrainWidget);
            observer.observe(byId('map-stage'));
            observer.observe(widget);
            ['commute-advice-panel', 'commute-advice-details'].map(byId).filter(Boolean)
                .forEach(panel => observer.observe(panel));
        }
    }

    function init() {
        const widget = byId('map-journey-widget'), launcher = byId('map-journey-toggle');
        if (initialized || !widget) return;
        initialized = true;
        initWidgetMovement();
        launcher?.addEventListener('click', () => showPanel(widget.hidden, true));
        byId('map-journey-close')?.addEventListener('click', () => showPanel(false, true));
        ['origin', 'destination'].forEach(kind => byId('map-pick-' + kind).addEventListener('click', () => armSelection(kind)));
        byId('map-journey-reset').addEventListener('click', () => {
            modules().api?.cancelPendingRequests({ keepMapJourney: true });
            reset();
        });
        document.addEventListener('keydown', event => {
            if (event.key !== 'Escape' || event.defaultPrevented) return;
            if (!armed) {
                if (launcher && !widget.hidden && (widget.contains(document.activeElement) || document.activeElement === launcher)) {
                    event.preventDefault();
                    showPanel(false, true);
                }
                return;
            }
            const fromSchoolForm = Boolean(schoolPlaceCallback);
            schoolPlaceCallback = null;
            armed = null;
            renderSelection();
            status('지도 선택을 취소했어요. 출발지 또는 도착지 버튼으로 다시 선택할 수 있어요.');
            event.preventDefault();
            if (fromSchoolForm) {
                showPanel(false);
                modules().shell?.openJourney('national-map-place');
            }
        });
        modules().shell?.onMenuSelect((_name, options) => {
            if (options?.source !== 'map') reset({ clearSelection: false });
        });
        renderSelection();
        if (!modules().map?.isReady()) status('지도가 연결되면 출발지와 도착지를 선택할 수 있어요.');
    }

    window.AppModules.mapJourney = { init, armSelection, armSchoolPlace: armSelection, pickPoint, reset, retry, onMapReady, onMapUnavailable,
        closePanel: () => showPanel(false),
        getSelection: () => ({ origin: origin ? { ...origin } : null, destination: destination ? { ...destination } : null, armed }) };
})();
