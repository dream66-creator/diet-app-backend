"""API 蓝图注册中心：统一挂载各资源路由与全局错误处理。"""
from flask import Blueprint, jsonify

api = Blueprint("api", __name__, url_prefix="/api")


@api.get("/health")
def health():
    return jsonify({"status": "ok", "service": "diet-agent"})


def register_routes(app):
    from app.routes import (auth, chat, dashboard, diet, food, meal,
                            profile, upload, water)

    for bp in (auth.bp, chat.bp, dashboard.bp, diet.bp, food.bp,
               meal.bp, profile.bp, upload.bp, water.bp):
        api.register_blueprint(bp)
    app.register_blueprint(api)

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"ok": False, "error": "接口不存在"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify({"ok": False, "error": "请求方法不允许"}), 405

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"ok": False, "error": "上传内容过大"}), 413
