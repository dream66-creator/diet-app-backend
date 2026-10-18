"""注册 / 登录：签发无状态 Token，前端存 localStorage 后随 Authorization 头携带。"""
from flask import Blueprint, jsonify, request
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User
from app.routes.auth_utils import make_token

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if len(username) < 2 or len(password) < 6:
        return jsonify({"ok": False, "error": "用户名至少 2 位，密码至少 6 位"}), 400
    exists = db.session.execute(
        select(User).where(User.username == username)).scalar_one_or_none()
    if exists is not None:
        return jsonify({"ok": False, "error": "用户名已存在"}), 409

    user = User(username=username,
                password_hash=generate_password_hash(password),
                nickname=data.get("nickname") or username)
    db.session.add(user)
    db.session.commit()
    return jsonify({"ok": True, "user_id": user.id,
                    "token": make_token(user.id), "nickname": user.nickname})


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    user = db.session.execute(
        select(User).where(User.username == (data.get("username") or "").strip())
    ).scalar_one_or_none()
    if user is None or not check_password_hash(user.password_hash, data.get("password") or ""):
        return jsonify({"ok": False, "error": "用户名或密码错误"}), 401
    return jsonify({"ok": True, "user_id": user.id,
                    "token": make_token(user.id), "nickname": user.nickname})
