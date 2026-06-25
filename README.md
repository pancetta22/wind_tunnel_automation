# Windy — 風洞実験計測制御システム

MATLAB + Python による風洞実験の自動計測・制御システムです。
迎角ステージ（QT-ADL1）と6軸力覚センサ（Leptrino）、デジタルマルチメータ（R6441B）を統合して実験を制御します。

## クイックスタート（最短手順）

```matlab
% 1. config.json.example をコピーして config.json を作り、COMポート等を設定（初回のみ）
% 2. MATLAB でリポジトリのフォルダに cd して：
run_experiment
```

あとは画面の指示に従うだけ（気温・気圧 → 迎角範囲 → 計測 → 後処理・グラフ生成まで自動）。
よく使うコマンドは他に2つ：

```matlab
setup_paths                                  % 診断ツールを単体で使う前に1回実行
run_postprocess('C:\...\WindyData\実験名')    % 後処理だけをやり直す／過去実験を再処理する
```

---

## 構成機器

| 機器 | メーカー/型番 | 接続方式 | 担当ファイル |
|------|-------------|---------|------------|
| 迎角ステージコントローラ | 中央精機 QT-ADL1 | RS-232C (COMポート) | `QT_ADL1.m` |
| 6軸力覚センサ | Leptrino | USB (CfsUsb.dll) | `leptrino/leptrino_server.py` |
| デジタルマルチメータ | Advantest R6441B | RS-232C (COMポート) | `get_voltage.m` |

---

## セットアップ

### 1. リポジトリのクローン

```bash
git clone <リポジトリURL>
cd Windy
```

### 2. 設定ファイルの作成

`config.json.example` をコピーして `config.json` を作成します。

```bash
copy config.json.example config.json   # Windows
```

`config.json` を自分の環境に合わせて編集します。

```json
{
  "python_exe": "C:/Users/<YourName>/AppData/Local/Programs/Python/Python312-32/python.exe",
  "leptrino_port": 5,
  "qt_adl1_port": "COM7"
}
```

| キー | 説明 | 確認方法 |
|-----|------|---------|
| `python_exe` | **32bit** Python の実行ファイルのフルパス | インストール先フォルダを確認 |
| `leptrino_port` | Leptrino センサの COM ポート番号（数字のみ） | デバイスマネージャー |
| `qt_adl1_port` | QT-ADL1 の COM ポート文字列 | デバイスマネージャー |
| `r6441b_port` | R6441B の COM ポート文字列 | デバイスマネージャー |
| `r6441b_n_samples` | 取得サンプル数 | — |
| `r6441b_timeout_sec` | 受信タイムアウト [秒] | — |

> ⚠️ `config.json` は `.gitignore` により Git 管理対象外です。コミットしないでください。

### 3. Python（32bit 版）のインストール

`CfsUsb.dll` は 32bit 版の DLL のため、**必ず 32bit 版の Python を使用してください**。
64bit 版では DLL の読み込みに失敗します。

1. https://www.python.org/downloads/ にアクセス
2. 目的のバージョンのページを開く
3. インストーラ一覧から **Windows installer (32-bit)** を選択してインストール
4. インストール先のパスを `config.json` の `python_exe` に設定する

インストール先の例：
```
C:/Users/<YourName>/AppData/Local/Programs/Python/Python312-32/python.exe
```

✅ パスに `Python312-32` のように **`-32`** が含まれていれば 32bit 版です。

---

## ファイル構成

```
Windy/
├── run_experiment.m            # メイン実験スクリプト（これを実行）
├── run_postprocess.m           # 後処理だけを単体で（再）実行
├── setup_paths.m               # 診断ツール等を単体で使う前に実行（パス追加）
├── config.json.example         # 設定ファイルのテンプレート
├── config.json                 # 各自の設定（Git管理外）
├── README.md / SPEC.md
│
├── measurement_control/        # run_experiment が使う計測機器ヘルパ
│   ├── QT_ADL1.m               #   迎角ステージ ドライバクラス
│   ├── LeptrinoLogger.m        #   Leptrino 6軸センサ 時系列ロガークラス
│   ├── WindyMonitor.m          #   リアルタイムモニタ表示クラス
│   ├── make_filename.m         #   ファイル名生成ユーティリティ
│   ├── get_sensor_data.m       #   Leptrinoセンサ データ取得関数
│   └── get_voltage.m           #   R6441B デジタルマルチメータ データ取得
│
├── diagnostics/                # 手動で実行する接続確認・診断ツール
│   ├── QT_ADL1_check_connection.m  # 迎角ステージ 接続確認
│   ├── check_sensor_limit.m        # 力センサ定格確認
│   ├── weight_check.m              # 既知荷重による力センサ確認
│   ├── tare_measure.m              # ゼロ点基準 6軸力測定
│   ├── lumix_check_connection.py   # カメラ接続確認
│   ├── lumix_capture.py            # シャッター制御＋画像DL（実験中の翼撮影）
│   └── lumix_test.py               # カメラ総合テスト（接続→DLNA→撮影＋保存）
│
├── leptrino/                   # Leptrinoセンサ 計測スクリプト（Python）
│   ├── leptrino_server.py
│   └── CfsUsb.dll              #   Leptrino USB ドライバ DLL（32bit）
├── post_process/               # 後処理を全て一元化（venvもここ）
│   ├── force_measurement.py    #   力の後処理 入口（windspeed→空力係数）
│   ├── picture_analysis.py     #   写真の後処理 入口（SD写真の整理＋翼型輪郭抽出）
│   ├── make_comparison.py      #   過去剛体翼との比較パワポ生成
│   ├── make_windspeed.py / calc_force.py  #   力後処理のワーカ
│   ├── naca0012.csv            #   輪郭抽出の参照翼型
│   └── assets/                 #   比較の同梱資産（過去データ・テンプレpptx）
└── manual/                     # 操作マニュアルと生成スクリプト
    └── make_manual_pptx.py     #   （pptx は output_dir に出力）
```

> 後処理の出力は `output_dir`（WindyData）の各実験フォルダに保存されます
> （`force/`＝力計測、`picture/`＝写真、ログは実験フォルダ直下）。

> ※ **本計測（`run_experiment`）はそのまま実行できます**（起動時に自分で
> `measurement_control` をパスに追加します）。
>
> ※ 診断ツールやヘルパ（`QT_ADL1_check_connection` / `weight_check` /
> `get_sensor_data` 等）を**単体で使うときは、先に一度 `setup_paths` を実行**して
> ください（measurement_control / diagnostics をパスへ追加します）。
> 各ツールは `config.json` / `leptrino/` をリポジトリルート基準で参照するため、
> パスさえ通れば従来どおり関数名・スクリプト名で実行できます。

---

## 使い方

> 以下の関数・スクリプトを単体で使う前に、リポジトリのルートで一度実行：
>
> ```matlab
> setup_paths
> ```

### 迎角ステージ（QT-ADL1）

#### 接続確認

```matlab
QT_ADL1_check_connection
```

利用可能な COM ポートの一覧を表示し、`config.json` の `qt_adl1_port` で指定したポートへの接続と通信を確認します。

#### 基本操作

```matlab
% 接続
stage = QT_ADL1('COM7');

% 原点復帰（必ず最初に実行）→ 自動で迎角0°へ移動
stage.homeReturn();

% 迎角の指定移動
stage.moveToAngle(15.0);   % 15°へ移動
stage.moveToAngle(0);      % 0°へ戻る

% 現在の迎角を取得
angle = stage.getAngle();
fprintf('現在の迎角: %.4f°\n', angle);

% 減速停止
stage.stop();

% 切断
delete(stage);
```

#### 迎角スイープ

```matlab
stage = QT_ADL1('COM7');
stage.homeReturn();

% 0°から30°まで1°ステップでスイープ、各点で2秒待機
stage.sweep(0:1:30, 2.0);

delete(stage);
```

各測定点では迎角0°を経由してから目標迎角へ移動します（ヒステリシスの影響を低減するため）。

コールバック関数を使って各迎角での計測処理を組み込むこともできます。

```matlab
stage.sweep(0:1:30, 2.0, @myMeasurementFunc);
```

#### 座標系について

迎角0°の原点は `config.json` の `origin_pulse`（既定 **11025**）で決まります。

| 迎角 | パルス値 |
|-----|---------|
| 0° | `origin_pulse`（既定 11025）pulse |
| +1° | `origin_pulse` − 250 pulse |
| +θ° | `origin_pulse` − θ × 250 pulse |

- 迎角増加方向 = パルス減少（CCW 方向）
- 分解能：0.004°/pulse（ARS-936-HP）
- 実験後にゼロ揚力角から推奨原点が提示され、**y/n で `origin_pulse` を自動更新**できます

---

### 6軸力覚センサ（Leptrino）

#### データ取得

```matlab
data = get_sensor_data();

fprintf('Fx = %.4f N\n', data.Fx);
fprintf('Fy = %.4f N\n', data.Fy);
fprintf('Fz = %.4f N\n', data.Fz);
fprintf('Mx = %.4f Nm\n', data.Mx);
fprintf('My = %.4f Nm\n', data.My);
fprintf('Mz = %.4f Nm\n', data.Mz);
```

#### 取得される値

| フィールド | 内容 |
|-----------|------|
| `Fx`, `Fy`, `Fz` | 力 [N] |
| `Mx`, `My`, `Mz` | モーメント [Nm] |
| `limit` | センサ定格値（6要素配列） |
| `n` | 平均に使用したサンプル数 |

内部では約 200Hz × 1秒間のデータを収集して平均値を返します。

#### 仕組み

MATLAB から Python スクリプト（`leptrino/leptrino_server.py`）をサブプロセスとして呼び出し、結果を JSON で受け取ります。Python 側では 32bit DLL（`CfsUsb.dll`）を経由してセンサと通信します。

---

### デジタルマルチメータ（R6441B）

`get_voltage.m` を実行すると、指定サンプル数の測定値を取得して CSV に保存します。

設定は `config.json` で管理します。

```json
{
  "r6441b_port":        "COM6",
  "r6441b_n_samples":   100,
  "r6441b_timeout_sec": 5
}
```

| キー | 説明 |
|-----|------|
| `r6441b_port` | COM ポート文字列（例：`"COM6"`） |
| `r6441b_n_samples` | 取得サンプル数 |
| `r6441b_timeout_sec` | 受信タイムアウト [秒] |

結果はターミナルに表示されます（各サンプル値・平均・標準偏差を V / mV 併記。CSV は出力しません）。
グラフ（時系列プロット）も表示されるので、計測前のデジボル接触チェック（mV の妥当性確認）に使えます。

> 通信仕様：9600bps / 8bit / パリティなし / ストップビット1 / ハードウェアフロー制御（DTR/DSR）

---

## トラブルシューティング

### DLL の読み込みに失敗する

```
DLL読み込み失敗: ...
```

Python が 64bit 版になっています。**32bit 版の Python** をインストールして `config.json` の `python_exe` を更新してください。

### PortOpen 失敗

```
PortOpen失敗
```

`config.json` の `leptrino_port` が間違っています。デバイスマネージャーで Leptrino センサの COM ポート番号を確認してください。

### センサデータが取得できない

```
データ取得失敗
```

センサが正常に動作しているか、USB ケーブルの接続を確認してください。それでも解決しない場合はセンサの電源を入れ直してください。

### QT-ADL1 が応答しない

- デバイスマネージャーで `qt_adl1_port` に指定した COM ポートが表示されているか確認
- `QT_ADL1_check_connection` スクリプトで通信確認
- ケーブル（RS-232C または USB-シリアル変換）の接続を確認

### タイムアウトエラー

```
waitForStop: タイムアウト (30 秒)
```

ステージが移動範囲の限界付近にいる可能性があります。`stage.stop()` で停止後、`stage.homeReturn()` から再実行してください。

---

## 注意事項

- 実験前に必ず `homeReturn()` を実行して機械原点を確定させてください
- スイープ中にステージを手で止めないでください
- **計測中に出力CSV（volt_summary 等）を Excel で開かないでください**
  （ファイルがロックされると、その計測点の電圧平均が記録できません）
- `config.json` はリポジトリにコミットしないでください（個人の環境情報が含まれます）
- Leptrino の Python スクリプトは必ず 32bit Python で実行してください