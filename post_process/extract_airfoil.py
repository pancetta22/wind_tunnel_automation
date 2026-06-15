#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# extract_airfoil.py  翼型輪郭 抽出スクリプト（Windy 新システム対応版）
#
# 実験フォルダ内の photo/ にある翼模型の写真から、緑マーカーで射影変換 →
# 迎角で回転 → 赤エッジ抽出 → 翼弦長で正規化、までを行って各迎角の
# 翼型輪郭（x/c, y/c）を取り出す。1迎角につき3枚撮った写真は輪郭を平均して
# 1本にまとめる。
#
# 従来の windtunnel_picture_analysis/extract_airfoil4.py の輪郭抽出部分
# （Gmarkers → warp → rotate → Redge → 正規化）を移植したもの。modPARSEC
# フィットは行わない（輪郭抽出まで）。
#
# 【実行方法】
#   cd <実験フォルダ>          ← photo/ が存在する場所
#   python <repo>/post_process/extract_airfoil.py
#     - 既定では ./photo を読み、./airfoil に結果を出力する
#     - run_postprocess.m から y/n で呼び出すこともできる
#
#   python extract_airfoil.py --photo_dir D:/.../photo --out D:/.../airfoil --debug
#
# 【入力】
#   photo/<label><shot>.JPG   label=0deg / p<N>deg / m<N>deg, shot=1..3
#                             （従来式の <label>.JPG = 1枚 も処理可）
#   naca0012.csv              参照翼型（本スクリプトと同じフォルダに同梱）
#
# 【出力（既定 ./airfoil/）】
#   contour/<label>.csv       3枚平均した正規化輪郭（x/c, y/c, 閉曲線）
#   contour/<label>.png       平均輪郭 + NACA0012 重ね描き
#   shots/<label><shot>.csv   各写真の正規化輪郭（ばらつき確認用）
#   overlay_all.png           全迎角の平均輪郭を重ねた図
#   debug/ (--debug 時)       緑マスク・射影・回転・エッジの中間画像（HSV調整用）

from __future__ import annotations

import argparse
import os
import re
import sys
import glob
import math
import traceback

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする安全網。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

try:
    import cv2
except ImportError:
    print("[エラー] OpenCV(cv2) が見つかりません。次でインストールしてください:\n"
          "        python -m pip install opencv-python-headless", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
#  幾何・スケール定数（従来 extract_airfoil4.py と同一）
# ============================================================
c        = 200            # 翼弦長 [mm 相当]
LE_x     = 25            # 前縁の x 位置
center_x = 50            # 回転中心の基準
scale    = 20            # 250*scale x 150*scale（5000x3000、5472x3648 を想定）
width, height = 250 * scale, 150 * scale

# 回転中心（前縁＋center_x, 高さ中央）
ROTATION_CENTER = (int((center_x + LE_x) * scale), int(height / 2))

# 迎角に加える回転補正 [deg]。従来コードは AoA+1 としていた（撮影系の取付け
# 角に対する補正）。新しい治具では 0 が適切なことが多いので --rotate-offset で
# 変更できるようにしてある（既定は従来挙動に合わせて 1.0）。
ROTATE_OFFSET_DEG = 1.0

# 既定の HSV しきい値（control.csv の既定値と同一）。緑マーカーと赤エッジ。
DEFAULT_HSV = {
    "G_low_H": 45, "G_high_H": 90, "G_low_S": 50, "G_high_S": 255,
    "G_low_V": 45, "G_high_V": 255,
    "R1_low_H": 0,   "R1_high_H": 15,  "R2_low_H": 165, "R2_high_H": 179,
    "R1_low_S": 60,  "R1_high_S": 255, "R2_low_S": 60,  "R2_high_S": 255,
    "R1_low_V": 60,  "R1_high_V": 255, "R2_low_V": 60,  "R2_high_V": 255,
}

# 平均輪郭を作るときの共通 x/c ステーション（前後縁を密にするコサイン分布）
N_STATIONS = 200
X_STATIONS = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, N_STATIONS)))


# ============================================================
#  ファイル名 → 迎角ラベル
# ============================================================
LABEL_RE = re.compile(r"^((?:0|p\d+|m\d+)deg)(\d*)\.jpe?g$", re.IGNORECASE)


def parse_photo_name(fname: str):
    """photo のファイル名から (label, shot) を返す。対象外なら None。
    例: p1deg2.JPG -> ('p1deg', 2)、0deg.JPG -> ('0deg', 1)"""
    m = LABEL_RE.match(fname)
    if not m:
        return None
    label = m.group(1).lower()
    shot = int(m.group(2)) if m.group(2) else 1
    return label, shot


def aoa_from_label(label: str) -> float:
    """ラベル(0deg/pNdeg/mNdeg) -> 迎角[deg]"""
    if label.startswith("p"):
        return float(label[1:-3])
    if label.startswith("m"):
        return -float(label[1:-3])
    return 0.0


# ============================================================
#  画像処理（従来 extract_airfoil4.py の輪郭抽出を移植）
# ============================================================
def detect_green_markers(img, hsv) -> np.ndarray | None:
    """緑の4隅マーカーの重心を検出し、warp 用の順序で返す。
    返値: 4x2 float32（[TL, BL, TR, BR] 相当＝原点からの距離順）。失敗時 None。"""
    hsv_img = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    low = np.array([hsv["G_low_H"], hsv["G_low_S"], hsv["G_low_V"]])
    high = np.array([hsv["G_high_H"], hsv["G_high_S"], hsv["G_high_V"]])
    mask = cv2.inRange(hsv_img, low, high)
    masked = cv2.bitwise_and(img, img, mask=mask)

    gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
    contours, _ = cv2.findContours(gray, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centers = []
    for contour in contours:
        M = cv2.moments(contour)
        if M["m00"] != 0:
            cX = int(M["m10"] / M["m00"])
            cY = int(M["m01"] / M["m00"])
            centers.append([cX, cY, (cX**2 + cY**2) ** 0.5])

    # 近接点（<20px）を統合して4点に寄せる
    min_distance = 20
    if len(centers) > 4:
        i = 0
        while i < len(centers):
            j = i + 1
            while j < len(centers):
                d = np.hypot(centers[i][0] - centers[j][0], centers[i][1] - centers[j][1])
                if d < min_distance:
                    centers[i][0] = (centers[i][0] + centers[j][0]) // 2
                    centers[i][1] = (centers[i][1] + centers[j][1]) // 2
                    del centers[j]
                else:
                    j += 1
            i += 1

    # 中央寄りの点を除外し、四隅だけ残す
    h, w = img.shape[0], img.shape[1]
    i = 0
    while i < len(centers):
        if (h / 4 < centers[i][1] < h * 3 / 4) or (w / 5 < centers[i][0] < w * 4 / 5):
            del centers[i]
        else:
            i += 1

    if len(centers) != 4:
        print(f"  [警告] 緑マーカーが4点になりません（検出 {len(centers)} 点）")
        return None

    centers.sort(key=lambda x: x[2])   # 原点からの距離順
    pts = np.float32([[ce[0], ce[1]] for ce in centers])
    return pts, mask


def warp_image(img, corners) -> np.ndarray:
    """4隅マーカーで射影変換し、正規化サイズへ。"""
    pts1 = np.float32([corners[0], corners[2], corners[1], corners[3]])
    pts2 = np.float32([[0, 0], [width, 0], [0, height], [width, height]])
    M = cv2.getPerspectiveTransform(pts1, pts2)
    return cv2.warpPerspective(img, M, (width, height))


def rotate_image(warped, aoa, rotate_offset) -> np.ndarray:
    """迎角ぶん回転して翼弦を水平に戻す。"""
    M = cv2.getRotationMatrix2D(ROTATION_CENTER, aoa + rotate_offset, 1)
    return cv2.warpAffine(warped, M, (width, height))


def _smooth_closed_contour(pts, w=5):
    """閉曲線用ローリング平均スムージング（scipy不要）。"""
    n = len(pts)
    if n < 2 * w + 1:
        return pts
    padded = np.vstack([pts[-w:], pts, pts[:w]])
    kernel = np.ones(2 * w + 1) / (2 * w + 1)
    sx = np.convolve(padded[:, 0], kernel, mode="same")[w:w + n]
    sy = np.convolve(padded[:, 1], kernel, mode="same")[w:w + n]
    return np.column_stack([sx, sy])


def extract_red_edge(rotated, hsv) -> np.ndarray | None:
    """赤エッジを抽出し、回転画像座標系での輪郭点列(Nx2)を返す。失敗時 None。"""
    hsv_img = cv2.cvtColor(rotated, cv2.COLOR_BGR2HSV)
    low1 = np.array([hsv["R1_low_H"], hsv["R1_low_S"], hsv["R1_low_V"]])
    high1 = np.array([hsv["R1_high_H"], hsv["R1_high_S"], hsv["R1_high_V"]])
    low2 = np.array([hsv["R2_low_H"], hsv["R2_low_S"], hsv["R2_low_V"]])
    high2 = np.array([hsv["R2_high_H"], hsv["R2_high_S"], hsv["R2_high_V"]])
    mask = cv2.bitwise_or(cv2.inRange(hsv_img, low1, high1),
                          cv2.inRange(hsv_img, low2, high2))

    # sf=0.5 に縮小して形態学処理（後縁の細い領域を保持）
    sf = 0.5
    small = cv2.resize(mask, None, fx=sf, fy=sf, interpolation=cv2.INTER_NEAREST)
    small = cv2.morphologyEx(small, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
    # 縦方向クローズ（最大厚み付近の大ギャップを埋める）
    filled = cv2.morphologyEx(small, cv2.MORPH_CLOSE, np.ones((301, 1), np.uint8), iterations=2)
    filled = cv2.morphologyEx(filled, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)

    contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        print("  [警告] 赤エッジの輪郭が見つかりません")
        return None, mask

    largest = max(contours, key=cv2.contourArea)
    contour_pts = largest.reshape(-1, 2).astype(np.float64) / sf

    step = max(1, len(contour_pts) // 3000)
    contour_pts = contour_pts[::step]
    contour_pts = _smooth_closed_contour(contour_pts, w=5)
    return contour_pts, mask


def normalize_contour(contour_pts) -> np.ndarray | None:
    """回転画像座標 -> 翼弦正規化 (x/c, y/c)。翼型領域外を除去して返す。"""
    data = contour_pts.astype(float).copy()
    data[:, 0] = (data[:, 0] - LE_x * scale)
    data[:, 1] = (data[:, 1] - height / 2)
    data[:, 1] = -data[:, 1]
    data[:, :] = data[:, :] / (c * scale)

    valid = (data[:, 0] >= -0.05) & (data[:, 0] <= 1.05) & \
            (data[:, 1] >= -0.2) & (data[:, 1] <= 0.2)
    data = data[valid]
    if len(data) == 0:
        return None
    return data


# ============================================================
#  3枚平均（上下面を共通 x ステーションに再標本化して平均）
# ============================================================
def _split_upper_lower(contour):
    """正規化した閉輪郭を上面・下面に分け、共通 x ステーション上の
    (yu, yl) を返す（その輪郭が覆わない x は NaN）。"""
    pts = contour[:, :2]
    if np.array_equal(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 5:
        return None

    i_le = int(np.argmin(pts[:, 0]))
    rolled = np.roll(pts, -i_le, axis=0)          # 前縁を先頭に
    i_te = int(np.argmax(rolled[:, 0]))
    arc1 = rolled[:i_te + 1]                       # 前縁→後縁
    arc2 = np.vstack([rolled[i_te:], rolled[:1]])  # 後縁→前縁（折返し）

    def resample(arc):
        x, y = arc[:, 0], arc[:, 1]
        order = np.argsort(x)
        xs_, ys_ = x[order], y[order]
        xs_u, idx = np.unique(xs_, return_index=True)
        ys_u = ys_[idx]
        if len(xs_u) < 2:
            return np.full_like(X_STATIONS, np.nan)
        out = np.interp(X_STATIONS, xs_u, ys_u, left=np.nan, right=np.nan)
        out[(X_STATIONS < xs_u[0]) | (X_STATIONS > xs_u[-1])] = np.nan
        return out

    y_a, y_b = resample(arc1), resample(arc2)
    # 平均 y の大きい方を上面とする
    if np.nanmean(y_a) >= np.nanmean(y_b):
        return y_a, y_b
    return y_b, y_a


def average_contours(contours):
    """複数の正規化輪郭を上下面で平均し、(closed_contour, yu, yl) を返す。"""
    uppers, lowers = [], []
    for ct in contours:
        res = _split_upper_lower(ct)
        if res is None:
            continue
        uppers.append(res[0])
        lowers.append(res[1])
    if not uppers:
        return None

    with np.errstate(invalid="ignore"):
        yu = np.nanmean(np.vstack(uppers), axis=0)
        yl = np.nanmean(np.vstack(lowers), axis=0)

    # 上面(後縁→前縁) + 下面(前縁→後縁) で閉曲線を構成、NaN は除外
    up = np.column_stack([X_STATIONS[::-1], yu[::-1]])
    lo = np.column_stack([X_STATIONS, yl])
    closed = np.vstack([up, lo])
    closed = closed[~np.isnan(closed[:, 1])]
    if len(closed) >= 1:
        closed = np.vstack([closed, closed[0]])   # 閉じる
    return closed, yu, yl


# ============================================================
#  1枚処理
# ============================================================
def process_shot(path, aoa, hsv, rotate_offset, debug_dir=None, tag=""):
    """1枚の写真から正規化輪郭を返す。失敗時 None。"""
    img = cv2.imread(path)
    if img is None:
        print(f"  [警告] 画像を読めません: {path}")
        return None

    gm = detect_green_markers(img, hsv)
    if gm is None:
        return None
    corners, gmask = gm

    warped = warp_image(img, corners)
    rotated = rotate_image(warped, aoa, rotate_offset)

    red = extract_red_edge(rotated, hsv)
    if red is None or red[0] is None:
        return None
    contour_pts, rmask = red

    norm = normalize_contour(contour_pts)

    if debug_dir is not None:
        os.makedirs(debug_dir, exist_ok=True)
        cv2.imwrite(os.path.join(debug_dir, f"{tag}_1green.png"), gmask)
        cv2.imwrite(os.path.join(debug_dir, f"{tag}_2warp.png"), warped)
        cv2.imwrite(os.path.join(debug_dir, f"{tag}_3rotate.png"), rotated)
        cv2.imwrite(os.path.join(debug_dir, f"{tag}_4redmask.png"), rmask)
    return norm


# ============================================================
#  HSV しきい値の読み込み（任意の override ファイル）
# ============================================================
def load_hsv_overrides(photo_dir, exp_dir):
    """airfoil_control.csv があれば label -> HSV辞書 を返す（無ければ空）。
    列は control.csv と同じ（name, ... のしきい値）。"""
    import csv
    overrides = {}
    for cand in (os.path.join(photo_dir, "airfoil_control.csv"),
                 os.path.join(exp_dir, "airfoil_control.csv")):
        if not os.path.isfile(cand):
            continue
        try:
            with open(cand, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    name = (row.get("name") or "").strip().lower()
                    if not name:
                        continue
                    hsv = dict(DEFAULT_HSV)
                    for k in DEFAULT_HSV:
                        if row.get(k):
                            hsv[k] = int(float(row[k]))
                    overrides[name] = hsv
            print(f"[HSV] override を読み込み: {cand}（{len(overrides)} 件）")
        except (OSError, ValueError, KeyError) as e:
            print(f"[HSV] override の読み込みに失敗（既定値を使用）: {e}")
        break
    return overrides


# ============================================================
#  参照翼型・プロット
# ============================================================
def load_reference():
    ref_path = os.path.join(SCRIPT_DIR, "naca0012.csv")
    if not os.path.isfile(ref_path):
        return None
    import pandas as pd
    ref = np.array(pd.read_csv(ref_path, header=None)).T
    return ref


def plot_contour(out_png, contour, ref, title, n_shots):
    plt.figure(figsize=(10, 3))
    if ref is not None:
        plt.plot(ref[0, :], ref[1, :], linestyle="--", linewidth=1,
                 color="grey", label="NACA0012")
    plt.plot(contour[:, 0], contour[:, 1], linewidth=1.4, color="red",
             label=f"measured (avg of {n_shots})")
    plt.axis("equal")
    plt.xlabel("x/c"); plt.ylabel("y/c")
    plt.grid(True)
    plt.ylim(-0.15, 0.15); plt.xlim(-0.05, 1.05)
    plt.title(title)
    plt.legend(loc="upper right", fontsize=9)
    plt.savefig(out_png, bbox_inches="tight")
    plt.close()


# ============================================================
#  メイン
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="翼型輪郭 抽出（Windy）")
    parser.add_argument("--photo_dir", default=None,
                        help="写真フォルダ（既定: ./photo）")
    parser.add_argument("--out", default=None,
                        help="出力フォルダ（既定: ./airfoil）")
    parser.add_argument("--rotate-offset", type=float, default=ROTATE_OFFSET_DEG,
                        help=f"迎角への回転補正[deg]（既定 {ROTATE_OFFSET_DEG}）")
    parser.add_argument("--debug", action="store_true",
                        help="中間画像（緑マスク・射影・回転・赤マスク）を出力")
    args = parser.parse_args()

    exp_dir = os.getcwd()
    photo_dir = args.photo_dir or os.path.join(exp_dir, "photo")
    out_dir = args.out or os.path.join(exp_dir, "airfoil")

    if not os.path.isdir(photo_dir):
        print(f"[エラー] 写真フォルダがありません: {photo_dir}", file=sys.stderr)
        return 1

    # photo/ を迎角ラベルごとにまとめる
    groups: dict[str, list] = {}
    for path in sorted(glob.glob(os.path.join(photo_dir, "*.JPG")) +
                       glob.glob(os.path.join(photo_dir, "*.jpg")) +
                       glob.glob(os.path.join(photo_dir, "*.jpeg"))):
        parsed = parse_photo_name(os.path.basename(path))
        if parsed is None:
            continue
        label, shot = parsed
        groups.setdefault(label, []).append((shot, path))
    if not groups:
        print(f"[エラー] 解析対象の写真がありません（0deg*.JPG / p1deg*.JPG ...）: {photo_dir}",
              file=sys.stderr)
        return 1

    contour_dir = os.path.join(out_dir, "contour")
    shots_dir = os.path.join(out_dir, "shots")
    debug_dir = os.path.join(out_dir, "debug") if args.debug else None
    os.makedirs(contour_dir, exist_ok=True)
    os.makedirs(shots_dir, exist_ok=True)

    hsv_overrides = load_hsv_overrides(photo_dir, exp_dir)
    ref = load_reference()

    # 迎角順（負→正）に並べる
    def sort_key(lbl):
        return aoa_from_label(lbl)
    labels = sorted(groups.keys(), key=sort_key)

    summary = []
    overlay = []   # (aoa, contour) for combined plot
    for label in labels:
        aoa = aoa_from_label(label)
        hsv = hsv_overrides.get(label, DEFAULT_HSV)
        shots = sorted(groups[label])
        print(f"\n=== {label} (AoA={aoa:+.0f}°, {len(shots)} 枚) ===")

        norms = []
        for shot, path in shots:
            tag = f"{label}{shot}"
            try:
                norm = process_shot(path, aoa, hsv, args.rotate_offset,
                                    debug_dir=debug_dir, tag=tag)
            except Exception:
                traceback.print_exc()
                norm = None
            if norm is None:
                print(f"  [スキップ] {os.path.basename(path)} の輪郭抽出に失敗")
                continue
            np.savetxt(os.path.join(shots_dir, f"{tag}.csv"), norm, delimiter=",")
            norms.append(norm)
            print(f"  [OK] {os.path.basename(path)}: {len(norm)} 点")

        if not norms:
            print(f"  [警告] {label}: 有効な輪郭が無く、平均を作れません")
            summary.append((label, aoa, 0))
            continue

        avg = average_contours(norms)
        if avg is None:
            print(f"  [警告] {label}: 平均化に失敗")
            summary.append((label, aoa, len(norms)))
            continue
        closed, yu, yl = avg

        csv_path = os.path.join(contour_dir, f"{label}.csv")
        np.savetxt(csv_path, closed, delimiter=",", header="x/c,y/c", comments="")
        png_path = os.path.join(contour_dir, f"{label}.png")
        plot_contour(png_path, closed, ref, f"AoA = {aoa:+.0f} deg", len(norms))
        # 上下面の厚み分布も保存（解析用）
        prof = np.column_stack([X_STATIONS, yu, yl])
        np.savetxt(os.path.join(contour_dir, f"{label}_profile.csv"), prof,
                   delimiter=",", header="x/c,y_upper,y_lower", comments="")
        print(f"  [保存] {csv_path}")
        summary.append((label, aoa, len(norms)))
        overlay.append((aoa, closed))

    # 全迎角の重ね描き
    if overlay:
        plt.figure(figsize=(11, 4))
        if ref is not None:
            plt.plot(ref[0, :], ref[1, :], "--", linewidth=1, color="grey",
                     label="NACA0012")
        cmap = plt.get_cmap("coolwarm")
        aoas = [a for a, _ in overlay]
        amin, amax = min(aoas), max(aoas)
        span = (amax - amin) or 1.0
        for aoa, ct in overlay:
            col = cmap((aoa - amin) / span)
            plt.plot(ct[:, 0], ct[:, 1], linewidth=0.8, color=col)
        plt.axis("equal"); plt.grid(True)
        plt.xlabel("x/c"); plt.ylabel("y/c")
        plt.ylim(-0.15, 0.15); plt.xlim(-0.05, 1.05)
        plt.title(f"翼型輪郭 全迎角 ({amin:+.0f}° 〜 {amax:+.0f}°)")
        sm = plt.cm.ScalarMappable(cmap=cmap,
                                   norm=plt.Normalize(vmin=amin, vmax=amax))
        plt.colorbar(sm, ax=plt.gca(), label="AoA [deg]")
        plt.savefig(os.path.join(out_dir, "overlay_all.png"), bbox_inches="tight")
        plt.close()

    print("\n==== 完了 ====")
    n_ok = sum(1 for _, _, n in summary if n > 0)
    print(f"  {n_ok}/{len(summary)} 迎角の輪郭を抽出しました。出力: {out_dir}")
    for label, aoa, n in summary:
        mark = "OK " if n > 0 else "NG "
        print(f"   {mark} {label:8s} AoA={aoa:+4.0f}°  使用 {n} 枚")
    return 0


if __name__ == "__main__":
    sys.exit(main())
