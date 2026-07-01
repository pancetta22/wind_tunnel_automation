function venv_python = setup_postprocess_venv(root, python_exe_64)
%% setup_postprocess_venv.m
% post_process の仮想環境を用意して venv の python パスを返す共有関数。
%   run_postprocess.m（定常空力）と flutter_run_postprocess.m（フラッター）の
%   両方から呼ばれる。
%
% 動作:
%   0. 既存の使える venv（post_process/venv または .venv）があればそれを使う
%   1. 無ければ 64bit Python で post_process/venv を作成 → requirements 導入
%   2. パッケージが足りなければ再インストール
%   3. それでも直らなければ venv フォルダを自動削除して作り直す
%  → 半端な venv / 32bit で作られた古い venv でも自動で復旧する。

    req_path = fullfile(root, 'post_process', 'requirements.txt');

    % --- 0. 既存の使える venv を優先（.venv も含めて探す）---
    for name = {'venv', '.venv'}
        cand = venv_python_path_(fullfile(root, 'post_process', name{1}));
        if isfile(cand) && venv_packages_ok_(cand)
            venv_python = cand;
            return
        end
    end

    % --- 以降は post_process/venv を対象に作成・修復する ---
    venv_dir    = fullfile(root, 'post_process', 'venv');
    venv_python = venv_python_path_(venv_dir);

    if isempty(python_exe_64) || ~isfile(python_exe_64)
        error(['[後処理] 使える venv が無く、python_exe_64 も見つかりません。\n' ...
               '  config.json の "python_exe_64" に 64bit Python(.exe) の実体パスを設定してください。\n' ...
               '  （.lnk ショートカットは不可）\n  指定値: %s'], python_exe_64);
    end

    % 1回目: 既存 venv で試す / 2回目: 削除して作り直して試す
    for attempt = 1:2

        % --- 2回目は venv フォルダを丸ごと削除してから作り直す ---
        if attempt == 2
            fprintf('[後処理] venv が正常に使えないため、削除して作り直します...\n');
            if isfolder(venv_dir)
                try
                    rmdir(venv_dir, 's');
                catch ME
                    error(['[後処理] venv フォルダを削除できませんでした: %s\n' ...
                           '  手動で次を削除してください: %s'], ME.message, venv_dir);
                end
            end
        end

        % --- venv 本体が無ければ作成 ---
        if ~isfile(venv_python)
            fprintf('[後処理] 仮想環境を作成しています（64bit Python）: %s\n', venv_dir);
            fprintf('         使用 Python: %s\n', python_exe_64);
            [st, out] = system(sprintf('"%s" -m venv "%s"', python_exe_64, venv_dir));
            if ~isempty(strtrim(out)), fprintf('%s\n', out); end
            if st ~= 0 || ~isfile(venv_python)
                if attempt == 1, continue; end   % 作成失敗 → 作り直しへ
                error(['[後処理] 仮想環境の作成に失敗しました（終了コード %d）。\n' ...
                       '  config.json の "python_exe_64" に 64bit Python(.exe) の正しいパスを設定してください。\n' ...
                       '  指定値: %s'], st, python_exe_64);
            end
        end

        % --- 主要パッケージが導入済みか確認 ---
        if venv_packages_ok_(venv_python)
            return   % 正常 → そのまま使う
        end

        % --- 不足 → pip でインストール ---
        fprintf('[後処理] 必要パッケージをインストールしています...\n');
        system(sprintf('"%s" -m pip install --upgrade pip -q', venv_python));
        [~, out] = system(sprintf('"%s" -m pip install -r "%s"', venv_python, req_path));
        if ~isempty(strtrim(out)), fprintf('%s\n', out); end

        % --- インストール後の再確認 ---
        if venv_packages_ok_(venv_python)
            fprintf('[後処理] パッケージのセットアップ完了。\n\n');
            return   % 正常
        end

        % --- まだダメ ---
        if attempt == 1
            fprintf('[後処理] 既存 venv では復旧できませんでした。作り直します。\n');
            % continue（次の attempt で削除→再作成）
        else
            error(['[後処理] venv を作り直してもパッケージを導入できませんでした。\n' ...
                   '  64bit Python のパス（python_exe_64）と pip のネットワーク接続を確認してください。\n' ...
                   '  Python: %s'], python_exe_64);
        end
    end
end

function p = venv_python_path_(venv_dir)
    if ispc
        p = fullfile(venv_dir, 'Scripts', 'python.exe');
    else
        p = fullfile(venv_dir, 'bin', 'python');
    end
end

function ok = venv_packages_ok_(venv_python)
    % 主要パッケージ（pandas / python-pptx）が導入済みか（pip show で確認）
    [s_pd, ~] = system(sprintf('"%s" -m pip show pandas',      venv_python));
    [s_px, ~] = system(sprintf('"%s" -m pip show python-pptx', venv_python));
    ok = (s_pd == 0 && s_px == 0);
end
