# lumix_check_connection.py
# Panasonic Lumix DC-G100D 接続確認スクリプト v4
#
# v4変更点:
#   LUMIX Syncアプリが送る接続開始シーケンスを再現し、
#   cam.cgi を有効化してからコマンドを送る

import json
import time
import urllib.error
import urllib.request

CAMERA_IP = "192.168.54.1"
CAMERA_PORT = 60606
TIMEOUT_SEC = 5
BASE_URL = f"http://{CAMERA_IP}:{CAMERA_PORT}"


def cam_get(path_and_query: str) -> tuple[int | None, str]:
    url = f"{BASE_URL}{path_and_query}"
    print(f"  GET {path_and_query}")
    try:
        req = urllib.request.Request(url)
        # LUMIX Syncアプリのユーザーエージェントを模倣
        req.add_header("User-Agent", "LUMIX Sync")
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"    → HTTP {resp.status}  body={body[:100]!r}")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"    → HTTP {e.code}  body={body[:100]!r}")
        return e.code, body
    except urllib.error.URLError as e:
        print(f"    → 接続失敗: {e.reason}")
        return None, str(e.reason)


print("=" * 56)
print("  Lumix DC-G100D 接続確認スクリプト v4")
print("=" * 56)
print(f"接続先: {BASE_URL}")
print()

# ============================================================
#  Step 1: アプリ接続開始シーケンスを再現
#  LUMIX Syncは以下の順でリクエストを送る
# ============================================================
print("=== Step 1: 接続開始シーケンス ===")

# 1-a: アクセス制御リクエスト（アプリ名を登録）
print("  [1-a] アクセス制御リクエスト")
status, body = cam_get("/cam.cgi?mode=accctrl&type=req_acc&value=0&value2=Windy")
time.sleep(0.5)

# 1-b: 接続確立（rec modeへ移行）
print("  [1-b] 撮影モード移行")
status, body = cam_get("/cam.cgi?mode=camcmd&value=recmode")
time.sleep(0.5)

# 1-c: 状態取得
print("  [1-c] 状態取得")
status, body = cam_get("/cam.cgi?mode=getstate")
time.sleep(0.3)

print()

# ============================================================
#  Step 2: cam.cgi が有効化されたか確認
# ============================================================
print("=== Step 2: API 有効化確認 ===")
status, body = cam_get("/cam.cgi?mode=getstate")

if status == 200:
    print()
    print("  ✓ cam.cgi 有効化成功！")
    try:
        data = json.loads(body)
        cam_mode = data.get("cammode", "不明")
        print(f"  カメラモード: {cam_mode}")
    except json.JSONDecodeError:
        print(f"  レスポンス（raw）: {body}")
elif status == 404:
    print()
    print("  ✗ まだ cam.cgi が応答しません。")
    print()
    print("  別のアプローチを試みます...")
    print()

    # ポート80経由（403だったが別パスを試す）
    print("=== Step 3: ポート80 の探索 ===")
    for path in ["/", "/cam.cgi?mode=getstate", "/cam.cgi"]:
        url = f"http://{CAMERA_IP}:80{path}"
        print(f"  GET http://{CAMERA_IP}:80{path}")
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "LUMIX Sync")
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                print(f"    → HTTP {resp.status}  body={body[:120]!r}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"    → HTTP {e.code}  body={body[:120]!r}")
        except Exception as e:
            print(f"    → エラー: {e}")
else:
    print(f"  予期しないレスポンス: HTTP {status}")

print()
print("=== 完了 ===")
print("この出力結果をそのまま共有してください。")
