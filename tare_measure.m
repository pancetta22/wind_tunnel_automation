%% tare_measure.m
%  データロガー風「ゼロ点基準」6軸力測定ツール（診断・セットアップ用）
%
%  旧CFSLGRロガーのように、まずゼロ点（基準）を取得し、以降その基準からの
%  相対値で6軸力を表示する。get_sensor_data()（leptrino_server.py の avg
%  モード = 約1秒平均）を用いる。
%
%  主な用途:
%    - Fy軸など特定方向の荷重テスト（タレ → 既知荷重を負荷 → 読み値確認）
%    - 計測前のゼロ点ドリフト確認・セットアップ
%
%  使い方:
%    1. 実行 → 基準状態でゼロ点を取得
%    2. Enter を押すたびに、ゼロ基準の6軸力を1回測定して表示
%       'z'+Enter で再ゼロ、'q'+Enter で終了
%
%  ※注意（重要）:
%    これは「測定・診断用」ツールです。本計測 run_experiment では各迎角で
%    Pofst を減算しているため、タレ値は最終的な空力係数では相殺されます
%    （= タレを入れても Cl,Cd 等の結果は変わりません）。

clc;
fprintf('=== ゼロ点基準 6軸力測定 (データロガー風) ===\n\n');

% ---- ゼロ点取得 ----
F_zero = capture_zero_();

% ---- 測定ループ ----
fprintf('\n[操作] Enter=測定  /  z=再ゼロ  /  q=終了\n\n');
labels = {'Fx','Fy','Fz','Mx','My','Mz'};
units  = {'N','N','N','Nm','Nm','Nm'};
while true
    cmd = input('> ', 's');
    if strcmpi(cmd, 'q'); break; end
    if strcmpi(cmd, 'z'); F_zero = capture_zero_(); continue; end

    F = read_force_() - F_zero;   % ゼロ基準の6軸力
    fprintf('   ');
    for i = 1:6
        fprintf('%s=%+8.4f %-2s  ', labels{i}, F(i), units{i});
    end
    fprintf('|  |F|=%.4f N\n', norm(F(1:3)));
end
fprintf('終了しました。\n');


% ================= ローカル関数 =================
function F = read_force_()
    % 6軸力を1回取得して [Fx Fy Fz Mx My Mz] で返す
    % （get_sensor_data は leptrino_server.py avg モード ≒ 200サンプル平均）
    d = get_sensor_data();
    F = [d.Fx, d.Fy, d.Fz, d.Mx, d.My, d.Mz];
end

function F0 = capture_zero_()
    % 基準（ゼロ点）を取得する。安定化のため複数回平均。
    input('ゼロ点を取得します。基準状態にして Enter: ');
    fprintf('取得中...');
    N = 3;
    buf = zeros(N, 6);
    for k = 1:N
        buf(k, :) = read_force_();
    end
    F0 = mean(buf, 1);
    fprintf(' 完了\n');
    fprintf('   ゼロ点: Fx=%.3f Fy=%.3f Fz=%.3f Mx=%.4f My=%.4f Mz=%.4f\n', F0);
end
