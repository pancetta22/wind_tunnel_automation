%% run_experiment.m
% 風洞実験 自動計測スクリプト（Windy）
%
% 事前準備:
%   1. config.json.example をコピーして config.json を作成
%   2. python_exe / qt_adl1_port / r6441b_port / leptrino_port / output_dir を設定
%
% 実行方法:
%   MATLAB コマンドウィンドウで:  run_experiment
%
% 実験フェーズ（SPEC.md Section 3.2）:
%   Pofst: 正迎角 (0°→+max)・無風   Mofst: 負迎角 (0°→-max)・無風
%   Pdata: 正迎角 (0°→+max)・有風   Mdata: 負迎角 (0°→-max)・有風

% =====================================================================
%  0. 設定読み込み
% =====================================================================
cfg = load_config_(fileparts(mfilename('fullpath')));

fprintf('\n========================================\n');
fprintf('  Windy 風洞実験自動計測システム\n');
fprintf('========================================\n\n');

% =====================================================================
%  0.5. 実験フォルダ名の入力
% =====================================================================
exp_name = input_experiment_name_();
exp_dir  = fullfile(cfg.output_dir, exp_name);
data_dir = fullfile(exp_dir, 'data');
[~, ~]   = mkdir(data_dir);
fprintf('[準備] 実験フォルダ : %s\n', exp_dir);
fprintf('[準備] データフォルダ: %s\n\n', data_dir);

% =====================================================================
%  1. 機器接続
% =====================================================================
fprintf('[接続] 迎角ステージ (%s) に接続中...\n', cfg.qt_adl1_port);
stage = QT_ADL1(cfg.qt_adl1_port);
stage.homeReturn();

fprintf('[接続] Leptrino センサ (ポート %d) を確認中...\n', cfg.leptrino_port);
script_path = fullfile(fileparts(mfilename('fullpath')), 'leptrino', 'leptrino_server.py');
logger = LeptrinoLogger(cfg.python_exe, script_path, ...
                        cfg.leptrino_port, cfg.force_sensor_size_limit_kb);

fprintf('[接続] R6441B デジボル (%s) に接続中...\n', cfg.r6441b_port);
s_volt = connect_r6441b_(cfg.r6441b_port, cfg.r6441b_timeout_sec);

monitor = WindyMonitor(cfg.force_sensor_size_limit_kb);
monitor.setDataSource(@() logger.getRecentRows(600));   % 6軸グラフ: 直近 0.5 秒をローリング表示

% =====================================================================
%  2. 実験設定（気象条件・迎角範囲・開始フェーズ）
% =====================================================================
t_start  = datetime('now');
date_str = sprintf('%04d%02d%02d', year(t_start), month(t_start), day(t_start));

% 実験ログのパス
log_path = fullfile(exp_dir, sprintf('%s_experiment_log.json', date_str));

% =====================================================================
%  3. 全フェーズ 連続計測
% =====================================================================
all_phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
n_phases   = numel(all_phases);

% ---- 実験リスタート制御ループ ----
%  停止ボタンのメニュー(ask_stop_action_)で、このループ先頭に戻って再入場できる:
%    完全に最初から    → need_met=true（気温・気圧の入力から）
%    気温気圧の後から  → need_met=false（迎角範囲・開始フェーズの選択から）
%    計測を途中から    → need_met=false かつ preset_start を選択フェーズに設定
need_met     = true;   % 気温・気圧を入力するか
preset_start = 0;      % >0 なら開始フェーズを固定（途中からやり直し用。迎角範囲は引き継ぐ）
while true

% ---- 気象条件（必要時のみ入力）----
if need_met
    met = input_met_conditions_();
    met.calib_a        = cfg.calib_a;
    met.calib_b        = cfg.calib_b;
    met.volt_offset_mV = cfg.volt_offset_mV;
    met.git_commit     = git_commit_hash_();   % コード版数（再現性記録用）
    save_experiment_log_(log_path, date_str, met);
    fprintf('[記録] 気象条件を保存しました\n\n');
end

% ---- 迎角範囲・開始フェーズ ----
if preset_start > 0
    start_idx = preset_start;   % 途中からやり直し: 迎角範囲は前回のまま、フェーズ固定
    fprintf('[再開] %s フェーズからやり直します（迎角範囲は前回設定を継続）。\n\n', all_phases{preset_start});
else
    max_angle = input_max_angle_();
    start_idx = select_start_phase_();
end

exp_control = '';   % 停止メニューの結果（''=正常完了 / restart_full / restart_after_met / restart_goto / quit）

ph_idx = start_idx;
while ph_idx <= n_phases

    phase = all_phases{ph_idx};
    monitor.setPhase(phase);

    fprintf('\n========================================\n');
    fprintf('  フェーズ %d/%d: %s\n', ph_idx, n_phases, phase);
    fprintf('========================================\n\n');

    % ---- ファイル準備 ----
    summary_fname = make_filename(date_str, '', phase, 0, 0, 'volt_summary');
    summary_path  = fullfile(exp_dir, summary_fname);
    init_volt_summary_(summary_path);
    fprintf('[準備] デジボルサマリー: %s\n\n', summary_fname);

    % ---- 差圧センサ電圧オフセット自動計測（Pofst の前に1回だけ）----
    if strcmp(phase, 'Pofst')
        measured_offset = measure_volt_offset_(s_volt);
        if ~isnan(measured_offset)
            met.volt_offset_mV = measured_offset;
            save_experiment_log_(log_path, date_str, met);
            fprintf('[更新] experiment_log.json を更新 (volt_offset_mV = %.4f mV)\n\n', measured_offset);
        else
            fprintf('[警告] オフセット計測失敗 — config.json の設定値 (%.1f mV) を使用します\n\n', cfg.volt_offset_mV);
        end
    end

    % ---- ブロワー状態の確認 ----
    confirm_blower_(phase);

    % ---- 計測ループ ----
    pts     = build_measurement_sequence_(phase, max_angle);
    n_total = numel(pts);
    fprintf('\n=== %s フェーズ開始 (%d 点) ===\n\n', phase, n_total);

    phase_action = 'next';   % 'next' | 'restart' | 'goto' | 'quit'
    goto_ph_idx  = ph_idx;

    idx = 1;
    while idx <= n_total

        pt = pts(idx);

        % ------ 一時停止チェック ------
        if monitor.isPaused()
            fprintf('[一時停止] 「再開」または「停止」ボタンを押してください...\n');
            while monitor.isPaused()
                pause(0.2);
                drawnow;
            end
            if ~monitor.isStopRequested()
                fprintf('[再開] 計測を再開します。\n\n');
            end
        end

        % ------ 停止チェック（一時停止を経由しなくても確実に検出）------
        %  停止ボタンは paused_ を false に戻すため、isPaused ゲートだけでは
        %  計測中の停止を取りこぼす。pause_action_ を直接見て判定し、メニューへ。
        if monitor.isStopRequested()
            exp_control = handle_stop_(stage, logger, s_volt, monitor);
            break;   % 計測点ループを抜ける → 下でフェーズループも抜け、外側で分岐
        end

        try
            % ------ a. 迎角ステージ移動 ------
            fprintf('[%d/%d] 迎角 %+d° へ移動中...\n', idx, n_total, pt.target_angle);
            stage.moveToAngle(pt.target_angle);
            fprintf('[%d/%d] 迎角 %+d° に到達\n', idx, n_total, pt.target_angle);

            % ------ b. 振動収束待ち ------
            fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
            pause(cfg.angle_settle_sec);

            % ------ c. ファイル名生成 ------
            t_now     = datetime('now');
            time_str  = sprintf('%02d%02d%02d', hour(t_now), minute(t_now), floor(second(t_now)));
            fname_force    = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'full');
            fname_volt_raw = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'volt_raw');
            fname_short    = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'short');
            force_path     = fullfile(data_dir, fname_force);
            volt_raw_path  = fullfile(data_dir, fname_volt_raw);

            % ------ d. 同時計測開始 ------
            monitor.resetGraph();
            fprintf('[計測開始] 6軸センサ & デジボル 同時計測中...\n');
            logger.start(force_path);
            pause(0.5);

            if ~logger.isAlive()
                error('[LeptrinoLogger] Python プロセスの起動に失敗しました。\n  python_exe や leptrino_port を確認してください。');
            end

            % ------ e. デジボル計測ループ（6軸センサが完了するまで続ける）------
            %
            % ポーリング設計:
            %   - ループ開始前にバッファをフラッシュ（前点の残留データを除去）
            %   - ループ内タイムアウトを短くして、1回の失敗が長時間ブロックしないようにする
            %   - 1サンプルごとに pause してデバイスの A/D サイクルに合わせる
            flush(s_volt, 'input');            % 受信バッファをクリア
            s_volt.Timeout = 0.8;              % ループ内タイムアウト（接続時の長い値から変更）
            voltages = zeros(1, 500);
            nv = 0;
            while ~logger.isDone()
                if monitor.isStopRequested()
                    break;   % 計測を中断 → ループ後の停止チェックで終了処理へ
                end
                try
                    writeline(s_volt, 'MD?');
                    raw  = readline(s_volt);
                    v_mv = str2double(strtrim(raw)) * 1000;
                    if ~isnan(v_mv)
                        nv = nv + 1;
                        if nv > numel(voltages)
                            voltages = [voltages, zeros(1, 200)]; %#ok<AGROW>
                        end
                        voltages(nv) = v_mv;
                        sz_kb = logger.getSizeKB();
                        fprintf('  6軸: %6.1f KB / %.0f KB  |  デジボル: %3d サンプル (%.2f mV)\r', ...
                            sz_kb, cfg.force_sensor_size_limit_kb, nv, v_mv);
                        prog = struct('idx', idx, 'total', n_total, ...
                                      'size_kb', sz_kb, 'limit_kb', cfg.force_sensor_size_limit_kb);
                        monitor.update(pt.target_angle, v_mv, prog);
                    end
                    pause(0.1);   % A/D サイクル待ち（~10 サンプル/秒にレート制限）
                catch ME_volt
                    warning('windy:r6441b', '%s', ME_volt.message);
                end
            end
            s_volt.Timeout = cfg.r6441b_timeout_sec;   % タイムアウトを元の値に戻す
            voltages = voltages(1:nv);
            fprintf('\n');

            % ------ 計測中に停止が押された場合：この点は保存せずメニューへ ------
            if monitor.isStopRequested()
                exp_control = handle_stop_(stage, logger, s_volt, monitor);
                break;   % 計測点ループを抜ける
            end

            % ------ f. 6軸センサの完全終了を待つ ------
            logger.waitForFinish();
            logger.getResult(); %#ok<NASGU>

            avg_mv = NaN;
            if ~isempty(voltages), avg_mv = mean(voltages); end

            fprintf('[計測完了] 6軸センサ: %.1f KB  |  デジボル: %d サンプル', ...
                logger.getSizeKB(), numel(voltages));
            if ~isnan(avg_mv), fprintf(' (平均: %.2f mV)', avg_mv); end
            fprintf('\n');

            % ------ g. デジボル生データCSV 保存 ------
            save_volt_raw_(volt_raw_path, voltages);
            fprintf('[保存] %s\n', fname_volt_raw);

            % ------ h. 6軸センサCSV の保存確認 ------
            fprintf('[保存] %s\n', fname_force);
            if ~isfile(force_path)
                warning('[保存] 6軸センサCSVが見当たりません: %s', fname_force);
            end

            % ------ i. デジボルサマリーCSV に1行追記 ------
            append_volt_summary_(summary_path, idx - 1, pt.target_angle, fname_short, voltages);
            fprintf('[更新] %s に追記 (%d/%d 点完了)\n\n', summary_fname, idx, n_total);

            idx = idx + 1;   % 正常完了 → 次の計測点へ

        catch ME_meas
            % ------ エラー発生 → 対処を確認 ------
            action_str = ask_error_action_(ME_meas, phase, idx, n_total);
            switch action_str
                case 'retry'
                    fprintf('[再試行] 計測点 %d/%d を再試行します。\n\n', idx, n_total);
                    % idx は変えない

                case 'skip'
                    fprintf('[スキップ] 計測点 %d/%d をスキップします。\n\n', idx, n_total);
                    idx = idx + 1;

                case 'restart_phase'
                    fprintf('[再開] %s フェーズを最初からやり直します。\n\n', phase);
                    phase_action = 'restart';
                    break;  % while idx を抜ける

                case 'goto_phase'
                    goto_ph_idx  = ask_goto_phase_();
                    phase_action = 'goto';
                    break;

                case 'quit'
                    phase_action = 'quit';
                    break;
            end

        end % try-catch

    end % while idx

    % 停止メニューが選ばれた場合はフェーズループも抜けて、外側で分岐する
    if ~isempty(exp_control)
        break;
    end

    % ---- フェーズ終了後の処理 ----
    switch phase_action
        case 'next'
            fprintf('=== %s フェーズ完了 ===\n\n', phase);
            notify_sound_(2);   % フェーズ完了を音で実験者に通知
            try; stage.moveToAngle(0); catch; end
            ph_idx = ph_idx + 1;

        case 'restart'
            % 部分データを削除してフェーズを最初からやり直す
            delete_phase_data_(data_dir, date_str, phase);
            % ph_idx は変えない

        case 'goto'
            try; stage.moveToAngle(0); catch; end
            ph_idx = goto_ph_idx;

        case 'quit'
            fprintf('[中止] 実験を終了します。\n\n');
            try; stage.moveToAngle(0); catch; end
            cleanup_devices_(stage, logger, s_volt, monitor);
            return;
    end

end % while ph_idx

% ===== 停止メニューの結果に応じて再入場 or 終了 =====
switch exp_control
    case ''                  % 停止なし = 全フェーズ正常完了
        break;               % 外側リスタートループを抜けて後処理へ

    case 'restart_full'      % 完全に最初から（気温・気圧から）
        need_met = true;  preset_start = 0;
        fprintf('[再起動] 完全に最初からやり直します。\n\n');
        continue;

    case 'restart_after_met' % 気温・気圧の後から（迎角範囲・開始フェーズの選択から）
        need_met = false; preset_start = 0;
        fprintf('[再起動] 迎角範囲・開始フェーズの選択からやり直します。\n\n');
        continue;

    case 'restart_goto'      % 計測を途中から（フェーズを選択）
        need_met = false; preset_start = ask_goto_phase_();
        continue;

    case 'quit'              % 実験を終了
        fprintf('[中止] 実験を終了します。\n\n');
        try; stage.moveToAngle(0); catch; end
        cleanup_devices_(stage, logger, s_volt, monitor);
        return;
end

end % while true（実験リスタート制御ループ）

% =====================================================================
%  4. 実験完了・後処理
% =====================================================================
fprintf('=== 全フェーズ完了 ===\n');
fprintf('[保存先] %s\n\n', exp_dir);
notify_sound_(4);   % 全実験完了を音で通知（フェーズ完了より長め）

cleanup_devices_(stage, logger, s_volt, monitor);
run_postprocess_if_ready_(exp_dir, date_str, cfg);

% =====================================================================
%  ローカル関数
% =====================================================================

function cfg = load_config_(base_dir)
    config_path = fullfile(base_dir, 'config.json');
    if ~isfile(config_path)
        error(['config.json が見つかりません。\n' ...
               'config.json.example をコピーして config.json を作成し、\n' ...
               '各自の環境に合わせて設定してください。\n  パス: %s'], config_path);
    end
    cfg = jsondecode(fileread(config_path));

    if ~isfield(cfg, 'force_sensor_size_limit_kb') || isempty(cfg.force_sensor_size_limit_kb)
        cfg.force_sensor_size_limit_kb = 1000;
    end
    if ~isfield(cfg, 'angle_settle_sec') || isempty(cfg.angle_settle_sec)
        cfg.angle_settle_sec = 2.0;
    end
    if ~isfield(cfg, 'csv_decimal_places')
        cfg.csv_decimal_places = [];
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

function s = connect_r6441b_(com_port, timeout_sec)
    s = serialport(com_port, 9600, ...
        'DataBits',    8,      ...
        'Parity',      'none', ...
        'StopBits',    1,      ...
        'FlowControl', 'none');
    configureTerminator(s, 'CR/LF');
    s.Timeout = timeout_sec;
    fprintf('[接続] R6441B に接続しました: %s\n', com_port);
end

function confirm_blower_(phase)
    if contains(phase, 'ofst')
        msg = 'ブロワーが停止していることを確認してください';
    else
        msg = 'ブロワーを起動し、風速が安定したことを確認してください';
    end
    input(sprintf('>> %s。\n   確認できたら Enter を押してください: ', msg));
    fprintf('\n');
end

function pts = build_measurement_sequence_(phase, max_angle)
    % 迎角シーケンスを生成する
    %
    % 返値: struct 配列。各要素のフィールド:
    %   target_angle : 実際に移動する角度 [度]
    %   ref_angle    : ファイル名に使う参照迎角（0〜max_angle, 常に正）
    %   suffix       : 0=0°基準計測, 1=目標迎角計測
    %
    % Pフェーズ: 0° → 1° → 0° → 2° → 0° → ... → max_angle° → 0°
    % Mフェーズ: 0° → -1° → 0° → -2° → 0° → ... → -max_angle° → 0°

    if startsWith(phase, 'P')
        angle_sign = +1;
    else
        angle_sign = -1;
    end

    n_pts = 1 + max_angle * 2;
    pts(n_pts) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    pts(1) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    k = 2;
    for n = 1:max_angle
        pts(k)   = struct('target_angle', angle_sign * n, 'ref_angle', n, 'suffix', 1);
        pts(k+1) = struct('target_angle', 0,              'ref_angle', n, 'suffix', 0);
        k = k + 2;
    end
end

function init_volt_summary_(filepath)
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0
        error('デジボルサマリー CSV を作成できません: %s', filepath);
    end
    fwrite(fid, uint8([0xEF 0xBB 0xBF]));
    fprintf(fid, 'No.,迎角,name,差圧電圧[mV],風速[m/s]\n');
    fclose(fid);
end

function append_volt_summary_(filepath, no, angle, name_str, voltages)
    if isempty(voltages)
        avg_str = '';
    else
        avg_str = sprintf('%.4f', mean(voltages));
    end

    fid = fopen(filepath, 'a', 'n', 'UTF-8');
    if fid < 0
        warning('デジボルサマリー CSV に追記できません: %s', filepath);
        return;
    end
    fprintf(fid, '%d,%d,%s,%s,\n', no, angle, name_str, avg_str);
    fclose(fid);
end

function save_volt_raw_(filepath, voltages)
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0
        warning('デジボル生データ CSV を作成できません: %s', filepath);
        return;
    end
    fprintf(fid, 'sample_no,voltage_mV\n');
    for i = 1:numel(voltages)
        fprintf(fid, '%d,%.4f\n', i - 1, voltages(i));
    end
    fclose(fid);
end

function cleanup_devices_(stage, logger, s_volt, monitor)
    fprintf('[終了] 機器の接続を閉じます...\n');
    try; logger.stop();   catch; end
    try; delete(s_volt);  catch; end
    try; delete(stage);   catch; end
    try; monitor.close(); catch; end
    clear logger;
end

function met = input_met_conditions_()
    fprintf('=== 気象条件の入力 ===\n');
    fprintf('  気温・気圧から空気密度 ρ を自動計算します。\n\n');

    while true
        T = input('気温 [℃]: ');
        if isnumeric(T) && isscalar(T) && T > -20 && T < 50, break; end
        fprintf('  ※ 有効な気温を入力してください（-20 ～ 50 ℃）\n');
    end

    while true
        P = input('気圧 [mmHg]: ');
        if isnumeric(P) && isscalar(P) && P > 700 && P < 820, break; end
        fprintf('  ※ 有効な気圧を入力してください（700 ～ 820 mmHg）\n');
    end

    % 空気密度（yymmdd.xlsx と同一の計算式）
    e     = 6.1078 * 10^(7.5 * T / (237.3 + T));
    P_cal = 1013.25/760 * (1 - 0.000182 * T) * P;
    rho   = 1.293 * (273.15 / (273.15 + T)) ...
          * (P_cal / 1013.25) * (1 - 0.378 * e / P_cal);

    % 水密度（Kell の式）
    rho_w = (999.83952 + 16.945176*T     - 7.9870401e-3*T^2 ...
             - 46.170461e-6*T^3  + 105.56302e-9*T^4 ...
             - 280.54253e-12*T^5) ...
            / (1 + 16.879850e-3*T) / 1000;

    fprintf('\n  → 空気密度 ρ = %.6f kg/m³\n', rho);
    fprintf('  → 水密度  ρ_w = %.6f g/cm³\n\n', rho_w);

    met = struct( ...
        'temperature_C', T,     ...
        'pressure_mmHg', P,     ...
        'rho_kg_m3',     rho,   ...
        'water_density', rho_w  ...
    );
end

function save_experiment_log_(filepath, date_str, met)
    log = struct( ...
        'date',           date_str,            ...
        'temperature_C',  met.temperature_C,   ...
        'pressure_mmHg',  met.pressure_mmHg,   ...
        'rho_kg_m3',      met.rho_kg_m3,       ...
        'water_density',  met.water_density,   ...
        'volt_offset_mV', met.volt_offset_mV,  ...
        'calib_a',        met.calib_a,         ...
        'calib_b',        met.calib_b,         ...
        'git_commit',     git_commit_field_(met) ...
    );

    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0
        warning('[記録] experiment_log.json を保存できません: %s', filepath);
        return
    end
    fprintf(fid, '%s\n', jsonencode(log));
    fclose(fid);
end

% ---------------------------------------------------------------------
%  リポジトリの現在のコミットハッシュを取得（再現性記録用）
%  - 未コミットの変更がある場合は末尾に "-dirty" を付ける
%  - git が無い／リポジトリ外の場合は 'unknown' を返す
% ---------------------------------------------------------------------
function h = git_commit_hash_()
    h = 'unknown';
    repo_dir = fileparts(mfilename('fullpath'));
    try
        [st, out] = system(sprintf('git -C "%s" rev-parse HEAD', repo_dir));
        if st == 0 && ~isempty(strtrim(out))
            h = strtrim(out);
            [st2, dirty] = system(sprintf('git -C "%s" status --porcelain', repo_dir));
            if st2 == 0 && ~isempty(strtrim(dirty))
                h = [h '-dirty'];   % 未コミットの変更あり
            end
        end
    catch
        % git 未導入等 → 'unknown' のまま
    end
end

% ---------------------------------------------------------------------
%  met から git_commit を安全に取り出す（未設定なら取得を試みる）
% ---------------------------------------------------------------------
function h = git_commit_field_(met)
    if isfield(met, 'git_commit') && ~isempty(met.git_commit)
        h = met.git_commit;
    else
        h = git_commit_hash_();
    end
end

function offset_mV = measure_volt_offset_(s_volt)
    MEAS_SEC = 5;

    fprintf('[オフセット計測] 無風時の差圧電圧を %.0f 秒間計測します...\n', MEAS_SEC);

    samples = zeros(1, 200);
    n = 0;
    t_end = tic;

    while toc(t_end) < MEAS_SEC
        try
            writeline(s_volt, 'MD?');
            raw  = readline(s_volt);
            v_mv = str2double(strtrim(raw)) * 1000;
            if ~isnan(v_mv)
                n = n + 1;
                if n > numel(samples)
                    samples = [samples, zeros(1, 100)]; %#ok<AGROW>
                end
                samples(n) = v_mv;
                fprintf('  %2d サンプル  最新: %+.2f mV\r', n, v_mv);
            end
        catch
        end
    end
    fprintf('\n');

    if n == 0
        warning('[オフセット計測] サンプルを取得できませんでした。');
        offset_mV = NaN;
        return;
    end

    offset_mV = mean(samples(1:n));
    fprintf('  → 電圧オフセット = %+.4f mV  (%d サンプル)\n\n', offset_mV, n);
end

function name = input_experiment_name_()
    fprintf('=== 実験フォルダ名の入力 ===\n');
    fprintf('  例: 260604_rigid\n\n');
    forbidden = '/\:*?"<>|';
    while true
        name = strtrim(input('実験フォルダ名: ', 's'));
        if ~isempty(name) && ~any(ismember(name, forbidden)), break; end
        fprintf('  ※ 有効なフォルダ名を入力してください（空白のみ・特殊文字 %s 不可）\n', forbidden);
    end
    fprintf('→ フォルダ名 [%s] を使用\n\n', name);
end

function max_angle = input_max_angle_()
    fprintf('=== 迎角範囲の設定 ===\n\n');
    while true
        val = input('最大迎角 [度, 1-30, デフォルト=30]: ');
        if isempty(val), max_angle = 30; break; end
        if isnumeric(val) && isscalar(val) && val >= 1 && val <= 30 && val == floor(val)
            max_angle = val; break;
        end
        fprintf('  ※ 1〜30 の整数を入力してください。\n');
    end
    fprintf('→ 最大迎角 %d°（%d 点/フェーズ）\n\n', max_angle, 1 + max_angle * 2);
end

function idx = select_start_phase_()
    fprintf('=== 開始フェーズの選択 ===\n');
    fprintf('  通常は 1 を選択。エラー中断後の再開は対象フェーズ番号を入力してください。\n\n');
    fprintf('  1: Pofst（正迎角・無風）\n');
    fprintf('  2: Mofst（負迎角・無風）\n');
    fprintf('  3: Pdata（正迎角・有風）\n');
    fprintf('  4: Mdata（負迎角・有風）\n\n');
    while true
        val = input('開始フェーズ [1-4, デフォルト=1]: ');
        if isempty(val), idx = 1; break; end
        if isnumeric(val) && isscalar(val) && ismember(val, 1:4), idx = val; break; end
        fprintf('  ※ 1〜4 の数字を入力してください。\n');
    end
    phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
    fprintf('→ [%s] から開始\n\n', phases{idx});
end

function action = ask_error_action_(ME, phase, idx, n_total)
    % エラー発生時にユーザーへ対処を確認する
    fprintf('\n');
    fprintf('════════════════════════════════════════\n');
    fprintf('  [エラー] 計測点 %d/%d でエラーが発生しました\n', idx, n_total);
    fprintf('  フェーズ: %s\n', phase);
    fprintf('  内容: %s\n', ME.message);
    fprintf('════════════════════════════════════════\n\n');
    fprintf('どうしますか？\n');
    fprintf('  R: この計測点を再試行する\n');
    fprintf('  S: この点をスキップして続ける\n');
    fprintf('  P: このフェーズ（%s）を最初からやり直す\n', phase);
    fprintf('  C: やり直すフェーズを選択する\n');
    fprintf('  Q: 実験を終了する\n\n');

    valid = {'R','S','P','C','Q'};
    while true
        choice = upper(strtrim(input('選択 [R/S/P/C/Q]: ', 's')));
        if ismember(choice, valid), break; end
        fprintf('  ※ R, S, P, C, Q のいずれかを入力してください。\n');
    end
    fprintf('\n');

    switch choice
        case 'R'; action = 'retry';
        case 'S'; action = 'skip';
        case 'P'; action = 'restart_phase';
        case 'C'; action = 'goto_phase';
        case 'Q'; action = 'quit';
    end
end

function ph = ask_goto_phase_()
    % やり直すフェーズをユーザーに選ばせる
    fprintf('やり直すフェーズを選択してください:\n');
    fprintf('  1: Pofst（正迎角・無風）\n');
    fprintf('  2: Mofst（負迎角・無風）\n');
    fprintf('  3: Pdata（正迎角・有風）\n');
    fprintf('  4: Mdata（負迎角・有風）\n\n');
    while true
        val = input('フェーズ番号 [1-4]: ');
        if isnumeric(val) && isscalar(val) && ismember(val, 1:4), ph = val; break; end
        fprintf('  ※ 1〜4 の数字を入力してください。\n');
    end
    phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
    fprintf('→ [%s] からやり直します。\n\n', phases{ph});
end

function action = ask_stop_action_()
    % 停止ボタン押下時にユーザーへ次の動作を確認する（ask_error_action_ と同スタイル）
    fprintf('\n');
    fprintf('════════════════════════════════════════\n');
    fprintf('  [停止] 計測を停止しました\n');
    fprintf('════════════════════════════════════════\n\n');
    fprintf('どうしますか？\n');
    fprintf('  F: 完全に最初からやり直す（気温・気圧の入力から）\n');
    fprintf('  A: 気温・気圧の後からやり直す（迎角範囲・開始フェーズの選択から）\n');
    fprintf('  M: 計測を途中からやり直す（やり直すフェーズを選択）\n');
    fprintf('  Q: 実験を終了する\n\n');

    valid = {'F','A','M','Q'};
    while true
        choice = upper(strtrim(input('選択 [F/A/M/Q]: ', 's')));
        if ismember(choice, valid), break; end
        fprintf('  ※ F, A, M, Q のいずれかを入力してください。\n');
    end
    fprintf('\n');

    switch choice
        case 'F'; action = 'restart_full';
        case 'A'; action = 'restart_after_met';
        case 'M'; action = 'restart_goto';
        case 'Q'; action = 'quit';
    end
end

function action = handle_stop_(stage, logger, s_volt, monitor) %#ok<INUSD>
    % 停止検出時の共通処理:
    %   1. 進行中の6軸記録を中断
    %   2. 翼を 0° へ戻す
    %   3. モニタの停止状態をリセット（再開できるように）
    %   4. メニューを表示して次の動作を返す
    try; logger.stop(); catch; end
    fprintf('\n[停止] 計測を停止しています...\n');
    try; stage.moveToAngle(0); catch; end
    monitor.resetControl();
    action = ask_stop_action_();
end

function notify_sound_(n_tones)
    % 通知音を鳴らす（n_tones 個の上昇音）。
    % 音声デバイスが無い環境でも実験は止めないよう try/catch で保護する。
    if nargin < 1, n_tones = 2; end
    try
        Fs    = 8000;
        t     = 0:1/Fs:0.16;                 % 1音あたり約0.16秒
        env   = linspace(1, 0, numel(t));    % 簡易フェードアウト（プチノイズ抑制）
        freqs = [784, 988, 1175, 1568];      % G5, B5, D6, G6（上昇する明るい音）
        y = [];
        for k = 1:min(n_tones, numel(freqs))
            y = [y, sin(2*pi*freqs(k)*t) .* env]; %#ok<AGROW>
        end
        sound(y * 0.3, Fs);   % 音量30%
    catch
        try; beep; catch; end   % フォールバック（システムビープ）
    end
end

function delete_phase_data_(data_dir, date_str, phase)
    % フェーズ再試行前に、そのフェーズの部分データを data/ から削除する
    yy_date = date_str(3:end);
    files = dir(fullfile(data_dir, sprintf('%s_*_%s_%s_*', date_str, yy_date, phase)));
    if isempty(files), return; end
    fprintf('[削除] %s フェーズの部分データ %d ファイルを削除します...\n\n', phase, numel(files));
    for i = 1:numel(files)
        delete(fullfile(files(i).folder, files(i).name));
    end
end

function run_postprocess_if_ready_(exp_dir, date_str, cfg)
    % 全4フェーズの volt_summary が揃っていれば後処理を実行する
    %   Step 0: post_process/venv が未作成なら 64bit Python で作成・パッケージインストール
    %   Step 1: make_windspeed.py → windspeed.csv
    %   Step 2: calc_force.py    → 空力係数 CSV・グラフ PNG

    phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
    for i = 1:numel(phases)
        fname = make_filename(date_str, '', phases{i}, 0, 0, 'volt_summary');
        if ~isfile(fullfile(exp_dir, fname))
            fprintf('[後処理] %s がまだありません → 後処理をスキップ\n\n', fname);
            return
        end
    end

    fprintf('\n[後処理] 全フェーズのデータが揃いました。後処理を開始します...\n\n');

    script_dir   = fileparts(mfilename('fullpath'));
    make_ws_path = fullfile(script_dir, 'post_process', 'make_windspeed.py');
    calc_f_path  = fullfile(script_dir, 'post_process', 'calc_force.py');

    % --- Step 0: 仮想環境の準備 ---
    py64 = cfg.python_exe_64;
    if isempty(py64)
        fprintf('[警告] python_exe_64 が config.json に設定されていません。\n');
        fprintf('         32bit Python (%s) で後処理を試みます。\n\n', cfg.python_exe);
        py64 = cfg.python_exe;
    end
    venv_python = setup_postprocess_venv_(script_dir, py64);

    % --- Step 1: windspeed.csv 生成 ---
    fprintf('[後処理 1/2] windspeed.csv を生成中...\n');
    cmd_ws = sprintf('"%s" "%s" --volt_dir "%s" --date %s --out "%s"', ...
        venv_python, make_ws_path, exp_dir, date_str, exp_dir);
    [st1, out1] = system(cmd_ws);
    if ~isempty(strtrim(out1)), fprintf('%s\n', out1); end
    if st1 ~= 0
        fprintf('[警告] make_windspeed.py に失敗しました（終了コード %d）。後処理を中断します。\n\n', st1);
        return
    end

    % --- Step 2: calc_force.py で空力係数・グラフ生成 ---
    fprintf('[後処理 2/2] 空力係数を計算・グラフを出力中...\n');
    prev_dir = cd(exp_dir);
    [st2, out2] = system(sprintf('"%s" "%s"', venv_python, calc_f_path));
    cd(prev_dir);
    if ~isempty(strtrim(out2)), fprintf('%s\n', out2); end
    if st2 ~= 0
        fprintf('[警告] calc_force.py に失敗しました（終了コード %d）。\n\n', st2);
        return
    end

    fprintf('[後処理完了] グラフを %s に保存しました。\n\n', exp_dir);

    % --- Step 3: 過去データとの比較（rigid 実験のみ・確認の上で実行）---
    [~, exp_name] = fileparts(exp_dir);
    if contains(lower(exp_name), 'rigid')
        ans_cmp = input('過去データと比較しますか？ [y/N]: ', 's');
        if any(strcmpi(strtrim(ans_cmp), {'y', 'yes'}))
            updater = fullfile(script_dir, '考察', 'update_aero_data.py');
            if ~isfile(updater)
                fprintf('[比較] update_aero_data.py が見つかりません: %s\n\n', updater);
                return
            end
            % 実験フォルダの親を探索元として、空力データ同期＋パワポ再生成
            % （venv セットアップで python-pptx 含む必要モジュールは導入済み）
            src_parent = fileparts(exp_dir);
            fprintf('[比較] 過去データと比較し、考察フォルダのパワポを更新します...\n');
            [stc, outc] = system(sprintf('"%s" "%s" "%s"', ...
                venv_python, updater, src_parent));
            if ~isempty(strtrim(outc)), fprintf('%s\n', outc); end
            if stc == 0
                fprintf('[比較完了] 考察フォルダの比較パワポを更新しました。\n\n');
            else
                fprintf('[比較] 失敗しました（終了コード %d）。\n\n', stc);
            end
        end
    end
end

function venv_python = setup_postprocess_venv_(script_dir, python_exe_64)
    % post_process/venv を用意して venv の python パスを返す。
    %   1. venv が無ければ 64bit Python で作成 → requirements をインストール
    %   2. 既存 venv のパッケージが足りなければ再インストール
    %   3. それでも直らなければ venv フォルダを自動削除して作り直す
    %  → 半端な venv / 32bit で作られた古い venv でも自動で復旧する。

    venv_dir = fullfile(script_dir, 'post_process', 'venv');
    req_path = fullfile(script_dir, 'post_process', 'requirements.txt');

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
