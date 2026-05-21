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

# コマンドライン引数でポート番号を受け取る
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=5, help="COMポート番号")
args = parser.parse_args()

PORT = args.port

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

# 1秒間データを収集して平均
N = 200
sums = [0.0] * 6
ok_count = 0
Data = (ctypes.c_double * 6)()
Status = ctypes.c_char(b"\x00")

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