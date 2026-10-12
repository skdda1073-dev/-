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

# ==========================================
# 🛠 3. API 함수 정의
# ==========================================
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
    route_dest_map = {}
    directions = []
    try:
        res = requests.get(url, timeout=10)
        items = res.json().get("response", {}).get("body", {}).get("items", {})
        if items:
            item_list = items.get("item", [])
            item_list = [item_list] if isinstance(item_list, dict) else item_list
            for item in item_list:
                route_id = str(item.get("routeid", ""))
                route_no = str(item.get("routeno", ""))
                end_node = item.get("endnodenm", "")
                
                if end_node:
                    route_dest_map[route_id] = end_node
                    route_dest_map[route_no] = end_node
                    if end_node not in directions:
                        directions.append(end_node)
    except Exception as e:
        print("경유 노선 조회 오류:", e)
        
    direction_str = "·".join(directions[:2]) + " 방면" if directions else "방면 정보 없음"
    return route_dest_map, direction_str

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

@app.route('/get_route_info', methods=['POST'])
def get_route_info():
    data = request.get_json()
    
    my_lat = data.get('lat')
    my_lon = data.get('lon')
    my_location = data.get('location', '현재 위치')
    campus = data.get('campus', 'hallym')

    if not my_lat or not my_lon:
        if my_location and my_location != '현재 위치':
            my_lat, my_lon = CAMPUS_BUILDINGS.get(campus, {}).get(my_location, (None, None))
            if not my_lat:
                my_lat, my_lon = ALL_BUILDINGS.get(my_location, (None, None))
            
    if not my_lat:
        return jsonify({"error": "위치를 찾을 수 없습니다."})

    stations = get_nearby_stations(my_lat, my_lon)[:4]
    result_data = []

    for sttn in stations:
        sttn_name = sttn.get("nodenm")
        sttn_no = sttn.get("nodeno", "")
        sttn_lat, sttn_lon = float(sttn.get("gpslati")), float(sttn.get("gpslong"))
        node_id = sttn.get("nodeid")
        
        walk_min, walk_dist = get_walking_time(my_lat, my_lon, my_location, sttn_lat, sttn_lon, sttn_name)
        route_dest_map, direction_str = get_station_routes(node_id)
        arrivals = get_arrival_info(node_id)
        
        bus_list = []
        for arr in arrivals:
            route_id = str(arr.get('routeid', ''))
            route_no = str(arr.get('routeno', ''))
            destination = route_dest_map.get(route_id) or route_dest_map.get(route_no) or "종점"
            
            bus_list.append({
                "route": route_no,
                "destination": f"{destination}행",
                "min_left": arr.get('arrtime', 0) // 60,
                "prev_cnt": arr.get('arrprevstationcnt', 0)
            })
            
        bus_list.sort(key=lambda x: x["min_left"])
            
        result_data.append({
            "station_name": sttn_name,
            "station_no": sttn_no,
            "direction": direction_str,
            "walk_min": walk_min,
            "walk_dist": walk_dist,
            "buses": bus_list,
            "target_lat": my_lat,
            "target_lon": my_lon
        })

    return jsonify(result_data)

if __name__ == '__main__':
    app.run(debug=True)
