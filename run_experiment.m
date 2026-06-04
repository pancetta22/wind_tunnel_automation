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
%   Pofst: 正迎角 (0°→+30°)・無風   Mofst: 負迎角 (0°→-30°)・無風
%   Pdata: 正迎角 (0°→+30°)・有風   Mdata: 負迎角 (0°→-30°)・有風

% =====================================================================
%  0. 設定読み込み
% =====================================================================
cfg = load_config_(fileparts(mfilename('fullpath')));

fprintf('\n========================================\n');
fprintf('  Windy 風洞実験自動計測システム\n');
fprintf('========================================\n\n');

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

% リアルタイムモニタ起動
monitor = WindyMonitor(cfg.force_sensor_size_limit_kb);

% =====================================================================
%  2. フェーズ選択
% =====================================================================
phase = select_phase_();
monitor.setPhase(phase);

% =====================================================================
%  3. 出力ディレクトリ・ファイル準備
% =====================================================================
t_start  = datetime('now');
date_str = sprintf('%04d%02d%02d', year(t_start), month(t_start), day(t_start));

if ~exist(cfg.output_dir, 'dir')
    mkdir(cfg.output_dir);
    fprintf('[準備] 出力フォルダを作成: %s\n', cfg.output_dir);
end

summary_fname = make_filename(date_str, '', phase, 0, 0, 'volt_summary');
summary_path  = fullfile(cfg.output_dir, summary_fname);
init_volt_summary_(summary_path);
fprintf('[準備] デジボルサマリー: %s\n\n', summary_fname);

% =====================================================================
%  3.5. 気象条件の入力（気温・気圧 → 空気密度 ρ）
% =====================================================================
met = input_met_conditions_();
met.calib_a        = cfg.calib_a;
met.calib_b        = cfg.calib_b;
met.volt_offset_mV = cfg.volt_offset_mV;
met.water_density  = cfg.water_density;

log_fname = sprintf('%s_experiment_log.json', date_str);
log_path  = fullfile(cfg.output_dir, log_fname);
save_experiment_log_(log_path, date_str, met);
fprintf('[記録] 気象条件を保存: %s\n\n', log_fname);

% =====================================================================
%  4. フェーズ開始確認（ブロワー状態）
% =====================================================================
confirm_blower_(phase);

% =====================================================================
%  4.5. 差圧センサ電圧オフセット自動計測（無風フェーズのみ）
% =====================================================================
if contains(phase, 'ofst')
    ans_offset = input('差圧センサの電圧オフセットを今計測しますか？ [y/N]: ', 's');
    if strcmpi(strtrim(ans_offset), 'y')
        measured_offset = measure_volt_offset_(s_volt);
        if ~isnan(measured_offset)
            met.volt_offset_mV = measured_offset;
            save_experiment_log_(log_path, date_str, met);
            fprintf('[更新] experiment_log.json を更新 (volt_offset_mV = %.4f mV)\n\n', measured_offset);
        else
            fprintf('[警告] オフセット計測失敗 — config.json の設定値 (%.1f mV) を使用します\n\n', cfg.volt_offset_mV);
        end
    else
        fprintf('[スキップ] config.json の設定値 (%.1f mV) を使用します\n\n', cfg.volt_offset_mV);
    end
end

% =====================================================================
%  5. 計測ループ
% =====================================================================
pts      = build_measurement_sequence_(phase);
n_total  = numel(pts);

fprintf('\n=== %s フェーズ開始 (%d 点) ===\n\n', phase, n_total);

% --- Ctrl+C で中断されても後片付けできるよう try-catch で囲む ---
try
    for idx = 1:n_total
        pt = pts(idx);

        % ------ 一時停止チェック ------
        if monitor.isPaused()
            fprintf('[一時停止] WindyMonitor の「再開」ボタンを押すと計測を再開します...\n');
            while monitor.isPaused()
                pause(0.2);
                drawnow;
            end
            fprintf('[再開] 計測を再開します。\n\n');
        end

        % ------ a. 迎角ステージ移動 ------
        fprintf('[%d/%d] 迎角 %+d° へ移動中...\n', idx, n_total, pt.target_angle);
        stage.moveToAngle(pt.target_angle);
        fprintf('[%d/%d] 迎角 %+d° に到達\n', idx, n_total, pt.target_angle);

        % ------ b. 振動収束待ち ------
        fprintf('[待機] 振動収束待ち... %.1f 秒\n', cfg.angle_settle_sec);
        pause(cfg.angle_settle_sec);

        % ------ c. ファイル名生成 ------
        t_now    = datetime('now');
        time_str = sprintf('%02d%02d%02d', hour(t_now), minute(t_now), floor(second(t_now)));
        fname_force   = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'full');
        fname_volt_raw = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'volt_raw');
        fname_short   = make_filename(date_str, time_str, phase, pt.ref_angle, pt.suffix, 'short');
        force_path    = fullfile(cfg.output_dir, fname_force);
        volt_raw_path = fullfile(cfg.output_dir, fname_volt_raw);

        % ------ d. 同時計測開始 ------
        monitor.resetGraph();
        fprintf('[計測開始] 6軸センサ & デジボル 同時計測中...\n');
        logger.start(force_path);
        pause(0.5);   % Python 起動待ち（DLL 初期化を含む）

        if ~logger.isAlive()
            error('[LeptrinoLogger] Python プロセスの起動に失敗しました。\n  python_exe や leptrino_port を確認してください。');
        end

        % ------ e. デジボル計測ループ（6軸センサが完了するまで続ける）------
        voltages = zeros(1, 500);   % 事前確保（~20秒×~10サンプル/秒 = ~200点）
        nv = 0;
        while ~logger.isDone()
            try
                writeline(s_volt, 'MD?');
                raw  = readline(s_volt);
                v_mv = str2double(strtrim(raw)) * 1000;   % V → mV
                if ~isnan(v_mv)
                    nv = nv + 1;
                    if nv > numel(voltages)
                        voltages = [voltages, zeros(1, 200)]; %#ok<AGROW>
                    end
                    voltages(nv) = v_mv;
                    sz_kb = logger.getSizeKB();
                    fprintf('  6軸: %6.1f KB / %.0f KB  |  デジボル: %3d サンプル (%.2f mV)\r', ...
                        sz_kb, cfg.force_sensor_size_limit_kb, nv, v_mv);
                    % モニタ更新（drawnow limitrate で描画負荷を抑制）
                    prog = struct('idx', idx, 'total', n_total, ...
                                  'size_kb', sz_kb, 'limit_kb', cfg.force_sensor_size_limit_kb);
                    monitor.update(pt.target_angle, logger.getLatest(), v_mv, prog);
                end
            catch ME
                warning('windy:r6441b', '%s', ME.message);
            end
        end
        voltages = voltages(1:nv);   % 有効分だけ切り出す
        fprintf('\n');   % \r で上書きした行の後に改行

        % ------ f. 6軸センサの完全終了を待つ ------
        logger.waitForFinish();
        lresult = logger.getResult();

        avg_mv = NaN;
        if ~isempty(voltages)
            avg_mv = mean(voltages);
        end

        fprintf('[計測完了] 6軸センサ: %.1f KB  |  デジボル: %d サンプル', ...
            logger.getSizeKB(), numel(voltages));
        if ~isnan(avg_mv)
            fprintf(' (平均: %.2f mV)', avg_mv);
        end
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


    end % for idx

catch ME
    fprintf('\n[中断] 計測中にエラーが発生しました: %s\n', ME.message);
    cleanup_devices_(stage, logger, s_volt, monitor);
    rethrow(ME);
end

% =====================================================================
%  6. フェーズ完了
% =====================================================================
fprintf('=== %s フェーズ完了 ===\n', phase);
stage.moveToAngle(0);
fprintf('[保存先] %s\n\n', cfg.output_dir);

cleanup_devices_(stage, logger, s_volt, monitor);

% =====================================================================
%  ローカル関数
% =====================================================================

function cfg = load_config_(base_dir)
    % config.json を読み込み、不足キーにデフォルト値を補完する
    config_path = fullfile(base_dir, 'config.json');
    if ~isfile(config_path)
        error(['config.json が見つかりません。\n' ...
               'config.json.example をコピーして config.json を作成し、\n' ...
               '各自の環境に合わせて設定してください。\n  パス: %s'], config_path);
    end
    cfg = jsondecode(fileread(config_path));

    % デフォルト値の補完
    if ~isfield(cfg, 'force_sensor_size_limit_kb') || isempty(cfg.force_sensor_size_limit_kb)
        cfg.force_sensor_size_limit_kb = 1000;
    end
    if ~isfield(cfg, 'angle_settle_sec') || isempty(cfg.angle_settle_sec)
        cfg.angle_settle_sec = 2.0;
    end
    if ~isfield(cfg, 'csv_decimal_places')
        cfg.csv_decimal_places = [];  % null → 既存CSV形式準拠
    end
    if ~isfield(cfg, 'r6441b_timeout_sec') || isempty(cfg.r6441b_timeout_sec)
        cfg.r6441b_timeout_sec = 5;
    end
    % 差圧センサ校正定数のデフォルト値（センサ交換・再校正時は config.json で上書き）
    if ~isfield(cfg, 'water_density') || isempty(cfg.water_density)
        cfg.water_density = 0.99704;                    % 水密度 [g/cm³]
    end
    if ~isfield(cfg, 'volt_offset_mV') || isempty(cfg.volt_offset_mV)
        cfg.volt_offset_mV = -5.0;                      % 零点オフセット [mV]
    end
    if ~isfield(cfg, 'calib_a') || isempty(cfg.calib_a)
        cfg.calib_a = 0.007904809948345278;             % 変換係数 a [cm/mV]
    end
    if ~isfield(cfg, 'calib_b') || isempty(cfg.calib_b)
        cfg.calib_b = -0.340200009144243;               % 変換係数 b [cm]
    end
end

function s = connect_r6441b_(com_port, timeout_sec)
    % R6441B デジタルマルチメータ（RS-232C）に接続する
    s = serialport(com_port, 9600, ...
        'DataBits',    8,      ...
        'Parity',      'none', ...
        'StopBits',    1,      ...
        'FlowControl', 'none');
    configureTerminator(s, 'CR/LF');
    s.Timeout = timeout_sec;
    fprintf('[接続] R6441B に接続しました: %s\n', com_port);
end

function phase = select_phase_()
    % 計測フェーズをユーザーに選択させる
    fprintf('計測フェーズを選択してください:\n');
    fprintf('  1: Pofst（正迎角 0°→+30°・無風）\n');
    fprintf('  2: Mofst（負迎角 0°→-30°・無風）\n');
    fprintf('  3: Pdata（正迎角 0°→+30°・有風）\n');
    fprintf('  4: Mdata（負迎角 0°→-30°・有風）\n');

    while true
        choice = input('番号を入力 [1-4]: ');
        if isnumeric(choice) && ismember(choice, 1:4)
            break;
        end
        fprintf('  ※ 1〜4 の数字を入力してください。\n');
    end

    phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
    phase  = phases{choice};
    fprintf('→ フェーズ [%s] を選択\n\n', phase);
end

function confirm_blower_(phase)
    % ブロワー状態の確認を実験員に促す
    if contains(phase, 'ofst')
        msg = 'ブロワーが停止していることを確認してください';
    else
        msg = 'ブロワーを起動し、風速が安定したことを確認してください';
    end
    input(sprintf('>> %s。\n   確認できたら Enter を押してください: ', msg));
    fprintf('\n');
end

function pts = build_measurement_sequence_(phase)
    % 迎角シーケンスを生成する
    %
    % 返値: struct 配列。各要素のフィールド:
    %   target_angle : 実際に移動する角度 [度]（負も可）
    %   ref_angle    : ファイル名に使う参照迎角（0〜30, 常に正）
    %   suffix       : ファイル名サフィックス（0=0°計測, 1=目標迎角計測）
    %
    % Pフェーズ: 0° → 1° → 0° → 2° → 0° → ... → 30° → 0°
    % Mフェーズ: 0° → -1° → 0° → -2° → 0° → ... → -30° → 0°

    if startsWith(phase, 'P')
        angle_sign = +1;
    else
        angle_sign = -1;
    end

    % 事前にサイズを確定して配列確保（61 点）
    n_pts = 1 + 30 * 2;
    pts(n_pts) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    % 1点目: 最初の 0° 計測
    pts(1) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    k = 2;
    for n = 1:30
        pts(k)   = struct('target_angle', angle_sign * n, 'ref_angle', n, 'suffix', 1);
        pts(k+1) = struct('target_angle', 0,              'ref_angle', n, 'suffix', 0);
        k = k + 2;
    end
end

function init_volt_summary_(filepath)
    % デジボルサマリー CSV を新規作成してヘッダ行を書く
    % 既存ファイルは上書きする
    fid = fopen(filepath, 'w', 'n', 'UTF-8');
    if fid < 0
        error('デジボルサマリー CSV を作成できません: %s', filepath);
    end
    fprintf(fid, 'No.,迎角,name,差圧電圧[mV],風速[m/s]\n');
    fclose(fid);
end

function append_volt_summary_(filepath, no, angle, name_str, voltages)
    % デジボルサマリー CSV に1行追記する
    if isempty(voltages)
        avg_str = '';   % 計測失敗時は空欄
    else
        avg_str = sprintf('%.4f', mean(voltages));
    end

    fid = fopen(filepath, 'a', 'n', 'UTF-8');
    if fid < 0
        warning('デジボルサマリー CSV に追記できません: %s', filepath);
        return;
    end
    % 末尾の 風速[m/s] は空欄（既存 Python が差圧電圧から計算）
    fprintf(fid, '%d,%d,%s,%s,\n', no, angle, name_str, avg_str);
    fclose(fid);
end

function save_volt_raw_(filepath, voltages)
    % デジボル生データ CSV を保存する（1計測点ごと）
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
    % 機器のクリーンアップ（正常終了・エラー終了共通）
    fprintf('[終了] 機器の接続を閉じます...\n');
    try
        logger.stop();
    catch
    end
    try
        delete(s_volt);
    catch
    end
    try
        delete(stage);
    catch
    end
    try
        monitor.close();
    catch
    end
    clear logger;
end

function met = input_met_conditions_()
    % 気温・気圧を入力させ、空気密度 ρ を計算して返す
    %
    % 返値: met 構造体
    %   .temperature_C  : 気温 [℃]
    %   .pressure_mmHg  : 気圧 [mmHg]
    %   .rho_kg_m3      : 空気密度 [kg/m³]（Excel と同一の計算式）

    fprintf('=== 気象条件の入力 ===\n');
    fprintf('  気温・気圧から空気密度 ρ を自動計算します。\n\n');

    while true
        T = input('気温 [℃]: ');
        if isnumeric(T) && isscalar(T) && T > -20 && T < 50
            break
        end
        fprintf('  ※ 有効な気温を入力してください（-20 ～ 50 ℃）\n');
    end

    while true
        P = input('気圧 [mmHg]: ');
        if isnumeric(P) && isscalar(P) && P > 700 && P < 820
            break
        end
        fprintf('  ※ 有効な気圧を入力してください（700 ～ 820 mmHg）\n');
    end

    % yymmdd.xlsx と同一の計算式
    e     = 6.1078 * 10^(7.5 * T / (237.3 + T));               % 飽和水蒸気圧 [hPa]
    P_cal = 1013.25/760 * (1 - 0.000182 * T) * P;              % 較正気圧 [hPa]
    rho   = 1.293 * (273.15 / (273.15 + T)) ...
          * (P_cal / 1013.25) * (1 - 0.378 * e / P_cal);       % 空気密度 [kg/m³]

    fprintf('\n  → 空気密度 ρ = %.6f kg/m³\n\n', rho);

    met = struct( ...
        'temperature_C', T,   ...
        'pressure_mmHg', P,   ...
        'rho_kg_m3',     rho  ...
    );
end

function save_experiment_log_(filepath, date_str, met)
    % 気象条件・センサ校正定数を JSON ファイルに保存する
    %
    %   filepath : 保存先パス（例: output_dir/20260520_experiment_log.json）
    %   date_str : 実験日 'YYYYMMDD'
    %   met      : input_met_conditions_() の戻り値＋校正定数を追加したもの

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
    % ブロワー停止状態で R6441B を 5 秒間読み取り、電圧オフセットを計測する
    %
    %   s_volt    : serialport オブジェクト（R6441B）
    %
    % 返値:
    %   offset_mV : 計測したオフセット [mV]。取得失敗時は NaN を返す。

    MEAS_SEC = 5;   % 計測時間 [秒]

    fprintf('[オフセット計測] 無風時の差圧電圧を %.0f 秒間計測します...\n', MEAS_SEC);

    samples = zeros(1, 200);
    n = 0;
    t_end = tic;

    while toc(t_end) < MEAS_SEC
        try
            writeline(s_volt, 'MD?');
            raw  = readline(s_volt);
            v_mv = str2double(strtrim(raw)) * 1000;   % V → mV
            if ~isnan(v_mv)
                n = n + 1;
                if n > numel(samples)
                    samples = [samples, zeros(1, 100)]; %#ok<AGROW>
                end
                samples(n) = v_mv;
                fprintf('  %2d サンプル  最新: %+.2f mV\r', n, v_mv);
            end
        catch
            % 読み取りエラーは無視して継続
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
