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
    choices=["avg", "stream", "limit"],
    default="avg",
    help="avg: 1秒平均をJSONで返す  stream: 時系列CSVを書き出す  limit: 定格値と生データを返す（診断用）",
)
parser.add_argument("--output", type=str, default="", help="[stream用] 出力CSVパス")
parser.add_argument(
    "--size_limit_kb",
    type=float,
    default=1000.0,
    help="[stream用] この KB を超えたら計測終了（--time_limit_sec と排他）",
)
parser.add_argument(
    "--time_limit_sec",
    type=float,
    default=None,
    help="[stream用] この秒数が経過したら計測終了（指定時は --size_limit_kb を無視）",
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
#  limit モード: 定格値(Limit)と生データ(±10000スケール)を返す
# ========================================================
elif args.mode == "limit":
    N = 100
    sums = [0.0] * 6
    ok_count = 0
    for _ in range(N):
        if dll.GetSerialData(PORT, Data, ctypes.byref(Status)):
            for i in range(6):
                sums[i] += Data[i]
            ok_count += 1
        time.sleep(0.005)

    dll.SetSerialMode(PORT, False)
    dll.PortClose(PORT)
    dll.Finalize()

    labels = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    raw_avg = [sums[i] / ok_count if ok_count else float("nan") for i in range(6)]
    phys = [limit_list[i] / 10000.0 * raw_avg[i] for i in range(6)]
    print(
        json.dumps(
            {
                "labels": labels,
                "limit": limit_list,
                "raw_avg": raw_avg,
                "phys": phys,
                "n": ok_count,
            }
        )
    )

# ========================================================
#  stream モード: 時系列CSVを書き出し、条件に応じて終了
#
#  終了条件（排他）:
#    --time_limit_sec N  : N秒経過で終了（フラッター実験用）
#    --size_limit_kb  N  : NKB超過で終了（定常空力実験用・既存動作）
#
#  【バッファドレイン → t=0決定 → イベント駆動ループ】の構造は変更なし。
# ========================================================
elif args.mode == "stream":
    if not args.output:
        dll.SetSerialMode(PORT, False)
        dll.PortClose(PORT)
        dll.Finalize()
        print(json.dumps({"error": "--output が指定されていません"}))
        sys.exit(1)

    # 終了条件を確定する
    use_time_limit = args.time_limit_sec is not None
    if use_time_limit:
        time_limit_sec = args.time_limit_sec
        size_limit_bytes = None  # 使わない
    else:
        time_limit_sec = None  # 使わない
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
    #  ① 起動直後のバッファドレイン（既存と同じ・変更なし）
    # ----------------------------------------------------------
    DRAIN_SEC = 0.25
    t_drain = time.perf_counter()
    while time.perf_counter() - t_drain < DRAIN_SEC:
        dll.GetSerialData(PORT, Data, ctypes.byref(Status))

    # ドレイン完了後、次の新鮮なサンプルが来るまで待つ
    # （1200Hzのポーリングを妨げない極小sleepでCPUスピンを緩和）
    while not dll.GetSerialData(PORT, Data, ctypes.byref(Status)):
        time.sleep(0.0002)

    # ----------------------------------------------------------
    #  ② 計測開始（ここが真の t=0）
    # ----------------------------------------------------------
    sample_count = 0
    t_start = time.perf_counter()

    # サイズチェックは120サンプルに1回（時間制限時はサイズチェック不要）
    CHECK_INTERVAL = 120

    # ----------------------------------------------------------
    #  ③ イベント駆動ループ
    # ----------------------------------------------------------
    try:
        while True:
            if not dll.GetSerialData(PORT, Data, ctypes.byref(Status)):
                # 1200Hz(≈0.83ms間隔)のポーリングを妨げない極小sleepでCPUスピンを緩和
                time.sleep(0.0002)
                continue

            elapsed = time.perf_counter() - t_start

            # 物理量に変換
            phys = [limit_list[i] / 10000.0 * Data[i] for i in range(6)]

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

            # ------ 終了判定 ------
            if use_time_limit:
                # 秒数制限: 毎サンプル elapsed を確認（CHECK_INTERVALは不要）
                if elapsed >= time_limit_sec:
                    break
            else:
                # サイズ制限: 120サンプルに1回チェック（既存動作）
                if sample_count % CHECK_INTERVAL == 0:
                    if os.path.getsize(args.output) >= size_limit_bytes:
                        break

    finally:
        f.close()
        dll.SetSerialMode(PORT, False)
        dll.PortClose(PORT)
        dll.Finalize()

    # 正常終了を JSON で通知
    final_size_kb = os.path.getsize(args.output) / 1024
    result = {
        "status": "ok",
        "samples": sample_count,
        "size_kb": round(final_size_kb, 1),
        "duration_sec": round(elapsed, 2),  # 実際の計測時間（秒数制限時の確認用）
    }
    print(json.dumps(result))
