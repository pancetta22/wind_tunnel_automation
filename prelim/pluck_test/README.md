# pluck test（自由減衰）による構造 f_n・ζ の同定【予備実験】

現有センサ（Leptrino 6軸力覚センサ）だけで、翼構造の**静止時固有振動数 f_n** と
**構造減衰比 ζ** を同定するための予備実験一式。無風で翼を弾いて放し、減衰振動を記録・解析する。

> **これは本番実験ではなく予備実験**です。本番パイプライン（`post_process/` 等）からは
> 切り離してあり、このフォルダ内で自己完結します（解析 Python は `post_process` を import しません）。
> 同定した f_n・ζ は将来フラッター解析の第2層無次元数（質量比・Scruton数・フラッター速度指数）の
> 入力に使います。

## なぜ f_n・ζ が要るか

フラッター/LCO を臨界的に解釈する無次元数のうち、**質量比 μ・Scruton数 Sc・フラッター速度指数・
構造基準の換算風速 U_R** は、翼の質量・固有振動数・構造減衰を必要とする。このうち f_n・ζ は
無風の自由減衰計測で同定できる。

## 物理（同定の原理）

剛性拘束された模型を初期変位から放すと、ベース反力は**減衰固有振動数 f_d** で振動し、
振幅の包絡線は e^(−ζ·ω_n·t) で指数減衰する。減衰率 σ（包絡線の対数の傾き）と f_d から

```
ω_d = 2π·f_d,   ω_n = √(σ² + ω_d²),   ζ = σ / ω_n,   f_n = ω_n / 2π
```

（ζ が小さいとき f_n ≈ f_d）。1200 Hz サンプリングは数〜数十Hzの構造モードに十分。

---

## 手順

### 1. 記録（`pluck_test.m`）

無風・迎角固定で、Leptrino 6軸の自由減衰を記録する MATLAB スクリプト。
機器ドライバ（QT_ADL1 / LeptrinoLogger）は本番と同じ `measurement_control/` を使うが、
データは本番フラッター構造（`_ofst`/`_cXX`）に混ぜず `output_dir/prelim_pluck/<日付>/` に保存する。

```matlab
% MATLAB で prelim/pluck_test/pluck_test.m を実行
%   → 迎角[度]（既定0）と 計測窓[秒]（既定20）を入力
%   → 原点復帰・迎角固定のあと、Enter で記録開始
```

**記録中に「模型を手で弾いてきれいに放す → 3秒ほど待って収束 → また弾く」を5回ほど繰り返す。**
減衰は数秒で収まるので、1本の記録窓に複数 pluck を入れてよい（解析側が自動でイベントを切り分ける）。
- 曲げ（並進）モードは Fy 方向に、ねじりモードは捻りを与えると効率的に励起できる。
- **モードを分けて別々に弾く**と単一モードのきれいな減衰が得られ、精度が上がる（推奨）。
- 短い記録を1回1pluckで複数本撮る運用でも可。
- Leptrino 通信は 32bit Python（`config.json` の `python_exe`）を使う。風速計測は不要なので回さない。

記録秒数は `config.json` の `pluck_measure_sec`（無ければ既定20秒）。

### 2. 解析（`free_decay_analysis.py`）

記録CSV（CFSLGR 形式・4ヘッダ・1200Hz・6軸）から各 pluck イベントを自動セグメント化し、
イベント×軸×モードごとに f_n・ζ を推定して平均±標準偏差を出す。**自己完結**（依存は
numpy/scipy/pandas/matplotlib のみ）。実行は依存を持つ任意の Python でよい。

```bash
# フォルダ内の全記録を解析（6軸すべて）
post_process/.venv/Scripts/python.exe prelim/pluck_test/free_decay_analysis.py \
    <output_dir>/prelim_pluck/260704 --out prelim/pluck_test/results

# ファイル指定・軸/帯域指定
python free_decay_analysis.py rec01.csv rec02.csv --signals Fy,Mz --fmin 1 --fmax 100
```

主なオプション：

| オプション | 意味 | 既定 |
|---|---|---|
| `--signals` | 解析する軸（カンマ区切り） | `Fx,Fy,Fz,Mx,My,Mz`（6軸すべて） |
| `--fmin` / `--fmax` | モード探索の周波数範囲 [Hz] | 0.5 / 200 |
| `--n_modes` | 1軸あたり同定する最大モード数 | 2 |
| `--hp_cutoff` | DCドリフト除去のHPカットオフ [Hz] | 0.5 |
| `--min_snr` | pluck検出のSNRしきい値（包絡線ピーク/雑音床）。雑音のみの軸を弾く | 8 |
| `--min_cycles` | モード採用に必要な最小周期数。短い偽減衰を弾く | 5 |
| `--out` | 出力先フォルダ | 最初の入力と同じ場所 |

出力：
- `free_decay_summary.csv`（全 ファイル×イベント×軸×モード の行）。
- 診断図 `<記録名>_freedecay.png`（軸ごとに [全体波形+検出イベント区間]／[ln(包絡線)直線フィット]／[PSDピーク]）。
- 末尾に軸×モードの **f_n・ζ の平均±標準偏差**と、config 転記候補を表示。

---

## 結果の読み方

`free_decay_summary.csv` の主な列：

| 列 | 意味 |
|---|---|
| `f_d_Hz` / `f_n_Hz` | 減衰固有振動数 / （減衰を補正した）固有振動数。ζが小さければほぼ一致 |
| `zeta` / `zeta_pct` | 減衰比（無次元 / %） |
| `sigma` | 減衰率 σ = ζ·ω_n [1/s] |
| `R2_env` | ln(包絡線) 直線フィットの決定係数（**1に近いほど良い**） |
| `R2_fit` | 減衰正弦 A·e^(−σt)·cos(ω_d t+φ) 非線形フィットの R²（交差検証） |
| `zeta_logdec` | 対数減衰法による ζ（サニティ用の独立推定） |
| `n_cycles` | フィットに使った周期数（多いほど信頼できる） |
| `note` | 警告（例: 包絡線が非直線＝振幅依存減衰＝構造非線形の可能性） |

**判断のコツ**：
- ζ の信頼性は `R2_env` と ln(包絡線) の直線性で見る。`R2_env ≥ 0.9` かつ `zeta_logdec`・`R2_fit` と
  整合していれば信頼できる。集計（平均±標準偏差）は `R2_env ≥ 0.8` かつ ζ 有限の行だけを使う。
- `note` に「非直線」が付く＝減衰が振幅で変わる＝**構造非線形**の兆候（フラッター/LCO 解釈で重要）。
- どの軸に構造モードが出るか未知なら 6軸すべてを見て、`amp0`（応答振幅）の大きい軸・モードを主に採る。
  Fy≒並進（曲げ）モード、Mz≒ねじりモードに対応することが多いが、**軸→構造DOFの対応は要判断**。

## 将来：config への転記（第2層の実装時）

同定した値は `config.json` にフラットキーで追記する想定（現行 config はネストなしのフラット1階層）：

```json
"_comment_structure": "翼構造パラメータ（pluck test で同定）",
"wing_mass_kg": 0.0,
"f_n_hz":       0.0,
"zeta_s":       0.0
```

本タスクでは同定と出力までを行う。μ/Sc/フラッター速度指数の算出は値が揃ってから別途実装する。

---

## 参考文献（理論的根拠）

同定した f_n・ζ が「なぜ・どうフラッター/LCO の理論解につながるか」の根拠。段階ごとに対応させる。

**(1) 構造を1自由度ばね‑質量‑減衰系とみなす／自由減衰・対数減衰による f_n・ζ 同定**
- J.P. Den Hartog, *Mechanical Vibrations*, 4th ed., McGraw‑Hill, 1956.（対数減衰法・粘性減衰系の自由振動）
- D.J. Ewins, *Modal Testing: Theory, Practice and Application*, 2nd ed., Research Studies Press, 2000.（減衰振動からのモード同定）
- R.W. Clough, J. Penzien, *Dynamics of Structures*, 3rd ed., Computers & Structures, 2003.

**(2) ギャロッピング臨界風速（実効減衰 c − ½ρUD·A₁ の符号反転＝発散条件）**
- J.P. Den Hartog, *Mechanical Vibrations*, 1956.（Den Hartog 判定法：横方向1自由度不安定条件 A₁ = ∂C_Fy/∂α + C_D > 0）
- R.D. Blevins, *Flow‑Induced Vibration*, 2nd ed., Van Nostrand Reinhold, 1990, Ch.4（ギャロッピング臨界風速 U_crit と構造減衰・質量の関係）。

**(3) 非線形準定常モデルによる LCO 振幅（空力入力エネルギー＝構造減衰散逸のつり合い）**
- G.V. Parkinson, J.D. Smith, "The square prism as an aeroelastic non‑linear oscillator," *Quarterly Journal of Mechanics and Applied Mathematics*, 17(2), 225–239, 1964.（準定常空気力を迎角の多項式 A₁α+A₃α³+… で表し LCO 振幅を導く古典）
- P.W. Bearman, I.S. Gartshore, D.J. Maull, G.V. Parkinson, "Experiments on flow‑induced vibration of a square‑section cylinder," *Journal of Fluids and Structures*, 1(1), 19–34, 1987.

**(4) 無次元数（Scruton 数 Sc=4πmζ/ρD²・換算風速 U_R=U/f_nD）による普遍化**
- R.D. Blevins, *Flow‑Induced Vibration*, 1990.
- E. Naudascher, D. Rockwell, *Flow‑Induced Vibrations: An Engineering Guide*, Balkema, 1994.（Scruton 数・質量‑減衰パラメータの定義と役割）

**(5) 古典フラッター（2自由度・曲げ‑ねじり連成、Mz 軸のねじりモードへ拡張する場合）**
- Y.C. Fung, *An Introduction to the Theory of Aeroelasticity*, Dover, 1993（原著 1955）。
- R.L. Bisplinghoff, H. Ashley, R.L. Halfman, *Aeroelasticity*, Addison‑Wesley, 1955.

> 本予備実験（無風・自由減衰）が担うのは (1) の「構造側2係数」の実測。理論解を出すには、これに別途の
> **質量 m** と **定常空力の勾配 A₁ = ∂C_Fy/∂α**（`run_experiment.m`／`post_process` で取得）を掛け合わせて
> (2)–(5) の式に代入する。
