function flutter_run_postprocess(target_dir, mode, cfg, lco)
%% flutter_run_postprocess.m
% フラッター実験の後処理（flutter_analysis.py）をバックグラウンドで実行する。
%   完了を待たずにすぐ返るため実験を止めない。
%
% 使い方:
%   flutter_run_postprocess(cond_dir, 'exp',  cfg)       % LCO なし（軽量・計測中向け）
%   flutter_run_postprocess(base_dir, 'base', cfg, true) % LCO あり（全条件完了後向け）
%
%   lco（第4引数）: true = --lco 付き、false/省略 = --lco なし
%
% post_process/flutter_launch_bg.py をランチャーとして使う。
% ランチャーが subprocess.Popen で flutter_analysis.py を切り離して起動し
% 即終了するため、MATLAB は完了を待たずに次へ進める。
% エラーは <target_dir>/postprocess_error.log に出力される（正常時は生成されない）。

    root = fileparts(fileparts(mfilename('fullpath')));

    if nargin < 2 || isempty(mode), mode = 'exp';  end
    if nargin < 4 || isempty(lco),  lco  = false; end
    if nargin < 3 || isempty(cfg)
        config_path = fullfile(root, 'config.json');
        if ~isfile(config_path)
            warning('[後処理] config.json が見つかりません: %s', config_path);
            return
        end
        try
            cfg = jsondecode(fileread(config_path));
        catch ME
            warning('Windy:flutterPostproc:configRead', ...
                '[後処理] config.json の読込みに失敗しました: %s', ME.message);
            return
        end
    end
    if ~isfield(cfg, 'python_exe_64'), cfg.python_exe_64 = ''; end
    if ~isfield(cfg, 'python_exe'),    cfg.python_exe    = ''; end

    if ~any(strcmpi(mode, {'exp', 'base'}))
        warning('[後処理] 未知の mode: %s', mode);
        return
    end

    launcher = fullfile(root, 'post_process', 'flutter_launch_bg.py');
    if ~isfile(launcher)
        warning('[後処理] flutter_launch_bg.py が見つかりません: %s', launcher);
        return
    end

    % --- venv の準備 ---
    py64 = cfg.python_exe_64;
    if isempty(py64)
        fprintf('[後処理] python_exe_64 が未設定のため python_exe で試みます。\n');
        py64 = cfg.python_exe;
    end
    try
        venv_python = setup_postprocess_venv(root, py64);
    catch ME
        warning('Windy:flutterPostproc:venvSetup', ...
            '[後処理] 仮想環境の準備に失敗しました（解析はスキップ）: %s', ME.message);
        return
    end

    % --- ランチャー経由でバックグラウンド起動 ---
    % MATLAB の system() はコマンドライン引数を cp932 でエンコードするため、
    % 日本語パスが Python 側で文字化けする。環境変数経由で渡すことで回避する。
    setenv('WINDY_BG_TARGET', target_dir);
    setenv('WINDY_BG_MODE',   mode);
    setenv('WINDY_BG_LCO',    mat2str(logical(lco)));   % 'true' / 'false'
    cmd = sprintf('"%s" "%s"', venv_python, launcher);
    [st, out] = system(cmd);
    setenv('WINDY_BG_TARGET', '');
    setenv('WINDY_BG_MODE',   '');
    setenv('WINDY_BG_LCO',    '');
    if ~isempty(strtrim(out)), fprintf('%s\n', strtrim(out)); end

    if st == 0
        lco_label = '';
        if lco, lco_label = ', --lco'; end
        fprintf('[後処理] バックグラウンドで解析を開始しました（--%s%s）\n', mode, lco_label);
        fprintf('         対象: %s\n', target_dir);
        fprintf('         失敗時ログ: %s\n\n', fullfile(target_dir, 'postprocess_error.log'));
    else
        warning('[後処理] ランチャーの起動に失敗しました（終了コード %d）。実験は続行します。', st);
    end
end
