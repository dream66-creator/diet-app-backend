"""图片上传：拍照识餐的入口。支持 multipart 文件或 JSON base64（data URL）。"""
import base64
import os
import uuid

from flask import Blueprint, current_app, g, jsonify, request

from app.routes.auth_utils import login_required

bp = Blueprint("upload", __name__, url_prefix="/upload")

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB


def _ext_from_data_url(header: str) -> str:
    """'data:image/png;base64' → '.png'"""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if ext.strip(".") in header:
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


@bp.post("/image")
@login_required
def upload_image():
    """保存图片并返回可访问 URL（/uploads/<filename>），随后可传给 /api/chat 的 image_url。"""
    if request.files and "file" in request.files:
        f = request.files["file"]
        raw = f.read()
        ext = os.path.splitext(f.filename or "")[1].lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
    else:
        payload = request.get_json(silent=True) or {}
        b64 = payload.get("image_base64") or ""
        if not b64:
            return jsonify({"ok": False, "error": "缺少 file 或 image_base64"}), 400
        header, _, body = b64.partition(",")  # 兼容 data:image/jpeg;base64,xxx
        ext = _ext_from_data_url(header)
        try:
            raw = base64.b64decode(body)
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "base64 解码失败"}), 400

    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return jsonify({"ok": False, "error": "图片为空或超过 8MB"}), 413

    filename = "{}{}".format(uuid.uuid4().hex, ext)
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    with open(path, "wb") as fh:
        fh.write(raw)
    return jsonify({"ok": True, "url": "/uploads/{}".format(filename)})
