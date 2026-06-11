%% Advantest R6441B voltage capture
base_dir = fileparts(fileparts(mfilename('fullpath')));
config_path = fullfile(base_dir, 'config.json');

if ~isfile(config_path)
    error(['config.json was not found.\n' ...
           'Copy config.json.example to config.json and edit it.\n' ...
           'Path: %s'], config_path);
end

cfg = jsondecode(fileread(config_path));

if ~isfield(cfg, 'r6441b_port') || isempty(cfg.r6441b_port)
    error('config.json does not define r6441b_port.');
end

COM_PORT = char(cfg.r6441b_port);
BAUD_RATE = 9600;
N_SAMPLES = 100;

if isfield(cfg, 'r6441b_timeout_sec') && ~isempty(cfg.r6441b_timeout_sec)
    TIMEOUT_SEC = cfg.r6441b_timeout_sec;
else
    TIMEOUT_SEC = 5;
end

s = serialport(COM_PORT, BAUD_RATE, ...
    'DataBits',    8,      ...
    'Parity',      'none', ...
    'StopBits',    1,      ...
    'FlowControl', 'none');
configureTerminator(s, 'CR/LF');
s.Timeout = TIMEOUT_SEC;

fprintf('R6441B connected: %s (%d bps)\n', COM_PORT, BAUD_RATE);
fprintf('Capturing voltage data... (%d samples)\n', N_SAMPLES);

pressure_raw = zeros(N_SAMPLES, 1);
timestamps = zeros(N_SAMPLES, 1);
t_start = tic;

for i = 1:N_SAMPLES
    try
        writeline(s, 'MD?');
        raw_str = readline(s);
        timestamps(i) = toc(t_start);
        val = str2double(strtrim(raw_str));
        if isnan(val)
            warning('Sample %d: invalid numeric value: "%s"', i, raw_str);
        else
            pressure_raw(i) = val;
            fprintf('  [%d/%d] %.6f V\n', i, N_SAMPLES, val);
        end
    catch ME
        warning('Sample %d: communication error: %s', i, ME.message);
        break
    end
end

fprintf('Capture finished in %.2f s\n', toc(t_start));
clear s;

valid_idx = pressure_raw ~= 0;
pressure_data = pressure_raw(valid_idx);
time_data = timestamps(valid_idx);
fprintf('Valid samples: %d / %d\n', sum(valid_idx), N_SAMPLES);
% 結果はターミナル表示のみ（CSV は出力しない）。
% mV 併記は計測前のデジボル接触チェック（差圧 mV の妥当性確認）用。
fprintf('Average: %.6f V  (%.2f mV)\n', mean(pressure_data), mean(pressure_data) * 1000);
fprintf('Std    : %.6f V  (%.2f mV)\n', std(pressure_data),  std(pressure_data) * 1000);

figure;
plot(time_data, pressure_data, 'b-o', 'MarkerSize', 3);
xlabel('Time [s]');
ylabel('Voltage [V]');
title('R6441B Voltage Data');
grid on;
