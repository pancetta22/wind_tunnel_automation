classdef LeptrinoLogger < handle
% LeptrinoLogger  Leptrino 6軸センサ 時系列ロガークラス
%
% leptrino_server.py を --mode stream で起動し、バックグラウンドで CSV を書き出す。
% Parallel Computing Toolbox 不要 — Java ProcessBuilder でバックグラウンド実行。
% MATLAB 側はファイルサイズを監視して計測完了を検知し、その間デジボル計測も行う。
%
% 使い方:
%   logger = LeptrinoLogger(pythonExe, scriptPath, port, sizeLimitKB);
%
%   logger.start(filepath);          % 計測開始（バックグラウンド）
%   pause(0.5);                      % Python 起動待ち
%   while ~logger.isDone()
%       % デジボル計測など他の処理をここで実行
%       fprintf('  6軸: %.1f KB\n', logger.getSizeKB());
%   end
%   logger.waitForFinish();          % ファイル書き出し完全終了を待つ
%   result = logger.getResult();     % {"status":"ok","samples":N,"size_kb":X}
%
%   data = logger.getLatest();       % 最新サンプル [Fx Fy Fz Mx My Mz]（モニタ用）
%   logger.stop();                   % 異常時の強制終了
%   delete(logger);                  % デストラクタ（stop を自動呼び出し）

    properties (Access = private)
        jProc_            % java.lang.Process  バックグラウンドプロセス
        filepath_         % string  出力 CSV パス
        sizeLimitBytes_   % double  ファイルサイズ閾値 [bytes]
        pythonExe_        % string  python.exe のパス
        scriptPath_       % string  leptrino_server.py のパス
        port_             % int     COMポート番号
    end

    methods

        % ================================================================
        %  コンストラクタ
        % ================================================================
        function obj = LeptrinoLogger(pythonExe, scriptPath, port, sizeLimitKB)
            % LeptrinoLogger(pythonExe, scriptPath, port, sizeLimitKB)
            %   pythonExe   : python.exe のフルパス（config.python_exe）
            %   scriptPath  : leptrino_server.py のフルパス
            %   port        : Leptrino の COM ポート番号（config.leptrino_port）
            %   sizeLimitKB : 計測終了のファイルサイズ閾値 [KB]
            obj.pythonExe_      = pythonExe;
            obj.scriptPath_     = scriptPath;
            obj.port_           = port;
            obj.sizeLimitBytes_ = sizeLimitKB * 1024;
            obj.jProc_          = [];
            obj.filepath_       = '';
        end

        % ================================================================
        %  計測開始
        % ================================================================
        function start(obj, filepath)
            % 指定パスへの CSV 書き出しを開始する（バックグラウンドプロセスを起動）
            %
            %   filepath : 出力 CSV のフルパス（ディレクトリは事前に作成しておくこと）
            obj.filepath_ = filepath;

            % Java の ArrayList でコマンドを組み立てる
            jList = java.util.ArrayList();
            jList.add(java.lang.String(obj.pythonExe_));
            jList.add(java.lang.String(obj.scriptPath_));
            jList.add(java.lang.String('--mode'));
            jList.add(java.lang.String('stream'));
            jList.add(java.lang.String('--port'));
            jList.add(java.lang.String(num2str(obj.port_)));
            jList.add(java.lang.String('--output'));
            jList.add(java.lang.String(filepath));
            jList.add(java.lang.String('--size_limit_kb'));
            jList.add(java.lang.String(sprintf('%.1f', obj.sizeLimitBytes_ / 1024)));

            pb = java.lang.ProcessBuilder(jList);
            pb.redirectErrorStream(true);   % stderr → stdout にマージ
            obj.jProc_ = pb.start();
        end

        % ================================================================
        %  状態確認
        % ================================================================
        function alive = isAlive(obj)
            % Python プロセスが実行中かどうか
            alive = ~isempty(obj.jProc_) && obj.jProc_.isAlive();
        end

        function sz = getSizeKB(obj)
            % 出力 CSV の現在のファイルサイズ [KB] を返す
            % ファイルが存在しない場合は 0 を返す
            if isempty(obj.filepath_) || ~isfile(obj.filepath_)
                sz = 0;
                return;
            end
            info = dir(obj.filepath_);
            sz   = info.bytes / 1024;
        end

        function done = isDone(obj)
            % 計測が完了したかどうかを返す
            %   true: ファイルサイズが閾値以上、または Python プロセスが終了
            %   false: まだ計測中
            if isempty(obj.jProc_)
                done = false;
                return;
            end
            size_done = (obj.getSizeKB() * 1024) >= obj.sizeLimitBytes_;
            proc_done = ~obj.jProc_.isAlive();
            done = size_done || proc_done;
        end

        % ================================================================
        %  終了処理
        % ================================================================
        function waitForFinish(obj, timeoutSec)
            % Python プロセスが完全に終了するまで待機する
            % isDone() が true になった後に呼ぶことで、ファイルが完全に
            % 書き出されることを保証する
            %
            %   timeoutSec : タイムアウト時間 [秒]（デフォルト: 15）
            if nargin < 2
                timeoutSec = 15;
            end
            if isempty(obj.jProc_)
                return;
            end
            t = tic;
            while obj.jProc_.isAlive()
                if toc(t) > timeoutSec
                    warning('[LeptrinoLogger] プロセス終了待ちタイムアウト（%.0f 秒）→ 強制終了', ...
                        timeoutSec);
                    obj.jProc_.destroyForcibly();
                    break;
                end
                pause(0.05);
            end
        end

        function stop(obj)
            % 計測を強制終了する（異常時・Ctrl+C 中断時に使用）
            if ~isempty(obj.jProc_) && obj.jProc_.isAlive()
                obj.jProc_.destroyForcibly();
            end
        end

        % ================================================================
        %  結果取得
        % ================================================================
        function result = getResult(obj)
            % Python プロセスが stdout に出力した JSON を取得する
            % waitForFinish() を呼んだ後に使用すること
            %
            % 返値の例: struct('status','ok','samples',24013,'size_kb',1004.2)
            result = struct('status', 'unknown', 'samples', 0, ...
                            'size_kb', obj.getSizeKB());
            if isempty(obj.jProc_)
                return;
            end
            try
                reader = java.io.BufferedReader( ...
                    java.io.InputStreamReader(obj.jProc_.getInputStream(), 'UTF-8'));
                line = reader.readLine();
                reader.close();
                if ~isempty(line)
                    result = jsondecode(char(line));
                end
            catch
                % 読み取り失敗は無視（size_kb フィールドで代替確認可）
            end
        end

        function data = getLatest(obj)
            % 後方互換用: getRecentRows(1) のラッパー
            rows = obj.getRecentRows(1);
            if isempty(rows)
                data = zeros(1, 6);
            else
                data = rows(end, 2:7);
            end
        end

        function rows = getRecentRows(obj, n_rows)
            % CSV の末尾から最新 n_rows 行を読み取って n×7 行列で返す
            %   列: [elapsed_s, Fx, Fy, Fz, Mx, My, Mz]
            % WindyMonitor のリアルタイム表示用（振動波形の確認に使う）
            rows = zeros(0, 7);
            if isempty(obj.filepath_) || ~isfile(obj.filepath_), return; end
            try
                fid = fopen(obj.filepath_, 'r', 'n', 'CP932');
                if fid < 0, return; end
                fseek(fid, 0, 'eof');
                file_size  = ftell(fid);
                read_bytes = min(n_rows * 64, file_size);  % 1行 ~60 bytes
                fseek(fid, -read_bytes, 'eof');
                tail = fread(fid, read_bytes, 'char=>char')';
                fclose(fid);

                lines = strsplit(strtrim(tail), newline);
                buf   = zeros(numel(lines), 7);
                count = 0;
                for k = 1:numel(lines)
                    vals = sscanf(lines{k}, '%f,%f,%f,%f,%f,%f,%f');
                    if numel(vals) == 7
                        count = count + 1;
                        buf(count, :) = vals';
                    end
                end
                if count == 0, return; end
                % 末尾 n_rows 行だけ返す
                rows = buf(max(1, count - n_rows + 1):count, :);
            catch
            end
        end

        % ================================================================
        %  デストラクタ
        % ================================================================
        function delete(obj)
            % オブジェクトが削除される際にプロセスを確実に終了させる
            obj.stop();
        end

    end % methods
end % classdef
