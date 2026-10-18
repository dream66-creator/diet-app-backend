"""Flask 应用工厂：组装配置、数据库、路由、种子数据与 Agent 单例。"""
import os

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import NotFound

from app.config import Config
from app.extensions import db


def _migrate_columns():
    """开发期轻量迁移：为已存在的 SQLite 表补充新增列（幂等）。

    正式迁移应使用 Alembic；此处仅为老库升级提供兼容路径。
    """
    from sqlalchemy import text

    engine = db.engine
    if engine.dialect.name != "sqlite":
        return
    additions = {"users": [("targets_customized", "BOOLEAN NOT NULL DEFAULT 0")]}
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info({})".format(table)))}
            for col, ddl in columns:
                if col not in existing:
                    conn.execute(text("ALTER TABLE {} ADD COLUMN {} {}".format(table, col, ddl)))


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    CORS(app)  # 前端（Vue3 开发服务器）跨域

    db.init_app(app)

    from app import models  # noqa: F401  注册全部 ORM 模型
    from app.routes import register_routes
    register_routes(app)

    # Agent 工具注册表 + 主循环单例（LLM 客户端懒加载，无 Key 也能启动）
    from app.agent.core import DietAgent
    from app.agent.tools import TOOL_REGISTRY  # noqa: F401
    app.extensions["agent_tools"] = TOOL_REGISTRY
    app.extensions["diet_agent"] = DietAgent()

    with app.app_context():
        db.create_all()  # 开发期自动建表；生产环境应改用迁移工具
        _migrate_columns()  # 轻量迁移：老库补充新增列
        if app.config.get("SEED_FOODS_ON_STARTUP", True):
            from app.seed import seed_foods
            seed_foods()

    # 上传文件（拍照识餐）静态服务
    upload_folder = app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_folder, exist_ok=True)

    @app.get("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(upload_folder, filename)

    # 前端构建产物（frontend/dist）静态托管：单端口交付模式。
    # 存在 dist 时，浏览器直接访问 http://localhost:5000 即可使用全部功能，
    # 无需另行启动 Vite；开发前端时仍可用 npm run dev + 代理。
    dist_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend", "dist"))
    if os.path.isdir(dist_dir):
        @app.get("/", defaults={"filename": "index.html"})
        @app.get("/<path:filename>")
        def frontend_files(filename):
            # API / 上传请求不参与 SPA 回退，保持 JSON 404 语义
            if filename.startswith(("api/", "uploads/")):
                return jsonify({"ok": False, "error": "接口不存在"}), 404
            try:
                return send_from_directory(dist_dir, filename)
            except NotFound:
                return send_from_directory(dist_dir, "index.html")  # SPA 回退

    return app
