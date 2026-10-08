# Combined row (§6.3 rule 15) on the gated TICON active-with-ref ids: request-weighted time MAE vs R0p.
# ruby combined_row.rb <RX>   (R2 reproduces gate/gateB-report.md row 15)
require 'csv'; require 'json'
require_relative '../paths'
G = "#{GatePaths::B}/gate3"
rx = ARGV[0] || 'R2c'
st = JSON.parse(File.read("#{G}/data/active_ticon_ref.json")).select { |s| s['gated'] }
L = ['R0p', 'R0', rx].to_h { |r| [r, CSV.read("#{G}/data/combined/#{r}.csv", headers: true).to_h { |x| [[x['station'], x['window']], x] }] }
med = ->(a) { s = a.sort; s.size.odd? ? s[s.size / 2] : (s[s.size / 2 - 1] + s[s.size / 2]) / 2.0 }
%w[W1 W2 W3 WY].each do |w|
    [['R0p only', false], ['R0p+R0 fill', true]].each do |label, fill|
        [nil, 'T9162534'].each do |excl|
            rows = st.filter_map do |s|
                next if s['id'] == excl
                a = L['R0p'][[s['id'], w]] || (fill ? L['R0'][[s['id'], w]] : nil)
                b = L[rx][[s['id'], w]]
                next unless a && b && a['t_mae'] && b['t_mae']
                [s['requests'].to_f, a['t_mae'].to_f, b['t_mae'].to_f]
            end
            wt = rows.sum(&:first)
            a = rows.sum { |q, x, _| q * x } / wt; b = rows.sum { |q, _, y| q * y } / wt
            per = rows.map { |_, x, y| x > 0 ? 1 - y / x : 0 }
            puts format('%s %-12s %-14s n=%2d weighted %.1f%%  median %.1f%%  improved %.0f%%', w, label, excl ? 'w/o Auckland' : 'all', rows.size,
                        100 * (1 - b / a), 100 * med[per], 100.0 * per.count(&:positive?) / rows.size)
        end
    end
end
