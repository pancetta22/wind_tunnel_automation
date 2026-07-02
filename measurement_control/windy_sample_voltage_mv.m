function [samples, n] = windy_sample_voltage_mv(s_volt, duration_sec, timeout_sec, show_progress)
% WINDY_SAMPLE_VOLTAGE_MV  R6441B デジボルから差圧電圧 [mV] を一定秒数取得する
%
% 書式:
%   [samples, n] = windy_sample_voltage_mv(s_volt, duration_sec)
%   [samples, n] = windy_sample_voltage_mv(s_volt, duration_sec, timeout_sec, show_progress)
%
% 入力:
%   s_volt       : R6441B の serialport オブジェクト
%   duration_sec : 計測する秒数 [秒]
%   timeout_sec  : ループ内 readline タイムアウト [秒]（省略時 0.8）
%   show_progress: 進捗を1行更新表示するか（省略時 true）
%
% 出力:
%   samples : 1×n の差圧電圧 [mV]
%   n       : 取得できたサンプル数
%
% デジボルの詰まり対策：溜まった古い応答を最初に捨て（flush）、短いタイムアウト＋
% ポーリング間隔で読む。これを行わないと直前フェーズ（前の風速・通風中）の古い mV を
% 平均に混ぜてしまう（260624_flutter の代表風速混入バグ → fix_windspeed/ で事後修正）。
% 読み取り失敗（writeline/readline 例外や NaN パース）が連続する場合は、原因不明の
% まま無限に握りつぶさないよう一定回数ごとに警告を出す。
%
% run_experiment.m（オフセット計測・代表電圧計測）と flutter_run_experiment.m
% （オフセット計測・代表風速計測）の両方から共有して使う。

    if nargin < 3 || isempty(timeout_sec), timeout_sec = 0.8; end
    if nargin < 4, show_progress = true; end

    CONSEC_FAIL_WARN = 30;  % 連続失敗がこの回数を超えたら警告（0.1s間隔で約3秒相当）

    samples = zeros(1, 200);
    n = 0;
    n_consec_fail = 0;

    flush(s_volt, 'input');
    prev_timeout = s_volt.Timeout;
    s_volt.Timeout = timeout_sec;
    t_end = tic;

    while toc(t_end) < duration_sec
        try
            writeline(s_volt, 'MD?');
            raw  = readline(s_volt);
            v_mv = str2double(strtrim(raw)) * 1000;
            if isnan(v_mv)
                n_consec_fail = n_consec_fail + 1;
            else
                n_consec_fail = 0;
                n = n + 1;
                if n > numel(samples)
                    samples = [samples, zeros(1, 100)]; %#ok<AGROW>
                end
                samples(n) = v_mv;
                if show_progress
                    fprintf('  %2d サンプル  最新: %+.2f mV\r', n, v_mv);
                end
            end
        catch
            n_consec_fail = n_consec_fail + 1;
        end

        if n_consec_fail == CONSEC_FAIL_WARN
            warning('[R6441B] 応答の取得に連続で失敗しています（%d回）。接続を確認してください。', n_consec_fail);
        end

        pause(0.1);
    end
    s_volt.Timeout = prev_timeout;
    if show_progress, fprintf('\n'); end

    samples = samples(1:n);
end
