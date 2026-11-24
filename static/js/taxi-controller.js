/** A taxi estimate stays separate from transit results and public meeting places. */
(function() {
    let initialized = false;
    let context = null;
    let expiryTimer = null;
    let splitVersion = 0;
    const byId = function(id) { return document.getElementById(id); };
    const money = function(value) { return Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('ko-KR') + '원' : '조회 불가'; };
    const initialRoute = '길찾기에서 출발지와 도착할 장소를 선택해 주세요.';

    function setButton(label, disabled) {
        const refresh = byId('taxi-refresh');
        if (refresh) {
            refresh.textContent = label;
            refresh.disabled = Boolean(disabled);
            refresh.setAttribute('aria-busy', String(Boolean(disabled)));
        }
    }

    function invalidateRoomForm() {
        if (window.AppModules.rideRooms) window.AppModules.rideRooms.invalidateSearchContext();
    }

    function reset() {
        splitVersion += 1;
        clearTimeout(expiryTimer);
        expiryTimer = null;
        context = null;
        const section = byId('taxi-section');
        if (section) { section.hidden = false; section.setAttribute('aria-busy', 'false'); }
        const heading = byId('taxi-route');
        if (heading) heading.textContent = initialRoute;
        const body = byId('taxi-content');
        if (body) body.replaceChildren(text('p', '조회 버튼을 누르면 예상 시간·거리·요금과 2~4명 분담 금액을 확인할 수 있어요.', 'taxi-status'));
        setButton('택시 예상요금 조회', false);
        invalidateRoomForm();
    }

    function text(tag, value, className) {
        const node = document.createElement(tag);
        node.textContent = value;
        if (className) node.className = className;
        return node;
    }

    function splitControls(estimate, amount, nextContext) {
        const version = splitVersion;
        const section = text('section', '', 'taxi-split-section');
        section.appendChild(text('h3', '함께 타는 인원', 'taxi-split-heading'));
        const choices = text('div', '', 'taxi-split-options');
        choices.setAttribute('role', 'group');
        choices.setAttribute('aria-label', '함께 타는 인원');
        const result = text('div', '', 'taxi-split-result');
        result.setAttribute('role', 'status');
        result.setAttribute('aria-live', 'polite');
        result.setAttribute('aria-atomic', 'true');
        const allocations = new Map(), buttons = new Map();
        [2, 3, 4].forEach(function(count) {
            const previews = Array.isArray(estimate.split_preview)
                ? estimate.split_preview.filter(function(item) { return item && item.count === count; }) : [];
            const amounts = previews.length === 1 ? previews[0].amounts_krw : null;
            const valid = Array.isArray(amounts) && amounts.length === count && amounts.every(function(value) {
                return Number.isSafeInteger(value) && value >= 0;
            }) && amounts.reduce(function(total, value) { return total + value; }, 0) === amount
                && Math.max(...amounts) - Math.min(...amounts) <= 1;
            if (valid) allocations.set(count, amounts.slice());
            const button = text('button', count + '명', 'taxi-split-choice');
            button.type = 'button';
            button.dataset.count = String(count);
            button.disabled = !valid;
            button.setAttribute('aria-pressed', 'false');
            button.addEventListener('click', function() { select(count); });
            buttons.set(count, button);
            choices.appendChild(button);
        });
        function select(count) {
            if (version !== splitVersion || context !== nextContext || !nextContext.isCurrent()) return;
            const amounts = allocations.get(count);
            if (!amounts) return;
            buttons.forEach(function(button, number) { button.setAttribute('aria-pressed', String(number === count)); });
            const lowest = Math.min(...amounts), highest = Math.max(...amounts);
            const perPerson = lowest === highest ? money(lowest)
                : lowest.toLocaleString('ko-KR') + '~' + money(highest);
            result.replaceChildren(text('p', count + '명 가정 · 1인당 예상', 'taxi-split-label'),
                text('p', perPerson, 'taxi-split-amount'),
                text('p', '분담 예시 · ' + amounts.map(money).join(' / '), 'taxi-split-breakdown'),
                text('p', '총 예상요금을 나눈 참고 금액이며 실제 결제·정산 금액은 달라질 수 있어요.', 'taxi-caption'));
        }
        section.append(choices, result);
        if (allocations.size) {
            select(allocations.keys().next().value);
            if (allocations.size < 3) section.appendChild(text('p', '일부 인원수의 분담금을 확인할 수 없어요.', 'taxi-caption'));
        } else {
            result.appendChild(text('p', '분담금 계산 불가 · 견적을 다시 조회해 주세요.', 'taxi-status'));
        }
        return section;
    }

    function prepare(nextContext) {
        splitVersion += 1;
        clearTimeout(expiryTimer);
        expiryTimer = null;
        context = nextContext || null;
        const section = byId('taxi-section');
        const heading = byId('taxi-route');
        if (section) section.hidden = false;
        if (heading) heading.textContent = nextContext && nextContext.payload
            ? nextContext.payload.location + ' → ' + nextContext.payload.dest_name : initialRoute;
        setButton('견적 갱신', false);
    }

    function prompt(message) {
        reset();
        const body = byId('taxi-content');
        if (body) body.replaceChildren(text('p', message, 'taxi-status'));
    }

    function loading(nextContext, message) {
        prepare(nextContext);
        const body = byId('taxi-content');
        if (body) body.replaceChildren(text('p', message || '택시 시간과 요금을 확인하고 있어요…', 'taxi-status'));
        setButton('조회 중…', true);
        const section = byId('taxi-section');
        if (section) section.setAttribute('aria-busy', 'true');
    }

    function failed(message, nextContext) {
        prepare(nextContext);
        const body = byId('taxi-content');
        if (body) body.replaceChildren(text('p', message || '택시 정보를 확인하지 못했어요.', 'taxi-status taxi-error'));
        const section = byId('taxi-section');
        if (section) section.setAttribute('aria-busy', 'false');
        setButton('다시 조회', false);
    }

    function render(estimate, nextContext) {
        if (!estimate || !['ok', 'partial', 'no_travel_required'].includes(estimate.status)) {
            failed('택시 응답을 확인할 수 없어요. 다시 조회해 주세요.', nextContext);
            return;
        }
        prepare(nextContext);
        const section = byId('taxi-section');
        if (section) section.setAttribute('aria-busy', 'false');
        const body = byId('taxi-content');
        if (!body) return;
        if (estimate.status === 'no_travel_required') {
            body.replaceChildren(text('p', '출발지와 도착지가 같아 택시 이동이 필요하지 않아요.', 'taxi-status'));
            setButton('다시 조회', false);
            return;
        }
        const seconds = estimate.ride_duration_seconds;
        const amount = estimate.fare && estimate.fare.amount_krw;
        const hasFare = estimate.status === 'ok' && Number.isSafeInteger(amount) && amount > 0;
        const duration = typeof seconds === 'number' && Number.isFinite(seconds) && seconds >= 0
            ? '택시 이동 약 ' + Math.ceil(seconds / 60) + '분' : '택시 주행시간 조회 불가';
        const fare = text('p', '', 'taxi-fare');
        fare.append(text('span', hasFare ? '총 예상요금 약 ' : '총 예상요금 ', 'taxi-fare-label'),
            text('strong', hasFare ? money(amount) : '요금 조회 불가', 'taxi-fare-total'));
        const nodes = [fare];
        if (hasFare) nodes.push(splitControls(estimate, amount, nextContext));
        nodes.push(text('p', duration, 'taxi-duration'));
        if (typeof estimate.distance_m === 'number' && Number.isFinite(estimate.distance_m) && estimate.distance_m >= 0) {
            nodes.push(text('p', '주행거리 약 ' + (estimate.distance_m / 1000).toFixed(1) + 'km', 'taxi-caption'));
        }
        const at = new Date(typeof estimate.fetched_at === 'string' ? estimate.fetched_at : NaN);
        if (Number.isFinite(at.getTime())) nodes.push(text('p', '조회 ' + new Intl.DateTimeFormat('ko-KR', {
            timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit'
        }).format(at) + ' · 현재 조회 기준 예상', 'taxi-caption'));
        const warnings = Array.isArray(estimate.warnings) ? estimate.warnings.filter(function(value) { return typeof value === 'string'; }) : [];
        warnings.forEach(function(value) { nodes.push(text('p', value, 'taxi-caption')); });
        const fetched = at.getTime();
        const age = typeof estimate.cache_age_seconds === 'number' && Number.isFinite(estimate.cache_age_seconds)
            ? Math.max(0, estimate.cache_age_seconds) * 1000 : 0;
        const ttl = typeof estimate.cache_ttl_seconds === 'number' && Number.isFinite(estimate.cache_ttl_seconds) && estimate.cache_ttl_seconds > 0
            ? Math.min(300, estimate.cache_ttl_seconds) * 1000 : 60000;
        const explicit = typeof estimate.expires_at === 'string' ? Date.parse(estimate.expires_at) : NaN;
        const expires = Number.isFinite(explicit) ? explicit : fetched + ttl;
        const remaining = Math.min(ttl - age, Number.isFinite(expires) ? expires - Date.now() : ttl);
        function expire() {
            if (context !== nextContext || !nextContext.isCurrent()) return;
            splitVersion += 1;
            expiryTimer = null;
            body.replaceChildren(text('p', '견적이 만료됐어요. 시간·요금을 갱신해 주세요.', 'taxi-status'));
            setButton('견적 갱신', false);
        }
        if (remaining <= 0) { expire(); return; }
        body.replaceChildren(...nodes);
        expiryTimer = setTimeout(expire, remaining);
    }

    function init() {
        if (initialized) return;
        initialized = true;
        reset();
        const refresh = byId('taxi-refresh');
        if (refresh) refresh.addEventListener('click', function() {
            if (refresh.disabled) return;
            if (window.AppModules.api && typeof window.AppModules.api.requestTaxiFromSelection === 'function') {
                window.AppModules.api.requestTaxiFromSelection();
            }
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

    window.AppModules.taxi = { init: init, loading: loading, render: render, failed: failed, prompt: prompt, reset: reset };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
