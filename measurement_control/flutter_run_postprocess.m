function ok = flutter_run_postprocess(target_dir, mode, cfg)
%% flutter_run_postprocess.m
% フラッター実験の後処理（flutter_analysis.py）を実行する。
%   LCO 非線形解析（--lco）付きで実行する。
%
% 使い方:
%   flutter_run_postprocess(cond_dir, 'exp',  cfg)   % 単一条件（_cXX）を随時解析
%   flutter_run_postprocess(base_dir, 'base', cfg)   % 全条件の横断マップを生成
%
%   mode:
%     'exp'  → flutter_analysis.py --exp_dir  <target_dir> --lco
%     'base' → flutter_analysis.py --base_dir <target_dir> --lco
%
% 失敗しても実験は止めない（warning のみ）。戻り値 ok は成功可否（true/false）。
% venv の準備は setup_postprocess_venv（run_postprocess と共通）に委譲する。

    ok = false;
    root = fileparts(fileparts(mfilename('fullpath')));   % リポジトリルート

    if nargin < 2 || isempty(mode), mode = 'exp'; end
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

    switch lower(mode)
        case 'exp'
            arg_flag = '--exp_dir';
        case 'base'
            arg_flag = '--base_dir';
        otherwise
            warning('[後処理] 未知の mode: %s（''exp'' または ''base''）', mode);
            return
    end

    fa_path = fullfile(root, 'post_process', 'flutter_analysis.py');
    if ~isfile(fa_path)
        warning('[後処理] flutter_analysis.py が見つかりません: %s', fa_path);
        return
    end

    % --- venv の準備（失敗しても実験は止めない）---
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

    % --- flutter_analysis.py の実行（--lco 付き）---
    fprintf('[後処理] フラッター解析を実行中（%s, --lco）...\n', arg_flag);
    fprintf('         対象: %s\n', target_dir);
    cmd = sprintf('"%s" "%s" %s "%s" --lco', venv_python, fa_path, arg_flag, target_dir);
    [st, out] = system(cmd);
    if ~isempty(strtrim(out)), fprintf('%s\n', out); end

    if st == 0
        fprintf('[後処理] フラッター解析が完了しました。\n\n');
        ok = true;
    else
        warning('[後処理] flutter_analysis.py に失敗しました（終了コード %d）。実験は続行します。', st);
    end
end
