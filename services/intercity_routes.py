"""시외버스 HTTP 요청 검증과 JSON 응답을 담당한다."""

from flask import Blueprint, jsonify, request

from services.intercity_service import BOOKING_LINKS, IntercityError


def create_intercity_blueprint(service):
    bp = Blueprint("intercity", __name__, url_prefix="/api/intercity")

    @bp.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.errorhandler(IntercityError)
    def error_response(error):
        return jsonify(status="error", code=error.code, error=str(error),
                       today=service.today(), booking_links=BOOKING_LINKS), error.status_code

    def arguments(allowed):
        if any(name not in allowed or len(request.args.getlist(name)) != 1 for name in request.args):
            raise IntercityError("INVALID_INTERCITY_QUERY", "시외버스 조회 조건을 확인해 주세요.", 400)
        return {name: request.args.get(name) for name in allowed}

    @bp.get("/config")
    def config():
        arguments(set())
        return jsonify(service.configuration())

    @bp.get("/terminals")
    def terminals():
        query = arguments({"q"})
        return jsonify(service.terminals(query["q"] or ""))

    @bp.get("/timetable")
    def timetable():
        query = arguments({"departure_id", "arrival_id", "date"})
        return jsonify(service.timetable(query["departure_id"], query["arrival_id"], query["date"]))

    @bp.get("/destinations")
    def destinations():
        query = arguments({"departure_id"})
        return jsonify(service.destinations(query["departure_id"]))

    return bp
