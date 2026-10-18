"""全局配置：环境变量优先，开发默认值保证零配置可运行。"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未安装时静默跳过，使用系统环境变量


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # 数据库：默认 SQLite（零配置开发）；生产切换 MySQL，如
    # DATABASE_URL=mysql+pymysql://user:pass@host:3306/diet_app
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///diet_app.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # LLM（OpenAI 兼容协议，默认 DeepSeek；可切换 Qwen / OpenAI / Claude 网关等）
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
    # 拍照识餐用的多模态模型：DeepSeek 官方 API 暂无视觉模型，阶段 3 需另配视觉供应商
    LLM_VISION_MODEL = os.getenv("LLM_VISION_MODEL", "")

    # 外部条码查询 API（阶段 3 接入，例：中国物品编码中心 / Open Food Facts）
    BARCODE_API_URL = os.getenv("BARCODE_API_URL", "")

    # 食物库种子数据：启动时若 foods 表为空则自动写入（幂等）
    SEED_FOODS_ON_STARTUP = True

    # 启动时自动打开浏览器（单端口交付模式）
    AUTO_OPEN_BROWSER = os.getenv("AUTO_OPEN_BROWSER", "1") == "1"

    # 图片上传（拍照识餐）
    UPLOAD_FOLDER = os.getenv(
        "UPLOAD_FOLDER",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads"))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10MB

    # 视觉模型供应商（可与主 LLM 不同，DeepSeek 官方暂无视觉模型）
    LLM_VISION_BASE_URL = os.getenv("LLM_VISION_BASE_URL", LLM_BASE_URL)
    # 视觉模型独立 Key（如智谱 GLM-4V 的 {id}.{secret} 格式）；缺省复用主 LLM Key
    LLM_VISION_API_KEY = os.getenv("LLM_VISION_API_KEY", LLM_API_KEY)
