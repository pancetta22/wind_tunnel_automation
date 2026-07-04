%% pluck_test.m
%  【予備実験】無風・迎角固定で翼を弾いて放し、Leptrino 6軸の自由減衰を記録する
%
%  目的: 翼構造の静止時固有振動数 f_n・構造減衰比 ζ を同定するための生データ取得。
%        解析は free_decay_analysis.py で行う（本フォルダ内で自己完結）。
%
%  手順:
%    1. 送風を止める（無風）。このスクリプトを実行 → 原点復帰 → 目的の迎角へ移動。
%    2. Enter で記録窓（既定20秒・可変）を開始する。
%    3. 記録中に「模型を手で弾いてきれいに放す → 3秒ほど待って収束 → また弾く」を
%       5回ほど繰り返す（減衰は数秒で収まるので1窓に複数pluckを入れられる）。
%       曲げモードは Fy 方向へ、ねじりは捻りを与えると効率的。
%    4. 収束したら次の記録へ。連番で複数本撮ってよい（q で終了）。
%
%  注意: Leptrino 通信は 32bit Python（config.json の python_exe）を使う。
%        デジボル（風速）・迎角スイープは pluck では不要なので回さない。
%        予備データは output_dir/prelim_pluck/<日付>/ に保存し、本番フラッター実験の
%        フォルダ構造（_ofst / _cXX）には混ぜない。

clc; clear;

%% ---- パス設定（リポジトリ root は2階層上：prelim/pluck_test/）----
repo_root = fileparts(fileparts(fileparts(mfilename('fullpath'))));
addpath(fullfile(repo_root, 'measurement_control'));

%% ---- 設定読み込み ----
config_path = fullfile(repo_root, 'config.json');
if ~isfile(config_path)
    error(['config.json が見つかりません。\n' ...
           'config.json.example をコピーして config.json を作成してください。']);
end
cfg = jsondecode(fileread(config_path));

% 記録秒数（無ければ既定20.0）
if isfield(cfg, 'pluck_measure_sec') && ~isempty(cfg.pluck_measure_sec)
    measure_sec = cfg.pluck_measure_sec;
else
    measure_sec = 20.0;
end
% 原点パルス（無ければ QT_ADL1 の既定にフォールバック）
if isfield(cfg, 'origin_pulse') && ~isempty(cfg.origin_pulse)
    origin_pulse = cfg.origin_pulse;
else
    origin_pulse = [];
end
% 角度整定待ち
if isfield(cfg, 'angle_settle_sec') && ~isempty(cfg.angle_settle_sec)
    settle_sec = cfg.angle_settle_sec;
else
    settle_sec = 1.0;
end

%% ---- 迎角・秒数の入力 ----
in = strtrim(input('迎角 [度]（既定 0）> ', 's'));
if isempty(in)
    target_angle = 0;
else
    target_angle = str2double(in);
    if isnan(target_angle)
        error('迎角は数値で入力してください。');
    end
end

in = strtrim(input(sprintf('計測窓 [秒]（既定 %.1f）> ', measure_sec), 's'));
if ~isempty(in)
    v = str2double(in);
    if ~isnan(v) && v > 0
        measure_sec = v;
    end
end

%% ---- ステージ接続・迎角固定 ----
stage = QT_ADL1(cfg.qt_adl1_port, [], origin_pulse);
cleanupStage = onCleanup(@() delete(stage));

fprintf('\n=== キャリブレーション（原点復帰）===\n');
stage.homeReturn();
stage.moveToAngle(target_angle);
fprintf('迎角を %.4f° に固定しました\n', stage.getAngle());
pause(settle_sec);

%% ---- Leptrino ロガー準備 ----
script_path = fullfile(repo_root, 'leptrino', 'leptrino_server.py');
logger = LeptrinoLogger(cfg.python_exe, script_path, ...
                        cfg.leptrino_port, cfg.force_sensor_size_limit_kb);
cleanupLogger = onCleanup(@() safe_stop_(logger));

%% ---- 保存先 ----
date_str = datestr(now, 'yymmdd');
save_dir = fullfile(cfg.output_dir, 'prelim_pluck', date_str);
if ~isfolder(save_dir)
    mkdir(save_dir);
end
fprintf('保存先: %s\n', save_dir);

%% ---- 記録ループ ----
fprintf(['\n[手順] Enter で %.1f 秒の記録を開始します。記録中に\n' ...
         '       「弾く→3秒ほど待って収束→また弾く」を5回ほど繰り返してください。\n' ...
         '       q で終了します。\n'], measure_sec);

n = 0;
while true
    cmd = strtrim(input(sprintf('\n記録 #%d を開始（Enter）/ 終了（q）> ', n + 1), 's'));
    if strcmpi(cmd, 'q')
        break;
    end
    n = n + 1;

    ts       = datestr(now, 'yyyymmdd_HHMMSS');
    aoa_tag  = sprintf('%+03d', round(target_angle));   % 例 +05 / -20
    fname    = sprintf('%s_pluck_aoa%s_%02d.csv', ts, aoa_tag, n);
    fpath    = fullfile(save_dir, fname);

    fprintf('[計測開始] %s（%.1f 秒）… 弾いてください\n', fname, measure_sec);
    logger.start(fpath, measure_sec);
    pause(0.3);

    last_print = 0;
    while ~logger.isDone()
        pause(0.2);
        el = logger.getElapsedSec();
        if el - last_print >= 1.0
            fprintf('  経過 %2.0f / %2.0f s\n', el, measure_sec);
            last_print = el;
        end
    end
    logger.waitForFinish();
    res = logger.getResult();
    fprintf('[保存] %s（%d サンプル, %.1f 秒, %.0f KB）\n', ...
            fname, res.samples, res.duration_sec, res.size_kb);
end

fprintf('\n[終了] %d 本の記録を保存しました: %s\n', n, save_dir);
clear cleanupLogger cleanupStage;   % onCleanup を明示発火（logger 停止・stage 切断）


%% ================================================================
%  ローカル関数
%% ================================================================
function safe_stop_(logger)
    % 異常終了ガード用: 走行中のときだけロガーを止める（未起動なら何もしない）。
    try
        if logger.isAlive()
            logger.stop();
        end
    catch
    end
end
