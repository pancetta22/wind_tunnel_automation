#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# force_measurement.py  力計測の後処理オーケストレータ（Windy 新システム）
#
# 実験フォルダを1つ受け取り、差圧→風速（make_windspeed.py）と
# 6軸力→空力係数（calc_force.py）を順に実行する。後処理の入口はこの1本。
#
# 【新フォルダ構成】
#   <実験フォルダ>/
#     ├ <YYYYMMDD>_experiment_log.json   ← 気温・気圧・校正定数（実験直下）
#     └ force/
#         ├ data/        生データ（6軸CSV・volt_summary・volt_raw）
#         └ analysis/    ここに windspeed.csv・C_aero.csv・グラフ等を出力
#
#   旧フラット構成（過去実験）にも対応：data/ と log が実験フォルダ直下にある場合は
#   そのまま実験フォルダを解析フォルダとして扱う。
#
# 【実行方法】
#   python force_measurement.py <実験フォルダ>
#     （run_postprocess.m から venv の python で呼ばれる）

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAKE_WS = os.path.join(SCRIPT_DIR, "make_windspeed.py")
CALC_F = os.path.join(SCRIPT_DIR, "calc_force.py")


def resolve_paths(exp_dir):
    """新構成(force/data・force/analysis)か旧フラットかを判定し、
    (data_dir, analysis_dir, log_path, date_str) を返す。"""
    force_data = os.path.join(exp_dir, "force", "data")
    if os.path.isdir(force_data):
        data_dir = force_data
        analysis_dir = os.path.join(exp_dir, "force", "analysis")
    else:
        data_dir = os.path.join(exp_dir, "data")    # 旧フラット
        analysis_dir = exp_dir
    os.makedirs(analysis_dir, exist_ok=True)

    # experiment_log.json は実験フォルダ直下（新）か、data の親（旧）を探す
    logs = sorted(glob.glob(os.path.join(exp_dir, "*_experiment_log.json")) +
                  glob.glob(os.path.join(os.path.dirname(data_dir), "*_experiment_log.json")))
    if not logs:
        print(f"[エラー] experiment_log.json が見つかりません: {exp_dir}", file=sys.stderr)
        sys.exit(1)
    log_path = logs[-1]
    m = re.search(r"(\d{8})", os.path.basename(log_path))
    if not m:
        print(f"[エラー] ログ名から実験日を判定できません: {log_path}", file=sys.stderr)
        sys.exit(1)
    return data_dir, analysis_dir, log_path, m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser(description="力計測の後処理（windspeed → 空力係数）")
    ap.add_argument("exp_dir", help="実験フォルダのパス")
    args = ap.parse_args()

    exp_dir = os.path.abspath(args.exp_dir)
    if not os.path.isdir(exp_dir):
        print(f"[エラー] 実験フォルダがありません: {exp_dir}", file=sys.stderr)
        return 1

    data_dir, analysis_dir, log_path, date_str = resolve_paths(exp_dir)
    print(f"[force] 実験日={date_str}")
    print(f"[force] data    : {data_dir}")
    print(f"[force] analysis: {analysis_dir}")

    py = sys.executable   # この後処理を起動している venv の python

    # --- Step 1: windspeed.csv（force/analysis へ出力）---
    print("[force 1/2] windspeed.csv を生成中...")
    r1 = subprocess.run([py, MAKE_WS,
                         "--volt_dir", data_dir,
                         "--date", date_str,
                         "--out", analysis_dir,
                         "--log", log_path])
    if r1.returncode != 0:
        print(f"[force] make_windspeed.py に失敗（終了コード {r1.returncode}）", file=sys.stderr)
        return r1.returncode

    # --- Step 2: 空力係数・グラフ（analysis_dir をカレントにして実行）---
    print("[force 2/2] 空力係数を計算・グラフを出力中...")
    r2 = subprocess.run([py, CALC_F, "--data_dir", data_dir, "--log", log_path],
                        cwd=analysis_dir)
    if r2.returncode != 0:
        print(f"[force] calc_force.py に失敗（終了コード {r2.returncode}）", file=sys.stderr)
        return r2.returncode

    print(f"[force] 完了。出力: {analysis_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
