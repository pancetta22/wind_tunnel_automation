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
% 計測制御ヘルパ（QT_ADL1 / LeptrinoLogger / WindyMonitor / make_filename 等）を
% サブフォルダ measurement_control へ移動したため、パスを通す。
addpath(fullfile(fileparts(mfilename('fullpath')), 'measurement_control'));

cfg = load_config_(fileparts(mfilename('fullpath')));

fprintf('\n========================================\n');
fprintf('  Windy 風洞実験自動計測システム\n');
fprintf('========================================\n\n');

% 前回の実行が Ctrl+C やエラーで中断されていた場合、COM ポートが掴まれた
% ままになっている。残っている解放ガードをここでクリアして発火させ、
% 前回分のデバイス接続を解放してから始める（"serialport unable to read" 対策）。
clear windy_cleanup_guard_
% さらに、ガード作成前（接続〜原点復帰の途中）で中断された場合はガード自体が
% 無いため、ポートを掴んだままのデバイス変数も明示的に解放しておく。
clear stage logger s_volt monitor

% Python 設定の事前確認（leptrino=32bit 必須 / 後処理=64bit 推奨）。
% 取り違えると接続後・実験完了後に遅れて失敗するため、ここで検出する。
check_python_bits_(cfg.python_exe, 32, 'python_exe（Leptrino 計測用）');
if ~isempty(cfg.python_exe_64)
    check_python_bits_(cfg.python_exe_64, 64, 'python_exe_64（後処理用）');
end

% =====================================================================
%  0.5. 実験フォルダ名の入力
% =====================================================================
exp_name = input_experiment_name_(cfg.output_dir);
exp_dir  = fullfile(cfg.output_dir, exp_name);
% 実験フォルダの中は force/（力計測）と picture/（写真）に分け、ログは直下に置く。
%   force/data/     生データ（6軸CSV・volt_summary・volt_raw）
%   force/analysis/ calc_force の出力（後処理で生成）
%   force/comparison/ 過去剛体翼との比較（後処理で生成）
%   picture/photo/  翼模型の写真
data_dir = fullfile(exp_dir, 'force', 'data');
[~, ~]   = mkdir(data_dir);
fprintf('[準備] 実験フォルダ : %s\n', exp_dir);
fprintf('[準備] データフォルダ: %s\n\n', data_dir);

% =====================================================================
%  0.6. 写真撮影の設定（任意。通風時に各迎角で翼模型を LUMIX で撮影）
% =====================================================================
photo_script    = fullfile(fileparts(mfilename('fullpath')), 'diagnostics', 'lumix_capture.py');
[take_photos, photo_dir] = setup_photos_(fullfile(exp_dir, 'picture'));
photo_connected = false;   % 通風フェーズ前のカメラ接続確認を済ませたか

% =====================================================================
%  1. 機器接続
% =====================================================================
fprintf('[接続] 迎角ステージ (%s) に接続中...\n', cfg.qt_adl1_port);
stage = QT_ADL1(cfg.qt_adl1_port, [], cfg.origin_pulse);   % 原点パルスは config.json から
stage.homeReturn();

fprintf('[接続] Leptrino センサ (ポート %d) を確認中...\n', cfg.leptrino_port);
script_path = fullfile(fileparts(mfilename('fullpath')), 'leptrino', 'leptrino_server.py');
logger = LeptrinoLogger(cfg.python_exe, script_path, ...
                        cfg.leptrino_port, cfg.force_sensor_size_limit_kb);

fprintf('[接続] R6441B デジボル (%s) に接続中...\n', cfg.r6441b_port);
s_volt = connect_r6441b_(cfg.r6441b_port, cfg.r6441b_timeout_sec);

monitor = WindyMonitor(cfg.force_sensor_size_limit_kb);
monitor.setDataSource(@() logger.getRecentRows(600));   % 6軸グラフ: 直近 0.5 秒をローリング表示

% 解放ガード：スクリプトがエラーで止まっても、この変数がクリアされる時に
% 必ず cleanup_devices_ が走り、COM ポート・Python プロセスを解放する。
% （正常終了時の明示的な cleanup_devices_ と二重に呼ばれても安全）
windy_cleanup_guard_ = onCleanup(@() cleanup_devices_(stage, logger, s_volt, monitor)); %#ok<NASGU>

% =====================================================================
%  2. 実験設定（気象条件・迎角範囲・開始フェーズ）
% =====================================================================
t_start  = datetime('now');
date_str = sprintf('%04d%02d%02d', year(t_start), month(t_start), day(t_start));

% 実験ログのパス（実験フォルダ直下に置く）
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
% 迎角スイープ設定の既定値（configure_angle_sweep_ で上書き。再開時はこの値を継続）
max_angle     = 30;
angle_step    = 1;
phase_enabled = [true true true true];   % [Pofst Mofst Pdata Mdata]
while true

% ---- 気象条件（必要時のみ入力。再実験で変更しない場合は前回値を継続）----
if need_met
    met = input_met_conditions_();
    met.calib_a        = cfg.calib_a;
    met.calib_b        = cfg.calib_b;
    met.volt_offset_mV = cfg.volt_offset_mV;
    met.origin_pulse   = cfg.origin_pulse;     % 計測時の原点（後処理の α₀→推奨原点の基準）
    met.git_commit     = git_commit_hash_();   % コード版数（再現性記録用）
end
% met（新規入力 or 前回継続）を、現在の実験フォルダのログに保存する
save_experiment_log_(log_path, date_str, met);
fprintf('[記録] 気象条件を保存しました\n\n');

% ---- 迎角範囲・開始フェーズ ----
if preset_start > 0
    start_idx = preset_start;   % 途中からやり直し: 迎角範囲は前回のまま、フェーズ固定
    fprintf('[再開] %s フェーズからやり直します（迎角範囲は前回設定を継続）。\n\n', all_phases{preset_start});
else
    [max_angle, angle_step, phase_enabled] = configure_angle_sweep_();
    start_idx = select_start_phase_();
end

exp_control = '';   % 停止メニューの結果（''=正常完了 / restart_full / restart_after_met / restart_goto / quit）

ph_idx = start_idx;
while ph_idx <= n_phases

    % 正負の選択で無効化されたフェーズ（例: 正のみ計測時の Mofst/Mdata）はスキップ
    if ~phase_enabled(ph_idx)
        fprintf('[スキップ] %s は計測対象外です。\n', all_phases{ph_idx});
        ph_idx = ph_idx + 1;
        continue;
    end

    phase = all_phases{ph_idx};
    monitor.setPhase(phase);

    fprintf('\n========================================\n');
    fprintf('  フェーズ %d/%d: %s\n', ph_idx, n_phases, phase);
    fprintf('========================================\n\n');

    % ---- ファイル準備 ----
    % フェーズ再入場（エラーメニューC・停止メニューからの再開・同フォルダ再実験）で
    % 古い計測CSVが残っていると、calc_force が同一点を二重に読んで結果が汚染される。
    % どの経路から入ってもクリーンに始まるよう、このフェーズの既存ファイルを毎回掃除する
    % （初回は対象が無く何もしない）。
    delete_phase_data_(data_dir, phase);
    summary_fname = make_filename(date_str, '', phase, 0, 0, 'volt_summary');
    summary_path  = fullfile(data_dir, summary_fname);
    init_volt_summary_(summary_path);
    fprintf('[準備] デジボルサマリー: %s\n\n', summary_fname);

    % ---- ブロワー状態の確認 ----
    confirm_blower_(phase);

    % ---- 差圧センサ電圧オフセット自動計測（Pofst で1回だけ）----
    %  連続実験では前回 Mdata の通風が残っていることがあるため、必ず
    %  confirm_blower_（停止確認）の後にオフセットを取り直す。さらに
    %  平均絶対値が大きい（通風中の疑い）場合は再計測できるようにする。
    if strcmp(phase, 'Pofst')
        measured_offset = measure_volt_offset_checked_(s_volt);
        if ~isnan(measured_offset)
            met.volt_offset_mV = measured_offset;
            save_experiment_log_(log_path, date_str, met);
            fprintf('[更新] experiment_log.json を更新 (volt_offset_mV = %.4f mV)\n\n', measured_offset);
        else
            fprintf('[警告] オフセット計測失敗 — config.json の設定値 (%.1f mV) を使用します\n\n', cfg.volt_offset_mV);
        end
    end

    % ---- 通風フェーズの最初に一度だけカメラ接続を確認 ----
    %  カメラ画面で接続を許可する操作が要るため、計測中ではなくここで確認する。
    if take_photos && is_wind_phase_(phase) && ~photo_connected
        photo_connected = confirm_photo_connection_(cfg.python_exe, photo_script);
        if ~photo_connected
            take_photos = false;   % 接続を諦めた → 以降は撮影しない
        end
    end

    % ---- 計測ループ ----
    pts     = build_measurement_sequence_(phase, max_angle, angle_step);
    n_total = numel(pts);
    fprintf('\n=== %s フェーズ開始 (%d 点) ===\n\n', phase, n_total);

    phase_action = 'next';   % 'next' | 'restart' | 'goto' | 'quit'
    goto_ph_idx  = ph_idx;

    idx = 1;
    while idx <= n_total

        pt = pts(idx);

        % この計測点で作るファイルのパス（エラー時の後始末用に毎回リセット。
        % ファイル名確定前にエラーが起きた場合、前の点のファイルを誤って
        % 消さないようにするため）
        force_path    = '';
        volt_raw_path = '';

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
            cap_proc = [];   % 非同期撮影プロセスのハンドル（撮影点でのみ設定）
            cap_manifest = '';            % 撮影記録CSVのパス（撮影点で設定）
            cap_shots_reported = 0;       % 端末に表示済みの撮影枚数

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

            % ------ d2. 写真撮影を力計測と並行で非同期起動（通風時の各迎角＋最初の0°）------
            %  迎角を保持している計測中にバックグラウンドでシャッターを切る（直列にせず短縮）。
            %  カメラ=Wi-Fi／力センサ=USB／デジボル=シリアルで経路が独立。完了待ち(join)は
            %  下の j でステージ移動前に行う。撮影失敗は計測を止めない。
            if take_photos && is_wind_phase_(phase) && (pt.suffix == 1 || idx == 1)
                cap_label    = angle_label_(pt.target_angle);
                cap_manifest = fullfile(fileparts(photo_dir), '_shot_manifest.csv');
                cap_shots_reported = announce_new_shots_(cap_manifest, inf);  % 既存行数に同期(印字なし)
                cap_proc  = start_capture_async_(cfg.python_exe, photo_script, ...
                    photo_dir, cap_label, 3, pt.target_angle, phase);
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
                    % 並行撮影が走っていれば、撮れた枚数をリアルタイムに端末へ表示
                    if ~isempty(cap_proc)
                        cap_shots_reported = announce_new_shots_(cap_manifest, cap_shots_reported);
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

            % ------ f2. 計測量の妥当性チェック ------
            % Python プロセスが計測途中で死んだ場合（USB瞬断など）、isDone は
            % プロセス終了側の条件で true になるため、データ不足のまま「成功」
            % 扱いになってしまう。目標サイズに大きく満たなければエラーにして
            % エラーメニュー（再試行/スキップ等）へ回す。
            % （再試行時は失敗点の部分ファイルが自動削除される）
            sz_kb = logger.getSizeKB();
            if sz_kb < 0.8 * cfg.force_sensor_size_limit_kb
                error(['[計測量不足] 6軸センサCSVが %.1f KB しかありません（目標 %.0f KB）。\n' ...
                       '  計測途中でセンサプロセスが終了した可能性があります。\n' ...
                       '  USB接続・センサ電源を確認してください。'], ...
                      sz_kb, cfg.force_sensor_size_limit_kb);
            end

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

            % ------ j. 並行撮影の完了待ち（d2で起動したぶんをステージ移動前にjoin）------
            %  タイムアウト付き。カメラがハングしても力計測（本命）は止めない。
            if ~isempty(cap_proc)
                cap_shots_reported = announce_new_shots_(cap_manifest, cap_shots_reported);
                wait_capture_async_(cap_proc, cap_label, 15);
                cap_shots_reported = announce_new_shots_(cap_manifest, cap_shots_reported);
                cap_proc = [];
            end

            idx = idx + 1;   % 正常完了 → 次の計測点へ

        catch ME_meas
            % 並行撮影プロセスが走っていれば破棄（角度が変わる前に止める）
            if exist('cap_proc', 'var') && ~isempty(cap_proc)
                try; cap_proc.destroy(); catch; end
                cap_proc = [];
            end
            % ------ エラー発生 → 対処を確認 ------
            action_str = ask_error_action_(ME_meas, phase, idx, n_total);
            switch action_str
                case 'retry'
                    fprintf('[再試行] 計測点 %d/%d を再試行します。\n\n', idx, n_total);
                    try; logger.stop(); catch; end             % ファイルを掴んでいる場合に備え停止
                    delete_point_files_(force_path, volt_raw_path);   % 失敗点の残骸を削除（重複防止）
                    % idx は変えない

                case 'skip'
                    fprintf('[スキップ] 計測点 %d/%d をスキップします。\n\n', idx, n_total);
                    try; logger.stop(); catch; end
                    delete_point_files_(force_path, volt_raw_path);   % 失敗点の残骸を削除
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
            % フェーズを最初からやり直す
            % （部分データはフェーズ先頭の delete_phase_data_ で自動削除される）
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
        % --- 完了通知・後処理 ---
        fprintf('=== 全フェーズ完了 ===\n');
        fprintf('[保存先] %s\n\n', exp_dir);
        notify_sound_(4);   % 全実験完了を音で通知
        run_postprocess_if_ready_(exp_dir, date_str, cfg, phase_enabled);

        % --- もう一度実験を行うか？ ---
        ans_again = strtrim(input('もう一度実験を行いますか？ [y/n]: ', 's'));
        if ~any(strcmpi(ans_again, {'y', 'yes'}))
            fprintf('[終了] 実験を終了します。\n\n');
            cleanup_devices_(stage, logger, s_volt, monitor);
            return;
        end

        % --- 気温・気圧を変更するか？（しない場合は前回値を継続）---
        ans_met  = strtrim(input('気温・気圧を変更しますか？ [y/n]: ', 's'));
        need_met = any(strcmpi(ans_met, {'y', 'yes'}));

        % --- 新しい実験フォルダを用意（前回データと混ざらないよう別フォルダ）---
        exp_name = input_experiment_name_(cfg.output_dir);
        exp_dir  = fullfile(cfg.output_dir, exp_name);
        data_dir = fullfile(exp_dir, 'force', 'data');
        [~, ~]   = mkdir(data_dir);
        t_start  = datetime('now');
        date_str = sprintf('%04d%02d%02d', year(t_start), month(t_start), day(t_start));
        log_path = fullfile(exp_dir, sprintf('%s_experiment_log.json', date_str));
        fprintf('[準備] 新しい実験フォルダ: %s\n\n', exp_dir);

        % 新しい実験フォルダ用に撮影設定を再確認（フォルダ毎に picture/photo を作る）
        [take_photos, photo_dir] = setup_photos_(fullfile(exp_dir, 'picture'));
        photo_connected = false;

        preset_start = 0;   % 最初のフェーズから
        continue;           % 外側リスタートループの先頭へ

    case 'restart_full'      % 完全に最初から（気温・気圧から）
        clear_experiment_data_(exp_dir, photo_dir);   % 構成変更(P+M→P等)で残る旧データを一掃
        need_met = true;  preset_start = 0;
        fprintf('[再起動] 完全に最初からやり直します。\n\n');
        continue;

    case 'restart_after_met' % 気温・気圧の後から（迎角範囲・開始フェーズの選択から）
        clear_experiment_data_(exp_dir, photo_dir);   % 構成変更(P+M→P等)で残る旧データを一掃
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
% ※ ループ完了・後処理・「もう一度実験するか」の確認は、上の switch の
%   case '' 内で行う（正常完了時はそこで return される）。

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

    % --- 必須キーの検証（欠けていると後で意味不明なエラーになるため先に確認）---
    required = {'python_exe', 'qt_adl1_port', 'r6441b_port', 'leptrino_port', ...
                'output_dir', 'calib_a', 'calib_b'};
    missing = {};
    for i = 1:numel(required)
        if ~isfield(cfg, required{i}) || isempty(cfg.(required{i}))
            missing{end+1} = required{i}; %#ok<AGROW>
        end
    end
    if ~isempty(missing)
        error(['config.json に必須キーが設定されていません: %s\n' ...
               'config.json.example を参考に設定してください。\n  パス: %s'], ...
              strjoin(missing, ', '), config_path);
    end

    if ~isfield(cfg, 'force_sensor_size_limit_kb') || isempty(cfg.force_sensor_size_limit_kb)
        cfg.force_sensor_size_limit_kb = 1000;
    end
    if ~isfield(cfg, 'angle_settle_sec') || isempty(cfg.angle_settle_sec)
        cfg.angle_settle_sec = 2.0;
    end
    if ~isfield(cfg, 'csv_decimal_places')
        cfg.csv_decimal_places = [];
    end
    if ~isfield(cfg, 'origin_pulse') || isempty(cfg.origin_pulse)
        cfg.origin_pulse = 11025;   % 迎角0°の機械座標 [pulse]（QT_ADL1 の既定）
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

function check_python_bits_(python_exe, want_bits, label)
    % Python のビット数を確認する。
    % Leptrino の CfsUsb.dll は 32bit 専用、後処理の venv は 64bit が必要なため、
    % 設定の取り違えを実験開始前に検出する。
    cmd = sprintf('"%s" -c "import struct; print(struct.calcsize(''P'')*8)"', python_exe);
    [st, out] = system(cmd);
    if st ~= 0
        error(['[設定確認] %s を実行できません。\n' ...
               '  config.json のパスを確認してください: %s'], label, python_exe);
    end
    bits = str2double(strtrim(out));
    if isnan(bits)
        fprintf('[設定確認] %s のビット数を判定できませんでした（そのまま続行します）。\n', label);
        return
    end
    if bits ~= want_bits
        if want_bits == 32
            error(['[設定確認] %s は %dbit Python です（32bit が必要）。\n' ...
                   '  Leptrino の CfsUsb.dll は 32bit 専用のため、このままでは計測できません。\n' ...
                   '  config.json の python_exe に 32bit Python のパスを設定してください。\n' ...
                   '  現在の設定: %s'], label, bits, python_exe);
        else
            fprintf(['[警告] %s は %dbit Python です（64bit 推奨）。\n' ...
                     '       後処理パッケージの導入に失敗する可能性があります。\n\n'], label, bits);
        end
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
    % 's' を付けて文字列として受ける（付けないと入力がMATLAB式として評価され、
    % 誤って文字を打つと「予約キーワード」等のエラーで落ちる）。
    input(sprintf('>> %s。\n   確認できたら Enter を押してください: ', msg), 's');
    fprintf('\n');
end

function pts = build_measurement_sequence_(phase, max_angle, angle_step)
    % 迎角シーケンスを生成する
    %
    % 返値: struct 配列。各要素のフィールド:
    %   target_angle : 実際に移動する角度 [度]
    %   ref_angle    : ファイル名に使う参照迎角（常に正の整数）
    %   suffix       : 0=0°基準計測, 1=目標迎角計測
    %
    % Pフェーズ: 0° → step° → 0° → 2*step° → 0° → ... → max_angle° → 0°
    % Mフェーズ: 0° → -step° → 0° → -2*step° → 0° → ... → -max_angle° → 0°

    if nargin < 3 || isempty(angle_step)
        angle_step = 1;
    end

    if startsWith(phase, 'P')
        angle_sign = +1;
    else
        angle_sign = -1;
    end

    angles = angle_step:angle_step:max_angle;   % step, 2*step, ..., ≤max_angle
    n_pts  = 1 + numel(angles) * 2;
    pts(n_pts) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    pts(1) = struct('target_angle', 0, 'ref_angle', 0, 'suffix', 0);

    k = 2;
    for a = angles
        pts(k)   = struct('target_angle', angle_sign * a, 'ref_angle', a, 'suffix', 1);
        pts(k+1) = struct('target_angle', 0,              'ref_angle', a, 'suffix', 0);
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
        T = str2double(input('気温 [℃]: ', 's'));
        if ~isnan(T) && T > -20 && T < 50, break; end
        fprintf('  ※ 有効な気温を入力してください（-20 ～ 50 ℃）\n');
    end

    while true
        P = str2double(input('気圧 [mmHg]: ', 's'));
        if ~isnan(P) && P > 700 && P < 820, break; end
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
    % 計測時の原点パルス（過去実験の再処理時も正しい推奨原点を計算するため）
    if isfield(met, 'origin_pulse') && ~isempty(met.origin_pulse)
        log.origin_pulse = met.origin_pulse;
    end

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

function offset_mV = measure_volt_offset_checked_(s_volt)
    % デジボル電圧オフセットを計測する。平均絶対値が大きい場合（通風中の値が
    % 入った疑い）は、ブロワー停止を再確認して再計測できるようにする。
    %  連続実験で前回の通風が残ったままオフセットを取ってしまう事故への保険。
    OFFSET_ABS_LIMIT = 10;   % |オフセット| がこの mV 以上なら再計測を促す
    while true
        offset_mV = measure_volt_offset_(s_volt);
        if isnan(offset_mV)
            return;   % 計測失敗 → 呼び出し側で config 値にフォールバック
        end
        if abs(offset_mV) < OFFSET_ABS_LIMIT
            return;   % 妥当な範囲
        end
        fprintf('[警告] オフセットの平均絶対値が大きいです: %.2f mV（しきい値 %d mV）。\n', ...
                offset_mV, OFFSET_ABS_LIMIT);
        fprintf('        ブロワーが通風中だと、無風オフセットに通風中の値が入ってしまいます。\n');
        ans_re = strtrim(input('        ブロワー停止を確認して再計測しますか？ [y/n]: ', 's'));
        if ~any(strcmpi(ans_re, {'y', 'yes'}))
            fprintf('        → この値（%.2f mV）をそのまま使用します。\n\n', offset_mV);
            return;
        end
        input('>> ブロワーが停止していることを確認して Enter を押してください: ', 's');
        fprintf('\n');
    end
end

function name = input_experiment_name_(output_dir)
    fprintf('=== 実験フォルダ名の入力 ===\n');
    fprintf('  例: 260604_rigid\n\n');
    forbidden = '/\:*?"<>|';
    while true
        name = strtrim(input('実験フォルダ名: ', 's'));
        if isempty(name) || any(ismember(name, forbidden))
            fprintf('  ※ 有効なフォルダ名を入力してください（空白のみ・特殊文字 %s 不可）\n', forbidden);
            continue;
        end
        % 既存フォルダにデータが残っている場合は確認する。
        % 同名フォルダを再利用すると data/ に前回の CSV が混在し、
        % calc_force の結果が汚染されるため（既定は別名の入力へ戻る）。
        if nargin >= 1
            old_csv = [dir(fullfile(output_dir, name, 'force', 'data', '*.csv')); ...
                       dir(fullfile(output_dir, name, 'data', '*.csv'))];   % 新旧両構成をチェック
            if ~isempty(old_csv)
                fprintf('  ※ フォルダ [%s] には既に計測データがあります（%d ファイル）。\n', ...
                        name, numel(old_csv));
                fprintf('     同じフォルダに追加すると前回のデータと混ざり、結果が汚染されます。\n');
                ans_ow = strtrim(input('     それでもこのフォルダを使いますか？ [y/n]: ', 's'));
                if ~any(strcmpi(ans_ow, {'y', 'yes'}))
                    continue;   % 別名を入力し直す
                end
            end
        end
        break;
    end
    fprintf('→ フォルダ名 [%s] を使用\n\n', name);
end

function [max_angle, angle_step, phase_enabled] = configure_angle_sweep_()
    % 迎角スイープの設定を対話で決める。
    %   max_angle     : 最大迎角 [度]
    %   angle_step    : 刻み幅 [度]
    %   phase_enabled : [Pofst Mofst Pdata Mdata] の論理ベクトル（正負の選択）
    fprintf('\n=== 迎角スイープの設定 ===\n');
    ans_def = strtrim(input('デフォルト（±30°・1°刻み・正負両方）で計測しますか？ [y/n]: ', 's'));
    if isempty(ans_def) || any(strcmpi(ans_def, {'y', 'yes'}))
        max_angle     = 30;
        angle_step    = 1;
        phase_enabled = [true true true true];
        fprintf('→ ±30°・1°刻み・正負両方で計測します。\n\n');
        return
    end

    % --- (1) 正負の選択 ---
    fprintf('\n計測する迎角の符号を選んでください:\n');
    fprintf('  1: 正負どちらも\n');
    fprintf('  2: 正のみ（0 〜 +max）\n');
    fprintf('  3: 負のみ（0 〜 −max）\n');
    sgn = ask_int_('選択 [1-3]: ', 1, 3);
    switch sgn
        case 1, phase_enabled = [true  true  true  true];   % 全フェーズ
        case 2, phase_enabled = [true  false true  false];  % 正のみ(Pofst,Pdata)
        case 3, phase_enabled = [false true  false true];   % 負のみ(Mofst,Mdata)
    end

    % --- (2) 最大迎角 ---
    max_angle = ask_int_('迎角は最大何度まで計測しますか？ [整数, 1-30]: ', 1, 30);

    % --- (3) 刻み幅 ---
    angle_step = ask_int_('迎角の刻み幅は何度ですか？ [整数, 1-max]: ', 1, max_angle);

    sgn_lbl = {'正負両方', '正のみ', '負のみ'};
    n_each  = numel(angle_step:angle_step:max_angle);
    fprintf('→ %s・最大%d°・%d°刻み（%d 点/フェーズ）で計測します。\n\n', ...
            sgn_lbl{sgn}, max_angle, angle_step, 1 + n_each * 2);
end

function v = ask_int_(prompt, lo, hi)
    % lo〜hi の整数を対話で取得する（範囲外・非整数・空入力は再入力）。
    % 文字列で受けて str2double で数値化するため、誤って文字を打っても
    % input() が評価エラーで落ちず、安全に再入力できる。
    while true
        val = str2double(input(prompt, 's'));
        if ~isnan(val) && val == floor(val) && val >= lo && val <= hi
            v = val;
            return
        end
        fprintf('  ※ %d〜%d の整数を入力してください。\n', lo, hi);
    end
end

function idx = select_start_phase_()
    fprintf('=== 開始フェーズの選択 ===\n');
    fprintf('  通常は 1 を選択。エラー中断後の再開は対象フェーズ番号を入力してください。\n\n');
    fprintf('  1: Pofst（正迎角・無風）\n');
    fprintf('  2: Mofst（負迎角・無風）\n');
    fprintf('  3: Pdata（正迎角・有風）\n');
    fprintf('  4: Mdata（負迎角・有風）\n\n');
    while true
        s_in = strtrim(input('開始フェーズ [1-4, デフォルト=1]: ', 's'));
        if isempty(s_in), idx = 1; break; end
        val = str2double(s_in);
        if ~isnan(val) && ismember(val, 1:4), idx = val; break; end
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
        val = str2double(input('フェーズ番号 [1-4]: ', 's'));
        if ~isnan(val) && ismember(val, 1:4), ph = val; break; end
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

function [take_photos, photo_dir] = setup_photos_(picture_dir)
    % 写真撮影をするか確認し、する場合は picture/photo/ を作る。
    %  通風時（Pdata/Mdata）に各迎角で翼模型を LUMIX DC-G100D で撮影する。
    fprintf('=== 写真撮影の設定 ===\n');
    fprintf('  通風時に各迎角で翼模型を撮影します（LUMIX DC-G100D, Wi-Fi接続）。\n');
    fprintf('  撮影する場合は、PCをカメラのSSID(G100D-xxxxxx)に接続しておいてください。\n');
    ans_p = strtrim(input('写真を撮影しますか？ [y/n]: ', 's'));
    take_photos = any(strcmpi(ans_p, {'y', 'yes'}));
    photo_dir = '';
    if take_photos
        [~, ~] = mkdir(picture_dir);
        photo_dir = fullfile(picture_dir, 'photo');
        [~, ~] = mkdir(photo_dir);   % 実験後にSDカードの写真をコピーする先
        % 撮影中はシャッターのみ（DLNAライブ転送は使わない）。撮影記録とログは
        % picture/ 直下に置く（photo/ はSDから移した写真だけにするため）。
        manifest = fullfile(picture_dir, '_shot_manifest.csv');
        if isfile(manifest), delete(manifest); end   % 古い記録は消して撮り直す
        capture_log = fullfile(picture_dir, '_capture.log');
        if isfile(capture_log), delete(capture_log); end
        fprintf('→ 撮影します。各迎角で3枚ずつシャッターを切り、SDカードに保存します（力計測と並行）。\n');
        fprintf('  （実験後、SDの写真を picture/photo にコピー → run_postprocess で整理・解析）\n');
        fprintf('[準備] 写真フォルダ : %s\n', photo_dir);
        fprintf('[準備] 撮影記録     : %s\n\n', manifest);
    else
        fprintf('→ 撮影しません。\n\n');
    end
end

function tf = is_wind_phase_(phase)
    % 通風フェーズ（有風）かどうか。オフセット（無風）は 'ofst' を含む。
    tf = ~contains(phase, 'ofst');
end

function label = angle_label_(angle)
    % 迎角 → ファイル名ラベル（0deg / p<deg>deg / m<deg>deg）。
    %  画像解析（picture_analysis.py）の命名規則に合わせる。
    if angle == 0
        label = '0deg';
    elseif angle > 0
        label = sprintf('p%ddeg', angle);
    else
        label = sprintf('m%ddeg', abs(angle));
    end
end

function ok = confirm_photo_connection_(python_exe, photo_script)
    % 通風フェーズの撮影前に、カメラ接続を一度だけ確認する。
    %  カメラ画面に出る「接続を許可しますか？」で「はい」を選ぶ必要がある。
    %  接続できたら true、ユーザーが諦めたら false（以降の撮影を無効化）。
    fprintf('=== カメラ接続の確認 ===\n');
    fprintf('  PCがカメラのWi-Fi(SSID: G100D-xxxxxx)に接続されているか確認してください。\n');
    fprintf('  接続確認時、カメラ画面に許可確認が出たら「はい」を選んでください。\n\n');
    while true
        input('>> 準備ができたら Enter を押してカメラ接続を確認します: ', 's');
        cmd = sprintf('"%s" "%s" --check', python_exe, photo_script);
        [st, out] = system(cmd);
        if ~isempty(strtrim(out)), fprintf('%s\n', out); end
        if st == 0
            fprintf('[カメラ] 接続OK。撮影を開始できます。\n\n');
            ok = true;
            return
        end
        fprintf('[カメラ] 接続できませんでした。\n');
        ans_r = strtrim(input('  再試行しますか？ [y/n]（n=今回の実験は撮影しない）: ', 's'));
        if ~any(strcmpi(ans_r, {'y', 'yes'}))
            fprintf('[カメラ] 撮影を無効化して実験を続行します。\n\n');
            ok = false;
            return
        end
    end
end

function proc = start_capture_async_(python_exe, photo_script, photo_dir, label, count, angle, phase)
    % 力計測と並行してカメラ撮影を非同期起動する（java.lang.ProcessBuilder）。
    %  迎角を保持している計測中にバックグラウンドでシャッターを切り、SDに保存する。
    %  各ショットは picture/_shot_manifest.csv に記録。子プロセスの出力は
    %  picture/_capture.log に書き出す（パイプ詰まり防止＋後で確認用。最新の点で上書き）。
    %  起動できなければ [] を返す（撮影は補助なので計測は止めない）。
    %  ※ ProcessBuilder は引数を個別に渡すため、パスに空白があっても安全。
    proc = [];
    picture_dir = fileparts(photo_dir);   % photo/ の親 = picture/
    manifest = fullfile(picture_dir, '_shot_manifest.csv');
    logfile  = fullfile(picture_dir, '_capture.log');
    try
        % 引数は java.lang.String[] を明示的に作って渡す。
        %  ・cellstr/char を ArrayList.add(Object) に渡すと char[] 扱いになり、
        %    ProcessBuilder.start() の toArray(String[]) で ArrayStoreException になる
        %  ・無名関数(@(x)...)経由は一部環境で評価エラーになるため使わない
        argv = { python_exe, photo_script, '--shutter-only', ...
                 '--name', label, '--count', num2str(count), ...
                 '--manifest', manifest, ['--angle=' num2str(angle)], ...
                 '--phase', phase };   % 負角(-5等)は --angle=-5 の1トークンで（argparse対策）
        jargs = javaArray('java.lang.String', numel(argv));
        for ai = 1:numel(argv)
            jargs(ai) = java.lang.String(argv{ai});
        end
        pb = java.lang.ProcessBuilder(jargs);
        pb.redirectErrorStream(true);
        pb.redirectOutput(java.io.File(logfile));   % File オーバーロード（入れ子クラス未使用）
        proc = pb.start();
        fprintf('[撮影] %s を %d 枚 バックグラウンド撮影（力計測と並行）...\n', label, count);
    catch ME
        fprintf('[撮影][警告] 撮影プロセスを起動できませんでした: %s\n', ME.message);
        proc = [];
    end
end

function n_reported = announce_new_shots_(manifest, n_reported)
    % manifest に新しく記録された撮影を端末に出す（撮れているか即わかるように）。
    %  _append_manifest は1ショットごとに追記してクローズするため読み取り競合しにくい。
    %  n_reported に inf を渡すと「印字せず現在の記録数だけ返す」（撮影開始時の同期用）。
    if isempty(manifest) || ~isfile(manifest)
        if isinf(n_reported), n_reported = 0; end
        return;
    end
    fid = fopen(manifest, 'r');
    if fid < 0, return; end
    rows = {};
    fgetl(fid);   % ヘッダを読み飛ばす
    while true
        line = fgetl(fid);
        if ~ischar(line), break; end
        if ~isempty(strtrim(line)), rows{end+1} = line; end %#ok<AGROW>
    end
    fclose(fid);
    if ~isinf(n_reported)
        for i = n_reported+1 : numel(rows)
            parts = strsplit(rows{i}, ',', 'CollapseDelimiters', false);
            if numel(parts) >= 7
                if strcmp(strtrim(parts{7}), '1')
                    status = 'OK（SDに保存）';
                else
                    status = '失敗（記録のみ）';
                end
                fprintf('\n  [撮影中] %s %s枚目 ... %s\n', parts{2}, parts{3}, status);
            end
        end
    end
    n_reported = numel(rows);
end

function wait_capture_async_(proc, label, timeout_sec)
    % start_capture_async_ で起動した撮影プロセスの完了を最大 timeout_sec 待つ。
    %  完了すれば終了コードで成否を表示。タイムアウト時は破棄して計測を続行する
    %  （カメラ不調でも力計測＝本命を止めないため）。詳細は _capture.log。
    if isempty(proc), return; end
    t0 = tic;
    while toc(t0) < timeout_sec
        try
            ev = proc.exitValue();   % 未終了なら例外、終了済みなら終了コード
            if ev == 0
                fprintf('[撮影] %s 完了（SDに保存）。\n\n', label);
            else
                fprintf('[撮影][警告] %s に失敗がありました（記録済み・続行）。\n\n', label);
            end
            return;
        catch
            pause(0.2);   % まだ実行中
        end
    end
    try; proc.destroy(); catch; end
    fprintf('[撮影][警告] %s の撮影がタイムアウト（%.0f秒）。計測は続行します。\n\n', label, timeout_sec);
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

function delete_phase_data_(data_dir, phase)
    % フェーズ開始前に、そのフェーズの既存データCSVを data/ から削除する。
    % エラー/停止からの再入場・同フォルダ再計測で古いCSVが残っていると、
    % calc_force が同一計測点を二重に読んで結果が汚染されるため。
    %
    % 日付プレフィックスではなくフェーズ名トークン（例: '_Pofst_'）で照合する:
    %   ・日付で照合すると、フォルダを別日に再利用した場合に取りこぼす
    %   ・Windows の複数ワイルドカード dir の挙動にも依存しない
    % 6軸CSV・volt_raw CSV はどちらも '_<phase>_' を名前に含む。
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

function clear_experiment_data_(exp_dir, photo_dir)
    % 「最初からやり直す」時に、この実験フォルダの計測データを一掃する。
    % P+M で計測後に P のみでやり直す等、構成変更で計測しないフェーズの旧データ
    % （例: Mofst / Mdata）が残ると後処理が汚染されるため、フェーズ単位ではなく全消し。
    % ※ 気象条件の experiment_log.json（実験フォルダ直下）は消さない。
    n = 0;
    % 力の生データ（force/data の全CSV: 6軸・volt_raw・volt_summary）
    n = n + delete_files_in_(fullfile(exp_dir, 'force', 'data'), {'*.csv'});
    % 力の解析結果（再計算で作り直されるが、古い結果が紛れないよう消す）
    n = n + delete_files_in_(fullfile(exp_dir, 'force', 'analysis'), ...
                             {'*.csv', '*.png', '*.json'});
    % 写真の撮影記録（撮り直しで前回分が混ざらないように）。picture/ 直下に置く。
    if nargin >= 2 && ~isempty(photo_dir)
        picture_dir = fileparts(photo_dir);   % photo/ の親 = picture/
        for f = {'_shot_manifest.csv', '_capture.log'}
            p = fullfile(picture_dir, f{1});
            if isfile(p)
                try; delete(p); n = n + 1; catch; end
            end
        end
    end
    fprintf('[初期化] この実験フォルダの計測データを %d ファイル削除しました。\n\n', n);
end

function n = delete_files_in_(folder, patterns)
    % folder 内で patterns に一致するファイルを削除し、削除数を返す。
    n = 0;
    if ~isfolder(folder), return; end
    for k = 1:numel(patterns)
        files = dir(fullfile(folder, patterns{k}));
        for i = 1:numel(files)
            if ~files(i).isdir
                try
                    delete(fullfile(files(i).folder, files(i).name));
                    n = n + 1;
                catch
                    fprintf('[警告] 削除できませんでした: %s\n', files(i).name);
                end
            end
        end
    end
end

function delete_point_files_(varargin)
    % 失敗した計測点の部分ファイルを削除する（リトライ/スキップ時の重複防止）。
    % ファイル名確定前にエラーが起きた場合はパスが空文字なので何もしない。
    for i = 1:nargin
        p = varargin{i};
        if ~isempty(p) && isfile(p)
            try
                delete(p);
                [~, n, e] = fileparts(p);
                fprintf('[削除] 失敗点の部分ファイル: %s%s\n', n, e);
            catch
                fprintf('[警告] 部分ファイルを削除できませんでした: %s\n', p);
            end
        end
    end
end

function run_postprocess_if_ready_(exp_dir, date_str, cfg, phase_enabled)
    % 計測したフェーズの volt_summary が揃っていれば後処理を実行する。
    % 片側のみの計測（正のみ／負のみ）では、計測していないフェーズは要求しない。
    % 後処理の本体はルートの run_postprocess.m（単体での再実行も可能）。
    phases = {'Pofst', 'Mofst', 'Pdata', 'Mdata'};
    for i = 1:numel(phases)
        if nargin >= 4 && ~phase_enabled(i)
            continue;   % このフェーズは計測対象外 → 要求しない
        end
        fname = make_filename(date_str, '', phases{i}, 0, 0, 'volt_summary');
        if ~isfile(fullfile(exp_dir, 'force', 'data', fname))
            fprintf('[後処理] %s がまだありません → 後処理をスキップ\n', fname);
            fprintf('         後で再実行する場合: run_postprocess(''%s'')\n\n', exp_dir);
            return
        end
    end

    fprintf('\n[後処理] 全フェーズのデータが揃いました。後処理を開始します...\n\n');
    run_postprocess(exp_dir, cfg);
end
