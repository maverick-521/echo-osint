"""ECHO desktop app: a real window (Edge WebView2 via pywebview) around the local ECHO server.
Starts the hidden server if needed, never opens a browser tab. Falls back to the browser only if WebView is missing."""
import ctypes
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "http://127.0.0.1:%s/" % os.environ.get("ECHO_PORT", "8765")
ICON = os.path.join(ROOT, "app", "static", "echo.ico")
WIN = sys.platform == "win32"


def up():
    try:
        return urllib.request.urlopen(URL + "healthz", timeout=1.5).status == 200
    except Exception:
        return False


def start_server():
    exe = sys.executable
    if WIN and exe.lower().endswith("python.exe"):
        exe = exe[:-10] + "pythonw.exe"
    env = dict(os.environ, ECHO_NO_BROWSER="1")
    flags = 0x08000000 | 0x00000008 if WIN else 0  # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen([exe, os.path.join(ROOT, "app", "server.py")], cwd=ROOT, env=env, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    for _ in range(100):
        time.sleep(0.3)
        if up():
            return True
    return False


def single_instance():
    """Second double-click just focuses the existing window."""
    if not WIN:
        return True
    k = ctypes.windll.kernel32
    k.CreateMutexW(None, False, "Local\\ECHO.desktop.app")
    if k.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        u = ctypes.windll.user32
        h = u.FindWindowW(None, "ECHO")
        if h:
            u.ShowWindow(h, 9)
            u.SetForegroundWindow(h)
        return False
    return True


def set_icon(window):
    if not (WIN and os.path.exists(ICON)):
        return
    try:
        u = ctypes.windll.user32
        for _ in range(50):
            h = u.FindWindowW(None, "ECHO")
            if h:
                break
            time.sleep(0.1)
        for size, which in ((16, 0), (48, 1)):
            hi = u.LoadImageW(None, ICON, 1, size, size, 0x10)
            u.SendMessageW(h, 0x80, which, hi)
    except Exception:
        pass


def main():
    if WIN:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ECHO.Desktop")
        except Exception:
            pass
    if not single_instance():
        return
    if not up() and not start_server():
        ctypes.windll.user32.MessageBoxW(0, "ECHO's engine didn't start. Try run.bat once.", "ECHO", 0x10) if WIN else None
        return
    try:
        import webview
    except ImportError:
        import webbrowser
        webbrowser.open(URL)
        return
    w = webview.create_window("ECHO", URL, width=1180, height=860, min_size=(420, 640), background_color="#0E0F12",
                              text_select=True)
    webview.start(set_icon, w, private_mode=False, storage_path=os.path.join(ROOT, ".webview"))


if __name__ == "__main__":
    main()
