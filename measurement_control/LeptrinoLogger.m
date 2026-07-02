classdef LeptrinoLogger < handle
% LeptrinoLogger  Leptrino 6軸センサ 時系列ロガークラス
%
% leptrino_server.py を --mode stream で起動し、バックグラウンドで CSV を書き出す。
% Parallel Computing Toolbox 不要 — Java ProcessBuilder でバックグラウンド実行。
% MATLAB 側はファイルサイズ or 経過時間を監視して計測完了を検知する。
%
% 【終了条件】
%   start(filepath)             : サイズ上限（sizeLimitKB）で終了（定常空力実験・既存動作）
%   start(filepath, timeSec)    : 秒数指定で終了（フラッター実験用）
%
% 使い方（秒数指定）:
%   logger = LeptrinoLogger(pythonExe, scriptPath, port, sizeLimitKB);
%   logger.start(filepath, 30.0);    % 30秒計測
%   pause(0.5);
%   while ~logger.isDone()
%       % デジボル計測など
%   end
%   logger.waitForFinish();
%   result = logger.getResult();     % {"status":"ok","samples":N,"size_kb":X,"duration_sec":T}

    properties (Access = private)
        jProc_            % java.lang.Process  バックグラウンドプロセス
        filepath_         % string  出力 CSV パス
        sizeLimitBytes_   % double  ファイルサイズ閾値 [bytes]（サイズ制限時のみ使用）
        timeLimitSec_     % double or []  秒数制限（[]ならサイズ制限）
        tStart_           % double  start() 呼び出し時刻（tic 値）
        pythonExe_        % string  python.exe のパス
        scriptPath_       % string  leptrino_server.py のパス
        port_             % int     COMポート番号
    end

    methods

        % ================================================================
        %  コンストラクタ
        % ================================================================
        function obj = LeptrinoLogger(pythonExe, scriptPath, port, sizeLimitKB)
            obj.pythonExe_      = pythonExe;
            obj.scriptPath_     = scriptPath;
            obj.port_           = port;
            obj.sizeLimitBytes_ = sizeLimitKB * 1024;
            obj.timeLimitSec_   = [];
            obj.jProc_          = [];
            obj.filepath_       = '';
            obj.tStart_         = [];
        end

        % ================================================================
        %  計測開始
        % ================================================================
        function start(obj, filepath, timeLimitSec)
            % start(filepath)
            %   → サイズ上限（sizeLimitKB）で終了（定常空力実験・既存動作）
            %
            % start(filepath, timeLimitSec)
            %   → timeLimitSec 秒経過で終了（フラッター実験用）
            %   timeLimitSec: 計測秒数 [秒]（例: 30.0）

            obj.filepath_ = filepath;

            if nargin >= 3 && ~isempty(timeLimitSec)
                obj.timeLimitSec_ = timeLimitSec;
            else
                obj.timeLimitSec_ = [];
            end

            obj.tStart_ = tic;

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

            if ~isempty(obj.timeLimitSec_)
                % 秒数制限（フラッター実験）
                jList.add(java.lang.String('--time_limit_sec'));
                jList.add(java.lang.String(sprintf('%.2f', obj.timeLimitSec_)));
            else
                % サイズ制限（定常空力実験・既存動作）
                jList.add(java.lang.String('--size_limit_kb'));
                jList.add(java.lang.String(sprintf('%.1f', obj.sizeLimitBytes_ / 1024)));
            end

            pb = java.lang.ProcessBuilder(jList);
            pb.redirectErrorStream(true);   % stderr → stdout にマージ
            obj.jProc_ = pb.start();
        end

        % ================================================================
        %  状態確認
        % ================================================================
        function alive = isAlive(obj)
            alive = ~isempty(obj.jProc_) && obj.jProc_.isAlive();
        end

        function sz = getSizeKB(obj)
            if isempty(obj.filepath_) || ~isfile(obj.filepath_)
                sz = 0;
                return;
            end
            info = dir(obj.filepath_);
            sz   = info.bytes / 1024;
        end

        function elapsed = getElapsedSec(obj)
            % 計測開始からの経過時間 [秒] を返す（秒数制限時のプログレス表示用）
            if isempty(obj.tStart_)
                elapsed = 0;
            else
                elapsed = toc(obj.tStart_);
            end
        end

        function done = isDone(obj)
            % 計測が完了したかどうかを返す
            %
            % 【秒数制限モード】
            %   MATLAB側の経過時間 or Python プロセス終了 で true
            %   ※ Python側が先に終了するはずだが、MATLAB側の時間チェックも
            %     フォールバックとして残す（プロセス監視の遅延対策）
            %
            % 【サイズ制限モード（既存動作）】
            %   ファイルサイズが閾値以上 or Python プロセス終了 で true
            if isempty(obj.jProc_)
                done = false;
                return;
            end

            proc_done = ~obj.jProc_.isAlive();

            if ~isempty(obj.timeLimitSec_)
                % 秒数制限モード
                time_done = obj.getElapsedSec() >= obj.timeLimitSec_;
                done = time_done || proc_done;
            else
                % サイズ制限モード（既存動作）
                size_done = (obj.getSizeKB() * 1024) >= obj.sizeLimitBytes_;
                done = size_done || proc_done;
            end
        end

        % ================================================================
        %  終了処理
        % ================================================================
        function waitForFinish(obj, timeoutSec)
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
            if ~isempty(obj.jProc_) && obj.jProc_.isAlive()
                obj.jProc_.destroyForcibly();
            end
        end

        % ================================================================
        %  結果取得
        % ================================================================
        function result = getResult(obj)
            % 返値の例（サイズ制限）: struct('status','ok','samples',24013,'size_kb',1004.2)
            % 返値の例（秒数制限）  : struct('status','ok','samples',36018,'size_kb',3001.5,'duration_sec',30.01)
            %
            % leptrino_server.py は stderr を stdout にマージして起動しており
            % (redirectErrorStream(true))、結果 JSON はプロセス終了直前に最後の
            % 行として出力される。そのため stdout の最初の1行ではなく、末尾から
            % 走査して最初に jsondecode に成功した行を採用する（先頭に警告等の
            % ログ行が混ざっていても壊れないようにするため）。
            result = struct('status', 'unknown', 'samples', 0, ...
                            'size_kb', obj.getSizeKB());
            if isempty(obj.jProc_)
                return;
            end
            try
                reader = java.io.BufferedReader( ...
                    java.io.InputStreamReader(obj.jProc_.getInputStream(), 'UTF-8'));
                lines = {};
                line = reader.readLine();
                while ~isempty(line)
                    lines{end+1} = char(line); %#ok<AGROW>
                    line = reader.readLine();
                end
                reader.close();
                for k = numel(lines):-1:1
                    if isempty(strtrim(lines{k})), continue; end
                    try
                        result = jsondecode(lines{k});
                        break;
                    catch
                    end
                end
            catch
            end
        end

        function data = getLatest(obj)
            rows = obj.getRecentRows(1);
            if isempty(rows)
                data = zeros(1, 6);
            else
                data = rows(end, 2:7);
            end
        end

        function rows = getRecentRows(obj, n_rows)
            % ファイル末尾から n_rows 行分をパースして返す。
            % leptrino_server.py の書式は "%.4f,%.3f,%.3f,%.3f,%.4f,%.4f,%.4f\n"
            % （7列）で、符号・整数桁数によって1行は 30～90byte 程度まで
            % 変動しうる。1行あたりの想定バイト数に余裕係数を掛け、それでも
            % n_rows 行に届かない場合は読み出し範囲を広げてリトライする。
            rows = zeros(0, 7);
            if isempty(obj.filepath_) || ~isfile(obj.filepath_), return; end
            fid = -1;
            try
                BYTES_PER_ROW_EST = 160;  % 余裕を持たせた1行あたりの想定バイト数
                MAX_ATTEMPTS = 4;

                fid = fopen(obj.filepath_, 'r', 'n', 'CP932');
                if fid < 0, return; end
                fseek(fid, 0, 'eof');
                file_size = ftell(fid);

                count  = 0;
                buf    = zeros(0, 7);
                mult   = 1;
                for attempt = 1:MAX_ATTEMPTS
                    read_bytes = min(n_rows * BYTES_PER_ROW_EST * mult, file_size);
                    fseek(fid, -read_bytes, 'eof');
                    tail = fread(fid, read_bytes, 'char=>char')';

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

                    if count >= n_rows || read_bytes >= file_size
                        break;
                    end
                    mult = mult * 4;
                end
                fclose(fid);
                fid = -1;

                if count == 0, return; end
                rows = buf(max(1, count - n_rows + 1):count, :);
            catch
                if fid >= 0, try; fclose(fid); catch; end; end
            end
        end

        % ================================================================
        %  デストラクタ
        % ================================================================
        function delete(obj)
            obj.stop();
        end

    end % methods
end % classdef