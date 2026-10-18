"""收藏餐食 + 饮食方案（日历视图）API。

方案编辑（PUT）会服务端重算每个条目的营养 —— 前端改餐单后实时看到新营养合计。
"""
from datetime import date, datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from app.agent.tools import dispatch_tool
from app.agent.tools.plan_tools import compute_plan_nutrition
from app.agent.tools.record_tools import record_diet_to_db
from app.extensions import db
from app.models import FavoriteMeal, MealPlan
from app.routes.auth_utils import login_required
from app.routes.utils import parse_date

bp = Blueprint("meal", __name__)


# ---------- 收藏餐食 ----------

def _fav_dict(f):
    return {
        "favorite_id": f.id,
        "name": f.name,
        "items": f.items,
        "total_calories_kcal": f.total_calories_kcal,
        "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "last_used_at": f.last_used_at.strftime("%Y-%m-%d %H:%M:%S") if f.last_used_at else None,
    }


@bp.get("/favorites")
@login_required
def list_favorites():
    rows = db.session.execute(
        select(FavoriteMeal)
        .where(FavoriteMeal.user_id == g.user_id)
        .order_by(FavoriteMeal.last_used_at.desc().nullslast(), FavoriteMeal.id.desc())
    ).scalars().all()
    return jsonify({"ok": True, "favorites": [_fav_dict(f) for f in rows]})


@bp.post("/favorites")
@login_required
def create_favorite():
    """收藏一餐：items 只需 food_name/food_id/amount_g，营养由服务端换算。"""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    items = data.get("items") or []
    if not name or not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "name 与 items 必填且 items 非空"}), 400
    content, totals = compute_plan_nutrition({"checkin": items})
    fav = FavoriteMeal(user_id=g.user_id, name=name,
                       items=content["checkin"], total_calories_kcal=totals["calories_kcal"])
    db.session.add(fav)
    db.session.commit()
    return jsonify({"ok": True, "favorite": _fav_dict(fav)})


@bp.delete("/favorites/<int:fav_id>")
@login_required
def delete_favorite(fav_id):
    fav = db.session.get(FavoriteMeal, fav_id)
    if fav is None or fav.user_id != g.user_id:
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    db.session.delete(fav)
    db.session.commit()
    return jsonify({"ok": True, "deleted_id": fav_id})


@bp.post("/favorites/<int:fav_id>/checkin")
@login_required
def checkin_favorite(fav_id):
    """一键打卡：把收藏条目写入 diet_records 并刷新使用时间。"""
    fav = db.session.get(FavoriteMeal, fav_id)
    if fav is None or fav.user_id != g.user_id:
        return jsonify({"ok": False, "error": "收藏不存在"}), 404
    data = request.get_json(silent=True) or {}
    meal_type = data.get("meal_type") or "snack"
    if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
        return jsonify({"ok": False, "error": "非法餐次"}), 400
    items = [{"food_id": it.get("food_id"), "food_name": it["food_name"],
              "amount_g": it["amount_g"]} for it in fav.items]
    result = record_diet_to_db(g.user_id, meal_type, items, source="favorite")
    if result.get("ok"):
        fav.last_used_at = datetime.now()
        db.session.commit()
    return jsonify(result), (200 if result.get("ok") else 400)


# ---------- 饮食方案（日历视图） ----------

def _plan_dict(mp):
    return {
        "plan_id": mp.id,
        "plan_date": str(mp.plan_date),
        "target_calories_kcal": mp.target_calories_kcal,
        "content": mp.content,
        "note": mp.note,
        "updated_at": mp.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _next_month(year, month):
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


@bp.get("/plans")
@login_required
def list_plans():
    """按月或按日查询方案：GET /api/plans?month=2026-10 或 ?date=2026-10-19"""
    month = (request.args.get("month") or "").strip()
    if month:
        try:
            y, m = month.split("-")
            start_d, end_d = date(int(y), int(m), 1), _next_month(int(y), int(m))
        except ValueError:
            return jsonify({"ok": False, "error": "month 格式应为 YYYY-MM"}), 400
        cond = (MealPlan.plan_date >= start_d, MealPlan.plan_date < end_d)
    else:
        d = parse_date(request.args.get("date"))
        cond = (MealPlan.plan_date == d,)
    rows = db.session.execute(
        select(MealPlan)
        .where(MealPlan.user_id == g.user_id, *cond)
        .order_by(MealPlan.plan_date)
    ).scalars().all()
    return jsonify({"ok": True, "plans": [_plan_dict(mp) for mp in rows]})


@bp.get("/plans/<date_str>")
@login_required
def get_plan(date_str):
    d = parse_date(date_str)
    mp = db.session.execute(
        select(MealPlan)
        .where(MealPlan.user_id == g.user_id, MealPlan.plan_date == d)
    ).scalar_one_or_none()
    if mp is None:
        return jsonify({"ok": False, "error": "该日期没有方案"}), 404
    return jsonify({"ok": True, "plan": _plan_dict(mp)})


@bp.put("/plans/<date_str>")
@login_required
def update_plan(date_str):
    """编辑餐单：服务端重算营养后返回最新合计（实时重算）。"""
    d = parse_date(date_str)
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    if not isinstance(content, dict) or not content:
        return jsonify({"ok": False, "error": "content 必填且为非空对象"}), 400

    mp = db.session.execute(
        select(MealPlan)
        .where(MealPlan.user_id == g.user_id, MealPlan.plan_date == d)
    ).scalar_one_or_none()
    if mp is None:
        mp = MealPlan(user_id=g.user_id, plan_date=d, target_calories_kcal=0)
        db.session.add(mp)

    new_content, totals = compute_plan_nutrition(content)
    mp.content = new_content
    mp.target_calories_kcal = float(data.get("target_calories_kcal") or totals["calories_kcal"])
    if data.get("note") is not None:
        mp.note = str(data["note"])
    db.session.commit()
    return jsonify({"ok": True, "plan": _plan_dict(mp), "total_nutrition": totals})


@bp.post("/plans/generate")
@login_required
def generate_plan_api():
    """直接生成/重新生成方案（走 Agent 工具：LLM 可用时智能搭配，否则模板降级）。

    日历页「生成方案」按钮走本接口，不依赖聊天链路。
    """
    data = request.get_json(silent=True) or {}
    try:
        days = min(max(int(data.get("days") or 1), 1), 7)
    except (TypeError, ValueError):
        days = 1
    result = dispatch_tool("generate_meal_plan", {
        "user_id": g.user_id,
        "days": days,
        "budget_per_day": data.get("budget_per_day"),
        "avoid": data.get("avoid") or [],
        "only": data.get("only") or [],
        "preferences_note": data.get("preferences_note"),
        "start_date": data.get("date"),
    })
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.delete("/plans/<date_str>")
@login_required
def delete_plan(date_str):
    d = parse_date(date_str)
    mp = db.session.execute(
        select(MealPlan)
        .where(MealPlan.user_id == g.user_id, MealPlan.plan_date == d)
    ).scalar_one_or_none()
    if mp is None:
        return jsonify({"ok": False, "error": "该日期没有方案"}), 404
    db.session.delete(mp)
    db.session.commit()
    return jsonify({"ok": True, "deleted": str(d)})
