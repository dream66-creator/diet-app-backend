"""ORM 模型汇总：from app import models 即可让 db.create_all() 感知全部表。"""
from app.models.diet import DietRecord
from app.models.food import Food
from app.models.memory import ConversationMessage
from app.models.plan import FavoriteMeal, MealPlan
from app.models.user import User, WeightLog
from app.models.water import WaterRecord

__all__ = [
    "User", "WeightLog", "Food", "DietRecord", "WaterRecord",
    "FavoriteMeal", "MealPlan", "ConversationMessage",
]
