%% Advantest R6441B デジタルマルチメータ RS-232C インターフェース
%  風洞実験用 気圧データ取得スクリプト
%
%  通信仕様（工場出荷時設定）:
%    ボーレート : 9600 bps
%    データビット: 8 bit
%    パリティ   : なし
%    ストップビット: 1
%    フロー制御 : ハードウェア (DTR/DSR)
%    デリミタ   : CR+LF (固定)
%    読み取りコマンド: MD?

%% ========== 設定（config.json から読み込み）==========
config_path = fullfile(fileparts(mfilename('fullpath')), 'config.json');

if ~isfile(config_path)
    error(['config.json が見つかりません。\n' ...
        'config.json.example をコピーして config.json を作成し、\n' ...
        '各自の環境に合わせてパスを設定してください。']);
end

cfg = jsondecode(fileread(config_path));
COM_PORT    = cfg.r6441b_port;
BAUD_RATE   = 9600;          % 工場出荷時固定値（変更不要）
N_SAMPLES   = cfg.r6441b_n_samples;
TIMEOUT_SEC = cfg.r6441b_timeout_sec;

%% ========== シリアルポートの設定と接続 ==========
s = serialport(COM_PORT, BAUD_RATE, ...
    'DataBits',    8,      ...
    'Parity',      'none', ...
    'StopBits',    1,      ...
    'FlowControl', 'hardware');   % DTR/DSR ハードウェアフロー制御

configureTerminator(s, 'CR/LF');
s.Timeout = TIMEOUT_SEC;

fprintf('R6441B に接続しました: %s (%d bps)\n', COM_PORT, BAUD_RATE);

%% ========== データ取得 ==========
pressure_raw = zeros(N_SAMPLES, 1);
timestamps   = zeros(N_SAMPLES, 1);

fprintf('データ取得開始... (%d サンプル)\n', N_SAMPLES);
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
        end
        
    catch ME
        warning('サンプル %d: 通信エラー → %s', i, ME.message);
        break
    end
end

fprintf('データ取得完了: %.2f 秒\n', toc(t_start));

%% ========== シリアルポートを閉じる ==========
clear s;
fprintf('ポートを閉じました。\n');

%% ========== データ確認・保存 ==========
valid_idx     = pressure_raw ~= 0;
pressure_data = pressure_raw(valid_idx);
time_data     = timestamps(valid_idx);

fprintf('有効サンプル数: %d / %d\n', sum(valid_idx), N_SAMPLES);
fprintf('平均値: %.6f\n', mean(pressure_data));
fprintf('最大値: %.6f\n', max(pressure_data));
fprintf('最小値: %.6f\n', min(pressure_data));

output_table = table(time_data, pressure_data, ...
    'VariableNames', {'Time_s', 'Pressure_raw'});
writetable(output_table, 'pressure_data.csv');
fprintf('データを pressure_data.csv に保存しました。\n');

figure;
plot(time_data, pressure_data, 'b-o', 'MarkerSize', 3);
xlabel('時間 [s]');
ylabel('測定値');
title('R6441B 測定データ（気圧）');
grid on;


%% ========== 補足：風速計算（必要に応じて編集） ==========
% ピトー管による動圧 → 風速変換の例
% 差圧 [Pa] が pressure_data に格納されている前提
%
%   rho = 1.225;                          % 空気密度 [kg/m^3]（標準大気）
%   wind_speed = sqrt(2 * pressure_data / rho);   % 風速 [m/s]
%
% ※ 使用するセンサの仕様に合わせてスケーリングしてください。