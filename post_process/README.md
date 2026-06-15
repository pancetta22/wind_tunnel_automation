# 後処理スクリプト (post_process/)

`run_experiment.m` で生成したデータから空力係数（Cl, Cd, Cm）を算出するスクリプト群。

---

## ファイル一覧

| ファイル | 説明 |
|---------|------|
| `calc_force.py` | 6軸力データから空力係数を算出（既存スクリプト改修版） |
| `make_windspeed.py` | 差圧電圧サマリー → `windspeed.csv` 変換スクリプト |
| `extract_airfoil.py` | 翼模型の写真（`photo/`）→ 各迎角の翼型輪郭を抽出（3枚平均）|
| `naca0012.csv` | 翼型輪郭抽出で重ね描きする参照翼型 |
| `requirements.txt` | 必要 Python パッケージ一覧（venv 自動構築で使用） |
| `venv/` | 自動生成される仮想環境（Git 管理外） |

---

## 通常の使い方：自動実行（推奨）

**手動操作は不要。** `run_experiment` で実験を完走すると、後処理は自動で実行される：

1. `post_process/venv` が無ければ **64bit Python**（`config.json` の `python_exe_64`）で
   自動作成し、`requirements.txt` のパッケージを自動インストール
   （venv が壊れていた場合は自動で作り直す）
2. `make_windspeed.py` を実行 → `windspeed.csv` を `post_process/` に生成
   （ρ は実験時に入力した気温・気圧から、電圧オフセットは Pofst 計測値から自動設定）
3. `calc_force.py` を実行 → 空力係数 CSV・グラフ PNG を `post_process/` に生成
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

新構成（`force_measurement/` と `post_process/` に分離）では、生データを
`force_measurement/` から読み、結果を `post_process/` に書く：

```bat
:: windspeed は force_measurement を読み post_process へ出力
post_process\venv\Scripts\python post_process\make_windspeed.py ^
    --volt_dir <実験フォルダ>\force_measurement --date YYYYMMDD ^
    --out <実験フォルダ>\post_process
:: calc_force は post_process をカレントにして実行（data/・log は ../force_measurement を自動参照）
cd <実験フォルダ>\post_process
<repo>\post_process\venv\Scripts\python <repo>\post_process\calc_force.py
```

（旧フラット構成の実験フォルダでは、従来どおり `--volt_dir <実験フォルダ>`・
`--out <実験フォルダ>` とし、`calc_force.py` は実験フォルダ直下で実行する）

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

## 出力ファイル（`<実験フォルダ>/post_process/` に生成）

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

## 翼型輪郭の抽出（`extract_airfoil.py`）

実験中に翼模型を撮影した場合（`run_experiment` の写真撮影を有効化）、`photo/` の
画像から各迎角の翼型輪郭（x/c, y/c）を抽出できる。従来の
`windtunnel_picture_analysis/extract_airfoil4.py` の輪郭抽出部分
（緑マーカー検出 → 射影変換 → 迎角で回転 → 赤エッジ抽出 → 翼弦長で正規化）を移植。
**1迎角3枚の写真は輪郭を平均して1本にまとめる**（PARSEC フィットは行わない）。

```matlab
% run_postprocess の最後に「翼型輪郭も抽出しますか？ [y/n]」で呼べる
run_postprocess('C:\Users\...\WindyData\260615_rigid')
```

```bat
:: 単体実行（既定で ./photo を読み ./airfoil に出力）
cd <実験フォルダ>
post_process\venv\Scripts\python <repo>\post_process\extract_airfoil.py
:: 中間画像を見て HSV を調整したいとき
... extract_airfoil.py --debug
```

| 入力 | 内容 |
|------|------|
| `force_measurement/photo/<label><shot>.JPG` | `0deg1.JPG`, `p1deg1.JPG`, `m1deg1.JPG` …（従来式 `<label>.JPG` も可）|
| `naca0012.csv` | 参照翼型（本フォルダに同梱）|
| `force_measurement/photo/airfoil_control.csv` | 任意。迎角ごとに HSV 閾値を上書き（列は従来 `control.csv` と同じ）|

| 出力（`<実験フォルダ>/post_process/airfoil/`）| 内容 |
|------|------|
| `contour/<label>.csv` | 3枚平均した正規化輪郭（x/c, y/c）|
| `contour/<label>.png` | 平均輪郭 + NACA0012 重ね描き |
| `contour/<label>_profile.csv` | 共通 x/c 上の上面・下面 y |
| `shots/<label><shot>.csv` | 各写真の輪郭（ばらつき確認用）|
| `overlay_all.png` | 全迎角の輪郭を重ねた図 |
| `debug/` | `--debug` 時のみ（緑マスク・射影・回転・赤マスク）|

> 照明等で輪郭がうまく出ない場合は `--debug` で中間画像を確認し、`airfoil_control.csv`
> で HSV を調整する。回転補正は従来 +1°（`--rotate-offset` で変更可）。

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

`requirements.txt` 参照（pandas / numpy / scipy / matplotlib / tqdm / python-pptx /
opencv-python-headless）。**通常は run_experiment が venv に自動インストールするため
手動インストール不要**（翼型輪郭抽出を使う際、既存 venv に OpenCV が無ければ
run_postprocess が自動で追加する）。
