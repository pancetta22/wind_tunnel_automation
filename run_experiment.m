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

% 気象条件（1回のみ入力）
met = input_met_conditions_();
met.calib_a        = cfg.calib_a;
met.calib_b        = cfg.calib_b;
met.volt_offset_mV = cfg.volt_offset_mV;

log_path = fullfile(exp_dir, sprintf('%s_experiment_log.json', date_str));
save_experiment_log_(log_path, date_str, met);
fprintf('[記録] 気象条件を保存しました\n\n');

% 迎角範囲・開始フェーズ
max_angle = input_max_angle_();
start_idx = select_start_phase_();

% =====================================================================
%  3. 全フェーズ 連続計測
% =====================================================================
all_phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
n_phases   = numel(all_phases);

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
            if strcmp(monitor.getPauseAction(), 'stop')
                error('windy:manual_stop', '手動停止が選択されました');
            end
            fprintf('[再開] 計測を再開します。\n\n');
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

    % ---- フェーズ終了後の処理 ----
    switch phase_action
        case 'next'
            fprintf('=== %s フェーズ完了 ===\n\n', phase);
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

% =====================================================================
%  4. 実験完了・後処理
% =====================================================================
fprintf('=== 全フェーズ完了 ===\n');
fprintf('[保存先] %s\n\n', exp_dir);

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
        'calib_b',        met.calib_b          ...
    );

    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0
        warning('[記録] experiment_log.json を保存できません: %s', filepath);
        return
    end
    fprintf(fid, '%s\n', jsonencode(log));
    fclose(fid);
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

    % --- Step 1: windspeed.csv 生成 ---
    fprintf('[後処理 1/2] windspeed.csv を生成中...\n');
    cmd_ws = sprintf('"%s" "%s" --volt_dir "%s" --date %s --out "%s"', ...
        cfg.python_exe, make_ws_path, exp_dir, date_str, exp_dir);
    [st1, out1] = system(cmd_ws);
    if ~isempty(strtrim(out1)), fprintf('%s\n', out1); end
    if st1 ~= 0
        fprintf('[警告] make_windspeed.py に失敗しました（終了コード %d）。後処理を中断します。\n\n', st1);
        return
    end

    % --- Step 2: calc_force.py で空力係数・グラフ生成 ---
    fprintf('[後処理 2/2] 空力係数を計算・グラフを出力中...\n');
    prev_dir = cd(exp_dir);
    [st2, out2] = system(sprintf('"%s" "%s"', cfg.python_exe, calc_f_path));
    cd(prev_dir);
    if ~isempty(strtrim(out2)), fprintf('%s\n', out2); end
    if st2 ~= 0
        fprintf('[警告] calc_force.py に失敗しました（終了コード %d）。\n\n', st2);
        return
    end

    fprintf('[後処理完了] グラフを %s に保存しました。\n\n', exp_dir);
end
