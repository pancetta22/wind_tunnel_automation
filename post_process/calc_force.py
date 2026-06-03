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
#   cd <解析フォルダ>          ← data/ フォルダと windspeed.csv が存在する場所
#   python calc_force.py
#
# 【必要な入力ファイル】
#   data/        6軸センサ CSV（全4フェーズ 244ファイル）
#   windspeed.csv  差圧電圧→風速変換済みCSV（make_windspeed.py で生成）
#
# 【生成されるファイル】
#   av_Forces.csv, Pofst/Mofst/Pdata/Mdata_Ncm.csv,
#   F_adcenter_Nm.csv, F_aero_Nm.csv, C_aero_raw.csv, C_aero.csv
#   Cl.png, Cd.png, Cm.png, polar.png, Cl_PM.png, Cd_PM.png, Cm_PM.png

import os
import pandas as pd
import numpy as np
import math
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
from tqdm import tqdm
import traceback


def average():
    dir = os.getcwd()
    data_dir = "%s/data" % dir

    folder_list = os.listdir(data_dir)
    folder_list = sorted(folder_list)

    # 不要ファイルを除外
    if ".DS_Store" in folder_list:
        folder_list.remove(".DS_Store")

    # --- [変更] volt_raw.csv を除外（新システムが data/ に混在させる場合の対策）---
    folder_list = [f for f in folder_list if not f.endswith("_volt_raw.csv")]

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
    data.loc[:, "Fx":"Mz"] = data.loc[:, "Fx":"Mz"] - data.loc[0, "Fx":"Mz"]  # 初期オフセットをゼロ
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
    Pofst["AoA"] = Pofst.index
    Pdata["AoA"] = Pdata.index
    Mofst["AoA"] = -Mofst.index
    Mdata["AoA"] = -Mdata.index

    data_wind = pd.read_csv("windspeed.csv", skiprows=2)
    rho = float(pd.read_csv("windspeed.csv", header=None).iloc[0, 1])
    S = 0.04  # 翼面積 [m²]
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
    Pdata.loc[:, "Fx":"Mz"] = Pdata.loc[:, "Fx":"Mz"] - Pofst.loc[:, "Fx":"Mz"]
    Mdata.loc[:, "Fx":"Mz"] = Mdata.loc[:, "Fx":"Mz"] - Mofst.loc[:, "Fx":"Mz"]
    data = pd.concat([Mdata.iloc[::-1], Pdata], ignore_index=True)

    # 空力中心まわり
    F_adcenter_gf = data.copy()
    F_adcenter_gf.loc[:, "Fx":"Fz"] = data.loc[:, "Fx":"Fz"] / 9.8 * 1000      # N → gf
    F_adcenter_gf.loc[:, "Mx":"Mz"] = data.loc[:, "Mx":"Mz"] / 9.8 * 1000 / 10  # Nm → gf10cm
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
    F_adcenter.loc[:, "Fx":"Fz"] = F_adcenter.loc[:, "Fx":"Fz"] * 9.8 / 1000        # gf → N
    F_adcenter.loc[:, "Mx":"Mz"] = F_adcenter.loc[:, "Mx":"Mz"] * 9.8 / 1000 / 10   # gf10cm → Nm
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
    for i in range(len(C_aero)):
        C_aero.loc[i, "Cl"] = float(F_aero.loc[i, "L"]) / float(C_aero.loc[i, "q"])
        C_aero.loc[i, "Cd"] = float(F_aero.loc[i, "D"]) / float(C_aero.loc[i, "q"])
        C_aero.loc[i, "Cs"] = float(F_aero.loc[i, "S"]) / float(C_aero.loc[i, "q"])
        C_aero.loc[i, "Cm"] = float(F_aero.loc[i, "P"]) / (float(C_aero.loc[i, "q"]) * 0.20)
        C_aero.loc[i, "Cr"] = float(F_aero.loc[i, "R"]) / (float(C_aero.loc[i, "q"]) * 0.20)
        C_aero.loc[i, "Cy"] = float(F_aero.loc[i, "Y"]) / (float(C_aero.loc[i, "q"]) * 0.20)
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
    ax.set_xlim(-20, 20)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_l$", fontsize=textsize)
    fig.savefig("Cl_raw.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(C_aero.loc[:, "AoA"], C_aero.loc[:, "Cd"],
            color="royalblue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-20, 20)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_d$", fontsize=textsize)
    fig.savefig("Cd_raw.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(C_aero.loc[:, "AoA"], C_aero.loc[:, "Cm"],
            color="royalblue", marker="^", linewidth=lw, markersize=mk)
    ax.set_xlim(-20, 20)
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
    ax.set_xlim(-20, 20)
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
    ax.set_xlim(-20, 20)
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
    ax.set_xlim(-20, 20)
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
    textsize = 24
    lw = 3
    mk = 8

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[21:, "AoA_mod"], data.loc[21:, "Cl"],
            color="red", marker="^", linewidth=lw, markersize=mk, label="Positive")
    ax.plot(-data.loc[:20, "AoA_mod"], -data.loc[:20, "Cl"],
            color="blue", marker="^", linewidth=lw, markersize=mk, label="Negative")
    x = np.arange(-30, 30)
    ax.plot(x, 2 * np.pi * np.pi / 180 * x,
            color="gray", label=r"$C_{l_{\alpha}}=2\pi$",
            linestyle="--", linewidth=lw, markersize=mk)
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 1.0)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_l$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cl_PM.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[21:, "AoA_mod"], data.loc[21:, "Cd"],
            color="red", marker="^", linewidth=lw, markersize=mk, label="Positive")
    ax.plot(-data.loc[:20, "AoA_mod"], data.loc[:20, "Cd"],
            color="blue", marker="^", linewidth=lw, markersize=mk, label="Negative")
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 0.3)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_d$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cd_PM.png", bbox_inches="tight")
    plt.close()

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.tick_params(labelsize=textsize)
    ax.grid(True)
    ax.plot(data.loc[21:, "AoA_mod"], data.loc[21:, "Cm"],
            color="red", marker="^", linewidth=lw, markersize=mk, label="Positive")
    ax.plot(-data.loc[:20, "AoA_mod"], -data.loc[:20, "Cm"],
            color="blue", marker="^", linewidth=lw, markersize=mk, label="Negative")
    ax.set_xlim(0, 20)
    ax.set_ylim(-0.2, 0.2)
    ax.set_xlabel("AoA [deg]", fontsize=textsize)
    ax.set_ylabel(r"$C_m$", fontsize=textsize)
    ax.legend(fontsize=20)
    fig.savefig("Cm_PM.png", bbox_inches="tight")
    plt.close()


average()
drift()
calc()
plot_C_raw()
calb()
plot_C_aero()
plot_PM()
