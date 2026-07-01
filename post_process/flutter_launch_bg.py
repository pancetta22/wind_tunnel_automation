"""
flutter_launch_bg.py — flutter_analysis.py をバックグラウンドで起動するランチャー。

MATLAB の system() から呼ばれる。subprocess.Popen(CREATE_NO_WINDOW) で
flutter_analysis.py を起動し即終了する。
MATLAB 側は system() が返った時点で次の処理へ進める。

パスは環境変数 WINDY_BG_TARGET / WINDY_BG_MODE で受け取る。
（MATLAB の system() はコマンドライン引数を cp932 でエンコードするため
  日本語パスが化けるが、環境変数は正しく引き継がれる）

エラーは <target_dir>/postprocess_error.log に書き出す（正常時は生成されない）。
"""

import sys
import os
import subprocess


def main():
    target_dir = os.environ.get("WINDY_BG_TARGET", "")
    mode       = os.environ.get("WINDY_BG_MODE",   "")

    if not target_dir or not mode:
        if len(sys.argv) >= 3:
            target_dir = sys.argv[1]
            mode       = sys.argv[2]
        else:
            print("WINDY_BG_TARGET / WINDY_BG_MODE を設定してください", file=sys.stderr)
            sys.exit(1)

    fa_path  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flutter_analysis.py")
    flag     = "--exp_dir" if mode == "exp" else "--base_dir"
    log_path = os.path.join(target_dir, "postprocess_error.log")
    use_lco  = os.environ.get("WINDY_BG_LCO", "false").lower() == "true"
    cmd      = [sys.executable, fa_path, flag, target_dir]
    if use_lco:
        cmd.append("--lco")

    try:
        log_f = open(log_path, "w", encoding="utf-8")
        stderr_dest = log_f
    except OSError:
        log_f = None
        stderr_dest = subprocess.DEVNULL

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_dest,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print(f"[後処理] PID {proc.pid} でバックグラウンド起動しました")
    except Exception as e:
        print(f"[後処理] 起動失敗: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if log_f is not None:
            log_f.close()


if __name__ == "__main__":
    main()
