/** A taxi estimate stays separate from transit results and public meeting places. */
(function() {
    let initialized = false;
    let context = null;
    let expiryTimer = null;
    const byId = function(id) { return document.getElementById(id); };
    const money = function(value) { return Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('ko-KR') + '원' : '조회 불가'; };

    function invalidateRoomForm() {
        if (window.AppModules.rideRooms) window.AppModules.rideRooms.invalidateSearchContext();
    }

    function reset() {
        clearTimeout(expiryTimer);
        expiryTimer = null;
        context = null;
        const section = byId('taxi-section');
        if (section) section.hidden = true;
        const body = byId('taxi-content');
        if (body) body.replaceChildren();
        invalidateRoomForm();
    }

    function text(tag, value, className) {
        const node = document.createElement(tag);
        node.textContent = value;
        if (className) node.className = className;
        return node;
    }

    function prepare(nextContext) {
        clearTimeout(expiryTimer);
        context = nextContext;
        const section = byId('taxi-section');
        const heading = byId('taxi-route');
        if (section) section.hidden = false;
        if (heading) heading.textContent = nextContext.payload.location + ' → ' + nextContext.payload.dest_name;
        const refresh = byId('taxi-refresh');
        if (refresh) refresh.disabled = false;
    }

    function loading(nextContext) {
        prepare(nextContext);
        const body = byId('taxi-content');
        if (body) body.replaceChildren(text('p', '택시 시간과 요금을 확인하고 있어요…', 'taxi-status'));
        const refresh = byId('taxi-refresh');
        if (refresh) refresh.disabled = true;
        const section = byId('taxi-section');
        if (section) section.setAttribute('aria-busy', 'true');
    }

    function failed(message, nextContext) {
        prepare(nextContext);
        const body = byId('taxi-content');
        if (body) body.replaceChildren(text('p', message || '택시 정보를 확인하지 못했어요.', 'taxi-status taxi-error'));
        const section = byId('taxi-section');
        if (section) section.setAttribute('aria-busy', 'false');
    }

    function render(estimate, nextContext) {
        prepare(nextContext);
        const section = byId('taxi-section');
        if (section) section.setAttribute('aria-busy', 'false');
        const body = byId('taxi-content');
        if (!body) return;
        const seconds = estimate.ride_duration_seconds;
        const amount = estimate.fare && estimate.fare.amount_krw;
        const duration = typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
            ? '택시 이동 약 ' + Math.ceil(seconds / 60) + '분' : '택시 주행시간 조회 불가';
        const nodes = [text('p', duration, 'taxi-duration'),
            text('p', Number.isSafeInteger(amount) && amount >= 0 ? '예상요금 약 ' + money(amount) : '요금 조회 불가', 'taxi-fare')];
        if (typeof estimate.distance_m === 'number' && Number.isFinite(estimate.distance_m)) {
            nodes.push(text('p', '주행거리 약 ' + (estimate.distance_m / 1000).toFixed(1) + 'km', 'taxi-caption'));
        }
        const at = new Date(estimate.fetched_at);
        if (Number.isFinite(at.getTime())) nodes.push(text('p', '조회 ' + new Intl.DateTimeFormat('ko-KR', {
            timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit'
        }).format(at) + ' · 현재 조회 기준 예상', 'taxi-caption'));
        const splits = document.createElement('ul');
        splits.className = 'taxi-splits';
        [2, 3, 4].forEach(function(count) {
            const preview = Array.isArray(estimate.split_preview)
                ? estimate.split_preview.find(function(item) { return item.count === count; }) : null;
            const amounts = preview && preview.amounts_krw;
            const valid = Array.isArray(amounts) && amounts.length === count && amounts.every(function(value) {
                return Number.isSafeInteger(value) && value >= 0;
            });
            splits.appendChild(text('li', count + '명 가정 · ' + (valid ? amounts.map(money).join(' / ') : '분담금 계산 불가')));
        });
        nodes.push(splits);
        const warnings = Array.isArray(estimate.warnings) ? estimate.warnings.filter(function(value) { return typeof value === 'string'; }) : [];
        warnings.forEach(function(value) { nodes.push(text('p', value, 'taxi-caption')); });
        body.replaceChildren(...nodes);
        const fetched = at.getTime();
        const age = typeof estimate.cache_age_seconds === 'number' ? Math.max(0, estimate.cache_age_seconds) * 1000 : 0;
        const ttl = typeof estimate.cache_ttl_seconds === 'number' ? estimate.cache_ttl_seconds * 1000 : 60000;
        const explicit = new Date(estimate.expires_at).getTime();
        const expires = Number.isFinite(explicit) ? explicit : fetched + ttl;
        const remaining = Math.min(ttl - age, Number.isFinite(expires) ? expires - Date.now() : ttl);
        expiryTimer = setTimeout(function() {
            if (context !== nextContext || !nextContext.isCurrent()) return;
            body.replaceChildren(text('p', '견적이 만료됐어요. 시간·요금을 갱신해 주세요.', 'taxi-status'));
        }, Math.max(0, remaining));
    }

    function init() {
        if (initialized) return;
        initialized = true;
        const refresh = byId('taxi-refresh');
        if (refresh) refresh.addEventListener('click', function() {
            if (context && context.isCurrent()) window.AppModules.api.refreshTaxi();
        });
        const find = byId('taxi-find-room');
        if (find) find.addEventListener('click', function() {
            if (context && context.isCurrent() && window.AppModules.rideRooms) {
                window.AppModules.rideRooms.openList(context.payload.dest_name);
            }
        });
        const create = byId('taxi-create-room');
        if (create) create.addEventListener('click', function() {
            if (context && context.isCurrent() && window.AppModules.rideRooms) window.AppModules.rideRooms.openCreate(context);
        });
    }

    window.AppModules.taxi = { init: init, loading: loading, render: render, failed: failed, reset: reset };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
