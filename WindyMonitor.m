classdef WindyMonitor < handle
% WindyMonitor  風洞実験リアルタイムモニタリング表示クラス
%
% MATLAB Figure ウィンドウに計測状況をリアルタイム表示する。
% drawnow limitrate を用いて計測ループのブロッキングを最小化する。
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
%   monitor.setPhase('Pofst');
%   monitor.resetGraph();   ← 各計測点の開始前
%   monitor.update(angle, logger.getLatest(), v_mv, progress);   ← ループ内
%   monitor.close();        ← 終了時

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

        % データバッファ（計測点ごとにリセット）
        buf_n_          % 6軸サンプル数
        buf_nv_         % 電圧サンプル数
        buf_F_          % M×6  [Fx Fy Fz Mx My Mz]（M は事前確保サイズ）
        buf_v_          % 1×M  差圧電圧 [mV]

        % 定数
        sizeLimitKB_
        BAR_X0          % バー左端（axes 正規化座標）
        BAR_X1          % バー右端
        BAR_Y0          % バー下端
        BAR_Y1          % バー上端
    end

    methods

        % ============================================================
        %  コンストラクタ
        % ============================================================
        function obj = WindyMonitor(sizeLimitKB)
            if nargin < 1, sizeLimitKB = 1000; end
            obj.sizeLimitKB_ = sizeLimitKB;

            % バッファ初期確保（~600サンプル/計測点で十分）
            obj.buf_n_  = 0;
            obj.buf_nv_ = 0;
            obj.buf_F_  = zeros(600, 6);
            obj.buf_v_  = zeros(1, 600);

            % バー座標（ax_btm_ の正規化座標系）
            obj.BAR_X0 = 0.02;
            obj.BAR_X1 = 0.98;
            obj.BAR_Y0 = 0.03;
            obj.BAR_Y1 = 0.42;

            obj.init_figure_();
        end

        % ============================================================
        %  全パネル一括更新（計測ループ内から呼ぶ）
        % ============================================================
        function update(obj, angle, sensorData, voltage, progress)
            % update(angle, sensorData, voltage, progress)
            %
            %   angle      : 現在の迎角 [度]（整数）
            %   sensorData : 1×6  [Fx Fy Fz Mx My Mz]  (getLatest() の戻り値)
            %   voltage    : スカラー  最新差圧電圧 [mV]
            %   progress   : struct(idx, total, size_kb, limit_kb)

            if ~obj.is_open_(), return; end

            % ---- ヘッダ ----
            set(obj.txt_angle_,    'String', sprintf('%+d°', angle));
            set(obj.txt_progress_, 'String', ...
                sprintf('%d / %d  点', progress.idx, progress.total));

            % ---- 6軸グラフ ----
            if numel(sensorData) == 6 && any(sensorData ~= 0)
                obj.buf_n_ = obj.buf_n_ + 1;
                n = obj.buf_n_;
                if n > size(obj.buf_F_, 1)
                    obj.buf_F_ = [obj.buf_F_; zeros(300, 6)];
                end
                obj.buf_F_(n, :) = sensorData(:)';
                x = 1:n;
                set(obj.ln_Fx_, 'XData', x, 'YData', obj.buf_F_(1:n, 1));
                set(obj.ln_Fy_, 'XData', x, 'YData', obj.buf_F_(1:n, 2));
                set(obj.ln_Fz_, 'XData', x, 'YData', obj.buf_F_(1:n, 3));
                set(obj.ln_Mx_, 'XData', x, 'YData', obj.buf_F_(1:n, 4));
                set(obj.ln_My_, 'XData', x, 'YData', obj.buf_F_(1:n, 5));
                set(obj.ln_Mz_, 'XData', x, 'YData', obj.buf_F_(1:n, 6));
            end

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

            obj.buf_n_  = 0;
            obj.buf_nv_ = 0;
            obj.buf_F_(:, :) = 0;
            obj.buf_v_(:)    = 0;

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

            % ---- Figure ----
            obj.fig_ = figure( ...
                'Name',        'Windy — 風洞実験モニタ', ...
                'NumberTitle', 'off', ...
                'Position',    [60, 60, 1040, 640], ...
                'Color',       FIG_BG, ...
                'MenuBar',     'none', ...
                'ToolBar',     'none', ...
                'Resize',      'off', ...
                'CloseRequestFcn', @(~,~) obj.close());

            % ---- ヘッダ（青帯）----
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

            obj.txt_progress_ = text(obj.ax_hdr_, 0.98, 0.50, '0 / 61  点', ...
                'FontSize', 14, 'Color', TXT_WHT, ...
                'HorizontalAlignment', 'right', 'VerticalAlignment', 'middle');

            % ---- 6軸グラフ 左（Fx/Fy/Fz）----
            obj.ax_force_ = axes('Parent', obj.fig_, ...
                'Position', [0.05, 0.37, 0.42, 0.47], 'Color', 'white');
            hold(obj.ax_force_, 'on');
            grid(obj.ax_force_, 'on');
            obj.ln_Fx_ = line(obj.ax_force_, NaN, NaN, ...
                'Color', [0.85 0.33 0.10], 'LineWidth', 1.4, 'DisplayName', 'Fx');
            obj.ln_Fy_ = line(obj.ax_force_, NaN, NaN, ...
                'Color', [0.47 0.67 0.19], 'LineWidth', 1.4, 'DisplayName', 'Fy');
            obj.ln_Fz_ = line(obj.ax_force_, NaN, NaN, ...
                'Color', [0.00 0.45 0.74], 'LineWidth', 1.4, 'DisplayName', 'Fz');
            legend(obj.ax_force_, 'Location', 'northwest', 'FontSize', 9);
            xlabel(obj.ax_force_, 'サンプル番号', 'FontSize', 9);
            ylabel(obj.ax_force_, '[N]', 'FontSize', 10);
            title(obj.ax_force_,  'Fx  /  Fy  /  Fz', 'FontSize', 11);

            % ---- 6軸グラフ 右（Mx/My/Mz）----
            obj.ax_moment_ = axes('Parent', obj.fig_, ...
                'Position', [0.55, 0.37, 0.42, 0.47], 'Color', 'white');
            hold(obj.ax_moment_, 'on');
            grid(obj.ax_moment_, 'on');
            obj.ln_Mx_ = line(obj.ax_moment_, NaN, NaN, ...
                'Color', [0.85 0.33 0.10], 'LineWidth', 1.4, 'DisplayName', 'Mx');
            obj.ln_My_ = line(obj.ax_moment_, NaN, NaN, ...
                'Color', [0.47 0.67 0.19], 'LineWidth', 1.4, 'DisplayName', 'My');
            obj.ln_Mz_ = line(obj.ax_moment_, NaN, NaN, ...
                'Color', [0.00 0.45 0.74], 'LineWidth', 1.4, 'DisplayName', 'Mz');
            legend(obj.ax_moment_, 'Location', 'northwest', 'FontSize', 9);
            xlabel(obj.ax_moment_, 'サンプル番号', 'FontSize', 9);
            ylabel(obj.ax_moment_, '[Nm]', 'FontSize', 10);
            title(obj.ax_moment_,  'Mx  /  My  /  Mz', 'FontSize', 11);

            % ---- 下部パネル（デジボル + サイズバー）----
            obj.ax_btm_ = axes('Parent', obj.fig_, ...
                'Position', [0.04, 0.03, 0.92, 0.29], ...
                'Color',  FIG_BG, ...
                'XColor', 'none', 'YColor', 'none', ...
                'XLim', [0 1], 'YLim', [0 1], ...
                'XTick', [], 'YTick', [], 'Box', 'off');
            hold(obj.ax_btm_, 'on');

            % デジボルテキスト
            obj.txt_volt_cur_ = text(obj.ax_btm_, 0.02, 0.88, '現在値   --- mV', ...
                'FontSize', 14, 'FontWeight', 'bold', 'Color', VOLT_CLR, ...
                'VerticalAlignment', 'middle');
            obj.txt_volt_avg_ = text(obj.ax_btm_, 0.02, 0.68, '平均値   --- mV', ...
                'FontSize', 13, 'Color', VOLT_CLR, ...
                'VerticalAlignment', 'middle');

            % ファイルサイズラベル
            obj.txt_size_ = text(obj.ax_btm_, 0.02, 0.51, ...
                sprintf('6軸センサ   0 KB / %.0f KB', obj.sizeLimitKB_), ...
                'FontSize', 11, 'Color', [0.35 0.35 0.35], ...
                'VerticalAlignment', 'middle');

            % バー背景
            patch(obj.ax_btm_, ...
                [obj.BAR_X0, obj.BAR_X1, obj.BAR_X1, obj.BAR_X0], ...
                [obj.BAR_Y0, obj.BAR_Y0, obj.BAR_Y1, obj.BAR_Y1], ...
                BAR_BG, 'EdgeColor', 'none');

            % バー塗り（初期幅ゼロ）
            obj.patch_fill_ = patch(obj.ax_btm_, ...
                repmat(obj.BAR_X0, 1, 4), ...
                [obj.BAR_Y0, obj.BAR_Y0, obj.BAR_Y1, obj.BAR_Y1], ...
                BAR_FG, 'EdgeColor', 'none');

            drawnow
        end

        function ok = is_open_(obj)
            ok = ~isempty(obj.fig_) && ishandle(obj.fig_) && isvalid(obj.fig_);
        end

    end % methods (private)

end % classdef
