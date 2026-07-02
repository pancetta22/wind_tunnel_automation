%% flutter_run_experiment.m
% フラッター実験 自動計測スクリプト（Windy）
%
% 【定常空力実験（run_experiment.m）との主な違い】
%   - 0°経由なし: 単純な単調増加スイープ（0° → 1° → 2° → ... → max°）
%   - 無風オフセット（Pofst/Mofst）は1回だけ計測して複数風速条件で共有
%   - Pdata/Mdata は秒数指定で計測（flutter_measure_sec）
%   - 風速条件ごとにフォルダを作成（c01, c02, ...）
%   - 後処理は flutter_analysis.py（flutter_run_postprocess で呼ぶ）
%
% 実行方法:
%   MATLAB コマンドウィンドウで:  flutter_run_experiment
%
% フォルダ構成:
%   output_dir/
%   └── 260620_flexible/              ← base_exp_dir（ベースフォルダ）
%       ├── 260620_flexible_ofst/     ← Pofst/Mofst
%       ├── 260620_flexible_c01/      ← 風速条件①
%       └── 260620_flexible_c02/      ← 風速条件②

addpath(fullfile(fileparts(mfilename('fullpath')), 'measurement_control'));

cfg = load_flutter_config_(fileparts(mfilename('fullpath')));

fprintf('\n========================================\n');
fprintf('  Windy フラッター実験自動計測システム\n');
fprintf('========================================\n\n');

% 前回の中断で残ったCOMポート等を解放
clear guard_stage_ guard_logger_ guard_volt_ guard_monitor_
clear stage logger s_volt monitor

% Python ビット数の確認
check_python_bits_(cfg.python_exe, 32, 'python_exe（Leptrino 計測用）');
if ~isempty(cfg.python_exe_64)
    check_python_bits_(cfg.python_exe_64, 64, 'python_exe_64（後処理用）');
end

% =====================================================================
%  0. 実験名の入力（例: 260620_flexible）
% =====================================================================
base_name = input_base_name_(cfg.output_dir);
fprintf('\n');

% =====================================================================
%  1. 機器接続
% =====================================================================
% 各機器の接続直後に個別の解放ガードを積む。接続の途中（例えば Leptrino や
% R6441B の接続失敗）で例外が出ても、その時点までに確保したガードは
% スコープ終了時に発火して確実に解放される（onCleanup はローカル変数の
% スコープ消滅時に発火するため、関数内であれば中断・エラー時も機能する）。
fprintf('[接続] 迎角ステージ (%s) に接続中...\n', cfg.qt_adl1_port);
stage = QT_ADL1(cfg.qt_adl1_port, [], cfg.origin_pulse);
stage.homeReturn();
guard_stage_ = onCleanup(@() safe_delete_(stage)); %#ok<NASGU>

fprintf('[接続] Leptrino センサ (ポート %d) を確認中...\n', cfg.leptrino_port);
script_path = fullfile(fileparts(mfilename('fullpath')), 'leptrino', 'leptrino_server.py');
logger = LeptrinoLogger(cfg.python_exe, script_path, ...
                        cfg.leptrino_port, cfg.force_sensor_size_limit_kb);
guard_logger_ = onCleanup(@() safe_stop_(logger)); %#ok<NASGU>

fprintf('[接続] R6441B デジボル (%s) に接続中...\n', cfg.r6441b_port);
s_volt = connect_r6441b_(cfg.r6441b_port, cfg.r6441b_timeout_sec);
guard_volt_ = onCleanup(@() safe_delete_(s_volt)); %#ok<NASGU>

% FlutterWindyMonitor を使用（秒数表示・ケース名表示に対応）
monitor = FlutterWindyMonitor(cfg.force_sensor_size_limit_kb);
monitor.setDataSource(@() logger.getRecentRows(3600));  % 直近3秒分（= 3.0s × 1200Hz）
guard_monitor_ = onCleanup(@() safe_close_(monitor)); %#ok<NASGU>

% =====================================================================
%  2. 実験設定（気象条件・迎角範囲・計測秒数）
% =====================================================================
t_start  = datetime('now');
date_str = sprintf('%04d%02d%02d', year(t_start), month(t_start), day(t_start));

met = input_met_conditions_();

[max_angle, angle_step, measure_sec] = configure_flutter_sweep_(cfg);

fprintf('[設定] 計測秒数: %.1f 秒/計測点（Pdata/Mdata）\n', measure_sec);
fprintf('[設定] KB打ち切り: %.0f KB（Pofst/Mofst）\n\n', cfg.force_sensor_size_limit_kb);

% =====================================================================
%  3. Pofst / Mofst（無風オフセット）を1回だけ計測
% =====================================================================
fprintf('========================================\n');
fprintf('  無風オフセット計測（Pofst / Mofst）\n');
fprintf('  ※ この結果を全風速条件で共有します\n');
fprintf('========================================\n\n');

% [バグ修正] ベースフォルダ（output_dir/260620_flexible/）を先に作成し、
% ofst・c0N はその中に作る
base_exp_dir  = fullfile(cfg.output_dir, base_name);
[~, ~] = mkdir(base_exp_dir);

ofst_name     = sprintf('%s_ofst', base_name);
ofst_dir      = fullfile(base_exp_dir, ofst_name);
ofst_data_dir = fullfile(ofst_dir, 'data');
[~, ~] = mkdir(ofst_data_dir);

ofst_log_path = fullfile(ofst_dir, sprintf('%s_experiment_log.json', date_str));
met.git_commit     = git_commit_hash_();
met.origin_pulse   = cfg.origin_pulse;
met.volt_offset_mV = cfg.volt_offset_mV;
met.calib_a        = cfg.calib_a;
met.calib_b        = cfg.calib_b;

% [バグ修正] Pofst でのみブロワー停止確認を行う。Mofst は連続実行。
input('ブロワーが停止していることを確認したら Enter を押してください: ', 's');
fprintf('\n');

% 無風時の差圧電圧オフセットを自動計測し、cfg・met に反映する。
% （代表風速計算・実験ログの両方で実測オフセットを使うため、無風確認後のここで確定させる。
%   値渡しの run_ofst_phase_ 内で更新しても呼び出し元へ伝播しないため、トップレベルで行う）
measured_offset = measure_volt_offset_(s_volt);
if ~isnan(measured_offset)
    cfg.volt_offset_mV = measured_offset;
    met.volt_offset_mV = measured_offset;
    fprintf('[更新] volt_offset_mV = %.4f mV\n\n', measured_offset);
end

% 実験ログ保存（実測オフセット反映後）
save_experiment_log_(ofst_log_path, date_str, met);

monitor.setConditionLabel('ofst');
monitor.setPhase('Pofst');
run_ofst_phase_('Pofst', ofst_data_dir, ofst_dir, date_str, ...
    max_angle, angle_step, stage, logger, s_volt, monitor, cfg);

monitor.setPhase('Mofst');
run_ofst_phase_('Mofst', ofst_data_dir, ofst_dir, date_str, ...
    max_angle, angle_step, stage, logger, s_volt, monitor, cfg);

fprintf('\n=== 無風オフセット計測完了 ===\n');
fprintf('[保存先] %s\n\n', ofst_dir);
notify_sound_(2);

% =====================================================================
%  4. 風速条件ループ（Pdata / Mdata）
% =====================================================================
cond_idx = 0;

while true
    cond_idx = cond_idx + 1;
    cond_label = sprintf('c%02d', cond_idx);

    fprintf('========================================\n');
    fprintf('  風速条件 %s の計測\n', cond_label);
    fprintf('========================================\n\n');

    % ---- ブロワー起動前に迎角を 0° に戻す ----
    % （前条件の Mdata フェーズ等で 0° 以外に取り残されている可能性があるため、
    %   「風速が安定したら Enter」の表示時点では常に 0° にしておく）
    fprintf('[準備] 迎角を 0° に戻しています...\n');
    stage.moveToAngle(0);
    fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
    pause(cfg.angle_settle_sec);

    % ---- ブロワー起動・風速安定の確認 ----
    input(sprintf('ブロワーを起動し、風速が安定したら Enter を押してください: '), 's');
    fprintf('\n');

    % ---- 代表風速の自動計測 ----
    [rep_mv, rep_U] = measure_representative_windspeed_(s_volt, cfg, met);

    % ---- 実験フォルダ名の決定（ベースフォルダの中に作成）----
    cond_name     = sprintf('%s_%s', base_name, cond_label);
    cond_dir      = fullfile(base_exp_dir, cond_name);   % [バグ修正] base_exp_dir の中へ
    cond_data_dir = fullfile(cond_dir, 'data');
    [~, ~] = mkdir(cond_data_dir);

    fprintf('[フォルダ] %s\n', cond_name);
    fprintf('  代表風速: U ≈ %.2f m/s  （%.1f mV）\n\n', rep_U, rep_mv);

    % ---- 実験ログ保存（代表風速・オフセットフォルダパスを記録）----
    met_cond = met;
    met_cond.rep_windspeed_mV  = rep_mv;
    met_cond.rep_windspeed_U   = rep_U;
    met_cond.ofst_dir          = ofst_dir;
    cond_log_path = fullfile(cond_dir, sprintf('%s_experiment_log.json', date_str));
    save_flutter_log_(cond_log_path, date_str, met_cond);

    % ---- 計測前の挙動確認（任意迎角への移動、データ取得なし）----
    manual_angle_check_(stage, max_angle, cfg);

    % ---- Pdata ----
    monitor.setConditionLabel(cond_label, rep_U);
    monitor.setTimeLimitSec(measure_sec);
    monitor.setPhase('Pdata');
    run_data_phase_('Pdata', cond_data_dir, cond_dir, date_str, ...
        max_angle, angle_step, measure_sec, stage, logger, s_volt, monitor, cfg);

    % ---- Mdata ----
    monitor.setPhase('Mdata');
    run_data_phase_('Mdata', cond_data_dir, cond_dir, date_str, ...
        max_angle, angle_step, measure_sec, stage, logger, s_volt, monitor, cfg);

    fprintf('\n=== 条件 %s 完了 ===\n', cond_label);
    fprintf('[保存先] %s\n\n', cond_dir);
    notify_sound_(2);

    % ---- この条件の後処理を随時実行（LCO なし・軽量）----
    %   計測中に重い LCO 解析を走らせないよう --lco は付けない。
    %   失敗しても実験は止めない（warning のみ）
    flutter_run_postprocess(cond_dir, 'exp', cfg, false);

    % ---- 任意の迎角へ移動して目視確認（計測なし）----
    manual_angle_check_(stage, max_angle, cfg);

    % ---- 次の条件を追加するか ----
    ans_next = strtrim(input('次の風速条件を追加しますか？ [y/n]: ', 's'));
    if ~any(strcmpi(ans_next, {'y', 'yes'}))
        break;
    end
    fprintf('\n');
end

% =====================================================================
%  5. 終了
% =====================================================================
fprintf('\n=== 全条件完了 ===\n');
fprintf('[ベースフォルダ] %s\n\n', base_exp_dir);
notify_sound_(4);

try; stage.moveToAngle(0); catch; end

% ---- 全条件の横断マップを生成（--base_dir --lco）----
%   実験終了後なので LCO 付きで実行。失敗しても続行（warning のみ）。
flutter_run_postprocess(base_exp_dir, 'base', cfg, true);

% 機器解放は guard_stage_/guard_logger_/guard_volt_/guard_monitor_ が
% スクリプト終了時のスコープ消滅で自動的に行う（onCleanup）。


% =====================================================================
%  フェーズ実行関数
% =====================================================================

function run_ofst_phase_(phase, data_dir, exp_dir, date_str, ...
        max_angle, angle_step, stage, logger, s_volt, monitor, cfg)
    % Pofst / Mofst フェーズ: KB打ち切り・単調スイープ
    % ブロワー停止確認は呼び出し元（Pofst の前）で1回だけ行うため、ここでは行わない

    fprintf('\n--- %s フェーズ開始 ---\n', phase);

    % 電圧オフセットの自動計測はトップレベル（無風確認直後）で行い、
    % cfg・met に反映済みのためここでは行わない。

    delete_phase_data_(data_dir, phase);
    summary_fname = make_filename(date_str, '', phase, 0, 0, 'volt_summary');
    summary_path  = fullfile(exp_dir, summary_fname);
    init_volt_summary_(summary_path);

    pts     = build_flutter_sequence_(phase, max_angle, angle_step);
    n_total = numel(pts);

    for idx = 1:n_total
        pt = pts(idx);
        fprintf('[%d/%d] 迎角 %+d° へ移動中...\n', idx, n_total, pt.target_angle);
        stage.moveToAngle(pt.target_angle);
        fprintf('[%d/%d] 迎角 %+d° に到達\n', idx, n_total, pt.target_angle);

        fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
        pause(cfg.angle_settle_sec);

        t_now     = datetime('now');
        time_str  = sprintf('%02d%02d%02d', hour(t_now), minute(t_now), floor(second(t_now)));
        fname_force    = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'full');
        fname_volt_raw = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'volt_raw');
        fname_short    = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'short');
        force_path     = fullfile(data_dir, fname_force);
        volt_raw_path  = fullfile(data_dir, fname_volt_raw);

        monitor.resetGraph();
        fprintf('[計測開始] 6軸センサ & デジボル 同時計測中（KB打ち切り）...\n');

        logger.start(force_path);
        pause(0.5);

        if ~logger.isAlive()
            warning('[フラッター実験] Leptrino プロセスの起動に失敗しました。');
            continue;
        end

        flush(s_volt, 'input');
        prev_timeout = s_volt.Timeout;
        s_volt.Timeout = 0.8;
        voltages = zeros(1, 500);
        nv = 0;
        n_consec_fail = 0;

        while ~logger.isDone()
            try
                writeline(s_volt, 'MD?');
                raw  = readline(s_volt);
                v_mv = str2double(strtrim(raw)) * 1000;
                if isnan(v_mv)
                    n_consec_fail = n_consec_fail + 1;
                else
                    n_consec_fail = 0;
                    nv = nv + 1;
                    if nv > numel(voltages)
                        voltages = [voltages, zeros(1, 200)]; %#ok<AGROW>
                    end
                    voltages(nv) = v_mv;
                    sz_kb = logger.getSizeKB();
                    fprintf('  6軸: %6.1f KB / %.0f KB  |  デジボル: %3d サンプル (%.2f mV)\r', ...
                        sz_kb, cfg.force_sensor_size_limit_kb, nv, v_mv);
                    % Pofst/Mofst はKBベースなのでsize_kb/limit_kbをそのまま使う
                    prog = struct('idx', idx, 'total', n_total, ...
                                  'size_kb', sz_kb, 'limit_kb', cfg.force_sensor_size_limit_kb);
                    monitor.update(pt.target_angle, v_mv, prog);
                end
            catch
                n_consec_fail = n_consec_fail + 1;
            end
            if n_consec_fail == 30
                warning('[R6441B] 応答の取得に連続で失敗しています（%d回）。接続を確認してください。', n_consec_fail);
            end
            pause(0.1);
        end
        s_volt.Timeout = prev_timeout;
        voltages = voltages(1:nv);
        fprintf('\n');

        logger.waitForFinish();
        logger.getResult(); %#ok<NASGU>

        save_volt_raw_(volt_raw_path, voltages);
        append_volt_summary_(summary_path, idx - 1, pt.target_angle, fname_short, voltages);
        fprintf('[保存] %s\n', fname_force);
        fprintf('[更新] %s に追記 (%d/%d 点完了)\n\n', summary_fname, idx, n_total);
    end

    % フェーズ終了後に迎角を 0° に戻す
    fprintf('[復帰] 迎角を 0° に戻しています...\n');
    stage.moveToAngle(0);
    fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
    pause(cfg.angle_settle_sec);

    fprintf('--- %s フェーズ完了 ---\n\n', phase);
end


function run_data_phase_(phase, data_dir, exp_dir, date_str, ...
        max_angle, angle_step, measure_sec, stage, logger, s_volt, monitor, cfg)
    % Pdata / Mdata フェーズ: 秒数打ち切り・単調スイープ

    fprintf('\n--- %s フェーズ開始（%.1f 秒/点）---\n', phase, measure_sec);

    delete_phase_data_(data_dir, phase);
    summary_fname = make_filename(date_str, '', phase, 0, 0, 'volt_summary');
    summary_path  = fullfile(exp_dir, summary_fname);
    init_volt_summary_(summary_path);

    pts     = build_flutter_sequence_(phase, max_angle, angle_step);
    n_total = numel(pts);

    for idx = 1:n_total
        pt = pts(idx);
        fprintf('[%d/%d] 迎角 %+d° へ移動中...\n', idx, n_total, pt.target_angle);
        stage.moveToAngle(pt.target_angle);
        fprintf('[%d/%d] 迎角 %+d° に到達\n', idx, n_total, pt.target_angle);

        fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
        pause(cfg.angle_settle_sec);

        t_now     = datetime('now');
        time_str  = sprintf('%02d%02d%02d', hour(t_now), minute(t_now), floor(second(t_now)));
        fname_force    = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'full');
        fname_volt_raw = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'volt_raw');
        fname_short    = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'short');
        force_path     = fullfile(data_dir, fname_force);
        volt_raw_path  = fullfile(data_dir, fname_volt_raw);

        monitor.resetGraph();
        fprintf('[計測開始] 6軸センサ & デジボル 同時計測中（%.1f 秒）...\n', measure_sec);

        logger.start(force_path, measure_sec);
        pause(0.5);

        if ~logger.isAlive()
            warning('[フラッター実験] Leptrino プロセスの起動に失敗しました。');
            continue;
        end

        flush(s_volt, 'input');
        prev_timeout = s_volt.Timeout;
        s_volt.Timeout = 0.8;
        voltages = zeros(1, 2000);
        nv = 0;
        n_consec_fail = 0;

        while ~logger.isDone()
            try
                writeline(s_volt, 'MD?');
                raw  = readline(s_volt);
                v_mv = str2double(strtrim(raw)) * 1000;
                if isnan(v_mv)
                    n_consec_fail = n_consec_fail + 1;
                else
                    n_consec_fail = 0;
                    nv = nv + 1;
                    if nv > numel(voltages)
                        voltages = [voltages, zeros(1, 500)]; %#ok<AGROW>
                    end
                    voltages(nv) = v_mv;
                    elapsed = logger.getElapsedSec();
                    fprintf('  経過: %5.1f / %.1f 秒  |  デジボル: %3d サンプル (%.2f mV)\r', ...
                        elapsed, measure_sec, nv, v_mv);
                    % 秒数ベースのプログレス（0〜1 を limit_kb スケールに変換して渡す）
                    prog = struct('idx', idx, 'total', n_total, ...
                                  'elapsed_sec', elapsed, 'limit_sec', measure_sec);
                    monitor.update(pt.target_angle, v_mv, prog);
                end
            catch
                n_consec_fail = n_consec_fail + 1;
            end
            if n_consec_fail == 30
                warning('[R6441B] 応答の取得に連続で失敗しています（%d回）。接続を確認してください。', n_consec_fail);
            end
            pause(0.1);
        end
        s_volt.Timeout = prev_timeout;
        voltages = voltages(1:nv);
        fprintf('\n');

        logger.waitForFinish();
        res = logger.getResult();
        if isfield(res, 'duration_sec')
            fprintf('[計測完了] %.2f 秒  |  6軸: %.1f KB  |  デジボル: %d サンプル\n', ...
                res.duration_sec, res.size_kb, nv);
        end

        save_volt_raw_(volt_raw_path, voltages);
        append_volt_summary_(summary_path, idx - 1, pt.target_angle, fname_short, voltages);
        fprintf('[保存] %s\n', fname_force);
        fprintf('[更新] %s に追記 (%d/%d 点完了)\n\n', summary_fname, idx, n_total);
    end

    % フェーズ終了後に迎角を 0° に戻す
    % （Mdata は -max_angle° で終わるため、次条件の「風速安定確認」時点を
    %   常に 0° にしておくために必要）
    fprintf('[復帰] 迎角を 0° に戻しています...\n');
    stage.moveToAngle(0);
    fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
    pause(cfg.angle_settle_sec);

    fprintf('--- %s フェーズ完了 ---\n\n', phase);
end


% =====================================================================
%  ローカル関数（以下は変更なし）
% =====================================================================

function cfg = load_flutter_config_(base_dir)
    config_path = fullfile(base_dir, 'config.json');
    if ~isfile(config_path)
        error(['config.json が見つかりません。\n' ...
               'config.json.example をコピーして config.json を作成してください。\n  パス: %s'], config_path);
    end
    cfg = jsondecode(fileread(config_path));

    required = {'python_exe', 'qt_adl1_port', 'r6441b_port', 'leptrino_port', 'output_dir'};
    missing = {};
    for i = 1:numel(required)
        if ~isfield(cfg, required{i}) || isempty(cfg.(required{i}))
            missing{end+1} = required{i}; %#ok<AGROW>
        end
    end
    if ~isempty(missing)
        error('config.json に必須キーが設定されていません: %s', strjoin(missing, ', '));
    end

    if ~isfield(cfg, 'force_sensor_size_limit_kb') || isempty(cfg.force_sensor_size_limit_kb)
        cfg.force_sensor_size_limit_kb = 1000;
    end
    if ~isfield(cfg, 'flutter_measure_sec') || isempty(cfg.flutter_measure_sec)
        cfg.flutter_measure_sec = 30.0;
    end
    if ~isfield(cfg, 'angle_settle_sec') || isempty(cfg.angle_settle_sec)
        cfg.angle_settle_sec = 2.0;
    end
    if ~isfield(cfg, 'origin_pulse') || isempty(cfg.origin_pulse)
        cfg.origin_pulse = 11025;
    end
    if ~isfield(cfg, 'r6441b_timeout_sec') || isempty(cfg.r6441b_timeout_sec)
        cfg.r6441b_timeout_sec = 5;
    end
    if ~isfield(cfg, 'python_exe_64') || isempty(cfg.python_exe_64)
        cfg.python_exe_64 = '';
    end
    if ~isfield(cfg, 'volt_offset_mV') || isempty(cfg.volt_offset_mV)
        cfg.volt_offset_mV = -5.0;
    end
    if ~isfield(cfg, 'calib_a') || isempty(cfg.calib_a)
        cfg.calib_a = 0.007904809948345278;
    end
    if ~isfield(cfg, 'calib_b') || isempty(cfg.calib_b)
        cfg.calib_b = -0.340200009144243;
    end
end

function name = input_base_name_(output_dir)
    fprintf('=== 実験ベース名の入力 ===\n');
    fprintf('  例: 260620_flexible\n');
    fprintf('  フォルダは自動で以下のように作成されます:\n');
    fprintf('    output_dir/260620_flexible/\n');
    fprintf('      ├── 260620_flexible_ofst/\n');
    fprintf('      ├── 260620_flexible_c01/\n');
    fprintf('      └── 260620_flexible_c02/\n\n');
    forbidden = '/\:*?"<>|';
    while true
        name = strtrim(input('実験ベース名: ', 's'));
        if isempty(name) || any(ismember(name, forbidden))
            fprintf('  ※ 有効な名前を入力してください（特殊文字 %s 不可）\n', forbidden);
            continue;
        end
        % ベースフォルダ自体（output_dir/name/）の存在で判定する。
        % 以前は _ofst/data の有無だけを見ていたため、ofst 無し・c01 等の
        % 条件フォルダだけが既存の場合（例: 途中で ofst を消した再実行）を
        % 見逃し、旧条件フォルダと新規計測が混在する恐れがあった。
        base_check = fullfile(output_dir, name);
        if isfolder(base_check)
            fprintf('  ※ [%s] は既に存在します。上書きしますか？ [y/n]: ', name);
            ans_ow = strtrim(input('', 's'));
            if ~any(strcmpi(ans_ow, {'y', 'yes'}))
                continue;
            end
        end
        break;
    end
    fprintf('→ ベース名 [%s] を使用\n', name);
end

function [max_angle, angle_step, measure_sec] = configure_flutter_sweep_(cfg)
    fprintf('\n=== フラッター計測の設定 ===\n');

    ans_def = strtrim(input(sprintf('デフォルト（±%d°・%d°刻み・%.1f秒/点）で計測しますか？ [y/n]: ', ...
        30, 1, cfg.flutter_measure_sec), 's'));

    if isempty(ans_def) || any(strcmpi(ans_def, {'y', 'yes'}))
        max_angle   = 30;
        angle_step  = 1;
        measure_sec = cfg.flutter_measure_sec;
        fprintf('→ ±%d°・%d°刻み・%.1f秒/点で計測します。\n\n', max_angle, angle_step, measure_sec);
        return
    end

    max_angle   = ask_int_('最大迎角 [度, 1-30]: ', 1, 30);
    angle_step  = ask_int_('迎角の刻み幅 [度, 1-max]: ', 1, max_angle);

    val = str2double(input(sprintf('計測秒数/点 [秒, デフォルト=%.1f]: ', cfg.flutter_measure_sec), 's'));
    if isnan(val) || val <= 0
        measure_sec = cfg.flutter_measure_sec;
    else
        measure_sec = val;
    end

    n_each = numel(angle_step:angle_step:max_angle);
    fprintf('→ ±%d°・%d°刻み・%.1f秒/点（%d 点/フェーズ）で計測します。\n\n', ...
        max_angle, angle_step, measure_sec, 1 + n_each);
end

function pts = build_flutter_sequence_(phase, max_angle, angle_step)
    if startsWith(phase, 'P')
        angle_sign = +1;
    else
        angle_sign = -1;
    end

    angles = [0, angle_step:angle_step:max_angle];
    n_pts  = numel(angles);
    pts(n_pts) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    for k = 1:n_pts
        a = angles(k);
        if a == 0
            pts(k) = struct('target_angle', 0,              'ref_angle', 0, 'suffix', 0);
        else
            pts(k) = struct('target_angle', angle_sign * a, 'ref_angle', a, 'suffix', 1);
        end
    end
end

function [rep_mv, rep_U] = measure_representative_windspeed_(s_volt, cfg, met)
    MEAS_SEC = 5;
    fprintf('[代表風速計測] %.0f 秒間計測中...\n', MEAS_SEC);

    [samples, n] = windy_sample_voltage_mv(s_volt, MEAS_SEC);

    if n == 0
        warning('[代表風速計測] サンプルを取得できませんでした。0 mV として扱います。');
        rep_mv = 0;
        rep_U  = 0;
        return;
    end

    rep_mv = mean(samples);

    offset_mV  = cfg.volt_offset_mV;
    a          = cfg.calib_a;
    b          = cfg.calib_b;
    rho        = met.rho_kg_m3;
    water_dens = met.water_density;

    h = (rep_mv - offset_mV) * a + b;
    if h > 0 && rho > 0
        rep_U = sqrt(2.0 * water_dens * h * 9.80665 / rho);
    else
        rep_U = 0;
    end

    fprintf('  → 代表風速: U ≈ %.2f m/s  （平均 %.1f mV, %d サンプル）\n', rep_U, rep_mv, n);
    fprintf('     使用パラメータ: ρ = %.6f kg/m³, ρ_w = %.6f g/cm³\n\n', rho, water_dens);
end

function met = input_met_conditions_()
    fprintf('=== 気象条件の入力 ===\n\n');
    while true
        T = str2double(input('気温 [℃]: ', 's'));
        if ~isnan(T) && T > -20 && T < 50, break; end
        fprintf('  ※ 有効な気温を入力してください（-20 ～ 50 ℃）\n');
    end
    while true
        P = str2double(input('気圧 [mmHg]: ', 's'));
        if ~isnan(P) && P > 700 && P < 820, break; end
        fprintf('  ※ 有効な気圧を入力してください（700 ～ 820 mmHg）\n');
    end

    e     = 6.1078 * 10^(7.5 * T / (237.3 + T));
    P_cal = 1013.25/760 * (1 - 0.000182 * T) * P;
    rho   = 1.293 * (273.15 / (273.15 + T)) ...
          * (P_cal / 1013.25) * (1 - 0.378 * e / P_cal);
    rho_w = (999.83952 + 16.945176*T - 7.9870401e-3*T^2 ...
             - 46.170461e-6*T^3 + 105.56302e-9*T^4 ...
             - 280.54253e-12*T^5) / (1 + 16.879850e-3*T) / 1000;

    fprintf('\n  → 空気密度 ρ = %.6f kg/m³\n', rho);
    fprintf('  → 水密度  ρ_w = %.6f g/cm³\n\n', rho_w);

    met = struct('temperature_C', T, 'pressure_mmHg', P, ...
                 'rho_kg_m3', rho, 'water_density', rho_w);
end

function save_experiment_log_(filepath, date_str, met)
    log = struct('date', date_str, ...
        'temperature_C',  met.temperature_C,  ...
        'pressure_mmHg',  met.pressure_mmHg,  ...
        'rho_kg_m3',      met.rho_kg_m3,      ...
        'water_density',  met.water_density,   ...
        'volt_offset_mV', met.volt_offset_mV,  ...
        'calib_a',        met.calib_a,         ...
        'calib_b',        met.calib_b,         ...
        'origin_pulse',   met.origin_pulse,    ...
        'git_commit',     met.git_commit);
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0, warning('experiment_log.json を保存できません: %s', filepath); return; end
    fprintf(fid, '%s\n', jsonencode(log));
    fclose(fid);
end

function save_flutter_log_(filepath, date_str, met)
    log = struct('date', date_str, ...
        'temperature_C',     met.temperature_C,    ...
        'pressure_mmHg',     met.pressure_mmHg,    ...
        'rho_kg_m3',         met.rho_kg_m3,        ...
        'water_density',     met.water_density,     ...
        'volt_offset_mV',    met.volt_offset_mV,   ...
        'calib_a',           met.calib_a,           ...
        'calib_b',           met.calib_b,           ...
        'origin_pulse',      met.origin_pulse,      ...
        'git_commit',        met.git_commit,        ...
        'rep_windspeed_mV',  met.rep_windspeed_mV,  ...
        'rep_windspeed_U',   met.rep_windspeed_U,   ...
        'ofst_dir',          met.ofst_dir);
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0, warning('flutter_log.json を保存できません: %s', filepath); return; end
    fprintf(fid, '%s\n', jsonencode(log));
    fclose(fid);
end

function offset_mV = measure_volt_offset_(s_volt)
    MEAS_SEC = 5;
    fprintf('[オフセット計測] 無風時の差圧電圧を %.0f 秒間計測します...\n', MEAS_SEC);

    [samples, n] = windy_sample_voltage_mv(s_volt, MEAS_SEC);

    if n == 0
        warning('[オフセット計測] サンプルを取得できませんでした。');
        offset_mV = NaN;
        return;
    end
    offset_mV = mean(samples);
    fprintf('  → 電圧オフセット = %+.4f mV  (%d サンプル)\n\n', offset_mV, n);
end

function s = connect_r6441b_(com_port, timeout_sec)
    s = serialport(com_port, 9600, ...
        'DataBits', 8, 'Parity', 'none', 'StopBits', 1, 'FlowControl', 'none');
    configureTerminator(s, 'CR/LF');
    s.Timeout = timeout_sec;
    fprintf('[接続] R6441B に接続しました: %s\n', com_port);
end

function init_volt_summary_(filepath)
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0, error('デジボルサマリー CSV を作成できません: %s', filepath); end
    fwrite(fid, uint8([0xEF 0xBB 0xBF]));
    fprintf(fid, 'No.,迎角,name,差圧電圧[mV],風速[m/s]\n');
    fclose(fid);
end

function append_volt_summary_(filepath, no, angle, name_str, voltages)
    if isempty(voltages), avg_str = ''; else, avg_str = sprintf('%.4f', mean(voltages)); end
    fid = fopen(filepath, 'a', 'n', 'UTF-8');
    if fid < 0, warning('デジボルサマリー CSV に追記できません: %s', filepath); return; end
    fprintf(fid, '%d,%d,%s,%s,\n', no, angle, name_str, avg_str);
    fclose(fid);
end

function save_volt_raw_(filepath, voltages)
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0, warning('デジボル生データ CSV を作成できません: %s', filepath); return; end
    fprintf(fid, 'sample_no,voltage_mV\n');
    for i = 1:numel(voltages)
        fprintf(fid, '%d,%.4f\n', i - 1, voltages(i));
    end
    fclose(fid);
end

function delete_phase_data_(data_dir, phase)
    if ~isfolder(data_dir), return; end
    files = dir(fullfile(data_dir, '*.csv'));
    token = ['_' phase '_'];
    n = 0;
    for i = 1:numel(files)
        if contains(files(i).name, token)
            delete(fullfile(files(i).folder, files(i).name));
            n = n + 1;
        end
    end
    if n > 0
        fprintf('[削除] %s フェーズの古いデータ %d ファイルを削除しました。\n\n', phase, n);
    end
end

function safe_stop_(logger)
    % onCleanup から呼ばれる個別解放ヘルパー。
    % 機器ごとに独立した onCleanup ガードにすることで、接続シーケンスの
    % 途中（例えば Leptrino や R6441B の接続失敗）で例外が出ても、その
    % 時点までに確保済みの機器だけを確実に解放できる。
    fprintf('[終了] Leptrino センサの接続を閉じます...\n');
    try; logger.stop(); catch; end
end

function safe_delete_(obj)
    fprintf('[終了] 機器の接続を閉じます...\n');
    try; delete(obj); catch; end
end

function safe_close_(monitor)
    try; monitor.close(); catch; end
end

function check_python_bits_(python_exe, want_bits, label)
    cmd = sprintf('"%s" -c "import struct; print(struct.calcsize(''P'')*8)"', python_exe);
    [st, out] = system(cmd);
    if st ~= 0
        error('[設定確認] %s を実行できません: %s', label, python_exe);
    end
    bits = str2double(strtrim(out));
    if ~isnan(bits) && bits ~= want_bits
        error(['[設定確認] %s は %dbit Python です（%dbit が必要）。\n' ...
               '  config.json のパス設定を確認してください。'], label, bits, want_bits);
    end
end

function h = git_commit_hash_()
    h = 'unknown';
    repo_dir = fileparts(mfilename('fullpath'));
    try
        [st, out] = system(sprintf('git -C "%s" rev-parse HEAD', repo_dir));
        if st == 0 && ~isempty(strtrim(out))
            h = strtrim(out);
            [st2, dirty] = system(sprintf('git -C "%s" status --porcelain', repo_dir));
            if st2 == 0 && ~isempty(strtrim(dirty))
                h = [h '-dirty'];
            end
        end
    catch
    end
end

function notify_sound_(n_tones)
    if nargin < 1, n_tones = 2; end
    try
        Fs = 8000; t = 0:1/Fs:0.16;
        env = linspace(1, 0, numel(t));
        freqs = [784, 988, 1175, 1568];
        y = [];
        for k = 1:min(n_tones, numel(freqs))
            y = [y, sin(2*pi*freqs(k)*t) .* env]; %#ok<AGROW>
        end
        sound(y * 0.3, Fs);
    catch
        try; beep; catch; end
    end
end

function manual_angle_check_(stage, max_angle, cfg)
    % 目視確認用に任意の迎角へステージを移動する（計測・保存は一切しない）。
    % n になるまで繰り返し、複数姿勢を続けて確認できる。
    ans_do = strtrim(input('迎角を指定して実行しますか？ [y/n]: ', 's'));
    if ~any(strcmpi(ans_do, {'y', 'yes'}))
        return;
    end

    fprintf('\n--- 任意迎角への移動（目視確認・計測なし）---\n');
    while true
        angle = ask_int_(sprintf('移動先の迎角 [度, -%d〜%d]: ', max_angle, max_angle), ...
                         -max_angle, max_angle);
        fprintf('[移動] 迎角 %+d° へ移動中...\n', angle);
        stage.moveToAngle(angle);
        fprintf('[移動] 迎角 %+d° に到達\n', angle);

        fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
        pause(cfg.angle_settle_sec);

        ans_more = strtrim(input('別の迎角も確認しますか？ [y/n]: ', 's'));
        if ~any(strcmpi(ans_more, {'y', 'yes'}))
            break;
        end
    end

    % 確認終了後は必ず迎角を 0° に戻す（後続の代表風速計測・計測を常に0°始まりにするため）
    fprintf('[復帰] 迎角を 0° に戻しています...\n');
    stage.moveToAngle(0);
    fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
    pause(cfg.angle_settle_sec);

    fprintf('--- 任意迎角への移動 完了 ---\n\n');
end

function v = ask_int_(prompt, lo, hi)
    while true
        val = str2double(input(prompt, 's'));
        if ~isnan(val) && val == floor(val) && val >= lo && val <= hi
            v = val; return
        end
        fprintf('  ※ %d〜%d の整数を入力してください。\n', lo, hi);
    end
end
