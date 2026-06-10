classdef WindyMonitor < handle
% WindyMonitor  風洞実験リアルタイムモニタリング表示クラス
%
% MATLAB Figure ウィンドウに計測状況をリアルタイム表示する。
%
% 6軸グラフはメインループとは独立した timer で更新するため、
% デジボルの readline ブロッキングに影響されない。
% グラフは直近 N_DISP_ROWS 行（~0.5 秒分）をローリング表示し、
% 振動波形がオシロスコープのように確認できる。
%
% レイアウト:
%   ┌─────────────────────────────────────────────┐
%   │  [Pofst]     迎角: +12°        23 / 61 点   │  ← ヘッダ
%   ├───────────────────┬─────────────────────────┤
%   │  Fx / Fy / Fz [N] │  Mx / My / Mz [Nm]     │  ← 6軸グラフ
%   ├───────────────────┴─────────────────────────┤
%   │  デジボル: 現在値 -3.82 mV  平均 -3.79 mV   │  ← デジボル
%   │  ██████████░░░░   512 KB / 1000 KB          │  ← サイズバー
%   └─────────────────────────────────────────────┘
%
% 使い方（run_experiment.m から）:
%   monitor = WindyMonitor(cfg.force_sensor_size_limit_kb);
%   monitor.setDataSource(@() logger.getRecentRows(600));  ← 一度だけ設定
%   monitor.setPhase('Pofst');
%   monitor.resetGraph();                  ← 各計測点の開始前
%   monitor.update(angle, v_mv, progress); ← 電圧ループ内（6軸グラフ更新不要）
%   monitor.close();                       ← 終了時

    properties (Access = private)
        % Figure
        fig_

        % ヘッダパネル
        ax_hdr_
        txt_phase_
        txt_angle_
        txt_progress_

        % 6軸グラフ
        ax_force_
        ax_moment_
        ln_Fx_, ln_Fy_, ln_Fz_
        ln_Mx_, ln_My_, ln_Mz_

        % 下部パネル（デジボル + サイズバー）
        ax_btm_
        txt_volt_cur_
        txt_volt_avg_
        txt_size_
        patch_fill_

        % デジボルバッファ（計測点ごとにリセット）
        buf_nv_         % 電圧サンプル数
        buf_v_          % 1×M  差圧電圧 [mV]

        % 定数
        sizeLimitKB_
        BAR_X0
        BAR_X1
        BAR_Y0
        BAR_Y1

        % 一時停止
        paused_
        pause_action_   % 'resume' | 'stop' | ''
        btn_pause_
        btn_resume_
        btn_stop_

        % 6軸グラフ独立タイマ
        force_logger_fn_    % function handle: @() logger.getRecentRows(N)
        force_timer_        % timer オブジェクト
    end

    % 表示する直近サンプル数（~1200 Hz × 0.5 s）
    properties (Constant, Access = private)
        N_DISP_ROWS = 600
        TIMER_PERIOD = 0.15   % 6軸グラフ更新間隔 [s]（~7 Hz）
    end

    methods

        % ============================================================
        %  コンストラクタ
        % ============================================================
        function obj = WindyMonitor(sizeLimitKB)
            if nargin < 1, sizeLimitKB = 1000; end
            obj.sizeLimitKB_ = sizeLimitKB;

            obj.buf_nv_ = 0;
            obj.buf_v_  = zeros(1, 600);

            obj.BAR_X0 = 0.02;
            obj.BAR_X1 = 0.98;
            obj.BAR_Y0 = 0.03;
            obj.BAR_Y1 = 0.42;

            obj.force_logger_fn_ = [];
            obj.force_timer_     = [];

            obj.init_figure_();
        end

        % ============================================================
        %  6軸データソース登録（計測開始前に一度だけ呼ぶ）
        % ============================================================
        function setDataSource(obj, logger_fn)
            % logger_fn: @() logger.getRecentRows(N) 形式の function handle
            if ~obj.is_open_(), return; end
            obj.force_logger_fn_ = logger_fn;
            if ~isempty(obj.force_timer_) && isvalid(obj.force_timer_)
                if strcmp(obj.force_timer_.Running, 'off')
                    start(obj.force_timer_);
                end
            end
        end

        % ============================================================
        %  電圧・進捗の更新（計測ループ内から呼ぶ）
        %  6軸グラフは timer が独立して更新するためここでは不要
        % ============================================================
        function update(obj, angle, voltage, progress)
            % update(angle, voltage, progress)
            %   angle    : 現在の迎角 [度]
            %   voltage  : 最新差圧電圧 [mV]
            %   progress : struct(idx, total, size_kb, limit_kb)

            if ~obj.is_open_(), return; end

            % ---- ヘッダ ----
            set(obj.txt_angle_,    'String', sprintf('%+d°', angle));
            set(obj.txt_progress_, 'String', ...
                sprintf('%d / %d  点', progress.idx, progress.total));

            % ---- デジボル ----
            if isscalar(voltage) && ~isnan(voltage)
                obj.buf_nv_ = obj.buf_nv_ + 1;
                nv = obj.buf_nv_;
                if nv > numel(obj.buf_v_)
                    obj.buf_v_ = [obj.buf_v_, zeros(1, 300)];
                end
                obj.buf_v_(nv) = voltage;
                avg_v = sum(obj.buf_v_(1:nv)) / nv;
                set(obj.txt_volt_cur_, 'String', sprintf('現在値   %.2f mV', voltage));
                set(obj.txt_volt_avg_, 'String', ...
                    sprintf('平均値   %.2f mV   （%d サンプル）', avg_v, nv));
            end

            % ---- ファイルサイズバー ----
            sz_kb = progress.size_kb;
            frac  = min(sz_kb / max(obj.sizeLimitKB_, 1), 1.0);
            x_r   = obj.BAR_X0 + (obj.BAR_X1 - obj.BAR_X0) * frac;
            set(obj.patch_fill_, 'XData', ...
                [obj.BAR_X0, x_r, x_r, obj.BAR_X0]);
            set(obj.txt_size_, 'String', ...
                sprintf('6軸センサ   %.1f KB / %.0f KB', sz_kb, obj.sizeLimitKB_));

            drawnow limitrate
        end

        % ============================================================
        %  計測点切り替え時のグラフリセット
        % ============================================================
        function resetGraph(obj)
            if ~obj.is_open_(), return; end

            obj.buf_nv_ = 0;
            obj.buf_v_(:) = 0;

            % 6軸グラフをクリア（timer が次のティックで新データを描画する）
            set(obj.ln_Fx_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Fy_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Fz_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Mx_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_My_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Mz_, 'XData', NaN, 'YData', NaN);

            set(obj.txt_volt_cur_, 'String', '現在値   --- mV');
            set(obj.txt_volt_avg_, 'String', '平均値   --- mV');
            set(obj.txt_size_,     'String', ...
                sprintf('6軸センサ   0 KB / %.0f KB', obj.sizeLimitKB_));
            set(obj.patch_fill_,   'XData', repmat(obj.BAR_X0, 1, 4));

            drawnow
        end

        % ============================================================
        %  一時停止フラグ取得
        % ============================================================
        function ok = isPaused(obj)
            if ~obj.is_open_(), ok = false; return; end
            ok = obj.paused_;
        end

        % ============================================================
        %  一時停止後の選択結果（'resume' | 'stop'）
        % ============================================================
        function action = getPauseAction(obj)
            if ~obj.is_open_()
                action = 'resume';
                return;
            end
            action = obj.pause_action_;
        end

        % ============================================================
        %  停止が要求されたか（一時停止の有無に関わらず判定）
        % ============================================================
        function ok = isStopRequested(obj)
            % 停止ボタンは paused_ を false に戻すため、isPaused だけでは
            % 計測中に押された停止を取りこぼす。pause_action_ を直接見る。
            if ~obj.is_open_(), ok = false; return; end
            ok = strcmp(obj.pause_action_, 'stop');
        end

        % ============================================================
        %  一時停止／停止の状態をリセット（実験リスタート時に呼ぶ）
        %  停止後に再開できるよう、フラグと操作ボタン表示を初期状態へ戻す。
        % ============================================================
        function resetControl(obj)
            if ~obj.is_open_(), return; end
            obj.paused_       = false;
            obj.pause_action_ = '';
            if ~isempty(obj.btn_resume_) && isvalid(obj.btn_resume_)
                obj.btn_resume_.Visible = 'off';
            end
            if ~isempty(obj.btn_stop_) && isvalid(obj.btn_stop_)
                obj.btn_stop_.Visible = 'off';
            end
            if ~isempty(obj.btn_pause_) && isvalid(obj.btn_pause_)
                obj.btn_pause_.Visible = 'on';
            end
            drawnow;
        end

        % ============================================================
        %  フェーズ名更新
        % ============================================================
        function setPhase(obj, phase)
            if ~obj.is_open_(), return; end
            set(obj.txt_phase_, 'String', ['フェーズ:  ' phase]);
            drawnow
        end

        % ============================================================
        %  終了
        % ============================================================
        function close(obj)
            % timer を停止・削除してから Figure を閉じる
            if ~isempty(obj.force_timer_) && isvalid(obj.force_timer_)
                stop(obj.force_timer_);
                delete(obj.force_timer_);
            end
            obj.force_timer_ = [];
            if ~isempty(obj.fig_) && ishandle(obj.fig_)
                delete(obj.fig_);
            end
            obj.fig_ = [];
        end

        function delete(obj)
            obj.close();
        end

    end % methods (public)

    % ================================================================
    %  プライベートメソッド
    % ================================================================
    methods (Access = private)

        function init_figure_(obj)
            HDR_CLR  = [0.18 0.44 0.70];
            TXT_WHT  = [1.00 1.00 1.00];
            FIG_BG   = [0.96 0.96 0.96];
            BAR_BG   = [0.82 0.82 0.82];
            BAR_FG   = [0.18 0.65 0.18];
            VOLT_CLR = [0.10 0.10 0.60];

            obj.paused_       = false;
            obj.pause_action_ = '';

            obj.fig_ = figure( ...
                'Name',        'Windy — 風洞実験モニタ', ...
                'NumberTitle', 'off', ...
                'Position',    [60, 60, 1040, 640], ...
                'Color',       FIG_BG, ...
                'MenuBar',     'none', ...
                'ToolBar',     'none', ...
                'Resize',      'off', ...
                'CloseRequestFcn', @(~,~) obj.close());

            % ---- 一時停止ボタン（通常時） ----
            obj.btn_pause_ = uicontrol(obj.fig_, ...
                'Style',           'pushbutton', ...
                'String',          '一時停止', ...
                'Units',           'normalized', ...
                'Position',        [0.77, 0.895, 0.20, 0.065], ...
                'FontSize',        10, ...
                'FontWeight',      'bold', ...
                'BackgroundColor', [0.95, 0.75, 0.20], ...
                'ForegroundColor', [0.00, 0.00, 0.00], ...
                'Callback',        @(~,~) obj.on_pause_pressed_());

            % ---- 再開ボタン（一時停止中のみ表示） ----
            obj.btn_resume_ = uicontrol(obj.fig_, ...
                'Style',           'pushbutton', ...
                'String',          '再  開', ...
                'Units',           'normalized', ...
                'Position',        [0.77, 0.895, 0.094, 0.065], ...
                'FontSize',        10, ...
                'FontWeight',      'bold', ...
                'BackgroundColor', [0.13, 0.55, 0.13], ...
                'ForegroundColor', [1.00, 1.00, 1.00], ...
                'Visible',         'off', ...
                'Callback',        @(~,~) obj.on_resume_pressed_());

            % ---- 停止ボタン（一時停止中のみ表示） ----
            obj.btn_stop_ = uicontrol(obj.fig_, ...
                'Style',           'pushbutton', ...
                'String',          '停  止', ...
                'Units',           'normalized', ...
                'Position',        [0.876, 0.895, 0.094, 0.065], ...
                'FontSize',        10, ...
                'FontWeight',      'bold', ...
                'BackgroundColor', [0.75, 0.10, 0.10], ...
                'ForegroundColor', [1.00, 1.00, 1.00], ...
                'Visible',         'off', ...
                'Callback',        @(~,~) obj.on_stop_pressed_());

            % ---- ヘッダ ----
            obj.ax_hdr_ = axes('Parent', obj.fig_, ...
                'Position', [0.00, 0.87, 1.00, 0.13], ...
                'Color',    HDR_CLR, ...
                'XColor',   HDR_CLR, 'YColor', HDR_CLR, ...
                'XLim', [0 1], 'YLim', [0 1], ...
                'XTick', [], 'YTick', []);

            obj.txt_phase_ = text(obj.ax_hdr_, 0.02, 0.50, 'フェーズ:  ---', ...
                'FontSize', 14, 'FontWeight', 'bold', 'Color', TXT_WHT, ...
                'HorizontalAlignment', 'left', 'VerticalAlignment', 'middle');

            obj.txt_angle_ = text(obj.ax_hdr_, 0.50, 0.50, '---°', ...
                'FontSize', 38, 'FontWeight', 'bold', 'Color', TXT_WHT, ...
                'HorizontalAlignment', 'center', 'VerticalAlignment', 'middle');

            obj.txt_progress_ = text(obj.ax_hdr_, 0.71, 0.50, '0 / 61  点', ...
                'FontSize', 14, 'Color', TXT_WHT, ...
                'HorizontalAlignment', 'right', 'VerticalAlignment', 'middle');

            % ---- 6軸グラフ 左（Fx/Fy/Fz）----
            obj.ax_force_ = axes('Parent', obj.fig_, ...
                'Position', [0.05, 0.37, 0.42, 0.47], 'Color', 'white');
            hold(obj.ax_force_, 'on');
            grid(obj.ax_force_, 'on');
            obj.ln_Fx_ = line(obj.ax_force_, NaN, NaN, ...
                'Color', [0.85 0.33 0.10], 'LineWidth', 1.0, 'DisplayName', 'Fx');
            obj.ln_Fy_ = line(obj.ax_force_, NaN, NaN, ...
                'Color', [0.47 0.67 0.19], 'LineWidth', 1.0, 'DisplayName', 'Fy');
            obj.ln_Fz_ = line(obj.ax_force_, NaN, NaN, ...
                'Color', [0.00 0.45 0.74], 'LineWidth', 1.0, 'DisplayName', 'Fz');
            legend(obj.ax_force_, 'Location', 'northwest', 'FontSize', 9);
            xlabel(obj.ax_force_, '時間 [s]', 'FontSize', 9);
            ylabel(obj.ax_force_, '[N]', 'FontSize', 10);
            title(obj.ax_force_,  'Fx  /  Fy  /  Fz', 'FontSize', 11);

            % ---- 6軸グラフ 右（Mx/My/Mz）----
            obj.ax_moment_ = axes('Parent', obj.fig_, ...
                'Position', [0.55, 0.37, 0.42, 0.47], 'Color', 'white');
            hold(obj.ax_moment_, 'on');
            grid(obj.ax_moment_, 'on');
            obj.ln_Mx_ = line(obj.ax_moment_, NaN, NaN, ...
                'Color', [0.85 0.33 0.10], 'LineWidth', 1.0, 'DisplayName', 'Mx');
            obj.ln_My_ = line(obj.ax_moment_, NaN, NaN, ...
                'Color', [0.47 0.67 0.19], 'LineWidth', 1.0, 'DisplayName', 'My');
            obj.ln_Mz_ = line(obj.ax_moment_, NaN, NaN, ...
                'Color', [0.00 0.45 0.74], 'LineWidth', 1.0, 'DisplayName', 'Mz');
            legend(obj.ax_moment_, 'Location', 'northwest', 'FontSize', 9);
            xlabel(obj.ax_moment_, '時間 [s]', 'FontSize', 9);
            ylabel(obj.ax_moment_, '[Nm]', 'FontSize', 10);
            title(obj.ax_moment_,  'Mx  /  My  /  Mz', 'FontSize', 11);

            % ---- 下部パネル ----
            obj.ax_btm_ = axes('Parent', obj.fig_, ...
                'Position', [0.04, 0.03, 0.92, 0.29], ...
                'Color',  FIG_BG, ...
                'XColor', 'none', 'YColor', 'none', ...
                'XLim', [0 1], 'YLim', [0 1], ...
                'XTick', [], 'YTick', [], 'Box', 'off');
            hold(obj.ax_btm_, 'on');

            obj.txt_volt_cur_ = text(obj.ax_btm_, 0.02, 0.88, '現在値   --- mV', ...
                'FontSize', 14, 'FontWeight', 'bold', 'Color', VOLT_CLR, ...
                'VerticalAlignment', 'middle');
            obj.txt_volt_avg_ = text(obj.ax_btm_, 0.02, 0.68, '平均値   --- mV', ...
                'FontSize', 13, 'Color', VOLT_CLR, ...
                'VerticalAlignment', 'middle');

            obj.txt_size_ = text(obj.ax_btm_, 0.02, 0.51, ...
                sprintf('6軸センサ   0 KB / %.0f KB', obj.sizeLimitKB_), ...
                'FontSize', 11, 'Color', [0.35 0.35 0.35], ...
                'VerticalAlignment', 'middle');

            patch(obj.ax_btm_, ...
                [obj.BAR_X0, obj.BAR_X1, obj.BAR_X1, obj.BAR_X0], ...
                [obj.BAR_Y0, obj.BAR_Y0, obj.BAR_Y1, obj.BAR_Y1], ...
                BAR_BG, 'EdgeColor', 'none');

            obj.patch_fill_ = patch(obj.ax_btm_, ...
                repmat(obj.BAR_X0, 1, 4), ...
                [obj.BAR_Y0, obj.BAR_Y0, obj.BAR_Y1, obj.BAR_Y1], ...
                BAR_FG, 'EdgeColor', 'none');

            % ---- 6軸グラフ独立タイマ（setDataSource で start する）----
            obj.force_timer_ = timer( ...
                'ExecutionMode', 'fixedSpacing', ...
                'Period',         obj.TIMER_PERIOD, ...
                'TimerFcn',      @(~,~) obj.refresh_force_());

            drawnow
        end

        function ok = is_open_(obj)
            ok = ~isempty(obj.fig_) && ishandle(obj.fig_) && isvalid(obj.fig_);
        end

        % ============================================================
        %  タイマコールバック: 6軸グラフをローリング更新
        % ============================================================
        function refresh_force_(obj)
            if ~obj.is_open_() || isempty(obj.force_logger_fn_), return; end
            try
                rows = obj.force_logger_fn_();   % n×7: [t Fx Fy Fz Mx My Mz]
                n = size(rows, 1);
                if n < 2, return; end

                t = rows(:, 1) - rows(1, 1);    % 先頭を 0 に正規化
                set(obj.ln_Fx_, 'XData', t, 'YData', rows(:, 2));
                set(obj.ln_Fy_, 'XData', t, 'YData', rows(:, 3));
                set(obj.ln_Fz_, 'XData', t, 'YData', rows(:, 4));
                set(obj.ln_Mx_, 'XData', t, 'YData', rows(:, 5));
                set(obj.ln_My_, 'XData', t, 'YData', rows(:, 6));
                set(obj.ln_Mz_, 'XData', t, 'YData', rows(:, 7));

                drawnow limitrate
            catch
                % 描画エラーは無視（計測ループを止めない）
            end
        end

        % ============================================================
        %  ボタンコールバック
        % ============================================================
        function on_pause_pressed_(obj)
            obj.paused_                  = true;
            obj.pause_action_            = '';
            obj.btn_pause_.Visible       = 'off';
            obj.btn_resume_.Visible      = 'on';
            obj.btn_stop_.Visible        = 'on';
            drawnow
        end

        function on_resume_pressed_(obj)
            obj.pause_action_            = 'resume';
            obj.paused_                  = false;
            obj.btn_resume_.Visible      = 'off';
            obj.btn_stop_.Visible        = 'off';
            obj.btn_pause_.Visible       = 'on';
            drawnow
        end

        function on_stop_pressed_(obj)
            obj.pause_action_            = 'stop';
            obj.paused_                  = false;
            obj.btn_resume_.Visible      = 'off';
            obj.btn_stop_.Visible        = 'off';
            obj.btn_pause_.Visible       = 'on';
            drawnow
        end

    end % methods (private)

end % classdef
