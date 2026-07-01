function run_postprocess(exp_dir, cfg)
%% run_postprocess.m
%  後処理（windspeed → 空力係数・グラフ → 過去データ比較）だけを実行する。
%
%  使い方（後処理だけやり直したい時・過去実験を再処理したい時）:
%      run_postprocess('C:\Users\...\WindyData\260615_rigid')
%
%   - 気温・気圧・校正値は実験フォルダ直下の experiment_log.json から自動で読む
%   - post_process/venv が無い・壊れている場合は自動で作成・修復する
%   - 写真があれば翼型輪郭の抽出（picture/）も y/n で選べる
%   - 実験名に "rigid" を含む場合は過去データとの比較（force/comparison/ に出力）も選べる
%
%  ※ run_experiment の実験完了時にも、この関数が内部的に呼ばれる（後処理の本体）。

root = fileparts(mfilename('fullpath'));
addpath(fullfile(root, 'measurement_control'));

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

% --- 解析結果・比較・写真の各フォルダ（新構成 force/・picture/ / 旧フラット）---
[analysis_dir, comparison_dir, photo_dir] = resolve_layout_(exp_dir);

% 後処理スクリプト（すべて post_process/ に一元化）
fm_path  = fullfile(root, 'post_process', 'force_measurement.py');
pic_path = fullfile(root, 'post_process', 'picture_analysis.py');
cmp_path = fullfile(root, 'post_process', 'make_comparison.py');

% --- Step 0: 仮想環境の準備 ----------------------------------------------
py64 = cfg.python_exe_64;
if isempty(py64)
    fprintf('[警告] python_exe_64 が config.json に設定されていません。\n');
    fprintf('         python_exe (%s) で後処理を試みます。\n\n', cfg.python_exe);
    py64 = cfg.python_exe;
end
venv_python = setup_postprocess_venv(root, py64);

% --- Step 1: 力計測の後処理（windspeed → 空力係数・グラフ）-----------------
%   force_measurement.py が make_windspeed と calc_force を順に実行し、
%   結果を force/analysis/ に出力する（旧フラット構成は実験フォルダ直下）。
fprintf('[後処理] 力データを処理中（windspeed → 空力係数）...\n');
[st1, out1] = system(sprintf('"%s" "%s" "%s"', venv_python, fm_path, exp_dir));
if ~isempty(strtrim(out1)), fprintf('%s\n', out1); end
if st1 ~= 0
    fprintf('[警告] force_measurement.py に失敗しました（終了コード %d）。後処理を中断します。\n\n', st1);
    return
end
fprintf('[後処理完了] 空力データを %s に保存しました。\n\n', analysis_dir);

% --- Step 2: ゼロ揚力角からの原点パルス修正（確認の上で config.json を更新）---
prompt_origin_pulse_update_(analysis_dir, fullfile(root, 'config.json'));

% --- Step 3: 翼模型の写真から翼型輪郭を抽出（picture/photo がある時のみ・確認の上で）---
prompt_picture_analysis_(photo_dir, venv_python, pic_path);

% --- Step 4: 過去データとの比較（rigid 実験のみ・確認の上で実行）-----------
[~, exp_name] = fileparts(exp_dir);
if contains(lower(exp_name), 'rigid')
    ans_cmp = input('過去データと比較しますか？ [y/n]: ', 's');
    if any(strcmpi(strtrim(ans_cmp), {'y', 'yes'}))
        if ~isfile(cmp_path)
            fprintf('[比較] make_comparison.py が見つかりません: %s\n\n', cmp_path);
            return
        end
        % WindyData(output_dir) の各実験 force/analysis/C_aero.csv を走査し、
        % 同梱の過去データと合わせて比較パワポを comparison_dir に出力する。
        if ~isfolder(comparison_dir), mkdir(comparison_dir); end
        fprintf('[比較] 過去データと比較し、比較パワポを生成します...\n');
        [stc, outc] = system(sprintf('"%s" "%s" --scan "%s" --out "%s"', ...
            venv_python, cmp_path, cfg.output_dir, comparison_dir));
        if ~isempty(strtrim(outc)), fprintf('%s\n', outc); end
        if stc == 0
            fprintf('[比較完了] 比較パワポを %s に保存しました。\n\n', comparison_dir);
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
        % 風速スイープ構成の場合、V_* サブフォルダ内を探す
        v_dirs = dir(fullfile(exp_dir, 'V_*'));
        v_dirs = v_dirs([v_dirs.isdir]);
        if ~isempty(v_dirs)
            report_path = fullfile(exp_dir, v_dirs(1).name, 'zero_lift_report.json');
        end
        if ~isfile(report_path)
            return   % レポートが無ければ何もしない
        end
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


function [analysis_dir, comparison_dir, photo_dir] = resolve_layout_(exp_dir)
    % 出力構成を解決して (analysis_dir, comparison_dir, photo_dir) を返す。
    %   新構成: exp_dir/force/{data,analysis,comparison} と exp_dir/picture/photo
    %   旧構成(フラット): analysis/comparison は exp_dir、写真は exp_dir/photo
    % 判定は force/data の有無で行う。
    if isfolder(fullfile(exp_dir, 'force', 'data'))
        analysis_dir   = fullfile(exp_dir, 'force', 'analysis');
        comparison_dir = fullfile(exp_dir, 'force', 'comparison');
        photo_dir      = fullfile(exp_dir, 'picture', 'photo');
        if ~isfolder(analysis_dir), mkdir(analysis_dir); end
    else
        analysis_dir   = exp_dir;
        comparison_dir = exp_dir;
        photo_dir      = fullfile(exp_dir, 'photo');
    end
end


function prompt_picture_analysis_(photo_dir, venv_python, pic_script)
    % picture/photo に写真があれば、翼型輪郭を抽出するか y/n で確認して実行する。
    %  各迎角3枚の写真から輪郭を抽出・平均し、picture/ 配下に出力する。
    if ~isfolder(photo_dir)
        return   % 写真を撮っていない実験 → 何もしない
    end
    imgs = [dir(fullfile(photo_dir, '*.JPG')); dir(fullfile(photo_dir, '*.jpg'))];
    if isempty(imgs)
        return
    end
    if ~isfile(pic_script)
        fprintf('[輪郭抽出] picture_analysis.py が見つかりません: %s\n\n', pic_script);
        return
    end

    picture_dir = fileparts(photo_dir);   % picture/photo の親 = picture/
    fprintf('[輪郭抽出] picture/photo に写真が %d 枚あります。\n', numel(imgs));
    ans_ex = strtrim(input('翼型輪郭も抽出しますか？（緑マーカー→射影→赤エッジ→正規化） [y/n]: ', 's'));
    if ~any(strcmpi(ans_ex, {'y', 'yes'}))
        fprintf('  → 抽出しませんでした（後で実行: python picture_analysis.py --photo_dir "%s" --out "%s"）\n\n', ...
                photo_dir, picture_dir);
        return
    end

    % OpenCV(cv2) が venv に無ければ導入する（既存 venv は requirements 変更を
    % 検知しないため、ここで明示的に確認・追加する）。
    [s_cv, ~] = system(sprintf('"%s" -c "import cv2"', venv_python));
    if s_cv ~= 0
        fprintf('[輪郭抽出] OpenCV を venv に導入しています...\n');
        [~, out_pip] = system(sprintf('"%s" -m pip install opencv-python-headless', venv_python));
        if ~isempty(strtrim(out_pip)), fprintf('%s\n', out_pip); end
    end

    fprintf('[輪郭抽出] 翼型輪郭を抽出中...\n');
    [st_ex, out_ex] = system(sprintf('"%s" "%s" --photo_dir "%s" --out "%s"', ...
        venv_python, pic_script, photo_dir, picture_dir));
    if ~isempty(strtrim(out_ex)), fprintf('%s\n', out_ex); end
    if st_ex == 0
        fprintf('[輪郭抽出完了] %s に保存しました。\n\n', picture_dir);
    else
        fprintf('[輪郭抽出] 失敗しました（終了コード %d）。\n', st_ex);
        fprintf('  HSV閾値の調整が必要な場合があります（picture/control.csv で調整）。\n\n');
    end
end
