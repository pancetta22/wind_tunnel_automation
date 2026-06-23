classdef FlutterWindyMonitor < handle
% FlutterWindyMonitor  フラッター実験用リアルタイムモニタリング表示クラス
%
% WindyMonitor との主な違い:
%   - プログレスバーが「秒数ベース」（Pdata/Mdata）と「KBベース」（Pofst/Mofst）を
%     自動的に切り替え（prog 構造体のフィールドで判断）
%   - ヘッダに「条件名（c01 など）」を表示
%   - プログレスバーの表示が「n.n s / 30.0 s」または「n KB / 1000 KB」
%
% レイアウト:
%   ┌──────────────────────────────────────────────────────────┐
%   │  [c01 / Pdata]     迎角: +12°          23 / 31 点       │
%   ├───────────────────┬──────────────────────────────────────┤
%   │  Fx / Fy / Fz [N] │  Mx / My / Mz [Nm]                 │
%   ├───────────────────┴──────────────────────────────────────┤
%   │  デジボル: 現在値 -3.82 mV  平均 -3.79 mV               │
%   │  ██████████░░░░   18.3 s / 30.0 s                       │
%   └──────────────────────────────────────────────────────────┘
%
% 使い方（flutter_run_experiment.m から）:
%   monitor = FlutterWindyMonitor(cfg.force_sensor_size_limit_kb);
%   monitor.setDataSource(@() logger.getRecentRows(600));
%   monitor.setConditionLabel('c01');     ← 条件名を設定
%   monitor.setTimeLimitSec(30.0);        ← Pdata/Mdata 用（秒数表示に切り替え）
%   monitor.setPhase('Pdata');
%   monitor.resetGraph();
%   monitor.update(angle, v_mv, prog);
%     prog は以下のどちらかの形式:
%       秒数ベース: struct('idx',i,'total',n,'elapsed_sec',t,'limit_sec',T)
%       KBベース  : struct('idx',i,'total',n,'size_kb',s,'limit_kb',L)
%   monitor.close();

    properties (Access = private)
        fig_

        % ヘッダパネル
        ax_hdr_
        txt_condition_   % 条件名（c01 / Pdata など）
        txt_angle_
        txt_progress_

        % 6軸グラフ
        ax_force_
        ax_moment_
        ln_Fx_, ln_Fy_, ln_Fz_
        ln_Mx_, ln_My_, ln_Mz_

        % 下部パネル
        ax_btm_
        txt_volt_cur_
        txt_volt_avg_
        txt_size_
        patch_fill_

        % デジボルバッファ
        buf_nv_
        buf_v_

        % 定数
        sizeLimitKB_
        BAR_X0
        BAR_X1
        BAR_Y0
        BAR_Y1

        % 状態
        conditionLabel_   % 'c01', 'c02', 'ofst' など
        currentPhase_     % 'Pofst', 'Mofst', 'Pdata', 'Mdata'
        timeLimitSec_     % 秒数制限（Pdata/Mdata）。[]の場合はKBベース表示

        % 6軸グラフ独立タイマ
        force_logger_fn_
        force_timer_
    end

    properties (Constant, Access = private)
        N_DISP_ROWS = 600
        TIMER_PERIOD = 0.15
    end

    methods

        function obj = FlutterWindyMonitor(sizeLimitKB)
            if nargin < 1, sizeLimitKB = 1000; end
            obj.sizeLimitKB_    = sizeLimitKB;
            obj.buf_nv_         = 0;
            obj.buf_v_          = zeros(1, 600);
            obj.BAR_X0          = 0.02;
            obj.BAR_X1          = 0.98;
            obj.BAR_Y0          = 0.03;
            obj.BAR_Y1          = 0.42;
            obj.force_logger_fn_ = [];
            obj.force_timer_     = [];
            obj.conditionLabel_ = '---';
            obj.currentPhase_   = '---';
            obj.timeLimitSec_   = [];
            obj.init_figure_();
        end

        % ============================================================
        %  条件名・秒数制限の設定
        % ============================================================
        function setConditionLabel(obj, label)
            % 'c01', 'c02', 'ofst' など
            obj.conditionLabel_ = label;
            obj.update_header_text_();
        end

        function setTimeLimitSec(obj, sec)
            % Pdata/Mdata 用: 秒数ベースのプログレスバーに切り替える
            % Pofst/Mofst（KBベース）に戻したい場合は [] を渡す
            obj.timeLimitSec_ = sec;
        end

        % ============================================================
        %  6軸データソース登録
        % ============================================================
        function setDataSource(obj, logger_fn)
            if ~obj.is_open_(), return; end
            obj.force_logger_fn_ = logger_fn;
            if ~isempty(obj.force_timer_) && isvalid(obj.force_timer_)
                if strcmp(obj.force_timer_.Running, 'off')
                    start(obj.force_timer_);
                end
            end
        end

        % ============================================================
        %  電圧・進捗の更新
        %
        %  prog 構造体の形式（どちらかを渡す）:
        %    秒数ベース: struct('idx',i,'total',n,'elapsed_sec',t,'limit_sec',T)
        %    KBベース  : struct('idx',i,'total',n,'size_kb',s,'limit_kb',L)
        % ============================================================
        function update(obj, angle, voltage, prog)
            if ~obj.is_open_(), return; end

            % ---- ヘッダ ----
            set(obj.txt_angle_,    'String', sprintf('%+d°', angle));
            set(obj.txt_progress_, 'String', ...
                sprintf('%d / %d  点', prog.idx, prog.total));

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

            % ---- プログレスバー（秒数 or KB を自動判定）----
            if isfield(prog, 'elapsed_sec') && isfield(prog, 'limit_sec')
                % 秒数ベース（Pdata/Mdata）
                elapsed   = prog.elapsed_sec;
                limit_sec = prog.limit_sec;
                frac      = min(elapsed / max(limit_sec, 1e-6), 1.0);
                bar_label = sprintf('6軸センサ   %.1f s / %.1f s', elapsed, limit_sec);
            else
                % KBベース（Pofst/Mofst・既存動作）
                sz_kb    = prog.size_kb;
                limit_kb = prog.limit_kb;
                frac     = min(sz_kb / max(limit_kb, 1), 1.0);
                bar_label = sprintf('6軸センサ   %.1f KB / %.0f KB', sz_kb, limit_kb);
            end

            x_r = obj.BAR_X0 + (obj.BAR_X1 - obj.BAR_X0) * frac;
            set(obj.patch_fill_, 'XData', [obj.BAR_X0, x_r, x_r, obj.BAR_X0]);
            set(obj.txt_size_, 'String', bar_label);

            drawnow limitrate
        end

        % ============================================================
        %  計測点切り替え時のリセット
        % ============================================================
        function resetGraph(obj)
            if ~obj.is_open_(), return; end

            obj.buf_nv_ = 0;
            obj.buf_v_(:) = 0;

            set(obj.ln_Fx_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Fy_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Fz_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Mx_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_My_, 'XData', NaN, 'YData', NaN);
            set(obj.ln_Mz_, 'XData', NaN, 'YData', NaN);

            set(obj.txt_volt_cur_, 'String', '現在値   --- mV');
            set(obj.txt_volt_avg_, 'String', '平均値   --- mV');
            set(obj.patch_fill_,   'XData', repmat(obj.BAR_X0, 1, 4));

            % バーのラベルを現在のモードに合わせてリセット
            if ~isempty(obj.timeLimitSec_)
                set(obj.txt_size_, 'String', ...
                    sprintf('6軸センサ   0.0 s / %.1f s', obj.timeLimitSec_));
            else
                set(obj.txt_size_, 'String', ...
                    sprintf('6軸センサ   0 KB / %.0f KB', obj.sizeLimitKB_));
            end

            drawnow
        end

        % ============================================================
        %  フェーズ名更新（ヘッダ左に「条件名 / フェーズ名」で表示）
        % ============================================================
        function setPhase(obj, phase)
            if ~obj.is_open_(), return; end
            obj.currentPhase_ = phase;
            obj.update_header_text_();
            drawnow
        end

        % ============================================================
        %  終了
        % ============================================================
        function close(obj)
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

        function update_header_text_(obj)
            % ヘッダ左のテキストを「条件名 / フェーズ名」で更新
            if ~obj.is_open_(), return; end
            label = sprintf('%s  /  %s', obj.conditionLabel_, obj.currentPhase_);
            set(obj.txt_condition_, 'String', label);
        end

        function init_figure_(obj)
            HDR_CLR  = [0.15 0.40 0.25];   % フラッター実験は緑系（定常空力の青系と区別）
            TXT_WHT  = [1.00 1.00 1.00];
            FIG_BG   = [0.96 0.96 0.96];
            BAR_BG   = [0.82 0.82 0.82];
            BAR_FG   = [0.18 0.65 0.18];
            VOLT_CLR = [0.10 0.10 0.60];

            obj.fig_ = figure( ...
                'Name',        'Windy — フラッター実験モニタ', ...
                'NumberTitle', 'off', ...
                'Position',    [60, 60, 1040, 640], ...
                'Color',       FIG_BG, ...
                'MenuBar',     'none', ...
                'ToolBar',     'none', ...
                'Resize',      'off', ...
                'CloseRequestFcn', @(~,~) obj.close());

            % ---- ヘッダ ----
            obj.ax_hdr_ = axes('Parent', obj.fig_, ...
                'Position', [0.00, 0.87, 1.00, 0.13], ...
                'Color',    HDR_CLR, ...
                'XColor',   HDR_CLR, 'YColor', HDR_CLR, ...
                'XLim', [0 1], 'YLim', [0 1], ...
                'XTick', [], 'YTick', []);

            % 左: 条件名 / フェーズ名（例: c01  /  Pdata）
            obj.txt_condition_ = text(obj.ax_hdr_, 0.02, 0.50, '---  /  ---', ...
                'FontSize', 14, 'FontWeight', 'bold', 'Color', TXT_WHT, ...
                'HorizontalAlignment', 'left', 'VerticalAlignment', 'middle');

            % 中央: 迎角
            obj.txt_angle_ = text(obj.ax_hdr_, 0.50, 0.50, '---°', ...
                'FontSize', 38, 'FontWeight', 'bold', 'Color', TXT_WHT, ...
                'HorizontalAlignment', 'center', 'VerticalAlignment', 'middle');

            % 右: 進捗点数
            obj.txt_progress_ = text(obj.ax_hdr_, 0.97, 0.50, '0 / --  点', ...
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

            % ---- 6軸グラフ独立タイマ ----
            obj.force_timer_ = timer( ...
                'ExecutionMode', 'fixedSpacing', ...
                'Period',         obj.TIMER_PERIOD, ...
                'TimerFcn',      @(~,~) obj.refresh_force_());

            drawnow
        end

        function ok = is_open_(obj)
            ok = ~isempty(obj.fig_) && ishandle(obj.fig_) && isvalid(obj.fig_);
        end

        function refresh_force_(obj)
            if ~obj.is_open_() || isempty(obj.force_logger_fn_), return; end
            try
                rows = obj.force_logger_fn_();
                n = size(rows, 1);
                if n < 2, return; end
                t = rows(:, 1) - rows(1, 1);
                set(obj.ln_Fx_, 'XData', t, 'YData', rows(:, 2));
                set(obj.ln_Fy_, 'XData', t, 'YData', rows(:, 3));
                set(obj.ln_Fz_, 'XData', t, 'YData', rows(:, 4));
                set(obj.ln_Mx_, 'XData', t, 'YData', rows(:, 5));
                set(obj.ln_My_, 'XData', t, 'YData', rows(:, 6));
                set(obj.ln_Mz_, 'XData', t, 'YData', rows(:, 7));
                drawnow limitrate
            catch
            end
        end

    end % methods (private)

end % classdef
