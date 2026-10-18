"""首页仪表盘聚合 API：今日摄入/目标/剩余、饮水进度、今日方案、档案完整度。

一次请求覆盖首页圆环 + 三大营养素进度条 + 水杯 + 方案卡所需数据。
"""
from datetime import date

from flask import Blueprint, g, jsonify
from sqlalchemy import select

from app.agent.tools.context_tools import get_today_diet_summary
from app.agent.tools.profile_tools import needs_onboarding
from app.extensions import db
from app.models import MealPlan
from app.routes.auth_utils import get_current_user, login_required

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.get("")
@login_required
def dashboard():
    user = get_current_user()
    summary = get_today_diet_summary(g.user_id)
    data = {k: v for k, v in summary.items() if k != "ok"}

    water_total = data["eaten"]["water_ml"]
    data["water_progress_pct"] = round(
        min(100.0, water_total / user.water_goal_ml * 100), 1)

    plan = db.session.execute(
        select(MealPlan)
        .where(MealPlan.user_id == g.user_id, MealPlan.plan_date == date.today())
    ).scalar_one_or_none()
    data["plan"] = {
        "plan_date": str(plan.plan_date),
        "target_calories_kcal": plan.target_calories_kcal,
        "content": plan.content,
        "note": plan.note,
    } if plan is not None else None

    data["profile_complete"] = not needs_onboarding(user)
    data["missing_fields"] = needs_onboarding(user)
    return jsonify({"ok": True, **data})
