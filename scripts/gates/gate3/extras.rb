# Extras for gateB3-report: per-source means, real-regression split (reference lacks TICON constituents vs other),
# B2 vs B3 real lists, spot stations. ruby extras.rb   (adapted from gate2/extras.rb)
require 'csv'; require 'json'; require 'yaml'
H = File.expand_path('~/.local/share/copilotkit/cr/webcaltides-harmonics-eval')
GB = File.expand_path('~/.local/share/copilotkit/cr/webcaltides-harmonics-b')
DS = JSON.parse(File.read("#{H}/builds/B3/ticon.json"))['stations'].to_h { |s| [s['id'], s] }
ld = ->(d) { CSV.read("#{GB}/#{d}/data/stations_ab.csv", headers: true).to_h { |r| [[r['window'], r['station']], r] } }
a = ld['gate']; b2 = ld['gate2']; b = ld['gate3']   # R1 -> R2 (B), R1 -> R2b (B2), R1 -> R2c (B3)
WT = File.expand_path('~/.local/state/worktrees/webcaltides/harmonics-b2-fixes')
FB = YAML.safe_load_file("#{WT}/scripts/ticon_fallback_stations.yml")['stations'].map { _1['id'] }
mean = ->(x) { x.empty? ? nil : x.sum / x.size }
f = ->(v) { v.nil? ? '—' : format('%.2f', v) }
puts '## per-source mean time MAE (gated), R1 / R2 (B) / R2b (B2) / R2c (B3), height MAE same order, real count B/B2/B3'
%w[bsh kartverket noaa_R rws imi chs_PERMANENT].each do |k|
    %w[W1 W2 W3].each do |w|
        rs = b.values.select { |r| r['window'] == w && r['gated'] == 'true' && r['ref_kind'] == k && r['r1_t_mae'] && r['r2_t_mae'] }
        r2 = rs.map { |r| a[[w, r['station']]] }.compact
        r2b = rs.map { |r| b2[[w, r['station']]] }.compact
        puts "#{k} #{w} n=#{rs.size} (B n=#{r2.size}, B2 n=#{r2b.size}) t: #{f[mean[rs.map { |r| r['r1_t_mae'].to_f }]]} / #{f[mean[r2.map { |r| r['r2_t_mae'].to_f }]]} / #{f[mean[r2b.map { |r| r['r2_t_mae'].to_f }]]} / #{f[mean[rs.map { |r| r['r2_t_mae'].to_f }]]}" \
             "  h: #{f[mean[rs.filter_map { |r| r['r1_h']&.to_f }]]} / #{f[mean[r2.filter_map { |r| r['r2_h']&.to_f }]]} / #{f[mean[r2b.filter_map { |r| r['r2_h']&.to_f }]]} / #{f[mean[rs.filter_map { |r| r['r2_h']&.to_f }]]}" \
             "  real #{r2.count { |r| r['class'] == 'real' }}/#{r2b.count { |r| r['class'] == 'real' }}/#{rs.count { |r| r['class'] == 'real' }}"
    end
end
NM = { 'LAM2' => 'LDA2', 'RHO' => 'RHO1' }
def ref_missing(id, ref)
    p = "#{H}/refs/noaa/harcon/harcon_#{ref}.json"
    return nil unless File.exist?(p)
    h = JSON.parse(File.read(p))['HarmonicConstituents'] or return nil
    have = h.select { |c| c['amplitude'].to_f > 0 }.map { |c| NM[c['name']] || c['name'] }
    DS[id]['constituents'].select { |c| c['amp'] >= 0.01 && !have.include?(c['name']) }.map { |c| "#{c['name']} #{(c['amp'] * 100).round(1)}" }
end
puts "\n## real regressions R1 -> R2c (gated), split"
all_real = {}
%w[W1 W2 W3].each do |w|
    rs = b.values.select { |r| r['window'] == w && r['gated'] == 'true' && r['class'] == 'real' }
    lack, other = rs.partition do |r|
        r['src'] == 'noaa' && (m = ref_missing(r['station'], r['ref_id'])) && m.any? { |x| x =~ /\A(MN4|MS4|MM|MSF|S1|M6|SA|SSA|MF|S4|M3|MKS2|M8|N4|S3) / }
    end
    puts "#{w} real=#{rs.size} ref-lacks=#{lack.size} other=#{other.size} by src other: #{other.map { |r| r['src'] }.tally} ; fallback ids among real: #{rs.count { |r| FB.include?(r['station']) }}"
    rs.each { |r| (all_real[r['station']] ||= { 'r' => r, 'w' => [] })['w'] << w }
end
puts "\n## distinct real stations (any window): #{all_real.size}"
all_real.sort_by { |id, v| [v['r']['src'], id] }.each do |id, v|
    r = v['r']; m = r['src'] == 'noaa' ? ref_missing(id, r['ref_id']) : nil
    t = %w[W1 W2 W3].map { |w| x = b[[w, id]]; x ? "#{x['r1_t_mae'].to_f.round(1)}→#{x['r2_t_mae'].to_f.round(1)}" : '—' }.join(' ')
    puts "#{id}#{FB.include?(id) ? ' [fallback]' : ''} | #{r['src']} #{r['ref_id']} | #{r['name'][0, 28]} | #{v['w'].join(',')} | #{t} | #{r['reasons'][0, 70]} | ref lacks: #{m ? m.first(6).join(', ') : 'n/a'}"
end
puts "\n## B2 real vs B3 real (distinct, any window)"
br = b2.values.select { |r| r['gated'] == 'true' && r['class'] == 'real' }.map { |r| r['station'] }.uniq
puts "B2=#{br.size} B3=#{all_real.size} cleared=#{(br - all_real.keys).size} (of which fallback #{(br - all_real.keys).count { FB.include?(_1) }}) new=#{(all_real.keys - br).size} new ids: #{(all_real.keys - br).join(' ')}"
puts "cleared ids: #{(br - all_real.keys).join(' ')}"
puts "\n## fallback ids in the gated set: class per window R1 -> R2c (B3 = master constants on A engine)"
FB.each do |id|
    row = %w[W1 W2 W3].map { |w| (y = b[[w, id]]) ? "#{y['r1_t_mae'].to_f.round(1)}→#{y['r2_t_mae'].to_f.round(1)} #{y['class']}" : '—' }
    next if row.all?('—')
    puts "#{id} #{DS[id]['name'][0, 30]}: #{row.join(' ; ')}"
end
puts "\n## spot stations (time MAE min W1/W2/W3 and h cm) R1 | R2b (B2) | R2c (B3)"
spot = { 'T53cdddf' => 'Helgoland', 'Td3e066d' => 'Bremerhaven', 'T4b9bc4c' => 'Trondheim', 'T1367d55' => 'Krautsandreede',
         'T43c901e' => 'Elmshorn', 'T87a0f24' => 'Mayport', 'Tb049af2' => 'Portland ME', 'Tfcfe8a4' => 'Sausalito',
         'T28f3eda' => 'Pawleys', 'Tc426ebd' => 'Clarendon', 'T7fde2a6' => 'South Santee', 'T54ed2fe' => 'Heesbeen', 'Tf2bcd2c' => 'Den Helder' }
spot.each do |id, n|
    row = %w[W1 W2 W3].map do |w|
        x = b2[[w, id]]; y = b[[w, id]]
        next '—' unless x && y
        "#{x['r1_t_mae'].to_f.round(1)}|#{x['r2_t_mae'].to_f.round(1)}|#{y['r2_t_mae'].to_f.round(1)} (h #{x['r1_h'].to_f.round(1)}|#{x['r2_h'].to_f.round(1)}|#{y['r2_h'].to_f.round(1)}) #{x['class']}->#{y['class']}"
    end
    puts "#{id} #{n}: #{row.join(' ; ')}"
end
