# lumix_test.py
# Panasonic Lumix DC-G100D カメラ総合テスト（接続 → DLNA → 撮影＋保存）
#
# run_experiment に組み込まれている写真撮影が正しく動くかを、実験を回さずに
# 1コマンドで確認するためのテストスクリプト。各ステージの PASS/FAIL を表示する。
#
# 使い方（コマンドプロンプトで・MATLABではない）:
#   cd <リポジトリのフォルダ>
#   python diagnostics\lumix_test.py                 # 接続→DLNA→2枚撮影＋保存
#   python diagnostics\lumix_test.py --count 3       # 撮影枚数を変える
#   python diagnostics\lumix_test.py --out test_out  # 保存先を指定
#   python diagnostics\lumix_test.py --skip-capture  # 撮影せず接続/DLNAのみ確認
#
#   ※ python が見つからない場合は config.json の python_exe のフルパスで実行:
#      "C:\...\Python312-32\python.exe" diagnostics\lumix_test.py
#
# 事前条件:
#   - PC がカメラの Wi-Fi（SSID: G100D-xxxxxx）に接続済み
#   - 実行中、カメラ画面に出る「接続を許可しますか？」で「はい」を選べること
#   - DLNA/撮影テストのため、カメラに写真が数枚あると確実（無くても撮影テストで作る）
#
# このスクリプトは diagnostics/lumix_capture.py の関数をそのまま呼ぶ。
# つまり本番(run_experiment)で使う処理経路を実際にテストする（コピーではない）。

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET

# このスクリプトと同じ diagnostics/ にある lumix_capture を読み込む。
# （python diagnostics\lumix_test.py で実行すると sys.path[0] が diagnostics になる
#   が、念のため明示的に追加しておく）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lumix_capture as cam   # noqa: E402

# 端末/cp932 でも文字化けで落ちないようにする安全網。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def _hr(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ============================================================
#  ステージ1: 接続テスト
# ============================================================
def stage_connect() -> bool:
    _hr("ステージ1: 接続テスト（accctrl → recmode → 状態取得）")
    print("PC がカメラの Wi-Fi(SSID: G100D-xxxxxx)に接続されていること、")
    print("カメラ画面の許可確認で「はい」を選べることを確認してください。")
    print()

    if not cam.connect():
        print("結果: NG  接続できませんでした（上のメッセージ参照）。")
        return False
    print("接続OK（accctrl → recmode 成功）")

    # カメラ状態を表示（モード・バッテリ・録画状態）
    _status, body = cam.cam_get_raw("mode=getstate")
    if cam.xml_result(body) == "ok":
        try:
            root = ET.fromstring(body)
            print(f"  カメラモード : {root.findtext('.//cammode', default='不明')}")
            print(f"  バッテリー   : {root.findtext('.//batt', default='不明')}")
            print(f"  録画状態     : {root.findtext('.//rec', default='不明')}")
        except ET.ParseError:
            print("  （状態XMLの解析に失敗。接続自体はOK）")
    else:
        print("  （getstate が ok を返しませんでした。接続自体はOK）")

    print("結果: OK  接続テスト PASS")
    return True


# ============================================================
#  ステージ2: DLNA構成テスト（撮影しない）
# ============================================================
def stage_dlna() -> bool:
    _hr("ステージ2: DLNA構成テスト（画像が見えるか）")
    # dlna_diag は ddd.xml探索・制御URL・Browse生応答・木構造を表示し、
    # item が1件以上見つかれば 0、見つからなければ 1 を返す。
    ok = (cam.dlna_diag() == 0)
    if ok:
        print("結果: OK  DLNA PASS（カメラ内の画像を Browse できています）")
    else:
        print("結果: 注意  画像が見つかりません。"
              "カメラのSDに写真があるか確認してください（この後の撮影テストで再確認します）。")
    return ok


# ============================================================
#  ステージ3: 撮影＋保存テスト
# ============================================================
def stage_capture(out_dir: str, count: int) -> bool:
    _hr(f"ステージ3: 撮影＋保存テスト（{count}枚）")
    print(f"保存先: {out_dir}")
    print("カメラのレンズに何か写る状態にしてから実行してください。")
    print()

    # capture_series は recmode 前提（ステージ1/2の後は recmode になっている）。
    # 内部で 撮影 → playmode → DLNAダウンロード → recmode を行う。
    ok = cam.capture_series(out_dir, "test", count)

    if ok and os.path.isdir(out_dir):
        saved = sorted(f for f in os.listdir(out_dir)
                       if f.lower().startswith("test") and f.lower().endswith(".jpg"))
        print(f"保存ファイル: {saved}")
        print("結果: OK  撮影＋保存 PASS")
    else:
        print("結果: NG  撮影＋保存 FAIL（上のメッセージ参照）")
    return ok


# ============================================================
#  サマリー
# ============================================================
def _summary(results: dict) -> int:
    _hr("テスト結果サマリー")
    all_ok = True
    for name, ok in results.items():
        print(f"  [{'OK ' if ok else 'NG '}] {name}")
        all_ok = all_ok and ok
    print()
    if all_ok:
        print("総合: OK  すべて PASS。run_experiment の写真撮影が使えます。")
    else:
        print("総合: NG  失敗があります。上のログを確認してください。")
        print("  ・DLNAで画像0なら : カメラのSDに写真があるか確認し、ステージ2の [1]〜[5] を共有")
        print("  ・撮影が0枚なら   : カメラが実際にJPEGを記録できるか（MF/フォーカス）を確認")
        print("  ・recmode/err_busy: カメラの電源を入れ直してから再実行")
    return 0 if all_ok else 1


# ============================================================
#  メイン
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Lumix DC-G100D カメラ総合テスト（接続→DLNA→撮影＋保存）")
    ap.add_argument("--count", type=int, default=2,
                    help="撮影テストの枚数（既定2）")
    ap.add_argument("--out", default=None,
                    help="撮影テストの保存先フォルダ（既定: diagnostics/lumix_test_out）")
    ap.add_argument("--skip-capture", action="store_true",
                    help="撮影せず接続/DLNAのみ確認する")
    args = ap.parse_args()

    out_dir = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "lumix_test_out")

    print("=" * 60)
    print("  Lumix DC-G100D カメラ総合テスト")
    print("=" * 60)

    results: dict = {}

    # ステージ1（接続）— ここで失敗したら以降は無意味なので終了
    results["接続"] = stage_connect()
    if not results["接続"]:
        return _summary(results)

    try:
        # ステージ2（DLNA構成）
        results["DLNA"] = stage_dlna()

        # ステージ3（撮影＋保存）
        if args.skip_capture:
            print("\n（--skip-capture 指定のため撮影テストはスキップしました）")
        else:
            results["撮影+保存"] = stage_capture(out_dir, max(1, args.count))
    finally:
        # 途中で例外が出てもカメラを再生モードに残さない（次の操作の err_busy を防ぐ）。
        cam.set_recmode()

    return _summary(results)


if __name__ == "__main__":
    sys.exit(main())
