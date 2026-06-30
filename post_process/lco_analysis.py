#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lco_analysis.py  フラッター実験 LCO（リミットサイクル振動）非線形動力学解析

flutter_analysis.py が振幅・周波数領域の解析（RMS / Welch PSD / 卓越周波数 /
迎角×周波数マップ / フラッター発生マップ）を担うのに対し、本モジュールは
Trickey et al. (2002) と Amandolese et al. (2013) で用いられる
非線形動力学の手法を追加する：

  - 時間遅れ埋め込み（Takens）による位相図
  - Poincaré 断面（速度ゼロ交差サンプリング）
  - スペクトルの調和構造の定量（調和率・スペクトル平坦度）
  - 成長率 ζ（Amandolese eq.6：ピーク振幅の対数減衰）

これらから応答タイプ（stable / periodic / quasi-periodic / chaotic）を
「人が目で読み取る」ための材料（図・指標）を出力する。
自動ラベル付けはしない（実データを見てから閾値を決める方が堅実なため）。

前処理（オフセット補正→等間隔リサンプリング→平均引き→ハイパス）は
flutter_analysis.py 側で済ませた信号を受け取る設計。本モジュールは
そこから import した純関数のみを使い、処理を二重実装しない。

【単体実行】
  python lco_analysis.py <6軸CSVのパス> [--signal Fy|Mz]
    1点ぶんのカルテ図と指標を出力して動作確認する（ofst補正なし・簡易）。
"""

import argparse
import os
import sys

import numpy as np
from scipy import signal
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# flutter_analysis の前処理関数を再利用（二重実装しない）
from flutter_analysis import (
    load_csv,
    resample_uniform,
    highpass,
    calc_psd,
    dominant_freq,
    FS_TARGET,
    HP_CUTOFF_HZ,
)

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


# ============================================================
#  定数
# ============================================================
# 調和・ピーク解析の対象帯域（flutter_analysis の dominant_freq に合わせる）
LCO_FMIN_HZ = 1.0
LCO_FMAX_HZ = 500.0

# 調和率の許容幅：ピーク周波数が f0 の整数倍からどれだけずれてよいか。
# 相対許容（次数に比例して効く）と、絶対許容（Welch の周波数分解能の倍数で
# 下限を確保）の大きい方を使う。高次高調波で分解能割れしないように。
HARMONIC_TOL_REL = 0.05      # f0 に対する相対許容
HARMONIC_TOL_BINS = 2.0      # 周波数分解能の何ビンぶんまで許容するか

# ピーク検出のプロミネンス（背景メジアンに対する倍率）
PEAK_PROMINENCE_RATIO = 4.0


# ============================================================
#  時間遅れ τ の推定
# ============================================================
def estimate_tau_autocorr(x, fs=FS_TARGET, max_lag_sec=0.5, f0=None):
    """自己相関の最初のゼロ交差を時間遅れ τ とする（Trickey の周期応答向き定義）。

    ゼロ交差が見つからない場合は f0 から τ=(1/4)/f0（1/4周期則）にフォールバック。
    f0 も無ければ NaN を返す。

    Returns
    -------
    tau_samples : int    遅れ（サンプル数、>=1）
    tau_sec     : float  遅れ（秒）
    """
    x = np.asarray(x, dtype=float)
    x = x - np.mean(x)
    n = len(x)
    if n < 4:
        return _tau_from_f0(f0, fs)

    max_lag = min(int(max_lag_sec * fs), n - 1)
    # 正側の自己相関のみ（lag 0..max_lag）を FFT で計算（O(n log n)）。
    # np.correlate の full は O(n^2) で 3万点超では非実用的に遅いため避ける。
    nfft = 1 << int(np.ceil(np.log2(2 * n - 1)))
    X = np.fft.rfft(x, nfft)
    ac_full = np.fft.irfft(X * np.conj(X), nfft)[: max_lag + 1]
    if ac_full[0] == 0:
        return _tau_from_f0(f0, fs)
    ac = ac_full / ac_full[0]

    # 最初のゼロ交差（符号が正→非正に変わるラグ）
    sign = np.sign(ac)
    cross = np.where((sign[:-1] > 0) & (sign[1:] <= 0))[0]
    if cross.size == 0:
        return _tau_from_f0(f0, fs)

    tau_samples = int(cross[0]) + 1
    tau_samples = max(tau_samples, 1)
    return tau_samples, tau_samples / fs


def _tau_from_f0(f0, fs):
    """f0 から 1/4 周期則で τ を決めるフォールバック。"""
    if f0 is None or not np.isfinite(f0) or f0 <= 0:
        return np.nan, np.nan
    tau_sec = 0.25 / f0
    tau_samples = max(int(round(tau_sec * fs)), 1)
    return tau_samples, tau_samples / fs


# ============================================================
#  時間遅れ埋め込み
# ============================================================
def delay_embedding(x, tau_samples, dim=2):
    """時間遅れ座標 [x(t), x(t-τ), ...] を生成する（Takens）。

    位相図用は dim=2（[x(t), x(t-τ)]）。dim は将来拡張用に引数化。

    Returns
    -------
    emb : ndarray, shape (N - (dim-1)*tau, dim)
          tau が不正な場合は None。
    """
    x = np.asarray(x, dtype=float)
    if not np.isfinite(tau_samples) or tau_samples < 1:
        return None
    tau = int(tau_samples)
    span = (dim - 1) * tau
    if len(x) <= span:
        return None
    cols = [x[span - k * tau: len(x) - k * tau] for k in range(dim)]
    return np.column_stack(cols)


# ============================================================
#  Poincaré 断面
# ============================================================
def poincare_section(x, fs=FS_TARGET, f0=None):
    """サイクルごとに1点をサンプリングして Poincaré 断面の点群を返す。

    Trickey に倣い「速度が上から（正→負）0 を横切る瞬間」の変位値をサンプルする。
    速度は信号の数値微分 v = d x/dt で求め、v が正→負に符号反転する各点で
    線形補間して v=0 となる瞬間の x（変位）を内挿する（サンプル粒度より精密）。
    周期LCO=1点に集中、period-2=2点、quasi-periodic=閉曲線、chaotic=散布 となる。

    高調波由来の微小な速度ゼロクロスを拾わないよう、基本周期に基づく最小間隔を
    与えて主極大（1周期1点）のみ採用する。

    Parameters
    ----------
    f0 : float or None   基本周波数 [Hz]。あれば1周期1点の最小間隔の決定に使う。

    Returns
    -------
    x_cross : ndarray   各サイクルで速度が正→負に0を横切る瞬間の x の値（変位）
    """
    x = np.asarray(x, dtype=float)
    if len(x) < 8:
        return np.array([])

    # 速度（数値微分）。ゼロクロス判定は符号のみだが物理スケールに揃えておく。
    v = np.gradient(x) * fs

    # 速度が正→負に0を横切る index（v[i] > 0 かつ v[i+1] <= 0）
    crossings = np.where((v[:-1] > 0) & (v[1:] <= 0))[0]
    if crossings.size == 0:
        return np.array([])

    # 1周期1点ガード：基本周期の 0.7 倍未満の間隔で続くゼロクロスは間引く。
    if f0 is not None and np.isfinite(f0) and f0 > 0:
        dmin = max(int(0.7 * fs / f0), 1)
    else:
        dmin = 1

    x_cross = []
    last = -dmin  # 最初の点は必ず採用できるよう十分過去に
    for i in crossings:
        if i - last < dmin:
            continue
        # v=0 となる位置で x を線形補間（v[i] > 0 >= v[i+1] なので分母は正）
        denom = v[i] - v[i + 1]
        frac = v[i] / denom if denom != 0 else 0.0
        x_cross.append(x[i] + frac * (x[i + 1] - x[i]))
        last = i
    if not x_cross:
        return np.array([])
    return np.asarray(x_cross, dtype=float)


def poincare_spread(pts):
    """Poincaré 点群の正規化分散と概数クラスタ数を返す。

    Returns
    -------
    dispersion : float   点群の標準偏差 / 振幅スケール（小=周期的, 大=カオス的）
    n_clusters : int     1=period-1, 2=period-2, それ以上=QP/chaos の目安
    """
    pts = np.asarray(pts, dtype=float)
    if pts.size < 2:
        return np.nan, 0
    amp = np.max(np.abs(pts)) + 1e-12
    dispersion = float(np.std(pts) / amp)

    # 点群が振幅に対して十分まとまっていれば（dispersion小）周期1とみなす。
    # わずかなばらつきを span 相対のギャップで過剰分割しないためのガード。
    if dispersion < 0.05:
        return dispersion, 1

    # クラスタ数：値をソートし、全幅の一定割合より大きいギャップで区切る。
    # ただしギャップ閾値は振幅スケールでも下限を設け、ノイズで割れないようにする。
    s = np.sort(pts)
    span = s[-1] - s[0]
    if span <= amp * 1e-3:
        return dispersion, 1
    gaps = np.diff(s)
    gap_thresh = max(0.15 * span, 0.05 * amp)
    n_clusters = int(1 + np.sum(gaps > gap_thresh))
    return dispersion, n_clusters


# ============================================================
#  位相図ループの太さ
# ============================================================
def phase_loop_thickness(emb):
    """埋め込み点群の重心まわり極座標で、動径方向ばらつきを正規化して返す。

    細い単一ループ（periodic）= 小、太いトーラス/カオス = 大。

    Returns
    -------
    thickness : float   角度ビンごとの動径標準偏差の平均 / 平均動径
    """
    if emb is None or emb.shape[0] < 8 or emb.shape[1] < 2:
        return np.nan
    c = emb[:, :2] - emb[:, :2].mean(axis=0)
    r = np.hypot(c[:, 0], c[:, 1])
    theta = np.arctan2(c[:, 1], c[:, 0])
    if np.mean(r) <= 0:
        return np.nan

    n_bins = 36
    bins = np.linspace(-np.pi, np.pi, n_bins + 1)
    idx = np.digitize(theta, bins) - 1
    spreads = []
    for b in range(n_bins):
        rb = r[idx == b]
        if rb.size >= 2:
            spreads.append(np.std(rb))
    if not spreads:
        return np.nan
    return float(np.mean(spreads) / np.mean(r))


# ============================================================
#  調和構造の定量
# ============================================================
def harmonic_metrics(freqs, psd, f0, fmin=LCO_FMIN_HZ, fmax=LCO_FMAX_HZ):
    """スペクトルの離散ピークから調和性・平坦度を算出する。

    Returns
    -------
    dict:
      n_peaks            有意ピーク数
      harmonic_ratio     ピークが f0 の整数倍に乗る割合（高=周期LCO）
      spectral_flatness  Wiener entropy（0=純音的, 1=広帯域=chaos寄り）
      n_incommensurate   f0 整数倍に乗らない強ピーク数（QPの目安）
    """
    freqs = np.asarray(freqs, dtype=float)
    psd = np.asarray(psd, dtype=float)
    mask = (freqs >= fmin) & (freqs <= fmax)
    out = {"n_peaks": 0, "harmonic_ratio": np.nan,
           "spectral_flatness": np.nan, "n_incommensurate": 0}
    if not np.any(mask):
        return out
    f = freqs[mask]
    p = psd[mask]
    if p.size < 4 or p.max() <= 0:
        return out

    # スペクトル平坦度（幾何平均 / 算術平均）
    p_pos = np.maximum(p, p.max() * 1e-12)
    flatness = float(np.exp(np.mean(np.log(p_pos))) / np.mean(p_pos))
    out["spectral_flatness"] = flatness

    # ピーク検出
    bg = np.median(p)
    prominence = max(bg * PEAK_PROMINENCE_RATIO, p.max() * 1e-6)
    peaks, _ = signal.find_peaks(p, prominence=prominence)
    out["n_peaks"] = int(peaks.size)
    if peaks.size == 0 or f0 is None or not np.isfinite(f0) or f0 <= 0:
        return out

    peak_freqs = f[peaks]
    # 各ピークが f0 の整数倍に乗るか（絶対周波数距離で判定）。
    # 許容幅 = max(相対許容 × 期待周波数, 分解能ビンぶん)。高次でも分解能割れしない。
    df_res = float(np.median(np.diff(f))) if f.size > 1 else 0.0
    nearest = np.round(peak_freqs / f0)
    expected = nearest * f0
    tol_hz = np.maximum(HARMONIC_TOL_REL * expected, HARMONIC_TOL_BINS * df_res)
    on_harmonic = (np.abs(peak_freqs - expected) <= tol_hz) & (nearest >= 1)
    out["harmonic_ratio"] = float(np.mean(on_harmonic))
    out["n_incommensurate"] = int(np.sum(~on_harmonic))
    return out


# ============================================================
#  成長率 ζ（Amandolese eq.6）
# ============================================================
def growth_rate(x, fs=FS_TARGET):
    """ピーク振幅列の対数減衰から成長率 ζ を算出する（Amandolese eq.6）。

    連続するピーク（極大）振幅 A_i に対し δ_i = ln(A_{i+1}) - ln(A_i)、
    ζ_i = δ_i / sqrt((2π)^2 + δ_i^2) として、全区間の中央値を返す。
    正=発散、負=減衰、0付近=定常LCO。構造減衰比と直接比較できる。

    Returns
    -------
    zeta : float   成長率（無次元）。ピークが少なければ NaN。
    """
    x = np.asarray(x, dtype=float)
    peaks, _ = signal.find_peaks(np.abs(x))
    if peaks.size < 3:
        return np.nan
    amps = np.abs(x[peaks])
    amps = amps[amps > 0]
    if amps.size < 3:
        return np.nan
    delta = np.diff(np.log(amps))
    zeta = delta / np.sqrt((2 * np.pi) ** 2 + delta ** 2)
    return float(np.median(zeta))


# ============================================================
#  1信号ぶんの指標まとめ
# ============================================================
def analyze_signal(x, fs=FS_TARGET, fmin=LCO_FMIN_HZ, fmax=LCO_FMAX_HZ,
                   tau_mode="zero_cross"):
    """補正済み信号 x から LCO 指標一式と中間生成物を返す。

    Returns
    -------
    dict（指標）と、図用の中間生成物（emb, poincare, freqs, psd, f0, tau）。
    """
    x = np.asarray(x, dtype=float)
    freqs, psd = calc_psd(x, fs=fs)
    f0 = dominant_freq(freqs, psd, fmin=fmin, fmax=fmax)

    if tau_mode == "quarter_period":
        tau_samples, tau_sec = _tau_from_f0(f0, fs)
    else:
        tau_samples, tau_sec = estimate_tau_autocorr(x, fs=fs, f0=f0)

    emb = delay_embedding(x, tau_samples, dim=2)
    pts = poincare_section(x, fs=fs, f0=f0)

    hm = harmonic_metrics(freqs, psd, f0, fmin=fmin, fmax=fmax)
    thickness = phase_loop_thickness(emb)
    disp, n_clusters = poincare_spread(pts)
    zeta = growth_rate(x, fs=fs)

    metrics = {
        "f0": f0,
        "tau_sec": tau_sec,
        "n_peaks": hm["n_peaks"],
        "harmonic_ratio": hm["harmonic_ratio"],
        "spectral_flatness": hm["spectral_flatness"],
        "n_incommensurate": hm["n_incommensurate"],
        "loop_thickness": thickness,
        "poincare_disp": disp,
        "poincare_nclust": n_clusters,
        "growth_rate": zeta,
    }
    artifacts = {
        "emb": emb,
        "poincare": pts,
        "freqs": freqs,
        "psd": psd,
        "f0": f0,
        "tau_samples": tau_samples,
    }
    return metrics, artifacts


# ============================================================
#  カルテ図（3点セット：時系列／位相図／スペクトル）
# ============================================================
def plot_chart(fig_dir, short, t, signals, fs=FS_TARGET, aoa=None,
               fmax_disp=None, case_name="", rep_U=None):
    """1計測点のカルテ図（Trickey fig.4-7 形式）を出力する。

    signals : dict {"Fy": (x_hp, metrics, artifacts), "Mz": (...)}
              x_hp は補正済み・HP済み信号。
    横3列（時系列 / 位相図 / スペクトル）× 行数（信号数）。
    """
    os.makedirs(fig_dir, exist_ok=True)
    names = list(signals.keys())
    nrow = len(names)
    fig, axes = plt.subplots(nrow, 3, figsize=(15, 4 * nrow), squeeze=False)

    title = "LCO chart"
    if aoa is not None:
        title = f"AoA = {aoa:+d}°   {title}"
    if case_name:
        title += f"   {case_name}"
    if rep_U is not None:
        title += f"   U ≈ {rep_U:.2f} m/s"
    title += f"   ({short})"
    fig.suptitle(title, fontsize=13)

    for i, name in enumerate(names):
        x, m, art = signals[name]
        t_x = t if (t is not None and len(t) == len(x)) else np.arange(len(x)) / fs

        # --- 列1: 時系列 ---
        ax = axes[i, 0]
        ax.plot(t_x, x, lw=0.6, color="steelblue")
        ax.set_xlabel("time [s]")
        ax.set_ylabel(f"{name}")
        ax.set_title(f"{name}  time series")

        # --- 列2: 位相図 x(t) vs x(t-τ) ---
        ax = axes[i, 1]
        emb = art["emb"]
        if emb is not None:
            ax.plot(emb[:, 0], emb[:, 1], lw=0.4, color="darkorange", alpha=0.8)
        ax.set_xlabel(f"{name}(t)")
        ax.set_ylabel(f"{name}(t-τ)")
        tau_ms = m["tau_sec"] * 1e3 if np.isfinite(m["tau_sec"]) else np.nan
        ax.set_title(f"phase  (τ={tau_ms:.1f} ms)")
        ax.set_aspect("equal", adjustable="datalim")

        # --- 列3: パワースペクトル ---
        ax = axes[i, 2]
        freqs, psd = art["freqs"], art["psd"]
        with np.errstate(divide="ignore"):
            psd_db = 10 * np.log10(np.maximum(psd, psd.max() * 1e-12)) if psd.max() > 0 else psd
        ax.plot(freqs, psd_db, lw=0.7, color="seagreen")
        if np.isfinite(m["f0"]):
            ax.axvline(m["f0"], color="red", ls="--", lw=0.8,
                       label=f"f0={m['f0']:.1f} Hz")
            ax.legend(fontsize=9)
        ax.set_xlabel("frequency [Hz]")
        ax.set_ylabel("power [dB]")
        ax.set_xlim(0, fmax_disp or 50.0)
        hr = m["harmonic_ratio"]
        sf = m["spectral_flatness"]
        ax.set_title(f"spectrum  (harm={hr:.2f}, flat={sf:.2f})"
                     if np.isfinite(hr) and np.isfinite(sf) else "spectrum")

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = os.path.join(fig_dir, f"{short}_lco.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


# ============================================================
#  flutter_analysis からのエントリ関数
# ============================================================
def analyze_point(t, sigs, aoa, short, fig_dir, args, case_name="", rep_U=None):
    """1計測点のLCO解析を行い、指標とカルテ図を出力する。

    flutter_analysis.process_one_condition のループから --lco 時に呼ばれる。

    Parameters
    ----------
    t     : ndarray         補正済み・等間隔の時刻 [s]
    sigs  : dict            {"Fy": x_hp, "Mz": x_hp}（補正済み・HP済み信号）
    aoa   : int             迎角 [deg]（負迎角は負値）
    short : str             短縮名（図ファイル名に使う）
    fig_dir : str           図の出力先
    args  : Namespace       CLI引数（lco_signals / lco_tau_mode / lco_fmin/fmax）
    case_name : str         条件フォルダ名（図タイトル表記用）
    rep_U : float           代表風速 [m/s]（図タイトル表記用）

    Returns
    -------
    dict:
      metrics : flutter_summary.csv にマージする指標（成分サフィックス付き）
      row     : 全条件レベルの図（分岐図等）用の中間生成物（aoa・Poincaré等を含む）
    """
    names = [s.strip() for s in args.lco_signals.split(",") if s.strip()]
    fmin = getattr(args, "lco_fmin", LCO_FMIN_HZ)
    fmax = getattr(args, "lco_fmax", LCO_FMAX_HZ)
    tau_mode = getattr(args, "lco_tau_mode", "zero_cross")
    fmax_disp = getattr(args, "map_fmax", 50.0)

    metrics = {}
    row = {"aoa": aoa, "short": short}
    chart_sigs = {}
    for name in names:
        if name not in sigs:
            continue
        x = sigs[name]
        m, art = analyze_signal(x, fs=FS_TARGET, fmin=fmin, fmax=fmax,
                                tau_mode=tau_mode)
        # 指標は成分サフィックス付きで CSV にマージ
        for k, v in m.items():
            metrics[f"{k}_{name}"] = v
        chart_sigs[name] = (x, m, art)
        # 条件・全条件レベル図用の生成物を退避（メモリ節約のため埋め込みは間引く）
        row[f"poincare_{name}"] = art["poincare"]
        row[f"f0_{name}"] = art["f0"]
        row[f"emb_{name}"] = _decimate_emb(art["emb"])
        row[f"loop_thickness_{name}"] = m["loop_thickness"]
        row[f"harmonic_ratio_{name}"] = m["harmonic_ratio"]
        row[f"spectral_flatness_{name}"] = m["spectral_flatness"]

    if chart_sigs:
        plot_chart(fig_dir, short, t, chart_sigs, fs=FS_TARGET,
                   aoa=aoa, fmax_disp=fmax_disp,
                   case_name=case_name, rep_U=rep_U)

    return {"metrics": metrics, "row": row}


def _decimate_emb(emb, max_points=4000):
    """位相図の俯瞰グリッド用に埋め込み座標を間引く（メモリ・描画コスト削減）。"""
    if emb is None:
        return None
    if emb.shape[0] <= max_points:
        return emb
    step = int(np.ceil(emb.shape[0] / max_points))
    return emb[::step]


def plot_phase_sweep(lco_rows, exp_dir, rep_U, args):
    """迎角に沿って位相図を並べたグリッド図を出力する（条件レベル）。

    Trickey et al. (2002) の応答変化の俯瞰に相当。迎角が増えるにつれ
    細い閉ループ（periodic LCO）⇔ 太い雲（乱流的）がどう移り変わるかを
    1枚で見渡せる。Fy・Mz それぞれ1枚出力する。

    lco_rows : list of row dict（analyze_point の戻り値 "row"）
    """
    if not lco_rows:
        return
    case_name = os.path.basename(exp_dir)
    names = [s.strip() for s in args.lco_signals.split(",") if s.strip()]
    # 迎角昇順（重複迎角は Pdata/Mdata 別個に残す＝そのまま並べる）
    rows = sorted(lco_rows, key=lambda r: r["aoa"])

    for name in names:
        emb_key = f"emb_{name}"
        cells = [r for r in rows if r.get(emb_key) is not None]
        if not cells:
            continue

        n = len(cells)
        ncol = min(8, max(4, int(np.ceil(np.sqrt(n)))))
        nrow = int(np.ceil(n / ncol))
        fig, axes = plt.subplots(nrow, ncol,
                                 figsize=(2.0 * ncol, 2.0 * nrow),
                                 squeeze=False)
        fig.suptitle(f"Phase sweep  {name}   {case_name}   U ≈ {rep_U:.2f} m/s",
                     fontsize=13)

        for k, r in enumerate(cells):
            ax = axes[k // ncol][k % ncol]
            emb = r[emb_key]
            ax.plot(emb[:, 0], emb[:, 1], lw=0.3, color="darkorange", alpha=0.8)
            thick = r.get(f"loop_thickness_{name}", np.nan)
            ax.set_title(f"{r['aoa']:+d}°  (thk={thick:.2f})", fontsize=8)
            ax.set_xticks([]); ax.set_yticks([])
            ax.set_aspect("equal", adjustable="datalim")

        # 余ったセルは消す
        for k in range(n, nrow * ncol):
            axes[k // ncol][k % ncol].axis("off")

        fig.tight_layout(rect=[0, 0, 1, 0.97])
        out = os.path.join(exp_dir, f"phase_sweep_{name}.png")
        fig.savefig(out, dpi=130, bbox_inches="tight")
        plt.close(fig)
        print(f"  [LCO] phase_sweep_{name}.png を保存しました")


def plot_all_conditions(lco_data, map_dir, args):
    """全条件レベルのLCO図（分岐図・周波数合流図・スペクトログラム）を出力する。

    lco_data : list of (rep_U, lco_rows)

    NOTE: 風速版スペクトログラム（ステップ4）は別途追加する。
    """
    if not lco_data:
        return
    names = [s.strip() for s in args.lco_signals.split(",") if s.strip()]
    plot_bifurcation(lco_data, map_dir, names)
    plot_freq_coalescence(lco_data, map_dir, names)
    plot_lco_metric_map(lco_data, map_dir, names)


def plot_speed_spectrogram(panel_data, map_dir, args, build_grid):
    """風速版スペクトログラム（Trickey fig.8）。迎角固定で横軸=風速・縦軸=周波数。

    フラッターが明瞭な迎角でこそ周波数追跡が意味を持つため、--lco_spec_aoa で
    迎角を指定する（カンマ区切り、複数可）。未指定なら全条件で最も振幅の大きい
    正側・負側の迎角を1つずつ自動選択する。

    build_grid : flutter_analysis.build_speed_freq_grid を渡す
                 （PSDデータは flutter_analysis 側が持つため）
    """
    if not panel_data:
        return

    # 対象迎角の決定
    spec_aoa = getattr(args, "lco_spec_aoa", "") or ""
    if spec_aoa.strip():
        target_aoas = []
        for tok in spec_aoa.split(","):
            tok = tok.strip()
            if tok:
                try:
                    target_aoas.append(int(tok))
                except ValueError:
                    pass
    else:
        target_aoas = _auto_select_aoas(panel_data)

    if not target_aoas:
        return

    for axis, key, unit in [("Fy", "psd_Fy", "N"), ("Mz", "psd_Mz", "Nm")]:
        for aoa in target_aoas:
            grid = build_grid(panel_data, aoa, key, args)
            if grid is None:
                continue
            u_axis, f_plot, Z_db, peak_u, peak_freq = grid

            fig, ax = plt.subplots(figsize=(10, 7))
            mesh = ax.pcolormesh(u_axis, f_plot, Z_db, shading="nearest",
                                 cmap="viridis", vmin=-args.map_dyn_range, vmax=0.0)
            cbar = fig.colorbar(mesh, ax=ax)
            cbar.set_label("Power [dB] (normalised to max)", fontsize=12)
            ax.scatter(peak_u, peak_freq, s=20, facecolors="none",
                       edgecolors="white", linewidths=0.9, zorder=3,
                       label="dominant freq")
            ax.set_xlabel("wind speed U [m/s]", fontsize=13)
            ax.set_ylabel("Frequency [Hz]", fontsize=13)
            ax.set_ylim(0, args.map_fmax)
            ax.set_title(f"Speed spectrogram  {axis} [{unit}]   AoA = {aoa:+d}°",
                         fontsize=13)
            ax.legend(fontsize=10, loc="upper right")
            out = os.path.join(map_dir,
                               f"spectrogram_speed_{axis}_aoa{aoa:+03d}.png")
            fig.savefig(out, dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"[LCO] {os.path.basename(out)} を保存しました")


def _auto_select_aoas(panel_data):
    """全条件のPSDから、正側・負側で最も卓越パワーが大きい迎角を1つずつ選ぶ。

    フラッターが明瞭（卓越ピークが強い）な迎角を風速スペクトログラムの対象にする。
    """
    score = {}   # aoa -> 全風速での最大PSDピークの合計
    for _, spec_rows in panel_data:
        for r in spec_rows:
            p = max(float(np.max(r["psd_Mz"])), float(np.max(r["psd_Fy"])))
            score[r["aoa"]] = score.get(r["aoa"], 0.0) + p
    if not score:
        return []
    pos = [a for a in score if a > 0]
    neg = [a for a in score if a < 0]
    out = []
    if pos:
        out.append(max(pos, key=lambda a: score[a]))
    if neg:
        out.append(max(neg, key=lambda a: score[a]))
    return out


def plot_bifurcation(lco_data, map_dir, names):
    """分岐図（Trickey fig.3）。横軸=迎角、縦軸=Poincaré点（変位）。

    Trickey に倣い「速度が上から（正→負）0 を横切る瞬間」の変位値をプロットする。
    各風速条件を1枚に重ね、フラッター発生迎角域での応答の質（1点に集中＝
    periodic / 縦に散らばる＝QP・chaos）を俯瞰する。風速ごとに色分け。

    この実験系では迎角が主要な制御パラメータのため、横軸を迎角にとる。
    """
    for name in names:
        pkey = f"poincare_{name}"
        fig, ax = plt.subplots(figsize=(11, 6))
        cmap = plt.get_cmap("viridis")
        us = [u for u, _ in lco_data]
        umin, umax = min(us), max(us)
        any_pt = False
        for rep_U, rows in lco_data:
            color = cmap((rep_U - umin) / (umax - umin + 1e-9))
            for r in rows:
                pts = r.get(pkey)
                if pts is None or len(pts) == 0:
                    continue
                aoa = r["aoa"]
                ax.scatter(np.full(len(pts), aoa), pts, s=2, color=color,
                           alpha=0.4, edgecolors="none")
                any_pt = True
        if not any_pt:
            plt.close(fig)
            continue

        sm = plt.cm.ScalarMappable(cmap=cmap,
                                   norm=plt.Normalize(vmin=umin, vmax=umax))
        cbar = fig.colorbar(sm, ax=ax)
        cbar.set_label("wind speed U [m/s]", fontsize=12)
        ax.set_xlabel("Angle of attack [deg]", fontsize=13)
        ax.set_ylabel(f"Poincaré value @ velocity zero-cross ({name})",
                      fontsize=13)
        ax.set_title(f"Bifurcation diagram  {name}  "
                     f"(displacement @ velocity:+→− vs AoA)",
                     fontsize=13)
        out = os.path.join(map_dir, f"bifurcation_{name}.png")
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[LCO] bifurcation_{name}.png を保存しました")


def plot_freq_coalescence(lco_data, map_dir, names):
    """周波数合流図（Amandolese fig.6）。横軸=風速、縦軸=卓越周波数。

    Fy・Mz の卓越周波数を同じ図に重ね、連成モードフラッターで2モードの
    周波数が近づく（合流する）様子を見る。フラッター発生点（振動が有意な点）の
    みプロットすると合流が見やすい。loop_thickness が小さい点（明瞭なLCO）を
    マーカーサイズで強調する。
    """
    fig, ax = plt.subplots(figsize=(10, 6))
    markers = {"Fy": "o", "Mz": "^"}
    colors = {"Fy": "tab:blue", "Mz": "tab:red"}
    any_pt = False
    for name in names:
        f0key = f"f0_{name}"
        tkey = f"loop_thickness_{name}"
        for rep_U, rows in lco_data:
            for r in rows:
                f0 = r.get(f0key)
                if f0 is None or not np.isfinite(f0):
                    continue
                thk = r.get(tkey, np.nan)
                # 細いループ（明瞭なLCO）は大きく濃く、ノイズ的な点は小さく薄く描く
                # （合流を見やすくするためノイズ点を目立たせない）
                clear = np.isfinite(thk) and thk < 0.2
                size = 70 if clear else 6
                alpha = 0.7 if clear else 0.12
                ax.scatter(rep_U, f0, s=size, marker=markers.get(name, "o"),
                           color=colors.get(name, "gray"), alpha=alpha,
                           edgecolors="none")
                any_pt = True
    if not any_pt:
        plt.close(fig)
        return
    # 凡例（成分とサイズの意味）
    from matplotlib.lines import Line2D
    handles = [Line2D([0], [0], marker=markers[n], color="w",
                      markerfacecolor=colors[n], markersize=8, label=n)
               for n in names if n in markers]
    handles.append(Line2D([0], [0], marker="o", color="w",
                          markerfacecolor="gray", markersize=10,
                          label="clear LCO (thin loop)"))
    ax.legend(handles=handles, fontsize=10)
    ax.set_xlabel("wind speed U [m/s]", fontsize=13)
    ax.set_ylabel("dominant frequency [Hz]", fontsize=13)
    ax.set_title("Frequency coalescence  (Fy / Mz dominant freq vs U)",
                 fontsize=13)
    out = os.path.join(map_dir, "freq_coalescence.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[LCO] freq_coalescence.png を保存しました")


def plot_lco_metric_map(lco_data, map_dir, names):
    """迎角×風速の格子に LCO 指標（loop_thickness）を重畳した俯瞰図。

    loop_thickness が小さい（細い閉ループ＝明瞭な periodic LCO）ほど濃く・
    大きく描く。フラッター発生域が迎角×風速平面のどこに広がるかを一望する。
    4タイプの自動色分けはせず、人が読み取る材料を提示する。
    """
    for name in names:
        tkey = f"loop_thickness_{name}"
        xs, ys, cs = [], [], []
        for rep_U, rows in lco_data:
            for r in rows:
                thk = r.get(tkey)
                if thk is None or not np.isfinite(thk):
                    continue
                xs.append(r["aoa"]); ys.append(rep_U); cs.append(thk)
        if not xs:
            continue
        cs = np.asarray(cs)
        # 細いループほど大きいマーカー（見やすさのため反転スケール）
        sizes = 200 * np.clip(1.0 - cs / (cs.max() + 1e-9), 0.05, 1.0)

        fig, ax = plt.subplots(figsize=(11, 6))
        sc = ax.scatter(xs, ys, c=cs, s=sizes, cmap="viridis_r",
                        edgecolors="k", linewidths=0.3)
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("loop thickness", fontsize=12)
        ax.set_xlabel("Angle of attack [deg]", fontsize=13)
        ax.set_ylabel("wind speed U [m/s]", fontsize=13)
        ax.set_title(f"LCO metric map  {name}  "
                     f"(large & yellow = thin loop = clear LCO)", fontsize=13)
        out = os.path.join(map_dir, f"lco_metric_map_{name}.png")
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"[LCO] lco_metric_map_{name}.png を保存しました")


# ============================================================
#  単体実行（1CSVの動作確認）
# ============================================================
def _standalone(csv_path, signal_name, hp_cutoff):
    """ofst補正なしの簡易処理で1点を解析し、カルテ図と指標を出力する。"""
    df = load_csv(csv_path)
    t = df["t"].values
    print(f"[読み込み] {os.path.basename(csv_path)}  ({len(df)} 行)")

    sigs = {}
    for name in signal_name:
        raw = df[name].values
        t_u, x_u = resample_uniform(t, raw, fs=FS_TARGET)
        x_hp = highpass(x_u - np.mean(x_u), hp_cutoff, fs=FS_TARGET)
        m, art = analyze_signal(x_hp, fs=FS_TARGET)
        sigs[name] = (x_hp, m, art)

        print(f"\n--- {name} ---")
        for k, v in m.items():
            print(f"  {k:18s}: {v}")

    fig_dir = os.path.dirname(os.path.abspath(csv_path))
    short = os.path.splitext(os.path.basename(csv_path))[0] + "_standalone"
    out = plot_chart(fig_dir, short, t_u, sigs, fs=FS_TARGET)
    print(f"\n[出力] {out}")


def main():
    p = argparse.ArgumentParser(description="LCO非線形動力学解析（単体動作確認）")
    p.add_argument("csv", help="6軸CSVのパス")
    p.add_argument("--signal", default="Fy,Mz",
                   help="解析する成分（カンマ区切り、既定: Fy,Mz）")
    p.add_argument("--hp_cutoff", type=float, default=HP_CUTOFF_HZ,
                   help=f"ハイパスカットオフ [Hz]（既定: {HP_CUTOFF_HZ}）")
    args = p.parse_args()
    signals = [s.strip() for s in args.signal.split(",") if s.strip()]
    _standalone(args.csv, signals, args.hp_cutoff)


if __name__ == "__main__":
    main()
