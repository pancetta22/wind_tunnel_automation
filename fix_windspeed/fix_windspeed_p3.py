#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_windspeed_p3.py
  代表風速を「Pdata 迎角3°の1点」から再計算し、あわせて volt_summary.csv の
  差圧電圧[mV]列全体の符号反転（プラグ逆挿しによる極性反転の補正）を行う修正スクリプト。

260624_flutter で発生した2つの不具合に対応する：

  不具合1: 各caseの冒頭で計測する代表電圧（experiment_log.json の
           rep_windspeed_mV）にバッファ残留データが混入し不適切
           （例: -0.38 mV のような桁違いの値）。
           → 各caseの <date>_Pdata_volt_summary.csv から「迎角=3」の行の
             差圧電圧[mV]を代表値として採用する（符号反転あり）。

  不具合2: 差圧センサ（デジボル）のプラグを逆に刺したため、差圧電圧 [mV] が
           全計測点にわたって逆符号で記録された。
           → volt_summary.csv（Pdata / Mdata）の 差圧電圧[mV] 列を全行
             符号反転する。これにより flutter_analysis.py が計測点ごとに
             再計算する mean_U（St数・力係数・reduced velocity 等に使用）
             も正しい正の値になる。

いずれも experiment_log.json / volt_summary.csv を直接書き換える（.bak バックアップ
を作成）。ofst フォルダは rep_windspeed_mV を持たないため代表値修正の対象外だが、
volt_summary.csv があれば同様に符号反転する（一貫性のため。post_process 側では
参照されていないため出力への影響はない）。

風速換算式は make_windspeed.py / flutter_run_experiment.m と同一。

【使い方】
  # コンテナフォルダ（_ofst / _c01 / _c02 ... を内包）を指定して一括修正
  python fix_windspeed_p3.py --base_dir "C:/.../WindyData/260624_flutter_windfix"

  # 1条件だけ修正
  python fix_windspeed_p3.py --exp_dir "C:/.../260624_flutter_windfix_c01"

  # 確認のみ（書き込まない）
  python fix_windspeed_p3.py --base_dir "..." --dry_run

【注意】
  - 上書き前に experiment_log.json / volt_summary.csv を *.bak としてバックアップ
    する（--no_backup で無効化）。.bak が既に存在する場合はバックアップを
    スキップする（上書きしない）。
  - 修正後に flutter_analysis.py を実行すれば、代表風速・mean_U・St数・
    力係数・フラッター発生マップがすべて正しい風速で再計算される。
"""

import argparse
import json
import math
import os
import re
import shutil
import sys

import pandas as pd

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

VOLT_COL = "差圧電圧[mV]"
ANGLE_COL = "迎角"
P3_ANGLE = 3


def calc_windspeed(mv, offset_mV, a, b, rho, water_dens):
    """make_windspeed.py / flutter_run_experiment.m と同じ風速換算式。"""
    h = (mv - offset_mV) * a + b
    if h <= 0 or rho <= 0:
        return 0.0
    return math.sqrt(2.0 * water_dens * h * 9.80665 / rho)


def find_pdata_volt_summary(exp_dir):
    """条件フォルダ直下の <date>_Pdata_volt_summary.csv のパスを返す（無ければ None）。"""
    for fname in sorted(os.listdir(exp_dir)):
        if fname.endswith("_Pdata_volt_summary.csv"):
            return os.path.join(exp_dir, fname)
    return None


def find_p3_mv(pdata_path):
    """Pdata volt_summary.csv 内の「迎角=3」の行の差圧電圧[mV]を返す。

    見つからなければ None。複数行ヒットした場合は最初の行を使い警告する。
    """
    df = pd.read_csv(pdata_path, encoding="utf-8-sig")
    if ANGLE_COL not in df.columns or VOLT_COL not in df.columns:
        return None
    rows = df[pd.to_numeric(df[ANGLE_COL], errors="coerce") == P3_ANGLE]
    if len(rows) == 0:
        return None
    if len(rows) > 1:
        print(f"  [警告] 迎角={P3_ANGLE} の行が複数あります（先頭を使用）: {pdata_path}")
    return float(rows.iloc[0][VOLT_COL])


def backup_file(path, backup):
    if not backup:
        return
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"    バックアップ: {os.path.basename(bak)}")


def flip_volt_summary_sign(exp_dir, dry_run=False, backup=True):
    """条件フォルダ内の *_volt_summary.csv すべての差圧電圧[mV]列の符号を反転する。

    Returns 反転したファイル数。
    """
    n_flipped = 0
    for fname in sorted(os.listdir(exp_dir)):
        if not fname.endswith("_volt_summary.csv"):
            continue
        fpath = os.path.join(exp_dir, fname)
        df = pd.read_csv(fpath, encoding="utf-8-sig")
        if VOLT_COL not in df.columns:
            print(f"  [スキップ] {VOLT_COL} 列がありません: {fname}")
            continue

        vals = pd.to_numeric(df[VOLT_COL], errors="coerce")
        print(f"  {fname}: 差圧電圧[mV] 符号反転（{vals.notna().sum()} 行）")

        if dry_run:
            n_flipped += 1
            continue

        backup_file(fpath, backup)
        df[VOLT_COL] = -vals
        df.to_csv(fpath, index=False, encoding="utf-8-sig")
        n_flipped += 1
    return n_flipped


def fix_one_condition(exp_dir, dry_run=False, backup=True):
    """1つの条件フォルダを修正する：
       (1) rep_windspeed_mV/U を Pdata 迎角3°の値（符号反転後）から再計算
           （volt_summary.csv がまだ反転されていない生値のうちに読む）
       (2) volt_summary.csv 全行の差圧電圧[mV]符号反転
    """
    cond_name = os.path.basename(exp_dir)

    # ---- (1) 代表風速の修正（rep_windspeed_mV を持つフォルダのみ）----
    # volt_summary.csv を符号反転する「前」に生値のまま P_3 の値を読む。
    logs = sorted(f for f in os.listdir(exp_dir)
                  if f.endswith("_experiment_log.json"))
    if not logs:
        print(f"  [スキップ] experiment_log.json が見つかりません: {cond_name}")
        has_rep = False
    else:
        log_path = os.path.join(exp_dir, logs[-1])
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
        has_rep = "rep_windspeed_mV" in log

    if logs and not has_rep:
        print(f"  [スキップ] rep_windspeed_mV なし（ofst等）: {cond_name}")

    if logs and has_rep:
        pdata_path = find_pdata_volt_summary(exp_dir)
        if pdata_path is None:
            print(f"  [エラー] Pdata_volt_summary.csv が見つかりません: {cond_name}")
        else:
            p3_mv = find_p3_mv(pdata_path)
            if p3_mv is None:
                print(f"  [エラー] 迎角={P3_ANGLE} の行が見つかりません: {pdata_path}")
            else:
                _apply_rep_windspeed_fix(exp_dir, log_path, log, p3_mv,
                                          dry_run=dry_run, backup=backup)

    # ---- (2) volt_summary.csv の符号反転（全フォルダ共通。ofst にも適用）----
    n_flipped = flip_volt_summary_sign(exp_dir, dry_run=dry_run, backup=backup)

    return (logs and has_rep) or n_flipped > 0


def _apply_rep_windspeed_fix(exp_dir, log_path, log, p3_mv, dry_run, backup):
    cond_name = os.path.basename(exp_dir)

    try:
        offset_mV  = log["volt_offset_mV"]
        a          = log["calib_a"]
        b          = log["calib_b"]
        rho        = log["rho_kg_m3"]
        water_dens = log["water_density"]
    except KeyError as e:
        print(f"  [エラー] 必要なキーがありません ({e}): {log_path}")
        return False

    new_mv = -p3_mv  # 不具合2と同じ符号反転
    new_U  = calc_windspeed(new_mv, offset_mV, a, b, rho, water_dens)

    old_mv = log["rep_windspeed_mV"]
    old_U  = log.get("rep_windspeed_U", 0.0)

    print(f"  {cond_name}:  (Pdata 迎角={P3_ANGLE}° の1点を採用)")
    print(f"    P_3 生電圧      : {p3_mv:+.2f} mV  →  符号反転  {new_mv:+.2f} mV")
    print(f"    rep_windspeed_mV: {old_mv:+.2f} mV  →  {new_mv:+.2f} mV")
    print(f"    rep_windspeed_U : {old_U:.4f} m/s  →  {new_U:.4f} m/s")

    if dry_run:
        print("    [DRY RUN] 書き込みをスキップしました")
        return True

    backup_file(log_path, backup)
    log["rep_windspeed_mV"] = new_mv
    log["rep_windspeed_U"]  = new_U
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"    → {log_path} を更新しました")
    return True


def main():
    p = argparse.ArgumentParser(
        description="Pdata 迎角3°の1点からの代表風速修正 + volt_summary 符号反転スクリプト（Windy）"
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--base_dir",
                     help="コンテナフォルダ（_ofst / _c01 / _c02 ... を内包）")
    grp.add_argument("--exp_dir", help="単一条件フォルダ（_c01 など）")
    p.add_argument("--dry_run", action="store_true",
                   help="修正内容を表示するだけで書き込まない")
    p.add_argument("--no_backup", action="store_true",
                   help="*.bak バックアップを作成しない")
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
    all_dirs = sorted(
        os.path.join(base_dir, d)
        for d in os.listdir(base_dir)
        if os.path.isdir(os.path.join(base_dir, d))
        and (re.search(r"_c\d+$", d) or re.search(r"_ofst$", d))
    )
    if not all_dirs:
        print(f"[エラー] 条件フォルダ（_c01 等）/ ofst フォルダが見つかりません: {base_dir}",
              file=sys.stderr)
        sys.exit(1)

    print(f"[一括修正] {len(all_dirs)} フォルダを処理します。\n")
    n_fixed = 0
    for cond_dir in all_dirs:
        if fix_one_condition(cond_dir, dry_run=args.dry_run, backup=backup):
            n_fixed += 1
        print()

    print(f"完了: {n_fixed} / {len(all_dirs)} フォルダを修正しました。")
    if args.dry_run:
        print("※ DRY RUN のため実際のファイルは変更されていません。")
        print("   問題なければ --dry_run を外して再実行してください。")


if __name__ == "__main__":
    main()
