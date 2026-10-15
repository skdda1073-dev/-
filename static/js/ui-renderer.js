/**
 * ui-renderer.js
 * 주변 정류장 정보와 직통·환승 경로 후보 UI를 담당합니다.
 */

function formatKoreanTime(dateObj) {
    let h = dateObj.getHours();
    const m = String(dateObj.getMinutes()).padStart(2, '0');
    const ampm = h >= 12 ? '오후' : '오전';
    h = h % 12;
    if (h === 0) h = 12;
    return `${ampm} ${h}:${m}`;
}

function format24Time(dateObj) {
    return `${String(dateObj.getHours()).padStart(2, '0')}:${String(dateObj.getMinutes()).padStart(2, '0')}`;
}

function resultElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
}

function resultMinutes(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.ceil(value) : null;
}

function minuteLabel(value) {
    const minutes = resultMinutes(value);
    return minutes === null ? '시간 미확인' : `약 ${minutes}분`;
}

function walkLabel(walk) {
    if (!walk) return '도보 시간 미확인';
    const distance = typeof walk.distance_m === 'number' && Number.isFinite(walk.distance_m)
        ? ` (${Math.round(walk.distance_m)}m)` : '';
    return `도보 ${minuteLabel(walk.minutes)}${distance}`;
}

function resultMessage(container, text, isError) {
    container.appendChild(resultElement('div', `result-card result-message${isError ? ' result-error' : ''}`, text));
}

function appendJourneyWarnings(parent, warnings) {
    const messages = [...new Set((Array.isArray(warnings) ? warnings : []).filter(item => typeof item === 'string' && item))];
    if (!messages.length) return;
    const list = resultElement('ul', 'journey-warnings');
    messages.forEach(message => list.appendChild(resultElement('li', '', message)));
    parent.appendChild(list);
}

function renderJourneys(container, response) {
    const journeys = Array.isArray(response.journeys) ? response.journeys.slice(0, 3) : [];
    const origin = response.origin || {};
    const destination = response.destination || {};
    if (!journeys.length) {
        resultMessage(container, '조회한 정류장 범위에서 직통·1회 환승 경로를 확인하지 못했습니다.');
        appendJourneyWarnings(container, response.warnings);
        return;
    }

    const introduction = resultElement('div', 'journey-introduction',
        `${origin.name || '출발지'} → ${destination.name || '목적지'} · 경로 후보 ${journeys.length}개`);
    introduction.appendChild(resultElement('p', 'journey-muted',
        '버스 시간은 정류장 순서·거리 기반 추정입니다. 미확인 배차 대기는 총 소요 시간에 포함할 수 없습니다.'));
    container.appendChild(introduction);
    appendJourneyWarnings(container, response.warnings);

    journeys.forEach((journey, index) => {
        const legs = Array.isArray(journey.legs) ? journey.legs : [];
        const transfers = resultMinutes(journey.transfer_count);
        const total = resultMinutes(journey.total_min);
        const known = resultMinutes(journey.known_min);
        const card = resultElement('article', 'result-card journey-card');
        card.appendChild(resultElement('div', 'eta-badge-opt',
            `경로 후보 ${index + 1} · ${transfers === 0 ? '직통' : `${transfers === null ? Math.max(0, legs.length - 1) : transfers}회 환승`}`));
        card.appendChild(resultElement('h3', 'journey-total', total === null
            ? `이동 구간 ${known === null ? '시간 미확인' : `약 ${known}분`} + 배차 대기 미확인`
            : `총 약 ${total}분`));
        card.appendChild(resultElement('p', 'journey-summary',
            `도보 ${minuteLabel(journey.walk_min)} · 버스 ${minuteLabel(journey.ride_min)} · 대기 ${journey.wait_min === null || journey.wait_min === undefined ? '미확인' : minuteLabel(journey.wait_min)}`));
        card.appendChild(resultElement('div', 'journey-walk-step',
            `🚶 ${origin.name || '출발지'} → ${legs[0] && legs[0].board ? legs[0].board.name : '승차 정류장'} · ${walkLabel(journey.access_walk)}`));

        legs.forEach((leg, legIndex) => {
            const board = leg.board || {};
            const alight = leg.alight || {};
            const step = resultElement('section', 'journey-leg');
            step.appendChild(resultElement('h4', 'journey-route', `🚌 ${leg.route_no || '노선번호 미확인'}번 · ${leg.direction || '방향 미확인'}`));
            step.appendChild(resultElement('p', 'journey-stops', `${board.name || '승차 정류장'} 승차 → ${alight.name || '하차 정류장'} 하차`));
            const stopCount = resultMinutes(leg.stop_count);
            step.appendChild(resultElement('p', 'journey-muted',
                `${stopCount === null ? '정류장 수 미확인' : `${stopCount}개 정류장 구간`} · 버스 ${minuteLabel(leg.ride_min)}`));
            const arrival = resultMinutes(leg.arrival_min);
            const wait = resultMinutes(leg.wait_min);
            step.appendChild(resultElement('p', 'journey-muted', arrival === null
                ? '이 구간의 실시간 도착·배차 대기는 미확인입니다.'
                : `조회 시점 버스 도착 약 ${arrival}분 후${wait === null ? ' · 탑승 대기 미확인' : ` · 정류장 도착 후 대기 약 ${wait}분`}`));
            card.appendChild(step);
            if (legIndex < legs.length - 1) {
                const buffer = resultMinutes(journey.transfer_buffer_min);
                card.appendChild(resultElement('div', 'journey-walk-step',
                    `↔ 환승${buffer === null ? '' : ` 이동·승차 여유 약 ${buffer}분`} · 다음 버스 배차에 따라 시간이 늘어날 수 있습니다.`));
            }
        });

        const lastLeg = legs[legs.length - 1];
        card.appendChild(resultElement('div', 'journey-walk-step',
            `🚶 ${lastLeg && lastLeg.alight ? lastLeg.alight.name : '하차 정류장'} → ${destination.name || '목적지'} · ${walkLabel(journey.egress_walk)}`));
        card.appendChild(resultElement('p', 'journey-muted',
            '예상 시간이며 실제 운행·신호·교통·보행 환경에 따라 달라집니다. 지도는 첫 번째 후보의 정류장을 표시합니다.'));
        appendJourneyWarnings(card, journey.warnings);
        container.appendChild(card);
    });
}

function renderNearbyStations(container, stations) {
    if (!stations.length) {
        resultMessage(container, '주변 정류장을 찾을 수 없습니다.');
        return;
    }
    stations.forEach(station => {
        const card = resultElement('article', 'result-card');
        const header = resultElement('div', 'station-header');
        const heading = resultElement('div');
        heading.appendChild(resultElement('h3', 'station-name', `🚏 ${station.station_name || '정류장'}${station.station_no ? ` (${station.station_no})` : ''}`));
        heading.appendChild(resultElement('p', 'journey-muted', `➡ ${station.direction || '방향 미확인'}`));
        header.appendChild(heading);
        header.appendChild(resultElement('span', 'nearby-walk',
            `🚶 도보 ${minuteLabel(station.walk_min)}${Number.isFinite(station.walk_dist) ? ` (${Math.round(station.walk_dist)}m)` : ''}`));
        card.appendChild(header);
        const buses = resultElement('div', 'bus-list');
        const stationBuses = Array.isArray(station.buses) ? station.buses : [];
        stationBuses.forEach(bus => {
            const row = resultElement('div', 'bus-item nearby-bus');
            row.appendChild(resultElement('span', 'journey-route', `🚌 ${bus.route || '노선번호 미확인'}번 (${bus.destination || '방향 미확인'})`));
            row.appendChild(resultElement('span', 'nearby-arrival', `${minuteLabel(bus.min_left)} · ${bus.prev_cnt ?? '?'}정류장`));
            buses.appendChild(row);
        });
        if (!stationBuses.length) buses.appendChild(resultElement('p', 'journey-muted', '도착 예정 버스가 없습니다.'));
        card.appendChild(buses);
        container.appendChild(card);
    });
}

function renderResults(data) {
    const loadingEl = document.getElementById('loading');
    if (loadingEl) loadingEl.style.display = 'none';
    const container = document.getElementById('result-container');
    if (!container) return;
    container.replaceChildren();
    if (data && data.error) {
        resultMessage(container, data.error, true);
    } else if (data && data.notice) {
        resultMessage(container, data.notice);
    } else if (data && data.mode === 'journeys') {
        renderJourneys(container, data);
    } else if (Array.isArray(data)) {
        renderNearbyStations(container, data);
    } else {
        resultMessage(container, '경로 정보를 확인할 수 없습니다. 잠시 후 다시 조회해 주세요.', true);
    }
}

window.AppModules.renderer = { renderResults: renderResults };
window.formatKoreanTime = formatKoreanTime;
window.format24Time = format24Time;
window.renderResults = renderResults;

