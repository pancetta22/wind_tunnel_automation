#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_aero_data.py
"rigid" を名前に含む実験フォルダの post_process 出力（C_aero.csv）を
この analysis フォルダの aero_data/ に同期し、研究室フォーマットの比較パワポを再生成する。

  rigid 実験を計測・後処理（calc_force.py）したあとに、これ1つを実行すれば
  aero_data への取り込みとパワポ更新が自動で行われる。

使い方:
    python update_aero_data.py [実験フォルダの親ディレクトリ ...]
      - 引数を省略すると config.json の output_dir を走査する（Windows/Mac 共通）
      - 例: python update_aero_data.py C:/Users/<name>/WindyData

仕様:
    - 探索元の直下フォルダのうち、名前に "rigid" を含み C_aero.csv を持つものが対象
    - 内容が変わったもの／新規のみコピー（idempotent）
    - 同期後に make_rigid_comparison_local.py を実行してパワポを更新
      （新フォルダは比較スクリプト側で自動検出され、表・全図に反映される）
"""

import os
import sys
import json
import shutil
import subprocess

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする安全網。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AERO_DATA  = os.path.join(SCRIPT_DIR, "aero_data")
GEN_SCRIPT = os.path.join(SCRIPT_DIR, "make_rigid_comparison_local.py")


def default_sources():
    """引数なしの場合の既定探索元：リポジトリルートの config.json の output_dir。
    config.json が無い・読めない場合は空リスト（メッセージのみ表示）。"""
    config_path = os.path.join(os.path.dirname(SCRIPT_DIR), "config.json")
    try:
        with open(config_path, encoding="utf-8") as f:
            output_dir = json.load(f).get("output_dir", "")
        if output_dir and os.path.isdir(output_dir):
            return [output_dir]
        if output_dir:
            print(f"[注意] config.json の output_dir が存在しません: {output_dir}")
    except (OSError, json.JSONDecodeError):
        print(f"[注意] config.json を読めませんでした: {config_path}")
    print("       探索元を引数で指定してください: python update_aero_data.py <親ディレクトリ>")
    return []


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
    sources = sys.argv[1:] if len(sys.argv) > 1 else default_sources()
    if sources:
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
