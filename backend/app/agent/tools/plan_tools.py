"""工具 5：个性化食谱生成（Agent 核心能力）。

实现策略：LLM 优先、确定性模板兜底 ——
- 主路径：组装用户档案、目标营养、食物候选（已过滤忌口）与预算，让 LLM 输出 JSON 方案；
- 营养一律由服务端按食物库重算（不信任 LLM 计算）；
- LLM 不可用（无 Key / 超时 / 输出不合法）时降级为「类别模板 + 主食量微调」方案，保证功能可用。
"""
import json
from datetime import date, timedelta
from typing import List, Optional

from flask import current_app
from openai import OpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.tools.base import fail, ok, register_tool
from app.agent.tools.calc_tools import calculate_bmr_tdee
from app.agent.tools.profile_tools import needs_onboarding
from app.agent.tools.record_tools import _PER100_FIELDS, find_food
from app.extensions import db
from app.models import Food, MealPlan, User
from app.models.user import _age

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")


class MealPlanInput(BaseModel):
    user_id: int = Field(description="用户 ID")
    days: int = Field(default=1, ge=1, le=7, description="规划天数（1=单日，7=一周）")
    start_date: Optional[str] = Field(
        default=None, description="方案起始日期（YYYY-MM-DD）；缺省从明天开始")
    budget_per_day: Optional[float] = Field(
        default=None, ge=0, description="每日餐食预算（元），缺省取用户档案中的预算")
    avoid: List[str] = Field(
        default_factory=list, description="本次额外忌口（与用户档案中的忌口合并），如 ['花生', '香菜']")
    only: List[str] = Field(
        default_factory=list,
        description="食物白名单：只允许名称/别名包含这些关键词的食物，如 ['鸡蛋', '牛奶']；空列表则不限制")
    preferences_note: Optional[str] = Field(
        default=None, description="本次规划的特殊要求，如「午餐想吃面」「明天要轻断食」")


def compute_plan_nutrition(content: dict):
    """服务端重算方案条目营养（食物库按克数缩放），返回 (新 content, 总营养)。

    供本工具与 /api/plans 编辑接口共用 —— 前端改餐单后实时重算。
    """
    out, totals = {}, {"calories_kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    for meal_type, items in content.items():
        new_items = []
        for it in items or []:
            food = find_food(it.get("food_id"), it.get("food_name"))
            scale = float(it.get("amount_g") or 0) / 100.0
            if food is not None:
                nutrition = {k: round(getattr(food, attr) * scale, 1)
                             for k, attr in _PER100_FIELDS.items()}
            else:
                nutrition = {k: round(float(it.get(k) or 0), 1)
                             for k in ("calories_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")}
                nutrition["sodium_mg"] = it.get("sodium_mg")
            new_items.append({
                "food_id": it.get("food_id"),
                "food_name": it.get("food_name") or (food.name if food is not None else "未知食物"),
                "amount_g": round(float(it.get("amount_g") or 0), 1),
                **nutrition,
            })
            for k in totals:
                totals[k] = round(totals[k] + nutrition[k], 1)
        out[meal_type] = new_items
    return out, totals


def _whitelist_match(food, keyword: str) -> bool:
    """白名单匹配：食物名/别名等于关键词或以关键词开头。

    与忌口（avoid）的子串匹配不同：白名单用前缀匹配，
    避免「鸡蛋」误命中「西红柿炒鸡蛋」「番茄炒蛋」这类含蛋菜品。
    """
    for n in [food.name] + (food.aliases or []):
        n = n or ""
        if n == keyword or n.startswith(keyword) or keyword.startswith(n):
            return True
    return False


def _parse_start(value: Optional[str]) -> date:
    """方案起始日期；非法或缺省时从明天开始。"""
    if value:
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            pass
    return date.today() + timedelta(days=1)


def generate_meal_plan(user_id: int, days: int = 1, budget_per_day: Optional[float] = None,
                       avoid: Optional[List[str]] = None,
                       only: Optional[List[str]] = None,
                       preferences_note: Optional[str] = None,
                       start_date: Optional[str] = None) -> dict:
    """生成个性化食谱并落库 meal_plans（按日期 upsert，重新生成即覆盖）。"""
    user = db.session.get(User, user_id)
    if user is None:
        return fail("用户 {} 不存在".format(user_id))
    missing = needs_onboarding(user)
    if missing:
        return fail("用户档案不完整（缺失：{}），请先完成信息采集".format("、".join(missing)))
    avoid = avoid or []
    only = only or []

    target = calculate_bmr_tdee(
        gender=user.gender, age=_age(user.birth_date),
        height_cm=user.height_cm, weight_kg=user.weight_kg,
        activity_level=user.activity_level, goal=user.goal)
    if not target.get("ok"):
        return fail("目标计算失败")

    foods = db.session.execute(select(Food).order_by(Food.name)).scalars().all()
    candidates = [f for f in foods
                  if not any(a and (a in (f.name or "") or a in " ".join(f.aliases or []))
                             for a in avoid)]
    if only:
        candidates = [f for f in candidates if any(_whitelist_match(f, o) for o in only)]
    if not candidates:
        return fail("按食物限制（白名单/忌口）过滤后没有可用食物，请放宽限制")
    budget = budget_per_day if budget_per_day is not None else user.budget_per_day

    start = _parse_start(start_date)
    plans, used_fallback = _llm_plan(user, target, candidates, start, days,
                                     budget, avoid, only, preferences_note), False
    if plans is None:
        plans, used_fallback = _fallback_plan(target, candidates, days, whitelist=bool(only)), True

    saved = []
    for i, raw in enumerate(plans):
        plan_date = start + timedelta(days=i)
        content, totals = compute_plan_nutrition(raw["meals"])
        mp = db.session.execute(
            select(MealPlan).where(MealPlan.user_id == user_id, MealPlan.plan_date == plan_date)
        ).scalar_one_or_none()
        if mp is None:
            mp = MealPlan(user_id=user_id, plan_date=plan_date,
                          target_calories_kcal=target["target_calories"])
            db.session.add(mp)
        mp.content = content
        mp.target_calories_kcal = target["target_calories"]
        mp.note = raw.get("note") or ""
        saved.append({
            "plan_date": str(plan_date),
            "target_calories_kcal": target["target_calories"],
            "meals": content,
            "total_nutrition": totals,
            "note": mp.note,
        })
    db.session.commit()
    return ok(
        plans=saved, saved=True, days=days, budget_per_day=budget,
        used_fallback=used_fallback,
        note="方案已保存，可在日历视图查看与调整。{}".format(
            "（当前 LLM 不可用，已使用内置模板方案）" if used_fallback else ""))


def _llm_plan(user, target, candidates, start, days, budget, avoid, only, preferences_note):
    """LLM 生成 JSON 方案；任何失败返回 None（触发模板降级）。"""
    food_lines = [
        "- id={} {}（每100g: {}kcal, 蛋白{}g, 脂肪{}g, 碳水{}g, 分类:{}）".format(
            f.id, f.name, f.calories_kcal_per_100g, f.protein_g_per_100g,
            f.fat_g_per_100g, f.carbs_g_per_100g, f.category)
        for f in candidates]
    prompt = (
        "你是注册营养师，为用户设计{}天的饮食方案（从{}开始，每天早餐/午餐/晚餐，可选加餐）。\n"
        "约束：\n"
        "- 每日总热量目标 {} kcal，蛋白 {}g，碳水 {}g，脂肪 {}g；\n"
        "- 三餐热量配比约 早餐30% / 午餐40% / 晚餐30%；\n"
        "- 每日餐食预算约 {} 元；\n"
        "- 忌口（已过滤食物库）：{}；\n"
        "- 食物白名单（只能使用这些食物，其余一律不用）：{}；\n"
        "- 用户偏好补充：{}；\n"
        "候选食物（只能从中选择，可按克数自由调整）：\n{}\n"
        "输出严格的 JSON（不要输出其他内容），格式：\n"
        '{{"plans": [{{"meals": {{"breakfast": [{{"food_id": 1, "food_name": "燕麦片", "amount_g": 40}}], '
        '"lunch": [...], "dinner": [...], "snack": [...]}}, "note": "当日搭配说明"}}]}}'
    ).format(days, str(start), target["target_calories"],
             target["macros"]["protein_g"], target["macros"]["carbs_g"], target["macros"]["fat_g"],
             budget if budget is not None else "不限",
             "、".join(avoid) or "无",
             "、".join(only) if only else "无限制",
             preferences_note or "无", "\n".join(food_lines))

    try:
        client = OpenAI(api_key=current_app.config["LLM_API_KEY"] or "missing-key",
                        base_url=current_app.config["LLM_BASE_URL"])
        resp = client.chat.completions.create(
            model=current_app.config["LLM_MODEL"],
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            timeout=90,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return None

    valid_ids = {f.id for f in candidates}
    plans = []
    for p in (data.get("plans") or [])[:days]:
        meals = {mt: [] for mt in MEAL_TYPES}
        for mt in MEAL_TYPES:
            for it in (p.get("meals") or {}).get(mt, [])[:8]:
                fid = it.get("food_id")
                try:
                    amount = float(it.get("amount_g") or 0)
                except (TypeError, ValueError):
                    amount = 0
                if fid in valid_ids and 10 <= amount <= 1500:
                    meals[mt].append({"food_id": fid,
                                      "food_name": it.get("food_name") or "",
                                      "amount_g": round(amount, 1)})
        if any(meals[mt] for mt in ("breakfast", "lunch", "dinner")):
            plans.append({"meals": meals, "note": str(p.get("note") or "")})
    return plans if len(plans) >= days else None


def _fallback_plan(target, candidates, days, whitelist=False):
    """确定性模板：类别选品 + 主食量微调贴近三餐热量（LLM 不可用时的兜底）。

    白名单模式或可用食物很少时走 _fallback_whitelist（轮询分配，避免空餐）。
    """
    if whitelist or len(candidates) <= 4:
        return _fallback_whitelist(target, candidates, days)
    by_cat = {}
    for f in candidates:
        by_cat.setdefault(f.category, []).append(f)

    def pick(categories):
        exclude = set()
        for cat in categories:
            pool = [f for f in by_cat.get(cat, []) if f.name not in exclude]
            if pool:
                f = pool[0]
                exclude.add(f.name)
                yield f

    templates = [
        ("breakfast", ("staple", "meat", "dairy"), 0.30),
        ("lunch", ("staple", "meat", "vegetable"), 0.40),
        ("dinner", ("staple", "fish", "vegetable"), 0.30),
    ]
    plans = []
    for _ in range(days):
        meals = {mt: [] for mt in MEAL_TYPES}
        for mt, cats, ratio in templates:
            items = []
            for f in pick(cats):
                scale = (f.default_portion_g or 150) / 100.0
                items.append({
                    "food_id": f.id, "food_name": f.name,
                    "amount_g": round(f.default_portion_g or 150, 1),
                    "calories_kcal": round(f.calories_kcal_per_100g * scale, 1),
                    "protein_g": round(f.protein_g_per_100g * scale, 1),
                    "fat_g": round(f.fat_g_per_100g * scale, 1),
                    "carbs_g": round(f.carbs_g_per_100g * scale, 1),
                    "fiber_g": round(f.fiber_g_per_100g * scale, 1),
                    "sodium_mg": f.sodium_mg_per_100g,
                    "_cat": f.category,
                })
            if not items:
                continue
            meal_kcal = sum(i["calories_kcal"] for i in items)
            target_kcal = target["target_calories"] * ratio
            staples = [i for i in items if i["_cat"] == "staple" and i["calories_kcal"] > 0]
            if staples:
                s = staples[0]
                factor = (target_kcal - (meal_kcal - s["calories_kcal"])) / s["calories_kcal"]
                factor = max(0.3, min(factor, 3.0))
                for k in ("calories_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g"):
                    s[k] = round(s[k] * factor, 1)
                s["amount_g"] = round(s["amount_g"] * factor, 1)
            for i in items:
                i.pop("_cat")
            meals[mt] = items
        # 经 compute_plan_nutrition 统一重算（按食物库归一）
        content, _ = compute_plan_nutrition(meals)
        plans.append({"meals": content, "note": "内置模板方案（可按需在日历中调整）"})
    return plans


def _fallback_whitelist(target, candidates, days):
    """白名单/极简食物集的确定性方案：把可用食物分配进三餐，缩放首项贴近餐目标热量。"""
    foods = sorted(candidates, key=lambda f: f.calories_kcal_per_100g, reverse=True)
    slots = ("breakfast", "lunch", "dinner")
    ratios = (0.30, 0.40, 0.30)
    plans = []
    for _ in range(days):
        meals = {mt: [] for mt in MEAL_TYPES}
        for si, (slot, ratio) in enumerate(zip(slots, ratios)):
            if len(foods) <= 2:
                picks = list(foods)  # 食物极少时每餐都用全部食物
            else:
                picks = [f for j, f in enumerate(foods) if j % 3 == si]
            items = []
            for f in picks:
                scale = (f.default_portion_g or 100) / 100.0
                items.append({
                    "food_id": f.id, "food_name": f.name,
                    "amount_g": round(f.default_portion_g or 100, 1),
                    "calories_kcal": round(f.calories_kcal_per_100g * scale, 1),
                    "protein_g": round(f.protein_g_per_100g * scale, 1),
                    "fat_g": round(f.fat_g_per_100g * scale, 1),
                    "carbs_g": round(f.carbs_g_per_100g * scale, 1),
                    "fiber_g": round(f.fiber_g_per_100g * scale, 1),
                    "sodium_mg": f.sodium_mg_per_100g,
                })
            # 缩放首项（热量密度最高的食物）贴近该餐目标热量
            if items and items[0]["calories_kcal"] > 0:
                first = items[0]
                kcal = sum(i["calories_kcal"] for i in items)
                factor = (target["target_calories"] * ratio) / kcal
                factor = max(0.2, min(factor, 3.0))
                for k in ("calories_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g"):
                    first[k] = round(first[k] * factor, 1)
                first["amount_g"] = round(first["amount_g"] * factor, 1)
            meals[slot] = items
        content, _ = compute_plan_nutrition(meals)
        plans.append({"meals": content, "note": "内置模板方案（白名单限制模式，可按需在日历中调整）"})
    return plans


register_tool(
    "generate_meal_plan",
    "为用户生成个性化食谱方案：结合生理数据与目标计算热量/营养素，"
    "过滤忌口与过敏原，控制预算，生成每日（或一周）三餐+加餐的组合并保存到方案表。"
    "用户要求「规划明天的减脂餐」「帮我做一周食谱」「给我配个餐」时调用；"
    "用户提出食物限制时如实传递：只能吃某些食物（如「只有鸡蛋和牛奶」）用 only 白名单，"
    "不吃某物用 avoid，其他要求写进 preferences_note；"
    "需要指定起始日期时传 start_date（YYYY-MM-DD），缺省从明天开始。"
    "用户档案不完整时应先完成信息采集再调用。",
    MealPlanInput,
    generate_meal_plan,
)
