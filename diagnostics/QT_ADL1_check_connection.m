%% QT_ADL1_check_connection.m
%  QT-ADL1 との RS-232C 接続確認スクリプト
%
%  確認内容:
%    Step 1: 利用可能なCOMポートを表示
%    Step 2: シリアルポートを開く
%    Step 3: Q:A0 を送信して現在位置・ステータスを受信
%    Step 4: 結果を表示して切断
%
%  使い方:
%    1. COM_PORT を実際のポート番号に変更して実行
%    2. コンソールに座標値とステータスが表示されれば接続OK

clc; clear;

%% ---- Step 1: 利用可能なCOMポートを確認 ----
fprintf('=== 利用可能なCOMポート ===\n');
ports = serialportlist("available");
if isempty(ports)
    fprintf('  (利用可能なポートが見つかりません)\n');
else
    for i = 1:numel(ports)
        fprintf('  %s\n', ports(i));
    end
end
fprintf('\n');

%% ---- 設定----
config_path = fullfile(fileparts(fileparts(mfilename('fullpath'))), 'config.json');
if ~isfile(config_path)
    error(['config.json が見つかりません。\n' ...
           'config.json.example をコピーして config.json を作成し、\n' ...
           '各自の環境に合わせてパスを設定してください。']);
end
cfg = jsondecode(fileread(config_path));
COM_PORT = cfg.qt_adl1_port;

%% ---- Step 2: ポートを開く ----
fprintf('=== Step 2: %s を開く ===\n', COM_PORT);
try
    s = serialport(COM_PORT, 9600, ...
        'DataBits',    8,      ...
        'Parity',      'none', ...
        'StopBits',    1,      ...
        'FlowControl', 'none');
    configureTerminator(s, 'CR/LF', 'CR/LF');
    s.Timeout = 3;
    fprintf('  OK: ポートを開きました\n\n');
catch ME
    fprintf('  NG: ポートを開けませんでした\n');
    fprintf('  エラー: %s\n', ME.message);
    return;
end

%% ---- Step 3: Q:A0 を送信してレスポンスを受信 ----
fprintf('=== Step 3: Q:A0 送信 → レスポンス受信 ===\n');
try
    writeline(s, 'Q:A0');
    resp = strtrim(readline(s));
    fprintf('  受信データ (raw): "%s"\n\n', resp);
catch ME
    fprintf('  NG: 送受信に失敗しました\n');
    fprintf('  エラー: %s\n', ME.message);
    delete(s);
    return;
end

%% ---- Step 4: レスポンスを解析して表示 ----
fprintf('=== Step 4: 解析結果 ===\n');

% string型 → char型に変換し、不可視文字を除去
resp = char(resp);
resp = resp(resp >= 32 & resp <= 126);

if isempty(resp)
    fprintf('  NG: レスポンスが空です\n');
    fprintf('  → ケーブル接続・COMポート番号・ボーレートを確認してください\n');
elseif resp(1) == '!'
    fprintf('  コントローラからエラーが返りました: %s\n', resp);
    fprintf('  → コマンド形式の問題の可能性があります\n');
else
    status = resp(end);
    posStr = resp(1:end-1);
    posVal = str2double(posStr);

    fprintf('  座標値   : %s pulse\n', posStr);
    fprintf('  ステータス: %s  ', status);
    switch status
        case 'K', fprintf('→ 正常停止\n');
        case 'D', fprintf('→ 移動中\n');
        case 'L', fprintf('→ リミット検出による停止\n');
        case 'E', fprintf('→ 非常停止状態\n');
        case 'H', fprintf('→ 原点復帰エラー停止\n');
        otherwise, fprintf('→ 未定義ステータス\n');
    end

    if ~isnan(posVal)
        fprintf('\n  ✓ 接続確認OK: 現在位置 %+d pulse, ステータス "%s"\n', posVal, status);
    else
        fprintf('\n  NG: 座標値のパースに失敗しました: "%s"\n', posStr);
    end
end

%% ---- 切断 ----
delete(s);
fprintf('\n=== 切断しました ===\n');