"""启动入口：python run.py（或双击项目根目录的 启动.bat）。

单端口交付模式：若 frontend/dist 已构建，本服务同时托管前端页面与全部 API，
浏览器打开 http://localhost:5000 即可使用全部功能。
"""
import os
import socket
import threading
import webbrowser

from app import create_app

app = create_app()


def _lan_ip():
    """局域网 IP（供手机等设备访问时提示）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


if __name__ == "__main__":
    host = "0.0.0.0"  # 允许手机等局域网设备访问
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG") == "1"

    url = "http://localhost:{}".format(port)
    print("=" * 56)
    print("  饮食管家已启动：{}".format(url))
    lan = _lan_ip()
    if lan:
        print("  手机/局域网访问：http://{}:{}".format(lan, port))
    print("  关闭本窗口即停止服务")
    print("=" * 56)

    if not debug and app.config.get("AUTO_OPEN_BROWSER", True):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=host, port=port, debug=debug)
    except OSError as exc:
        # 双击启动时让用户看清失败原因（如端口被占用），而不是窗口一闪而过
        print()
        print("启动失败：{}".format(exc))
        print("可能原因：端口 {} 已被其他程序占用。".format(port))
        print("解决办法：关闭占用该端口的程序后重试，或设置环境变量 PORT 换一个端口。")
        input("按回车键退出...")
