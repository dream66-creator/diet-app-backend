"""档案维护工具：update_user_profile —— 首次信息采集与档案更新的落库入口。

配合 core.DietAgent 的动态采集注入（needs_onboarding）：
- 系统检测缺失字段 → 注入采集指令 → Agent 逐项询问 → 本工具落库；
- 生理数据齐全时自动重算 BMR/TDEE 并缓存目标值（users.target_*）；
- 按产品要求：不主动询问忌口（dietary_preferences 仅在用户主动提及时更新）。
"""
from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.agent.tools.base import fail, ok, register_tool
from app.agent.tools.calc_tools import calculate_bmr_tdee
from app.extensions import db
from app.models import User
from app.models.user import _age


def needs_onboarding(user: User) -> List[str]:
    """返回档案「硬缺失」的中文字段名列表；空列表表示硬性档案已完整。

    注意：activity_level / goal 在库中有默认值（light / maintain），
    属可选收集项，不作为采集触发条件；忌口不采集。
    """
    missing = []
    if user.gender not in ("male", "female"):
        missing.append("性别")
    if not user.birth_date:
        missing.append("年龄")
    if user.height_cm is None:
        missing.append("身高")
    if user.weight_kg is None:
        missing.append("体重")
    return missing


class UpdateProfileInput(BaseModel):
    """所有字段可选：仅更新用户明确提供的项（信息采集对话中逐项落库）。"""
    user_id: int = Field(description="用户 ID")
    gender: Optional[Literal["male", "female"]] = Field(
        default=None, description="性别：male 男 / female 女")
    age: Optional[int] = Field(default=None, ge=5, le=100, description="年龄（岁）")
    height_cm: Optional[float] = Field(default=None, gt=0, le=250, description="身高（厘米）")
    weight_kg: Optional[float] = Field(default=None, gt=0, le=350, description="体重（公斤）")
    activity_level: Optional[Literal["sedentary", "light", "moderate", "active", "very_active"]] = Field(
        default=None, description="活动量等级")
    goal: Optional[Literal["lose_fat", "gain_muscle", "maintain", "control_sugar"]] = Field(
        default=None, description="目标：减脂/增肌/维持/控糖")
    budget_per_day: Optional[float] = Field(default=None, ge=0, description="每日餐食预算（元）")
    water_goal_ml: Optional[int] = Field(default=None, ge=500, le=5000, description="每日饮水目标（毫升）")
    nickname: Optional[str] = Field(default=None, max_length=32, description="用户称呼")
    dietary_preferences: Optional[dict] = Field(
        default=None,
        description="口味/忌口偏好字典（仅在用户主动提及时提供），合并进现有偏好，例 "
        "{\"avoid\": [\"香菜\"]}")
    # ---- 每日目标营养素：手动自定义（覆盖公式计算） ----
    target_calories: Optional[float] = Field(
        default=None, gt=0, le=10000, description="手动指定的每日热量目标（kcal）")
    target_protein_g: Optional[float] = Field(
        default=None, gt=0, le=1000, description="手动指定的每日蛋白质目标（g）")
    target_fat_g: Optional[float] = Field(
        default=None, gt=0, le=1000, description="手动指定的每日脂肪目标（g）")
    target_carbs_g: Optional[float] = Field(
        default=None, gt=0, le=2000, description="手动指定的每日碳水目标（g）")
    recompute_targets: bool = Field(
        default=False,
        description="true 时放弃自定义目标，按 Mifflin-St Jeor 公式重新计算热量与营养素目标")


def update_user_profile(user_id: int, gender=None, age=None, height_cm=None, weight_kg=None,
                        activity_level=None, goal=None, budget_per_day=None,
                        water_goal_ml=None, nickname=None, dietary_preferences=None,
                        target_calories=None, target_protein_g=None,
                        target_fat_g=None, target_carbs_g=None,
                        recompute_targets=False) -> dict:
    """部分更新用户档案；生理数据齐全时自动重算并缓存热量/营养素目标。

    目标营养素三种模式：
    - 首次采集完整 → 按公式自动重算；
    - 手动提供 target_* 任一值 → 保存自定义值，此后公式不再自动覆盖；
    - recompute_targets=true → 放弃自定义，恢复公式计算。
    """
    user = db.session.get(User, user_id)
    if user is None:
        return fail("用户 {} 不存在".format(user_id))

    updated = []

    def _set(attr, value):
        if value is not None:
            setattr(user, attr, value)
            updated.append(attr)

    _set("gender", gender)
    _set("height_cm", height_cm)
    _set("weight_kg", weight_kg)
    _set("activity_level", activity_level)
    _set("goal", goal)
    _set("budget_per_day", budget_per_day)
    _set("water_goal_ml", water_goal_ml)
    _set("nickname", nickname)

    if age is not None:
        # 仅知年龄：出生月日未知，取当年 1 月 1 日，仅用于年龄换算
        user.birth_date = date(date.today().year - age, 1, 1)
        updated.append("age")
    if dietary_preferences is not None:
        user.dietary_preferences.update(dietary_preferences)
        updated.append("dietary_preferences")

    # ---- 自定义目标营养素：手动值优先于公式 ----
    target_fields = {
        "target_calories": target_calories,
        "target_protein_g": target_protein_g,
        "target_fat_g": target_fat_g,
        "target_carbs_g": target_carbs_g,
    }
    # 任一宏量营养素被修改时，热量按 4/4/9 供能系数自动换算（热量跟随宏量）
    macro_keys = ("target_protein_g", "target_fat_g", "target_carbs_g")
    if any(target_fields[k] is not None for k in macro_keys):
        p = target_fields["target_protein_g"] if target_fields["target_protein_g"] is not None else (user.target_protein_g or 0)
        f = target_fields["target_fat_g"] if target_fields["target_fat_g"] is not None else (user.target_fat_g or 0)
        c = target_fields["target_carbs_g"] if target_fields["target_carbs_g"] is not None else (user.target_carbs_g or 0)
        target_fields["target_calories"] = round(4 * p + 4 * c + 9 * f)
    custom_values = {k: v for k, v in target_fields.items() if v is not None}
    for attr, value in custom_values.items():
        setattr(user, attr, value)
        updated.append(attr)
    if custom_values:
        user.targets_customized = True
        updated.append("targets_customized")

    if not updated and not recompute_targets:
        return ok(profile=user.to_dict(), updated=[], note="未提供任何新信息")

    note = ""
    if custom_values and not recompute_targets:
        note = "已保存自定义目标（将不再随公式自动调整）"
    if recompute_targets or (not custom_values and not user.targets_customized
                             and not needs_onboarding(user)):
        missing = needs_onboarding(user)
        if missing:
            note = "档案不完整（缺失：{}），无法自动计算目标".format("、".join(missing))
        else:
            # 生理数据齐全：重算并缓存目标，后续对话/食谱生成直接使用
            result = calculate_bmr_tdee(
                gender=user.gender, age=_age(user.birth_date),
                height_cm=user.height_cm, weight_kg=user.weight_kg,
                activity_level=user.activity_level, goal=user.goal)
            if result.get("ok"):
                user.bmr, user.tdee, user.target_calories = result["bmr"], result["tdee"], result["target_calories"]
                user.target_protein_g = result["macros"]["protein_g"]
                user.target_fat_g = result["macros"]["fat_g"]
                user.target_carbs_g = result["macros"]["carbs_g"]
                user.targets_customized = False
                note = "已按公式重算热量与营养素目标"

    db.session.commit()
    return ok(profile=user.to_dict(), updated=updated, note=note)


register_tool(
    "update_user_profile",
    "更新/保存用户档案：性别、年龄、身高、体重、活动量、目标、每日预算、饮水目标、称呼，"
    "以及用户主动提及的忌口偏好（dietary_preferences）。只传用户明确提供的字段。"
    "信息采集对话中用户每回答一项就调用一次；生理数据齐全时会自动重算并缓存热量与营养素目标。"
    "用户要求调整每日热量/蛋白质/碳水/脂肪目标时，传 target_calories/target_protein_g/"
    "target_fat_g/target_carbs_g 保存自定义值；用户要求恢复默认或按公式算时传 recompute_targets=true。"
    "不要主动询问或收集用户的忌口/过敏原，除非用户自己提起。",
    UpdateProfileInput,
    update_user_profile,
)
