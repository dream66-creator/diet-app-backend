"""路由共享小工具。"""
from datetime import date, datetime


def parse_datetime(value):
    """解析 ISO 时间字符串（如 '2026-10-18 12:30:00'），失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_date(value):
    """解析 'YYYY-MM-DD'，失败返回今天。"""
    if not value:
        return date.today()
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return date.today()


def record_to_dict(r) -> dict:
    return {
        "record_id": r.id,
        "meal_type": r.meal_type,
        "food_id": r.food_id,
        "food_name": r.food_name,
        "amount_g": r.amount_g,
        "calories_kcal": r.calories_kcal,
        "protein_g": r.protein_g,
        "fat_g": r.fat_g,
        "carbs_g": r.carbs_g,
        "fiber_g": r.fiber_g,
        "sodium_mg": r.sodium_mg,
        "source": r.source,
        "eaten_at": r.eaten_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
