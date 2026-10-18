"""饮食打卡 API：今日明细（按餐次分组）、按日期查询、手动打卡、删除。"""
from datetime import date, datetime, time, timedelta

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from app.agent.tools.context_tools import _day_start, get_today_diet_summary
from app.agent.tools.record_tools import record_diet_to_db
from app.extensions import db
from app.models import DietRecord
from app.routes.auth_utils import login_required
from app.routes.utils import parse_date, parse_datetime, record_to_dict

bp = Blueprint("diet", __name__, url_prefix="/diet")


@bp.get("/today")
@login_required
def today():
    """今日打卡明细（按餐次分组）+ 摄入汇总 + 剩余额度（首页/打卡页数据源）。"""
    result = get_today_diet_summary(g.user_id)
    data = {k: v for k, v in result.items() if k != "ok"}

    start = _day_start(date.today())
    rows = db.session.execute(
        select(DietRecord)
        .where(DietRecord.user_id == g.user_id, DietRecord.eaten_at >= start)
        .order_by(DietRecord.eaten_at.desc())
    ).scalars().all()
    grouped = {"breakfast": [], "lunch": [], "dinner": [], "snack": []}
    for r in rows:
        grouped[r.meal_type].append(record_to_dict(r))
    data["meals"] = grouped
    return jsonify({"ok": True, **data})


@bp.get("/records")
@login_required
def records_by_date():
    """按日期查询打卡记录：GET /api/diet/records?date=2026-10-18"""
    d = parse_date(request.args.get("date"))
    start = datetime.combine(d, time.min)
    end = datetime.combine(d + timedelta(days=1), time.min)
    rows = db.session.execute(
        select(DietRecord)
        .where(DietRecord.user_id == g.user_id,
               DietRecord.eaten_at >= start, DietRecord.eaten_at < end)
        .order_by(DietRecord.eaten_at)
    ).scalars().all()
    return jsonify({"ok": True, "date": str(d), "records": [record_to_dict(r) for r in rows]})


@bp.post("/records")
@login_required
def add_records():
    """手动打卡：与 Agent 打卡共用同一套换算逻辑（record_diet_to_db）。"""
    data = request.get_json(silent=True) or {}
    meal_type = data.get("meal_type") or "snack"
    if meal_type not in ("breakfast", "lunch", "dinner", "snack"):
        return jsonify({"ok": False, "error": "非法餐次"}), 400
    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "items 必填且为非空数组"}), 400

    result = record_diet_to_db(g.user_id, meal_type, items,
                               source=data.get("source") or "manual",
                               eaten_at=parse_datetime(data.get("eaten_at")))
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.delete("/records/<int:record_id>")
@login_required
def delete_record(record_id):
    r = db.session.get(DietRecord, record_id)
    if r is None or r.user_id != g.user_id:
        return jsonify({"ok": False, "error": "记录不存在"}), 404
    db.session.delete(r)
    db.session.commit()
    return jsonify({"ok": True, "deleted_id": record_id})
