#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_windspeed.py
差圧電圧サマリー（volt_summary）から windspeed.csv を生成するスクリプト。

【使い方（基本）】
  cd <解析フォルダ>
  python make_windspeed.py --volt_dir C:/Users/.../WindyData --date 20260520

  → --log を省略すると同じ日付の experiment_log.json を volt_dir から自動で探します。

【使い方（ログファイルを明示指定）】
  python make_windspeed.py ^
      --volt_dir C:/Users/.../WindyData ^
      --date     20260520 ^
      --log      C:/Users/.../WindyData/20260520_experiment_log.json

【使い方（手動入力で上書き）】
  python make_windspeed.py ^
      --volt_dir C:/Users/.../WindyData ^
      --date     20260520 ^
      --temp     25.4 ^
      --pressure 760.7

【引数】
  必須:
    --volt_dir    volt_summary.csv があるフォルダ（run_experiment の output_dir）
    --date        実験日 YYYYMMDD（例: 20260520）

  ログファイル（省略時は自動検索）:
    --log         experiment_log.json のパス（省略時: volt_dir/YYYYMMDD_experiment_log.json）

  手動上書き（--log より優先）:
    --temp        気温 [℃]
    --pressure    気圧 [mmHg]
    --offset      差圧センサ零点オフセット [mV]（デフォルト: -5.0）
    --a           変換係数 a [cm/mV]（デフォルト: 0.007904809948345278）
    --b           変換係数 b [cm]（デフォルト: -0.340200009144243）
    --water_dens  水密度 [g/cm³]（デフォルト: 0.99704）

  出力:
    --out         windspeed.csv の保存先フォルダ（省略時: カレントディレクトリ）

【風速の計算式（yymmdd.xlsx と完全一致）】
  飽和水蒸気圧  e     = 6.1078 × 10^(7.5T / (237.3+T))        [hPa]
  較正気圧      P_cal = 1013.25/760 × (1 - 0.000182T) × P_mmHg [hPa]
  空気密度      ρ     = 1.293 × (273.15/(273.15+T)) × (P_cal/1013.25) × (1 - 0.378e/P_cal)
  動圧高さ      h     = (V_mV - offset) × a + b               [cm, 水柱]
  風速          U     = √(2 × water_density × h × g / ρ)      [m/s]

  ※ 変換係数 a, b は 2022年3月29日 微差圧センサ風速較正値（yymmdd.xlsx より）
"""

import argparse
import json
import math
import os
import sys

import pandas as pd


# ============================================================
#  計算関数
# ============================================================

def calc_rho(T_C: float, P_mmHg: float) -> float:
    """気温・気圧から空気密度を計算する（yymmdd.xlsx と同一式）"""
    e     = 6.1078 * 10 ** (7.5 * T_C / (237.3 + T_C))
    P_cal = 1013.25 / 760 * (1 - 0.000182 * T_C) * P_mmHg
    rho   = (1.293
             * (273.15 / (273.15 + T_C))
             * (P_cal / 1013.25)
             * (1 - 0.378 * e / P_cal))
    return rho


def mV_to_U(mV: float, rho: float,
            water_density: float, offset_mV: float,
            a: float, b: float) -> float:
    """差圧電圧 [mV] から風速 [m/s] を計算する（yymmdd.xlsx と完全一致）

    U = sqrt(2 * water_density * ((mV - offset) * a + b) * g / rho)

    ※ water_density は g/cm³ 単位、h = (mV-offset)*a + b は cm 単位で
       数値的に等価な計算を行っている（Excel セル C11, C15, C16 参照）。
    """
    G = 9.80665  # 重力加速度 [m/s²]
    h = (mV - offset_mV) * a + b  # 水柱高さ相当値 [cm]
    inner = 2.0 * water_density * h * G / rho
    if inner <= 0:
        return 0.0
    return math.sqrt(inner)


# ============================================================
#  ログファイル読み込み
# ============================================================

def load_log(log_path: str) -> dict:
    """experiment_log.json を読み込んで辞書で返す"""
    with open(log_path, encoding="utf-8") as f:
        return json.load(f)


# ============================================================
#  メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="volt_summary.csv → windspeed.csv 変換スクリプト（Windy）"
    )
    # 必須
    parser.add_argument("--volt_dir", required=True,
                        help="volt_summary.csv が入っているフォルダ")
    parser.add_argument("--date",     required=True,
                        help="実験日 YYYYMMDD（例: 20260520）")
    # ログファイル
    parser.add_argument("--log",      default=None,
                        help="experiment_log.json のパス（省略時: volt_dir 内を自動検索）")
    # 手動上書き
    parser.add_argument("--temp",       type=float, default=None,
                        help="気温 [℃]（--log より優先）")
    parser.add_argument("--pressure",   type=float, default=None,
                        help="気圧 [mmHg]（--log より優先）")
    parser.add_argument("--offset",     type=float, default=None,
                        help="零点オフセット [mV]（デフォルト: -5.0）")
    parser.add_argument("--a",          type=float, default=None,
                        help="変換係数 a [cm/mV]（デフォルト: 0.007905）")
    parser.add_argument("--b",          type=float, default=None,
                        help="変換係数 b [cm]（デフォルト: -0.340）")
    parser.add_argument("--water_dens", type=float, default=None,
                        help="水密度 [g/cm³]（デフォルト: 0.99704）")
    # 出力先
    parser.add_argument("--out",        default=None,
                        help="windspeed.csv の保存先フォルダ（省略時: カレントディレクトリ）")
    args = parser.parse_args()

    # ------ デフォルト校正値 ------
    DEFAULTS = {
        "water_density":  0.99704,
        "volt_offset_mV": -5.0,
        "calib_a":        0.007904809948345278,
        "calib_b":        -0.340200009144243,
    }

    # ------ ログファイルの読み込み ------
    log = {}
    log_path = args.log
    if log_path is None:
        log_path = os.path.join(args.volt_dir,
                                f"{args.date}_experiment_log.json")
    if os.path.isfile(log_path):
        log = load_log(log_path)
        print(f"[ログ] {log_path} を読み込みました。")
    else:
        if args.log is not None:
            print(f"[エラー] ログファイルが見つかりません: {log_path}", file=sys.stderr)
            sys.exit(1)
        if args.temp is None or args.pressure is None:
            print(
                f"[警告] {args.date}_experiment_log.json が見つかりません。\n"
                "  --temp と --pressure を手動で指定してください。",
                file=sys.stderr,
            )
            sys.exit(1)

    # ------ パラメータ確定（優先順位: 手動引数 > ログ > デフォルト）------
    def resolve(arg_val, log_key, default):
        if arg_val is not None:
            return arg_val
        if log_key in log:
            return float(log[log_key])
        return default

    T_C          = resolve(args.temp,       "temperature_C",  None)
    P_mmHg       = resolve(args.pressure,   "pressure_mmHg",  None)
    water_dens   = resolve(args.water_dens, "water_density",  DEFAULTS["water_density"])
    offset_mV    = resolve(args.offset,     "volt_offset_mV", DEFAULTS["volt_offset_mV"])
    a            = resolve(args.a,          "calib_a",        DEFAULTS["calib_a"])
    b            = resolve(args.b,          "calib_b",        DEFAULTS["calib_b"])

    # 空気密度の決定
    if "rho_kg_m3" in log and args.temp is None and args.pressure is None:
        rho = float(log["rho_kg_m3"])
        print(f"[パラメータ] ρ = {rho:.6f} kg/m³（ログから読み込み）")
    elif T_C is not None and P_mmHg is not None:
        rho = calc_rho(T_C, P_mmHg)
        print(f"[パラメータ] T = {T_C}℃, P = {P_mmHg} mmHg → ρ = {rho:.6f} kg/m³")
    else:
        print("[エラー] 空気密度を決定できません。--temp と --pressure を指定してください。",
              file=sys.stderr)
        sys.exit(1)

    print(f"[パラメータ] offset = {offset_mV} mV,  a = {a},  b = {b},  "
          f"water_density = {water_dens}")

    # ------ volt_summary の読み込みと windspeed 行の生成 ------
    rows = []
    for phase in ["Pdata", "Mdata"]:
        fname = f"{args.date}_{phase}_volt_summary.csv"
        fpath = os.path.join(args.volt_dir, fname)
        if not os.path.isfile(fpath):
            print(f"[警告] {fname} が見つかりません。スキップします。")
            continue

        df = pd.read_csv(fpath, encoding="utf-8-sig")

        required = ["name", "差圧電圧[mV]"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            print(f"[エラー] {fname} に列 {missing} が見つかりません。", file=sys.stderr)
            sys.exit(1)

        count = 0
        for _, row in df.iterrows():
            try:
                mv = float(row["差圧電圧[mV]"])
            except (ValueError, TypeError):
                print(f"  [スキップ] {row['name']}: 差圧電圧が読み取れません")
                continue

            U = mV_to_U(mv, rho, water_dens, offset_mV, a, b)
            rows.append({
                "name": row["name"],
                "mV":   f"{mv:.0f}",
                "U":    f"{U:.8f}",
            })
            count += 1

        print(f"[読み込み] {fname}: {count} 行")

    if not rows:
        print("[エラー] Pdata / Mdata の volt_summary が1件も読み込めませんでした。",
              file=sys.stderr)
        sys.exit(1)

    # ------ windspeed.csv を書き出す ------
    out_dir  = args.out if args.out else os.getcwd()
    out_path = os.path.join(out_dir, "windspeed.csv")
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"rho,{rho},\n")
        f.write(",,\n")
        f.write("name,mV,U\n")
        for r in rows:
            f.write(f"{r['name']},{r['mV']},{r['U']}\n")

    print(f"\n[完了] {out_path} を生成しました（{len(rows)} 行）")


if __name__ == "__main__":
    main()
