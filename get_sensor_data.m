function data = get_sensor_data()
    config_path = fullfile(fileparts(mfilename('fullpath')), 'config.json');

    if ~isfile(config_path)
        error(['config.json が見つかりません。\n' ...
               'config.json.example をコピーして config.json を作成し、\n' ...
               '各自の環境に合わせてパスを設定してください。']);
    end

    cfg = jsondecode(fileread(config_path));
    PYTHON_EXE = cfg.python_exe;
    PORT       = cfg.leptrino_port;

    SCRIPT = fullfile(fileparts(mfilename('fullpath')), 'leptrino', 'leptrino_server.py');

    cmd = sprintf('"%s" "%s" --port %d', PYTHON_EXE, SCRIPT, PORT);
    [status, output] = system(cmd);

    if status ~= 0
        error("センサ取得失敗: %s", output);
    end

    data = jsondecode(output);
end