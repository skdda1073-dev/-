/**
 * ui-renderer.js
 * 시간 포맷 변환 및 결과 카드(최적 단일 카드 / 주변 정류장 목록) UI 렌더링 모듈
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
    const h = String(dateObj.getHours()).padStart(2, '0');
    const m = String(dateObj.getMinutes()).padStart(2, '0');
    return `${h}:${m}`;
}

function renderResults(data) {
    const loadingEl = document.getElementById('loading');
    if (loadingEl) loadingEl.style.display = 'none';

    const container = document.getElementById('result-container');
    if (!container) return;
    
    if (data.error) {
        container.innerHTML = `<div class="result-card" style="text-align:center; color:#E53E3E; font-weight:bold;">${data.error}</div>`;
        return;
    }

    if (!data || data.length === 0) {
        container.innerHTML = `<div class="result-card" style="text-align:center; color:#718096; font-weight:bold;">주변 정류장을 찾을 수 없습니다.</div>`;
        return;
    }

    const isDestMode = Boolean(destName);

    // =========================================================================
    // ★ 모드 1: 목적지('어디로 가시나요?')를 선택한 경우
    // -> 기존 주변 정류장 목록은 모두 숨기고, 하얀색 바탕의 네이버 지도 스타일 결과 카드만 출력!
    // =========================================================================
    if (isDestMode) {
        const recStation = data.find(s => s.has_recommended_bus);
        
        if (!recStation) {
            container.innerHTML = `
            <div class="naver-white-card" style="border-color:#CBD5E0;">
                <div class="eta-badge-opt" style="background:#EDF2F7; color:#4A5568;">경로 안내</div>
                <div style="font-size:17px; font-weight:800; color:#1A202C;">
                    주변 정류장에서 [${destName}]까지 한 번에 가는 직통 버스가 없습니다.
                </div>
                <div style="font-size:14px; color:#718096; margin-top:6px;">
                    다른 목적지를 검색하시거나 '✕ 취소'를 눌러 주변 전체 정류장을 확인해 보세요.
                </div>
            </div>`;
            return;
        }

        const recNo = recStation.station_no ? `(${recStation.station_no})` : '';
        const recBuses = recStation.buses.filter(b => b.is_recommended);
        const liveRecBus = recBuses.find(b => b.status_type !== 'miss') || recBuses[0];
        const fallbackRoute = recStation.matched_routes[0];

        const walkMin = recStation.walk_min;
        const rideMin = liveRecBus ? liveRecBus.ride_min : (fallbackRoute ? fallbackRoute.ride_min : 10);
        const waitIncludeMin = liveRecBus ? Math.max(walkMin, liveRecBus.min_left) : walkMin;
        const totalTripMin = waitIncludeMin + rideMin;

        const now = new Date();
        const arrivalDate = new Date(now.getTime() + totalTripMin * 60000);
        const timeRangeStr = `${formatKoreanTime(now)} - ${formatKoreanTime(arrivalDate)}`;

        const allRouteNames = recStation.matched_routes.map(r => `${r.route_no}번`).join(', ');
        const alightStationName = liveRecBus ? liveRecBus.alighting_station : fallbackRoute.alighting_station;

        const walkPct = Math.max(28, Math.min(68, Math.round((walkMin / (walkMin + rideMin)) * 100)));
        const busPct = 100 - walkPct;

        // 실시간 도착 버스 및 도보 여유시간 멘트 HTML 조립
        let busDetailHtml = '';
        if (recBuses.length > 0) {
            recBuses.forEach(bus => {
                let badgeStyle = '';
                let msgBoxStyle = '';
                let icon = '';

                if (bus.status_type === 'relax') {
                    badgeStyle = 'border: 1.5px solid #03c75a; color: #03c75a; background: #F0FFF4;';
                    msgBoxStyle = 'background: #F0FFF4; color: #276749; border-left: 3px solid #03c75a;';
                    icon = '💡';
                } else if (bus.status_type === 'hurry') {
                    badgeStyle = 'border: 1.5px solid #DD6B20; color: #DD6B20; background: #FFFAF0;';
                    msgBoxStyle = 'background: #FFFAF0; color: #9C4221; border-left: 3px solid #DD6B20;';
                    icon = '🏃';
                } else {
                    badgeStyle = 'border: 1.5px solid #A0AEC0; color: #718096; background: #F7FAFC;';
                    msgBoxStyle = 'background: #F7FAFC; color: #718096; border-left: 3px solid #CBD5E0;';
                    icon = '⏳';
                }

                busDetailHtml += `
                <div style="background:#F8FAFC; border:1px solid #EDF2F7; border-radius:10px; padding:10px 12px; margin-top:8px;">
                    <div class="bus-item" style="margin-bottom:6px;">
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span style="font-size:16px; font-weight:800; color:#03c75a;">🚌 ${bus.route}번</span>
                            <span style="font-size:12px; color:#718096; font-weight:600;">(${bus.destination})</span>
                        </div>
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span style="color:#E53E3E; font-weight:800; font-size:16px;">${bus.min_left}분</span>
                            <span style="color:#718096; font-size:13px; font-weight:500;">${bus.prev_cnt}정류장</span>
                            <span style="font-size:11px; font-weight:700; padding:2px 6px; border-radius:4px; ${badgeStyle}">
                                ${bus.status_badge}
                            </span>
                        </div>
                    </div>
                    <div style="font-size:13px; font-weight:600; padding:7px 10px; border-radius:6px; ${msgBoxStyle}">
                        ${icon} <b>도보 ${bus.walk_min}분</b> 소요 · ${bus.guide_msg}
                    </div>
                </div>`;
            });
        } else {
            busDetailHtml = `
            <div style="background:#F8FAFC; border:1px solid #EDF2F7; border-radius:10px; padding:10px 12px; margin-top:8px; font-size:13px; color:#718096; font-weight:600;">
                💡 현재 도착 예정인 실시간 버스가 없습니다. 도보 ${walkMin}분 이동 후 다음 배차를 탑승하세요.
            </div>`;
        }

        container.innerHTML = `
        <div class="naver-white-card">
            <div class="eta-badge-opt">🎯 최적 경로 · ${recStation.station_name} ${recNo} 이동 추천</div>
            
            <div style="display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;">
                <span class="eta-big-time">${totalTripMin}<span style="font-size:20px; font-weight:700;">분</span></span>
                <span style="font-size:14px; color:#005AAB; font-weight:800;">
                    🧭 도보 ${walkMin}분(${recStation.walk_dist}m) 거리의 『${recStation.station_name}』 정류장으로 가세요!
                </span>
            </div>

            <div class="eta-clock-range">
                ${timeRangeStr} <span style="color:#CBD5E0; margin:0 6px;">|</span> 카드 1,550원
            </div>

            <!-- 네이버 지도 스타일 가로 타임라인 바 (화이트 테마) -->
            <div class="eta-bar-track">
                <div class="eta-bar-walk" style="width:${walkPct}%;">🚶 도보 ${walkMin}분</div>
                <div class="eta-bar-bus" style="width:${busPct}%;">🚌 버스 ${rideMin}분</div>
            </div>

            <!-- 승차 정류장 및 실시간 버스 + 여유시간 멘트 -->
            <div class="eta-step-row">
                <div style="color:#03c75a; font-weight:800; font-size:15px; min-width:85px;">
                    🚌 ${allRouteNames}
                </div>
                <div style="flex:1;">
                    <div style="font-weight:800; font-size:16px; color:#1A202C;">
                        ${recStation.station_name} <span style="font-size:14px; color:#718096; font-weight:500;">${recNo}</span>
                    </div>
                    <div style="font-size:13px; color:#03c75a; font-weight:700; margin-top:2px;">
                        ➡️ ${recStation.direction}
                    </div>
                    ${busDetailHtml}
                </div>
            </div>

            <!-- 하차 정류장 -->
            <div class="eta-step-row" style="margin-top:14px;">
                <div style="color:#718096; font-weight:800; font-size:14px; min-width:85px;">⚪ 하차</div>
                <div>
                    <div style="font-weight:800; font-size:16px; color:#1A202C;">${alightStationName} 정류장</div>
                    <div style="font-size:13px; color:#005AAB; font-weight:700; margin-top:2px;">🎯 도착지: ${destName}</div>
                </div>
            </div>
        </div>`;
        return;
    }

    // =========================================================================
    // ★ 모드 2: 목적지 없이 출발지만 선택한 경우
    // -> 별다른 멘트 없이 깔끔하게 주변 정류장의 버스 도착 정보만 표시!
    // =========================================================================
    let html = '';
    data.forEach(station => {
        const sttnNoText = station.station_no ? `(${station.station_no})` : '';
        
        html += `
        <div class="result-card">
            <div class="station-header">
                <div>
                    <h3 class="station-name">🚏 ${station.station_name} <span style="font-size:14px; color:#718096; font-weight:500;">${sttnNoText}</span></h3>
                    <div style="font-size: 13px; color: #03c75a; font-weight: 700; margin-top: 4px;">
                        ➡️ ${station.direction}
                    </div>
                </div>
                <span style="background: #EBF8FF; color: #005AAB; padding: 6px 12px; border-radius: 20px; font-size: 13px; font-weight: 800; white-space: nowrap;">
                    🚶 도보 ${station.walk_min}분 (${station.walk_dist}m)
                </span>
            </div>
            <div class="bus-list">`;
        
        if (station.buses.length > 0) {
            station.buses.forEach(bus => {
                html += `
                <div class="bus-item" style="padding: 10px 0; border-bottom: 1px solid #EDF2F7;">
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="color: #03c75a; font-size: 18px;">🚌</span>
                        <span style="font-size: 17px; font-weight: 800; color: #1A202C;">${bus.route}번</span>
                        <span style="font-size: 13px; color: #718096; font-weight: 600;">(${bus.destination})</span>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="color: #E53E3E; font-weight: 800; font-size: 17px;">${bus.min_left}분</span>
                        <span style="color: #718096; font-size: 14px; font-weight: 500;">${bus.prev_cnt}정류장</span>
                    </div>
                </div>`;
            });
        } else {
            html += `<div style="text-align: center; color: #A0AEC0; padding: 12px 0; font-weight:600; font-size:14px;">도착 예정 버스가 없습니다.</div>`;
        }
        
        html += `</div></div>`;
    });
    container.innerHTML = html;
}

window.formatKoreanTime = formatKoreanTime;
window.format24Time = format24Time;
window.renderResults = renderResults;

