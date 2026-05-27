# 後処理スクリプト (post_process/)

`run_experiment.m` で生成したデータから空力係数（Cl, Cd, Cm）を算出するスクリプト群。

---

## ファイル一覧

| ファイル | 説明 |
|---------|------|
| `calc_force.py` | 6軸力データから空力係数を算出（既存スクリプト改修版） |
| `make_windspeed.py` | 差圧電圧サマリー → `windspeed.csv` 変換スクリプト |

---

## 実行手順

### ステップ 1：解析フォルダの準備

```
analysis_20260520/          ← 実験日ごとに作成する任意フォルダ
├── data/                   ← 6軸センサ CSV を配置（全4フェーズ 244ファイル）
│   ├── 20260520_093355_260520_Pofst_00.00.csv
│   ├── 20260520_093526_260520_Pofst_01.01.csv
│   └── ...
├── make_windspeed.py       ← このフォルダにコピー
└── calc_force.py           ← このフォルダにコピー
```

`data/` フォルダへのファイル配置：
- `run_experiment` の `output_dir` から **6軸センサ CSV のみ**コピー
- `_volt_raw.csv` が混在していても `calc_force.py` が自動除外するので問題なし
- `_volt_summary.csv` は `data/` に入れない（ステップ 2 で参照するのみ）

---

### ステップ 2：windspeed.csv の生成

有風フェーズ（Pdata / Mdata）の差圧電圧から風速を計算します。

```bat
cd analysis_20260520
python make_windspeed.py ^
    --volt_dir C:\Users\...\WindyData ^
    --date     20260520 ^
    --rho      1.165 ^
    --k        0.0741
```

| オプション | 必須 | 説明 |
|-----------|:---:|------|
| `--volt_dir` | ✔ | `volt_summary.csv` があるフォルダ（`run_experiment` の `output_dir`） |
| `--date` | ✔ | 実験日 `YYYYMMDD` |
| `--rho` | ✔ | 空気密度 [kg/m³]（気温・気圧から計算） |
| `--k` | | 差圧係数 [Pa/mV]（デフォルト: 0.0741） |
| `--v_offset` | | 無風時の電圧オフセット [mV]（デフォルト: 0.0） |
| `--out` | | `windspeed.csv` の保存先（省略時: カレントディレクトリ） |

#### 差圧係数 `k` の求め方

風速計で実測した風速 U [m/s] と差圧電圧 V [mV]、空気密度 ρ [kg/m³] から：

```
k [Pa/mV] = U² × ρ / (2 × V)
```

過去の実験値：

| 実験 | rho | 代表 mV | 代表 U | k |
|-----|-----|---------|-------|---|
| 250924 | 1.165 | 1160 | 12.148 | 0.0741 |
| 260520 | 1.164 | 1170 | 12.259 | 0.0748 |

センサや日によって微妙に変化するため、**実験ごとに校正を推奨**します。

---

### ステップ 3：空力係数の算出

```bat
python calc_force.py
```

`data/` フォルダの 6軸 CSV と `windspeed.csv` を読み込んで以下を生成します：

| 出力ファイル | 内容 |
|------------|------|
| `av_Forces.csv` | 各計測点の 6軸力 平均・標準偏差 |
| `Pofst_Ncm.csv` / `Mofst_Ncm.csv` | オフセット補正済み無風力 |
| `Pdata_Ncm.csv` / `Mdata_Ncm.csv` | オフセット補正済み有風力（風速列付き）|
| `F_adcenter_Nm.csv` | 空力中心まわりの力（校正行列適用後）|
| `F_aero_Nm.csv` | 揚力・抗力座標系に変換後 |
| `C_aero_raw.csv` | 無次元空力係数（壁補正前）|
| `C_aero.csv` | 壁補正済み空力係数 |
| `Cl.png` / `Cd.png` / `Cm.png` | 空力係数グラフ |
| `polar.png` | Cl–Cd 極曲線 |
| `Cl_PM.png` / `Cd_PM.png` / `Cm_PM.png` | 正・負迎角比較グラフ |

---

## 既存スクリプトからの変更点

| 変更内容 | 変更前（旧システム）| 変更後（新システム）|
|---------|--------------|--------------|
| `average()` のファイル数 | 2ファイル/計測点（生CSV + fc10Hz版）| **1ファイル/計測点**（生CSVのみ）|
| `_volt_raw.csv` の除外 | 不要（そのようなファイルはなかった）| **自動除外**（data/ に混在していてもOK）|
| `windspeed.csv` のフォーマット | 変更なし | 変更なし（完全互換）|
| 物理定数（翼面積・校正行列・壁補正係数）| 変更なし | **変更なし** |

---

## 必要な Python パッケージ

```
pandas
numpy
matplotlib
scipy
tqdm
```

インストール：
```bat
pip install pandas numpy matplotlib scipy tqdm
```
