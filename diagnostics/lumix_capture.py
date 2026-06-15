# lumix_capture.py
# Panasonic Lumix DC-G100D シャッター制御スクリプト
#
# 使い方:
#   python lumix_capture.py
#
# 事前条件:
#   - PCがカメラのSSID（G100D-XXXXXX）に接続済みであること
#   - lumix_check_connection.py で接続確認済みであること
#
# 終了コード:
#   0: 撮影成功
#   1: 接続失敗 / 撮影失敗

import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import time

CAMERA_IP        = "192.168.54.1"
CAMERA_PORT      = 80
TIMEOUT_SEC      = 5
BASE_URL         = f"http://{CAMERA_IP}:{CAMERA_PORT}"
ACC_POLL_INTERVAL = 1.0
ACC_POLL_TIMEOUT  = 15.0


# ============================================================
#  ユーティリティ
# ============================================================
def cam_get_raw(query: str) -> tuple[int | None, str]:
    url = f"{BASE_URL}/cam.cgi?{query}"
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "LUMIX Sync")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return None, str(e.reason)


def xml_result(body: str) -> str | None:
    try:
        root = ET.fromstring(body)
        el = root.find("result")
        return el.text if el is not None else None
    except ET.ParseError:
        return None


def cam_cmd(query: str) -> str | None:
    status, body = cam_get_raw(query)
    return xml_result(body) if status == 200 else None


def connect() -> bool:
    """接続シーケンス（accctrl → recmode）を実行する"""
    start = time.time()
    while time.time() - start < ACC_POLL_TIMEOUT:
        status, body = cam_get_raw(
            "mode=accctrl&type=req_acc&value=0&value2=Windy"
        )
        acc = body.strip().split(",")[0] if status == 200 else None
        if acc == "ok":
            break
        elif acc in ("ok_under_research", "ok_under_research_no_msg"):
            time.sleep(ACC_POLL_INTERVAL)
        else:
            print(f"エラー: accctrl 失敗 ({body.strip()!r})", file=sys.stderr)
            return False
    else:
        print("エラー: 接続タイムアウト", file=sys.stderr)
        return False

    result = cam_cmd("mode=camcmd&value=recmode")
    if result != "ok":
        print(f"エラー: recmode 移行失敗 (result={result})", file=sys.stderr)
        return False
    return True


def capture() -> bool:
    """シャッターを切る（AF → 撮影 → 解放）"""
    # AF開始
    result = cam_cmd("mode=camcmd&value=af_s_push")
    if result not in ("ok", None):  # 機種によりAFなしでもok
        print(f"警告: AF失敗 (result={result})", file=sys.stderr)

    time.sleep(0.5)  # AFのための待機

    # シャッター押下
    result = cam_cmd("mode=camcmd&value=capture")
    if result != "ok":
        print(f"エラー: シャッター失敗 (result={result})", file=sys.stderr)
        return False

    time.sleep(0.3)

    # シャッター解放
    cam_cmd("mode=camcmd&value=capture_cancel")

    return True


# ============================================================
#  メイン
# ============================================================
if __name__ == "__main__":
    print("=== Lumix DC-G100D 撮影スクリプト ===")

    print("接続中...")
    if not connect():
        sys.exit(1)
    print("接続OK")

    print("撮影中...")
    if not capture():
        sys.exit(1)
    print("撮影完了")

    sys.exit(0)
