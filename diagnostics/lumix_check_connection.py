# lumix_check_connection.py
# Panasonic Lumix DC-G100D 接続確認スクリプト v6 (完成版)
#
# 確認済み仕様:
#   - エンドポイント: http://192.168.54.1:80/cam.cgi
#   - レスポンス形式: accctrl のみプレーンテキスト、それ以外はXML
#   - accctrl レスポンス:
#       ok_under_research / ok_under_research_no_msg → カメラ側でユーザー確認中
#       ok                                           → 接続許可済み
#   - 接続シーケンス: accctrl (ok待ち) → recmode → getstate


import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

CAMERA_IP = "192.168.54.1"
CAMERA_PORT = 80
TIMEOUT_SEC = 5
BASE_URL = f"http://{CAMERA_IP}:{CAMERA_PORT}"

# accctrl ポーリング設定
ACC_POLL_INTERVAL = 1.0  # 秒
ACC_POLL_TIMEOUT = 30.0  # 秒（カメラ操作の猶予）


def cam_get_raw(query: str) -> tuple[int | None, str]:
    """cam.cgi にGETリクエストを送り (HTTPステータス, rawボディ) を返す"""
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


def parse_xml_result(body: str) -> str | None:
    """XMLの <result> タグの値を返す"""
    try:
        root = ET.fromstring(body)
        el = root.find("result")
        return el.text if el is not None else None
    except ET.ParseError:
        return None


def cam_cmd(query: str) -> str | None:
    """コマンドを送り XML result を返す（失敗時は None）"""
    status, body = cam_get_raw(query)
    if status == 200:
        return parse_xml_result(body)
    return None


# ============================================================
print("=" * 56)
print("  Lumix DC-G100D 接続確認スクリプト v6")
print("=" * 56)
print(f"接続先: {BASE_URL}/cam.cgi")
print()

# ============================================================
#  Step 1: accctrl — カメラ側の許可を待つ
# ============================================================
print("=== Step 1: 接続許可リクエスト ===")
print("  カメラの画面に「接続を許可しますか？」が表示されたら")
print("  カメラ側で「はい」を選択してください。")
print()

start = time.time()
acc_result = None

while time.time() - start < ACC_POLL_TIMEOUT:
    status, body = cam_get_raw("mode=accctrl&type=req_acc&value=0&value2=Windy")
    acc_result = body.strip().split(",")[0] if status == 200 else None
    elapsed = time.time() - start

    if acc_result == "ok":
        print(f"  ✓ 接続許可されました（{elapsed:.1f}秒）")
        break
    elif acc_result in ("ok_under_research", "ok_under_research_no_msg"):
        print(f"  △ カメラ側で確認中... ({elapsed:.0f}秒経過) [{acc_result}]")
        time.sleep(ACC_POLL_INTERVAL)
    else:
        print(f"  ✗ 予期しないレスポンス: {body.strip()!r}")
        sys.exit(1)
else:
    print(f"  ✗ タイムアウト（{ACC_POLL_TIMEOUT}秒以内に許可されませんでした）")
    sys.exit(1)

print()

# ============================================================
#  Step 2: 撮影モードへ移行
# ============================================================
print("=== Step 2: 撮影モード移行 ===")
result = cam_cmd("mode=camcmd&value=recmode")
if result == "ok":
    print("  ✓ 撮影モード移行: OK")
else:
    print(f"  ✗ 撮影モード移行失敗: result={result}")
    sys.exit(1)

time.sleep(0.5)
print()

# ============================================================
#  Step 3: 状態取得
# ============================================================
print("=== Step 3: カメラ状態取得 ===")
status, body = cam_get_raw("mode=getstate")
result = parse_xml_result(body)

if result == "ok":
    try:
        root = ET.fromstring(body)
        cammode = root.findtext(".//cammode", default="不明")
        batt = root.findtext(".//batt", default="不明")
        rec = root.findtext(".//rec", default="不明")
    except ET.ParseError:
        cammode = batt = rec = "パースエラー"

    print("  ✓ 状態取得: OK")
    print(f"    カメラモード : {cammode}")
    print(f"    バッテリー   : {batt}")
    print(f"    録画状態     : {rec}")
else:
    print(f"  ✗ 状態取得失敗: result={result}")
    sys.exit(1)

print()

# ============================================================
#  Step 4: サマリー
# ============================================================
print("=== 結果サマリー ===")
print("  ✓ 接続確認 OK — HTTP API でカメラを制御できます。")
print()
print("  次のステップ:")
print("    lumix_capture.py を実行してシャッターテストを行ってください。")
print()
print("=== 完了 ===")
