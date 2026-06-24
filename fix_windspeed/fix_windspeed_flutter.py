#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_windspeed.py  デジボル逆接続時の代表風速修正スクリプト

差圧センサのプラグを逆に刺してしまい、電圧が逆符号で記録された場合に使う。
rep_windspeed_mV の符号を反転して rep_windspeed_U を再計算し、
experiment_log.json を上書き保存する。

【使い方】
  # ベースフォルダ指定（c01, c02, ... を一括修正）
  python fix_windspeed.py --base_dir C:/WindyData/260620_flexible

  # 1条件だけ修正
  python fix_windspeed.py --exp_dir C:/WindyData/260620_flexible/260620_flexible_c01

【注意】
  - ofst フォルダは修正対象外（Pofst/Mofst は差圧電圧を風速換算しないため）
  - volt_summary.csv の差圧電圧[mV]列は参照値として残す（後処理には使わない）
  - 実行前に experiment_log.json を手動でバックアップしておくことを推奨
"""

import argparse
import json
import math
import os
import re
import sys


def calc_windspeed(rep_mv, offset_mV, a, b, rho, water_dens):
    """make_windspeed.py / flutter_run_experiment.m と同じ風速換算式。"""
    h = (rep_mv - offset_mV) * a + b
    if h <= 0 or rho <= 0:
        return 0.0
    return math.sqrt(2.0 * water_dens * h * 9.80665 / rho)


def fix_one_condition(exp_dir, dry_run=False):
    """1つの条件フォルダの experiment_log.json を修正する。"""

    # experiment_log.json を探す（複数あれば最新）
    logs = sorted(
        f for f in os.listdir(exp_dir)
        if f.endswith("_experiment_log.json")
    )
    if not logs:
        print(f"  [スキップ] experiment_log.json が見つかりません: {exp_dir}")
        return False

    log_path = os.path.join(exp_dir, logs[-1])

    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)

    # rep_windspeed_mV がないフォルダ（ofst フォルダなど）はスキップ
    if "rep_windspeed_mV" not in log:
        print(f"  [スキップ] rep_windspeed_mV フィールドなし: {os.path.basename(exp_dir)}")
        return False

    # 修正前の値を表示
    old_mv  = log["rep_windspeed_mV"]
    old_U   = log.get("rep_windspeed_U", 0.0)

    # 符号反転
    new_mv = -old_mv

    # 風速再計算（experiment_log に校正定数が記録されていることを前提）
    try:
        offset_mV  = log["volt_offset_mV"]
        a          = log["calib_a"]
        b          = log["calib_b"]
        rho        = log["rho_kg_m3"]
        water_dens = log["water_density"]
    except KeyError as e:
        print(f"  [エラー] experiment_log.json に必要なキーがありません ({e}): {log_path}")
        return False

    new_U = calc_windspeed(new_mv, offset_mV, a, b, rho, water_dens)

    cond_name = os.path.basename(exp_dir)
    print(f"  {cond_name}:")
    print(f"    rep_windspeed_mV : {old_mv:+.2f} mV  →  {new_mv:+.2f} mV")
    print(f"    rep_windspeed_U  : {old_U:.4f} m/s  →  {new_U:.4f} m/s")

    if dry_run:
        print(f"    [DRY RUN] 書き込みをスキップしました")
        return True

    # 上書き保存
    log["rep_windspeed_mV"] = new_mv
    log["rep_windspeed_U"]  = new_U

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    print(f"    → {log_path} を更新しました")
    return True


def main():
    p = argparse.ArgumentParser(
        description="デジボル逆接続時の代表風速修正スクリプト（Windy）"
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--base_dir",
                     help="実験ベースフォルダ（_c01 / _c02 ... を一括修正）")
    grp.add_argument("--exp_dir",
                     help="単一条件フォルダ（_c01 など）")
    p.add_argument("--dry_run", action="store_true",
                   help="修正内容を表示するだけで書き込まない（確認用）")
    args = p.parse_args()

    if args.dry_run:
        print("[DRY RUN モード] ファイルへの書き込みは行いません。\n")

    # ---- 単一条件モード ----
    if args.exp_dir:
        exp_dir = args.exp_dir.rstrip("/\\")
        print(f"[修正対象] {exp_dir}\n")
        fix_one_condition(exp_dir, dry_run=args.dry_run)
        print("\n完了")
        return

    # ---- 一括処理モード ----
    base_dir  = args.base_dir.rstrip("/\\")
    base_name = os.path.basename(base_dir)

    # base_dir の中にある c0N フォルダを列挙（ofst は除外）
    cond_dirs = sorted(
        os.path.join(base_dir, d)
        for d in os.listdir(base_dir)
        if re.match(rf"^{re.escape(base_name)}_c\d+$", d)
        and os.path.isdir(os.path.join(base_dir, d))
    )

    if not cond_dirs:
        print(f"[エラー] 条件フォルダ（_c01 など）が見つかりません: {base_dir}",
              file=sys.stderr)
        sys.exit(1)

    print(f"[一括修正] {len(cond_dirs)} 条件を処理します。\n")

    n_fixed = 0
    for cond_dir in cond_dirs:
        if fix_one_condition(cond_dir, dry_run=args.dry_run):
            n_fixed += 1
        print()

    print(f"完了: {n_fixed} / {len(cond_dirs)} 条件を修正しました。")
    if args.dry_run:
        print("※ DRY RUN のため実際のファイルは変更されていません。")
        print("   問題なければ --dry_run を外して再実行してください。")


if __name__ == "__main__":
    main()
