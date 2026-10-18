"""用户档案 API：读取（含采集缺失项）与更新（Web 端表单，与 Agent 采集共用落库逻辑）。"""
from flask import Blueprint, g, jsonify, request

from app.agent.tools.profile_tools import needs_onboarding, update_user_profile
from app.routes.auth_utils import get_current_user, login_required

bp = Blueprint("profile", __name__, url_prefix="/profile")

_UPDATABLE = {"gender", "age", "height_cm", "weight_kg", "activity_level", "goal",
              "budget_per_day", "water_goal_ml", "nickname", "dietary_preferences",
              "target_calories", "target_protein_g", "target_fat_g", "target_carbs_g",
              "recompute_targets"}


@bp.get("")
@login_required
def get_profile():
    user = get_current_user()
    return jsonify({"ok": True, "profile": user.to_dict(),
                    "missing_fields": needs_onboarding(user)})


@bp.put("")
@login_required
def put_profile():
    data = request.get_json(silent=True) or {}
    payload = {k: v for k, v in data.items() if k in _UPDATABLE}
    if not payload:
        return jsonify({"ok": False, "error": "没有可更新的字段"}), 400
    result = update_user_profile(g.user_id, **payload)
    return jsonify(result), (200 if result.get("ok") else 400)
