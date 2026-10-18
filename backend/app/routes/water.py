"""饮水 API：记录、今日进度、删除。"""
from datetime import date

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from app.agent.tools.context_tools import _day_start
from app.agent.tools.record_tools import add_water_intake
from app.extensions import db
from app.models import WaterRecord
from app.routes.auth_utils import get_current_user, login_required

bp = Blueprint("water", __name__, url_prefix="/water")


@bp.post("")
@login_required
def add_water():
    data = request.get_json(silent=True) or {}
    try:
        volume = int(data.get("volume_ml") or 0)
    except (TypeError, ValueError):
        volume = 0
    if volume <= 0:
        return jsonify({"ok": False, "error": "volume_ml 必须为正整数"}), 400
    return jsonify(add_water_intake(g.user_id, volume))


@bp.get("/today")
@login_required
def water_today():
    """今日饮水汇总与明细（仪表盘水杯进度数据源）。"""
    start = _day_start(date.today())
    rows = db.session.execute(
        select(WaterRecord)
        .where(WaterRecord.user_id == g.user_id, WaterRecord.drank_at >= start)
        .order_by(WaterRecord.drank_at.desc())
    ).scalars().all()
    total = sum(r.volume_ml for r in rows)
    user = get_current_user()
    return jsonify({
        "ok": True,
        "date": str(date.today()),
        "total_ml": total,
        "goal_ml": user.water_goal_ml,
        "remaining_ml": max(0, user.water_goal_ml - total),
        "progress_pct": round(min(100.0, total / user.water_goal_ml * 100), 1),
        "records": [{"record_id": r.id, "volume_ml": r.volume_ml,
                     "drank_at": r.drank_at.strftime("%Y-%m-%d %H:%M:%S")} for r in rows],
    })


@bp.delete("/<int:record_id>")
@login_required
def delete_water(record_id):
    r = db.session.get(WaterRecord, record_id)
    if r is None or r.user_id != g.user_id:
        return jsonify({"ok": False, "error": "记录不存在"}), 404
    db.session.delete(r)
    db.session.commit()
    return jsonify({"ok": True, "deleted_id": record_id})
