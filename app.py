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
# 🏫 2. CSV 데이터 로드
# ==========================================
HALLYM_BUILDINGS = {}
try:
    try:
        df_buildings = pd.read_csv('한림대학교 건물별 좌표.csv', encoding='utf-8')
    except UnicodeDecodeError:
        df_buildings = pd.read_csv('한림대학교 건물별 좌표.csv', encoding='cp949')
        
    for index, row in df_buildings.iterrows():
        building_name = str(row['건물명']).strip()
        HALLYM_BUILDINGS[building_name] = (float(row['위도(lat)']), float(row['경도(lon)']))
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

def get_arrival_info(node_id):
    url = f"http://apis.data.go.kr/1613000/ArvlInfoInqireService/getSttnAcctoArvlPrearngeInfoList?serviceKey={PUBLIC_DATA_KEY}&cityCode={CITY_CODE}&nodeId={node_id}&numOfRows=5&pageNo=1&_type=json"
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
    building_names = list(HALLYM_BUILDINGS.keys())
    return render_template('index.html', buildings=building_names)

@app.route('/get_route_info', methods=['POST'])
def get_route_info():
    data = request.get_json()
    
    # 프론트에서 넘어온 좌표와 위치명 확인
    my_lat = data.get('lat')
    my_lon = data.get('lon')
    my_location = data.get('location', '현재 위치') 

    # 수동 검색인 경우 건물 이름으로 좌표 검색
    if not my_lat or not my_lon:
        if my_location and my_location != '현재 위치':
            my_lat, my_lon = HALLYM_BUILDINGS.get(my_location, (None, None))
            
    if not my_lat:
        return jsonify({"error": "위치를 찾을 수 없습니다."})

    stations = get_nearby_stations(my_lat, my_lon)[:3]
    result_data = []

    for sttn in stations:
        sttn_name = sttn.get("nodenm")
        sttn_lat, sttn_lon = float(sttn.get("gpslati")), float(sttn.get("gpslong"))
        node_id = sttn.get("nodeid")
        
        walk_min, walk_dist = get_walking_time(my_lat, my_lon, my_location, sttn_lat, sttn_lon, sttn_name)
        arrivals = get_arrival_info(node_id)
        
        bus_list = []
        for arr in arrivals:
            bus_list.append({
                "route": arr.get('routeno'),
                "min_left": arr.get('arrtime', 0) // 60
            })
            
        result_data.append({
            "station_name": sttn_name,
            "walk_min": walk_min,
            "walk_dist": walk_dist,
            "buses": bus_list
        })

    return jsonify(result_data)

if __name__ == '__main__':
    app.run(debug=True)
