"""共享扩展实例：db 在 create_app 中 init_app 绑定应用。"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
