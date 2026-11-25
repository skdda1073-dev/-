"""HTTP boundary for anonymous, same-origin ride rooms; no app imports."""

from hashlib import sha256
import hmac
import ipaddress
import secrets
import sqlite3
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException

from services.mobility_validation import MobilityInputError, require_json_object
from services.ride_room_service import RoomError
from services.taxi_service import TaxiError


def _is_loopback(value):
    try:
        return ipaddress.ip_address(value or "").is_loopback
    except ValueError:
        return False


def is_loopback_http_request():
    host = urlsplit(request.host_url).hostname
    return (request.scheme == "http" and _is_loopback(request.remote_addr)
            and (host == "localhost" or _is_loopback(host)))


def create_ride_room_blueprint(service, configured=True):
    blueprint = Blueprint("ride_rooms", __name__, url_prefix="/api/ride-rooms")

    def fail(code, message, status):
        return jsonify(code=code, error=message), status

    def public_https():
        # The local cloudflared connector preserves Host and sets X-Forwarded-Proto.
        # Forwarded host and requests from external proxy addresses are never trusted.
        return request.is_secure or (_is_loopback(request.remote_addr)
                                     and request.host.lower() == "tonghak.com"
                                     and request.headers.get("X-Forwarded-Proto") == "https")

    def actor_hash(prepare=False):
        token = session.get("ride_actor")
        if not isinstance(token, str) or len(token) < 40:
            if not prepare:
                raise RoomError("SESSION_REQUIRED", "참가 정보를 준비한 뒤 다시 시도해 주세요.", 403)
            token = secrets.token_urlsafe(48)
            session["ride_actor"] = token
        if prepare and not session.get("ride_csrf"):
            session["ride_csrf"] = secrets.token_urlsafe(32)
        return sha256(token.encode("utf-8")).hexdigest()

    def same_origin(value):
        try:
            parsed = urlsplit(value)
            current = urlsplit(request.host_url)
            return (parsed.scheme in {"http", "https"} and not parsed.username
                    and not parsed.password and parsed.scheme == ("https" if public_https() else current.scheme)
                    and parsed.netloc.lower() == current.netloc.lower())
        except (ValueError, AttributeError):
            return False

    @blueprint.before_request
    def protect_requests():
        if not configured or not current_app.secret_key:
            return fail("CHAT_NOT_CONFIGURED", "모집·채팅 설정을 준비 중입니다. 택시와 버스 검색은 이용할 수 있어요.", 503)
        if not public_https() and not is_loopback_http_request():
            return fail("CHAT_HTTPS_REQUIRED", "모집·채팅은 HTTPS 연결이 필요합니다.", 403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not request.is_json:
                return fail("INVALID_REQUEST", "JSON 객체로 요청해 주세요.", 400)
            expected = session.get("ride_csrf")
            actual = request.headers.get("X-CSRF-Token", "")
            if not isinstance(expected, str) or not actual or not hmac.compare_digest(expected.encode(), actual.encode()):
                return fail("CSRF_INVALID", "화면을 새로고침한 뒤 다시 시도해 주세요.", 403)
            source = request.headers.get("Origin")
            if source is None:
                source = request.headers.get("Referer", "")
            fetch_site = request.headers.get("Sec-Fetch-Site", "")
            if not same_origin(source) or fetch_site not in {"", "same-origin", "none"}:
                return fail("ORIGIN_INVALID", "같은 웹사이트에서 다시 요청해 주세요.", 403)
            actor_hash()
        try:
            service.purge_expired_rooms()
        except RoomError as error:
            if error.code != "ROOM_DB_UNAVAILABLE":
                raise
            # Retry on a later request without interrupting an available read.
            current_app.logger.debug("Ride room expiry cleanup will retry on the next request")

    @blueprint.after_request
    def private_response(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    def known_error(error):
        response = jsonify(code=error.code, error=str(error))
        response.status_code = error.status_code
        retry_after = getattr(error, "retry_after", None)
        if retry_after is not None:
            response.headers["Retry-After"] = str(max(1, int(retry_after)))
        return response

    blueprint.register_error_handler(RoomError, known_error)
    blueprint.register_error_handler(MobilityInputError, known_error)
    blueprint.register_error_handler(TaxiError, known_error)

    @blueprint.errorhandler(sqlite3.Error)
    def database_error(_error):
        return fail("ROOM_DB_UNAVAILABLE", "모집 정보를 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.", 503)

    @blueprint.errorhandler(HTTPException)
    def request_error(error):
        if error.code == 413:
            return fail("REQUEST_TOO_LARGE", "요청 내용이 너무 깁니다.", 413)
        return fail("INVALID_REQUEST", "요청 형식을 확인해 주세요.", 400)

    def body():
        return require_json_object(request.get_json(silent=True))

    @blueprint.get("/session")
    def prepare_session():
        actor_hash(prepare=True)
        return jsonify(ready=True, csrf_token=session["ride_csrf"])

    @blueprint.get("")
    def room_list():
        return jsonify(service.list_rooms(actor_hash(prepare=True), request.args.to_dict()))

    @blueprint.post("")
    def create_room():
        result, status = service.create_room(actor_hash(), body())
        return jsonify(result), status

    @blueprint.get("/<room_id>")
    def room_details(room_id):
        return jsonify(service.get_room(actor_hash(prepare=True), room_id))

    @blueprint.delete("/<room_id>")
    def delete_room(room_id):
        body()
        return jsonify(service.delete_room(actor_hash(), room_id))

    @blueprint.post("/<room_id>/join")
    def join_room(room_id):
        return jsonify(service.join_room(actor_hash(), room_id, body().get("nickname")))

    @blueprint.post("/<room_id>/leave")
    def leave_room(room_id):
        body()
        return jsonify(service.leave_room(actor_hash(), room_id))

    @blueprint.post("/<room_id>/close")
    def close_room(room_id):
        body()
        return jsonify(service.close_room(actor_hash(), room_id))

    @blueprint.get("/<room_id>/estimate")
    def room_estimate(room_id):
        return jsonify(service.get_estimate(actor_hash(prepare=True), room_id))

    @blueprint.get("/<room_id>/messages")
    def messages(room_id):
        return jsonify(service.get_messages(actor_hash(prepare=True), room_id,
                                             after_id=request.args.get("after_id"),
                                             limit=request.args.get("limit", "50")))

    @blueprint.post("/<room_id>/messages")
    def send_message(room_id):
        result, status = service.send_message(actor_hash(), room_id, body())
        return jsonify(result), status

    return blueprint
