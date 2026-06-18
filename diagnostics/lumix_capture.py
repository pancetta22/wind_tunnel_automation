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
import socket
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urljoin, urlparse

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
# ※ まず SSDP で実際の記述URLを探し、見つからない時だけこの候補を使う。
DDD_CANDIDATES = [
    "/Server0/ddd/ddd.xml", "/Server0/ddd.xml", "/ddd.xml",
    "/Server0/description.xml", "/description.xml", "/DMS/ddd.xml",
]
# ContentDirectory 制御URLの既定（記述ファイルから取得できなかった場合に使用）
CDS_CONTROL_DEFAULT = "/Server0/CDS_control"

# SSDP（UPnP デバイス発見）でデバイス記述URL(LOCATION)を探すための設定
SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_TARGETS = [
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:service:ContentDirectory:1",
    "ssdp:all",
]

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


def _soap_post_raw(url: str, body: str, soap_action: str,
                   timeout: float = 15.0, http_version: str = "1.1") -> tuple[int | None, str]:
    """SOAP POST を生ソケットで送り、Connection: close で接続終了まで読み切る。
    urllib(HTTP/1.1) は keep-alive 待ちでハングし、HTTP/1.0 だと DLNA1.5 サーバが
    接続を即閉じることがあるため、HTTP/1.1 + Connection: close を既定にする。
    (status, body文字列) を返す。"""
    p = urlparse(url)
    host, port = p.hostname, (p.port or 80)
    path = p.path or "/"
    if p.query:
        path += "?" + p.query
    data = body.encode("utf-8")
    req = (
        f"POST {path} HTTP/{http_version}\r\n"
        f"HOST: {host}:{port}\r\n"
        'CONTENT-TYPE: text/xml; charset="utf-8"\r\n'
        f"CONTENT-LENGTH: {len(data)}\r\n"
        f'SOAPACTION: "{soap_action}"\r\n'
        "USER-AGENT: LUMIX Sync\r\n"
        "CONNECTION: close\r\n\r\n"
    ).encode("ascii") + data

    s = None
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        s.sendall(req)
        chunks = []
        while True:
            try:
                buf = s.recv(65536)
            except socket.timeout:
                break
            if not buf:
                break
            chunks.append(buf)
        raw = b"".join(chunks)
    except OSError as e:
        return None, str(e)
    finally:
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    head, _, body_bytes = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
    parts = status_line.split()
    status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    return status, body_bytes.decode("utf-8", errors="replace")


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


def _set_mode(value: str, retries: int = 4, settle: float = 1.0) -> str | None:
    """camcmd のモード切替（recmode/playmode）をリトライ付きで行う。
    err_busy / err_critical はカメラがモード遷移中の一時的失敗のことが多いので、
    間隔を空けて数回試す。最後に得た result を返す（成功時は "ok"）。"""
    result = None
    for attempt in range(retries):
        result = cam_cmd(f"mode=camcmd&value={value}")
        if result == "ok":
            time.sleep(settle)            # 切替後はカメラが安定するまで待つ
            return "ok"
        time.sleep(0.8 * (attempt + 1))   # err_busy/err_critical/None → 待って再試行
    return result


def set_recmode() -> bool:
    result = _set_mode("recmode")
    if result != "ok":
        print(f"エラー: recmode 移行失敗 (result={result})", file=sys.stderr)
        return False
    return True


def set_playmode() -> bool:
    result = _set_mode("playmode")
    if result != "ok":
        print(f"警告: playmode 移行失敗 (result={result})", file=sys.stderr)
        return False
    return True


_af_warned = False   # AF 非対応の警告は1プロセスにつき1回だけ出す


def capture() -> bool:
    """シャッターを切る（AF → 撮影 → 解放）。
    AF コマンド(af_s_push)はフォーカスモードや機種によって err_param を返すが、
    撮影自体は capture で行えるため致命的ではない（警告は1回だけに抑える）。"""
    global _af_warned
    result = cam_cmd("mode=camcmd&value=af_s_push")
    if result not in ("ok", None) and not _af_warned:
        print(f"情報: AF コマンドが効きませんでした (result={result})。"
              "撮影は継続します（MF/フォーカス済みなら問題ありません）。", file=sys.stderr)
        _af_warned = True

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
def _local_ip_for(remote_ip: str) -> str | None:
    """remote_ip に到達するために使うローカルIPを返す（PCに複数NICがある場合の判別用）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((remote_ip, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def ssdp_discover(timeout: float = 2.0, stop_early: bool = True) -> list:
    """SSDP M-SEARCH を投げ、応答の LOCATION（デバイス記述URL）を集めて返す。
    カメラのAPに接続している前提。送出インターフェースはカメラ側に固定する。
    stop_early=True なら最初に LOCATION が取れた時点で打ち切る（実運用の高速化）。
    --diag では stop_early=False で全 ST を試す。"""
    locations, seen = [], set()
    local_ip = _local_ip_for(CAMERA_IP)
    for st in SSDP_TARGETS:
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            f"ST: {st}\r\n\r\n"
        ).encode("ascii")
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if local_ip:
                # 送出を確実にカメラ側NICへ（有線LAN等が同居していても届くように）
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(local_ip))
            s.settimeout(timeout)
            s.sendto(msg, (SSDP_ADDR, SSDP_PORT))
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data, _ = s.recvfrom(65507)
                except socket.timeout:
                    break
                for line in data.decode("utf-8", "replace").splitlines():
                    if line.lower().startswith("location:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc and loc not in seen:
                            seen.add(loc)
                            locations.append(loc)
        except OSError:
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        if stop_early and locations:
            break
    return locations


def _control_from_description(desc_url: str) -> str | None:
    """デバイス記述XML(desc_url)を取得し、ContentDirectory の controlURL を
    絶対URLにして返す（見つからなければ None）。"""
    status, body = _http_get(desc_url)
    if status != 200 or not body or isinstance(body, str):
        return None
    try:
        root = ET.fromstring(body.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None

    # 相対 controlURL の解決基準: URLBase があればそれ、
    # 無ければ記述ドキュメントのURL自身（RFC3986）。urljoin が
    # 絶対パス(/...)も相対パス(...)も正しく解決する。
    base = None
    for el in root.iter():
        if el.tag.endswith("URLBase") and el.text and el.text.strip():
            base = el.text.strip()
            break
    if not base:
        base = desc_url

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
            return urljoin(base, ctrl)
    return None


def discover_cds_control() -> str:
    """ContentDirectory の制御URL（絶対URL）を得る。
      1) SSDP でデバイス記述URLを発見し、そこから制御URLを取得
      2) 既知の候補パス(DDD_CANDIDATES)を直接たたく
      3) どれもダメなら既定値
    """
    for loc in ssdp_discover():
        ctrl = _control_from_description(loc)
        if ctrl:
            return ctrl
    for path in DDD_CANDIDATES:
        ctrl = _control_from_description(f"{DLNA_BASE}{path}")
        if ctrl:
            return ctrl
    return f"{DLNA_BASE}{CDS_CONTROL_DEFAULT}"


def _browse_soap(object_id: str, start: int, count: int) -> str:
    """ContentDirectory#Browse の SOAP リクエスト本文を組み立てる。"""
    return (
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


def _browse(control_url: str, object_id: str,
            start: int, count: int) -> tuple[list, list, int]:
    """ContentDirectory を Browse して (items, containers, total_matches) を返す。
    items/containers は dict のリスト。"""
    soap = _browse_soap(object_id, start, count)
    # 組込みUPnPサーバ対策で生ソケット送信（HTTP/1.1 + Connection: close）。
    # 初回Browseはカメラがインデックス生成で遅いことがあるので timeout は長め。
    status, resp = _soap_post_raw(control_url, soap, f"{CDS_TYPE}#Browse", timeout=15.0)
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


def _browse_children(control_url: str, object_id: str, page: int = 50) -> tuple[list, list]:
    """object_id の直下を（ページングしながら）すべて Browse し、
    (items, containers) を返す。
    ※ RequestedCount=0 は機種により 0 件を返すため使わず、ページごとに取得する。"""
    items, containers = [], []
    start = 0
    for _ in range(200):      # 安全弁: StartingIndex を無視する機種での無限ループ防止
        got_items, got_conts, total = _browse(control_url, object_id, start, page)
        items.extend(got_items)
        containers.extend(got_conts)
        got = len(got_items) + len(got_conts)
        if got == 0:
            break
        start += got
        if total and start >= total:
            break
        if got < page:        # total 不明でも1ページに収まったら終わり
            break
    return items, containers


def _collect_all_items(control_url: str, root_id: str = "0", max_depth: int = 5) -> list:
    """root_id 以下を幅優先でたどって全 item を DLNA の並び順で集める。
    Panasonic は「0 → 日付などのコンテナ → item」と階層化されることが多いので再帰する。"""
    collected = []
    queue = [(root_id, 0)]
    visited = set()
    while queue:
        oid, depth = queue.pop(0)
        if oid in visited or depth > max_depth:
            continue
        visited.add(oid)
        items, containers = _browse_children(control_url, oid)
        collected.extend(items)
        for c in containers:
            queue.append((c["id"], depth + 1))
    return collected


def _newest_items(control_url: str, count: int) -> list:
    """撮影直後に呼ぶ。全 item を集めて末尾（最新）count 件を返す。
    DLNA は通常フォルダ内を古い→新しい順で返すため、全体の末尾が直近の撮影に対応する
    （撮影順に <name>1..<name>N と対応させる）。"""
    items = _collect_all_items(control_url)
    if not items:
        return []
    return items[-count:]


def download_newest(out_dir: str, base_name: str, count: int) -> int:
    """撮影直後に呼ぶ。新しい count 枚を out_dir/<base_name>{1..N}.JPG に保存。
    保存できた枚数を返す。"""
    os.makedirs(out_dir, exist_ok=True)
    control_url = discover_cds_control()
    print(f"[DLNA] ContentDirectory: {control_url}")

    # 撮影直後はインデックス更新に時間がかかることがあるのでリトライ
    items = []
    for _ in range(4):
        items = _newest_items(control_url, count)
        if len(items) >= count:
            break
        time.sleep(1.5)
    if not items:
        print("エラー: カメラ内に画像が見つかりません（DLNA構成が想定と異なる可能性）。",
              file=sys.stderr)
        print("  → カメラ単体で `python lumix_capture.py --diag` を実行し、"
              "DLNA構成（木構造）を確認してください。", file=sys.stderr)
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
    saved = 0
    try:
        for i in range(count):
            if not capture():
                return False
            if i < count - 1:
                time.sleep(SHOT_INTERVAL_SEC)

        # 再生モードに切り替えて画像を取り出す（DLNA が立ち上がるまで余裕を持って待つ）
        if not set_playmode():
            print("警告: 再生モードへ移行できず、画像を取り出せません。", file=sys.stderr)
            return False
        time.sleep(3.0)   # 再生モードのDLNA(DMS)が立ち上がるまで待つ
        saved = download_newest(out_dir, base_name, count)
    finally:
        # 撮影モードへ必ず戻す。戻し忘れるとカメラが再生モードのまま残り、
        # 次の迎角の接続(recmode)が err_busy になって連鎖的に失敗するため。
        set_recmode()

    if saved < count:
        print(f"撮影/保存が不完全です（{saved}/{count} 枚）", file=sys.stderr)
        return False
    print(f"撮影完了: {saved}/{count} 枚保存しました")
    return True


# ============================================================
#  DLNA 構成診断（--diag）
# ============================================================
def dlna_diag() -> int:
    """DLNA 構成を診断して表示する（画像を保存できない時の原因切り分け用）。
    ddd.xml の探索結果・制御URL・Browse の生ステータス・木構造を出力する。
    カメラに写真がある状態で実行し、出力を共有してもらえれば構成を特定できる。"""
    print("=== DLNA 構成診断 ===")

    print("[0] SSDP でデバイス記述(LOCATION)を探索:")
    locs = ssdp_discover(timeout=3.0, stop_early=False)   # 診断では全STを試す
    if locs:
        for loc in locs:
            ctrl = _control_from_description(loc)
            print(f"    LOCATION: {loc}")
            print(f"      → ContentDirectory 制御URL: {ctrl or '(見つからず)'}")
        # 先頭LOCATIONの記述XMLを生で出す（controlURL/eventURL/URLBase 確認用）
        st_d, body_d = _http_get(locs[0])
        text_d = (body_d.decode("utf-8", "replace")
                  if isinstance(body_d, (bytes, bytearray)) else str(body_d))
        print(f"    --- デバイス記述(先頭1200字) status={st_d} ---")
        for line in text_d[:1200].splitlines():
            print(f"    {line}")
    else:
        print("    （SSDP 応答なし。カメラのWi-Fiに繋がっているか、"
              "PCの別NIC/ファイアウォールを確認）")

    print("[1] デバイス記述ファイル(ddd.xml)候補の直接探索:")
    for path in DDD_CANDIDATES:
        status, body = _http_get(f"{DLNA_BASE}{path}")
        n = len(body) if isinstance(body, (bytes, bytearray)) else 0
        print(f"    {DLNA_BASE}{path}  -> status={status}, {n} bytes")

    control_url = discover_cds_control()
    print(f"[2] 採用する ContentDirectory 制御URL: {control_url}")

    # SCPD（対応アクション定義）を確認 — Browse アクションが宣言されているか
    scpd_url = control_url.replace("CDS_control", "CDS_SCPD")
    s_st, s_body = _http_get(scpd_url)
    s_text = (s_body.decode("utf-8", "replace")
              if isinstance(s_body, (bytes, bytearray)) else str(s_body))
    print(f"[2.5] SCPD {scpd_url}  status={s_st}, "
          f"Browseアクション={'有' if '<name>Browse</name>' in s_text else '無'}")

    print("[3] 再生モードへ移行して Browse します...")
    pm = set_playmode()
    time.sleep(3.0)   # 再生モードのDLNA(DMS)が立ち上がるまで待つ
    _st, _b = cam_get_raw("mode=getstate")
    try:
        _mode = ET.fromstring(_b).findtext(".//cammode", default="不明")
    except ET.ParseError:
        _mode = "解析失敗"
    print(f"    playmode設定: {'OK' if pm else 'NG'} / 現在のcammode: {_mode}")

    # Browse POST を複数方式で試し、どれが 200 を返すか確認する（timeout長め）。
    soap = _browse_soap("0", 0, 50)
    BT = 15.0
    trials = [
        ("urllib HTTP/1.1     ",
         lambda: _http_post(control_url, soap, f"{CDS_TYPE}#Browse", timeout=BT)),
        ("raw    HTTP/1.1 close",
         lambda: _soap_post_raw(control_url, soap, f"{CDS_TYPE}#Browse",
                                timeout=BT, http_version="1.1")),
        ("raw    HTTP/1.0 close",
         lambda: _soap_post_raw(control_url, soap, f"{CDS_TYPE}#Browse",
                                timeout=BT, http_version="1.0")),
    ]
    print(f"[4] Browse(ObjectID='0') を複数方式で試行（各 timeout={BT:.0f}s）:")
    for label, fn in trials:
        st, resp = fn()
        head = resp[:200].replace("\n", " ").replace("\r", " ")
        print(f"    [{label}] -> status={st}, {len(resp)} chars  先頭: {head!r}")

    print("[5] ContentDirectory の木構造（raw HTTP/1.0 で取得）:")

    def walk(oid: str, depth: int) -> int:
        if depth > 4:
            return 0
        items, containers = _browse_children(control_url, oid)
        pad = "    " + "  " * depth
        n_total = len(items)
        for c in containers:
            print(f"{pad}[container] id={c['id']} childCount={c['child_count']}")
            n_total += walk(c["id"], depth + 1)
        for it in items[:5]:
            url = _pick_original(it) or "(URLなし)"
            print(f"{pad}[item] id={it['id']}  {url}")
        if len(items) > 5:
            print(f"{pad}... 他 {len(items) - 5} 件の item")
        return n_total

    total_items = walk("0", 0)
    print(f"[6] 見つかった item 合計: {total_items} 件")
    if total_items == 0:
        print("    → カメラに画像が無いか、Browse 構成が想定と異なります。")
        print("       カメラのSDに写真があるか確認し、[1]〜[5] の出力を共有してください。")
    set_recmode()
    return 0 if total_items > 0 else 1


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
    parser.add_argument("--diag", action="store_true",
                        help="DLNA構成を診断して表示する（画像を保存できない時の原因切り分け）")
    args = parser.parse_args()

    print("接続中...")
    if not connect():
        return 1
    print("接続OK")

    # --diag: DLNA構成の診断（撮影はしない）
    if args.diag:
        return dlna_diag()

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
