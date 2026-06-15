# lumix_capture.py
# Panasonic Lumix DC-G100D シャッター制御 + 画像ダウンロード
#
# 使い方:
#   # 接続確認のみ（カメラ画面で「はい」を選ぶ必要あり）
#   python lumix_capture.py --check
#
#   # 1枚だけ撮影してカメラのSDに保存（従来動作・ダウンロードなし）
#   python lumix_capture.py
#
#   # count 枚撮影し、PCの out フォルダに <name>1.JPG .. <name>N.JPG として保存
#   python lumix_capture.py --out "C:/Users/.../260615_rigid/photo" --name p1deg --count 3
#
# 事前条件:
#   - PCがカメラのSSID（G100D-XXXXXX）に接続済みであること
#   - lumix_check_connection.py で接続確認済みであること
#
# 終了コード:
#   0: 成功（--check は接続OK、撮影系は全画像の保存に成功）
#   1: 失敗（接続失敗 / 撮影失敗 / 1枚でも保存に失敗）
#
# 画像取得の仕組み:
#   cam.cgi(ポート80) でシャッターを切ると画像はカメラのSDに保存される。
#   その画像を取り出すため、再生モードに切り替えてカメラ内蔵の DLNA
#   (UPnP ContentDirectory, ポート60606) を Browse し、新しい順に count 枚の
#   オリジナルJPEGのURLを得てダウンロードする。取得後は撮影モードに戻す。
#   ※ DLNAの構成は機種・ファームで差があるため、うまく取得できない場合は
#     本ファイル冒頭の定数（ポート・記述ファイルのパス）を調整すること。

from __future__ import annotations   # 型注釈を遅延評価にして古い Python でも動くように

import argparse
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする安全網。
# 日本語Windows(cp932)など、出力先が表現できない文字を含む print があっても
# UnicodeEncodeError で中断せず、その文字を置換して続行する。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

# ============================================================
#  定数（機種・ネットワークに依存。必要なら調整する）
# ============================================================
CAMERA_IP = "192.168.54.1"
CAM_PORT = 80                      # cam.cgi（制御）
DLNA_PORT = 60606                  # UPnP デバイス記述 / ContentDirectory 制御
TIMEOUT_SEC = 5
DOWNLOAD_TIMEOUT_SEC = 30          # 画像本体の取得は大きいので長め

BASE_URL = f"http://{CAMERA_IP}:{CAM_PORT}"
DLNA_BASE = f"http://{CAMERA_IP}:{DLNA_PORT}"

ACC_POLL_INTERVAL = 1.0
ACC_POLL_TIMEOUT = 30.0

# UPnP デバイス記述ファイルの候補（機種により異なる）。先頭から順に試す。
DDD_CANDIDATES = ["/Server0/ddd/ddd.xml", "/Server0/ddd.xml", "/ddd.xml"]
# ContentDirectory 制御URLの既定（記述ファイルから取得できなかった場合に使用）
CDS_CONTROL_DEFAULT = "/Server0/CDS_control"

CDS_TYPE = "urn:schemas-upnp-org:service:ContentDirectory:1"
SHOT_INTERVAL_SEC = 0.8            # 連写の間隔（別ファイルとして確実に記録させる）


# ============================================================
#  低レベル HTTP
# ============================================================
def _http_get(url: str, timeout: float = TIMEOUT_SEC) -> tuple[int | None, bytes]:
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "LUMIX Sync")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        return None, str(e).encode("utf-8", "replace")


def _http_post(url: str, body: str, soap_action: str,
               timeout: float = TIMEOUT_SEC) -> tuple[int | None, str]:
    data = body.encode("utf-8")
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{soap_action}"',
        "User-Agent": "LUMIX Sync",
    }
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return None, str(e)


# ============================================================
#  cam.cgi（制御コマンド）
# ============================================================
def cam_get_raw(query: str) -> tuple[int | None, str]:
    status, body = _http_get(f"{BASE_URL}/cam.cgi?{query}")
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    return status, text


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
        status, body = cam_get_raw("mode=accctrl&type=req_acc&value=0&value2=Windy")
        acc = body.strip().split(",")[0] if status == 200 else None
        if acc == "ok":
            break
        elif acc in ("ok_under_research", "ok_under_research_no_msg"):
            time.sleep(ACC_POLL_INTERVAL)
        else:
            print(f"エラー: accctrl 失敗 ({body.strip()!r})", file=sys.stderr)
            return False
    else:
        print("エラー: 接続タイムアウト（カメラ画面で接続を許可してください）", file=sys.stderr)
        return False

    if not set_recmode():
        return False
    return True


def set_recmode() -> bool:
    result = cam_cmd("mode=camcmd&value=recmode")
    if result != "ok":
        print(f"エラー: recmode 移行失敗 (result={result})", file=sys.stderr)
        return False
    return True


def set_playmode() -> bool:
    result = cam_cmd("mode=camcmd&value=playmode")
    if result != "ok":
        print(f"警告: playmode 移行失敗 (result={result})", file=sys.stderr)
        return False
    return True


def capture() -> bool:
    """シャッターを切る（AF → 撮影 → 解放）"""
    result = cam_cmd("mode=camcmd&value=af_s_push")
    if result not in ("ok", None):  # 機種によりAFなしでもok
        print(f"警告: AF失敗 (result={result})", file=sys.stderr)

    time.sleep(0.5)  # AFのための待機

    result = cam_cmd("mode=camcmd&value=capture")
    if result != "ok":
        print(f"エラー: シャッター失敗 (result={result})", file=sys.stderr)
        return False

    time.sleep(0.3)
    cam_cmd("mode=camcmd&value=capture_cancel")
    return True


# ============================================================
#  DLNA（撮影画像の取り出し）
# ============================================================
def discover_cds_control() -> str:
    """UPnP デバイス記述から ContentDirectory の制御URL（絶対URL）を得る。
    取得できなければ既定値を返す。"""
    for path in DDD_CANDIDATES:
        status, body = _http_get(f"{DLNA_BASE}{path}")
        if status != 200 or not body:
            continue
        try:
            text = body.decode("utf-8", errors="replace")
            # 名前空間を無視して service を走査する
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for svc in root.iter():
            if not svc.tag.endswith("service"):
                continue
            stype = ctrl = None
            for child in svc:
                if child.tag.endswith("serviceType"):
                    stype = (child.text or "").strip()
                elif child.tag.endswith("controlURL"):
                    ctrl = (child.text or "").strip()
            if stype and "ContentDirectory" in stype and ctrl:
                if ctrl.startswith("http"):
                    return ctrl
                if not ctrl.startswith("/"):
                    ctrl = "/" + ctrl
                return f"{DLNA_BASE}{ctrl}"
    return f"{DLNA_BASE}{CDS_CONTROL_DEFAULT}"


def _browse(control_url: str, object_id: str,
            start: int, count: int) -> tuple[list, list, int]:
    """ContentDirectory を Browse して (items, containers, total_matches) を返す。
    items/containers は dict のリスト。"""
    soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        f'<u:Browse xmlns:u="{CDS_TYPE}">'
        f'<ObjectID>{object_id}</ObjectID>'
        '<BrowseFlag>BrowseDirectChildren</BrowseFlag>'
        '<Filter>*</Filter>'
        f'<StartingIndex>{start}</StartingIndex>'
        f'<RequestedCount>{count}</RequestedCount>'
        '<SortCriteria></SortCriteria>'
        '</u:Browse></s:Body></s:Envelope>'
    )
    status, resp = _http_post(control_url, soap, f"{CDS_TYPE}#Browse")
    if status != 200 or not resp:
        return [], [], 0

    # SOAP応答から <Result>（DIDL-Lite がエスケープされて入っている）と
    # <TotalMatches> を取り出す。
    try:
        root = ET.fromstring(resp)
    except ET.ParseError:
        return [], [], 0
    result_text = total = None
    for el in root.iter():
        if el.tag.endswith("Result"):
            result_text = el.text
        elif el.tag.endswith("TotalMatches"):
            total = el.text
    total_matches = int(total) if (total and total.isdigit()) else 0
    if not result_text:
        return [], [], total_matches

    items, containers = _parse_didl(unescape(result_text))
    return items, containers, total_matches


def _parse_didl(didl_xml: str) -> tuple[list, list]:
    """DIDL-Lite を解析して item / container のリストを返す。"""
    items, containers = [], []
    try:
        root = ET.fromstring(didl_xml)
    except ET.ParseError:
        return items, containers
    for node in root:
        tag = node.tag.split("}")[-1]
        obj_id = node.attrib.get("id", "")
        child_count = node.attrib.get("childCount", "0")
        resources = []
        for child in node:
            if child.tag.endswith("res"):
                size = child.attrib.get("size")
                resources.append({
                    "url": (child.text or "").strip(),
                    "size": int(size) if (size and size.isdigit()) else 0,
                    "info": child.attrib.get("protocolInfo", ""),
                })
        rec = {"id": obj_id, "child_count": int(child_count) if child_count.isdigit() else 0,
               "res": resources}
        if tag == "item":
            items.append(rec)
        elif tag == "container":
            containers.append(rec)
    return items, containers


def _pick_original(item: dict) -> str | None:
    """item のリソースからオリジナルJPEG（最大サイズ）のURLを選ぶ。"""
    res = [r for r in item["res"] if r["url"]]
    if not res:
        return None
    # サムネイル(JPEG_TN/JPEG_SM)を避け、サイズ最大を選ぶ
    def score(r):
        info = r["info"].upper()
        penalty = -1 if ("JPEG_TN" in info or "JPEG_SM" in info) else 0
        return (penalty, r["size"])
    res.sort(key=score)
    return res[-1]["url"]


def _newest_items(control_url: str, count: int) -> list:
    """新しい順ではなく「古い→新しい」順で末尾 count 件の item を返す
    （撮影順に <name>1..<name>N と対応させるため）。"""
    # まずルート直下を見る
    items, containers, total = _browse(control_url, "0", 0, 0)
    target_id, total_items = "0", total
    if not items and containers:
        # アイテムが直下に無い → 子数最大のコンテナを写真フォルダとみなす
        target = max(containers, key=lambda c: c["child_count"])
        target_id = target["id"]
        _, _, total_items = _browse(control_url, target_id, 0, 1)

    if total_items <= 0:
        # total が取れない機種向けフォールバック：直下/コンテナを総なめ
        if items:
            return items[-count:]
        return []

    start = max(0, total_items - count)
    tail, _, _ = _browse(control_url, target_id, start, count)
    return tail


def download_newest(out_dir: str, base_name: str, count: int) -> int:
    """撮影直後に呼ぶ。新しい count 枚を out_dir/<base_name>{1..N}.JPG に保存。
    保存できた枚数を返す。"""
    os.makedirs(out_dir, exist_ok=True)
    control_url = discover_cds_control()
    print(f"[DLNA] ContentDirectory: {control_url}")

    # 撮影直後はインデックス更新に時間がかかることがあるので軽くリトライ
    items = []
    for _ in range(3):
        items = _newest_items(control_url, count)
        if len(items) >= count:
            break
        time.sleep(1.0)
    if not items:
        print("エラー: カメラ内に画像が見つかりません（DLNA構成が想定と異なる可能性）",
              file=sys.stderr)
        return 0
    if len(items) < count:
        print(f"警告: 取得できた画像が {len(items)} 枚で、要求 {count} 枚に足りません。",
              file=sys.stderr)

    saved = 0
    for i, item in enumerate(items, start=1):
        url = _pick_original(item)
        if not url:
            print(f"警告: 画像URLを取得できません (id={item['id']})", file=sys.stderr)
            continue
        status, data = _http_get(url, timeout=DOWNLOAD_TIMEOUT_SEC)
        if status != 200 or not data or isinstance(data, str):
            print(f"警告: ダウンロード失敗 (id={item['id']}, status={status})", file=sys.stderr)
            continue
        out_path = os.path.join(out_dir, f"{base_name}{i}.JPG")
        try:
            with open(out_path, "wb") as f:
                f.write(data)
            saved += 1
            print(f"[保存] {out_path}  ({len(data)//1024} KB)")
        except OSError as e:
            print(f"警告: 保存失敗 {out_path}: {e}", file=sys.stderr)
    return saved


def capture_series(out_dir: str, base_name: str, count: int) -> bool:
    """count 枚撮影して out_dir/<base_name>{1..N}.JPG に保存する。"""
    print(f"撮影中... {base_name} を {count} 枚")
    for i in range(count):
        if not capture():
            return False
        if i < count - 1:
            time.sleep(SHOT_INTERVAL_SEC)

    # 再生モードに切り替えて画像を取り出し、撮影モードに戻す
    set_playmode()
    time.sleep(1.0)
    saved = download_newest(out_dir, base_name, count)
    set_recmode()

    if saved < count:
        print(f"撮影/保存が不完全です（{saved}/{count} 枚）", file=sys.stderr)
        return False
    print(f"撮影完了: {saved}/{count} 枚保存しました")
    return True


# ============================================================
#  メイン
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lumix DC-G100D シャッター制御 + 画像ダウンロード")
    parser.add_argument("--check", action="store_true",
                        help="接続確認のみ（撮影しない）")
    parser.add_argument("--out", default=None,
                        help="画像の保存先フォルダ（指定すると連写+ダウンロード）")
    parser.add_argument("--name", default=None,
                        help="保存ファイル名のベース（例 p1deg → p1deg1.JPG..）")
    parser.add_argument("--count", type=int, default=1,
                        help="撮影枚数（--out 指定時、既定1）")
    args = parser.parse_args()

    print("接続中...")
    if not connect():
        return 1
    print("接続OK")

    # --check: 接続確認のみ
    if args.check:
        return 0

    # --out + --name: 連写してダウンロード
    if args.out and args.name:
        ok = capture_series(args.out, args.name, max(1, args.count))
        return 0 if ok else 1

    # 引数なし: 従来動作（1枚撮影、SDに保存のみ）
    print("撮影中...")
    if not capture():
        return 1
    print("撮影完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
