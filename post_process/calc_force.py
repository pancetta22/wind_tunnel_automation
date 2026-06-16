#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# calc_force.py  空力係数算出スクリプト（Windy 新システム対応版）
#
# 【既存版からの変更点】
#   average(): 従来は data/ フォルダに「生CSV + fc10Hz版」の2ファイル/計測点が
#              あることを前提としていたが、新システムでは生CSVのみ1ファイル/計測点。
#              → case_num の算出と folder_list のインデックスを修正。
#              → _volt_raw.csv が混在していても自動除外。
#   drift()  : windspeed.csv のフォーマットは既存と同一（変更なし）。
#
# 【実行方法】
#   cd <解析フォルダ>          ← windspeed.csv が存在する場所（新構成では post_process/）
#   python calc_force.py
#
# 【必要な入力ファイル】
#   新構成: post_process/ で実行し、data/ と *_experiment_log.json は
#           ../force_measurement から読む。出力はカレント(post_process/)へ。
#   旧構成(フラット): data/ と windspeed.csv が同じフォルダにある（従来どおり）。
#   data/        6軸センサ CSV（全4フェーズ 244ファイル）
#   windspeed.csv  差圧電圧→風速変換済みCSV（make_windspeed.py で生成）
#
# 【生成されるファイル】
#   av_Forces.csv, Pofst/Mofst/Pdata/Mdata_Ncm.csv,
#   F_adcenter_Nm.csv, F_aero_Nm.csv, C_aero_raw.csv, C_aero.csv
#   Cl.png, Cd.png, Cm.png, polar.png, Cl_PM.png, Cd_PM.png, Cm_PM.png

import os
import re
import sys
import json
import glob
import pandas as pd
import numpy as np
import math
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
from tqdm import tqdm
import traceback

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする安全網。
# 日本語Windows(cp932)など、出力先が表現できない文字を含む print があっても
# UnicodeEncodeError で中断せず、その文字を '?' 等に置換して続行する。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def angle_from_name(name, sign):
    """ファイル名（..._Pdata_10.01.csv 等）から参照迎角を取り出す。
    刻み幅が 1° 以外でも、行番号ではなく実際の角度を AoA に使えるようにする。
    sign: 正フェーズ(P)は +1、負フェーズ(M)は -1。
    """
    m = re.search(r"_(\d+)\.\d{2}\.csv$", str(name))
    return sign * int(m.group(1)) if m else 0

# ロータリーステージ設定
#   origin_pulse は「その実験を計測した時の値」を使うのが正しい
#   （α0 は計測時の原点を基準に測られているため）。
#   優先1: 実験フォルダの experiment_log.json（run_experiment が計測時に記録）
#   優先2: リポジトリルートの config.json（現在の設定。古いログにはキーが無い）
#   優先3: 既定値 11025
PULSE_PER_DEG = 250     # pulse per degree (ARS-936-HP: 0.004°/pulse)


# 入力の明示指定（force_measurement.py から CLI で渡される）。None なら自動解決。
_DATA_DIR_OVERRIDE = None   # 6軸センサ CSV のある data フォルダ
_LOG_OVERRIDE      = None   # experiment_log.json のパス


def _raw_dir():
    """data/ と *_experiment_log.json を探す場所を返す（CLI 未指定時の自動解決）。
      新構成: force/analysis/ で実行 → data は ../data、log は ../../（実験直下）
      旧構成(フラット): 実験フォルダ直下で実行 → カレント
    """
    for cand in (".", os.path.join("..", "force_measurement"), ".."):
        if os.path.isdir(os.path.join(cand, "data")):
            return cand
    return "."


def _data_dir():
    """6軸センサ CSV のある data フォルダ。"""
    if _DATA_DIR_OVERRIDE:
        return _DATA_DIR_OVERRIDE
    return os.path.join(_raw_dir(), "data")


def _load_origin_pulse(default=11025):
    """origin_pulse を experiment_log → config.json → 既定値 の順で決める。"""
    if _LOG_OVERRIDE and os.path.isfile(_LOG_OVERRIDE):
        logs = [_LOG_OVERRIDE]
    else:
        # 自動解決: data の場所 と その1つ上（新構成では実験直下にログ）を探す
        search = {_raw_dir(), os.path.dirname(os.path.abspath(_data_dir()))}
        logs = []
        for d in search:
            logs += glob.glob(os.path.join(d, "*_experiment_log.json"))
        logs = sorted(logs)
    for lp in reversed(logs):                      # 複数あれば新しい日付を優先
        try:
            with open(lp, encoding="utf-8") as f:
                v = json.load(f).get("origin_pulse")
            if v is not None:
                return int(v)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
    repo_root   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(repo_root, "config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            return int(json.load(f).get("origin_pulse", default))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return default


ORIGIN_PULSE = _load_origin_pulse()   # 迎角0°に対応する機械座標 [pulse]（計測時の値）


def _check_duplicate_points(folder_list):
    """同一（フェーズ・角度）のCSVが複数無いか検査する。
    リトライ残骸などの重複があるとオフセット対応がずれて結果が静かに汚染される
    （260608 の Cl=NaN の原因）ため、処理前に明示的に止める。"""
    pt = re.compile(r"_(Pofst|Mofst|Pdata|Mdata)_(\d+\.\d{2})\.csv$")
    seen = {}
    for f in folder_list:
        m = pt.search(f)
        if m:
            seen.setdefault((m.group(1), m.group(2)), []).append(f)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    if not dups:
        return
    print("[エラー] 同一計測点のCSVが複数あります（中断・リトライの残骸の可能性）:")
    for (ph, ang), fs in sorted(dups.items()):
        print(f"  {ph} {ang}:")
        for f in fs:
            print(f"    - {f}")
    print("  → 正しい方（通常は新しいタイムスタンプ）を残して他を削除し、再実行してください。")
    raise SystemExit(1)


def average():
    data_dir = _data_dir()

    folder_list = os.listdir(data_dir)
    folder_list = sorted(folder_list)

    # 不要ファイルを除外
    if ".DS_Store" in folder_list:
        folder_list.remove(".DS_Store")

    # --- volt_raw / volt_summary を除外（新システムが data/ に混在させるため）---
    folder_list = [f for f in folder_list
                   if not f.endswith("_volt_raw.csv")
                   and not f.endswith("_volt_summary.csv")]

    # --- 重複計測点があれば（リトライ残骸など）汚染前に明示エラーで停止 ---
    _check_duplicate_points(folder_list)

    print(folder_list)

    col_names = ["time", "Fx", "Fy", "Fz", "Mx", "My", "Mz"]

    data1_force = pd.DataFrame(
        index=[],
        columns=["Fx", "Fy", "Fz", "Mx", "My", "Mz",
                 "Fx_sd", "Fy_sd", "Fz_sd", "Mx_sd", "My_sd", "Mz_sd"],
    )

    # --- [変更] 1ファイル/計測点に対応 ---
    # 旧: case_num = int(len(folder_list) / 2)
    case_num = len(folder_list)

    for i in range(case_num):
        # 旧: data1_name = folder_list[2 * i]
        data1_name = folder_list[i]

        data1_org = pd.read_csv(
            "%s/%s" % (data_dir, data1_name),
            delimiter=",",
            names=col_names,
            encoding="cp932",
        )
        data1 = data1_org[4:len(data1_org)].copy()
        for j in range(len(data1.columns)):
            data1[data1.columns[j]] = pd.to_numeric(
                data1[data1.columns[j]], errors="coerce"
            )
        data1_dsc = data1.describe()
        case_list = [
            data1_dsc.loc["mean", "Fx"], data1_dsc.loc["mean", "Fy"],
            data1_dsc.loc["mean", "Fz"], data1_dsc.loc["mean", "Mx"],
            data1_dsc.loc["mean", "My"], data1_dsc.loc["mean", "Mz"],
            data1_dsc.loc["std",  "Fx"], data1_dsc.loc["std",  "Fy"],
            data1_dsc.loc["std",  "Fz"], data1_dsc.loc["std",  "Mx"],
            data1_dsc.loc["std",  "My"], data1_dsc.loc["std",  "Mz"],
        ]
        data1_force.loc[data1_name] = case_list

    data1_force.to_csv("av_Forces.csv")


def drift():
    data = pd.read_csv("av_Forces.csv", usecols=[0, 1, 2, 3, 4, 5, 6])
    data.columns = ["name", "Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    _C6 = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    # 初期オフセットをゼロに（.loc[:, "A":"B"] のスライス代入は新しい pandas で
    # dtype 不整合エラーになるため、列リスト代入＋float化で安全に行う）
    data[_C6] = data[_C6].astype(float) - data.loc[0, _C6].astype(float)
    Pofst = np.empty((0, len(data.columns)))
    Mofst = np.empty((0, len(data.columns)))
    Pdata = np.empty((0, len(data.columns)))
    Mdata = np.empty((0, len(data.columns)))

    for i in range(len(data.index)):
        # 正迎角の後に負迎角を計測した場合
        if "Pofst" in data.loc[0, "name"]:
            if "Pofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Pofst = np.append(Pofst, [data.loc[i, :]], axis=0)
            elif "Pofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Pofst = np.append(Pofst, [data.loc[i, :]], axis=0)
                Pofst[len(Pofst) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"]
                )
            elif "Mofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Mofst = np.append(Mofst, [Pofst[0, :]], axis=0)
            elif "Mofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Mofst = np.append(Mofst, [data.loc[i, :]], axis=0)
                Mofst[len(Mofst) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"]
                )
            elif "Pdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Pdata = np.append(Pdata, [data.loc[i, :]], axis=0)
                Pdata[len(Pdata) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i - 1, "Fx":"Mz"]
                )
            elif "Pdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Pdata = np.append(Pdata, [data.loc[i, :]], axis=0)
                Pdata[len(Pdata) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"] + Pdata[0, 1:]
                )
            elif "Mdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Mdata = np.append(Mdata, [data.loc[i, :]], axis=0)
                Mdata[len(Mdata) - 1, 1:] = Pdata[0, 1:]
            elif "Mdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Mdata = np.append(Mdata, [data.loc[i, :]], axis=0)
                Mdata[len(Mdata) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"] + Mdata[0, 1:]
                )
        # 負迎角の後に正迎角を計測した場合
        if "Mofst" in data.loc[0, "name"]:
            if "Mofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Mofst = np.append(Mofst, [data.loc[i, :]], axis=0)
            elif "Mofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Mofst = np.append(Mofst, [data.loc[i, :]], axis=0)
                Mofst[len(Mofst) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"]
                )
            elif "Pofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Pofst = np.append(Pofst, [Mofst[0, :]], axis=0)
            elif "Pofst" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Pofst = np.append(Pofst, [data.loc[i, :]], axis=0)
                Pofst[len(Pofst) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"]
                )
            elif "Mdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Mdata = np.append(Mdata, [data.loc[i, :]], axis=0)
                Mdata[len(Mdata) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i - 1, "Fx":"Mz"]
                )
            elif "Mdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Mdata = np.append(Mdata, [data.loc[i, :]], axis=0)
                Mdata[len(Mdata) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"] + Mdata[0, 1:]
                )
            elif "Pdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("00.00.csv"):
                Pdata = np.append(Pdata, [data.loc[i, :]], axis=0)
                Pdata[len(Pdata) - 1, 1:] = Mdata[0, 4:]
            elif "Pdata" in data.loc[i, "name"] and data.loc[i, "name"].endswith("01.csv"):
                Pdata = np.append(Pdata, [data.loc[i, :]], axis=0)
                Pdata[len(Pdata) - 1, 1:] = (
                    data.loc[i, "Fx":"Mz"] - data.loc[i + 1, "Fx":"Mz"] + Pdata[0, 1:]
                )

    Pofst = pd.DataFrame(Pofst, columns=data.columns)
    Mofst = pd.DataFrame(Mofst, columns=data.columns)
    Pdata = pd.DataFrame(Pdata, columns=data.columns)
    Mdata = pd.DataFrame(Mdata, columns=data.columns)
    # AoA はファイル名の参照迎角から決める（刻み幅が 1° 以外でも正しくなる）。
    # 旧実装は行番号（index）を使っていたため、刻み幅 1° 以外で角度がずれていた。
    Pofst["AoA"] = [angle_from_name(n, +1) for n in Pofst["name"]]
    Pdata["AoA"] = [angle_from_name(n, +1) for n in Pdata["name"]]
    Mofst["AoA"] = [angle_from_name(n, -1) for n in Mofst["name"]]
    Mdata["AoA"] = [angle_from_name(n, -1) for n in Mdata["name"]]

    data_wind = pd.read_csv("windspeed.csv", skiprows=2)
    rho = float(pd.read_csv("windspeed.csv", header=None).iloc[0, 1])
    S = 0.04  # 翼面積 [m^2]
    U_P, U_M, q_P, q_M = [], [], [], []
    for i in range(len(data_wind)):
        if "Pdata" in data_wind.loc[i, "name"] and data_wind.loc[i, "name"].endswith("00.00"):
            U_P.append(data_wind.loc[i, "U"])
            q_P.append(0.5 * rho * data_wind.loc[i, "U"] ** 2 * S)
        elif "Pdata" in data_wind.loc[i, "name"] and data_wind.loc[i, "name"].endswith("01"):
            U_P.append(data_wind.loc[i, "U"])
            q_P.append(0.5 * rho * data_wind.loc[i, "U"] ** 2 * S)
        elif "Mdata" in data_wind.loc[i, "name"] and data_wind.loc[i, "name"].endswith("00.00"):
            U_M.append(data_wind.loc[i, "U"])
            q_M.append(0.5 * rho * data_wind.loc[i, "U"] ** 2 * S)
        elif "Mdata" in data_wind.loc[i, "name"] and data_wind.loc[i, "name"].endswith("01"):
            U_M.append(data_wind.loc[i, "U"])
            q_M.append(0.5 * rho * data_wind.loc[i, "U"] ** 2 * S)
    Pdata["U"] = U_P
    Mdata["U"] = U_M
    Pdata["q"] = q_P
    Mdata["q"] = q_M

    Pofst.to_csv("Pofst_Ncm.csv")
    Mofst.to_csv("Mofst_Ncm.csv")
    Pdata.to_csv("Pdata_Ncm.csv")
    Mdata.to_csv("Mdata_Ncm.csv")


def calc():
    Pofst = pd.read_csv("Pofst_Ncm.csv", index_col=0)
    Mofst = pd.read_csv("Mofst_Ncm.csv", index_col=0)
    Pdata = pd.read_csv("Pdata_Ncm.csv", index_col=0)
    Mdata = pd.read_csv("Mdata_Ncm.csv", index_col=0)
    _C6 = ["Fx", "Fy", "Fz", "Mx", "My", "Mz"]
    Pdata[_C6] = Pdata[_C6].astype(float) - Pofst[_C6].astype(float)
    Mdata[_C6] = Mdata[_C6].astype(float) - Mofst[_C6].astype(float)
    data = pd.concat([Mdata.iloc[::-1], Pdata], ignore_index=True)

    # 空力中心まわり
    _CF = ["Fx", "Fy", "Fz"]
    _CM = ["Mx", "My", "Mz"]
    F_adcenter_gf = data.copy()
    F_adcenter_gf[_CF] = data[_CF].astype(float) / 9.8 * 1000        # N → gf
    F_adcenter_gf[_CM] = data[_CM].astype(float) / 9.8 * 1000 * 10   # Nm → gf10cm
    calbmatrix = np.matrix([
        [1, 0, 0, 0, 0, 0],
        [0, 0, 1, 0, 0, 0],
        [0, -1, 0, 0, 0, 0],
        [0, 1.346, 0, 1, 0, 0],
        [0, 0, 0, 0, 0, -1],
        [1.346, 0, 0, 0, -1, 0],
    ])
    for i in range(len(F_adcenter_gf)):
        F = np.dot(
            calbmatrix,
            np.array([F_adcenter_gf.loc[i, "Fx":"Mz"].to_numpy().astype(float)]).T,
        ).T
        F_adcenter_gf.loc[i, "Fx"] = F[0, 0]
        F_adcenter_gf.loc[i, "Fy"] = F[0, 1]
        F_adcenter_gf.loc[i, "Fz"] = F[0, 2]
        F_adcenter_gf.loc[i, "Mx"] = F[0, 3]
        F_adcenter_gf.loc[i, "My"] = F[0, 4]
        F_adcenter_gf.loc[i, "Mz"] = F[0, 5]
    F_adcenter = F_adcenter_gf
    F_adcenter[_CF] = F_adcenter[_CF].astype(float) * 9.8 / 1000        # gf → N
    F_adcenter[_CM] = F_adcenter[_CM].astype(float) * 9.8 / 1000 / 10   # gf10cm → Nm
    F_adcenter = F_adcenter.rename(
        columns={"Fx": "F1", "Fy": "F2", "Fz": "F3", "Mx": "F4", "My": "F5", "Mz": "F6"}
    )
    F_adcenter.to_csv("F_adcenter_Nm.csv")

    # 気流に垂直方向
    F_aero = F_adcenter.rename(
        columns={"F1": "L", "F2": "D", "F3": "S", "F4": "R", "F5": "P", "F6": "Y"}
    )
    for i in range(len(F_aero)):
        F_aero.loc[i, "D"] = (
            -float(F_adcenter.loc[i, "F1"]) * np.cos(np.radians(float(F_aero.loc[i, "AoA"])))
            + float(F_adcenter.loc[i, "F3"]) * np.sin(np.radians(float(F_aero.loc[i, "AoA"])))
        )
        F_aero.loc[i, "L"] = (
            float(F_adcenter.loc[i, "F1"]) * np.sin(np.radians(float(F_aero.loc[i, "AoA"])))
            + float(F_adcenter.loc[i, "F3"]) * np.cos(np.radians(float(F_aero.loc[i, "AoA"])))
        )
    F_aero.to_csv("F_aero_Nm.csv")

    # 無次元化
    C_aero = F_aero.rename(
        columns={"L": "Cl", "D": "Cd", "S": "Cs", "R": "Cr", "P": "Cm", "Y": "Cy"}
    )
    c_chord = 0.20    # 代表長さ（翼弦長 [m]）＝モーメント係数の無次元化に使う
    n_zero_q = 0
    for i in range(len(C_aero)):
        q = float(C_aero.loc[i, "q"])
        # 動圧 q=0（風速 U=0）の点は無次元化できない。ここで割ると
        # ZeroDivisionError で後処理全体が落ちるため、クラッシュさせず NaN にする。
        # （通風なしのテスト計測や、差圧センサ/デジボルが差圧を拾えていない時に起きる）
        if q <= 0:
            for col in ("Cl", "Cd", "Cs", "Cm", "Cr", "Cy"):
                C_aero.loc[i, col] = np.nan
            n_zero_q += 1
            continue
        C_aero.loc[i, "Cl"] = float(F_aero.loc[i, "L"]) / q
        C_aero.loc[i, "Cd"] = float(F_aero.loc[i, "D"]) / q
        C_aero.loc[i, "Cs"] = float(F_aero.loc[i, "S"]) / q
        C_aero.loc[i, "Cm"] = float(F_aero.loc[i, "P"]) / (q * c_chord)
        C_aero.loc[i, "Cr"] = float(F_aero.loc[i, "R"]) / (q * c_chord)
        C_aero.loc[i, "Cy"] = float(F_aero.loc[i, "Y"]) / (q * c_chord)
    if n_zero_q:
        print(f"[警告] 動圧 q=0（風速 U=0）の計測点が {n_zero_q}/{len(C_aero)} 点あります。"
              "該当点の空力係数は NaN にしました。\n"
              "  → 通風していない、または差圧センサ/デジボルが差圧を読めていない可能性があります"
              "（差圧電圧が零点オフセットとほぼ同じ）。")
    C_aero = C_aero.drop(["U", "q"], axis=1)
    C_aero.to_csv("C_aero_raw.csv")


def plot_C_raw():
    C_aero = pd.read_csv("C_aero_raw.csv", index_col=0)
    textsize = 24
    lw = 2
    mk = 4

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(C_aero.loc[:, "AoA"], C_aero.loc[:, "Cl"],
            color="royalblue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-30, 30)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_l$", fontsize=textsize)
    fig.savefig("Cl_raw.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(C_aero.loc[:, "AoA"], C_aero.loc[:, "Cd"],
            color="royalblue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-30, 30)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_d$", fontsize=textsize)
    fig.savefig("Cd_raw.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(C_aero.loc[:, "AoA"], C_aero.loc[:, "Cm"],
            color="royalblue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-30, 30)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_m$", fontsize=textsize)
    fig.savefig("Cm_raw.png", bbox_inches="tight")
    plt.close()


def calb():
    """風洞壁補正"""
    C_aero_raw = pd.read_csv("C_aero_raw.csv", index_col=0)
    AoA = np.array(C_aero_raw.loc[:, "AoA"])
    Cl  = np.array(C_aero_raw.loc[:, "Cl"])
    Cd  = np.array(C_aero_raw.loc[:, "Cd"])
    Cm  = np.array(C_aero_raw.loc[:, "Cm"])

    c, h = 0.20, 0.60
    AoA_mod = AoA - (0.25 * c / h * Cl + np.pi / 24 * (c / h) ** 2 * Cl) * 180 / np.pi
    Cd_mod  = Cd - 0.25 * c / h * Cl ** 2
    Cm_mod  = Cm - np.pi ** 2 / 96 * (c / h) ** 2 * Cl

    C_aero = pd.DataFrame()
    C_aero["AoA"]     = AoA
    C_aero["AoA_mod"] = AoA_mod
    C_aero["Cl"]      = Cl
    C_aero["Cd"]      = Cd_mod
    C_aero["Cm"]      = Cm_mod
    C_aero.to_csv("C_aero.csv", index=False)


def plot_C_aero():
    data = pd.read_csv("C_aero.csv", delimiter=",")
    textsize = 24
    lw = 3
    mk = 8

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[:, "AoA_mod"], data.loc[:, "Cl"],
            color="blue", marker="^", linewidth=lw, markersize=mk)
    x = np.arange(-30, 30)
    ax.plot(x, 2 * np.pi * np.pi / 180 * x,
            color="gray", label=r"$C_{l_{\alpha}}=2\pi$",
            linestyle="--", linewidth=lw, markersize=mk)
    ax.set_xlim(-30, 30)
    ax.set_ylim(-1.0, 1.0)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_l$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cl.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[:, "AoA_mod"], data.loc[:, "Cd"],
            color="blue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-30, 30)
    ax.set_ylim(0, 0.3)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_d$", fontsize=textsize)
    fig.savefig("Cd.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[:, "AoA_mod"], data.loc[:, "Cm"],
            color="blue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-30, 30)
    ax.set_ylim(-0.2, 0.2)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_m$", fontsize=textsize)
    fig.savefig("Cm.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[:, "Cl"], data.loc[:, "Cd"],
            color="blue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-1.0, 1.0)
    ax.set_ylim(0, 0.25)
    ax.set_xlabel(r"$C_l$", fontsize=textsize)
    ax.set_ylabel(r"$C_d$", fontsize=textsize)
    fig.savefig("polar.png", bbox_inches="tight")
    plt.close()


def plot_PM():
    data = pd.read_csv("C_aero.csv", delimiter=",")

    # P-M 比較は正負両方のデータが前提。片側のみ（正のみ／負のみ）の計測では
    # 左右対称に分割できないためスキップする。
    if (data["AoA"] > 0).sum() == 0 or (data["AoA"] < 0).sum() == 0:
        print("[plot_PM] 片側のみの計測のため P-M 比較図はスキップします。")
        return

    textsize = 24
    lw = 3
    mk = 8

    # data = [Mdata_reversed, Pdata] で両者は同数。先半分が負迎角、後半分が正迎角。
    n = len(data) // 2   # = max_angle + 1
    max_aoa = n - 1
    pos = data.loc[n:].reset_index(drop=True)   # 正迎角 (AoA 0 〜 max)
    neg = data.loc[:n - 1].reset_index(drop=True)  # 負迎角 (AoA -max 〜 0, 反転済み)

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(pos.loc[:, "AoA_mod"], pos.loc[:, "Cl"],
            color="red", marker="^", linewidth=lw, markersize=mk, label="Positive")
    ax.plot(-neg.loc[:, "AoA_mod"], -neg.loc[:, "Cl"],
            color="blue", marker="^", linewidth=lw, markersize=mk, label="Negative")
    x = np.arange(0, max_aoa + 1)
    ax.plot(x, 2 * np.pi * np.pi / 180 * x,
            color="gray", label=r"$C_{l_{\alpha}}=2\pi$",
            linestyle="--", linewidth=lw, markersize=mk)
    ax.set_xlim(0, max_aoa)
    ax.set_ylim(0, 1.0)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_l$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cl_PM.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(pos.loc[:, "AoA_mod"], pos.loc[:, "Cd"],
            color="red", marker="^", linewidth=lw, markersize=mk, label="Positive")
    ax.plot(-neg.loc[:, "AoA_mod"], neg.loc[:, "Cd"],
            color="blue", marker="^", linewidth=lw, markersize=mk, label="Negative")
    ax.set_xlim(0, max_aoa)
    ax.set_ylim(0, 0.3)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_d$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cd_PM.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(pos.loc[:, "AoA_mod"], pos.loc[:, "Cm"],
            color="red", marker="^", linewidth=lw, markersize=mk, label="Positive")
    ax.plot(-neg.loc[:, "AoA_mod"], -neg.loc[:, "Cm"],
            color="blue", marker="^", linewidth=lw, markersize=mk, label="Negative")
    ax.set_xlim(0, max_aoa)
    ax.set_ylim(-0.2, 0.2)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_m$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cm_PM.png", bbox_inches="tight")
    plt.close()


def report_zero_lift():
    """C_aero_raw の線形域から α0（ゼロ揚力角）を推定し、
    次回実験に向けた ORIGIN_PULSE 推奨値をターミナルに出力する。

    推定範囲: AoA = -5° 〜 +10°（失速前の線形域）
    推奨値  : ORIGIN_PULSE_next = ORIGIN_PULSE - round(α0 × PULSE_PER_DEG)
    """
    # 前回実行のレポートが残ると、データ不足でスキップした場合に
    # 古い推奨値が y/n プロンプトに出てしまうため、先に削除しておく。
    try:
        os.remove("zero_lift_report.json")
    except FileNotFoundError:
        pass

    data = pd.read_csv("C_aero_raw.csv")

    mask = (data["AoA"] >= -5) & (data["AoA"] <= 10)
    sub  = data[mask].dropna(subset=["AoA", "Cl"])

    if len(sub) < 4:
        print("[report_zero_lift] 線形域のデータ点が不足しています。スキップします。")
        return

    p      = np.polyfit(sub["AoA"], sub["Cl"], 1)   # p[0]=slope, p[1]=intercept
    alpha0 = -p[1] / p[0]                            # Cl=0 となる AoA [度]
    Cl_at0 = float(np.polyval(p, 0))

    correction = round(alpha0 * PULSE_PER_DEG)
    suggested  = ORIGIN_PULSE - correction

    sep = "=" * 56
    print(sep)
    print("  ゼロ揚力角 (α0) 推定レポート")
    print(sep)
    print(f"  回帰範囲          : AoA = {sub['AoA'].min():.0f}° 〜 {sub['AoA'].max():.0f}°")
    print(f"  Cl スロープ       : {p[0]:.4f} /°  ({p[0]*180/math.pi:.4f} /rad)")
    print(f"  α0                : {alpha0:+.3f}°   [Cl(0°) = {Cl_at0:+.4f}]")
    print(sep)
    print(f"  計測時の原点パルス: {ORIGIN_PULSE} pulse  (experiment_log → config.json の順で取得)")
    print(f"  補正量            : {correction:+d} pulse  ({alpha0:+.3f}°)")
    print(f"  次回推奨 origin   : {suggested} pulse")
    print(sep)

    # MATLAB 側（run_postprocess.m）が読み取り、y/n で config.json を更新する用のレポート。
    report = {
        "alpha0_deg":            round(float(alpha0), 4),
        "Cl_slope_per_deg":      round(float(p[0]), 6),
        "current_origin_pulse":  int(ORIGIN_PULSE),
        "suggested_origin_pulse": int(suggested),
        "correction_pulse":      int(correction),
        "pulse_per_deg":         int(PULSE_PER_DEG),
    }
    try:
        with open("zero_lift_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("  → ゼロ揚力角レポートを zero_lift_report.json に保存しました。")
    except OSError as e:
        print(f"  [警告] zero_lift_report.json を保存できませんでした: {e}")
    print(sep)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="6軸力データ→空力係数（windspeed.csv はカレントから読む）")
    ap.add_argument("--data_dir", default=None,
                    help="6軸センサ CSV のある data フォルダ（省略時は自動解決）")
    ap.add_argument("--log", default=None,
                    help="experiment_log.json のパス（origin_pulse 用・省略時は自動探索）")
    args = ap.parse_args()

    global _DATA_DIR_OVERRIDE, _LOG_OVERRIDE, ORIGIN_PULSE
    if args.data_dir:
        _DATA_DIR_OVERRIDE = args.data_dir
    if args.log:
        _LOG_OVERRIDE = args.log
    ORIGIN_PULSE = _load_origin_pulse()   # 指定を反映して再計算

    average()
    drift()
    calc()
    plot_C_raw()
    calb()
    plot_C_aero()
    plot_PM()
    report_zero_lift()


if __name__ == "__main__":
    main()
