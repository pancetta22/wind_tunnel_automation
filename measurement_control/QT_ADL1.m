classdef QT_ADL1 < handle
% QT_ADL1  中央精機 QT-ADL1 コントローラドライバ (RS-232C)
%
% 使い方:
%   stage = QT_ADL1('COM7');          % 接続
%   stage.homeReturn();               % 原点復帰 → 自動で迎角0°へ移動
%   stage.moveToAngle(15.0);          % 迎角15°へ移動
%   stage.moveToAngle(0);             % 迎角0°へ戻る
%   angle = stage.getAngle();         % 現在の迎角取得 [度]
%   stage.stop();                     % 減速停止
%   delete(stage);                    % 切断
%
% 座標系:
%   迎角0° = originPulse (既定 11025 pulse / config.json の origin_pulse で指定)
%   迎角θ° = originPulse - θ×250 pulse  (角度増加 → パルス減少 / CCW方向)
%
% 通信仕様 (工場出荷時デフォルト):
%   ボーレート : 9600 bps
%   データビット: 8 bit
%   パリティ   : なし
%   ストップビット: 1
%   終端文字   : CR+LF
%   フロー制御 : なし

    properties (Access = private)
        port_            % serialport オブジェクト
        pulsePerDeg_     % 1度あたりのパルス数
        originPulse_     % 迎角0°に対応する機械座標 [pulse]（config.json の origin_pulse）
    end

    properties (Constant)
        BAUD_RATE       = 9600;
        TIMEOUT_S       = 30;     % 移動完了待ちタイムアウト [秒]
        POLL_INTERVAL   = 0.05;   % ステータスポーリング間隔 [秒]
        ORIGIN_PULSE_DEFAULT = 11025;  % origin_pulse 未指定時の既定値 [pulse]
        PPD             = 250;    % pulse per degree (ARS-936-HP: 0.004°/pulse)
    end

    %% ======== パブリックメソッド ========
    methods

        % ----- コンストラクタ -----
        function obj = QT_ADL1(comPort, pulsePerDeg, originPulse)
            % QT_ADL1(comPort)
            %   : pulsePerDeg=250, originPulse=11025（既定）
            % QT_ADL1(comPort, pulsePerDeg, originPulse)
            %   : パルス/度・迎角0°の原点パルスを明示指定
            %     （run_experiment は config.json の origin_pulse を渡す）
            %
            % ARS-936-HP の場合:
            %   分解能 0.004°/pulse → pulsePerDeg = 1/0.004 = 250

            if nargin < 2 || isempty(pulsePerDeg)
                pulsePerDeg = obj.PPD;
            end
            if nargin < 3 || isempty(originPulse)
                originPulse = obj.ORIGIN_PULSE_DEFAULT;
            end
            obj.pulsePerDeg_ = pulsePerDeg;
            obj.originPulse_ = originPulse;

            obj.port_ = serialport(comPort, obj.BAUD_RATE, ...
                'DataBits',    8,      ...
                'Parity',      'none', ...
                'StopBits',    1,      ...
                'FlowControl', 'none');

            configureTerminator(obj.port_, 'CR/LF', 'CR/LF');
            obj.port_.Timeout = obj.TIMEOUT_S;

            fprintf('[QT-ADL1] %s に接続しました (%.0f bps)\n', comPort, obj.BAUD_RATE);
            fprintf('[QT-ADL1] 座標系: 迎角0° = %d pulse, %.4f°/pulse\n', ...
                obj.originPulse_, 1/obj.pulsePerDeg_);
        end

        % ----- デストラクタ -----
        function delete(obj)
            if ~isempty(obj.port_) && isvalid(obj.port_)
                delete(obj.port_);
                fprintf('[QT-ADL1] 切断しました\n');
            end
        end

        % ----- 原点復帰 → 迎角0°へ移動 -----
        function homeReturn(obj)
            % H:A で機械原点へ戻したあと、迎角0° (ORIGIN_PULSE) へ移動する
            fprintf('[QT-ADL1] 原点復帰中 (機械原点)...\n');
            obj.send('H:A');
            obj.waitForStop();
            fprintf('[QT-ADL1] 機械原点に到達\n');

            fprintf('[QT-ADL1] 迎角0°へ移動中 (%d pulse)...\n', obj.originPulse_);
            obj.moveAbsolute(obj.originPulse_);
            fprintf('[QT-ADL1] 迎角0°に到達\n');
        end

        % ----- 迎角指定で移動（メイン使用メソッド）-----
        function moveToAngle(obj, angle_deg)
            % 迎角 angle_deg [度] へ絶対移動する
            % 座標変換: pulse = ORIGIN_PULSE - angle_deg × pulsePerDeg
            pulses = obj.originPulse_ - round(angle_deg * obj.pulsePerDeg_);
            fprintf('[QT-ADL1] 迎角移動: %.4f° → %+d pulse\n', angle_deg, pulses);
            obj.moveAbsolute(pulses);
        end

        % ----- 現在の迎角取得 -----
        function angle = getAngle(obj)
            % 戻り値: 迎角 [度]
            pos   = obj.getPosition();
            angle = (obj.originPulse_ - pos) / obj.pulsePerDeg_;
        end

        % ----- 現在位置取得（パルス単位）-----
        function pos = getPosition(obj)
            obj.send('Q:A0');
            resp = obj.recv();
            pos  = obj.parsePosition(resp);
        end

        % ----- 現在のステータス取得 -----
        function [pos, status] = getStatus(obj)
            % 戻り値:
            %   pos    : 現在位置 [pulse]
            %   status : 'D'=移動中 / 'K'=正常停止 / 'L'=リミット停止
            %            'E'=非常停止 / 'H'=原点復帰エラー
            obj.send('Q:A0');
            resp = obj.recv();
            [pos, status] = obj.parseStatusResponse(resp);
        end

        % ----- 絶対移動（パルス単位・内部用だが公開）-----
        function moveAbsolute(obj, pulses)
            cmd = sprintf('AGO:A%d', round(pulses));
            obj.send(cmd);
            obj.waitForStop();
        end

        % ----- 相対移動（角度指定）-----
        function moveByAngle(obj, delta_deg)
            % 現在の迎角から相対的に回転する（+ = 迎角増加方向）
            pulses = -round(delta_deg * obj.pulsePerDeg_); % 符号反転に注意
            fprintf('[QT-ADL1] 相対角度移動: %+.4f°\n', delta_deg);
            cmd = sprintf('MGO:A%d', pulses);
            obj.send(cmd);
            obj.waitForStop();
        end

        % ----- 減速停止 -----
        function stop(obj)
            obj.send('L:A');
            fprintf('[QT-ADL1] 停止コマンド送信\n');
        end

        % ----- 非常停止 -----
        function emergencyStop(obj)
            obj.send('E:');
            fprintf('[QT-ADL1] 非常停止コマンド送信\n');
        end

        % ----- 角度スイープ（迎角実験用）-----
        function sweep(obj, angles_deg, pauseSec, callback)
            % 指定した迎角リストを順番に移動する
            % 各測定点の前後に迎角0°へ戻る（旧プログラムと同じ挙動）
            %
            % 引数:
            %   angles_deg : 迎角ベクトル (例: 0:1:30)
            %   pauseSec   : 各位置での待機時間 [秒] (省略時: 1.0)
            %   callback   : 各位置到達後に実行する関数ハンドル (省略可)
            %                  例: @() disp('計測中')
            %
            % 使用例:
            %   stage.sweep(0:1:30, 2.0, @myMeasurementFunc);

            if nargin < 3, pauseSec = 1.0; end
            if nargin < 4, callback = []; end

            fprintf('[QT-ADL1] スイープ開始: %g° → %g° (%d点)\n', ...
                angles_deg(1), angles_deg(end), numel(angles_deg));

            for i = 1:numel(angles_deg)
                % 迎角0°へ戻る（旧プログラムの挙動を再現）
                obj.moveToAngle(0);

                % 目標迎角へ移動
                obj.moveToAngle(angles_deg(i));
                fprintf('  [%d/%d] 迎角 %.4f° 到達\n', i, numel(angles_deg), angles_deg(i));

                if ~isempty(callback)
                    callback();
                end

                pause(pauseSec);
            end

            % スイープ終了後に迎角0°へ戻る
            obj.moveToAngle(0);
            fprintf('[QT-ADL1] スイープ完了 → 迎角0°\n');
        end

    end % methods (public)

    %% ======== プライベートメソッド ========
    methods (Access = private)

        function send(obj, cmd)
            writeline(obj.port_, cmd);
        end

        function resp = recv(obj)
            try
                resp = char(strtrim(readline(obj.port_)));
            catch
                resp = '';
                warning('[QT-ADL1] レスポンスのタイムアウトまたは受信エラー');
            end
        end

        function waitForStop(obj)
            % status == '?' は通信失敗・パース失敗（recv 空応答など）を示す。
            % これを「移動完了」と誤判定すると、実際は移動中のまま次工程へ
            % 進んでしまう危険があるため、'?' の間はリトライしてタイムアウト
            % まで待つ（'D'=移動中と同様に扱う）。
            startTime = tic;
            while true
                obj.send('Q:A0');
                resp = obj.recv();
                [~, status] = obj.parseStatusResponse(resp);

                if ~strcmp(status, 'D') && ~strcmp(status, '?')
                    if strcmp(status, 'L')
                        warning('[QT-ADL1] リミット検出による停止 (L)');
                    elseif strcmp(status, 'E')
                        warning('[QT-ADL1] 非常停止状態 (E)');
                    elseif strcmp(status, 'H')
                        warning('[QT-ADL1] 原点復帰エラー停止 (H)');
                    end
                    return;
                end

                if toc(startTime) > obj.TIMEOUT_S
                    if strcmp(status, '?')
                        warning('[QT-ADL1] waitForStop: 応答を取得できないままタイムアウトしました (%.0f 秒)', obj.TIMEOUT_S);
                    else
                        warning('[QT-ADL1] waitForStop: タイムアウト (%.0f 秒)', obj.TIMEOUT_S);
                    end
                    return;
                end

                pause(obj.POLL_INTERVAL);
            end
        end

        function pos = parsePosition(obj, resp)
            [pos, ~] = obj.parseStatusResponse(resp);
        end

        function [pos, status] = parseStatusResponse(~, resp)
            pos    = 0;
            status = '?';
            if isempty(resp), return; end

            if startsWith(resp, '!')
                warning('[QT-ADL1] エラーレスポンス: %s', resp);
                return;
            end

            status = resp(end);
            numStr = resp(1:end-1);
            pos    = str2double(numStr);
            if isnan(pos)
                warning('[QT-ADL1] 座標値のパースに失敗: "%s"', resp);
                pos = 0;
            end
        end

    end % methods (private)

end % classdef