import os
from flask import Flask, render_template, request, jsonify
import requests
from math import isfinite
import pandas as pd

from services.public_transit_client import PublicDataError, PublicTransitClient
from services.transit_service import TransitService
from services.walking_service import WalkingService

base_dir = os.path.abspath(os.path.dirname(__file__))
template_dir = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_dir)

# ==========================================
# 🔑 1. API 키 설정
# ==========================================
NAVER_MAP_CLIENT_ID = os.environ.get("NAVER_MAP_CLIENT_ID", "")
TMAP_APP_KEY = os.environ.get("TMAP_APP_KEY", "")
PUBLIC_DATA_KEY = os.environ.get("PUBLIC_DATA_KEY", "")
CITY_CODE = "32010" # 춘천시

transit_client = PublicTransitClient(PUBLIC_DATA_KEY, CITY_CODE)
walking_service = WalkingService(TMAP_APP_KEY)
transit_service = TransitService(transit_client, walking_service.get_time)

# ==========================================
# 🏫 2. CSV 데이터 로드 (한림대 + 강원대 모두 로드)
# ==========================================
CAMPUS_BUILDINGS = {
    "hallym": {},
    "knu": {}
}
ALL_BUILDINGS = {}

def load_csv_data(filename, campus_key):
    filepath = os.path.join(base_dir, filename)
    if not os.path.exists(filepath):
        return
    try:
        df = pd.read_csv(filepath, encoding='utf-8')
    except UnicodeDecodeError:
        df = pd.read_csv(filepath, encoding='cp949')
        
    for _, row in df.iterrows():
        b_name = str(row['건물명']).strip()
        coords = (float(row['위도(lat)']), float(row['경도(lon)']))
        CAMPUS_BUILDINGS[campus_key][b_name] = coords
        ALL_BUILDINGS[b_name] = coords

try:
    load_csv_data('한림대학교 건물별 좌표.csv', 'hallym')
    load_csv_data('강원대학교 건물별 좌표.csv', 'knu')
    print(f"✅ 로드 완료 - 한림대: {len(CAMPUS_BUILDINGS['hallym'])}개, 강원대: {len(CAMPUS_BUILDINGS['knu'])}개")
except Exception as e:
    print("CSV 로드 오류:", e)

def parse_coordinate(value, lower, upper):
    """JSON 숫자·숫자 문자열을 유효한 위도·경도로 변환한다."""
    if isinstance(value, bool):
        raise ValueError("좌표 형식이 올바르지 않습니다.")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError("좌표 범위가 올바르지 않습니다.") from None
    if not isfinite(number) or not lower <= number <= upper:
        raise ValueError("좌표 범위가 올바르지 않습니다.")
    return number


# ==========================================
# 웹 요청은 검증·응답만 담당하고 경로 계산은 services에 위임한다.
# ==========================================
@app.route('/')
def home():
    return render_template(
        'index.html',
        hallym_buildings=list(CAMPUS_BUILDINGS["hallym"].keys()),
        knu_buildings=list(CAMPUS_BUILDINGS["knu"].keys()),
        naver_map_client_id=NAVER_MAP_CLIENT_ID,
    )

@app.route('/search_poi', methods=['GET'])
def search_poi():
    keyword = request.args.get('q', '').strip()
    if not keyword:
        return jsonify([])

    suggestions = []
    seen_names = set()

    for b_name, (lat, lon) in ALL_BUILDINGS.items():
        if keyword in b_name:
            school = "한림대학교" if b_name in CAMPUS_BUILDINGS["hallym"] else "강원대학교"
            full_name = f"{school} {b_name}"
            suggestions.append({
                "name": full_name,
                "sub": f"{school} 교내 건물",
                "lat": lat,
                "lon": lon
            })
            seen_names.add(full_name)
            if len(suggestions) >= 4:
                break

    url = "https://apis.openapi.sk.com/tmap/pois"
    params = {
        "version": 1,
        "searchKeyword": f"춘천 {keyword}",
        "count": 8
    }
    headers = {"Accept": "application/json", "appKey": TMAP_APP_KEY}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        if res.status_code == 200 and res.text:
            pois = res.json().get('searchPoiInfo', {}).get('pois', {}).get('poi', [])
            for poi in pois:
                name = poi.get('name', '').strip()
                lat = float(poi.get('noorLat', 0))
                lon = float(poi.get('noorLon', 0))
                addr = f"{poi.get('upperAddrName', '')} {poi.get('middleAddrName', '')} {poi.get('lowerAddrName', '')}".strip()
                
                if name and name not in seen_names:
                    suggestions.append({
                        "name": name,
                        "sub": addr,
                        "lat": lat,
                        "lon": lon
                    })
                    seen_names.add(name)
    except Exception as e:
        print("POI 검색 오류:", e)

    return jsonify(suggestions[:8])

@app.route('/get_route_info', methods=['POST'])
def get_route_info():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "요청 본문은 JSON 객체여야 합니다.", "code": "INVALID_REQUEST"}), 400

    my_location = str(data.get('location') or '내 실시간 위치')
    campus = data.get('campus', 'hallym')
    if not isinstance(campus, str) or campus not in CAMPUS_BUILDINGS:
        return jsonify({"error": "캠퍼스를 확인해 주세요.", "code": "INVALID_CAMPUS"}), 400

    my_lat, my_lon = data.get('lat'), data.get('lon')
    if my_lat is None and my_lon is None:
        # 선택된 캠퍼스 안에서만 조회해 동명 건물의 좌표가 섞이지 않게 한다.
        my_lat, my_lon = CAMPUS_BUILDINGS[campus].get(my_location, (None, None))
    try:
        origin = {
            "name": my_location,
            "lat": parse_coordinate(my_lat, -90, 90),
            "lon": parse_coordinate(my_lon, -180, 180),
        }
    except (ValueError, TypeError):
        return jsonify({"error": "출발 위치를 찾을 수 없습니다. GPS 또는 출발 건물을 다시 선택하세요.", "code": "INVALID_ORIGIN"}), 400

    destination = None
    dest_lat, dest_lon = data.get('dest_lat'), data.get('dest_lon')
    if dest_lat is not None or dest_lon is not None or data.get('dest_name'):
        try:
            destination = {
                "name": str(data.get('dest_name') or '선택한 목적지'),
                "lat": parse_coordinate(dest_lat, -90, 90),
                "lon": parse_coordinate(dest_lon, -180, 180),
            }
        except (ValueError, TypeError):
            return jsonify({"error": "목적지 좌표를 확인할 수 없습니다. 검색 결과에서 장소를 다시 선택하세요.", "code": "INVALID_DESTINATION"}), 400

    try:
        if destination is not None:
            return jsonify(transit_service.plan(origin, destination))
        return jsonify(transit_service.nearby(origin))
    except PublicDataError as error:
        body = {"error": str(error), "code": error.code}
        if destination is not None:
            body.update({
                "mode": "journeys", "origin": origin, "destination": destination,
                "journeys": [], "warnings": [],
            })
        return jsonify(body), 502


if __name__ == '__main__':
    app.run(debug=True)
