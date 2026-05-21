## セットアップ

1. `config.json.example` を `config.json` としてコピー
2. `config.json` を自分の環境に合わせて編集
   - `python_exe`: Python実行ファイルのパス
   - `leptrino_port`: 天秤のCOMポート番号（デバイスマネージャーで確認）
3. `config.json` は `.gitignore` により Git の管理対象外です（コミットしないでください）

## 注意事項

### Python は 32bit 版を使用すること

`CfsUsb.dll` は 32bit 版の DLL です。
64bit 版の Python を使用すると DLL の読み込みに失敗します。

- ✅ Python 3.x (32bit) — `python.exe` のパスに `Python312-32` のように `-32` が含まれるもの
- ❌ Python 3.x (64bit) — 通常のインストーラでインストールしたもの

### 32bit 版 Python のインストール

1. https://www.python.org/downloads/ にアクセス
2. 目的のバージョンのページを開く
3. インストーラ一覧から **Windows installer (32-bit)** を選択してインストール
4. インストール先のパスを `config.json` の `python_exe` に設定する