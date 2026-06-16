function setup_paths()
%% setup_paths.m
%  Windy のサブフォルダを MATLAB パスへ追加するセットアップ関数。
%
%  使い方（MATLAB コマンドウィンドウで、リポジトリのルートに cd した状態で）:
%      setup_paths
%
%  これを一度実行すると、以下が関数名・スクリプト名だけで呼べるようになる:
%    - measurement_control/ : QT_ADL1 / LeptrinoLogger / WindyMonitor /
%                             make_filename / get_sensor_data / get_voltage
%    - diagnostics/         : QT_ADL1_check_connection / check_sensor_limit /
%                             weight_check / tare_measure
%
%  ※ run_experiment.m は起動時に自分で measurement_control をパスに追加する
%    ため、本計測だけならこの関数の実行は不要。
%    診断ツールやヘルパを単体で使うときに実行する。

root = fileparts(mfilename('fullpath'));
addpath(fullfile(root, 'measurement_control'));
addpath(fullfile(root, 'diagnostics'));

fprintf('[setup_paths] パスを追加しました:\n');
fprintf('  %s\n', fullfile(root, 'measurement_control'));
fprintf('  %s\n', fullfile(root, 'diagnostics'));
end
