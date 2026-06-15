# lumix_check_connection.py
# Panasonic Lumix DC-G100D 接続確認スクリプト v3
#
# v3変更点:
#   - LUMIX Syncアプリが最初に送る接続開始コマンドを試す
#   - ポート・パス候補を広げる
#   - HTTPヘッダも表示してサーバー情報を収集

import urllib.error
import urllib.request

CAMERA_IP = "192.168.54.1"
TIMEOUT_SEC = 5


def try_get(url: str) -> tuple[int | None, dict, str]:
    """GETリクエストを送り (ステータス, ヘッダ, ボディ) を返す"""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            headers = dict(resp.headers)
            return resp.status, headers, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), str(e.reason)
    except urllib.error.URLError as e:
        return None, {}, str(e.reason)
    except Exception as e:
        return None, {}, str(e)


print("=" * 56)
print("  Lumix DC-G100D 接続確認スクリプト v3")
print("=" * 56)
print()

# ============================================================
#  Step 1: ポートスキャン（よく使われるポートを確認）
# ============================================================
print("=== Step 1: ポートスキャン ===")
ports = [60606, 80, 8080, 8888, 443]
open_ports = []
for port in ports:
    status, headers, body = try_get(f"http://{CAMERA_IP}:{port}/")
    if status is not None:
        print(f"  ポート {port:5d}: HTTP {status}  body={body[:40]!r}")
        open_ports.append(port)
    else:
        print(f"  ポート {port:5d}: 接続不可 ({body[:40]})")
print()

# ============================================================
#  Step 2: 60606で接続開始コマンドを試す
#  LUMIX Syncは最初に acc=1 を付けて接続セッションを開く
# ============================================================
print("=== Step 2: 接続開始コマンドを試す（ポート60606） ===")
base = f"http://{CAMERA_IP}:60606"

# LUMIX Syncが送る接続開始コマンドの候補
start_commands = [
    "/cam.cgi?mode=accctrl&type=req_acc&value=0&value2=Windy",
    "/cam.cgi?mode=getinfo&type=allmenu",
    "/cam.cgi?mode=getinfo&type=curmenu",
    "/cam.cgi?mode=getstate",
    "/cam.cgi",  # クエリなし
]

for cmd in start_commands:
    url = f"{base}{cmd}"
    status, headers, body = try_get(url)
    server_info = headers.get("Server", headers.get("server", ""))
    if status is not None:
        print(f"  {cmd[:55]}")
        print(f"    → HTTP {status}  Server={server_info!r}  body={body[:60]!r}")
    else:
        print(f"  {cmd[:55]}")
        print(f"    → 接続失敗: {body[:60]}")
    print()

# ============================================================
#  Step 3: サーバーヘッダの詳細表示
# ============================================================
print("=== Step 3: サーバー情報 ===")
status, headers, body = try_get(f"{base}/cam.cgi?mode=getstate")
if headers:
    print("  レスポンスヘッダ:")
    for k, v in headers.items():
        print(f"    {k}: {v}")
else:
    print("  ヘッダ取得不可")

print()
print("=== 完了 ===")
print("この出力結果をそのまま共有してください。")
