%% set_angle.m
%  ターミナルで指定した迎角へステージを移動するだけの単純なスクリプト
%
%  使い方:
%    1. このスクリプトを実行
%    2. プロンプトに迎角 [度] を入力（例: 15）
%    3. 入力するたびにその迎角へ移動する
%    4. "q" または空入力（何も入力せず Enter）でステージとの接続を切って終了

clc; clear;

% QT_ADL1 クラスは measurement_control フォルダにあるためパスを通す。
addpath(fullfile(fileparts(fileparts(mfilename('fullpath'))), 'measurement_control'));

%% ---- 設定読み込み ----
config_path = fullfile(fileparts(fileparts(mfilename('fullpath'))), 'config.json');
if ~isfile(config_path)
    error(['config.json が見つかりません。\n' ...
           'config.json.example をコピーして config.json を作成し、\n' ...
           '各自の環境に合わせてパスを設定してください。']);
end
cfg = jsondecode(fileread(config_path));
COM_PORT = cfg.qt_adl1_port;

%% ---- 接続 ----
stage = QT_ADL1(COM_PORT, [], cfg.origin_pulse);
cleanupObj = onCleanup(@() delete(stage));

%% ---- 迎角入力ループ ----
fprintf('\n迎角 [度] を入力して Enter（"q" または空入力で切断・終了）\n');
while true
    in = strtrim(input('迎角> ', 's'));
    if isempty(in) || strcmpi(in, 'q')
        break;
    end

    angle = str2double(in);
    if isnan(angle)
        fprintf('  数値を入力してください（"q" で切断・終了）\n');
        continue;
    end

    stage.moveToAngle(angle);
    fprintf('  現在の迎角: %.4f°\n', stage.getAngle());
end

clear cleanupObj;  % onCleanup を明示的に発火させて delete(stage) を実行
fprintf('ステージとの接続を切断しました\n');
