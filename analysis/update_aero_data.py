#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_aero_data.py
"rigid" を名前に含む実験フォルダの post_process 出力（C_aero.csv）を
この考察フォルダの aero_data/ に同期し、研究室フォーマットの比較パワポを再生成する。

  rigid 実験を計測・後処理（calc_force.py）したあとに、これ1つを実行すれば
  aero_data への取り込みとパワポ更新が自動で行われる。

使い方:
    python update_aero_data.py [実験フォルダの親ディレクトリ ...]
      - 引数を省略すると DEFAULT_SOURCES を走査する
      - 例: python update_aero_data.py C:/Users/<name>/WindyData

仕様:
    - 探索元の直下フォルダのうち、名前に "rigid" を含み C_aero.csv を持つものが対象
    - 内容が変わったもの／新規のみコピー（idempotent）
    - 同期後に make_rigid_comparison_local.py を実行してパワポを更新
      （新フォルダは比較スクリプト側で自動検出され、表・全図に反映される）
"""

import os
import sys
import shutil
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AERO_DATA  = os.path.join(SCRIPT_DIR, "aero_data")
GEN_SCRIPT = os.path.join(SCRIPT_DIR, "make_rigid_comparison_local.py")

# 既定の探索元（環境に合わせて編集、または実行時に引数で指定）
DEFAULT_SOURCES = [
    "/Users/yuyaokamoto/Downloads/imamura_lab/windtunnel_force_measurement",
]


def _same(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def sync(sources):
    os.makedirs(AERO_DATA, exist_ok=True)
    copied = []
    for src in sources:
        if not os.path.isdir(src):
            print(f"[スキップ] 存在しないフォルダ: {src}")
            continue
        for sub in sorted(os.listdir(src)):
            if "rigid" not in sub.lower():
                continue
            ca = os.path.join(src, sub, "C_aero.csv")
            if not os.path.isfile(ca):
                continue
            dst_dir = os.path.join(AERO_DATA, sub)
            dst     = os.path.join(dst_dir, "C_aero.csv")
            if os.path.isfile(dst) and _same(ca, dst):
                continue   # 既に同一 → スキップ
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy(ca, dst)
            copied.append(sub)
    return copied


def main():
    sources = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_SOURCES
    print(f"[探索元] {', '.join(sources)}")
    copied = sync(sources)
    if copied:
        print(f"[同期] {len(copied)} 件をコピー/更新:")
        for c in copied:
            print(f"        + {c}")
    else:
        print("[同期] 新規・更新はありませんでした。")

    print("[再生成] 比較パワポを更新します...")
    r = subprocess.run([sys.executable, GEN_SCRIPT],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print(r.stdout.strip())
        print("[完了] パワポを更新しました。")
    else:
        print("[エラー] パワポ生成に失敗しました:")
        print(r.stderr[-600:])
        sys.exit(1)


if __name__ == "__main__":
    main()
