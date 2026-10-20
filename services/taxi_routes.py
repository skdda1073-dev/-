"""기존 통학 응답과 독립적인 택시 API Blueprint."""

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from services.mobility_validation import MobilityInputError, clean_text, coordinate, require_json_object
from services.taxi_service import TaxiError


def create_taxi_blueprint(taxi_service, campus_buildings):
    blueprint = Blueprint("taxi", __name__)

    @blueprint.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.errorhandler(MobilityInputError)
    @blueprint.errorhandler(TaxiError)
    def safe_error(error):
        return jsonify(code=error.code, error=str(error)), error.status_code

    @blueprint.errorhandler(RequestEntityTooLarge)
    def oversized_request(error):
        return jsonify(code="REQUEST_TOO_LARGE", error="요청 본문이 너무 큽니다."), 413

    @blueprint.post("/api/taxi/estimate")
    def estimate_taxi():
        data = require_json_object(request.get_json(silent=True))
        campus = data.get("campus", "hallym")
        if not isinstance(campus, str) or campus not in campus_buildings:
            raise MobilityInputError("INVALID_CAMPUS", "캠퍼스를 확인해 주세요.")
        location = clean_text(data.get("location"), "location", 100, default="내 실시간 위치")
        lat, lon = data.get("lat"), data.get("lon")
        if lat is None and lon is None:
            coordinates = campus_buildings[campus].get(location)
            if not isinstance(coordinates, (tuple, list)) or len(coordinates) != 2:
                raise MobilityInputError("INVALID_ORIGIN", "출발 건물이나 현재 위치를 확인해 주세요.")
            lat, lon = coordinates
        origin = {"name": location, "lat": coordinate(lat, -90, 90, "origin"),
                  "lon": coordinate(lon, -180, 180, "origin")}
        destination = {
            "name": clean_text(data.get("dest_name"), "destination_name", 100, default="선택한 목적지"),
            "lat": coordinate(data.get("dest_lat"), -90, 90, "destination"),
            "lon": coordinate(data.get("dest_lon"), -180, 180, "destination"),
        }
        split_count = data.get("split_count", 1)
        if isinstance(split_count, bool) or not isinstance(split_count, int) or not 1 <= split_count <= 4:
            raise MobilityInputError("INVALID_SPLIT_COUNT", "분담 인원은 1~4의 정수로 입력해 주세요.")
        return jsonify(taxi_service.estimate(origin, destination, split_count=split_count))

    return blueprint
