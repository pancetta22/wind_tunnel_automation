#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
free_decay_analysis.py  自由減衰（pluck test）解析スクリプト【予備実験・自己完結】

無風で翼を弾いて放した自由減衰振動（Leptrino 6軸力覚センサ記録）から、翼構造の
静止時固有振動数 f_n と構造減衰比 ζ を同定する。将来のフラッター解析・第2層無次元数
（質量比・Scruton数・フラッター速度指数）の入力に使うためのパラメータ同定。

【物理】剛性拘束された模型を初期変位から放すと、ベース反力は減衰固有振動数 f_d で
振動し、包絡線は e^(−ζ·ω_n·t) で減衰する。減衰率 σ（包絡線の対数の傾き）と f_d から
    ω_d = 2π·f_d,  ω_n = √(σ² + ω_d²),  ζ = σ/ω_n,  f_n = ω_n / 2π
が求まる（ζ が小さいとき f_n ≈ f_d）。

【自己完結方針】予備実験を本番後処理（post_process/）から疎結合にするため、CSV読込・
リサンプリング・PSD・フィルタ等の小関数はこのファイル内にコピー実装する（乖離リスクの
小さい定型処理のみ）。実行は依存ライブラリ（numpy/scipy/pandas/matplotlib）を持つ任意の
Python でよい（例: post_process/.venv の python をインタプリタとして使うだけ。コード依存は無い）。

【計測手順】無風・迎角固定で、1本の記録窓（既定20〜30秒）の中で「弾く→3秒ほど待って
収束→また弾く」を5回程度繰り返す。本スクリプトが各pluckイベントを自動セグメント化し、
イベントごとに f_n・ζ を推定して平均±標準偏差を出す。

【使い方】
  # フォルダ内の全CSVをまとめて解析（6軸すべて）
  python free_decay_analysis.py C:/WindyData/prelim_pluck/260704 --out results

  # ファイル指定・特定軸・帯域指定
  python free_decay_analysis.py rec1.csv rec2.csv --signals Fy,Mz --fmin 1 --fmax 100
"""

import argparse
import glob
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import interpolate, signal
from scipy.optimize import curve_fit
import matplotlib
matplotlib.use("Agg")   # GUIなし環境でも動作
import matplotlib.pyplot as plt

# 図の日本語ラベル用に、環境にある日本語対応フォントを選ぶ（無ければ既定のまま）。
for _cand in ("Yu Gothic", "MS Gothic", "Meiryo", "Noto Sans CJK JP"):
    if any(_f.name == _cand for _f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _cand
        break
plt.rcParams["axes.unicode_minus"] = False

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


# ============================================================
#  定数
# ============================================================
FS_TARGET   = 1200.0    # リサンプリング後のサンプリング周波数 [Hz]
HEADER_ROWS = 4         # Leptrino CSV（CFSLGR）のヘッダ行数
COL_NAMES   = ["t", "Fx", "Fy", "Fz", "Mx", "My", "Mz"]
ALL_AXES    = ("Fx", "Fy", "Fz", "Mx", "My", "Mz")


# ============================================================
#  自己完結ユーティリティ（post_process/flutter_analysis.py の該当実装を踏襲）
# ============================================================
def load_csv(path):
    """Leptrino CSV（CP932・4行ヘッダ）を読んで DataFrame を返す。"""
    df = pd.read_csv(path, skiprows=HEADER_ROWS, names=COL_NAMES,
                     encoding="cp932", dtype=float)
    return df.dropna().reset_index(drop=True)


def resample_uniform(t, x, fs=FS_TARGET):
    """不均一タイムスタンプの時系列を均一グリッドへ cubic 補間する。

    x は 1D でも (n, m) の 2D（m成分まとめて）でも可。t が非単調・重複する点は
    最初の出現を残して除去する。除去後 4 点未満なら ValueError。
    """
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    keep = np.concatenate(([True], np.diff(t) > 0))
    if not keep.all():
        t = t[keep]
        x = x[keep]
    if len(t) < 4:
        raise ValueError(f"resample_uniform: 点数不足（{len(t)} 点）")
    t_u = np.arange(t[0], t[-1], 1.0 / fs)
    f = interpolate.interp1d(t, x, kind="cubic", bounds_error=False,
                             fill_value="extrapolate", axis=0)
    return t_u, f(t_u)


def highpass(x, cutoff_hz, fs=FS_TARGET, order=4):
    """ハイパスフィルタ（DCドリフト除去、ゼロ位相）。"""
    sos = signal.butter(order, cutoff_hz, fs=fs, btype="high", output="sos")
    return signal.sosfiltfilt(sos, x)


def bandpass(x, f_lo, f_hi, fs=FS_TARGET, order=4):
    """バンドパス（単一モード分離用、ゼロ位相）。短すぎる信号では原信号を返す。"""
    nyq = fs / 2.0
    f_lo = max(float(f_lo), 0.1)
    f_hi = min(float(f_hi), nyq * 0.99)
    if f_hi <= f_lo:
        return np.asarray(x, dtype=float)
    sos = signal.butter(order, [f_lo, f_hi], fs=fs, btype="band", output="sos")
    # sosfiltfilt はある程度の長さが必要。短ければフィルタせず返す。
    if len(x) <= 3 * (2 * order + 1):
        return np.asarray(x, dtype=float)
    try:
        return signal.sosfiltfilt(sos, x)
    except ValueError:
        return np.asarray(x, dtype=float)


def calc_psd(x, fs=FS_TARGET, nperseg=2048):
    """Welch 法で PSD を推定する。"""
    freqs, psd = signal.welch(x, fs=fs, nperseg=min(nperseg, len(x)))
    return freqs, psd


def dominant_freq(freqs, psd, fmin=1.0, fmax=500.0):
    """指定範囲内で PSD 最大の周波数を返す。範囲に点が無ければ NaN。"""
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return np.nan
    return float(freqs[mask][np.argmax(psd[mask])])


# ============================================================
#  補助
# ============================================================
def _r2(y, yhat):
    """決定係数 R²。"""
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan


def _envelope(x):
    """解析信号の絶対値（Hilbert 包絡線）。"""
    return np.abs(signal.hilbert(x))


def _smooth(x, fs, win_sec=0.05):
    """移動平均で包絡線を平滑化する（イベント検出の安定化用）。"""
    n = max(1, int(win_sec * fs))
    if n <= 1:
        return x
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def logdec_zeta(x_bp):
    """片側（正）ピークの対数減衰で ζ を返す（サニティ用。減衰=正の符号に統一）。

    連続する正ピーク振幅 A_n に対し ln(A_n) を n の1次で回帰し、傾き −δ から
    ζ = δ / √((2π)² + δ²) を返す（δ は1周期あたりの対数減衰量）。
    """
    peaks, _ = signal.find_peaks(x_bp)
    if peaks.size < 3:
        return np.nan
    amps = x_bp[peaks]
    amps = amps[amps > 0]
    if amps.size < 3:
        return np.nan
    n = np.arange(amps.size)
    coef = np.polyfit(n, np.log(amps), 1)
    delta = -coef[0]
    if not np.isfinite(delta) or delta <= 0:
        return np.nan
    return float(delta / np.sqrt((2 * np.pi) ** 2 + delta ** 2))


def _curve_fit_decay(t, x, sigma0, fd0):
    """減衰正弦 A·e^(−σt)·cos(2π·fd·t + φ) を非線形フィットし (R², σ, fd) を返す。"""
    t = np.asarray(t, dtype=float)
    x = np.asarray(x, dtype=float)
    if len(t) < 8:
        return np.nan, np.nan, np.nan
    t = t - t[0]
    A0 = float(np.max(np.abs(x))) or 1.0

    def model(tt, A, sig, fd, phi):
        return A * np.exp(-sig * tt) * np.cos(2 * np.pi * fd * tt + phi)

    try:
        popt, _ = curve_fit(model, t, x, p0=[A0, max(sigma0, 1e-3), fd0, 0.0],
                            maxfev=8000)
        xhat = model(t, *popt)
        return _r2(x, xhat), abs(float(popt[1])), abs(float(popt[2]))
    except Exception:
        return np.nan, np.nan, np.nan


# ============================================================
#  pluck イベントの自動セグメント化（1本の記録に複数 pluck）
# ============================================================
def segment_pluck_events(x, fs, min_gap_sec=0.8, peak_frac=0.15,
                         end_frac=0.05, snr_min=8.0):
    """HP後の1軸信号 x から、各pluckの減衰区間 (start, end) のリストを返す。

    平滑化包絡線の大きな極大を pluck開始とみなし、包絡線がそのピーク値×end_frac を
    下回るまで（or 次イベント直前）を1セグメントとして切り出す。

    偽検出（雑音のみの軸）を弾くため、ピーク高さのしきい値を
    max(最大×peak_frac, 雑音床×snr_min) とする（雑音床＝包絡線のメジアン）。
    実 pluck は雑音床の数十〜数百倍になるので通過し、雑音だけの軸では 1 件も通らない。
    """
    env = _smooth(_envelope(x), fs)
    if env.size == 0 or env.max() <= 0:
        return []
    noise_floor = float(np.median(env))
    height = max(env.max() * peak_frac, noise_floor * snr_min)
    raw, _ = signal.find_peaks(env, height=height,
                               distance=max(1, int(min_gap_sec * fs)))
    if raw.size == 0:
        return []   # 有意な pluck 無し（雑音のみの軸はここで空になる）

    # オンセット基準のデデュープ: 1回の減衰の途中に生じる副極大を弾き、
    # 物理的な pluck 1回＝1イベントにする。前イベント以降に包絡線が
    # リセット水準（雑音床の数倍）まで落ちてから、はじめて次イベントを認める。
    reset = max(noise_floor * 3.0, height * 0.2)
    peaks = []
    last = -1
    for pk in raw:
        if last < 0 or float(np.min(env[last:pk])) < reset:
            peaks.append(int(pk))
            last = int(pk)
    peaks = np.array(peaks)

    segments = []
    min_len = int(0.05 * fs)   # 最低50ms
    for i, pk in enumerate(peaks):
        pk_val = env[pk]
        end_lim = int(peaks[i + 1]) if i + 1 < len(peaks) else len(x)
        tail = env[pk:end_lim]
        below = np.where(tail < pk_val * end_frac)[0]
        end = pk + (int(below[0]) if below.size else len(tail))
        if end - pk >= min_len:
            segments.append((int(pk), int(end)))
    return segments


# ============================================================
#  1減衰セグメントの推定（1軸・最大 n_modes モード）
# ============================================================
def estimate_decay(x_seg, fs, fmin, fmax, n_modes, min_cycles=5.0, dom_ratio=8.0):
    """切り出し済み単一減衰 x_seg から、各卓越モードの (f_d, f_n, ζ, σ, R²...) を返す。

    偽モードを弾くため、(a) PSDピークが帯域メジアンの dom_ratio 倍以上卓越、
    (b) フィット窓が min_cycles 周期以上、を満たすモードのみ採用する。

    Returns
    -------
    list of dict（モードごと）。図用の中間生成物は "_" 始まりキーに退避。
    """
    results = []
    x_seg = np.asarray(x_seg, dtype=float)
    x_seg = x_seg - np.mean(x_seg)
    if len(x_seg) < 16:
        return results

    freqs, psd = calc_psd(x_seg, fs=fs)
    band = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(band):
        return results
    fb, pb = freqs[band], psd[band]
    if pb.max() <= 0:
        return results

    pb_median = float(np.median(pb)) or (pb.max() * 1e-12)
    prom = max(pb_median * 3.0, pb.max() * 1e-3)
    pk, _ = signal.find_peaks(pb, prominence=prom)
    if pk.size == 0:
        pk = np.array([int(np.argmax(pb))])
    # パワーの強い順に n_modes 個。周波数昇順で処理する。
    strongest = pk[np.argsort(pb[pk])[::-1]][:max(1, n_modes)]
    for mi, p in enumerate(sorted(strongest, key=lambda q: fb[q])):
        f0 = float(fb[p])
        if not np.isfinite(f0) or f0 <= 0:
            continue
        # (a) スペクトルの卓越度（帯域メジアン比）が低いピークは偽モードとして除外
        if pb[p] < pb_median * dom_ratio:
            continue

        xb = bandpass(x_seg, 0.6 * f0, 1.4 * f0, fs=fs)
        env = _envelope(xb)
        t = np.arange(len(xb)) / fs

        i0 = int(np.argmax(env))
        emax = float(env[i0])
        if emax <= 0:
            continue
        tail = env[i0:]
        below = np.where(tail < emax * 0.05)[0]
        i1 = i0 + (int(below[0]) if below.size else len(tail))
        if i1 - i0 < max(10, int(0.02 * fs)):
            continue

        seg_t = t[i0:i1]
        seg_env = env[i0:i1]
        good = seg_env > emax * 1e-3
        if int(good.sum()) < 5:
            continue
        st = seg_t[good]
        se = seg_env[good]

        # 包絡線の対数を直線回帰 → 傾き = −σ
        coef = np.polyfit(st - st[0], np.log(se), 1)
        sigma = float(-coef[0])
        r2_env = _r2(np.log(se), np.polyval(coef, st - st[0]))

        # 減衰固有振動数 f_d（バンドパス信号のPSDピークで確定）
        fbb, pbb = calc_psd(xb, fs=fs)
        f_d = dominant_freq(fbb, pbb, fmin=0.6 * f0, fmax=1.4 * f0)
        if not np.isfinite(f_d) or f_d <= 0:
            f_d = f0
        omega_d = 2 * np.pi * f_d

        # (b) フィット窓が短すぎる（周期数不足）モードは偽検出として除外
        n_cycles = float((st[-1] - st[0]) * f_d)
        if n_cycles < min_cycles:
            continue

        if sigma <= 0 or not np.isfinite(sigma):
            note = "減衰が検出できない/フィット不良"
            f_n = f_d
            zeta = np.nan
            r2_fit = sigma_fit = f_d_fit = np.nan
        else:
            omega_n = np.sqrt(sigma ** 2 + omega_d ** 2)
            zeta = sigma / omega_n
            f_n = omega_n / (2 * np.pi)
            note = "" if r2_env >= 0.9 else "包絡線が非直線（振幅依存減衰の可能性）"
            r2_fit, sigma_fit, f_d_fit = _curve_fit_decay(
                t[i0:i1], xb[i0:i1], sigma, f_d)

        results.append({
            "mode": mi,
            "f_d_Hz": f_d,
            "f_n_Hz": f_n,
            "zeta": zeta,
            "zeta_pct": zeta * 100 if np.isfinite(zeta) else np.nan,
            "sigma": sigma,
            "R2_env": r2_env,
            "R2_fit": r2_fit,
            "zeta_logdec": logdec_zeta(xb),
            "sigma_fit": sigma_fit,
            "f_d_fit_Hz": f_d_fit,
            "n_cycles": n_cycles,
            "amp0": emax,
            "note": note,
            # 図用（CSVには書き出さない）
            "_t": t, "_x": xb, "_env": env, "_fit": (i0, i1, coef),
            "_psd": (fbb, pbb), "_f0": f0,
        })
    return results


# ============================================================
#  1ファイルの処理
# ============================================================
def _aoa_from_name(fname):
    """ファイル名 ..._pluck_aoa+05_... から迎角[deg]を取り出す（無ければ NaN）。"""
    m = re.search(r"aoa([+-]?\d+)", os.path.basename(fname))
    return int(m.group(1)) if m else np.nan


def process_file(path, signals, args):
    """1つのpluck記録CSVを処理して (rows, per_axis) を返す。

    rows     : summary CSV 行の list（"_"始まりキーは含めない）
    per_axis : {axis: [result dict(...図用中間生成物つき), ...]}  図描画用
    """
    fname = os.path.basename(path)
    aoa = _aoa_from_name(path)
    df = load_csv(path)
    if len(df) < 100:
        warnings.warn(f"データ不足のためスキップ: {fname}（{len(df)} 行）")
        return [], {}

    t = df["t"].values
    X = df[list(ALL_AXES)].values          # (n, 6)
    try:
        t_u, X_u = resample_uniform(t, X)
    except ValueError as e:
        warnings.warn(f"リサンプリング失敗のためスキップ: {fname}（{e}）")
        return [], {}

    rows = []
    per_axis = {}
    for axis in signals:
        if axis not in ALL_AXES:
            warnings.warn(f"未知の軸をスキップ: {axis}")
            continue
        x = X_u[:, ALL_AXES.index(axis)]
        x_hp = highpass(x - np.mean(x), args.hp_cutoff)

        segments = segment_pluck_events(x_hp, FS_TARGET, snr_min=args.min_snr)
        axis_results = []
        for ev_idx, (s, e) in enumerate(segments):
            modes = estimate_decay(x_hp[s:e], FS_TARGET,
                                   args.fmin, args.fmax, args.n_modes,
                                   min_cycles=args.min_cycles)
            for m in modes:
                # セグメントの絶対時刻オフセット（図で全体波形に重ねるため）
                m["_seg"] = (s, e)
                m["_event"] = ev_idx
                axis_results.append(m)
                rows.append({
                    "file": fname, "aoa_deg": aoa, "axis": axis,
                    "event": ev_idx,
                    **{k: v for k, v in m.items() if not k.startswith("_")},
                })
        if axis_results:
            per_axis[axis] = {"results": axis_results, "signal": x_hp}

    return rows, per_axis


# ============================================================
#  図（1ファイル）
# ============================================================
def plot_file(path, per_axis, out_dir, args):
    """1ファイルにつき、軸ごとに [全体波形+イベント区間]／[ln(env)フィット]／[PSD] を並べる。"""
    axes_with_data = [a for a in per_axis if per_axis[a]["results"]]
    if not axes_with_data:
        return
    fname = os.path.basename(path)
    base = os.path.splitext(fname)[0]

    n = len(axes_with_data)
    fig, axs = plt.subplots(n, 3, figsize=(16, 3.2 * n), squeeze=False)
    fig.suptitle(f"自由減衰解析: {fname}", fontsize=13)

    for r, axis in enumerate(axes_with_data):
        x_hp = per_axis[axis]["signal"]
        results = per_axis[axis]["results"]
        t_full = np.arange(len(x_hp)) / FS_TARGET

        # 代表イベント = フィット良好（R²≥0.8）な中で振幅最大のモード。
        # 良好なものが無ければ全体から R² 最大を出す（図の信頼性を確保）。
        good = [m for m in results
                if np.isfinite(m["R2_env"]) and m["R2_env"] >= 0.8]
        if good:
            rep = max(good, key=lambda m: m.get("amp0", 0.0))
        else:
            rep = max(results, key=lambda m: (m["R2_env"] if np.isfinite(m["R2_env"]) else -1))

        # --- col0: 全体波形 + 各イベント区間を色分け ---
        ax0 = axs[r][0]
        ax0.plot(t_full, x_hp, lw=0.4, color="0.6")
        events = sorted({m["_event"]: m["_seg"] for m in results}.items())
        cmap = plt.get_cmap("tab10")
        for ei, (_ev, (s, e)) in enumerate(events):
            ax0.axvspan(t_full[s], t_full[min(e, len(t_full) - 1)],
                        color=cmap(ei % 10), alpha=0.15)
        ax0.set_title(f"{axis}: 全体波形（検出 {len(events)} pluck）", fontsize=10)
        ax0.set_xlabel("時間 [s]"); ax0.set_ylabel(axis)
        ax0.grid(True, alpha=0.3)

        # --- col1: 代表イベントの ln(env) 直線フィット ---
        ax1 = axs[r][1]
        t_rep = rep["_t"]; env = rep["_env"]
        i0, i1, coef = rep["_fit"]
        ax1.plot(t_rep, np.log(np.maximum(env, env.max() * 1e-6)),
                 lw=0.6, color="steelblue", label="ln(env)")
        tt = t_rep[i0:i1]
        ax1.plot(tt, np.polyval(coef, tt - tt[0]), lw=1.6, color="red",
                 label="直線フィット")
        ax1.set_title(
            f"{axis}: f_n={rep['f_n_Hz']:.2f}Hz  ζ={rep['zeta_pct']:.2f}%  "
            f"R²={rep['R2_env']:.3f}", fontsize=10)
        ax1.set_xlabel("時間 [s]"); ax1.set_ylabel("ln(振幅)")
        ax1.legend(fontsize=8); ax1.grid(True, alpha=0.3)

        # --- col2: 代表イベントの PSD とピーク ---
        ax2 = axs[r][2]
        fbb, pbb = rep["_psd"]
        ax2.semilogy(fbb, pbb, lw=0.8, color="steelblue")
        ax2.axvline(rep["f_d_Hz"], color="red", ls="--", lw=1.0,
                    label=f"f_d={rep['f_d_Hz']:.2f}Hz")
        ax2.set_xlim(0, min(args.fmax * 1.5, FS_TARGET / 2))
        ax2.set_title(f"{axis}: PSD", fontsize=10)
        ax2.set_xlabel("周波数 [Hz]"); ax2.set_ylabel("PSD")
        ax2.legend(fontsize=8); ax2.grid(True, alpha=0.3, which="both")

    fig.tight_layout(rect=(0, 0, 1, 0.98))
    out_path = os.path.join(out_dir, f"{base}_freedecay.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[図] {out_path} を保存しました")


# ============================================================
#  集計（全ファイル・全イベント）
# ============================================================
def summarize(df):
    """軸×モード（f_n を 1Hz 丸めでビン化）でグループ化し、平均±標準偏差を表示する。

    有効行（ζ が有限、R2_env ≥ 0.8）のみ集計に使い、config 転記候補も表示する。
    """
    valid = df[np.isfinite(df["zeta"]) & (df["R2_env"] >= 0.8)].copy()
    print("\n" + "=" * 64)
    print("[同定] 構造モードの同定結果（有効イベントの平均±標準偏差）")
    print("=" * 64)
    if valid.empty:
        print("  有効な減衰イベントがありません（R²・ζの条件を満たす行なし）。")
        print("  波形・図を確認し、pluck をやり直すか --fmin/--fmax/--hp_cutoff を調整してください。")
        return

    valid["fbin"] = valid["f_n_Hz"].round(0)
    grp = valid.groupby(["axis", "fbin"])
    config_lines = []
    for (axis, fbin), g in grp:
        fn_m, fn_s = g["f_n_Hz"].mean(), g["f_n_Hz"].std(ddof=0)
        z_m, z_s = g["zeta"].mean(), g["zeta"].std(ddof=0)
        print(f"  {axis:>3}  f_n = {fn_m:7.3f} ± {fn_s:5.3f} Hz   "
              f"ζ = {z_m*100:6.3f} ± {z_s*100:5.3f} %   "
              f"(n={len(g)}, R²_env={g['R2_env'].mean():.3f})")

    # config 転記候補（軸ごとに最も強い＝amp0 最大のモード）
    print("\n[同定] config.json 転記候補（軸ごとに振幅最大モード。軸→構造DOFの対応は要判断）:")
    for axis, g in valid.groupby("axis"):
        top = g.loc[g["amp0"].idxmax()]
        print(f"  {axis}:  f_n_hz = {top['f_n_Hz']:.3f},  zeta_s = {top['zeta']:.5f}")
    print("=" * 64)


# ============================================================
#  入力・CLI
# ============================================================
def collect_files(paths):
    """パス（ファイル or フォルダ）群から解析対象CSVを集める（volt_raw を除外）。"""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for f in sorted(glob.glob(os.path.join(p, "*.csv"))):
                if not f.endswith("_volt_raw.csv"):
                    files.append(f)
        elif os.path.isfile(p):
            files.append(p)
        else:
            warnings.warn(f"見つかりません: {p}")
    return files


def parse_args():
    p = argparse.ArgumentParser(
        description="自由減衰（pluck test）から構造 f_n・ζ を同定する（予備実験・自己完結）")
    p.add_argument("paths", nargs="+",
                   help="pluck記録CSV、またはそれを含むフォルダ（複数可）")
    p.add_argument("--signals", default=",".join(ALL_AXES),
                   help=f"解析する軸（カンマ区切り。既定: {','.join(ALL_AXES)}＝6軸すべて）")
    p.add_argument("--fmin", type=float, default=0.5,
                   help="モード探索の下限周波数 [Hz]（既定: 0.5）")
    p.add_argument("--fmax", type=float, default=200.0,
                   help="モード探索の上限周波数 [Hz]（既定: 200）")
    p.add_argument("--n_modes", type=int, default=2,
                   help="1軸あたり同定する最大モード数（既定: 2）")
    p.add_argument("--hp_cutoff", type=float, default=0.5,
                   help="DCドリフト除去のハイパスカットオフ [Hz]（既定: 0.5）")
    p.add_argument("--min_snr", type=float, default=8.0,
                   help="pluck検出のSNRしきい値（包絡線ピーク/雑音床。既定: 8。雑音のみの軸を弾く）")
    p.add_argument("--min_cycles", type=float, default=5.0,
                   help="モード採用に必要な最小周期数（既定: 5。短い偽減衰を弾く）")
    p.add_argument("--out", default=None,
                   help="出力先フォルダ（既定: 最初の入力と同じ場所）")
    return p.parse_args()


def main():
    args = parse_args()
    files = collect_files(args.paths)
    if not files:
        print("[エラー] 解析対象CSVが見つかりません。", file=sys.stderr)
        sys.exit(1)

    signals = [s.strip() for s in args.signals.split(",") if s.strip()]
    out_dir = args.out or os.path.dirname(os.path.abspath(files[0])) or "."
    os.makedirs(out_dir, exist_ok=True)

    print(f"[解析] {len(files)} ファイル・軸 {signals} を処理します")
    all_rows = []
    for path in files:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            rows, per_axis = process_file(path, signals, args)
        all_rows.extend(rows)
        if per_axis:
            plot_file(path, per_axis, out_dir, args)
        print(f"  {os.path.basename(path)}: {len(rows)} イベント×モードを検出")

    if not all_rows:
        print("[警告] 有効な減衰イベントが1つも見つかりませんでした。")
        return

    df = pd.DataFrame(all_rows)
    csv_path = os.path.join(out_dir, "free_decay_summary.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[保存] {csv_path}（{len(df)} 行）")

    summarize(df)


if __name__ == "__main__":
    main()
