%% Advantest R6441B RS-232C データ取得
COM_PORT    = 'COM10';
BAUD_RATE   = 9600;
N_SAMPLES   = 100;
TIMEOUT_SEC = 5;

s = serialport(COM_PORT, BAUD_RATE, ...
    'DataBits',    8,      ...
    'Parity',      'none', ...
    'StopBits',    1,      ...
    'FlowControl', 'none');
configureTerminator(s, 'CR/LF');
s.Timeout = TIMEOUT_SEC;

fprintf('R6441B に接続しました: %s (%d bps)\n', COM_PORT, BAUD_RATE);
fprintf('データ取得開始... (%d サンプル)\n', N_SAMPLES);

pressure_raw = zeros(N_SAMPLES, 1);
timestamps   = zeros(N_SAMPLES, 1);
t_start = tic;

for i = 1:N_SAMPLES
    try
        writeline(s, 'MD?');
        raw_str = readline(s);
        timestamps(i) = toc(t_start);
        val = str2double(strtrim(raw_str));
        if isnan(val)
            warning('サンプル %d: 数値変換失敗 → "%s"', i, raw_str);
        else
            pressure_raw(i) = val;
            fprintf('  [%d/%d] %.6f V\n', i, N_SAMPLES, val);
        end
    catch ME
        warning('サンプル %d: 通信エラー → %s', i, ME.message);
        break
    end
end

fprintf('データ取得完了: %.2f 秒\n', toc(t_start));
clear s;

% 統計
valid_idx     = pressure_raw ~= 0;
pressure_data = pressure_raw(valid_idx);
time_data     = timestamps(valid_idx);
fprintf('有効サンプル数: %d / %d\n', sum(valid_idx), N_SAMPLES);
fprintf('平均値: %.6f V\n', mean(pressure_data));

% CSV保存
output_table = table(time_data, pressure_data, ...
    'VariableNames', {'Time_s', 'Voltage_V'});
writetable(output_table, 'pressure_data.csv');
fprintf('pressure_data.csv に保存しました\n');

% プロット
figure;
plot(time_data, pressure_data, 'b-o', 'MarkerSize', 3);
xlabel('時間 [s]'); ylabel('電圧 [V]');
title('R6441B 測定データ'); grid on;