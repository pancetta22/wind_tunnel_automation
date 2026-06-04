# leptrino_server.py
import argparse
import ctypes
import json
import os
import sys
import time

DLL_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(DLL_DIR, "CfsUsb.dll")

try:
    dll = ctypes.WinDLL(DLL_PATH)
except Exception as e:
    print(json.dumps({"error": f"DLL読み込み失敗: {e}"}))
    sys.exit(1)

dll.Initialize.restype = None
dll.Finalize.restype = None
dll.PortOpen.restype = ctypes.c_bool
dll.PortOpen.argtypes = [ctypes.c_int]
dll.PortClose.restype = None
dll.PortClose.argtypes = [ctypes.c_int]
dll.GetSensorLimit.restype = ctypes.c_bool
dll.GetSensorLimit.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_double)]
dll.GetLatestData.restype = ctypes.c_bool
dll.GetLatestData.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_char),
]
dll.SetSerialMode.restype = ctypes.c_bool
dll.SetSerialMode.argtypes = [ctypes.c_int, ctypes.c_bool]
dll.GetSerialData.restype = ctypes.c_bool
dll.GetSerialData.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_double),
    ctypes.POINTER(ctypes.c_char),
]

# ---------- コマンドライン引数 ----------
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=5, help="COMポート番号")
parser.add_argument(
    "--mode",
    choices=["avg", "stream"],
    default="avg",
    help="avg: 1秒平均をJSONで返す（既存動作）  stream: 時系列CSVを書き出す",
)
parser.add_argument("--output", type=str, default="", help="[stream用] 出力CSVパス")
parser.add_argument(
    "--size_limit_kb",
    type=float,
    default=1000.0,
    help="[stream用] この KB を超えたら計測終了（デフォルト: 1000）",
)
args = parser.parse_args()

PORT = args.port

# ---------- DLL 初期化 ----------
dll.Initialize()

if not dll.PortOpen(PORT):
    print(json.dumps({"error": "PortOpen失敗"}))
    dll.Finalize()
    sys.exit(1)

# 定格値取得
Limit = (ctypes.c_double * 6)()
dll.GetSensorLimit(PORT, Limit)
limit_list = list(Limit)

# 連続モード開始
dll.SetSerialMode(PORT, True)
time.sleep(0.2)  # 安定待ち

Data = (ctypes.c_double * 6)()
Status = ctypes.c_char(b"\x00")

# ========================================================
#  avg モード（既存動作）: 1秒間の平均値を JSON で返す
# ========================================================
if args.mode == "avg":
    N = 200
    sums = [0.0] * 6
    ok_count = 0

    for _ in range(N):
        if dll.GetSerialData(PORT, Data, ctypes.byref(Status)):
            for i in range(6):
                sums[i] += Data[i]
            ok_count += 1
        time.sleep(0.005)  # 5ms待ち → 約200Hz

    dll.SetSerialMode(PORT, False)
    dll.PortClose(PORT)
    dll.Finalize()

    if ok_count == 0:
        print(json.dumps({"error": "データ取得失敗"}))
        sys.exit(1)

    labels = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    result = {}
    for i, label in enumerate(labels):
        raw_avg = sums[i] / ok_count
        result[label] = limit_list[i] / 10000.0 * raw_avg

    result["limit"] = limit_list
    result["n"] = ok_count
    print(json.dumps(result))

# ========================================================
#  stream モード: 時系列CSVを書き出し、サイズ上限で終了
#
#  【修正点】SetSerialMode直後はDLL内部バッファに古いデータが
#  蓄積しており、ループ開始直後だけ高速（3000Hz超）で返ってしまう。
#  これをドレイン（flush）してからt=0を決定することで、
#  ファイル先頭から均一な~1200Hzのタイムスタンプが記録される。
#
#  固定タイマ（ビジーウェイト）は使わない。
#  GetSerialDataはセンサの更新を待って返るため、
#  タイトループで呼ぶだけで自然にセンサレート（~1200Hz）に追従する。
#  固定タイマを使うとセンサの実レートとのズレがビート周波数として
#  スペクトルに偽ピークを生む（フラッター計測では致命的）。
# ========================================================
elif args.mode == "stream":
    if not args.output:
        dll.SetSerialMode(PORT, False)
        dll.PortClose(PORT)
        dll.Finalize()
        print(json.dumps({"error": "--output が指定されていません"}))
        sys.exit(1)

    size_limit_bytes = args.size_limit_kb * 1024

    # 定格値を整数表示用に整形（例: 30.0 → "30"、5.0 → "5"）
    def _fmt_limit(v):
        return str(int(v)) if float(v).is_integer() else str(v)

    limit_strs = ",".join(_fmt_limit(v) for v in limit_list)

    # CP932 でヘッダ4行を書き出す
    try:
        f = open(args.output, "w", encoding="cp932", buffering=1)
    except Exception as e:
        dll.SetSerialMode(PORT, False)
        dll.PortClose(PORT)
        dll.Finalize()
        print(json.dumps({"error": f"ファイルオープン失敗: {e}"}))
        sys.exit(1)

    f.write("CFSLGR_DataFile\n")
    f.write("データ保存[個/秒],1200,,Filter,OFF\n")
    f.write(f"定格,{limit_strs}\n")
    f.write("経過時間[sec],Fx[N],Fy[N],Fz[N],Mx[Nm],My[Nm],Mz[Nm]\n")
    f.flush()

    # ----------------------------------------------------------
    #  ① 起動直後のバッファドレイン
    #
    #  SetSerialMode(True) 後、DLL内部バッファには
    #  time.sleep(0.2) の 0.2秒分（≈240サンプル）の古いデータが
    #  蓄積している。これを読み捨ててから計測を開始しないと、
    #  ファイル先頭だけ異常に短い時刻間隔（3000Hz超）が記録される。
    #
    #  DRAIN_SEC = 0.25 s
    #  SetSerialMode(True) 直前の time.sleep(0.2) の間に
    #  センサは既にデータを送り続けており、DLL内部バッファには
    #  約 1200Hz × 0.2s = 240サンプル分が蓄積している。
    #  これを完全に吐き出すには最低0.2秒のドレインが必要。
    #  安全マージン0.05秒を加えて 0.25秒とする。
    # ----------------------------------------------------------
    DRAIN_SEC = 0.25
    t_drain = time.perf_counter()
    while time.perf_counter() - t_drain < DRAIN_SEC:
        dll.GetSerialData(PORT, Data, ctypes.byref(Status))

    # ドレイン完了後、次の「新鮮な」サンプルが来るまで待つ。
    # これにより t=0 がセンサ更新タイミングと一致する。
    while not dll.GetSerialData(PORT, Data, ctypes.byref(Status)):
        pass

    # ----------------------------------------------------------
    #  ② 計測開始（ここが真の t=0）
    # ----------------------------------------------------------
    sample_count = 0
    t_start = time.perf_counter()

    # ファイルサイズチェックは毎サンプルではなく 120サンプルに1回（約0.1秒ごと）
    CHECK_INTERVAL = 120

    # ----------------------------------------------------------
    #  ③ イベント駆動ループ
    #
    #  sleep や固定タイマは一切使わない。
    #  GetSerialData がセンサの更新ごとに True を返すので、
    #  タイトループで呼ぶだけでセンサレートに自然追従する。
    #  False が返った場合（センサ未更新）は即リトライ。
    # ----------------------------------------------------------
    try:
        while True:
            if not dll.GetSerialData(PORT, Data, ctypes.byref(Status)):
                # センサがまだ更新していない → 即リトライ
                continue

            elapsed = time.perf_counter() - t_start

            # 物理量に変換
            phys = [limit_list[i] / 10000.0 * Data[i] for i in range(6)]

            # SPEC準拠フォーマット: 時間=小数4桁, F=小数3桁, M=小数4桁
            line = "{:.4f},{:.3f},{:.3f},{:.3f},{:.4f},{:.4f},{:.4f}\n".format(
                elapsed,
                phys[0],
                phys[1],
                phys[2],
                phys[3],
                phys[4],
                phys[5],
            )
            f.write(line)
            f.flush()
            sample_count += 1

            # ファイルサイズ確認
            if sample_count % CHECK_INTERVAL == 0:
                if os.path.getsize(args.output) >= size_limit_bytes:
                    break

    finally:
        f.close()
        dll.SetSerialMode(PORT, False)
        dll.PortClose(PORT)
        dll.Finalize()

    # 正常終了を JSON で通知（MATLAB 側が status を確認できるよう）
    final_size_kb = os.path.getsize(args.output) / 1024
    result = {
        "status": "ok",
        "samples": sample_count,
        "size_kb": round(final_size_kb, 1),
    }
    print(json.dumps(result))
