# フラッター実験 後処理（`flutter_analysis.py` / `lco_analysis.py`）

`flutter_run_experiment.m` で取得したフラッター実験データ（`_ofst` / `_cXX` 構成）を
解析するスクリプト群。RMS・Welch PSD・卓越周波数・ストローハル数・迎角×周波数マップ・
フラッター発生マップに加え、`--lco` で LCO（リミットサイクル振動）非線形解析
（位相図・Poincaré・分岐図・風速版スペクトログラムほか）を出力する。

> 剛体翼（定常空力）実験の後処理は [README.md](README.md) を参照。
> 風速較正の計算式・定数、venv の詳細は剛体翼版と共通なのでそちらにまとめてある。

---

## 入力フォルダ構成

`flutter_run_experiment.m` は次の構成でデータを吐く：

```
WindyData/
├ 260624_flutter_ofst/            Pofst / Mofst（全風速条件で共有するオフセット）
│   ├ data/
│   └ <YYYYMMDD>_experiment_log.json
├ 260624_flutter_c01/             風速条件①
│   ├ data/
│   └ <YYYYMMDD>_experiment_log.json  ← ofst_dir / rep_windspeed_U を含む
├ 260624_flutter_c02/             風速条件②
└ …
```

- `_ofst` は実験冒頭で1回だけ撮り、全風速条件で共有する。
- 各 `_cXX` の `experiment_log.json` に代表風速（`rep_windspeed_U`）・共有オフセットの
  場所（`ofst_dir`）・気温気圧などが記録される。

---

## ファイル一覧

| ファイル | 説明 |
| --- | --- |
| `flutter_analysis.py`        | **後処理 入口**。RMS・Welch PSD・卓越周波数・St・迎角×周波数マップ・フラッター発生マップ |
| `lco_analysis.py`            | LCO 非線形解析（位相図・Poincaré・分岐図・風速版スペクトログラムほか）。`--lco` で有効化。1CSVの単体実行も可 |
| `LCO_ANALYSIS_GUIDE.md`      | **LCO の図・指標の読み方ガイド**（応答タイプ早見表つき）。[LCO_ANALYSIS_GUIDE.md](LCO_ANALYSIS_GUIDE.md) |
| `flutter_run_postprocess.m`  | MATLAB から後処理をバックグラウンド起動する（`flutter_launch_bg.py` をランチャーに使う） |
| `flutter_launch_bg.py`       | 後処理を子プロセスとして切り離して起動し即終了するランチャー（計測を止めない） |

---

## 通常の使い方：自動実行（推奨）

**手動操作は不要。** `flutter_run_experiment` を使うと後処理は
[flutter_run_postprocess.m](../measurement_control/flutter_run_postprocess.m) 経由で自動で走る。
タイミングは2段階：

1. **各風速条件（`_cXX`）の計測が終わるたび**に `--exp_dir` でその条件だけ随時解析する
   （早く結果が見られる。計測中に重い LCO 解析を走らせないよう **`--lco` は付けない**＝軽量）。
2. **全条件の計測完了後**に `--base_dir --lco` で全条件横断のマップ・LCO 図一式を生成する。

後処理が失敗しても計測は止まらない（warning を表示して続行し、
`<対象>/postprocess_error.log` にエラーを残す）。venv は `run_postprocess` と共通の
`setup_postprocess_venv.m` が用意する（`post_process/venv` または既存 `.venv` を自動利用）。

---

## 手動で実行する

Python を直接叩く場合の基本形（3系統）：

```bash
# 全風速条件を一括処理（フラッター発生マップまで出力）
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter

# 1条件だけ処理（途中確認用）
python flutter_analysis.py --exp_dir C:/WindyData/260624_flutter_c01

# LCO 非線形解析つきで一括処理
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --lco

# 1つの6軸CSVだけで素早く動作確認（カルテ図＋指標）
python lco_analysis.py C:/WindyData/.../20260624_..._Pdata_15.01.csv
```

MATLAB から後処理だけやり直したい場合は、venv の Python を明示して叩く
（剛体翼 [README.md](README.md) と同じ流儀）：

```bat
post_process\venv\Scripts\python post_process\flutter_analysis.py --base_dir <実験ベースフォルダ> --lco
```

### ⚠️ Gドライブ上のデータは「ローカルにコピーしてから」処理する（重要）

**Google 共有ドライブ（`G:\共有ドライブ\...`）上のフォルダを直接 `--base_dir`
に渡すと、処理が極端に遅くなる。** 計算自体は各計測点2秒程度と正常なのに、
本スクリプトが生成する大量の PNG（1条件あたり約124枚 × 風速条件数）を
Gドライブへ連続書き込みすると、Google ドライブの同期キューが詰まり、条件の
切れ目で数十分ストールする（実測：本来15分の処理が1時間超でも未完了）。

回避手順（手動でやり直すときは必ずこれ）：

```bash
# 0) 走行中の後処理が無いことを確認（重複起動は競合書き込みを生む）
ps aux | grep python

# 1) 実験フォルダをローカルへコピー（フォルダ名は元のまま！ _local 等を付けない）
cp -r "G:/共有ドライブ/.../260701_flutter" /c/tmp/260701_flutter

# 2) 古いバイトコードを消す（St_Fy/St_Mz 等の列欠落を防ぐ）
rm -rf post_process/__pycache__

# 3) ローカルコピーに対して処理（ストール無しで本来の速度で完走）
post_process/.venv/Scripts/python.exe post_process/flutter_analysis.py \
    --base_dir /c/tmp/260701_flutter --lco

# 4) 生成物を Gドライブへ一括書き戻し（1ファイルずつより速い）
#    PowerShell: robocopy C:\tmp\260701_flutter "G:\...\260701_flutter" /E

# 5) ローカル一時コピーを削除
rm -rf /c/tmp/260701_flutter
```

- フォルダ名を変えないこと：`--base_dir` はフォルダ名から `<base>_c\d+$` の
  正規表現で条件フォルダ（`_c01` …）を検出するため、`_local` などを付けると
  「条件フォルダが見つかりません」で即エラーになる。
- `_ofst` の `experiment_log.json` 内 `ofst_dir` は元のGパスを指すが、コード側に
  条件フォルダ名から `<親>/<base>_ofst` を自動探索するフォールバックがあるので、
  ローカルでもオフセットは解決される。
- **同じ処理を複数プロセスで同時に走らせない**（バックグラウンド連打は禁物）。
  同一フォルダへの競合書き込みで同期と書き戻しが壊れる。

---

## 出力されるグラフ・CSV（どれが自動で出て、どれが手動指定か）

各出力の「出力トリガ」を次の3カテゴリで示す：

- **(A) 常に自動** … 自動実行①②のどちらでも出る（`--lco` 不要）。
- **(B) 自動②のみ** … 全条件処理（自動②＝`--base_dir --lco`）でしか出ない。
  1条件だけ手動で出したいなら自分で `--exp_dir --lco` を指定する。
- **(C) 手動指定のみ** … `--lco_spec_aoa` など**追加オプションを自分で付けないと狙って出せない**図。

### 条件ごと（各 `_cXX/` に出力）

| 出力 | 内容 | トリガ |
| --- | --- | --- |
| `figures/<名前>.png`        | 時系列3版／PSD／RMS時間推移 | **(A) 常に自動** |
| `flutter_summary.csv`       | 迎角・RMS各成分・卓越周波数・St・フラッター判定A/B（`--lco`時はLCO指標列を追加） | **(A) 常に自動** |
| `aoa_freq_map_Fy/Mz.png`    | 迎角×周波数マップ | **(A) 常に自動** |
| `strouhal_aoa.png`          | St–迎角プロット | **(A) 常に自動** |
| `figures/<名前>_lco.png`    | LCO カルテ図（時系列／位相図／スペクトル） | **(B) 自動②のみ**（1条件で欲しければ `--exp_dir --lco`） |
| `phase_sweep_Fy/Mz.png`     | 迎角に沿った位相図スイープ | **(B) 自動②のみ** |

### 全条件（`<base>_results/` に出力。`--base_dir` 時のみ）

| 出力 | 内容 | トリガ |
| --- | --- | --- |
| `flutter_map_Fy/Mz_A_threshold.png`, `..._B_snr.png` | フラッター発生マップ（ルートA/B） | **(B) 自動②のみ** |
| `strouhal_fu.png`           | 卓越周波数×風速＋等St線 | **(B) 自動②のみ** |
| `rms_overview.png` / `rms_overview_6axis.png` | 全条件・全迎角のRMS概観（Fy/Mz・6成分） | **(B) 自動②のみ** |
| `aoa_freq_panel.png`        | 風速条件を縦積みした迎角×周波数マップ | **(B) 自動②のみ** |
| `bifurcation_Fy/Mz.png`     | 分岐図（迎角×Poincaré値） | **(B) 自動②のみ** |
| `freq_coalescence.png`      | 周波数合流図（Fy/Mz の卓越周波数×風速） | **(B) 自動②のみ** |
| `lco_metric_map_Fy/Mz.png`  | 迎角×風速のLCO指標（loop_thickness）マップ | **(B) 自動②のみ** |
| `spectrogram_speed_<Fy\|Mz>_aoa±NN.png` | **風速版スペクトログラム**（迎角固定・横軸風速） | **(B/C)**：自動②では振幅最大の迎角が自動選択で出る／**狙った迎角は `--lco_spec_aoa` を指定** |

**出力の仕方（まとめ）**：`--exp_dir` は条件レベル (A) まで、`--base_dir` を使うと
全条件図 (B) が加わる。`--lco` で LCO 図が有効化され、`--lco_spec_aoa` を付けて初めて
狙った迎角の風速スペクトログラム (C) が出る。

---

## 特定の迎角で「異なる風速の挙動」を比較したいとき

> **迎角を固定して風速ごとの挙動を比較する図は
> `spectrogram_speed_<Fy|Mz>_aoa±NN.png`（風速版スペクトログラム）です。**
> 横軸=風速 U・縦軸=周波数・濃淡=PSD[dB] で、指定した1つの迎角について
> 風速を上げたとき卓越周波数がどう動くか（合流・分岐・ロックイン）を追える。

紛らわしい他の図との違い（誤読しないよう対比）：

| 図 | 横軸 | 迎角の扱い |
| --- | --- | --- |
| `spectrogram_speed_*` | **風速** | **1つに固定**（これが迎角固定×風速比較） |
| `freq_coalescence.png` | 風速 | 固定せず全計測点を重ねる（全体の周波数合流傾向） |
| `bifurcation_*` | 迎角 | 迎角掃引が主役（風速は色分け） |
| `aoa_freq_panel.png` | 迎角 | 風速条件を縦に積むが各行の横軸は迎角 |

出し方：

```bash
# 迎角 +18° と -20° について、風速スイープのスペクトログラムを出す
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --lco --lco_spec_aoa 18,-20
```

- `--lco` は**必須**（この図は `--lco` 経路でのみ生成される）。
- `--lco_spec_aoa` 未指定でも、`--base_dir --lco` なら振幅最大の正側・負側の迎角を
  自動で1つずつ選んで出す。**見たい迎角を確実に出すには明示指定**する。
- 出力先は `<base>_results/`。指定した迎角ごとに Fy・Mz の2枚が出る
  （例：`spectrogram_speed_Fy_aoa+018.png` / `spectrogram_speed_Mz_aoa-020.png`）。

---

## 主なCLIオプション

| オプション | 意味 | 既定値 |
| --- | --- | --- |
| `--base_dir`      | 実験ベースフォルダ（`_ofst` / `_c01` … の親）。全条件を一括処理 | （`--exp_dir` と排他・どちらか必須） |
| `--exp_dir`       | 単一条件フォルダ（`_c01` など）だけ処理 | 〃 |
| `--threshold_rms` | ルートA: フラッター判定のRMS閾値 [N or Nm] | 自動推定（未設定なら判定保留） |
| `--peak_snr_db`   | ルートB: ピークが背景より何dB高ければフラッターとみなすか | `10.0` |
| `--hp_cutoff`     | ハイパスフィルタのカットオフ周波数 [Hz]（DCドリフト除去） | `1.0` |
| `--rms_window`    | LCO収束確認用のRMS窓幅 [秒] | `1.0` |
| `--edge_trim_sec` | 前処理後に両端を切り捨てる長さ [秒]（補間段差・フィルタ端の除去。0で無効） | `0.5` |
| `--map_fmax`      | 迎角×周波数マップの周波数表示上限 [Hz] | `50.0` |
| `--map_dyn_range` | マップのカラー dB ダイナミックレンジ | `60.0` |
| `--lco`           | LCO非線形解析（位相図・Poincaré・調和指標・成長率）を有効化 | 無効 |
| `--lco_signals`   | LCO解析の主軸信号（カンマ区切り） | `Fy,Mz` |
| `--lco_tau_mode`  | 時間遅れτの推定法（`zero_cross` / `quarter_period`） | `zero_cross` |
| `--lco_fmin`      | LCO調和・ピーク解析の下限周波数 [Hz] | `1.0` |
| `--lco_fmax`      | LCO調和・ピーク解析の上限周波数 [Hz] | `500.0` |
| `--lco_spec_aoa`  | 風速版スペクトログラムの対象迎角（カンマ区切り。未指定なら振幅最大の正/負を自動選択） | 空（自動選択） |

### 目的別・コピペで使えるコマンド集

```bash
# ① まず全体を普通に処理する（迷ったらこれ）
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --lco

# ② 特定の迎角で風速ごとの挙動を比べたい（風速スペクトログラム）
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --lco --lco_spec_aoa 18,-20

# ③ マップの周波数上限を100Hzまで広げて見たい
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --map_fmax 100

# ④ フラッター判定のしきい値を実データに合わせたい（ルートA/B）
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --threshold_rms 0.5 --peak_snr_db 12

# ⑤ LCO解析をFyだけに絞って軽くしたい
python flutter_analysis.py --base_dir C:/WindyData/260624_flutter --lco --lco_signals Fy

# ⑥ 1条件だけ・1CSVだけ素早く確認（動作確認）
python flutter_analysis.py --exp_dir C:/WindyData/260624_flutter_c01 --lco
python lco_analysis.py C:/WindyData/.../20260624_..._Pdata_15.01.csv
```

---

## フラッター判定の2ルート

`flutter_summary.csv` とフラッター発生マップは、次の2ルートで判定する：

- **ルートA（振幅閾値）**：RMS > `--threshold_rms` [N or Nm]。閾値未設定なら判定保留。
- **ルートB（スペクトルピーク）**：卓越ピークが背景レベル（帯域メジアン）より
  `--peak_snr_db` [dB] 以上高ければフラッター有。

デフォルトは保守的な値なので、**実験データを見てから調整する**前提
（`--threshold_rms` / `--peak_snr_db`）。

---

## LCO 解析の読み方

`--lco` で出力される位相図・Poincaré・分岐図・各指標（`loop_thickness`・
`harmonic_ratio`・`growth_rate` ほか）の**読み方と応答タイプ早見表**は
[LCO_ANALYSIS_GUIDE.md](LCO_ANALYSIS_GUIDE.md) にまとめてある。応答タイプ
（stable / periodic / quasi-periodic / chaotic）の自動ラベル付けはせず、
図と数値指標から人が読み取る設計。
