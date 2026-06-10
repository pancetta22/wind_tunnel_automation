# 後処理スクリプト (post_process/)

`run_experiment.m` で生成したデータから空力係数（Cl, Cd, Cm）を算出するスクリプト群。

---

## ファイル一覧

| ファイル | 説明 |
|---------|------|
| `calc_force.py` | 6軸力データから空力係数を算出（既存スクリプト改修版） |
| `make_windspeed.py` | 差圧電圧サマリー → `windspeed.csv` 変換スクリプト |
| `requirements.txt` | 必要 Python パッケージ一覧（venv 自動構築で使用） |
| `venv/` | 自動生成される仮想環境（Git 管理外） |

---

## 通常の使い方：自動実行（推奨）

**手動操作は不要。** `run_experiment` で実験を完走すると、後処理は自動で実行される：

1. `post_process/venv` が無ければ **64bit Python**（`config.json` の `python_exe_64`）で
   自動作成し、`requirements.txt` のパッケージを自動インストール
   （venv が壊れていた場合は自動で作り直す）
2. `make_windspeed.py` を実行 → `windspeed.csv` を実験フォルダに生成
   （ρ は実験時に入力した気温・気圧から、電圧オフセットは Pofst 計測値から自動設定）
3. `calc_force.py` を実行 → 空力係数 CSV・グラフ PNG を実験フォルダに生成
4. 実験名に "rigid" を含む場合、「過去データと比較しますか？」→ **y** で
   `analysis/` の比較パワポも自動更新

片側のみの計測（正 or 負だけ）や、迎角範囲・刻み幅を変えた計測にも対応している。

---

## 後処理だけをやり直したい場合

MATLAB で1行（venv 修復・気温気圧の読込みも全部自動）：

```matlab
run_postprocess('C:\Users\...\WindyData\260615_rigid')
```

後処理がネットワーク障害などで失敗した時のリトライや、
過去実験の再処理（グラフ範囲を変更した後など）もこれだけでよい。

### Python を直接叩きたい場合（上級者向け）

```bat
post_process\venv\Scripts\python post_process\make_windspeed.py ^
    --volt_dir <実験フォルダ> --date YYYYMMDD --out <実験フォルダ>
post_process\venv\Scripts\python post_process\calc_force.py
```

（`--rho` 等は省略すると実験フォルダ内の experiment_log.json から自動取得）

| make_windspeed.py オプション | 必須 | 説明 |
|-----------|:---:|------|
| `--volt_dir` | ✔ | `volt_summary.csv` があるフォルダ |
| `--date` | ✔ | 実験日 `YYYYMMDD` |
| `--rho` | ✔ | 空気密度 [kg/m³]（気温・気圧から計算） |
| `--k` | | 差圧係数 [Pa/mV]（デフォルト: 0.0741） |
| `--v_offset` | | 無風時の電圧オフセット [mV]（デフォルト: 0.0） |
| `--out` | | `windspeed.csv` の保存先（省略時: カレントディレクトリ） |

### 差圧係数 `k` の求め方

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

> ⚠️ 計測前に差圧 mV の妥当性も確認すること（デジボルの接触不良で mV が
> 過小になると、風速・動圧が過小評価され空力係数がすべて過大になる。260605〜260610 で実証済み）。

---

## 出力ファイル（実験フォルダに生成）

| 出力ファイル | 内容 |
|------------|------|
| `windspeed.csv` | 各計測点の風速 |
| `av_Forces.csv` | 各計測点の 6軸力 平均・標準偏差 |
| `Pofst_Ncm.csv` / `Mofst_Ncm.csv` | オフセット補正済み無風力 |
| `Pdata_Ncm.csv` / `Mdata_Ncm.csv` | オフセット補正済み有風力（風速列付き）|
| `F_adcenter_Nm.csv` | 空力中心まわりの力（校正行列適用後）|
| `F_aero_Nm.csv` | 揚力・抗力座標系に変換後 |
| `C_aero_raw.csv` | 無次元空力係数（壁補正前）|
| `C_aero.csv` | 壁補正済み空力係数（**比較パワポの入力**）|
| `Cl.png` / `Cd.png` / `Cm.png` | 空力係数グラフ（表示範囲 ±30°）|
| `polar.png` | Cl–Cd 極曲線 |
| `Cl_PM.png` / `Cd_PM.png` / `Cm_PM.png` | 正・負迎角比較グラフ（両側計測時のみ）|

---

## 既存スクリプトからの変更点

| 変更内容 | 変更前（旧システム）| 変更後（新システム）|
|---------|--------------|--------------|
| `average()` のファイル数 | 2ファイル/計測点（生CSV + fc10Hz版）| **1ファイル/計測点**（生CSVのみ）|
| `_volt_raw.csv` の除外 | 不要（そのようなファイルはなかった）| **自動除外**（data/ に混在していてもOK）|
| 迎角の取得 | 行番号から推定 | **ファイル名から取得**（刻み幅変更・片側計測に対応）|
| `windspeed.csv` のフォーマット | 変更なし | 変更なし（完全互換）|
| 物理定数（翼面積・校正行列・壁補正係数）| 変更なし | **変更なし** |

---

## 必要な Python パッケージ

`requirements.txt` 参照（pandas / numpy / scipy / matplotlib / tqdm / python-pptx）。
**通常は run_experiment が venv に自動インストールするため手動インストール不要。**
