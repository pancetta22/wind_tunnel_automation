function run_postprocess(exp_dir, cfg)
%% run_postprocess.m
%  後処理（windspeed → 空力係数・グラフ → 過去データ比較）だけを実行する。
%
%  使い方（後処理だけやり直したい時・過去実験を再処理したい時）:
%      run_postprocess('C:\Users\...\WindyData\260615_rigid')
%
%   - 気温・気圧・校正値は実験フォルダ内の experiment_log.json から自動で読む
%   - post_process/venv が無い・壊れている場合は自動で作成・修復する
%   - 実験名に "rigid" を含む場合は過去データとの比較（analysis/ のパワポ更新）も選べる
%
%  ※ run_experiment の実験完了時にも、この関数が内部的に呼ばれる（後処理の本体）。

root = fileparts(mfilename('fullpath'));

% --- 引数・設定 ---------------------------------------------------------
if nargin < 1 || isempty(exp_dir)
    error('使い方: run_postprocess(''実験フォルダのパス'')\n  例: run_postprocess(''C:\\...\\WindyData\\260615_rigid'')');
end
if ~isfolder(exp_dir)
    error('実験フォルダが見つかりません: %s', exp_dir);
end
if nargin < 2
    config_path = fullfile(root, 'config.json');
    if ~isfile(config_path)
        error('config.json が見つかりません: %s', config_path);
    end
    cfg = jsondecode(read_text_utf8_(config_path));
end
if ~isfield(cfg, 'python_exe_64'), cfg.python_exe_64 = ''; end
if ~isfield(cfg, 'python_exe'),    cfg.python_exe    = ''; end

% --- 実験日の特定（experiment_log.json のファイル名から）------------------
logs = dir(fullfile(exp_dir, '*_experiment_log.json'));
if isempty(logs)
    error(['experiment_log.json が見つかりません: %s\n' ...
           '  run_experiment で計測した実験フォルダを指定してください。'], exp_dir);
end
[~, newest] = max([logs.datenum]);   % 複数日にまたがる場合は最新を使う
date_str = regexp(logs(newest).name, '^\d{8}', 'match', 'once');
if isempty(date_str)
    error('experiment_log.json の名前から実験日を判定できません: %s', logs(newest).name);
end

make_ws_path = fullfile(root, 'post_process', 'make_windspeed.py');
calc_f_path  = fullfile(root, 'post_process', 'calc_force.py');

% --- Step 0: 仮想環境の準備 ----------------------------------------------
py64 = cfg.python_exe_64;
if isempty(py64)
    fprintf('[警告] python_exe_64 が config.json に設定されていません。\n');
    fprintf('         python_exe (%s) で後処理を試みます。\n\n', cfg.python_exe);
    py64 = cfg.python_exe;
end
venv_python = setup_postprocess_venv_(root, py64);

% --- Step 1: windspeed.csv 生成 ------------------------------------------
fprintf('[後処理 1/2] windspeed.csv を生成中...\n');
cmd_ws = sprintf('"%s" "%s" --volt_dir "%s" --date %s --out "%s"', ...
    venv_python, make_ws_path, exp_dir, date_str, exp_dir);
[st1, out1] = system(cmd_ws);
if ~isempty(strtrim(out1)), fprintf('%s\n', out1); end
if st1 ~= 0
    fprintf('[警告] make_windspeed.py に失敗しました（終了コード %d）。後処理を中断します。\n\n', st1);
    return
end

% --- Step 2: calc_force.py で空力係数・グラフ生成 -------------------------
fprintf('[後処理 2/2] 空力係数を計算・グラフを出力中...\n');
prev_dir = cd(exp_dir);
restore_dir = onCleanup(@() cd(prev_dir));   % エラーでも元のフォルダへ戻す
[st2, out2] = system(sprintf('"%s" "%s"', venv_python, calc_f_path));
clear restore_dir   % ここで cd(prev_dir) が走る
if ~isempty(strtrim(out2)), fprintf('%s\n', out2); end
if st2 ~= 0
    fprintf('[警告] calc_force.py に失敗しました（終了コード %d）。\n\n', st2);
    return
end

fprintf('[後処理完了] グラフを %s に保存しました。\n\n', exp_dir);

% --- Step 2.5: ゼロ揚力角からの原点パルス修正（確認の上で config.json を更新）---
prompt_origin_pulse_update_(exp_dir, fullfile(root, 'config.json'));

% --- Step 3: 過去データとの比較（rigid 実験のみ・確認の上で実行）-----------
[~, exp_name] = fileparts(exp_dir);
if contains(lower(exp_name), 'rigid')
    ans_cmp = input('過去データと比較しますか？ [y/n]: ', 's');
    if any(strcmpi(strtrim(ans_cmp), {'y', 'yes'}))
        updater = fullfile(cfg.output_dir, 'analysis', 'update_aero_data.py');
        if ~isfile(updater)
            fprintf('[比較] update_aero_data.py が見つかりません: %s\n', updater);
            fprintf('       WindyData/analysis/ に update_aero_data.py を配置してください。\n\n');
            return
        end
        % 実験フォルダの親を探索元として、空力データ同期＋パワポ再生成
        % （venv セットアップで python-pptx 含む必要モジュールは導入済み）
        src_parent = fileparts(exp_dir);
        fprintf('[比較] 過去データと比較し、WindyData/analysis/ のパワポを更新します...\n');
        [stc, outc] = system(sprintf('"%s" "%s" "%s"', ...
            venv_python, updater, src_parent));
        if ~isempty(strtrim(outc)), fprintf('%s\n', outc); end
        if stc == 0
            fprintf('[比較完了] WindyData/analysis/ の比較パワポを更新しました。\n\n');
        else
            fprintf('[比較] 失敗しました（終了コード %d）。\n\n', stc);
        end
    end
end
end


function prompt_origin_pulse_update_(exp_dir, config_path)
    % calc_force.py が出力した zero_lift_report.json を読み、
    % ゼロ揚力角から求めた推奨原点パルスを提示して、y/n で config.json を更新する。
    %   ・レポートの current_origin_pulse は「その実験を計測した時の原点」
    %     （experiment_log.json 由来）。推奨値はゼロ揚力位置の絶対座標なので、
    %     過去実験の再処理でもそのまま config に適用できる。
    report_path = fullfile(exp_dir, 'zero_lift_report.json');
    if ~isfile(report_path)
        return   % レポートが無ければ何もしない（線形域不足などでスキップされた場合）
    end
    try
        rep = jsondecode(read_text_utf8_(report_path));
    catch
        fprintf('[ゼロ揚力角] zero_lift_report.json を読めませんでした。スキップします。\n\n');
        return
    end

    cur = rep.current_origin_pulse;     % 計測時の原点
    sug = rep.suggested_origin_pulse;   % ゼロ揚力位置（機械絶対座標）

    % 現在の config 設定値（過去実験の再処理では計測時と異なることがある）
    cfg_origin = cur;
    try
        c = jsondecode(read_text_utf8_(config_path));
        if isfield(c, 'origin_pulse') && ~isempty(c.origin_pulse)
            cfg_origin = c.origin_pulse;
        end
    catch
    end

    fprintf('==== ゼロ揚力角からの原点パルス修正 ====\n');
    fprintf('  推定ゼロ揚力角 α₀ : %+.3f°\n', rep.alpha0_deg);
    fprintf('  計測時の原点パルス: %d pulse\n', cur);
    if cfg_origin ~= cur
        fprintf('  現在の設定(config): %d pulse\n', cfg_origin);
    end
    fprintf('  推奨の原点パルス  : %d pulse  (補正 %+d pulse)\n', sug, rep.correction_pulse);

    if sug == cfg_origin
        fprintf('  → 既に設定が推奨値と一致しています。修正は不要です。\n\n');
        return
    end

    ans_up = strtrim(input('  ゼロ揚力角の設定（origin_pulse）をこの推奨値に修正しますか？ [y/n]: ', 's'));
    if ~any(strcmpi(ans_up, {'y', 'yes'}))
        fprintf('  → 修正しませんでした（origin_pulse = %d のまま）。\n\n', cfg_origin);
        return
    end

    if update_config_value_(config_path, 'origin_pulse', sug)
        fprintf('  → config.json の origin_pulse を %d に更新しました。\n', sug);
        fprintf('     次回の実験から新しい原点が反映されます。\n\n');
    else
        fprintf('  [警告] config.json を更新できませんでした。手動で origin_pulse を %d にしてください。\n\n', sug);
    end
end


function ok = update_config_value_(config_path, key, value)
    % config.json の数値キーを UTF-8 のままテキスト置換で更新する
    % （日本語コメント・整形・キー順を保持。キーが無ければ先頭に追記）。
    ok = false;
    if ~isfile(config_path)
        return
    end
    txt = read_text_utf8_(config_path);
    if isempty(txt)
        return
    end
    pat = sprintf('("%s"\\s*:\\s*)(-?\\d+(?:\\.\\d+)?)', key);
    if ~isempty(regexp(txt, pat, 'once'))
        txt = regexprep(txt, pat, sprintf('$1%d', value), 'once');
    else
        % キーが無い場合は最初の { の直後に1行追加
        txt = regexprep(txt, '\{', sprintf('{\n  "%s": %d,', key, value), 'once');
    end
    fid = fopen(config_path, 'w', 'n', 'UTF-8');
    if fid < 0
        return
    end
    fwrite(fid, txt, 'char');
    fclose(fid);
    ok = true;
end


function txt = read_text_utf8_(path)
    % UTF-8 を明示してテキストを読む。
    % fileread は MATLAB の既定エンコーディング依存のため、日本語コメントを含む
    % config.json を扱う際に文字化け・破損しないようこちらを使う。
    txt = '';
    fid = fopen(path, 'r', 'n', 'UTF-8');
    if fid < 0
        return
    end
    txt = fread(fid, [1, Inf], '*char');
    fclose(fid);
end


function venv_python = setup_postprocess_venv_(root, python_exe_64)
    % post_process/venv を用意して venv の python パスを返す。
    %   1. venv が無ければ 64bit Python で作成 → requirements をインストール
    %   2. 既存 venv のパッケージが足りなければ再インストール
    %   3. それでも直らなければ venv フォルダを自動削除して作り直す
    %  → 半端な venv / 32bit で作られた古い venv でも自動で復旧する。

    venv_dir = fullfile(root, 'post_process', 'venv');
    req_path = fullfile(root, 'post_process', 'requirements.txt');

    if ispc
        venv_python = fullfile(venv_dir, 'Scripts', 'python.exe');
    else
        venv_python = fullfile(venv_dir, 'bin', 'python');
    end

    % 1回目: 既存 venv で試す / 2回目: 削除して作り直して試す
    for attempt = 1:2

        % --- 2回目は venv フォルダを丸ごと削除してから作り直す ---
        if attempt == 2
            fprintf('[後処理] venv が正常に使えないため、削除して作り直します...\n');
            if isfolder(venv_dir)
                try
                    rmdir(venv_dir, 's');
                catch ME
                    error(['[後処理] venv フォルダを削除できませんでした: %s\n' ...
                           '  手動で次を削除してください: %s'], ME.message, venv_dir);
                end
            end
        end

        % --- venv 本体が無ければ作成 ---
        if ~isfile(venv_python)
            fprintf('[後処理] 仮想環境を作成しています（64bit Python）: %s\n', venv_dir);
            fprintf('         使用 Python: %s\n', python_exe_64);
            [st, out] = system(sprintf('"%s" -m venv "%s"', python_exe_64, venv_dir));
            if ~isempty(strtrim(out)), fprintf('%s\n', out); end
            if st ~= 0 || ~isfile(venv_python)
                if attempt == 1, continue; end   % 作成失敗 → 作り直しへ
                error(['[後処理] 仮想環境の作成に失敗しました（終了コード %d）。\n' ...
                       '  config.json の "python_exe_64" に 64bit Python(.exe) の正しいパスを設定してください。\n' ...
                       '  指定値: %s'], st, python_exe_64);
            end
        end

        % --- 主要パッケージが導入済みか確認（pip show は引用符問題が無く安全）---
        [s_pd, ~] = system(sprintf('"%s" -m pip show pandas',      venv_python));
        [s_px, ~] = system(sprintf('"%s" -m pip show python-pptx', venv_python));
        if s_pd == 0 && s_px == 0
            return   % 正常 → そのまま使う
        end

        % --- 不足 → pip でインストール ---
        fprintf('[後処理] 必要パッケージをインストールしています...\n');
        system(sprintf('"%s" -m pip install --upgrade pip -q', venv_python));
        [~, out] = system(sprintf('"%s" -m pip install -r "%s"', venv_python, req_path));
        if ~isempty(strtrim(out)), fprintf('%s\n', out); end

        % --- インストール後の再確認 ---
        [s_pd2, ~] = system(sprintf('"%s" -m pip show pandas',      venv_python));
        [s_px2, ~] = system(sprintf('"%s" -m pip show python-pptx', venv_python));
        if s_pd2 == 0 && s_px2 == 0
            fprintf('[後処理] パッケージのセットアップ完了。\n\n');
            return   % 正常
        end

        % --- まだダメ ---
        if attempt == 1
            fprintf('[後処理] 既存 venv では復旧できませんでした。作り直します。\n');
            % continue（次の attempt で削除→再作成）
        else
            error(['[後処理] venv を作り直してもパッケージを導入できませんでした。\n' ...
                   '  64bit Python のパス（python_exe_64）と pip のネットワーク接続を確認してください。\n' ...
                   '  Python: %s'], python_exe_64);
        end
    end
end
