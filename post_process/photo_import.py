#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# photo_import.py  SDカードの実験写真を撮影manifestに従って取り込む
#
# DLNAライブ転送が使えないカメラ（LUMIX DC-G100D 等）向けの取り込み手段。
# 実験中は run_experiment が「シャッターのみ」で撮影し、撮影順・迎角ラベルを
#   picture/photo/_shot_manifest.csv
# に記録する。実験後にSDカードから写真をPCへコピーし、本スクリプトで
#   <label><shot>.JPG（例: 0deg1.JPG, p5deg1.JPG ...）
# にリネーム取り込みすると、従来どおり picture_analysis.py で輪郭抽出できる。
#
# 【対応づけの考え方】
#   カメラはファイル名を連番で付ける（撮影順＝ファイル名順）。
#   manifest の成功ショット(shutter_ok=1)を撮影順に並べ、SDフォルダのJPEGを
#   ファイル名順に並べて「最新 N 枚」を実験写真とみなし、先頭から割り当てる。
#   （SDに過去写真が残っていても、実験写真は最も新しい連番になる前提）
#   同じ迎角ラベルが複数回出ても、出現順に 1,2,3,... と採番するので衝突しない。
#
# 【使い方】
#   python photo_import.py --sd <SDからコピーした写真フォルダ> \
#       --manifest <実験>/picture/photo/_shot_manifest.csv \
#       --out      <実験>/picture/photo \
#       [--dry-run] [--move]
#
#   --dry-run : 実際には書かず、割り当て（どのファイル→どのラベル）だけ表示
#   --move    : コピーではなく移動する（既定はコピー）

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

JPEG_EXT = (".jpg", ".jpeg")


def load_manifest(path: str) -> list:
    """成功ショット(shutter_ok=1)のラベルを撮影順(seq昇順)で返す。"""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if str(r.get("shutter_ok", "")).strip() != "1":
                continue
            label = (r.get("label") or "").strip()
            if not label:
                continue
            try:
                seq = int(r.get("seq", ""))
            except (TypeError, ValueError):
                seq = len(rows) + 1
            rows.append((seq, label))
    rows.sort(key=lambda x: x[0])
    return [label for _seq, label in rows]


def collect_jpegs(sd_dir: str) -> list:
    """SDフォルダ配下のJPEGをファイル名順（=撮影順）で返す。"""
    files = []
    for root, _dirs, names in os.walk(sd_dir):
        for n in names:
            if n.lower().endswith(JPEG_EXT):
                files.append(os.path.join(root, n))
    files.sort(key=lambda p: os.path.basename(p).lower())
    return files


def main() -> int:
    ap = argparse.ArgumentParser(
        description="SDカードの実験写真を撮影manifestに従いラベル名へ取り込む")
    ap.add_argument("--sd", required=True,
                    help="SDカードからコピーした写真フォルダ")
    ap.add_argument("--manifest", required=True,
                    help="_shot_manifest.csv のパス")
    ap.add_argument("--out", required=True,
                    help="出力先フォルダ（picture/photo）")
    ap.add_argument("--move", action="store_true",
                    help="コピーでなく移動する")
    ap.add_argument("--dry-run", action="store_true",
                    help="実際には書かず割り当てだけ表示する")
    args = ap.parse_args()

    if not os.path.isfile(args.manifest):
        print(f"[エラー] manifest がありません: {args.manifest}", file=sys.stderr)
        return 1
    if not os.path.isdir(args.sd):
        print(f"[エラー] SD写真フォルダがありません: {args.sd}", file=sys.stderr)
        return 1

    labels = load_manifest(args.manifest)
    if not labels:
        print("[エラー] manifest に成功ショットがありません。", file=sys.stderr)
        return 1
    files = collect_jpegs(args.sd)
    if not files:
        print(f"[エラー] {args.sd} に JPEG が見つかりません。", file=sys.stderr)
        return 1

    n = len(labels)
    if len(files) < n:
        print(f"[警告] SDのJPEG {len(files)}枚 < 撮影成功 {n}枚。"
              "古い順に分かるところまで割り当てます（不足分はスキップ）。",
              file=sys.stderr)
        chosen = files
        labels = labels[:len(files)]
    else:
        chosen = files[-n:]   # 最新N枚＝実験中の撮影
        if len(files) > n:
            print(f"[情報] SDに{len(files)}枚。最新{n}枚を実験写真として使用します。")

    os.makedirs(args.out, exist_ok=True)

    # 同じラベルの出現順に shot 番号を採番（0deg1, 0deg2, ... 衝突しない）
    counter: dict = {}
    plan = []
    for src, label in zip(chosen, labels):
        counter[label] = counter.get(label, 0) + 1
        dst = os.path.join(args.out, f"{label}{counter[label]}.JPG")
        plan.append((src, dst))

    tag = " [DRY-RUN]" if args.dry_run else ""
    print(f"--- 割り当て（{len(plan)}枚）{tag} ---")
    for src, dst in plan:
        print(f"  {os.path.basename(src)}  ->  {os.path.basename(dst)}")

    if args.dry_run:
        print("[DRY-RUN] 問題なければ --dry-run を外して再実行してください。")
        return 0

    done = 0
    for src, dst in plan:
        try:
            if args.move:
                shutil.move(src, dst)
            else:
                shutil.copy2(src, dst)
            done += 1
        except OSError as e:
            print(f"  [失敗] {os.path.basename(src)}: {e}", file=sys.stderr)

    print(f"[完了] {done}/{len(plan)} 枚を {args.out} に取り込みました。")
    print("  → 続けて run_postprocess を実行すると翼型輪郭の抽出に進めます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
