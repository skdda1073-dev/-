"""통학 준비 안내 API. 사용자 위치·비밀키는 응답하지 않는다."""

from flask import Blueprint, jsonify, request

from services.commute_advice_service import CAMPUSES


def create_commute_advice_blueprint(service):
    blueprint = Blueprint("commute_advice", __name__)

    @blueprint.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @blueprint.get("/api/commute-advice")
    def advice():
        if any(name != "campus" or len(request.args.getlist(name)) != 1 for name in request.args):
            return jsonify(status="error", code="INVALID_COMMUTE_QUERY",
                           error="출발 캠퍼스를 다시 선택해 주세요."), 400
        campus = request.args.get("campus", "hallym")
        if campus not in CAMPUSES:
            return jsonify(status="error", code="INVALID_COMMUTE_CAMPUS",
                           error="출발 캠퍼스를 다시 선택해 주세요."), 400
        return jsonify(service.advice(campus))

    return blueprint
