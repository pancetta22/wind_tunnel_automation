function name = make_filename(date_str, time_str, phase, ref_angle, suffix, mode)
% MAKE_FILENAME  Windy 実験ファイル名生成ユーティリティ
%
% 書式:
%   name = make_filename(date_str, time_str, phase, ref_angle, suffix, mode)
%
% 入力:
%   date_str   : 実験日（西暦4桁） 'YYYYMMDD'  例: '20260520'
%   time_str   : 計測開始時刻      'HHMMSS'    例: '094953'
%                ('volt_summary' モードでは使用しないので '' でよい)
%   phase      : フェーズ識別子    'Pofst' | 'Mofst' | 'Pdata' | 'Mdata'
%   ref_angle  : 参照迎角 [度]     整数 0〜30
%   suffix     : 計測種別          0 (0°基準) | 1 (目標迎角)
%                ('volt_summary' モードでは使用しないので 0 でよい)
%   mode       : 出力形式          'full' | 'volt_raw' | 'short' | 'volt_summary'
%
% 出力:
%   name : ファイル名文字列（'full'/'volt_raw'/'volt_summary' は拡張子付き）
%
% 使用例:
%   make_filename('20260520','094953','Pofst',12,1,'full')
%     → '20260520_094953_260520_Pofst_12.01.csv'
%
%   make_filename('20260520','094953','Pofst',12,1,'volt_raw')
%     → '20260520_094953_260520_Pofst_12.01_volt_raw.csv'
%
%   make_filename('20260520','094953','Pofst',12,1,'short')
%     → '260520_Pofst_12.01'
%
%   make_filename('20260520','','Pofst',0,0,'volt_summary')
%     → '20260520_Pofst_volt_summary.csv'
%
% ファイル命名規則（SPEC.md Section 4）:
%   フルファイル名: YYYYMMDD_HHMMSS_YYMMDD_[type]_[angle].[suffix].csv
%   短縮名:         YYMMDD_[type]_[angle].[suffix]
%   angle・suffix はともに2桁ゼロ埋め・ドット区切り（既存 calc_force.py との互換）

    % YYYYMMDD → YYMMDD（下6桁）
    if length(date_str) ~= 8
        error('make_filename: date_str は YYYYMMDD 形式（8文字）で指定してください: "%s"', date_str);
    end
    yy_date = date_str(3:end);   % '260520'

    % angle・suffix を 2桁ゼロ埋めしてドット結合
    angle_dot_suffix = sprintf('%02d.%02d', ref_angle, suffix);

    switch mode
        case 'full'
            % 20260520_094953_260520_Pofst_12.01.csv
            name = sprintf('%s_%s_%s_%s_%s.csv', ...
                date_str, time_str, yy_date, phase, angle_dot_suffix);

        case 'volt_raw'
            % 20260520_094953_260520_Pofst_12.01_volt_raw.csv
            name = sprintf('%s_%s_%s_%s_%s_volt_raw.csv', ...
                date_str, time_str, yy_date, phase, angle_dot_suffix);

        case 'short'
            % 260520_Pofst_12.01
            name = sprintf('%s_%s_%s', yy_date, phase, angle_dot_suffix);

        case 'volt_summary'
            % 20260520_Pofst_volt_summary.csv
            name = sprintf('%s_%s_volt_summary.csv', date_str, phase);

        otherwise
            error('make_filename: 未知のモード "%s"。full / volt_raw / short / volt_summary のいずれかを指定してください。', mode);
    end
end
