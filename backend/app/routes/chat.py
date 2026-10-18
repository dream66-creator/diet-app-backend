"""/api/chat：前端与 Agent 的对话入口。

支持附带图片：前端先 POST /api/upload/image 拿到 url，再随消息传 image_url，
Agent 会调用 analyze_food_image 识别（结果需用户确认后才打卡）。
"""
import uuid

from flask import Blueprint, current_app, g, jsonify, request

from app.routes.auth_utils import login_required

bp = Blueprint("chat", __name__, url_prefix="/chat")


@bp.post("")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    session_id = (data.get("session_id") or "").strip() or str(uuid.uuid4())

    if not message and not image_url:
        return jsonify({"ok": False, "error": "message 与 image_url 至少提供一个"}), 400

    if image_url:
        message = (message or "识别这张照片里的食物") + (
            "\n[系统提示：用户上传了食物照片，请调用 analyze_food_image 工具识别，"
            "参数 image_url 为：{}；识别结果需经用户确认后再打卡]".format(image_url))

    result = current_app.extensions["diet_agent"].chat(g.user_id, session_id, message)
    return jsonify({"ok": True, **result})
