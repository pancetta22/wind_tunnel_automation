## 手順

**1. MATLAB のターミナル（またはコマンドプロンプト）で実行**

```bat
cd C:\path\to\Windy

post_process\venv\Scripts\python post_process\fix_windspeed.py ^
    --base_dir C:\Users\YourName\WindyData\260620_flexible ^
    --dry_run
```

`--dry_run`で表示が正しければ、外して本番実行します。

```bat
post_process\venv\Scripts\python post_process\fix_windspeed.py ^
    --base_dir C:\Users\YourName\WindyData\260620_flexible
```

---

## MATLABから直接実行する場合

```matlab
venv_python = fullfile(fileparts(mfilename('fullpath')), 'post_process', 'venv', 'Scripts', 'python.exe');
script      = fullfile(fileparts(mfilename('fullpath')), 'post_process', 'fix_windspeed.py');
base_dir    = 'C:\Users\YourName\WindyData\260620_flexible';

% まず確認
system(sprintf('"%s" "%s" --base_dir "%s" --dry_run', venv_python, script, base_dir));

% 問題なければ本番
system(sprintf('"%s" "%s" --base_dir "%s"', venv_python, script, base_dir));
```

---

## 配置場所の確認

`fix_windspeed.py`は`post_process/`フォルダに置いてください。

```
Windy/
└── post_process/
    └── fix_windspeed.py    ← ここ
```

全文をきれいなmdファイルになおして

---

# gemini response

```markdown
# 風速データ修正スクリプト（fix_windspeed.py）の実行手順

本手順は、符号が反転してしまった風速データのログファイルを一括修正するためのものです。事前に`post_process/`フォルダ内にスクリプトが配置されていることを確認してください。

---

## 📂 配置場所の確認

`fix_windspeed.py`は以下の構造になるよう配置してください。

```text
Windy/
└── post_process/
    └── fix_windspeed.py    ← ここに配置
```

> **Note:** `post_process/venv`（仮想環境）が構築済みであることを前提としています。

---

## 🛠️ 実行手順

### 方法1：コマンドプロンプト（またはMATLABのターミナル）から実行

まずは `--dry_run` オプションを付けて実行し、書き込みを行わずに修正内容を確認します。

```bat
cd C:\path\to\Windy

post_process\venv\Scripts\python post_process\fix_windspeed.py ^
    --base_dir C:\Users\YourName\WindyData\260620_flexible ^
    --dry_run
```

表示された修正内容に問題がなければ、`--dry_run` を外して本番実行します。

```bat
post_process\venv\Scripts\python post_process\fix_windspeed.py ^
    --base_dir C:\Users\YourName\WindyData\260620_flexible
```

---

### 方法2：MATLABから直接実行

MATLABのコマンドウィンドウやスクリプトから実行する場合は、以下のコードを使用します。

```matlab
venv_python = fullfile(fileparts(mfilename('fullpath')), 'post_process', 'venv', 'Scripts', 'python.exe');
script      = fullfile(fileparts(mfilename('fullpath')), 'post_process', 'fix_windspeed.py');
base_dir    = 'C:\Users\YourName\WindyData\260620_flexible';

% まずはドライラン（確認のみ）
system(sprintf('"%s" "%s" --base_dir "%s" --dry_run', venv_python, script, base_dir));

% 問題なければ本番実行
system(sprintf('"%s" "%s" --base_dir "%s"', venv_python, script, base_dir));
```

---

## 📝 実行時の出力イメージ

実行すると、以下のように処理プロセスが表示されます。

```text
[一括修正] 3 条件を処理します。

  260620_flexible_c01:
    rep_windspeed_mV : -1234.56 mV  →  +1234.56 mV
    rep_windspeed_U  : 0.0000 m/s   →  12.3456 m/s
    → experiment_log.json を更新しました

  260620_flexible_c02:
    ...

完了: 3 / 3 条件を修正しました。
```

* `ofst`フォルダは自動的にスキップされます。
* 修正完了後、通常通り `flutter_analysis.py` を再実行すれば、フラッター発生マップの縦軸（風速）が正しい値でプロットされます。
```