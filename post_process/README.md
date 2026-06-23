# 後処理スクリプト (post_process/)

力計測・写真・過去データ比較の後処理を**すべてここに一元化**したスクリプト群。
仮想環境（`venv/`）もここにのみ作る。出力はすべて `output_dir`（WindyData）の
各実験フォルダに保存する。

## 出力フォルダ構成（`output_dir/<実験名>/`）

```
<実験名>/
├ <YYYYMMDD>_experiment_log.json   気温・気圧・校正定数（実験直下）
├ force/
│   ├ data/         生データ（6軸CSV・volt_summary・volt_raw）
│   ├ analysis/     windspeed.csv・C_aero.csv・*.png・zero_lift_report.json
│   └ comparison/   過去剛体翼との比較パワポ
└ picture/          写真と翼型輪郭（photo/・Gmarkers/・…・plot/・control.csv）
```

---

## ファイル一覧

| ファイル | 説明 |
|---------|------|
| `force_measurement.py` | **力の後処理 入口**。windspeed → calc_force を順に実行 |
| `photo_import.py` | SDカードの実験写真を撮影manifestに従い `0deg1.JPG` 等へ取り込み |
| `picture_analysis.py` | **写真の後処理 入口**。翼模型写真 → 翼型輪郭（3枚平均） |
| `make_comparison.py` | 過去剛体翼との比較パワポを生成（WindyData を走査＋同梱過去分） |
| `make_windspeed.py` | 差圧電圧サマリー → `windspeed.csv`（force_measurement が呼ぶ） |
| `calc_force.py` | 6軸力 → 空力係数（force_measurement が呼ぶ） |
| `naca0012.csv` | 翼型輪郭抽出の参照翼型 |
| `assets/` | 比較の同梱資産（`aero_data/`＝過去データ・テンプレ pptx・`archive/`） |
| `requirements.txt` / `venv/` | パッケージ一覧 / 自動生成される仮想環境（Git 管理外） |

---

## 通常の使い方：自動実行（推奨）

**手動操作は不要。** `run_experiment` で実験を完走すると、後処理は自動で実行される：

1. `post_process/venv` が無ければ **64bit Python**（`config.json` の `python_exe_64`）で
   自動作成し、`requirements.txt` のパッケージを自動インストール
   （venv が壊れていた場合は自動で作り直す）
2. `force_measurement.py` を実行 → `force/analysis/` に `windspeed.csv`・空力係数・グラフ
   （ρ は実験時の気温・気圧から、電圧オフセットは Pofst 計測値から自動設定）
3. 写真があれば「翼型輪郭も抽出しますか？」→ **y** で `picture_analysis.py` を実行
4. 実験名に "rigid" を含む場合、「過去データと比較しますか？」→ **y** で
   `make_comparison.py` が `force/comparison/` に比較パワポを生成

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

力の後処理は `force_measurement.py` に実験フォルダを渡すだけ
（内部で make_windspeed → calc_force を正しいパスで実行する）：

```bat
post_process\venv\Scripts\python post_process\force_measurement.py <実験フォルダ>
```

（旧フラット構成の実験フォルダ＝data/ と log が直下にある場合もそのまま動く。
`--rho` 等は省略すると experiment_log.json から自動取得）

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

## 出力ファイル（`<実験フォルダ>/force/analysis/` に生成）

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

## 写真の取り込み（`photo_import.py`・SDカード方式）

LUMIX DC-G100D はリモート操作（cam.cgi）中はDLNAで画像を配信しないため、
実験中の**ライブ転送はできない**。そこで撮影は次の2段構えで行う：

1. **実験中**：`run_experiment` がシャッターだけ切り、SDカードに保存する。
   各ショットの撮影順・迎角ラベル・成否を `picture/photo/_shot_manifest.csv` に記録。
2. **実験後**：SDカードの写真をPCの任意フォルダにコピーし、`photo_import.py` で
   撮影順に `0deg1.JPG`・`p5deg1.JPG`… へリネーム取り込みする。

```bat
:: まず割り当てを確認（--dry-run）→ よければ外して実行
post_process\venv\Scripts\python <repo>\post_process\photo_import.py ^
    --sd "<SDからコピーした写真フォルダ>" ^
    --manifest "<実験フォルダ>\picture\photo\_shot_manifest.csv" ^
    --out "<実験フォルダ>\picture\photo" --dry-run
```

対応づけは「manifest の成功ショットを撮影順に」「SDのJPEGをファイル名順（=撮影順）に」
並べ、**最新 N 枚**を実験写真として先頭から割り当てる（SDに過去写真が残っていてもよい）。
取り込み後は下記の `picture_analysis.py` に進める。`run_postprocess` は写真が未取り込み
（manifestだけ存在）の場合に上記コマンドを案内する。

---

## 翼型輪郭の抽出（`picture_analysis.py`）

実験中に翼模型を撮影した場合、`picture/photo/` の画像から各迎角の翼型輪郭
（x/c, y/c）を抽出する。従来の `windtunnel_picture_analysis/extract_airfoil4.py`
の輪郭抽出部分（緑マーカー検出 → 射影変換 → 迎角で回転 → 赤エッジ抽出 → 翼弦長で
正規化）を移植。出力は従来 picture_analysis と同じサブフォルダ構成。
**1迎角3枚の写真は輪郭を平均して1本にまとめる**（PARSEC フィットは行わない）。

```matlab
% run_postprocess の最後に「翼型輪郭も抽出しますか？ [y/n]」で呼べる
run_postprocess('C:\Users\...\WindyData\260615_rigid')
```

```bat
:: 単体実行（picture/photo を読み picture/ 配下に出力）
post_process\venv\Scripts\python <repo>\post_process\picture_analysis.py ^
    --photo_dir <実験フォルダ>\picture\photo --out <実験フォルダ>\picture
```

| 入力 | 内容 |
|------|------|
| `picture/photo/<label><shot>.JPG` | `0deg1.JPG`, `p1deg1.JPG`, `m1deg1.JPG` …（従来式 `<label>.JPG` も可）|
| `naca0012.csv` | 参照翼型（本フォルダに同梱）|
| `picture/control.csv` | 迎角ごとの HSV 閾値・flag（無ければ既定を自動生成。編集して再実行で調整）|

| 出力（`<実験フォルダ>/picture/`）| 内容 |
|------|------|
| `Gmarkers/ warp/ rotate/ Redge/` | 各処理ステップの中間画像（`<label><shot>`）|
| `plot/<label>.csv` | 3枚平均した正規化輪郭（x/c, y/c）|
| `plot/<label>.png` | 平均輪郭 + NACA0012 重ね描き |
| `plot/<label>_profile.csv` | 共通 x/c 上の上面・下面 y |
| `overlay_all.png` | 全迎角の輪郭を重ねた図 |

> 照明等で輪郭がうまく出ない場合は中間画像（Gmarkers/ など）を確認し、
> `picture/control.csv` の HSV を編集して再実行する（`flag`=0 の迎角はスキップ）。
> 回転補正は従来 +1°（`--rotate-offset` で変更可）。

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
