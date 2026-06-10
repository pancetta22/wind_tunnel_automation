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

import json
import sys
import urllib.request
import urllib.error

# ============================================================
#  設定
# ============================================================
CAMERA_IP   = "192.168.54.1"
CAMERA_PORT = 60606
TIMEOUT_SEC = 5
BASE_URL    = f"http://{CAMERA_IP}:{CAMERA_PORT}"


# ============================================================
#  ユーティリティ
# ============================================================
def cam_get(params: str) -> dict | None:
    """
    cam.cgi にGETリクエストを送り、JSONレスポンスを辞書で返す。
    失敗時は None を返す。
    """
    url = f"{BASE_URL}/cam.cgi?{params}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.URLError as e:
        print(f"  通信エラー: {e.reason}")
        return None
    except json.JSONDecodeError as e:
        print(f"  JSONパースエラー: {e}")
        return None
    except Exception as e:
        print(f"  予期しないエラー: {e}")
        return None


def result_str(data: dict | None) -> str:
    """APIレスポンスの result フィールドを返す（取得不能時は '?'）"""
    if data is None:
        return "?"
    return data.get("result", "?")


# ============================================================
#  Step 1: 疎通確認
# ============================================================
print("=" * 52)
print("  Lumix DC-G100D 接続確認スクリプト")
print("=" * 52)
print()
print(f"接続先: {BASE_URL}")
print()

print("=== Step 1: 疎通確認 ===")
data = cam_get("mode=getstate")
if data is None:
    print("  NG: カメラに接続できません。")
    print()
    print("  チェックリスト:")
    print("  □ カメラのWi-Fiが有効か（画面にSSIDが表示されているか）")
    print("  □ PCがカメラのSSID（LUMIX-XXXX）に接続しているか")
    print("  □ IPアドレス・ポートが正しいか")
    print(f"    現在の設定: IP={CAMERA_IP}, PORT={CAMERA_PORT}")
    sys.exit(1)

print(f"  OK: カメラから応答を受信しました")
print(f"  レスポンス: {data}")
print()

# ============================================================
#  Step 2: カメラ情報の取得
# ============================================================
print("=== Step 2: カメラ情報 ===")
info = cam_get("mode=getinfo&type=allmenu")
if info is not None and result_str(info) == "ok":
    # allmenu は機種情報を含む場合がある（機種依存）
    print(f"  カメラ情報取得: OK")
else:
    # 一部機種では対応していないコマンドもある
    print(f"  カメラ情報取得: スキップ（機種依存）")

# 機種名・ファームウェアバージョンを取得
info2 = cam_get("mode=getinfo&type=curmenu")
if info2 is not None:
    print(f"  curmenu 取得:   OK  → result={result_str(info2)}")
else:
    print(f"  curmenu 取得:   NG")
print()

# ============================================================
#  Step 3: 撮影モードの取得
# ============================================================
print("=== Step 3: 撮影モード取得 ===")
state = cam_get("mode=getstate")
if state is not None and result_str(state) == "ok":
    cam_mode = state.get("cammode", "不明")
    print(f"  現在のカメラモード: {cam_mode}")
    if cam_mode == "recmode":
        print("  → 撮影モード（リモート撮影に使用可能）")
    elif cam_mode == "playmode":
        print("  → 再生モード（撮影するには撮影モードへの切り替えが必要）")
    else:
        print(f"  → モード '{cam_mode}'")
else:
    print("  NG: モード取得失敗")
print()

# ============================================================
#  Step 4: サマリー
# ============================================================
print("=== Step 4: 確認結果サマリー ===")

checks = {
    "疎通（getstate）":   data is not None and result_str(data) == "ok",
    "情報取得（curmenu）": info2 is not None,
    "モード取得":         state is not None and result_str(state) == "ok",
}

all_ok = True
for label, ok in checks.items():
    mark = "✓" if ok else "✗"
    print(f"  {mark} {label}")
    if not ok:
        all_ok = False

print()
if all_ok:
    print("  ✓ 接続確認 OK — HTTP API でカメラを制御できます。")
    print()
    print("  次のステップ:")
    print("    lumix_capture.py を実行してシャッターテストを行ってください。")
else:
    print("  ✗ 一部の確認に失敗しました。上記のエラー内容を確認してください。")

print()
print("=== 完了 ===")
