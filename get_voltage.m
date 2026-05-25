COM_PORT = 'COM7';

s = serialport(COM_PORT, 9600, ...
    'DataBits',    8,      ...
    'Parity',      'none', ...
    'StopBits',    1,      ...
    'FlowControl', 'none');

configureTerminator(s, 'CR/LF');
s.Timeout = 5;

writeline(s, 'MD?');
disp('コマンド送信完了、応答待ち...');

raw = readline(s);
disp(raw);

clear s;