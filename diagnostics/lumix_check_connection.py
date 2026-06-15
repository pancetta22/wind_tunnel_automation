# lumix_check_connection.py
# Panasonic Lumix DC-G100D 接続確認スクリプト
#
# 確認内容:
#   Step 1: カメラのIPアドレス・ポートへの疎通確認（ping相当）
#   Step 2: カメラ情報の取得（機種名・ファームウェアバージョン）
#   Step 3: 現在の撮影モード取得
#   Step 4: 結果のサマリー表示
#
# 事前準備:
#   1. カメラ側: [MENU/SET] → [セットアップ] → [Wi-Fi] → [Wi-Fi機能]
#                → [新規に接続する] → [スマートフォンとつないで使う]
#      （カメラの画面にSSID・パスワードが表示される）
#   2. PC側: 表示されたSSID（例: LUMIX-XXXX）にWiFi接続する
#   3. 本スクリプトを実行する


# Panasonic Lumix DC-G100D 接続確認スクリプト v2
#
# v2変更点: エンドポイントパスの候補を総当たりして特定する

import sys
import urllib.error
import urllib.request

CAMERA_IP = "192.168.54.1"
CAMERA_PORT = 60606
TIMEOUT_SEC = 5
BASE_URL = f"http://{CAMERA_IP}:{CAMERA_PORT}"

# DC-G100D で報告されているパスの候補
ENDPOINT_CANDIDATES = [
    "/cam.cgi",
    "/Lumix/Server0/cc",  # 新世代Lumix API
    "/v1/cameras/0",  # 別世代
]
QUERY = "mode=getstate"


def try_get(path: str, query: str) -> tuple[int | None, str]:
    """GETリクエストを送り (HTTPステータス, レスポンスボディ) を返す"""
    url = f"{BASE_URL}{path}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, str(e.reason)
    except urllib.error.URLError as e:
        return None, str(e.reason)
    except Exception as e:
        return None, str(e)


print("=" * 52)
print("  Lumix DC-G100D 接続確認スクリプト v2")
print("=" * 52)
print(f"接続先: {BASE_URL}")
print()

# ============================================================
#  Step 1: エンドポイント候補を総当たり
# ============================================================
print("=== Step 1: エンドポイント探索 ===")

found_path = None
for path in ENDPOINT_CANDIDATES:
    status, body = try_get(path, QUERY)
    if status is None:
        print(f"  {path:35s} → 接続失敗 ({body})")
    elif status == 200:
        print(f"  {path:35s} → HTTP {status} ✓  body={body[:80]}")
        if found_path is None:
            found_path = path
    else:
        print(f"  {path:35s} → HTTP {status}   body={body[:80]}")

print()

if found_path is None:
    print("NG: 有効なエンドポイントが見つかりませんでした。")
    print()
    print("追加情報として、カメラのルートにアクセスします...")
    status, body = try_get("/", "")
    print(f"  GET / → HTTP {status}")
    print(f"  body  = {body[:200]}")
    sys.exit(1)

# ============================================================
#  Step 2: 有効パスでカメラ状態を取得
# ============================================================
print(f"=== Step 2: カメラ状態取得（{found_path}） ===")
status, body = try_get(found_path, "mode=getstate")
print(f"  HTTP {status}")
print(f"  body = {body}")
print()

# ============================================================
#  Step 3: サマリー
# ============================================================
print("=== 結果サマリー ===")
print(f"  有効エンドポイント: {BASE_URL}{found_path}")
print()
print("  次のステップ: 上記エンドポイントを使って lumix_capture.py を作成します。")
