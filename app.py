import os
from flask import Flask, render_template, request, jsonify
import requests
import urllib.parse
import pandas as pd

base_dir = os.path.abspath(os.path.dirname(__file__))
template_dir = os.path.join(base_dir, 'templates')

app = Flask(__name__, template_folder=template_dir)

# ==========================================
# 🔑 1. API 키 설정
# ==========================================
NAVER_CLIENT_ID = "gthv6ddfee"
NAVER_CLIENT_SECRET = "fPXCfIgHCdzW7XaH3q421qXGLbtaWEpBPrIXbRex"
TMAP_APP_KEY = "lWNZN6c9qbafOyL5qAudP7335IOhHX8E2pvprQ4C"
PUBLIC_DATA_KEY = "a71451c7c7109143b19101cf7ff0fcb04f7278bf1424454a720d9a9d1db98ecb" 
CITY_CODE = "32010" # 춘천시

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

import math

# ==========================================
# 🛠 3. API 함수 정의 (이 부분부터 끝까지 덮어쓰기)
# ==========================================
def calc_bus_ride_minutes(lat1, lon1, lat2, lon2):
    """승차 정류장과 하차 정류장 사이의 좌표를 기반으로 예상 버스 탑승 시간(분)을 계산합니다."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_km = R * c
    # 시내 도로 굴곡 계수(1.35) 및 정차 포함 시내버스 평균 표정속도(20km/h) 반영
    return max(3, round((dist_km * 1.35) / 20 * 60))

def get_nearby_stations(lat, lon):
    url = f"http://apis.data.go.kr/1613000/BusSttnInfoInqireService/getCrdntPrxmtSttnList?serviceKey={PUBLIC_DATA_KEY}&gpsLati={lat}&gpsLong={lon}&numOfRows=5&pageNo=1&_type=json"
    res = requests.get(url, timeout=10)
    if res.status_code == 200:
        items = res.json().get("response", {}).get("body", {}).get("items", {})
        if items:
            item_list = items.get("item", [])
            return [item_list] if isinstance(item_list, dict) else item_list
    return []

def get_station_routes(node_id):
    url = f"http://apis.data.go.kr/1613000/BusSttnInfoInqireService/getSttnThrghRouteList?serviceKey={PUBLIC_DATA_KEY}&cityCode={CITY_CODE}&nodeid={node_id}&numOfRows=50&pageNo=1&_type=json"
    route_id_map = {}   # {routeid(CCB...): {"route_no": "300", "end_node": "남춘천역"}}
    route_no_map = {}   # {routeno("300"): "남춘천역"} -> 화면 표시용 순수 노선번호 사전
    directions = []
    try:
        res = requests.get(url, timeout=10)
        items = res.json().get("response", {}).get("body", {}).get("items", {})
        if items:
            item_list = items.get("item", [])
            item_list = [item_list] if isinstance(item_list, dict) else item_list
            for item in item_list:
                route_id = str(item.get("routeid", "")).strip()
                route_no = str(item.get("routeno", "")).strip()
                end_node = str(item.get("endnodenm", "")).strip()
                
                if route_id and route_no:
                    route_id_map[route_id] = {"route_no": route_no, "end_node": end_node}
                    route_no_map[route_no] = end_node
                    if end_node and end_node not in directions:
                        directions.append(end_node)
    except Exception as e:
        print("경유 노선 조회 오류:", e)
        
    direction_str = "·".join(directions[:2]) + " 방면" if directions else "방면 정보 없음"
    return route_id_map, route_no_map, direction_str

def get_arrival_info(node_id):
    url = f"http://apis.data.go.kr/1613000/ArvlInfoInqireService/getSttnAcctoArvlPrearngeInfoList?serviceKey={PUBLIC_DATA_KEY}&cityCode={CITY_CODE}&nodeId={node_id}&numOfRows=10&pageNo=1&_type=json"
    try:
        res = requests.get(url, timeout=10)
        items = res.json().get("response", {}).get("body", {}).get("items", {})
        if not items:
            return []
        item_list = items.get("item", [])
        return [item_list] if isinstance(item_list, dict) else item_list
    except:
        return []

def get_walking_time(start_lat, start_lon, start_name, end_lat, end_lon, end_name):
    url = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1"
    payload = {
        "startX": start_lon, "startY": start_lat, "endX": end_lon, "endY": end_lat,
        "startName": urllib.parse.quote(start_name), "endName": urllib.parse.quote(end_name)
    }
    headers = {"Accept": "application/json", "Content-Type": "application/json", "appKey": TMAP_APP_KEY}
    res = requests.post(url, json=payload, headers=headers)
    if res.status_code == 200:
        data = res.json()
        return round(data['features'][0]['properties']['totalTime'] / 60), data['features'][0]['properties']['totalDistance']
    return -1, -1

# ==========================================
# 🌐 4. 웹 페이지 라우팅 및 연동 로직
# ==========================================
@app.route('/')
def home():
    return render_template(
        'index.html',
        hallym_buildings=list(CAMPUS_BUILDINGS["hallym"].keys()),
        knu_buildings=list(CAMPUS_BUILDINGS["knu"].keys())
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
    data = request.get_json()
    
    my_lat = data.get('lat')
    my_lon = data.get('lon')
    my_location = data.get('location', '내 실시간 위치')
    campus = data.get('campus', 'hallym')

    dest_lat = data.get('dest_lat')
    dest_lon = data.get('dest_lon')
    dest_name = data.get('dest_name', '')

    if not my_lat or not my_lon:
        if my_location and my_location != '내 실시간 위치':
            my_lat, my_lon = CAMPUS_BUILDINGS.get(campus, {}).get(my_location, (None, None))
            if not my_lat:
                my_lat, my_lon = ALL_BUILDINGS.get(my_location, (None, None))
            
    if not my_lat:
        return jsonify({"error": "출발 위치를 찾을 수 없습니다."})

    # ★ 목적지가 선택된 경우: 목적지 주변 정류장의 노선 정보 수집 (CCB 코드가 아닌 순수 버스 번호만 저장)
    dest_route_by_id = {}
    dest_route_by_no = {}
    if dest_lat and dest_lon:
        dest_stations = get_nearby_stations(dest_lat, dest_lon)[:4]
        for d_sttn in dest_stations:
            d_node_id = d_sttn.get("nodeid")
            d_sttn_name = d_sttn.get("nodenm", "목적지 주변")
            d_lat = float(d_sttn.get("gpslati", dest_lat))
            d_lon = float(d_sttn.get("gpslong", dest_lon))
            d_id_map, _, _ = get_station_routes(d_node_id)
            
            for r_id, r_info in d_id_map.items():
                r_no = r_info["route_no"]
                info_pack = {
                    "route_no": r_no,
                    "alighting_station": d_sttn_name,
                    "dest_sttn_lat": d_lat,
                    "dest_sttn_lon": d_lon
                }
                if r_id not in dest_route_by_id:
                    dest_route_by_id[r_id] = info_pack
                if r_no not in dest_route_by_no:
                    dest_route_by_no[r_no] = info_pack

    stations = get_nearby_stations(my_lat, my_lon)[:4]
    result_data = []

    for sttn in stations:
        sttn_name = sttn.get("nodenm")
        sttn_no = sttn.get("nodeno", "")
        sttn_lat, sttn_lon = float(sttn.get("gpslati")), float(sttn.get("gpslong"))
        node_id = sttn.get("nodeid")
        
        walk_min, walk_dist = get_walking_time(my_lat, my_lon, my_location, sttn_lat, sttn_lon, sttn_name)
        safe_walk_min = walk_min if walk_min >= 0 else 0

        route_id_map, route_no_map, direction_str = get_station_routes(node_id)
        arrivals = get_arrival_info(node_id)

        # ★ 순수 버스 번호(예: 300, 15-S)만 깔끔하게 매칭하고 예상 탑승 시간 계산
        matched_routes = []
        seen_route_nos = set()
        if dest_lat and dest_lon:
            for r_id, r_info in route_id_map.items():
                r_no = r_info["route_no"]
                end_node = r_info["end_node"]
                match = dest_route_by_id.get(r_id) or dest_route_by_no.get(r_no)
                if match and r_no not in seen_route_nos:
                    ride_min = calc_bus_ride_minutes(sttn_lat, sttn_lon, match["dest_sttn_lat"], match["dest_sttn_lon"])
                    matched_routes.append({
                        "route_no": r_no,
                        "end_node": end_node,
                        "alighting_station": match["alighting_station"],
                        "ride_min": ride_min
                    })
                    seen_route_nos.add(r_no)

        has_recommended_bus = len(matched_routes) > 0
        bus_list = []

        for arr in arrivals:
            route_id = str(arr.get('routeid', '')).strip()
            route_no = str(arr.get('routeno', '')).strip()
            destination = (route_id_map.get(route_id, {}).get("end_node")) or route_no_map.get(route_no) or "종점"
            
            min_left = arr.get('arrtime', 0) // 60
            spare_min = min_left - safe_walk_min
            
            if spare_min > 0:
                status_badge = "여유"
                status_type = "relax"
                guide_msg = f"정류장 도착 후, {spare_min}분 정도의 여유가 있어요."
            elif spare_min == 0 or (spare_min == -1 and safe_walk_min >= 3):
                status_badge = "촉박"
                status_type = "hurry"
                guide_msg = "뛰어가야 도착할 거 같아요!"
            else:
                status_badge = "다음차"
                status_type = "miss"
                guide_msg = "뛰어도 못 갈 시간이에요. 다음 차를 기다리시는 게 좋을 거 같아요."

            match = dest_route_by_id.get(route_id) or dest_route_by_no.get(route_no)
            is_recommended = bool(match)
            alighting_station = match["alighting_station"] if match else ""
            ride_min = calc_bus_ride_minutes(sttn_lat, sttn_lon, match["dest_sttn_lat"], match["dest_sttn_lon"]) if match else 0

            if is_recommended:
                has_recommended_bus = True
            
            bus_list.append({
                "route": route_no,
                "destination": f"{destination}행",
                "min_left": min_left,
                "walk_min": safe_walk_min,
                "spare_min": spare_min,
                "status_badge": status_badge,
                "status_type": status_type,
                "guide_msg": guide_msg,
                "prev_cnt": arr.get('arrprevstationcnt', 0),
                "is_recommended": is_recommended,
                "alighting_station": alighting_station,
                "ride_min": ride_min
            })
            
        bus_list.sort(key=lambda x: (not x["is_recommended"], x["min_left"]))
            
        result_data.append({
            "start_name": my_location,
            "station_name": sttn_name,
            "station_no": sttn_no,
            "station_lat": sttn_lat,
            "station_lon": sttn_lon,
            "direction": direction_str,
            "walk_min": safe_walk_min,
            "walk_dist": walk_dist,
            "buses": bus_list,
            "has_recommended_bus": has_recommended_bus,
            "matched_routes": matched_routes,
            "target_lat": my_lat,
            "target_lon": my_lon
        })

    if dest_lat and dest_lon:
        # 목적지행 실시간 버스가 있고 탑승 가능한 정류장을 최우선 정렬
        result_data.sort(key=lambda x: (
            not x["has_recommended_bus"],
            not any(b["is_recommended"] and b["status_type"] != "miss" for b in x["buses"]),
            x["walk_min"]
        ))
    else:
        result_data.sort(key=lambda x: x["walk_min"])

    return jsonify(result_data)

if __name__ == '__main__':
    app.run(debug=True)
