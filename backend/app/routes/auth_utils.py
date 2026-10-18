"""极简 Token 认证：itsdangerous 签名、无状态、无需新增表（阶段 5 可替换为正式方案）。"""
from functools import wraps

from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

TOKEN_MAX_AGE = 30 * 24 * 3600  # 30 天


def make_token(user_id: int) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="diet-auth")
    return s.dumps({"uid": user_id})


def parse_token(token: str) -> int:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="diet-auth")
    return s.loads(token, max_age=TOKEN_MAX_AGE)["uid"]


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"ok": False, "error": "未登录，请先登录"}), 401
        try:
            g.user_id = parse_token(header[len("Bearer "):])
        except (BadSignature, SignatureExpired):
            return jsonify({"ok": False, "error": "登录已过期，请重新登录"}), 401
        return fn(*args, **kwargs)
    return wrapper


def get_current_user():
    from app.extensions import db
    from app.models import User
    return db.session.get(User, g.user_id)
