%% weight_check.m
%  Leptrino 6軸センサの力スケール確認スクリプト
%
%  手順:
%    Step 1: 何も載せない状態でゼロ計測（タレ）
%    Step 2: 既知質量のおもりを載せて計測
%    Step 3: 差分と期待値を比較 → センサスケール誤差を算出
%
%  使い方:
%    1. センサを接続・起動した状態で実行
%    2. プロンプトに従い「おもりの質量 [g]」を入力
%    3. コンソールに結果が表示される

clc;

%% ---- 設定 ----
MEASURE_SEC  = 3;      % 1回の計測時間 [秒]（長いほど安定）
SIZE_KB      = MEASURE_SEC * 60;  % ≒ 1200 sps × 7col × ~17B/row

config_path = fullfile(fileparts(fileparts(mfilename('fullpath'))), 'config.json');
if ~isfile(config_path)
    error('config.json が見つかりません。');
end
cfg        = jsondecode(fileread(config_path));
PYTHON_EXE = cfg.python_exe;
LEPTRINO_PORT = cfg.leptrino_port;
SCRIPT     = fullfile(fileparts(fileparts(mfilename('fullpath'))), 'leptrino', 'leptrino_server.py');

%% ---- おもり質量の入力 ----
mass_g = input('おもりの質量 [g] を入力してください: ');
if isempty(mass_g) || mass_g <= 0
    error('質量は正の値を入力してください。');
end
mass_kg      = mass_g / 1000;
expected_N   = mass_kg * 9.80665;
fprintf('\n期待値: %.1f g → %.4f N\n\n', mass_g, expected_N);

%% ---- Step 1: ゼロ計測 ----
fprintf('=== Step 1: ゼロ計測（おもりなし） ===\n');
input('センサが通常の状態であることを確認したら Enter を押してください: ');
F_zero = measure_mean(PYTHON_EXE, SCRIPT, LEPTRINO_PORT, SIZE_KB, 'ゼロ');
fprintf('  Fx=%.4f  Fy=%.4f  Fz=%.4f  Mx=%.4f  My=%.4f  Mz=%.4f [N/Nm]\n\n', F_zero);

%% ---- Step 2: おもり計測 ----
fprintf('=== Step 2: おもり計測（%.1f g を載せる） ===\n', mass_g);
input('おもりを載せたら Enter を押してください: ');
F_weight = measure_mean(PYTHON_EXE, SCRIPT, LEPTRINO_PORT, SIZE_KB, 'おもり');
fprintf('  Fx=%.4f  Fy=%.4f  Fz=%.4f  Mx=%.4f  My=%.4f  Mz=%.4f [N/Nm]\n\n', F_weight);

%% ---- Step 3: 結果表示 ----
dF = F_weight - F_zero;   % 差分 = おもりによる力
[max_val, max_ax] = max(abs(dF(1:3)));
ax_names = {'Fx', 'Fy', 'Fz'};

fprintf('=== 結果 ===\n');
fprintf('差分  Fx=%+.4f  Fy=%+.4f  Fz=%+.4f N\n', dF(1), dF(2), dF(3));
fprintf('\n最大軸: %s = %+.4f N\n', ax_names{max_ax}, dF(max_ax));
fprintf('期待値:          %+.4f N  (%.1f g × g)\n', -expected_N, mass_g);
fprintf('測定値(絶対値):   %+.4f N\n', max_val);
fprintf('\nスケール誤差: %.4f 倍 (測定/期待)\n', max_val / expected_N);

if abs(max_val / expected_N - 1.0) < 0.02
    fprintf('→ センサスケール正常（誤差 2%% 以内）\n');
elseif max_val / expected_N > 1.02
    fprintf('→ センサが %.3f 倍 過大計測しています\n', max_val / expected_N);
else
    fprintf('→ センサが %.3f 倍 過小計測しています\n', max_val / expected_N);
end

%% ---- 計測関数 ----
function F = measure_mean(python_exe, script, port, size_kb, label)
    tmp = [tempdir, 'weight_check_tmp.csv'];
    logger = LeptrinoLogger(python_exe, script, port, size_kb);
    fprintf('[%s] 計測中（約 %d 秒）...', label, round(size_kb/60));
    logger.start(tmp);
    while ~logger.isDone(), pause(0.1); end
    logger.waitForFinish();
    fprintf(' 完了\n');

    raw = readmatrix(tmp, 'NumHeaderLines', 4, ...
                     'Delimiter', ',', 'ConsecutiveDelimitersRule', 'join');
    % 列: [time, Fx, Fy, Fz, Mx, My, Mz]
    if size(raw, 2) < 7
        error('CSV の列数が不正です（%d 列）。', size(raw, 2));
    end
    F = mean(raw(:, 2:7), 1, 'omitnan');  % [Fx Fy Fz Mx My Mz] 平均
end
