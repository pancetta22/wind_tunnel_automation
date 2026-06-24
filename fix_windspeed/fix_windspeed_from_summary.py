#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_windspeed_from_summary.py
  代表風速を「各caseの volt_summary.csv 平均」から再計算する修正スクリプト。

260624_flutter で発生した2つの不具合に対応する：

  不具合1: 差圧センサ（デジボル）のプラグを逆に刺したため、差圧電圧 [mV] が
           逆符号で記録された。            → 平均値の符号を反転して補正。

  不具合2: 各caseの冒頭で計測する代表電圧（experiment_log.json の
           rep_windspeed_mV）にバッファ残留データが混入し不適切。
           → rep_windspeed_mV は使わず、各caseの volt_summary.csv
             （Pdata + Mdata）の差圧電圧[mV]の平均（NaN除外）を代表値とする。

各 case の experiment_log.json の rep_windspeed_mV / rep_windspeed_U を
上書き保存する。風速換算式は make_windspeed.py / flutter_run_experiment.m と同一。

【使い方】
  # コンテナフォルダ（_ofst / _c01 / _c02 ... を内包）を指定して一括修正
  python fix_windspeed_from_summary.py --base_dir "C:/.../WindyData/260624_flutter"

  # 1条件だけ修正
  python fix_windspeed_from_summary.py --exp_dir "C:/.../260624_flutter/260624_flutter_c01"

  # 確認のみ（書き込まない）
  python fix_windspeed_from_summary.py --base_dir "..." --dry_run

【注意】
  - ofst フォルダは修正対象外（差圧電圧を風速換算しないため）。
  - 上書き前に experiment_log.json を *.bak としてバックアップする（--no_backup で無効化）。
  - 修正後に flutter_analysis.py を実行すれば、発生マップ縦軸が正しい風速になる。
"""

import argparse
import json
import math
import os
import re
import shutil
import sys

import numpy as np
import pandas as pd

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

VOLT_COL = "差圧電圧[mV]"


def calc_windspeed(mv, offset_mV, a, b, rho, water_dens):
    """make_windspeed.py / flutter_run_experiment.m と同じ風速換算式。"""
    h = (mv - offset_mV) * a + b
    if h <= 0 or rho <= 0:
        return 0.0
    return math.sqrt(2.0 * water_dens * h * 9.80665 / rho)


def summary_mean_mv(exp_dir):
    """case フォルダ内の volt_summary.csv（Pdata + Mdata）の差圧電圧平均を返す。

    Returns (mean_mv, n_valid) または見つからなければ (None, 0)。
    """
    vals = []
    for fname in sorted(os.listdir(exp_dir)):
        if fname.endswith("_volt_summary.csv"):
            df = pd.read_csv(os.path.join(exp_dir, fname), encoding="utf-8-sig")
            if VOLT_COL in df.columns:
                vals.append(pd.to_numeric(df[VOLT_COL], errors="coerce"))
    if not vals:
        return None, 0
    series = pd.concat(vals, ignore_index=True)
    n_valid = int(series.notna().sum())
    if n_valid == 0:
        return None, 0
    return float(np.nanmean(series.values)), n_valid


def fix_one_condition(exp_dir, dry_run=False, backup=True):
    """1つの case フォルダの experiment_log.json を修正する。"""
    logs = sorted(f for f in os.listdir(exp_dir)
                  if f.endswith("_experiment_log.json"))
    if not logs:
        print(f"  [スキップ] experiment_log.json が見つかりません: {exp_dir}")
        return False

    log_path = os.path.join(exp_dir, logs[-1])
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)

    # ofst フォルダなど代表風速を持たないものはスキップ
    if "rep_windspeed_mV" not in log:
        print(f"  [スキップ] rep_windspeed_mV なし（ofst等）: {os.path.basename(exp_dir)}")
        return False

    mean_mv, n_valid = summary_mean_mv(exp_dir)
    if mean_mv is None:
        print(f"  [スキップ] volt_summary.csv が読めません: {os.path.basename(exp_dir)}")
        return False

    try:
        offset_mV  = log["volt_offset_mV"]
        a          = log["calib_a"]
        b          = log["calib_b"]
        rho        = log["rho_kg_m3"]
        water_dens = log["water_density"]
    except KeyError as e:
        print(f"  [エラー] 必要なキーがありません ({e}): {log_path}")
        return False

    new_mv = -mean_mv                  # 不具合1: 符号反転
    new_U  = calc_windspeed(new_mv, offset_mV, a, b, rho, water_dens)

    old_mv = log["rep_windspeed_mV"]
    old_U  = log.get("rep_windspeed_U", 0.0)

    cond_name = os.path.basename(exp_dir)
    print(f"  {cond_name}:  (volt_summary 平均, n={n_valid})")
    print(f"    summary 平均 mV : {mean_mv:+.2f} mV  →  符号反転  {new_mv:+.2f} mV")
    print(f"    rep_windspeed_mV: {old_mv:+.2f} mV  →  {new_mv:+.2f} mV")
    print(f"    rep_windspeed_U : {old_U:.4f} m/s  →  {new_U:.4f} m/s")

    if dry_run:
        print("    [DRY RUN] 書き込みをスキップしました")
        return True

    if backup:
        bak = log_path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(log_path, bak)
            print(f"    バックアップ: {os.path.basename(bak)}")

    log["rep_windspeed_mV"] = new_mv
    log["rep_windspeed_U"]  = new_U
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"    → {log_path} を更新しました")
    return True


def main():
    p = argparse.ArgumentParser(
        description="volt_summary 平均からの代表風速修正スクリプト（Windy）"
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--base_dir",
                     help="コンテナフォルダ（_c01 / _c02 ... を内包）")
    grp.add_argument("--exp_dir", help="単一条件フォルダ（_c01 など）")
    p.add_argument("--dry_run", action="store_true",
                   help="修正内容を表示するだけで書き込まない")
    p.add_argument("--no_backup", action="store_true",
                   help="experiment_log.json のバックアップを作成しない")
    args = p.parse_args()

    backup = not args.no_backup
    if args.dry_run:
        print("[DRY RUN モード] ファイルへの書き込みは行いません。\n")

    if args.exp_dir:
        exp_dir = args.exp_dir.rstrip("/\\")
        print(f"[修正対象] {exp_dir}\n")
        fix_one_condition(exp_dir, dry_run=args.dry_run, backup=backup)
        print("\n完了")
        return

    base_dir = args.base_dir.rstrip("/\\")
    cond_dirs = sorted(
        os.path.join(base_dir, d)
        for d in os.listdir(base_dir)
        if re.search(r"_c\d+$", d) and os.path.isdir(os.path.join(base_dir, d))
    )
    if not cond_dirs:
        print(f"[エラー] 条件フォルダ（_c01 など）が見つかりません: {base_dir}",
              file=sys.stderr)
        sys.exit(1)

    print(f"[一括修正] {len(cond_dirs)} 条件を処理します。\n")
    n_fixed = 0
    for cond_dir in cond_dirs:
        if fix_one_condition(cond_dir, dry_run=args.dry_run, backup=backup):
            n_fixed += 1
        print()

    print(f"完了: {n_fixed} / {len(cond_dirs)} 条件を修正しました。")
    if args.dry_run:
        print("※ DRY RUN のため実際のファイルは変更されていません。")
        print("   問題なければ --dry_run を外して再実行してください。")


if __name__ == "__main__":
    main()
