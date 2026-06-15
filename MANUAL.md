# Windy 風洞実験自動計測システム — 操作マニュアル

MATLAB + Python による迎角ステージ・6軸センサ・差圧デジボルの統合自動化システム。

---

## 1. 最短の使い方

```matlab
% ① 初回のみ：config.json を作成してポート・パス等を設定
%   config.json.example をコピー → config.json にリネーム → 中身を編集

% ② MATLAB でリポジトリルートに cd して実行
run_experiment
```

画面の指示に従うだけで計測〜後処理まで完了します。

---

## 2. リポジトリ構成

```
Windy/
├ run_experiment.m        ← 実験はこれを実行
├ run_postprocess.m       ← 後処理だけ再実行
├ setup_paths.m           ← 診断ツール用パス追加
├ config.json(.example)   設定ファイル
├ MANUAL.md               このファイル
├ README.md / SPEC.md
│
├ measurement_control/    計測機器の制御（MATLAB）
├ diagnostics/            点検・診断ツール（MATLAB）
├ leptrino/               6軸センサ通信（Python 32bit）
├ post_process/           後処理スクリプト（Python 64bit）
├ analysis/               比較パワポ生成スクリプト + データ
└ manual/                 マニュアルパワポ生成スクリプト
```

> **生成される pptx は `config.json` の `output_dir`（WindyData フォルダ）に出力されます。**

---

## 3. ルート直下のファイル

| ファイル | 役割 |
|---|---|
| `run_experiment.m` | メイン実験スクリプト。計測〜後処理まで一気通貫 |
| `run_postprocess.m` | 後処理だけを単体で再実行 |
| `setup_paths.m` | 診断ツールを使う前に1回実行（パスを通す） |
| `config.json` | 各自の環境設定（COMポート・Pythonパス・保存先）※Git管理外 |
| `config.json.example` | 設定の雛形。コピーして `config.json` を作る |
| `README.md` | クイックスタート・構成・使い方 |
| `SPEC.md` | 設計仕様書 |

---

## 4. measurement_control/ — 計測機器の制御

`run_experiment` が内部で使う MATLAB ヘルパ群。通常は直接触らない。

| ファイル | 役割 |
|---|---|
| `QT_ADL1.m` | 迎角ステージ（QT-ADL1）ドライバクラス。原点復帰・角度移動 |
| `LeptrinoLogger.m` | Leptrino 6軸センサの時系列ロガー（バックグラウンド記録） |
| `WindyMonitor.m` | 計測中リアルタイムモニタ（波形・進捗・停止ボタン） |
| `get_sensor_data.m` | 6軸センサの瞬時平均値を1回取得 |
| `get_voltage.m` | R6441B デジボル（差圧電圧）を取得 |
| `make_filename.m` | 計測ファイル名を規則に従って生成 |

> 個別に使いたいときは先に `setup_paths` を実行する。

---

## 5. diagnostics/ — 点検・診断ツール

実験前後に手動で実行して機器やセンサを確認する。

```matlab
setup_paths   % 最初に1回だけ実行
QT_ADL1_check_connection   % ステージ接続確認
weight_check               % 力センサ検証
tare_measure               % ゼロ点・6軸力表示
check_sensor_limit         % センサ定格確認
```

| ファイル | 役割 |
|---|---|
| `QT_ADL1_check_connection.m` | 迎角ステージの接続・通信確認 |
| `check_sensor_limit.m` | 6軸センサの定格（最大計測レンジ）確認 |
| `weight_check.m` | 既知のおもりを載せて力センサの読みを検証 |
| `tare_measure.m` | ゼロ点を取り、その基準からの6軸力を表示 |
| `lumix_check_connection.py` | カメラ（LUMIX DC-G100D）の接続確認 |
| `lumix_capture.py` | カメラのシャッター制御＋画像ダウンロード（実験中の翼撮影に使用） |

---

## 6. leptrino/ と post_process/

### leptrino/ — 6軸センサ通信（32bit Python 経由）

| ファイル | 役割 |
|---|---|
| `leptrino_server.py` | センサ計測サーバ。MATLAB から呼ばれ CSV へ記録 |
| `CfsUsb.dll` | Leptrino USB ドライバ（**32bit 専用**） |

> **重要：** `CfsUsb.dll` は 32bit 専用。`config.json` の `python_exe` には **32bit Python** を指定すること。

### post_process/ — 後処理（64bit Python）

| ファイル | 役割 |
|---|---|
| `make_windspeed.py` | 差圧電圧 → 風速 `windspeed.csv` |
| `calc_force.py` | 6軸力 → 空力係数 `C_aero.csv` + グラフ PNG |
| `extract_airfoil.py` | 翼模型の写真 → 翼型輪郭（x/c, y/c）を抽出（後述） |
| `naca0012.csv` | 翼型輪郭抽出で重ね描きする参照翼型 |
| `requirements.txt` | 必要 Python パッケージ一覧 |
| `venv/` | 自動生成される仮想環境（Git 管理外） |

`run_experiment` 完了時に `venv` 構築から自動実行される。失敗時は `run_postprocess('…')` で再実行。

---

## 7. analysis/ と manual/ — スクリプトは repo、出力は WindyData

スクリプト・データは repo 内に管理し、生成された **pptx のみ `output_dir`（WindyData）に保存**される。

### analysis/

| ファイル | 役割 |
|---|---|
| `update_aero_data.py` | 新実験の `C_aero.csv` を取り込み → 比較パワポ再生成（これ1つでOK） |
| `make_rigid_comparison_local.py` | 比較パワポを生成する本体スクリプト |
| `研究室MTGテンプレート.pptx` | パワポの雛形（研究室フォーマット・入力ファイル） |
| `aero_data/` | 各実験の空力係数データ `C_aero.csv` の置き場 |
| `archive/` | 使い終わった単発スクリプト・旧資料 |

出力: `Windy新システムによる実験結果.pptx` → **`output_dir/`** に保存

```bash
# 手動で比較パワポを更新する場合
cd analysis
python update_aero_data.py
```

実験名に `rigid` を含む場合、後処理の最後に「過去データと比較しますか？」と聞かれ、`y` で自動更新される。

### manual/

| ファイル | 役割 |
|---|---|
| `make_manual_pptx.py` | マニュアル PowerPoint を生成するスクリプト |

出力: `Windy_操作マニュアル.pptx` → **`output_dir/`** に保存

---

## 8. データの流れ

出力は実験フォルダの中で **`force_measurement/`（生データ）** と
**`post_process/`（解析結果）** に分かれる。

```
run_experiment.m
    │
    ▼
output_dir/<実験名>/
    ├ force_measurement/             ← 生データ（計測で生成）
    │   ├ data/                      各計測点の6軸センサ CSV
    │   ├ photo/                     翼模型の写真（撮影する場合・後述）
    │   ├ *_volt_summary.csv         フェーズ毎の差圧電圧
    │   └ *_experiment_log.json      気温・気圧・校正定数
    │
    ▼  post_process/                 ← 解析結果（後処理で生成）
    │   ├ windspeed.csv              風速
    │   ├ C_aero.csv                 空力係数
    │   ├ *.png                      グラフ
    │   ├ zero_lift_report.json      ゼロ揚力角の推定
    │   └ airfoil/                   翼型輪郭（写真を撮り、抽出した場合）
    │
    ▼  analysis/  （rigid 実験のみ）
    └ output_dir/Windy新システムによる実験結果.pptx  ← 自動更新
```

---

## 9. 実験中の翼模型の写真撮影（任意）

`run_experiment` の開始時（実験フォルダ作成後）に「写真を撮影しますか？ [y/n]」と聞かれる。
`y` を選ぶと、**通風フェーズ（Pdata / Mdata）の各迎角で翼模型を3枚ずつ自動撮影**し、
`force_measurement/photo/` サブフォルダに保存する。

**事前準備：**
- カメラ（LUMIX DC-G100D）の電源を入れ、Wi-Fi を有効化
- PC をカメラの SSID（`G100D-xxxxxx`）に接続
- 最初の通風フェーズの直前に接続確認が走るので、カメラ画面の許可確認で「はい」を選ぶ

**撮影タイミングと枚数：**
- 通風フェーズのみ（無風のオフセットフェーズでは撮影しない）
- 0°（各通風フェーズの最初）＋ 各目標迎角で3枚ずつ
- 力計測の保存後（迎角を保持したまま）に撮影するため、撮影の失敗・遅延が力データに影響しない

**ファイル名（既存の画像解析 `extract_airfoil.py` の命名規則に準拠）：**

| 迎角 | ファイル名 |
|---|---|
| 0° | `0deg1.JPG`, `0deg2.JPG`, `0deg3.JPG` |
| +1° | `p1deg1.JPG`, `p1deg2.JPG`, `p1deg3.JPG` |
| −1° | `m1deg1.JPG`, `m1deg2.JPG`, `m1deg3.JPG` |

**仕組み：** `diagnostics/lumix_capture.py` が cam.cgi(ポート80) でシャッターを切り、
再生モードに切り替えてカメラ内蔵 DLNA（ポート60606）から撮影画像をダウンロードする。
撮影に失敗しても警告を出すだけで計測は継続する（力データが主・写真は補助）。

> **DLNA がうまく動かない場合：** カメラの DLNA 構成は機種・ファームで差がある。
> `lumix_capture.py` 冒頭の定数（`DLNA_PORT`・`DDD_CANDIDATES`・`CDS_CONTROL_DEFAULT`）を
> 実機に合わせて調整する。接続だけ確認するには `python lumix_capture.py --check`。

### 撮影画像からの翼型輪郭抽出（`post_process/extract_airfoil.py`）

`force_measurement/photo/` の画像から、各迎角の翼型輪郭（x/c, y/c）を抽出する。
従来の `windtunnel_picture_analysis/extract_airfoil4.py` の輪郭抽出部分
（緑マーカー検出 → 射影変換 → 迎角で回転 → 赤エッジ抽出 → 翼弦長で正規化）を移植したもの。
**1迎角3枚の写真は輪郭を平均して1本にまとめる。** PARSEC フィットは行わない。

- **実行：** 後処理（`run_postprocess`）の最後に「翼型輪郭も抽出しますか？ [y/n]」で呼べる。
  単体実行も可：`cd <実験フォルダ>; python <repo>/post_process/extract_airfoil.py`
  （`force_measurement/photo/` を読み `post_process/airfoil/` に出力）
- **入力：** `force_measurement/photo/<label><shot>.JPG`（`0deg1.JPG`, `p1deg1.JPG` …）／参照 `naca0012.csv`
- **出力（`post_process/airfoil/`）：**
  - `contour/<label>.csv` … 3枚平均した正規化輪郭（x/c, y/c）
  - `contour/<label>.png` … 平均輪郭 + NACA0012 重ね描き
  - `contour/<label>_profile.csv` … 共通 x/c 上の上面・下面 y
  - `shots/<label><shot>.csv` … 各写真の輪郭（ばらつき確認用）
  - `overlay_all.png` … 全迎角の輪郭を重ねた図
  - `debug/` … `--debug` 時のみ（緑マスク・射影・回転・赤マスクの中間画像）

> **HSV閾値の調整：** 照明で輪郭がうまく出ない場合、`force_measurement/photo/airfoil_control.csv`
> （列は従来の `control.csv` と同じ）を置くと迎角ごとに HSV 閾値を上書きできる。
> まず `--debug` で中間画像を見て緑・赤のマスク具合を確認する。
> **回転補正：** 従来コードは迎角に +1° 補正していた（`--rotate-offset` で変更可）。

---

## 10. ゼロ揚力角からの原点パルス自動修正

後処理完了後、以下のような対話が表示される：

```
==== ゼロ揚力角からの原点パルス修正 ====
  推定ゼロ揚力角 α₀ : +0.772°
  現在の原点パルス  : 11025 pulse
  推奨の原点パルス  : 10832 pulse  (補正 +193 pulse)
  ゼロ揚力角の設定(origin_pulse)をこの推奨値に修正しますか？ [y/n]:
```

- `y` → `config.json` の `origin_pulse` が自動更新され、次回の実験から新しい原点で計測される
- `n` → 現状維持
- 既に推奨値と一致している場合はスキップ

**仕組み：**
1. `calc_force.py` が線形域からゼロ揚力角 α₀ を推定
2. 推奨値 = 現在の原点パルス − round(α₀ × 250)
3. `y` で `config.json` の `origin_pulse` を書き換え

> **注意：** α₀ はキャンバー翼など α₀≠0 が物理的に正しい場合もある。推奨値の適用は計測者が判断すること。

---

## 11. よく使うコマンド早見表

```matlab
% 実験を最初から実行（計測〜後処理〜比較まで）
run_experiment

% 後処理だけを単体で（再）実行
run_postprocess('C:\...\WindyData\260615_rigid')

% 診断ツール・ヘルパを単体で使う前の準備
setup_paths
```

| コマンド | 用途 |
|---|---|
| `run_experiment` | 実験を最初から実行 |
| `run_postprocess('実験フォルダ')` | 後処理だけやり直し・過去実験の再処理 |
| `setup_paths` | 診断ツールを使う前にパスを通す |
| `QT_ADL1_check_connection` | ステージ接続確認（要 setup_paths） |
| `weight_check` | 力センサ検証（要 setup_paths） |
| `tare_measure` | ゼロ点・力確認（要 setup_paths） |
