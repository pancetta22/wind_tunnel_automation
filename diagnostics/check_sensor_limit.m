%% check_sensor_limit.m
%  Leptrino 6軸センサの「定格値(Limit)」が正しいか確認する診断スクリプト
%
%  背景:
%    物理量は leptrino_server.py 内で
%        phys = limit / 10000 * raw
%    として計算される。GetSensorLimit が返す定格値(limit)が誤って大きいと、
%    すべての力が比例して過大になる（揚力が大きく出る原因になり得る）。
%
%  このスクリプトは GetSensorLimit の値と生データを取得し、期待値
%    SFS080F300M5R0U6 : Fx,Fy,Fz = 30 N,  Mx,My,Mz = 5 Nm
%  と各軸ごとに照合する。
%
%  使い方:
%    >> check_sensor_limit
%  （センサ接続・起動した状態で実行。無負荷でOK）

clc;
EXPECTED = [30, 30, 30, 5, 5, 5];          % SFS080F300M5R0U6 の定格
LABELS   = {'Fx','Fy','Fz','Mx','My','Mz'};
UNITS    = {'N','N','N','Nm','Nm','Nm'};

%% ---- 設定読み込み ----
config_path = fullfile(fileparts(fileparts(mfilename('fullpath'))), 'config.json');
if ~isfile(config_path)
    error('config.json が見つかりません。');
end
cfg    = jsondecode(fileread(config_path));
SCRIPT = fullfile(fileparts(fileparts(mfilename('fullpath'))), 'leptrino', 'leptrino_server.py');

%% ---- limit モードでセンサから取得 ----
fprintf('センサから定格値(Limit)を取得中...\n');
cmd = sprintf('"%s" "%s" --port %d --mode limit', ...
              cfg.python_exe, SCRIPT, cfg.leptrino_port);
[status, output] = system(cmd);
if status ~= 0
    error('センサ取得失敗:\n%s', output);
end
d = jsondecode(output);   % d.limit, d.raw_avg, d.phys, d.labels, d.n

%% ---- 照合結果を表示 ----
fprintf('\n=== Leptrino 定格値(Limit)チェック ===\n');
fprintf('センサ型番想定: SFS080F300M5R0U6  →  Fx,Fy,Fz=30N / Mx,My,Mz=5Nm\n');
fprintf('物理量 = Limit / 10000 × 生データ\n\n');
fprintf('%-4s %12s %10s %8s %14s %12s\n', ...
        '軸', 'Limit(取得)', '期待', '比', '生(±10000)', '物理');
fprintf('%s\n', repmat('-', 1, 64));

all_ok = true;
for i = 1:6
    lim   = d.limit(i);
    expv  = EXPECTED(i);
    ratio = lim / expv;
    flag  = '';
    if abs(ratio - 1) > 0.001
        flag   = sprintf('  <-- 不一致! (%.3f倍)', ratio);
        all_ok = false;
    end
    fprintf('%-4s %12.3f %10.1f %8.3f %14.1f %9.4f %-2s%s\n', ...
            LABELS{i}, lim, expv, ratio, d.raw_avg(i), d.phys(i), UNITS{i}, flag);
end

%% ---- 判定 ----
fprintf('%s\n', repmat('-', 1, 64));
if all_ok
    fprintf('\n✓ 定格値は全軸で正しい（期待値と一致）。\n');
    fprintf('  → 「定格(Limit)適用ミス」は原因ではありません。\n');
else
    fprintf('\n✗ 定格値に不一致があります！\n');
    fprintf('  → 物理量は「比」の分だけ誤ってスケールします。\n');
    fprintf('    例: Fy の Limit が 1.29倍なら、揚力も 1.29倍 過大に出ます。\n');
    fprintf('  → センサのファームウェア定格 / DLL / 接続を確認してください。\n');
end
