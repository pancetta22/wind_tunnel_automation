# -*- coding: utf-8 -*-
"""3次元 時間遅れ埋め込み 位相図の試作（C07 Fy 限定）

既存 2D 位相図（lco_analysis.plot_chart 列2）を `[Fy(t), Fy(t-τ)]` から
`[Fy(t), Fy(t-τ), Fy(t-2τ)]` の **3次元軌道**へ拡張する見え方確認用の試作。

Takens 埋め込みでは次元が不足すると軌道が自己交差し周期/準周期/カオスの
区別が曖昧になる。3軸目 Fy(t-2τ) を足してアトラクタ幾何を忠実に見る。

本番パイプライン（flutter_analysis 経由）は無改変。delay_embedding は既に
dim 引数対応なので dim=3 を渡すだけで3次元埋め込みが得られる。

使い方:
    cd post_process
    python phase3d_trial.py            # 既定で C07 の Pdata_11 / Pdata_08・Fy
    python phase3d_trial.py --csv A.csv B.csv --signal Fy
"""
import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (3D投影の登録に必要)

# 前処理・埋め込み・τ推定は既存実装を再利用（二重実装しない）
from flutter_analysis import (
    load_csv,
    resample_uniform,
    highpass,
    calc_psd,
    dominant_freq,
    FS_TARGET,
    HP_CUTOFF_HZ,
)
from lco_analysis import (
    estimate_tau_autocorr,
    _tau_from_f0,
    delay_embedding,
    LCO_FMIN_HZ,
    LCO_FMAX_HZ,
)

# 端末/MATLAB の system() 経由でも文字エンコードで落ちないようにする
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


# ------------------------------------------------------------
# 既定の対象（C07 の Pdata_11 / Pdata_08）
# ------------------------------------------------------------
C07_DATA_DIR = os.path.join(
    r"C:\Users\kento\OneDrive - The University of Tokyo",
    "4S", "実験", "WindyData", "260624_flutter", "260624_flutter_c07", "data",
)
DEFAULT_CSVS = [
    os.path.join(C07_DATA_DIR, "20260624_160751_260624_Pdata_11.01.csv"),
    os.path.join(C07_DATA_DIR, "20260624_160611_260624_Pdata_08.01.csv"),
]


def preprocess(csv_path, name, hp_cutoff, edge_trim_sec, duration_sec=None):
    """1 CSV を本番(analyze_point)同様に前処理して HP済み信号と τ・f0 を返す。

    流れ: load_csv → resample_uniform → 平均引き+highpass → 両端トリミング
          → （任意）先頭 duration_sec だけ切り出し。

    両端トリミングは補間段差/HP端トランジェントの偽スパイク除去（GUIDE §7）。
    duration_sec を与えると、軌道の線の重なりで潰れるのを避けるため、τ・f0 は
    全長で推定したうえで描画用に先頭区間だけ残す（数十サイクル分）。
    """
    df = load_csv(csv_path)
    t = df["t"].values
    raw = df[name].values

    t_u, x_u = resample_uniform(t, raw, fs=FS_TARGET)
    x_hp = highpass(x_u - np.mean(x_u), hp_cutoff, fs=FS_TARGET)

    # 両端トリミング（本番 analyze_point に合わせる。_standalone は未適用なので寄せる）
    trim = int(round(edge_trim_sec * FS_TARGET))
    if trim > 0 and len(x_hp) > 2 * trim + 8:
        x_hp = x_hp[trim:len(x_hp) - trim]
        t_u = t_u[trim:len(t_u) - trim]

    # 卓越周波数と τ は全長で推定（既定 zero_cross。見つからなければ 1/4周期則）
    freqs, psd = calc_psd(x_hp, fs=FS_TARGET)
    f0 = dominant_freq(freqs, psd, fmin=LCO_FMIN_HZ, fmax=LCO_FMAX_HZ)
    tau_samples, tau_sec = estimate_tau_autocorr(x_hp, fs=FS_TARGET, f0=f0)

    # 描画用に先頭 duration_sec だけ切り出す（線の重なりで潰れるのを避ける）
    if duration_sec is not None and duration_sec > 0:
        ndraw = int(round(duration_sec * FS_TARGET))
        if 0 < ndraw < len(x_hp):
            x_hp = x_hp[:ndraw]

    return {
        "short": os.path.splitext(os.path.basename(csv_path))[0],
        "x": x_hp,
        "f0": f0,
        "tau_samples": tau_samples,
        "tau_sec": tau_sec,
    }


def _setup_axes(ax, it, signal_name, elev, azim):
    """1サブプロットの軸ラベル・タイトル・視点を設定する（共通処理）。"""
    tau_ms = it["tau_sec"] * 1e3 if np.isfinite(it["tau_sec"]) else np.nan
    f0 = it["f0"]
    ax.set_xlabel(f"{signal_name}(t)")
    ax.set_ylabel(f"{signal_name}(t-tau)")
    ax.set_zlabel(f"{signal_name}(t-2tau)")
    ax.set_title(
        f"{it['short']}\n"
        f"tau={tau_ms:.1f} ms,  f0={f0:.1f} Hz" if np.isfinite(f0)
        else f"{it['short']}\ntau={tau_ms:.1f} ms",
        fontsize=10,
    )
    ax.view_init(elev=elev, azim=azim)


def plot_phase3d(items, signal_name, out_path, elev, azim, mode="line",
                 point_size=3.0):
    """前処理済み各点の3次元位相図を1枚に横並びで描く（静的PNG）。

    mode="line": 時間方向 viridis グラデーションの線図。
    mode="point": 線で結ばず散布（thk大で線が潰れる場合に軌跡を追いやすい）。
    """
    n = len(items)
    fig = plt.figure(figsize=(7.5 * n, 7.0))
    fig.suptitle(
        f"3D delay embedding  {signal_name}  "
        f"[{signal_name}(t), {signal_name}(t-tau), {signal_name}(t-2tau)]"
        f"   ({mode})",
        fontsize=13,
    )

    for i, it in enumerate(items):
        ax = fig.add_subplot(1, n, i + 1, projection="3d")
        emb = delay_embedding(it["x"], it["tau_samples"], dim=3)

        if emb is None or emb.shape[0] < 8:
            ax.set_title(f"{it['short']}  (埋め込み不可)", fontsize=10)
            continue

        m = emb.shape[0]
        c = np.linspace(0.0, 1.0, m)
        if mode == "point":
            # 線で結ばず点の散布。時間進行を viridis で着色。
            ax.scatter(emb[:, 0], emb[:, 1], emb[:, 2],
                       c=c, cmap="viridis", s=point_size,
                       depthshade=True, edgecolors="none")
        else:
            # 時間進行が分かるよう、隣接区間ごとに viridis で着色した線分で描く。
            cmap = plt.get_cmap("viridis")
            for k in range(m - 1):
                ax.plot(emb[k:k + 2, 0], emb[k:k + 2, 1], emb[k:k + 2, 2],
                        color=cmap(c[k]), lw=0.4, alpha=0.8)

        _setup_axes(ax, it, signal_name, elev, azim)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path


def animate_phase3d(items, signal_name, out_path, elev, azim,
                    duration_anim=12.0, fps=20, trail=None, point_size=3.0):
    """軌道を時間進行で順に伸ばすアニメーション（GIF）。

    束の重なりを時間軸でほぐして1本の軌跡を追えるようにする。
    trail を与えると直近 trail 点だけ残す「尾」表示（None で全履歴を残す）。

    duration_anim : GIF全体の秒数。fps と合わせて総フレーム数を決める。
    """
    from matplotlib.animation import FuncAnimation, PillowWriter

    n = len(items)
    fig = plt.figure(figsize=(7.5 * n, 7.0))
    fig.suptitle(
        f"3D delay embedding  {signal_name}  (animation)", fontsize=13)

    axes, embs = [], []
    for i, it in enumerate(items):
        ax = fig.add_subplot(1, n, i + 1, projection="3d")
        emb = delay_embedding(it["x"], it["tau_samples"], dim=3)
        embs.append(emb)
        axes.append(ax)
        if emb is None or emb.shape[0] < 8:
            ax.set_title(f"{it['short']}  (埋め込み不可)", fontsize=10)
            continue
        # 軸範囲を固定（フレームごとに動かない）
        ax.set_xlim(emb[:, 0].min(), emb[:, 0].max())
        ax.set_ylim(emb[:, 1].min(), emb[:, 1].max())
        ax.set_zlim(emb[:, 2].min(), emb[:, 2].max())
        _setup_axes(ax, it, signal_name, elev, azim)

    n_frames = max(int(round(duration_anim * fps)), 2)
    cmap = plt.get_cmap("viridis")

    def update(frame):
        frac = (frame + 1) / n_frames
        artists = []
        for ax, emb in zip(axes, embs):
            if emb is None or emb.shape[0] < 8:
                continue
            m = emb.shape[0]
            upto = max(int(round(frac * m)), 2)
            lo = 0 if trail is None else max(0, upto - trail)
            seg = emb[lo:upto]
            # 既存の動的アーティストを消してから現区間を描き直す
            for ln in list(ax.lines):
                ln.remove()
            cc = np.linspace(lo / m, upto / m, len(seg))
            for k in range(len(seg) - 1):
                ln, = ax.plot(seg[k:k + 2, 0], seg[k:k + 2, 1], seg[k:k + 2, 2],
                              color=cmap(cc[k]), lw=0.6, alpha=0.9)
                artists.append(ln)
        return artists

    anim = FuncAnimation(fig, update, frames=n_frames, blit=False)
    anim.save(out_path, writer=PillowWriter(fps=fps))
    plt.close(fig)
    return out_path


def main():
    p = argparse.ArgumentParser(
        description="3次元 時間遅れ埋め込み 位相図の試作（C07 Fy 限定）")
    p.add_argument("--csv", nargs="+", default=DEFAULT_CSVS,
                   help="対象 CSV（複数可）。既定: C07 の Pdata_11 / Pdata_08")
    p.add_argument("--signal", default="Fy",
                   help="解析する成分（既定: Fy）")
    p.add_argument("--hp_cutoff", type=float, default=HP_CUTOFF_HZ,
                   help=f"ハイパスカットオフ [Hz]（既定: {HP_CUTOFF_HZ}）")
    p.add_argument("--edge_trim_sec", type=float, default=0.5,
                   help="両端トリミング秒数（既定: 0.5、0で無効）")
    p.add_argument("--duration_sec", type=float, default=3.0,
                   help="描画する先頭秒数（既定: 3.0、0以下で全長）。"
                        "τ・f0は全長で推定し描画のみ先頭を使う")
    p.add_argument("--elev", type=float, default=25.0,
                   help="3D視点の仰角 elev（既定: 25）")
    p.add_argument("--azim", type=float, default=-60.0,
                   help="3D視点の方位角 azim（既定: -60）")
    p.add_argument("--mode", default="point", choices=["line", "point", "anim"],
                   help="表示モード: line=線図 / point=点描画(既定) / anim=GIFアニメーション")
    p.add_argument("--point_size", type=float, default=3.0,
                   help="point/anim モードの点サイズ（既定: 3.0）")
    p.add_argument("--anim_duration", type=float, default=12.0,
                   help="anim: GIF全体の秒数（既定: 12）")
    p.add_argument("--anim_fps", type=int, default=20,
                   help="anim: フレームレート（既定: 20）")
    p.add_argument("--anim_trail", type=int, default=None,
                   help="anim: 直近何点だけ尾を残すか（未指定で全履歴）")
    p.add_argument("--out", default=None,
                   help="出力パス（既定: 1つ目CSVのフォルダ直下 phase3d_trial_<signal>.<png|gif>）")
    args = p.parse_args()

    items = []
    for csv_path in args.csv:
        if not os.path.isfile(csv_path):
            print(f"[警告] 見つかりません: {csv_path}")
            continue
        it = preprocess(csv_path, args.signal, args.hp_cutoff,
                        args.edge_trim_sec, duration_sec=args.duration_sec)
        items.append(it)
        tau_ms = it["tau_sec"] * 1e3 if np.isfinite(it["tau_sec"]) else float("nan")
        print(f"[読み込み] {it['short']}  N={len(it['x'])}  "
              f"f0={it['f0']:.2f} Hz  tau={tau_ms:.1f} ms "
              f"({it['tau_samples']} samp)")

    if not items:
        print("[終了] 処理対象がありません。")
        return

    ext = "gif" if args.mode == "anim" else "png"
    if args.out:
        out_path = args.out
    else:
        base_dir = os.path.dirname(os.path.abspath(args.csv[0]))
        # data/ 配下なら1つ上（C07直下）に出す
        parent = os.path.dirname(base_dir) if os.path.basename(base_dir) == "data" else base_dir
        suffix = "_anim" if args.mode == "anim" else f"_{args.mode}"
        out_path = os.path.join(parent, f"phase3d_trial_{args.signal}{suffix}.{ext}")

    if args.mode == "anim":
        print("[描画] アニメーション生成中…（点数によっては時間がかかります）")
        out = animate_phase3d(items, args.signal, out_path, args.elev, args.azim,
                              duration_anim=args.anim_duration, fps=args.anim_fps,
                              trail=args.anim_trail, point_size=args.point_size)
    else:
        out = plot_phase3d(items, args.signal, out_path, args.elev, args.azim,
                           mode=args.mode, point_size=args.point_size)
    print(f"[出力] {out}")


if __name__ == "__main__":
    main()
