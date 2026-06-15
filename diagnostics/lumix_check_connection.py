# lumix_check_connection.py
# Panasonic Lumix DC-G100D 接続確認スクリプト v5
#
# v5変更点:
#   - 正しいエンドポイント: http://192.168.54.1:80/cam.cgi
#   - レスポンスはXML形式
#   - 接続開始シーケンスを正しいポートで実行

import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

CAMERA_IP = "192.168.54.1"
CAMERA_PORT = 80
TIMEOUT_SEC = 5
BASE_URL = f"http://{CAMERA_IP}:{CAMERA_PORT}"


def cam_get(query: str) -> tuple[int | None, str, str | None]:
    """
    cam.cgi にGETリクエストを送る。
    戻り値: (HTTPステータス, rawボディ, result要素のテキスト or None)
    """
    url = f"{BASE_URL}/cam.cgi?{query}"
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "LUMIX Sync")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            result = parse_result(body)
            return resp.status, body, result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body, parse_result(body)
    except urllib.error.URLError as e:
        return None, str(e.reason), None


def parse_result(xml_body: str) -> str | None:
    """XMLから <result> タグの値を取得する"""
    try:
        root = ET.fromstring(xml_body)
        el = root.find("result")
        return el.text if el is not None else None
    except ET.ParseError:
        return None


def show(label: str, query: str) -> tuple[int | None, str, str | None]:
    status, body, result = cam_get(query)
    mark = "✓" if result == "ok" else "△" if status == 200 else "✗"
    print(f"  {mark} {label}")
    print(f"    query  : {query}")
    print(f"    HTTP   : {status}")
    print(f"    result : {result}")
    print(f"    body   : {body[:120]!r}")
    print()
    return status, body, result


print("=" * 56)
print("  Lumix DC-G100D 接続確認スクリプト v5")
print("=" * 56)
print(f"接続先: {BASE_URL}/cam.cgi")
print()

# ============================================================
#  Step 1: 接続開始シーケンス
# ============================================================
print("=== Step 1: 接続開始シーケンス ===")
print()

# 1-a: アクセス制御（アプリ名を登録）
show("アクセス制御リクエスト", "mode=accctrl&type=req_acc&value=0&value2=Windy")
time.sleep(0.5)

# 1-b: 撮影モードへ移行
show("撮影モード移行", "mode=camcmd&value=recmode")
time.sleep(1.0)

# ============================================================
#  Step 2: 状態取得
# ============================================================
print("=== Step 2: 状態取得 ===")
print()
status, body, result = show("getstate", "mode=getstate")

# ============================================================
#  Step 3: サマリー
# ============================================================
print("=== Step 3: 結果サマリー ===")
if result == "ok":
    # XMLからcammodeを取得
    try:
        root = ET.fromstring(body)
        cammode = root.findtext("cammode", default="不明")
    except ET.ParseError:
        cammode = "パースエラー"
    print("  ✓ 接続確認 OK")
    print(f"  カメラモード: {cammode}")
    print()
    print("  次のステップ: lumix_capture.py でシャッターテストを行います。")
elif result == "err_critical":
    print("  △ err_critical: セッション未確立")
    print("  → 接続開始シーケンスが必要か、カメラ側の操作が必要な可能性があります。")
    print()
    print("  カメラの画面に何か表示されているか確認してください。")
elif result == "err_param":
    print("  △ err_param: パラメータ不正（エンドポイント自体は正常）")
else:
    print(f"  ✗ 予期しない結果: {result}")

print()
print("=== 完了 ===")
