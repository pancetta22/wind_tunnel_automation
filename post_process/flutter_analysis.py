#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flutter_analysis.py  フラッター実験 後処理スクリプト

【入力フォルダ構造】
  WindyData/
  ├ 260620_flexible_ofst/          Pofst / Mofst（共有オフセット）
  │   ├ data/
  │   └ YYYYMMDD_experiment_log.json
  ├ 260620_flexible_c01/           風速条件①
  │   ├ data/
  │   └ YYYYMMDD_experiment_log.json  ← ofst_dir / rep_windspeed_U を含む
  └ 260620_flexible_c02/           風速条件②

【実行方法】
  # 全条件を一括処理（フラッター発生マップまで出力）
  python flutter_analysis.py --base_dir C:/WindyData/260620_flexible

  # 1条件だけ処理（途中確認用）
  python flutter_analysis.py --exp_dir C:/WindyData/260620_flexible_c01

【出力】
  Layer 1: 計測点ごとの詳細（各 cXX/figures/ フォルダ）
    - 時系列波形（生 / Pofst補正済み / 平均引き済みの3バージョン）
    - RMSの時間推移（LCO収束確認用）
    - PSDスペクトル（Welch法、Fy・Mz）

  Layer 2: 条件ごとのサマリー（各 cXX/ フォルダ）
    - flutter_summary.csv
      迎角, RMS_Fy, RMS_Mz, freq_Fy, freq_Mz, St_Fy, St_Mz,
      C_Fy_rms, C_Mz_rms, Ustar_Fy, Ustar_Mz, k_Fy, k_Mz, Re,
      flutter_A_Fy, flutter_A_Mz, flutter_B_Fy, flutter_B_Mz
      （A=振幅閾値判定, B=スペクトルピーク判定。C_*=変動空気力係数,
        Ustar_*=換算風速=1/St, k_*=換算周波数=π·St, Re=レイノルズ数）

  Layer 3: 全条件のマップ（--base_dir 指定時）
    - flutter_map_Fy.png / flutter_map_Mz.png
      風速×迎角のフラッター発生マップ
    - coeff_reduced_velocity.png
      変動空気力係数 C′ × 換算風速 U*（collapse=準定常 / 発散=フラッター臨界接近）

【フラッター判定の2ルート】
  ルートA（振幅閾値）: RMS > threshold_rms  [N] or [Nm]
  ルートB（スペクトルピーク）: 卓越ピークが背景レベルより peak_snr_db [dB] 以上高い

  threshold_rms と peak_snr_db は --threshold_rms / --peak_snr_db で変更可能。
  デフォルトは保守的な値にしてあるため、実験データを見てから調整する。
"""

import argparse
import datetime
import json
import os
import re
import sys
import traceback
import warnings
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import numpy as np
import pandas as pd
from scipy import interpolate, signal
import matplotlib
matplotlib.use("Agg")   # GUIなし環境でも動作
import matplotlib.pyplot as plt
from tqdm import tqdm

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

# 風速較正の定数・計算式は make_windspeed.py と完全一致させる必要があるため、
# 独自に再実装せずここから import する（同じ post_process/ ディレクトリにあり、
# Python がスクリプトのディレクトリを sys.path[0] に自動追加するため解決できる）。
from make_windspeed import WINDSPEED_DEFAULTS, mV_to_U as mv_to_U


# ============================================================
#  定数
# ============================================================
FS_TARGET   = 1200.0    # リサンプリング後のサンプリング周波数 [Hz]
HEADER_ROWS = 4         # Leptrino CSV のヘッダ行数
COL_NAMES   = ["t", "Fx", "Fy", "Fz", "Mx", "My", "Mz"]

# ストローハル数 St = f·L/U 用の代表長さ（翼弦長 [m]）。
# calc_force.py の c_chord と一致させる。
REF_LENGTH_M = 0.20

# 変動空気力係数 C = F/(½ρU²A) 用の代表面積 [m²]。calc_force.py の基準面積と一致させる。
REF_AREA_M2 = 0.04

# レイノルズ数 Re = U·c/ν 用の空気の動粘性 [m²/s]（≈15℃）。Re は非依存性チェック用の
# 副次量なので定数で足りる。将来 experiment_log の気温から精緻化する余地はある。
NU_AIR = 1.5e-5

# RMSのみ追加で算出する成分（Fy/Mz は既存ブロックで処理済み）
EXTRA_RMS_COMPS = ("Fx", "Fz", "Mx", "My")

# フラッター成分抽出用ハイパスフィルタ
HP_CUTOFF_HZ = 1.0      # 1 Hz 以下をDCドリフトとして除去

# 両端トリミング長 [秒]。リサンプリングの補間段差・ハイパス(sosfiltfilt)の端
# トランジェントが 0s/30s 付近に非物理的な大振幅スパイクを作るため、前処理後に
# 両端を一定秒だけ切り捨てる。1Hz 4次バターワースの過渡は 1 秒未満で十分減衰し、
# 30秒計測に対する損失は両端で 3.3% 程度なので RMS/PSD/LCO への実害はない。
EDGE_TRIM_SEC = 0.5

# LCO収束確認用の時間窓
RMS_WINDOW_SEC  = 1.0   # 窓幅 [秒]
RMS_OVERLAP     = 0.5   # オーバーラップ率

# WINDSPEED_DEFAULTS・mv_to_U は make_windspeed.py から import している
# （冒頭の import 文を参照。二重実装による乖離を避けるため）。


def windspeed_params_from_log(log):
    """experiment_log.json から風速計算パラメータ一式を取り出す。

    無い項目は WINDSPEED_DEFAULTS でフォールバックする。rho は rho_kg_m3。
    """
    return {
        "rho":           float(log.get("rho_kg_m3", 0.0)) or None,
        "water_density": float(log.get("water_density", WINDSPEED_DEFAULTS["water_density"])),
        "offset_mV":     float(log.get("volt_offset_mV", WINDSPEED_DEFAULTS["volt_offset_mV"])),
        "a":             float(log.get("calib_a", WINDSPEED_DEFAULTS["calib_a"])),
        "b":             float(log.get("calib_b", WINDSPEED_DEFAULTS["calib_b"])),
    }


def load_volt_summary_means(exp_dir, date_str):
    """条件フォルダの volt_summary.csv（Pdata/Mdata）から各点の平均差圧電圧を読む。

    volt_summary.csv は flutter_run_experiment.m が各計測点の平均差圧電圧
    （= _volt_raw.csv の平均）を書き出した正典。make_windspeed.py と同じ読み口
    （列 name / 差圧電圧[mV]、BOM付きUTF-8）で読み、_volt_raw を点ごとに開き直さない。

    Parameters
    ----------
    exp_dir  : str   条件フォルダ（volt_summary.csv が直下にある）
    date_str : str   実験日 YYYYMMDD（ファイル名 <date>_<phase>_volt_summary.csv 用）

    Returns
    -------
    dict  {short_name: mean_mV}   例 {"260608_Pdata_15.01": 1171.82, ...}
          ファイルが無ければその phase 分は空。
    """
    means = {}
    for phase in ("Pdata", "Mdata"):
        fpath = os.path.join(exp_dir, f"{date_str}_{phase}_volt_summary.csv")
        if not os.path.isfile(fpath):
            continue
        try:
            df = pd.read_csv(fpath, encoding="utf-8-sig")
        except Exception as e:
            warnings.warn(f"volt_summary の読み込みに失敗: {fpath}（{e}）")
            continue
        if "name" not in df.columns or "差圧電圧[mV]" not in df.columns:
            warnings.warn(f"volt_summary に想定列がありません: {fpath}")
            continue
        for _, row in df.iterrows():
            try:
                means[str(row["name"])] = float(row["差圧電圧[mV]"])
            except (ValueError, TypeError):
                continue
    return means


def mean_U_for_point(short_name, volt_means, ws_params):
    """計測点の平均差圧電圧（volt_summary 由来）から平均風速 [m/s] を返す。

    short_name（例 "260608_Pdata_15.01"）で volt_summary の平均電圧を引き、
    mv_to_U で風速へ変換する。電圧が無い・rho 不明なら NaN を返す。
    """
    if ws_params is None or ws_params.get("rho") in (None, 0.0):
        return float("nan")
    mv_mean = volt_means.get(short_name)
    if mv_mean is None:
        return float("nan")
    return mv_to_U(mv_mean, ws_params["rho"], ws_params["water_density"],
                   ws_params["offset_mV"], ws_params["a"], ws_params["b"])


# ============================================================
#  コマンドライン引数
# ============================================================
def parse_args():
    p = argparse.ArgumentParser(
        description="フラッター実験後処理スクリプト（Windy）"
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--base_dir", help="実験ベースフォルダ（_ofst / _c01 / _c02 ... の親）")
    grp.add_argument("--exp_dir",  help="単一条件フォルダ（_c01 など）")

    p.add_argument("--threshold_rms", type=float, default=None,
                   help="ルートA: フラッター判定のRMS閾値 [N or Nm]（デフォルト: 自動推定）")
    p.add_argument("--peak_snr_db",   type=float, default=10.0,
                   help="ルートB: ピークが背景より何dB高ければフラッターとみなすか（デフォルト: 10）")
    p.add_argument("--hp_cutoff",     type=float, default=HP_CUTOFF_HZ,
                   help=f"ハイパスフィルタのカットオフ周波数 [Hz]（デフォルト: {HP_CUTOFF_HZ}）")
    p.add_argument("--rms_window",    type=float, default=RMS_WINDOW_SEC,
                   help=f"LCO収束確認用の窓幅 [秒]（デフォルト: {RMS_WINDOW_SEC}）")
    p.add_argument("--edge_trim_sec", type=float, default=EDGE_TRIM_SEC,
                   help=f"前処理後に両端を切り捨てる長さ [秒]（補間段差・フィルタ"
                        f"端トランジェント除去。0で無効。デフォルト: {EDGE_TRIM_SEC}）")
    p.add_argument("--map_fmax",      type=float, default=50.0,
                   help="迎角×周波数マップの周波数表示上限 [Hz]（デフォルト: 50）")
    p.add_argument("--map_dyn_range", type=float, default=60.0,
                   help="迎角×周波数マップのカラー dB ダイナミックレンジ（デフォルト: 60）")
    p.add_argument("--st_clear_thk", type=float, default=0.2,
                   help="ストローハル数図で「明瞭なLCO」とみなす loop_thickness の上限"
                        "（--lco 併用時に強調表示。デフォルト: 0.2）")
    p.add_argument("--st_hide_noisy", action="store_true",
                   help="ストローハル数図（strouhal_fu）で loop_thickness が st_clear_thk を"
                        "超えるノイズ的な点を完全に非表示にする（--lco 併用時のみ有効）")

    # ---- 高速化オプション ----
    p.add_argument("--workers", type=int, default=None,
                   help="計測点処理の並列ワーカー数。1で従来同様の逐次実行（トラブル時の"
                        "切り分け用）。デフォルト: min(8, CPUコア数-2)。数値出力は"
                        "ワーカー数によらず同一")
    p.add_argument("--hq", action="store_true",
                   help="計測点ごとの図を従来品質で出力する（間引きなし・bbox_inches='tight'）。"
                        "既定は高速な軽量描画（min/max間引き。波形の外形・スパイクは保存される）")

    # ---- LCO（リミットサイクル振動）非線形動力学解析（オプトイン） ----
    p.add_argument("--lco", action="store_true",
                   help="LCO非線形解析（位相図・Poincaré・調和指標・成長率）を有効化")
    p.add_argument("--lco_signals", default="Fy,Mz",
                   help="LCO解析の主軸信号（カンマ区切り、デフォルト: Fy,Mz）")
    p.add_argument("--lco_tau_mode", choices=["zero_cross", "quarter_period"],
                   default="zero_cross",
                   help="時間遅れτの推定法（zero_cross=自己相関ゼロ交差 / quarter_period=1/4周期則）")
    p.add_argument("--lco_fmin", type=float, default=1.0,
                   help="LCO調和・ピーク解析の下限周波数 [Hz]（デフォルト: 1）")
    p.add_argument("--lco_fmax", type=float, default=500.0,
                   help="LCO調和・ピーク解析の上限周波数 [Hz]（デフォルト: 500）")
    p.add_argument("--lco_spec_aoa", default="",
                   help="風速版スペクトログラムの対象迎角（カンマ区切り。"
                        "未指定なら振幅最大の正側・負側を自動選択）")
    return p.parse_args()


# ============================================================
#  ユーティリティ
# ============================================================
def load_log(exp_dir):
    """experiment_log.json を読む。複数あれば最新を使う。"""
    logs = sorted(
        [f for f in os.listdir(exp_dir) if f.endswith("_experiment_log.json")]
    )
    if not logs:
        raise FileNotFoundError(f"experiment_log.json が見つかりません: {exp_dir}")
    path = os.path.join(exp_dir, logs[-1])
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_csv(path):
    """Leptrino CSV（CP932・4行ヘッダ）を読んで DataFrame を返す。"""
    df = pd.read_csv(
        path, skiprows=HEADER_ROWS, names=COL_NAMES,
        encoding="cp932", dtype=float
    )
    return df.dropna().reset_index(drop=True)


def angle_from_name(fname):
    """ファイル名から (ref_angle, suffix) を取り出す。
    例: 20260620_123456_260620_Pdata_15.01.csv → (15, 1)
    """
    m = re.search(r"_(\d+)\.(\d{2})\.csv$", fname)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def phase_sign(fname):
    """Pdata → +1, Mdata → -1"""
    return -1 if "_Mdata_" in fname else +1


def _short_name(fname):
    """フルファイル名から短縮名を取り出す。
    例: 20260620_123456_260620_Pdata_15.01.csv → 260620_Pdata_15.01
    """
    return re.sub(r"^.*?_(\d{6}_(?:P|M)data_\d+\.\d{2})\.csv$", r"\1",
                  os.path.basename(fname))


def resample_uniform(t, x, fs=FS_TARGET):
    """不均一タイムスタンプの時系列を均一グリッドへスプライン補間する。

    センサ実レート（≈1209 Hz）とPCタイマの差によるビート周波数偽ピークを
    防ぐためのリサンプリング（添付資料の指摘事項への対処）。

    PCタイマの分解能不足でタイムスタンプが重複・非単調になることがあるため、
    cubic補間（x が狭義単調増加であることを要求）の前に重複点を除去する。

    重複除去後の点数が cubic 補間の次数（4点必要）に満たない場合は
    ValueError を送出する。呼び出し側で短すぎるデータ点をスキップする
    判断ができるようにするため。

    x は 1D（1成分）のほか、(n, m) の 2D も受け付ける（m 成分をまとめて
    1回のスプライン構築で補間。数値は成分ごとに1Dで呼んだ場合と同一）。
    """
    # 狭義単調増加になるよう、t が増加しない点を除外（最初の出現を残す）
    keep = np.concatenate(([True], np.diff(t) > 0))
    if not keep.all():
        t = t[keep]
        x = x[keep]

    if len(t) < 4:
        raise ValueError(
            f"resample_uniform: 重複除去後の点数が不足しています（{len(t)} 点、cubic 補間には4点以上必要）"
        )

    t_uniform = np.arange(t[0], t[-1], 1.0 / fs)
    f_interp  = interpolate.interp1d(t, x, kind="cubic", bounds_error=False,
                                      fill_value="extrapolate", axis=0)
    return t_uniform, f_interp(t_uniform)


def decimate_minmax(t, x, n_bins=3000):
    """描画専用の min/max エンベロープ間引き。

    時系列をビン分割し、各ビンの最小値・最大値を折れ線として並べる。
    波形の外形・スパイクを保存したまま点数を 2*n_bins に落とす
    （3.5万点 → 6000点で描画コストを大幅削減。見た目はほぼ同一）。
    数値解析には使わないこと（描画専用）。
    """
    t = np.asarray(t)
    x = np.asarray(x)
    n = len(x)
    if n <= 2 * n_bins:
        return t, x
    edges  = np.linspace(0, n, n_bins + 1).astype(int)
    starts = edges[:-1]
    mins = np.minimum.reduceat(x, starts)
    maxs = np.maximum.reduceat(x, starts)
    t_mid = t[np.minimum((starts + edges[1:]) // 2, n - 1)]
    out_t = np.repeat(t_mid, 2)
    out_x = np.empty(2 * n_bins, dtype=float)
    out_x[0::2] = mins
    out_x[1::2] = maxs
    return out_t, out_x


def highpass(x, cutoff_hz, fs=FS_TARGET, order=4):
    """ハイパスフィルタ（DCドリフト除去）。"""
    sos = signal.butter(order, cutoff_hz, fs=fs, btype="high", output="sos")
    return signal.sosfiltfilt(sos, x)


def calc_psd(x, fs=FS_TARGET, nperseg=2048):
    """Welch 法で PSD を推定する。"""
    freqs, psd = signal.welch(x, fs=fs, nperseg=min(nperseg, len(x)))
    return freqs, psd


def psd_on_grid(freqs, psd, ref_freqs):
    """psd を ref_freqs の周波数グリッド上へ線形補間する。

    calc_psd は nperseg=min(2048, len(x)) のため、信号長が短い計測点が
    1つでも混じると freqs の長さ・刻みが他の計測点と変わる。
    build_aoa_freq_grid / build_speed_freq_grid は複数計測点の PSD を
    np.vstack で束ねるため、長さが揃っていないと例外になる。
    束ねる直前に全て ref_freqs（代表として最初の計測点の freqs）へ
    揃えることで、この暗黙の前提を明示的なガードに変える。
    """
    freqs = np.asarray(freqs)
    if freqs.shape == np.asarray(ref_freqs).shape and np.allclose(freqs, ref_freqs):
        return psd
    return np.interp(ref_freqs, freqs, psd, left=0.0, right=0.0)


def dominant_freq(freqs, psd, fmin=1.0, fmax=500.0):
    """指定周波数範囲内でパワーが最大の周波数を返す。"""
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return np.nan
    idx = np.argmax(psd[mask])
    return freqs[mask][idx]


def strouhal(freq, U, L=REF_LENGTH_M):
    """卓越周波数 freq [Hz]・風速 U [m/s]・代表長さ L [m] から St = f·L/U を返す。"""
    if U is None or not np.isfinite(U) or U <= 0 or not np.isfinite(freq):
        return np.nan
    return freq * L / U


def reduced_velocity(freq, U, L=REF_LENGTH_M):
    """換算風速 U* = U/(f·L)（= 1/St）を返す。1周期あたり流れが翼弦の何倍進むか。

    空力弾性で標準的な横軸。U/f が無効（U≤0・非有限、f≤0・非有限）なら NaN。
    """
    if (U is None or not np.isfinite(U) or U <= 0
            or not np.isfinite(freq) or freq <= 0):
        return np.nan
    return U / (freq * L)


def reduced_frequency(freq, U, L=REF_LENGTH_M):
    """換算周波数 k = π·f·L/U（= π·St、半弦 b=L/2 基準）を返す。

    St から自明だが慣例で併記する。ガードは strouhal と同一。
    """
    st = strouhal(freq, U, L)
    return np.pi * st if np.isfinite(st) else np.nan


def force_coefficient(rms, U, rho, area=REF_AREA_M2, lever=1.0):
    """変動空気力係数 C = rms/(½·ρ·U²·area·lever) を返す。

    力係数なら lever=1、モーメント係数なら lever=代表長さ（例 翼弦 c）を渡す。
    動圧を作れない条件（ρ が None/非有限/≤0、U≤0・非有限）は NaN。
    """
    if (rho is None or not np.isfinite(rho) or rho <= 0
            or U is None or not np.isfinite(U) or U <= 0
            or not np.isfinite(rms)):
        return np.nan
    return rms / (0.5 * rho * U ** 2 * area * lever)


def reynolds(U, L=REF_LENGTH_M, nu=NU_AIR):
    """レイノルズ数 Re = U·L/ν を返す。U が無効なら NaN。"""
    if U is None or not np.isfinite(U) or U <= 0:
        return np.nan
    return U * L / nu


def flutter_judge_A(rms, threshold):
    """ルートA: RMS が閾値を超えたらフラッター有。"""
    if threshold is None:
        return None   # 閾値未設定 → 判定保留
    return int(rms > threshold)


def flutter_judge_B(freqs, psd, snr_db, fmin=1.0, fmax=500.0):
    """ルートB: 卓越ピークが背景レベルより snr_db [dB] 以上高ければフラッター有。

    背景レベル = 全周波数帯のメジアン（ロバスト推定）。
    """
    mask = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(mask):
        return 0
    psd_band   = psd[mask]
    peak_power = np.max(psd_band)
    bg_power   = np.median(psd_band)
    if bg_power <= 0:
        return 0
    snr = 10 * np.log10(peak_power / bg_power)
    return int(snr >= snr_db)


def rms_timeseries(x, fs, window_sec, overlap):
    """時系列を窓分割してRMSを計算する（LCO収束確認用）。

    Returns
    -------
    t_centers : 各窓の中心時刻 [秒]
    rms_vals  : 各窓のRMS値
    """
    n_window = int(window_sec * fs)
    n_step   = int(n_window * (1 - overlap))
    n_step   = max(n_step, 1)

    centers, rms_vals = [], []
    i = 0
    while i + n_window <= len(x):
        window = x[i:i + n_window]
        centers.append((i + n_window / 2) / fs)
        rms_vals.append(np.sqrt(np.mean(window ** 2)))
        i += n_step

    return np.array(centers), np.array(rms_vals)


# ============================================================
#  オフセット（Pofst / Mofst）の読み込み
# ============================================================
def load_ofst_means(ofst_dir):
    """Pofst・Mofst の各計測点平均値を辞書で返す。

    Returns
    -------
    ofst : dict  {short_name: {"Fx": float, "Fy": float, "Fz": float,
                               "Mx": float, "My": float, "Mz": float}}
            6成分すべての平均値を保持する。
            例: {"260620_Pofst_15.01": {"Fy": -2.31, "Mz": 0.012, ...}, ...}
    """
    data_dir = os.path.join(ofst_dir, "data")
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"ofst/data フォルダが見つかりません: {data_dir}")

    ofst = {}
    files = sorted(f for f in os.listdir(data_dir)
                   if f.endswith(".csv") and not f.endswith("_volt_raw.csv"))

    for fname in files:
        ref_angle, suffix = angle_from_name(fname)
        if ref_angle is None:
            continue
        # short_name を再構築（後でPdataのファイル名と照合するため）
        # 例: 260620_Pofst_15.01
        m = re.search(r"(\d{6})_((?:P|M)ofst)_(\d+\.\d{2})\.csv$", fname)
        if not m:
            continue
        short = f"{m.group(1)}_{m.group(2)}_{m.group(3)}"

        df = load_csv(os.path.join(data_dir, fname))
        ofst[short] = {
            "Fx": float(df["Fx"].mean()),
            "Fy": float(df["Fy"].mean()),
            "Fz": float(df["Fz"].mean()),
            "Mx": float(df["Mx"].mean()),
            "My": float(df["My"].mean()),
            "Mz": float(df["Mz"].mean()),
        }

    print(f"[オフセット] {len(ofst)} 計測点を読み込みました（{ofst_dir}）")
    return ofst


def find_ofst_key(data_fname, ofst, phase):
    """Pdata/Mdata のファイル名に対応するオフセットキーを探す。

    Pdata → Pofst、Mdata → Mofst の同じ ref_angle / suffix を使う。
    """
    ref_angle, suffix = angle_from_name(data_fname)
    if ref_angle is None:
        return None

    # yymmdd はファイル名の3トークン目
    m = re.search(r"_(\d{6})_(?:P|M)data_", data_fname)
    if not m:
        return None
    yymmdd = m.group(1)

    ofst_phase = "Pofst" if phase == "Pdata" else "Mofst"
    key = f"{yymmdd}_{ofst_phase}_{ref_angle:02d}.{suffix:02d}"
    return key if key in ofst else None


# ============================================================
#  1計測点の処理
# ============================================================
def process_one_point(csv_path, ofst, phase, args, fig_dir, case_name="", rep_U=None,
                      ws_params=None, volt_means=None):
    """1つの6軸CSVを処理して結果辞書を返す。

    Returns
    -------
    dict with keys:
        ref_angle, suffix, aoa,
        rms_Fy_raw, rms_Mz_raw,          # Pofst補正のみ（平均引かず）
        rms_Fy, rms_Mz,                   # Pofst補正 + 平均引き（フラッター成分）
        freq_Fy, freq_Mz,                 # 卓越周波数 [Hz]
        flutter_A_Fy, flutter_A_Mz,       # ルートA判定
        flutter_B_Fy, flutter_B_Mz,       # ルートB判定
    """
    fname     = os.path.basename(csv_path)
    ref_angle, suffix = angle_from_name(fname)
    sign      = phase_sign(fname)
    aoa       = sign * ref_angle   # 実際の迎角（負迎角は負値）

    df = load_csv(csv_path)
    if len(df) < 100:
        warnings.warn(f"データ不足: {fname}（{len(df)} 行）")
        return None

    t  = df["t"].values

    # ---- Pofst / Mofst によるオフセット補正（6成分まとめて） ----
    comp_names = ["Fy", "Mz"] + list(EXTRA_RMS_COMPS)
    X = df[comp_names].values          # (n, 6)
    ofst_key = find_ofst_key(fname, ofst, phase)
    if ofst_key:
        X = X - np.array([ofst[ofst_key][c] for c in comp_names])
    else:
        warnings.warn(f"オフセットキーが見つかりません: {fname}")

    # ---- 均一グリッドへリサンプリング ----
    # 6成分を (n,6) のまま渡し、スプライン構築を1回で済ませる（数値は
    # 成分ごとに呼んだ場合と同一）。
    # len(df)>=100 でも重複タイムスタンプ除去後に4点未満まで減ることがあり、
    # その場合 resample_uniform が ValueError を送出する。他の点の処理を
    # 止めないよう、この1点だけ warning でスキップする。
    try:
        t_u, X_u = resample_uniform(t, X)
    except ValueError as e:
        warnings.warn(f"リサンプリング失敗のためスキップ: {fname}（{e}）")
        return None
    Fy_u = X_u[:, 0]
    Mz_u = X_u[:, 1]

    # ---- 両端トリミングの範囲を決定 ----
    # 補間段差・ハイパス端トランジェントが 0s/30s 付近に作る非物理的スパイクを
    # 除去するため、前処理（平均引き→ハイパス）後の信号から両端を切り捨てる。
    # ハイパスは全長で先にかけ（フィルタの端効果を端側に押し込んでから切る）、
    # その後に共通インデックス [trim:-trim] でスライスして整合させる。
    trim = int(getattr(args, "edge_trim_sec", EDGE_TRIM_SEC) * FS_TARGET)
    if trim <= 0 or 2 * trim >= len(t_u):
        trim = 0   # 無効化（短すぎるデータで全消ししない安全弁）
    sl = slice(trim, len(t_u) - trim) if trim > 0 else slice(None)

    # ---- 平均引き済み（フラッター成分）→ ハイパス → 両端トリム ----
    Fy_hp = highpass(Fy_u - np.mean(Fy_u), args.hp_cutoff)[sl]
    Mz_hp = highpass(Mz_u - np.mean(Mz_u), args.hp_cutoff)[sl]

    # ---- RMS（Pofst補正のみ・平均引かず） ----
    # ハイパスはトリム前の全長でかけ（端効果を端側に押し込む）、同じ sl で切る。
    # 平均は元データ先頭1秒（トリム前）で取る。Fy_u を上書きする前に算出する。
    Fy_raw_hp = highpass(Fy_u - np.mean(Fy_u[:int(FS_TARGET)]), args.hp_cutoff)[sl]
    Mz_raw_hp = highpass(Mz_u - np.mean(Mz_u[:int(FS_TARGET)]), args.hp_cutoff)[sl]

    # 以降の時系列・プロットもトリム後の区間に揃える
    t_u  = t_u[sl]
    Fy_u = Fy_u[sl]
    Mz_u = Mz_u[sl]

    # ---- RMS（フラッター成分） ----
    rms_Fy = float(np.sqrt(np.mean(Fy_hp ** 2)))
    rms_Mz = float(np.sqrt(np.mean(Mz_hp ** 2)))

    # ---- 追加成分のRMS（Fx/Fz/Mx/My：RMSのみ） ----
    # Fy/Mz と同じ処理（オフセット補正→リサンプリング→平均引き→ハイパス→トリム→RMS）。
    # オフセット補正・リサンプリングは冒頭の6成分一括処理（X_u）で済んでいる。
    rms_extra = {}
    for k, comp in enumerate(EXTRA_RMS_COMPS):
        x_u  = X_u[:, 2 + k]
        x_hp = highpass(x_u - np.mean(x_u), args.hp_cutoff)[sl]
        rms_extra[f"rms_{comp}"] = float(np.sqrt(np.mean(x_hp ** 2)))

    # ---- raw版RMS（Fy_raw_hp/Mz_raw_hp は上で算出済み） ----
    rms_Fy_raw = float(np.sqrt(np.mean(Fy_raw_hp ** 2)))
    rms_Mz_raw = float(np.sqrt(np.mean(Mz_raw_hp ** 2)))

    # ---- PSD（Welch法） ----
    freqs_Fy, psd_Fy = calc_psd(Fy_hp)
    freqs_Mz, psd_Mz = calc_psd(Mz_hp)

    freq_Fy = dominant_freq(freqs_Fy, psd_Fy)
    freq_Mz = dominant_freq(freqs_Mz, psd_Mz)

    # ---- フラッター判定 ----
    f_A_Fy = flutter_judge_A(rms_Fy, args.threshold_rms)
    f_A_Mz = flutter_judge_A(rms_Mz, args.threshold_rms)
    f_B_Fy = flutter_judge_B(freqs_Fy, psd_Fy, args.peak_snr_db)
    f_B_Mz = flutter_judge_B(freqs_Mz, psd_Mz, args.peak_snr_db)

    # ---- LCO収束確認（RMSの時間推移） ----
    t_rms_Fy, rms_t_Fy = rms_timeseries(Fy_hp, FS_TARGET, args.rms_window, RMS_OVERLAP)
    t_rms_Mz, rms_t_Mz = rms_timeseries(Mz_hp, FS_TARGET, args.rms_window, RMS_OVERLAP)

    # ---- グラフ出力 ----
    short = _short_name(fname)
    _plot_point(fig_dir, short, t_u, Fy_u, Mz_u, Fy_hp, Mz_hp,
                freqs_Fy, psd_Fy, freqs_Mz, psd_Mz,
                t_rms_Fy, rms_t_Fy, t_rms_Mz, rms_t_Mz, aoa,
                case_name=case_name, rep_U=rep_U,
                hq=getattr(args, "hq", False))

    # ---- この計測点の平均風速（volt_summary の平均差圧電圧から算出） ----
    # 代表風速（各条件の冒頭1点）ではなく、当該計測データ全体の平均を使う。
    mean_U = mean_U_for_point(short, volt_means or {}, ws_params)

    # ---- 第1層の無次元数（変動空気力係数・換算風速・換算周波数・Re） ----
    # 動圧 ½ρU² で振幅を正規化し、風速をまたいで応答を比較できるようにする。
    # ρ は experiment_log 由来（ws_params["rho"]）。ρ・U が無効なら各ヘルパが NaN を返す。
    rho_air = ws_params.get("rho") if ws_params else None
    C_Fy_rms = force_coefficient(rms_Fy, mean_U, rho_air)                   # 力係数（lever=1）
    C_Mz_rms = force_coefficient(rms_Mz, mean_U, rho_air, lever=REF_LENGTH_M)  # モーメント係数

    return {
        "ref_angle":    ref_angle,
        "suffix":       suffix,
        "aoa":          aoa,
        "mean_U":       mean_U,
        "rms_Fy_raw":   rms_Fy_raw,
        "rms_Mz_raw":   rms_Mz_raw,
        "rms_Fy":       rms_Fy,
        "rms_Mz":       rms_Mz,
        # 追加成分のRMS（Fx/Fz/Mx/My）。flutter_summary.csv に自動で列追加される
        **rms_extra,
        "freq_Fy":      freq_Fy,
        "freq_Mz":      freq_Mz,
        "St_Fy":        strouhal(freq_Fy, mean_U),
        "St_Mz":        strouhal(freq_Mz, mean_U),
        # 第1層の無次元数（振幅の無次元化・標準的な換算量）
        "C_Fy_rms":     C_Fy_rms,                          # 変動力係数
        "C_Mz_rms":     C_Mz_rms,                          # 変動モーメント係数
        "Ustar_Fy":     reduced_velocity(freq_Fy, mean_U),  # 換算風速（=1/St）
        "Ustar_Mz":     reduced_velocity(freq_Mz, mean_U),
        "k_Fy":         reduced_frequency(freq_Fy, mean_U),  # 換算周波数（=π·St）
        "k_Mz":         reduced_frequency(freq_Mz, mean_U),
        "Re":           reynolds(mean_U),                   # レイノルズ数（副次量）
        "flutter_A_Fy": f_A_Fy,
        "flutter_A_Mz": f_A_Mz,
        "flutter_B_Fy": f_B_Fy,
        "flutter_B_Mz": f_B_Mz,
        # 迎角×周波数マップ構築用（CSV へは書き出さない一時キー）
        "_psd_freqs":   freqs_Fy,   # freqs_Fy と freqs_Mz は同一グリッド
        "_psd_Fy":      psd_Fy,
        "_psd_Mz":      psd_Mz,
        # LCO非線形解析用（--lco 時のみ使用。CSV へは書き出さない一時キー）
        "_t":           t_u,
        "_sig_Fy":      Fy_hp,
        "_sig_Mz":      Mz_hp,
    }


# ============================================================
#  グラフ出力（1計測点）
# ============================================================
def _plot_point(fig_dir, short, t_u, Fy_u, Mz_u, Fy_hp, Mz_hp,
                freqs_Fy, psd_Fy, freqs_Mz, psd_Mz,
                t_rms_Fy, rms_t_Fy, t_rms_Mz, rms_t_Mz, aoa,
                case_name="", rep_U=None, hq=False):

    os.makedirs(fig_dir, exist_ok=True)

    # 軽量モード（既定）: 時系列を min/max エンベロープで間引いて描画コストを削減。
    # --hq 時は従来どおり全点を描く。
    if hq:
        dec = lambda t, x: (t, x)
    else:
        plt.rcParams["path.simplify_threshold"] = 1.0
        dec = decimate_minmax

    title = f"AoA = {aoa:+d}°"
    if case_name:
        title += f"   {case_name}"
    if rep_U is not None:
        title += f"   U ≈ {rep_U:.2f} m/s"
    title += f"   ({short})"

    fig, axes = plt.subplots(3, 2, figsize=(14, 10))
    fig.suptitle(title, fontsize=13)

    # --- 上段: 時系列（オフセット補正済み・平均引き済み） ---
    axes[0, 0].plot(*dec(t_u, Fy_u), lw=0.5, color="steelblue", alpha=0.7, label="Pofst-corrected")
    axes[0, 0].plot(*dec(t_u, Fy_hp), lw=0.8, color="red",      alpha=0.9, label="Flutter comp. (HP)")
    axes[0, 0].set_xlabel("Time [s]"); axes[0, 0].set_ylabel("Fy [N]")
    axes[0, 0].set_title("Fy time series"); axes[0, 0].legend(fontsize=8); axes[0, 0].grid(True)

    axes[0, 1].plot(*dec(t_u, Mz_u), lw=0.5, color="steelblue", alpha=0.7, label="Pofst-corrected")
    axes[0, 1].plot(*dec(t_u, Mz_hp), lw=0.8, color="red",      alpha=0.9, label="Flutter comp. (HP)")
    axes[0, 1].set_xlabel("Time [s]"); axes[0, 1].set_ylabel("Mz [Nm]")
    axes[0, 1].set_title("Mz time series"); axes[0, 1].legend(fontsize=8); axes[0, 1].grid(True)

    # --- 中段: PSD ---
    axes[1, 0].semilogy(freqs_Fy, psd_Fy, color="steelblue", lw=1.0)
    axes[1, 0].set_xlabel("Frequency [Hz]"); axes[1, 0].set_ylabel("PSD [N²/Hz]")
    axes[1, 0].set_title("Fy PSD (Welch)"); axes[1, 0].set_xlim(0, 200); axes[1, 0].grid(True)

    axes[1, 1].semilogy(freqs_Mz, psd_Mz, color="steelblue", lw=1.0)
    axes[1, 1].set_xlabel("Frequency [Hz]"); axes[1, 1].set_ylabel("PSD [Nm²/Hz]")
    axes[1, 1].set_title("Mz PSD (Welch)"); axes[1, 1].set_xlim(0, 200); axes[1, 1].grid(True)

    # --- 下段: RMS時間推移（LCO収束確認） ---
    axes[2, 0].plot(t_rms_Fy, rms_t_Fy, marker="o", ms=3, color="tomato", lw=1.2)
    axes[2, 0].set_xlabel("Time [s]"); axes[2, 0].set_ylabel("RMS [N]")
    axes[2, 0].set_title(f"Fy RMS trend (window={RMS_WINDOW_SEC}s)"); axes[2, 0].grid(True)

    axes[2, 1].plot(t_rms_Mz, rms_t_Mz, marker="o", ms=3, color="tomato", lw=1.2)
    axes[2, 1].set_xlabel("Time [s]"); axes[2, 1].set_ylabel("RMS [Nm]")
    axes[2, 1].set_title(f"Mz RMS trend (window={RMS_WINDOW_SEC}s)"); axes[2, 1].grid(True)

    fig.tight_layout()
    # --hq 時のみ bbox_inches="tight"（再レイアウトが重いため軽量モードでは省く）
    save_kw = {"bbox_inches": "tight"} if hq else {}
    fig.savefig(os.path.join(fig_dir, f"{short}.png"), dpi=120, **save_kw)
    plt.close(fig)


# ============================================================
#  計測点処理の並列実行
# ============================================================
def effective_workers(args):
    """--workers の実効値を返す。未指定なら min(8, CPUコア数-2)。"""
    w = getattr(args, "workers", None)
    if w is not None:
        return max(1, int(w))
    return min(8, max(1, (os.cpu_count() or 2) - 2))


def _limit_blas_threads():
    """ワーカー並列と BLAS 内部スレッドの掛け算でコアを取り合わないよう、
    数値ライブラリのスレッド数を1に制限する。

    spawn される子プロセスへ環境変数として継承される。ユーザーが明示的に
    設定済みの値は尊重する（setdefault）。
    """
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")


class _executor_context:
    """--workers に応じて ProcessPoolExecutor を with 文で貸し出す（実効1なら None=逐次）。

    ワーカープロセスの異常死（BrokenProcessPool）はここで捕捉し、
    `--workers 1` での再実行を促すメッセージを添えて再送出する。
    """

    def __init__(self, args):
        self.n = effective_workers(args)
        self.executor = None

    def __enter__(self):
        if self.n > 1:
            _limit_blas_threads()
            print(f"[並列] {self.n} ワーカーで計測点を処理します"
                  f"（--workers 1 で従来同様の逐次実行に戻せます）")
            self.executor = ProcessPoolExecutor(max_workers=self.n)
        return self.executor

    def __exit__(self, exc_type, exc, tb):
        if self.executor is not None:
            self.executor.shutdown(wait=True, cancel_futures=True)
        if exc_type is not None and issubclass(exc_type, BrokenProcessPool):
            print("[エラー] 並列ワーカーが異常終了しました。"
                  " `--workers 1` を付けて逐次実行で再実行してください。",
                  file=sys.stderr)
        return False


def _point_worker(task):
    """1計測点ぶんの処理（process_one_point + --lco 時は LCO 解析・カルテ図）。

    ProcessPoolExecutor のワーカーとしても、逐次実行（--workers 1）からも
    同じ関数を呼ぶ（並列の有無で数値・挙動が変わらないことを保証するため）。

    例外・警告はワーカー内で捕捉して文字列で返し、メイン側で表示する
    （1点の失敗で全体を止めない。tqdm 表示との混線も避ける）。

    Returns
    -------
    (index, result, spec_row, lco_row, messages)
        result が None の場合はこの点をスキップ（理由は messages に入る）。
    """
    (index, csv_path, ofst, phase, args, fig_dir,
     case_name, rep_U, ws_params, volt_means) = task
    fname = os.path.basename(csv_path)
    msgs = []
    result = spec_row = lco_row = None
    try:
        with warnings.catch_warnings(record=True) as wlist:
            warnings.simplefilter("always")
            result = process_one_point(
                csv_path, ofst, phase, args, fig_dir,
                case_name=case_name, rep_U=rep_U, ws_params=ws_params,
                volt_means=volt_means
            )
            if result is not None:
                # スペクトル配列は DataFrame に混ぜず別リストへ退避
                spec_row = {
                    "aoa":    result["aoa"],
                    "mean_U": result["mean_U"],
                    "freqs":  result.pop("_psd_freqs"),
                    "psd_Fy": result.pop("_psd_Fy"),
                    "psd_Mz": result.pop("_psd_Mz"),
                }
                # 補正済み信号は --lco 有無に関わらずワーカー内で消費して捨てる
                # （大配列を親プロセスへ pickle 転送しない）
                sig_t = result.pop("_t")
                sigs  = {"Fy": result.pop("_sig_Fy"), "Mz": result.pop("_sig_Mz")}
                if args.lco:
                    # 遅延 import（lco_analysis が flutter_analysis を import する
                    # ため、トップレベル import だと循環参照になる）
                    import lco_analysis
                    lco_res = lco_analysis.analyze_point(
                        sig_t, sigs, result["aoa"],
                        short=_short_name(fname), fig_dir=fig_dir, args=args,
                        case_name=case_name, rep_U=rep_U
                    )
                    # 指標を summary 行へマージ（自動列追加。自動ラベルは付けない）
                    result.update(lco_res["metrics"])
                    lco_row = lco_res["row"]
        msgs.extend(str(w.message) for w in wlist)
    except Exception:
        result = spec_row = lco_row = None
        msgs.append(f"処理失敗のためスキップ: {fname}\n{traceback.format_exc()}")
    return index, result, spec_row, lco_row, msgs


# ============================================================
#  Layer 2: 1条件の処理
# ============================================================
def process_one_condition(exp_dir, ofst, args, executor=None):
    """1つの風速条件フォルダ（_c01 など）を処理してサマリーCSVを出力する。"""

    log = load_log(exp_dir)
    rep_U   = log.get("rep_windspeed_U",  0.0)
    rep_mv  = log.get("rep_windspeed_mV", 0.0)
    ofst_dir_log = log.get("ofst_dir", "")
    case_name = os.path.basename(exp_dir)

    # 計測点ごとの平均風速を算出するためのパラメータ（volt_summary の平均電圧を使う）
    ws_params = windspeed_params_from_log(log)

    print(f"\n[条件] {case_name}  U ≈ {rep_U:.2f} m/s  ({rep_mv:.1f} mV)")

    data_dir = os.path.join(exp_dir, "data")
    fig_dir  = os.path.join(exp_dir, "figures")
    os.makedirs(fig_dir, exist_ok=True)

    files = sorted(
        f for f in os.listdir(data_dir)
        if f.endswith(".csv") and not f.endswith("_volt_raw.csv")
        and ("_Pdata_" in f or "_Mdata_" in f)
    )

    if not files:
        print(f"  [警告] Pdata/Mdata の CSV が見つかりません: {data_dir}")
        return None

    # 条件フォルダの volt_summary.csv（Pdata/Mdata）から各点の平均差圧電圧を一括読み。
    # date_str はデータファイル名の先頭 YYYYMMDD から取る（log["date"] は YYMMDD のことがある）。
    m_date = re.match(r"(\d{8})_", files[0])
    date_str = m_date.group(1) if m_date else str(log.get("date", ""))
    volt_means = load_volt_summary_means(exp_dir, date_str)

    tasks = [
        (i, os.path.join(data_dir, fname),
         ofst, "Pdata" if "_Pdata_" in fname else "Mdata", args, fig_dir,
         case_name, rep_U, ws_params, volt_means)
        for i, fname in enumerate(files)
    ]

    # index -> (result, spec_row, lco_row) で受け取り、元のファイル順に復元してから
    # rows / spec_rows / lco_rows へ積む（--workers の値によらず出力が同一になるようにする）。
    ordered = [None] * len(tasks)
    desc = f"  {os.path.basename(exp_dir)}"
    if executor is not None:
        for index, result, spec_row, lco_row, msgs in tqdm(
            executor.map(_point_worker, tasks), total=len(tasks), desc=desc, ncols=70
        ):
            for m in msgs:
                tqdm.write(f"  [警告] {m}")
            ordered[index] = (result, spec_row, lco_row)
    else:
        for task in tqdm(tasks, desc=desc, ncols=70):
            index, result, spec_row, lco_row, msgs = _point_worker(task)
            for m in msgs:
                tqdm.write(f"  [警告] {m}")
            ordered[index] = (result, spec_row, lco_row)

    rows = []
    spec_rows = []   # 迎角×周波数マップ用のスペクトル配列
    lco_rows  = []   # LCO非線形解析（--lco 時のみ）の結果・中間生成物
    for result, spec_row, lco_row in ordered:
        if result is None:
            continue
        spec_rows.append(spec_row)
        if lco_row is not None:
            lco_rows.append(lco_row)
        result["rep_windspeed_U"]  = rep_U
        result["rep_windspeed_mV"] = rep_mv
        rows.append(result)

    if not rows:
        return None

    df = pd.DataFrame(rows).sort_values("aoa", kind="stable").reset_index(drop=True)
    out_path = os.path.join(exp_dir, "flutter_summary.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"  → {out_path} を保存しました（{len(df)} 点）")

    # 迎角×周波数マップ（fig10風スペクトログラム）
    if spec_rows:
        plot_aoa_freq_map(spec_rows, exp_dir, rep_U, args, case_name=case_name)

    # St–迎角プロット（この1風速条件でのストローハル数の迎角依存）
    plot_strouhal_aoa(df, exp_dir, rep_U, args, case_name=case_name)

    # LCO: 迎角に沿った位相図スイープ（--lco 時のみ。遅延 import は
    # lco_analysis が flutter_analysis を import する循環参照を避けるため）
    if args.lco and lco_rows:
        import lco_analysis
        lco_analysis.plot_phase_sweep(lco_rows, exp_dir, rep_U, args)

    return df, spec_rows, lco_rows


# ============================================================
#  Layer 1.5: 迎角×周波数マップ（fig10風スペクトログラム）
# ============================================================
def build_aoa_freq_grid(spec_rows, key, args):
    """spec_rows から迎角×周波数の dB グリッドと卓越周波数線を構築する。

    Parameters
    ----------
    spec_rows : list of {"aoa": int, "freqs": ndarray,
                         "psd_Fy": ndarray, "psd_Mz": ndarray}
    key       : "psd_Fy" or "psd_Mz"

    Returns
    -------
    aoa_axis : ndarray   迎角軸（昇順・重複統合済み）
    f_plot   : ndarray   周波数軸（0〜map_fmax）
    Z_db     : ndarray   [freq, aoa] の dB 値（全体最大を 0 dB に正規化）
    peak_aoa, peak_freq : list  各迎角の卓越周波数
    None を返す場合はデータが空 or 全ゼロ。
    """
    if not spec_rows:
        return None

    # 迎角でソート。重複迎角（aoa=0 が Pdata/Mdata で2点など）は PSD を平均して統合
    by_aoa = {}
    freqs  = spec_rows[0]["freqs"]
    for r in spec_rows:
        by_aoa.setdefault(r["aoa"], []).append(r)
    aoa_axis = np.array(sorted(by_aoa.keys()))

    fmask  = freqs <= args.map_fmax
    f_plot = freqs[fmask]

    # Z[freq, aoa] を構築（重複迎角は平均）。
    # 各計測点の freqs は基本的に全点同一グリッドだが、信号長が短い点が
    # 混じっていると calc_psd の nperseg=min(2048,len(x)) により長さ・刻みが
    # 変わることがある。vstack で束ねる前に代表グリッド（freqs）へ揃える。
    Z = np.empty((f_plot.size, aoa_axis.size))
    for j, a in enumerate(aoa_axis):
        cols = [psd_on_grid(r["freqs"], r[key], freqs)[fmask] for r in by_aoa[a]]
        Z[:, j] = np.vstack(cols).mean(axis=0)

    zmax = Z.max()
    if zmax <= 0:
        return None
    Z_db = 10.0 * np.log10(np.maximum(Z, zmax * 1e-12) / zmax)
    Z_db = np.clip(Z_db, -args.map_dyn_range, 0.0)

    # 卓越周波数線（各迎角・表示範囲内）
    peak_aoa, peak_freq = [], []
    for j, a in enumerate(aoa_axis):
        col = Z[:, j]
        if np.any(col > 0):
            peak_aoa.append(a)
            peak_freq.append(f_plot[np.argmax(col)])

    return aoa_axis, f_plot, Z_db, peak_aoa, peak_freq


def build_speed_freq_grid(panel_data, target_aoa, key, args):
    """指定迎角について、風速×周波数の dB グリッドを構築する（風速版スペクトログラム）。

    Trickey et al. (2002) の fig.8（流速×周波数スペクトログラム）に対応。
    build_aoa_freq_grid が迎角軸で束ねるのに対し、こちらは迎角を固定して
    全風速条件（panel_data）を風速軸に並べる。

    風速軸は代表風速（rep_U）ではなく、対象迎角における各計測点の平均風速
    （spec_row["mean_U"] ＝ volt_summary の平均差圧電圧から算出）を使う。
    同一条件で target_aoa の計測点が複数（Pdata/Mdata 等）ある場合は mean_U も平均する。
    mean_U が NaN の条件は rep_U にフォールバックする。

    Parameters
    ----------
    panel_data : list of (rep_U, spec_rows)
    target_aoa : int                 固定する迎角
    key        : "psd_Fy" or "psd_Mz"

    Returns
    -------
    u_axis, f_plot, Z_db, peak_u, peak_freq  または データが無ければ None
    """
    if not panel_data:
        return None

    freqs = None
    # 条件ごとに (mean_U, 平均PSD) を作る。風速値が偶然衝突しても条件は潰さない
    # よう、いったんリストで持ってから最後に風速昇順で並べる。
    #
    # 各計測点の freqs は信号長が短い点が混じると長さ・刻みが変わりうる
    # （calc_psd の nperseg=min(2048,len(x)) のため）。vstack で束ねる前に
    # 代表グリッド（最初に見つかった freqs）へ揃える。
    entries = []   # list of (u_val, psd_col)
    for rep_U, spec_rows in panel_data:
        matched = [r for r in spec_rows if r["aoa"] == target_aoa]
        if not matched:
            continue
        if freqs is None:
            freqs = matched[0]["freqs"]
        psds = np.vstack([psd_on_grid(r["freqs"], r[key], freqs) for r in matched]).mean(axis=0)
        # 対象迎角の計測点の平均風速。全て NaN なら代表風速へフォールバック
        u_vals = [r.get("mean_U", np.nan) for r in matched]
        u_vals = [u for u in u_vals if u is not None and np.isfinite(u)]
        u_val = float(np.mean(u_vals)) if u_vals else float(rep_U)
        entries.append((u_val, psds))

    if not entries or freqs is None:
        return None

    entries.sort(key=lambda e: e[0])
    u_axis = np.array([e[0] for e in entries])
    fmask  = freqs <= args.map_fmax
    f_plot = freqs[fmask]

    Z = np.column_stack([e[1][fmask] for e in entries])
    zmax = Z.max()
    if zmax <= 0:
        return None
    Z_db = 10.0 * np.log10(np.maximum(Z, zmax * 1e-12) / zmax)
    Z_db = np.clip(Z_db, -args.map_dyn_range, 0.0)

    peak_u, peak_freq = [], []
    for j, u in enumerate(u_axis):
        col = Z[:, j]
        if np.any(col > 0):
            peak_u.append(u)
            peak_freq.append(f_plot[np.argmax(col)])

    return u_axis, f_plot, Z_db, peak_u, peak_freq


def plot_strouhal_aoa(df, exp_dir, rep_U, args, case_name=""):
    """1風速条件について、横軸=迎角・縦軸=St のプロットを描く。

    ストローハル数の迎角依存を見る図。St が迎角に対して概ね一定なら流れ律速
    （渦放出）、特定の迎角域で急変・張り付きがあれば構造の固有振動数への
    ロックイン／フラッターが疑われる。Fy/Mz を同一軸に重ね描きする。

    df: process_one_condition が返す DataFrame（aoa 昇順、St_Fy/St_Mz と
        flutter_A_Fy/flutter_A_Mz を含む）。横軸は df["aoa"]。
    """
    case_name = case_name or os.path.basename(exp_dir)
    if df is None or "aoa" not in df.columns:
        return
    if "St_Fy" not in df.columns and "St_Mz" not in df.columns:
        return

    fig, ax = plt.subplots(figsize=(11, 6.5))

    # Fy/Mz はマーカー形状（○/□）で区別し、透過を掛けて重なっても両方見えるようにする
    for st_col, flut_col, comp, color, marker in [
        ("St_Fy", "flutter_A_Fy", "Fy", "tab:blue",   "o"),
        ("St_Mz", "flutter_A_Mz", "Mz", "tab:orange", "s"),
    ]:
        if st_col not in df.columns:
            continue
        valid = df["aoa"].notna() & df[st_col].notna()
        if not valid.any():
            continue
        aoa = df.loc[valid, "aoa"].values
        st  = df.loc[valid, st_col].values

        # 迎角順に線でつなぎ、傾向（一定か張り付きか）を見やすくする
        order = np.argsort(aoa)
        ax.plot(aoa[order], st[order], color=color, lw=1.2, alpha=0.4,
                zorder=2, label=f"{comp}")

        # フラッター判定（Route A）有の点だけ赤×を重ねる
        if flut_col in df.columns:
            flag = pd.to_numeric(df.loc[valid, flut_col], errors="coerce").values
            idx_yes = flag == 1
            ax.scatter(aoa[idx_yes], st[idx_yes], marker="x", s=70,
                       color="red", zorder=4, label="_")
        ax.scatter(aoa, st, marker=marker, s=32, color=color, alpha=0.55,
                   edgecolors="none", zorder=3, label="_")

    # 凡例用ダミー（フラッター印）
    ax.scatter([], [], marker="x", color="red", label="Flutter (Route A)")

    ax.set_xlabel("Angle of attack [deg]", fontsize=13)
    ax.set_ylabel("Strouhal number  St = f·L/U", fontsize=13)
    ax.set_ylim(bottom=0)
    ax.set_title(f"St vs AoA   {case_name}   "
                 f"U ≈ {rep_U:.2f} m/s   (L = {REF_LENGTH_M:g} m)",
                 fontsize=13)
    ax.legend(fontsize=10, loc="best")
    ax.grid(True, alpha=0.4)

    fname = "strouhal_aoa.png"
    fig.savefig(os.path.join(exp_dir, fname), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [マップ] {fname} を保存しました")


def plot_aoa_freq_map(spec_rows, exp_dir, rep_U, args, case_name=""):
    """1風速条件について、横軸=迎角・縦軸=周波数・濃淡=PSD[dB] のマップを描く。

    Trickey et al. (2002) の fig10（流速×周波数スペクトログラム）の流速軸を
    迎角に置き換えたもの。Fy・Mz それぞれ1枚ずつ出力する。
    """
    case_name = case_name or os.path.basename(exp_dir)
    for axis, key, unit in [("Fy", "psd_Fy", "N"), ("Mz", "psd_Mz", "Nm")]:
        grid = build_aoa_freq_grid(spec_rows, key, args)
        if grid is None:
            continue
        aoa_axis, f_plot, Z_db, peak_aoa, peak_freq = grid

        fig, ax = plt.subplots(figsize=(11, 7))
        mesh = ax.pcolormesh(aoa_axis, f_plot, Z_db,
                             shading="nearest", cmap="viridis",
                             vmin=-args.map_dyn_range, vmax=0.0)
        cbar = fig.colorbar(mesh, ax=ax)
        cbar.set_label("Power [dB] (normalised to max)", fontsize=12)

        ax.scatter(peak_aoa, peak_freq, s=14, facecolors="none",
                   edgecolors="white", linewidths=0.8, zorder=3,
                   label="dominant freq")

        ax.set_xlabel("Angle of attack [deg]", fontsize=13)
        ax.set_ylabel("Frequency [Hz]", fontsize=13)
        ax.set_ylim(0, args.map_fmax)
        ax.set_title(f"AoA-frequency map  {axis} [{unit}]   {case_name}   "
                     f"U ≈ {rep_U:.2f} m/s",
                     fontsize=13)
        ax.legend(fontsize=10, loc="upper right")

        fname = f"aoa_freq_map_{axis}.png"
        fig.savefig(os.path.join(exp_dir, fname), dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  [マップ] {fname} を保存しました")


def plot_aoa_freq_panel(panel_data, out_dir, args):
    """全条件を縦に並べて風速ごとの迎角×周波数マップを比較するパネル図。

    Trickey et al. (2002) の流速掃引スペクトログラムに相当する俯瞰図を、
    「風速＝条件」を行方向に積み上げて表現する（上=高速、下=低速）。
    1枚の図に Fy（左列）・Mz（右列）を並べて出力する。

    各行（風速条件）は build_aoa_freq_grid によって自条件内の最大値へ
    個別に正規化される。条件間でパワーの絶対値を比較したい場合は
    build_speed_freq_grid（全条件を単一グリッドで正規化）を使うこと。

    panel_data : list of (rep_U, spec_rows)
    """
    if not panel_data:
        return

    # 風速の高い順に上から並べる
    panel_data = sorted(panel_data, key=lambda t: t[0], reverse=True)
    n = len(panel_data)

    fig, axes = plt.subplots(n, 2, figsize=(15, 2.6 * n + 1.2),
                             sharex=True, squeeze=False)
    meshes = {0: None, 1: None}

    for col, (axis, key, unit) in enumerate(
        [("Fy", "psd_Fy", "N"), ("Mz", "psd_Mz", "Nm")]
    ):
        for i, (rep_U, spec_rows) in enumerate(panel_data):
            ax = axes[i, col]
            grid = build_aoa_freq_grid(spec_rows, key, args)
            if grid is None:
                ax.text(0.5, 0.5, "no data", ha="center", va="center",
                        transform=ax.transAxes)
            else:
                aoa_axis, f_plot, Z_db, peak_aoa, peak_freq = grid
                meshes[col] = ax.pcolormesh(aoa_axis, f_plot, Z_db,
                                            shading="nearest", cmap="viridis",
                                            vmin=-args.map_dyn_range, vmax=0.0)
                ax.scatter(peak_aoa, peak_freq, s=8, facecolors="none",
                           edgecolors="white", linewidths=0.6, zorder=3)
            ax.set_ylim(0, args.map_fmax)
            if col == 0:
                ax.set_ylabel(f"U≈{rep_U:.1f} m/s\nFreq [Hz]", fontsize=10)

        axes[0, col].set_title(f"{axis} [{unit}]", fontsize=13)
        axes[-1, col].set_xlabel("Angle of attack [deg]", fontsize=13)

    fig.suptitle("AoA-frequency map across wind speeds", fontsize=14, y=0.995)

    # 各行（風速条件）は build_aoa_freq_grid 内でそれぞれ独立に自条件の
    # 最大値へ正規化している（条件ごとにピーク位置を見やすくするため）。
    # そのため列で共有する1本のカラーバーは行間でdB値を比較できる指標では
    # ない。誤読を避けるため、その旨を明示したラベルにする。
    for col in (0, 1):
        if meshes[col] is not None:
            cbar = fig.colorbar(meshes[col], ax=axes[:, col].tolist(),
                                pad=0.02, aspect=40)
            cbar.set_label("Power [dB]\n(normalised to each row's own max —\nnot comparable across rows)",
                          fontsize=9)

    fname = "aoa_freq_panel.png"
    fig.savefig(os.path.join(out_dir, fname), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[パネル] {fname} を保存しました")


# ============================================================
#  Layer 3: フラッター発生マップ
# ============================================================
def plot_flutter_map(summaries, out_dir):
    """全条件のサマリーからフラッター発生マップを描画する。

    summaries: list of (rep_U, DataFrame)
    """
    if not summaries:
        return

    for axis, col_A, col_B, unit in [
        ("Fy", "flutter_A_Fy", "flutter_B_Fy", "N"),
        ("Mz", "flutter_A_Mz", "flutter_B_Mz", "Nm"),
    ]:
        for route, col, label in [
            ("A_threshold", col_A, "Route A (RMS threshold)"),
            ("B_snr",       col_B, "Route B (spectral SNR)"),
        ]:
            fig, ax = plt.subplots(figsize=(10, 7))

            for rep_U, df in summaries:
                # フラッター判定が None（閾値未設定）の場合はスキップ
                valid = df[col].notna()
                if not valid.any():
                    continue

                aoas      = df.loc[valid, "aoa"].values
                flutter   = df.loc[valid, col].values.astype(int)

                # フラッター有: ×、なし: ○
                idx_yes = flutter == 1
                idx_no  = flutter == 0
                ax.scatter(aoas[idx_yes], [rep_U] * idx_yes.sum(),
                           marker="x", s=80, color="red",   zorder=3, label="_")
                ax.scatter(aoas[idx_no],  [rep_U] * idx_no.sum(),
                           marker="o", s=50, color="royalblue", zorder=3, label="_")

            # 凡例用ダミー
            ax.scatter([], [], marker="x", color="red",        label="Flutter")
            ax.scatter([], [], marker="o", color="royalblue",   label="No flutter")

            ax.set_xlabel("Angle of attack [deg]", fontsize=13)
            ax.set_ylabel("Representative wind speed U [m/s]", fontsize=13)
            ax.set_title(f"Flutter map  {axis} [{unit}]  {label}", fontsize=13)
            ax.legend(fontsize=11)
            ax.grid(True, alpha=0.4)

            fname = f"flutter_map_{axis}_{route}.png"
            fig.savefig(os.path.join(out_dir, fname), dpi=150, bbox_inches="tight")
            plt.close(fig)
            print(f"[マップ] {fname} を保存しました")


def plot_strouhal_fu(summaries, out_dir, args):
    """卓越周波数 f を縦軸・風速 U を横軸に取り、等St線を重ねた散布図を描く。

    渦放出（St一定 → 点が等St線に沿う）とロックイン（f が固有振動数に張り付き、
    St が 1/U で低下 → 点が等St線を横切る）を判別するための図。Fy/Mz を左右の
    サブプロットに並べる。

    --lco 併用で loop_thickness_Fy/Mz が算出済みなら、細い閉ループ（=明瞭な周期
    LCO）の点を大きく・濃く・viridis_r で着色して強調し、ノイズ的な点を沈める
    （点が多すぎて意味のある点が埋もれる問題への対策）。列が無ければ従来どおり
    フラッター判定（Route A）の有無で色分けする。

    summaries: list of (rep_U, DataFrame)  各 DataFrame に mean_U / freq_Fy /
               freq_Mz / flutter_A_Fy / flutter_A_Mz（--lco時は loop_thickness_*）
               を含む。横軸は各計測点の mean_U（rep_U ではない）。L=REF_LENGTH_M。
    """
    if not summaries:
        return

    L = REF_LENGTH_M
    clear_thk  = getattr(args, "st_clear_thk", 0.2)
    hide_noisy = getattr(args, "st_hide_noisy", False)

    comps = [
        (0, "freq_Fy", "flutter_A_Fy", "loop_thickness_Fy", "Fy", "N"),
        (1, "freq_Mz", "flutter_A_Mz", "loop_thickness_Mz", "Mz", "Nm"),
    ]

    # loop_thickness 列が（--lco で）存在すれば「明瞭な振動点の強調」モード。
    # 無ければ従来の Route A 色分けにフォールバックする。
    use_thk = any(c[3] in df.columns for _rep_U, df in summaries for c in comps)

    # 強調モードでは color/size を「安定域 [0, clear_thk]」に集中させる。
    # こうすると安定点(thk<clear_thk)の階調が潰れず、不安定点(thk>=clear_thk)は
    # 単色グレーの中抜きに退避して、安定/不安定をカテゴリとして一目で分けられる。
    # （旧実装は norm を 0〜全体最大(≈2, 不安定点が決める)に取っていたため、安定点が
    #   すべてカラーマップ上端に押し込まれてほぼ同色になり、境界も曖昧だった。）
    norm = None
    if use_thk:
        norm = plt.Normalize(vmin=0.0, vmax=clear_thk)

    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))

    for idx_ax, freq_col, flut_col, thk_col, comp, unit in comps:
        ax = axes[idx_ax]
        u_all = []   # 等St線のレンジ決定用に有効な mean_U を集める
        f_all = []
        for _rep_U, df in summaries:
            if freq_col not in df.columns or "mean_U" not in df.columns:
                continue
            # mean_U が有限かつ正、freq が有限の点のみ採用
            valid = (df["mean_U"].notna() & (df["mean_U"] > 0)
                     & df[freq_col].notna())
            if not valid.any():
                continue
            u = df.loc[valid, "mean_U"].values
            f = df.loc[valid, freq_col].values

            if use_thk and thk_col in df.columns:
                # loop_thickness でカテゴリ分け:
                #   安定(thk<clear_thk): viridis_r（[0,clear_thk]正規化）で塗り＋黒縁、
                #                        細いほど大きく → 安定点の中の質の差が見える
                #   不安定(thk>=clear_thk): 単色グレーの中抜き○に統一 → 境界を明示
                thk = pd.to_numeric(df.loc[valid, thk_col], errors="coerce").values
                fin   = np.isfinite(thk)
                clear = fin & (thk < clear_thk)
                noisy = fin & (thk >= clear_thk)
                nan_t = ~fin
                # 安定点サイズ: clear_thk を基準に細い(=0)ほど大きく
                sizes = 240 * np.clip(1 - thk / (clear_thk + 1e-9), 0.2, 1.0)
                ax.scatter(u[clear], f[clear], c=thk[clear], cmap="viridis_r",
                           norm=norm, s=sizes[clear], alpha=0.95,
                           edgecolors="k", linewidths=0.6, zorder=4, label="_")
                if hide_noisy:
                    shown = clear
                else:
                    # 不安定点: 単色グレーの中抜き○（カラーマップは使わない）
                    ax.scatter(u[noisy], f[noisy], marker="o", s=22,
                               facecolors="none", edgecolors="0.55",
                               linewidths=0.8, alpha=0.5, zorder=2, label="_")
                    # thickness 未算出（freqのみ有効）の点はさらに薄い灰の小点で残す
                    ax.scatter(u[nan_t], f[nan_t], marker="o", s=8, color="0.8",
                               alpha=0.2, edgecolors="none", zorder=1, label="_")
                    shown = np.ones(u.shape, dtype=bool)
                u_all.extend(u[shown])
                f_all.extend(f[shown])
            else:
                # フォールバック: フラッター判定（Route A）で色分け。None は灰○。
                if flut_col in df.columns:
                    flag = df.loc[valid, flut_col].values
                else:
                    flag = np.full(u.shape, np.nan)
                flag_num = pd.to_numeric(pd.Series(flag), errors="coerce").values
                idx_yes = flag_num == 1
                idx_no  = flag_num == 0
                idx_na  = ~np.isfinite(flag_num)
                ax.scatter(u[idx_yes], f[idx_yes], marker="x", s=80,
                           color="red", zorder=3, label="_")
                ax.scatter(u[idx_no], f[idx_no], marker="o", s=50,
                           color="royalblue", zorder=3, label="_")
                ax.scatter(u[idx_na], f[idx_na], marker="o", s=50,
                           facecolors="none", edgecolors="gray", zorder=3, label="_")
                u_all.extend(u)
                f_all.extend(f)

        # 散布点を打ってから軸範囲を確定し、その範囲で等St線を引く
        if u_all:
            u_max = max(u_all)
            f_max = max(f_all)
            ax.set_xlim(0, u_max * 1.05)
            ax.set_ylim(0, f_max * 1.15)
            xr = ax.get_xlim()
            yr = ax.get_ylim()
            u_line = np.array([xr[0], xr[1]])
            for st in (0.05, 0.1, 0.15, 0.2, 0.3, 0.5):
                f_line = st * u_line / L
                # 縦軸上限を超える線は描かない（読みにくくなるため）
                if f_line[1] > yr[1] * 1.001 and f_line[0] > yr[1]:
                    continue
                ax.plot(u_line, f_line, color="0.6", lw=0.8,
                        ls="--", zorder=1, label="_")
                # ラベルは線が縦軸上限に収まる x 位置に置く
                x_lbl = xr[1] * 0.92
                y_lbl = st * x_lbl / L
                if y_lbl > yr[1]:
                    y_lbl = yr[1] * 0.95
                    x_lbl = y_lbl * L / st
                ax.text(x_lbl, y_lbl, f"St={st:g}", color="0.4",
                        fontsize=9, ha="right", va="bottom", zorder=2)

        # 凡例用ダミー
        if use_thk:
            ax.scatter([], [], marker="o", s=90, color="0.35",
                       edgecolors="k", linewidths=0.6,
                       label=f"stable LCO (thk < {clear_thk:g})")
            if not hide_noisy:
                ax.scatter([], [], marker="o", s=30, facecolors="none",
                           edgecolors="0.55", linewidths=0.8,
                           label=f"unstable (thk >= {clear_thk:g})")
            ax.plot([], [], color="0.6", lw=0.8, ls="--", label="iso-St lines")
        else:
            ax.scatter([], [], marker="x", color="red",       label="Flutter (Route A)")
            ax.scatter([], [], marker="o", color="royalblue",  label="No flutter")
            ax.plot([], [], color="0.6", lw=0.8, ls="--",      label="iso-St lines")

        ax.set_xlabel("Wind speed U [m/s]", fontsize=13)
        ax.set_ylabel("Dominant frequency f [Hz]", fontsize=13)
        ax.set_title(f"{comp} [{unit}]", fontsize=13)
        ax.legend(fontsize=10, loc="upper left")
        ax.grid(True, alpha=0.4)

    fig.suptitle(f"Strouhal number  (St = f·L/U,  L = {L:g} m)", fontsize=14)
    fig.tight_layout()
    # 強調モードのみ、thickness の共有カラーバーを付す
    if use_thk and norm is not None:
        sm = plt.cm.ScalarMappable(norm=norm, cmap="viridis_r")
        sm.set_array([])
        # extend='max' の矢印で「上端(clear_thk)より上=不安定域」を明示
        cbar = fig.colorbar(sm, ax=axes, fraction=0.046, pad=0.04, extend="max")
        cbar.set_label(
            f"loop thickness (stable range 0-{clear_thk:g}; grey o = unstable)",
            fontsize=11)
    else:
        print("  [ヒント] --lco を付けると loop_thickness で明瞭な振動点を強調表示します")
    out_path = os.path.join(out_dir, "strouhal_fu.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[マップ] strouhal_fu.png を保存しました")


def plot_thickness_hist(summaries, out_dir, args):
    """loop_thickness の分布（Fy/Mz）を描き、安定/不安定の閾値選定を支援する。

    strouhal_fu 図の色分けは thk < st_clear_thk を「安定な周期LCO」とみなすが、
    その閾値（既定 0.2）が妥当かはデータの分布を見ないと判断できない。本図は
    ヒストグラム＋ECDF（累積分布）を描き、現在の clear_thk を縦線で重ねる。

    - 分布が二峰性でその谷に clear_thk が乗っていれば、安定/不安定を自然に分離
      できている（閾値は妥当）。
    - 谷が clear_thk とずれていれば、谷の位置に --st_clear_thk を合わせるとよい。
    - そもそも谷が無い（単峰・連続的）なら thk 単独では切れないサインで、振幅や
      SNR の併用を検討する。

    --lco 併用で loop_thickness_Fy/Mz が算出済みのときのみ図を出す。
    """
    if not summaries:
        return

    clear_thk = getattr(args, "st_clear_thk", 0.2)
    comps = [("loop_thickness_Fy", "Fy", "tab:blue"),
             ("loop_thickness_Mz", "Mz", "tab:orange")]

    # 成分ごとに有限な thickness を収集
    data = {}
    for thk_col, comp, _color in comps:
        vals = []
        for _rep_U, df in summaries:
            if thk_col in df.columns:
                v = pd.to_numeric(df[thk_col], errors="coerce").values
                vals.extend(v[np.isfinite(v)])
        data[comp] = np.asarray(vals, dtype=float)

    if all(v.size == 0 for v in data.values()):
        return  # --lco 無しなど thickness が無ければスキップ

    # ビン範囲を全成分共通化（左右で直接比較できるように）
    vmax = max((v.max() for v in data.values() if v.size), default=1.0)
    vmax = max(vmax, clear_thk * 1.5)
    bins = np.linspace(0, vmax * 1.02, 41)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, (thk_col, comp, color) in zip(axes, comps):
        v = data[comp]
        if v.size == 0:
            ax.set_visible(False)
            continue
        n_stable = int(np.sum(v < clear_thk))
        n_total  = v.size
        frac = 100.0 * n_stable / n_total

        # 安定域を薄く塗り、ヒストグラム＋閾値線
        ax.axvspan(0, clear_thk, color="tab:green", alpha=0.08, zorder=0)
        ax.hist(v, bins=bins, color=color, alpha=0.4, edgecolor=color,
                zorder=2, label=f"{comp}  (n={n_total})")
        ax.axvline(clear_thk, color="crimson", lw=1.6, ls="--", zorder=3,
                   label=f"clear_thk = {clear_thk:g}")

        # ECDF（右軸）: 谷やしきい位置での累積割合を読みやすくする
        ax2 = ax.twinx()
        vs = np.sort(v)
        ecdf = np.arange(1, vs.size + 1) / vs.size
        ax2.plot(vs, ecdf, color="0.35", lw=1.2, alpha=0.85, zorder=4)
        ax2.set_ylim(0, 1)
        ax2.set_ylabel("ECDF (cumulative fraction)", color="0.35", fontsize=11)
        ax2.tick_params(axis="y", labelcolor="0.35")

        ax.set_title(f"{comp}:  stable (thk < {clear_thk:g}) = "
                     f"{n_stable}/{n_total}  ({frac:.0f}%)", fontsize=12)
        ax.set_xlabel("loop thickness", fontsize=12)
        ax.set_ylabel("count", fontsize=12)
        ax.set_xlim(0, vmax * 1.02)
        ax.legend(fontsize=10, loc="upper right")
        ax.grid(True, alpha=0.3)

    fig.suptitle("loop_thickness distribution "
                 "(for choosing the stable/unstable threshold)", fontsize=13)
    fig.tight_layout()
    out_path = os.path.join(out_dir, "thickness_hist.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[マップ] thickness_hist.png を保存しました")


def plot_coeff_reduced_velocity(summaries, out_dir, args):
    """全条件横断で、変動空気力係数 C′ を換算風速 U* に対してプロットする。

    横軸 U* = U/(f·c)、縦軸 C′ = RMS/(½ρU²A[·c])。動圧で正規化した応答振幅を
    U* に対して見ることで「C′ が U* に対し崩れず一致（collapse）＝準定常応答か、
    発散＝フラッター臨界接近か」を1枚で読む。点は迎角で着色する。Fy/Mz を左右に並べる。

    summaries: list of (rep_U, DataFrame)  各 DataFrame は process_one_condition が返す。
               C_Fy_rms/C_Mz_rms・Ustar_Fy/Ustar_Mz・aoa を含む。
    """
    if not summaries:
        return

    comps = [
        (0, "Ustar_Fy", "C_Fy_rms", "Fy", "C'_Fy = RMS_Fy / (½ρU²A)"),
        (1, "Ustar_Mz", "C_Mz_rms", "Mz", "C'_Mz = RMS_Mz / (½ρU²A·c)"),
    ]

    # 迎角の全域を共有カラースケールにする（左右サブプロットで色の意味を揃える）
    aoa_all = []
    for _rep_U, df in summaries:
        if "aoa" in df.columns:
            aoa_all.extend(pd.to_numeric(df["aoa"], errors="coerce").dropna().tolist())
    if not aoa_all:
        return
    norm = plt.Normalize(vmin=min(aoa_all), vmax=max(aoa_all))

    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    any_point = False
    for idx_ax, ux_col, cy_col, comp, ylabel in comps:
        ax = axes[idx_ax]
        for _rep_U, df in summaries:
            if ux_col not in df.columns or cy_col not in df.columns:
                continue
            ux = pd.to_numeric(df[ux_col], errors="coerce").values
            cy = pd.to_numeric(df[cy_col], errors="coerce").values
            aoa = pd.to_numeric(df.get("aoa"), errors="coerce").values
            # U*>0・C′>0（log軸）・迎角有限の点のみ採用
            valid = (np.isfinite(ux) & (ux > 0)
                     & np.isfinite(cy) & (cy > 0) & np.isfinite(aoa))
            if not valid.any():
                continue
            any_point = True
            ax.scatter(ux[valid], cy[valid], c=aoa[valid], cmap="coolwarm",
                       norm=norm, s=36, alpha=0.85, edgecolors="k",
                       linewidths=0.3, zorder=3, label="_")

        ax.set_xlabel("Reduced velocity  U* = U/(f·c)", fontsize=13)
        ax.set_ylabel(ylabel, fontsize=13)
        ax.set_yscale("log")
        ax.set_title(f"{comp}", fontsize=13)
        ax.grid(True, which="both", alpha=0.3)

    fig.suptitle("Fluctuating force coefficient vs reduced velocity "
                 "(collapse = quasi-steady, divergence = flutter onset)", fontsize=14)
    fig.tight_layout()
    if any_point:
        sm = plt.cm.ScalarMappable(norm=norm, cmap="coolwarm")
        sm.set_array([])
        cbar = fig.colorbar(sm, ax=axes, fraction=0.046, pad=0.04)
        cbar.set_label("Angle of attack [deg]", fontsize=11)
    out_path = os.path.join(out_dir, "coeff_reduced_velocity.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[マップ] coeff_reduced_velocity.png を保存しました")


def plot_rms_overview(summaries, out_dir):
    """全条件・全迎角のRMS一覧グラフ（フラッター強度の俯瞰用）。"""
    if not summaries:
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    cmap = plt.get_cmap("viridis")
    n = len(summaries)

    for k, (rep_U, df) in enumerate(summaries):
        color = cmap(k / max(n - 1, 1))
        label = f"U={rep_U:.1f} m/s"
        axes[0].plot(df["aoa"], df["rms_Fy"], marker="o", ms=4,
                     lw=1.2, color=color, label=label)
        axes[1].plot(df["aoa"], df["rms_Mz"], marker="o", ms=4,
                     lw=1.2, color=color, label=label)

    for ax, ylabel, title in [
        (axes[0], "RMS [N]",  "Fy flutter amplitude RMS"),
        (axes[1], "RMS [Nm]", "Mz flutter amplitude RMS"),
    ]:
        ax.set_xlabel("Angle of attack [deg]", fontsize=12)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_title(title, fontsize=12)
        ax.legend(fontsize=9, ncol=2)
        ax.grid(True, alpha=0.4)

    fig.tight_layout()
    out_path = os.path.join(out_dir, "rms_overview.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[概観] {out_path} を保存しました")


def plot_rms_overview_6axis(summaries, out_dir):
    """全条件・全迎角の6成分RMS一覧グラフ（2行3列・フラッター強度の俯瞰用）。

    上段 Fx/Fy/Fz [N]・下段 Mx/My/Mz [Nm] を並べ、各サブプロットは
    横軸=迎角・線の色=風速条件。どの成分（自由度）に振動が乗っているかを俯瞰する。
    """
    if not summaries:
        return

    # (列名, 表示名, 単位) を 2行3列の並び順で定義
    panels = [
        ("rms_Fx", "Fx", "N"),  ("rms_Fy", "Fy", "N"),  ("rms_Fz", "Fz", "N"),
        ("rms_Mx", "Mx", "Nm"), ("rms_My", "My", "Nm"), ("rms_Mz", "Mz", "Nm"),
    ]

    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    flat = axes.ravel()
    cmap = plt.get_cmap("viridis")
    n = len(summaries)

    for k, (rep_U, df) in enumerate(summaries):
        color = cmap(k / max(n - 1, 1))
        label = f"U={rep_U:.1f} m/s"
        for ax, (col, comp, unit) in zip(flat, panels):
            ax.plot(df["aoa"], df[col], marker="o", ms=4,
                    lw=1.2, color=color, label=label)

    for ax, (col, comp, unit) in zip(flat, panels):
        ax.set_xlabel("Angle of attack [deg]", fontsize=12)
        ax.set_ylabel(f"RMS [{unit}]", fontsize=12)
        ax.set_title(f"{comp} flutter amplitude RMS", fontsize=12)
        ax.legend(fontsize=8, ncol=2)
        ax.grid(True, alpha=0.4)

    fig.tight_layout()
    out_path = os.path.join(out_dir, "rms_overview_6axis.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[概観] {out_path} を保存しました")


# ============================================================
#  メイン
# ============================================================
def run(args):
    """後処理の本体。処理対象の出力先ディレクトリを返す（完了マーカーの保存先）。

    単一条件モードは条件フォルダ自身、一括処理モードは <base>_results を返す。
    """
    # ---- 単一条件モード（--exp_dir） ----
    if args.exp_dir:
        exp_dir = args.exp_dir.rstrip("/\\")
        log     = load_log(exp_dir)
        ofst_dir = log.get("ofst_dir", "")

        # ログの ofst_dir が解決できない場合（別PCで取得したデータ等）は、
        # 条件フォルダ名から `<親>/<base>_ofst` を自動探索するフォールバック
        if not ofst_dir or not os.path.isdir(ofst_dir):
            cond_name = os.path.basename(exp_dir)         # 例: 260624_flutter_c07
            base_name = re.sub(r"_c\d+$", "", cond_name)  # 例: 260624_flutter
            parent    = os.path.dirname(exp_dir)
            for cand in (os.path.join(parent, f"{base_name}_ofst"),
                         os.path.join(exp_dir, f"{base_name}_ofst")):
                if os.path.isdir(cand):
                    print(f"[ofst] ログのパスが解決できないため自動探索: {cand}")
                    ofst_dir = cand
                    break

        if not ofst_dir or not os.path.isdir(ofst_dir):
            print(f"[エラー] ofst_dir が見つかりません: {ofst_dir}", file=sys.stderr)
            sys.exit(1)
        ofst = load_ofst_means(ofst_dir)
        with _executor_context(args) as executor:
            process_one_condition(exp_dir, ofst, args, executor=executor)
        return exp_dir

    # ---- 一括処理モード（--base_dir） ----
    base_dir = args.base_dir.rstrip("/\\")
    base_name = os.path.basename(base_dir)   # 例: 260620_flexible

    # 条件フォルダ（_cXX）が並ぶ場所を特定する。
    #   - フラット構成: base_dir の親階層に _ofst / _cXX が並ぶ
    #   - ネスト構成  : base_dir 直下に _ofst / _cXX が並ぶ（260624_flutter など）
    parent = os.path.dirname(base_dir) if os.path.dirname(base_dir) else "."
    cond_re = re.compile(rf"^{re.escape(base_name)}_c\d+$")

    def list_conds(d):
        if not os.path.isdir(d):
            return []
        return sorted(
            os.path.join(d, x) for x in os.listdir(d)
            if cond_re.match(x) and os.path.isdir(os.path.join(d, x))
        )

    cond_dirs = list_conds(base_dir) or list_conds(parent)
    if not cond_dirs:
        print("[エラー] 条件フォルダ（_c01 など）が見つかりません。", file=sys.stderr)
        sys.exit(1)

    # 条件フォルダが見つかった場所を基準に ofst / 結果フォルダを決める
    search_dir = os.path.dirname(cond_dirs[0])

    # ofst フォルダを探す
    ofst_dir = os.path.join(search_dir, f"{base_name}_ofst")
    if not os.path.isdir(ofst_dir):
        print(f"[エラー] ofst フォルダが見つかりません。\n"
              f"  探した場所: {ofst_dir}", file=sys.stderr)
        sys.exit(1)

    ofst = load_ofst_means(ofst_dir)

    print(f"[一括処理] {len(cond_dirs)} 条件を処理します。")

    summaries  = []
    panel_data = []   # 全条件比較パネル用 (rep_U, spec_rows)
    lco_data   = []   # 全条件LCO用 (rep_U, lco_rows)
    with _executor_context(args) as executor:
        for cond_dir in cond_dirs:
            log = load_log(cond_dir)
            rep_U = log.get("rep_windspeed_U", 0.0)
            result = process_one_condition(cond_dir, ofst, args, executor=executor)
            if result is not None:
                df, spec_rows, lco_rows = result
                summaries.append((rep_U, df))
                panel_data.append((rep_U, spec_rows))
                lco_data.append((rep_U, lco_rows))

    # Layer 3: マップ出力（条件フォルダと同じ階層に保存）
    map_dir = os.path.join(search_dir, f"{base_name}_results")
    os.makedirs(map_dir, exist_ok=True)
    plot_flutter_map(summaries, map_dir)
    plot_strouhal_fu(summaries, map_dir, args)
    plot_thickness_hist(summaries, map_dir, args)
    plot_coeff_reduced_velocity(summaries, map_dir, args)
    plot_rms_overview(summaries, map_dir)
    plot_rms_overview_6axis(summaries, map_dir)
    plot_aoa_freq_panel(panel_data, map_dir, args)

    # LCO全条件レベルの図（--lco 時のみ）
    if args.lco:
        import lco_analysis
        lco_analysis.plot_all_conditions(lco_data, map_dir, args)
        # 風速版スペクトログラム（迎角固定で風速スイープ）は既存の PSD（panel_data）を
        # 再利用する。build_aoa_freq_grid と同形式のグリッドを風速軸で構築する。
        lco_analysis.plot_speed_spectrogram(
            panel_data, map_dir, args, build_grid=build_speed_freq_grid)

    print(f"\n[完了] 結果を保存しました: {map_dir}")
    return map_dir


def _write_marker(target_dir, name, body):
    """完了/失敗マーカーを target_dir に書き出す（書けなくても本処理は止めない）。"""
    if not target_dir:
        return
    try:
        with open(os.path.join(target_dir, name), "w", encoding="utf-8") as f:
            f.write(body)
    except OSError:
        pass


def main():
    args = parse_args()

    # マーカーの第一候補は起動時の対象ディレクトリ（--exp_dir / --base_dir）。
    # run() が正常終了すれば返り値（実際の出力先）で上書きして完了マーカーを置く。
    target_dir = (args.exp_dir or args.base_dir or "").rstrip("/\\")

    # 前回の残骸マーカーを消してから開始（古い done/failed を誤読しないため）
    for m in ("postprocess_done.marker", "postprocess_failed.marker"):
        try:
            os.remove(os.path.join(target_dir, m))
        except OSError:
            pass

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        out_dir = run(args)
    except SystemExit as e:
        # sys.exit(非0) は明示的な失敗として扱う（0/None は正常終了）
        if e.code not in (0, None):
            _write_marker(target_dir, "postprocess_failed.marker",
                          f"[失敗] {ts}  exit code {e.code}\n")
            raise
        _write_marker(target_dir, "postprocess_done.marker", f"[完了] {ts}\n")
        return
    except BaseException:
        _write_marker(target_dir, "postprocess_failed.marker",
                      f"[失敗] {ts}\n{traceback.format_exc()}")
        raise

    _write_marker(out_dir or target_dir, "postprocess_done.marker",
                  f"[完了] {ts}\n")


if __name__ == "__main__":
    main()
