"""食物 API：搜索、详情、扫码、自建食物（拍照纠正/库外食物的入口）。"""
from flask import Blueprint, g, jsonify, request

from app.agent.tools.food_tools import fetch_barcode_info, food_to_dict, search_food_database
from app.extensions import db
from app.models import Food
from app.routes.auth_utils import login_required

bp = Blueprint("food", __name__, url_prefix="/foods")


@bp.get("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "缺少 q 参数"}), 400
    try:
        limit = min(int(request.args.get("limit", 5)), 20)
    except ValueError:
        limit = 5
    return jsonify(search_food_database(q, limit))


@bp.get("/<int:food_id>")
@login_required
def food_detail(food_id):
    food = db.session.get(Food, food_id)
    if food is None:
        return jsonify({"ok": False, "error": "食物不存在"}), 404
    return jsonify({"ok": True, "food": food_to_dict(food)})


@bp.post("/barcode")
@login_required
def barcode():
    data = request.get_json(silent=True) or {}
    code = (data.get("barcode") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "barcode 必填"}), 400
    return jsonify(fetch_barcode_info(code))


@bp.post("/custom")
@login_required
def create_custom_food():
    """自建食物：拍照识别后用户纠正、或食物库外的食物，可沉淀为用户自定义条目。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name or data.get("calories_kcal_per_100g") is None:
        return jsonify({"ok": False, "error": "name 与 calories_kcal_per_100g 必填"}), 400
    food = Food(
        name=name,
        category=data.get("category") or "other",
        unit=data.get("unit") or "g",
        default_portion_g=data.get("default_portion_g"),
        calories_kcal_per_100g=float(data["calories_kcal_per_100g"]),
        protein_g_per_100g=float(data.get("protein_g_per_100g") or 0),
        fat_g_per_100g=float(data.get("fat_g_per_100g") or 0),
        carbs_g_per_100g=float(data.get("carbs_g_per_100g") or 0),
        fiber_g_per_100g=float(data.get("fiber_g_per_100g") or 0),
        sodium_mg_per_100g=data.get("sodium_mg_per_100g"),
        source="custom",
        created_by=g.user_id,
    )
    db.session.add(food)
    db.session.commit()
    return jsonify({"ok": True, "food": food_to_dict(food)})
