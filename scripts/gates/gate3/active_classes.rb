# §6.2 classes on the 28 TICON active-with-reference ids: R1->R2 (change B) and R0p(fill R0)->R2 (combined).
require 'csv'; require 'json'
require_relative '../paths'
H = GatePaths::ROOT
G = "#{GatePaths::B}/gate3"
require_relative '../classify'
st = JSON.parse(File.read("#{G}/data/active_ticon_ref.json"))
L = %w[R0p R0 R1 R2 R2b R2c].to_h { |r| [r, CSV.read("#{G}/data/combined/#{r}.csv", headers: true).to_h { |x| [[x['station'], x['window']], x] }] }
tot_active = 19626.0
%w[W1 W2 W3 WY].each do |w|
    [['R1', 'R1->R2'], ['R0pfill', 'R0p->R2']].each do |base, label|
        cls = st.filter_map do |s|
            a = base == 'R0pfill' ? (L['R0p'][[s['id'], w]] || L['R0'][[s['id'], w]]) : L['R1'][[s['id'], w]]
            b = L[ENV.fetch('RX', 'R2c')][[s['id'], w]]
            next unless a && b
            c = Classify.classify(a, b)
            [s, c]
        end
        minor = cls.select { |_, c| c[:cls] == 'minor' }; real = cls.select { |_, c| c[:cls] == 'real' }
        mw = minor.sum { |s, _| s['requests'] }
        puts "#{w} #{label} n=#{cls.size} minor=#{minor.size} (#{(100.0 * minor.size / cls.size).round(1)}%, #{(100 * mw / tot_active).round(2)}% req) real=#{real.size} " \
             "#{real.map { |s, c| "#{s['id']}(#{s['name'][0, 18]},#{s['requests']}req,#{s['gated'] ? 'gated' : 'nongated'}: #{c[:reasons].join(',')})" }.join(' ')} minor: #{minor.map { |s, c| "#{s['id']}(#{s['requests']}) dt=#{c[:dt]&.round(2)} dh=#{c[:dh]&.round(2)}" }.join(' ')}"
    end
end
