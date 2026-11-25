/** Campus preparation advice. Provider keys and recommendation rules stay on the server. */
(function() {
    'use strict';
    const REFRESH_MS = 15 * 60 * 1000;
    const REQUEST_TIMEOUT_MS = 22000;
    const CAMPUS_NAMES = { hallym: '한림대학교', knu: '강원대학교' };
    const DATA_STATES = new Set(['ok', 'partial', 'unavailable', 'stale']);
    const AIR_GRADES = new Set(['좋음', '보통', '나쁨', '매우 나쁨']);
    const ALERT_IDS = new Set(['rain', 'snow', 'cold_wind', 'cold', 'wind', 'dust']);
    let initialized = false, sequence = 0, controller = null, timer = null;
    let lastAttemptAt = 0, renderedCampus = null, activeCampus = null, inFlight = false, hasRendered = false;
    let toggle, panel, details, close, refresh, body;

    function element(id) { return document.getElementById(id); }
    function text(value, fallback, limit) {
        return typeof value === 'string' && value.trim() ? value.trim().slice(0, limit || 280) : fallback;
    }
    function write(id, value) { const node = element(id); if (node) node.textContent = value; }
    function campus() {
        const selected = window.AppState && window.AppState.getSelectedCampus();
        return selected === 'knu' ? 'knu' : 'hallym';
    }
    function numeric(value, suffix) {
        if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
        return value.toLocaleString('ko-KR', { maximumFractionDigits: 1 }) + suffix;
    }
    function localTime(value) {
        if (typeof value !== 'string') return '';
        const date = new Date(value);
        if (!Number.isFinite(date.getTime())) return '';
        return new Intl.DateTimeFormat('ko-KR', {
            timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric',
            hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
        }).format(date);
    }
    function showDetails(value, moveFocus = false) {
        const focusedInside = details.contains(document.activeElement) || document.activeElement === close;
        details.hidden = !value;
        close.hidden = !value;
        panel.dataset.detailsOpen = String(value);
        toggle.setAttribute('aria-expanded', String(value));
        if (moveFocus || (!value && focusedInside)) (value ? body : toggle).focus({ preventScroll: true });
    }
    function stopTimer() { if (timer !== null) window.clearTimeout(timer); timer = null; }
    function schedule() {
        stopTimer();
        if (document.hidden) return;
        const delay = Math.max(1000, REFRESH_MS - (Date.now() - lastAttemptAt));
        timer = window.setTimeout(function() { load(); }, delay);
    }
    function badge(id, status, label) {
        const node = element(id);
        node.textContent = label;
        node.classList.toggle('is-delayed', status === 'partial' || status === 'stale');
    }
    function countAlerts(count) {
        const node = element('commute-advice-count');
        node.textContent = count ? String(count) : '';
        node.hidden = !count;
    }
    function toggleStatus(value) {
        write('commute-advice-toggle-status', value);
        const count = element('commute-advice-count');
        toggle.setAttribute('aria-label', '통학 준비 상세 · ' + value + (count.hidden ? '' : ' · 안내 ' + count.textContent + '개'));
    }
    function writeWeatherNumbers(values) {
        const fields = [['temperature', '℃'], ['wind', 'm/s'], ['rain', '%']];
        fields.forEach(function([field, unit], index) {
            const value = numeric(values[index], unit);
            write('commute-weather-' + field, value);
            write('commute-advice-glance-' + field, value);
        });
    }
    function showWeatherGlance(visible, message) {
        const status = element('commute-advice-glance-status');
        element('commute-advice-glance-values').hidden = !visible;
        status.hidden = !message;
        status.textContent = message;
    }
    function clearReadings(loading) {
        element('commute-weather-values').hidden = true;
        element('commute-air-values').hidden = true;
        write('commute-weather-time', '');
        write('commute-air-time', '');
        writeWeatherNumbers([]);
        showWeatherGlance(false, loading ? '예보 확인 중' : '예보 조회 불가');
        write('commute-air-pm25-grade', '—');
        write('commute-air-pm10-grade', '—');
        write('commute-air-pm25-value', '—');
        write('commute-air-pm10-value', '—');
        ['pm10', 'pm25'].forEach(function(kind) { element('commute-air-' + kind + '-grade').classList.remove('is-bad'); });
        badge('commute-weather-badge', 'unavailable', loading ? '확인 중' : '조회 불가');
        badge('commute-air-badge', 'unavailable', loading ? '확인 중' : '조회 불가');
        write('commute-weather-message', loading ? '선택한 캠퍼스의 예보를 확인하고 있어요.' : '날씨 정보를 불러오지 못했어요.');
        write('commute-air-message', loading ? '주변 측정소의 대기질을 확인하고 있어요.' : '대기질 정보를 불러오지 못했어요.');
        element('commute-advice-alerts').replaceChildren();
        countAlerts(0);
    }
    function loading(selected) {
        renderedCampus = null;
        hasRendered = false;
        write('commute-advice-campus', CAMPUS_NAMES[selected] + ' 기준');
        write('commute-advice-status', '날씨와 대기질을 확인하고 있어요.');
        element('commute-advice-status').classList.remove('is-unavailable');
        clearReadings(true);
        toggleStatus('확인 중');
        refresh.disabled = true;
        body.setAttribute('aria-busy', 'true');
    }
    function unavailable(selected) {
        clearReadings(false);
        write('commute-advice-campus', CAMPUS_NAMES[selected] + ' 기준');
        write('commute-advice-status', '지금은 준비 안내를 불러오지 못했어요. 잠시 후 다시 확인해 주세요.');
        element('commute-advice-status').classList.add('is-unavailable');
        toggleStatus('확인 불가');
        renderedCampus = selected;
        hasRendered = true;
    }
    function stateLabel(status, normal) {
        return { ok: normal, partial: '일부 확인', unavailable: '조회 불가', stale: '자료 지연' }[status];
    }
    function renderWeather(weather) {
        badge('commute-weather-badge', weather.status, stateLabel(weather.status, '예보'));
        const usable = weather.status !== 'unavailable';
        const values = [weather.temperature_c, weather.wind_speed_ms, weather.rain_probability];
        const visible = usable && values.some(value => typeof value === 'number' && Number.isFinite(value));
        element('commute-weather-values').hidden = !visible;
        writeWeatherNumbers(values);
        const forecastAt = localTime(weather.forecast_at);
        showWeatherGlance(visible, weather.status === 'ok' && visible
            ? [forecastAt, '예보'].filter(Boolean).join(' ') : stateLabel(weather.status, '예보 조회 불가'));
        write('commute-weather-message', text(weather.message, usable ? '다음 예보 시각의 날씨예요.' : '날씨 정보를 불러오지 못했어요.'));
        const times = [];
        if (forecastAt) times.push(forecastAt + ' 예보');
        if (localTime(weather.issued_at)) times.push(localTime(weather.issued_at) + ' 발표');
        write('commute-weather-time', times.join(' · '));
    }
    function renderAir(air) {
        badge('commute-air-badge', air.status, stateLabel(air.status, '최근 측정'));
        let available = false;
        ['pm25', 'pm10'].forEach(function(kind) {
            const item = air[kind] && typeof air[kind] === 'object' ? air[kind] : {};
            const value = typeof item.value === 'number' && Number.isFinite(item.value) && item.value >= 0 ? item.value : null;
            const grade = AIR_GRADES.has(item.grade) ? item.grade : null;
            available = available || value !== null || grade !== null;
            write('commute-air-' + kind + '-value', numeric(value, '㎍/㎥'));
            write('commute-air-' + kind + '-grade', grade || '확인 불가');
            element('commute-air-' + kind + '-grade').classList.toggle('is-bad', grade === '나쁨' || grade === '매우 나쁨');
        });
        element('commute-air-values').hidden = !available || air.status === 'unavailable';
        write('commute-air-message', text(air.message, air.status === 'unavailable' ? '대기질 정보를 불러오지 못했어요.' : '주변 측정소의 참고값이에요.'));
        const parts = [];
        const station = text(air.station_name, '', 50);
        if (station) parts.push(station + ' 측정소 · 춘천 참고값');
        if (localTime(air.observed_at)) parts.push(localTime(air.observed_at) + ' 측정');
        write('commute-air-time', parts.join(' / '));
    }
    function icon(kind) {
        const paths = {
            rain: ['M3 12a9 9 0 0 1 18 0H3Z', 'M12 3v15a3 3 0 0 0 6 0'],
            snow: ['M12 2v20M3.3 7l17.4 10M3.3 17 20.7 7', 'm9 4 3 3 3-3M9 20l3-3 3 3'],
            wind: ['M3 8h12a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h5a3 3 0 1 1-3 3'],
            cold: ['M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0Z', 'M12 7v10'],
            dust: ['M4 9 12 6l8 3v5c0 4-8 7-8 7s-8-3-8-7V9Z', 'M8 11h8M8 14h8M4 10 1 8M20 10l3-2'],
        };
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('class', 'icon');
        svg.setAttribute('aria-hidden', 'true');
        (paths[kind === 'cold_wind' ? 'cold' : kind] || paths.wind).forEach(function(value) {
            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            path.setAttribute('d', value); svg.appendChild(path);
        });
        return svg;
    }
    function renderAlerts(data) {
        const list = element('commute-advice-alerts');
        list.replaceChildren();
        const seen = new Set();
        (Array.isArray(data.alerts) ? data.alerts : []).slice(0, 10).forEach(function(alert) {
            if (!alert || !ALERT_IDS.has(alert.id) || seen.has(alert.id)) return;
            const source = alert.id === 'dust' ? data.air : data.weather;
            if (source.status !== 'ok' && source.status !== 'partial') return;
            if (typeof alert.title !== 'string' || typeof alert.message !== 'string') return;
            seen.add(alert.id);
            const row = document.createElement('li');
            row.className = 'commute-advice-alert' + (alert.severity === 'warning' ? ' is-warning' : '');
            row.dataset.advice = alert.id;
            const mark = document.createElement('span');
            mark.className = 'commute-advice-alert-icon'; mark.appendChild(icon(alert.id));
            const copy = document.createElement('div'); copy.className = 'commute-advice-alert-copy';
            const heading = document.createElement('h3'); heading.textContent = text(alert.title, '출발 준비 안내', 80);
            const message = document.createElement('p'); message.textContent = text(alert.message, '출발 전 정보를 확인해 주세요.');
            copy.append(heading, message);
            if (localTime(alert.at)) {
                const at = document.createElement('p'); at.className = 'commute-advice-alert-time';
                at.textContent = localTime(alert.at) + (alert.id === 'dust' ? ' 측정' : ' 예보');
                copy.appendChild(at);
            }
            row.append(mark, copy); list.appendChild(row);
        });
        countAlerts(seen.size);
        return seen.size;
    }
    function valid(data, selected) {
        return data && typeof data === 'object' && ['ok', 'partial', 'unavailable'].includes(data.status)
            && data.campus && data.campus.id === selected
            && data.weather && DATA_STATES.has(data.weather.status)
            && data.air && DATA_STATES.has(data.air.status);
    }
    function render(data, selected) {
        write('commute-advice-campus', CAMPUS_NAMES[selected] + ' 기준');
        write('commute-advice-status', text(data.summary, data.status === 'ok'
            ? '예보와 대기질을 확인했어요. 출발 전 준비할 내용을 살펴보세요.'
            : '일부 정보를 확인하지 못했어요. 아래 자료 시각을 함께 확인해 주세요.'));
        element('commute-advice-status').classList.toggle('is-unavailable', data.status === 'unavailable');
        renderWeather(data.weather);
        renderAir(data.air);
        const alertCount = renderAlerts(data);
        toggleStatus(data.status === 'unavailable' ? '확인 불가' : data.status === 'partial' ? '일부 확인' : alertCount ? '안내 있음' : '정보 확인됨');
        renderedCampus = selected;
        hasRendered = true;
    }
    async function load(force) {
        if (!initialized || document.hidden) return;
        const selected = campus();
        if (inFlight && activeCampus === selected && !force) return;
        stopTimer();
        const requestNumber = ++sequence;
        if (controller) controller.abort();
        controller = new AbortController();
        const currentController = controller;
        inFlight = true;
        activeCampus = selected;
        lastAttemptAt = Date.now();
        loading(selected);
        const timeout = window.setTimeout(function() { currentController.abort(); }, REQUEST_TIMEOUT_MS);
        try {
            const response = await window.fetch('/api/commute-advice?campus=' + encodeURIComponent(selected), {
                method: 'GET', credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'application/json' }, signal: currentController.signal,
            });
            if (!response.ok) throw new Error('Advice unavailable');
            const data = await response.json();
            if (requestNumber !== sequence || selected !== campus()) return;
            if (!valid(data, selected)) throw new Error('Advice response invalid');
            render(data, selected);
        } catch (error) {
            if (requestNumber !== sequence || selected !== campus()) return;
            unavailable(selected);
        } finally {
            window.clearTimeout(timeout);
            if (requestNumber === sequence) {
                controller = null;
                inFlight = false;
                refresh.disabled = false;
                body.setAttribute('aria-busy', 'false');
                schedule();
            }
        }
    }
    function init() {
        if (initialized) return;
        toggle = element('commute-advice-toggle'); panel = element('commute-advice-panel');
        details = element('commute-advice-details');
        close = element('commute-advice-close'); refresh = element('commute-advice-refresh');
        body = element('commute-advice-body');
        if (!toggle || !panel || !details || !close || !refresh || !body) return;
        initialized = true;
        const desktop = window.matchMedia('(min-width: 1280px)');
        toggle.addEventListener('click', function() {
            const opening = details.hidden;
            showDetails(opening, true);
            if (opening && (!hasRendered || renderedCampus !== campus() || Date.now() - lastAttemptAt >= REFRESH_MS)) load();
        });
        close.addEventListener('click', function() { showDetails(false, true); });
        refresh.addEventListener('click', function() { load(true); });
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape' && !details.hidden && (panel.contains(document.activeElement) || toggle.contains(document.activeElement))) {
                event.preventDefault(); showDetails(false, true);
            }
        });
        desktop.addEventListener('change', function(event) { showDetails(event.matches); });
        window.addEventListener('commute:campus-change', function() {
            ++sequence;
            if (controller) controller.abort();
            controller = null; inFlight = false; lastAttemptAt = 0;
            loading(campus());
            if (!document.hidden) load(true);
        });
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) stopTimer();
            else if (!hasRendered || renderedCampus !== campus() || Date.now() - lastAttemptAt >= REFRESH_MS) load();
            else schedule();
        });
        window.addEventListener('pagehide', function() {
            stopTimer(); ++sequence;
            if (controller) controller.abort();
            controller = null; inFlight = false;
        });
        window.addEventListener('pageshow', function(event) {
            if (event.persisted) {
                if (!hasRendered || Date.now() - lastAttemptAt >= REFRESH_MS) load();
                else schedule();
            }
        });
        showDetails(desktop.matches);
        load();
    }
    window.AppModules = window.AppModules || {};
    window.AppModules.commuteAdvice = { init: init, refresh: function() { return load(true); } };
})();
