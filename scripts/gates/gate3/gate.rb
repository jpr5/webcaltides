#!/usr/bin/env ruby
# Gate A+B (plan §6.3 row A+B) on R1 -> R2, reference set, W1–W3.
# ruby gate.rb <R1 runs csv> <R2 runs csv>   e.g. R1,R1_other R2,R2_other
# Writes gate/data/stations_ab.csv and gate/data/gate_ab.json; prints a summary.
require 'csv'; require 'json'
require_relative '../paths'
H = GatePaths::ROOT
G = "#{GatePaths::B}/gate3"
G1 = "#{GatePaths::B}/gate"
require_relative '../classify'
r1_runs, r2_runs = ARGV[0].split(','), ARGV[1].split(',')
WINS = %w[W1 W2 W3]
DS = JSON.parse(File.read("#{H}/builds/B3/ticon.json"))['stations'].to_h { |s| [s['id'], s] }
RECS = JSON.parse(File.read("#{G1}/data/records.json"), allow_nan: true)
FULLREF = JSON.parse(File.read("#{GatePaths::B}/ticon_full_ref.json")).to_h { |x| [x['id'], x] }
load = lambda do |runs, w|
    runs.each_with_object({}) do |r, h|
        CSV.foreach("#{H}/results/#{r}/#{w}/stations.csv", headers: true) do |row|
            next unless row['prov'] == 'ticon'
            abort "dup #{row['station']} #{w}" if h[row['station']]
            h[row['station']] = row
        end
    end
end
mean = ->(a) { a.empty? ? nil : a.sum / a.size.to_f }
med = ->(a) { a.empty? ? nil : (s = a.sort; s.size.odd? ? s[s.size / 2] : (s[s.size / 2 - 1] + s[s.size / 2]) / 2.0) }
fv = ->(r, k) { r[k].nil? || r[k] == '' ? nil : r[k].to_f }

def amp(st, n) = (st['constituents'].find { |c| c['name'] == n } || {})['amp'].to_f
def tags(id, row)
    st = DS[id]; rec = (RECS[id] || [])[0] || {}
    m2, s2, k1, o1, m4 = %w[M2 S2 K1 O1 M4].map { |n| amp(st, n) }
    f = (k1 + o1) / (m2 + s2)
    rng = 2 * (m2 + s2)
    lat, lon = st['lat'], st['lon']; c = st['region']
    region =
        if %w[USA PRI].include?(c) || (c.nil? && lon < -60 && lat > 15)
            if lat > 51 && lon < -129 then 'Alaska'
            elsif lon < -140 || lon > 0 then 'Pacific islands'
            elsif lon < -100 then 'US-Pacific'
            else 'US-Atlantic/Gulf' end
        elsif %w[PLW FJI MHL ASM].include?(c) || (c.nil? && (lon > 120 || lon < -140)) then 'Pacific islands'
        elsif c.nil? && lat > 45 && lon > -15 && lon < 32 then 'NW Europe'
        elsif c == 'CAN' then 'Canada'
        elsif %w[GBR IRL].include?(c) then 'UK/Ireland'
        elsif %w[NLD DEU NOR DNK FRA BEL SWE].include?(c) then 'NW Europe'
        elsif %w[NZL AUS].include?(c) then 'Australia/NZ'
        elsif %w[MEX PAN CRI BHS].include?(c) then 'C. America/Caribbean'
        elsif c == 'ECU' then 'S. America'
        else "other:#{c}" end
    regime = f < 0.25 ? 'semidiurnal' : f < 1.5 ? 'mixed semidiurnal' : f < 3 ? 'mixed diurnal' : 'diurnal'
    shallow = m2.zero? ? 'n/a' : (r = m4 / m2) < 0.05 ? '<0.05' : r < 0.15 ? '0.05-0.15' : '>0.15'
    rngb = rng < 1 ? '<1 m' : rng < 3 ? '1-3 m' : '>3 m'
    name = st['name'].to_s
    estuary = name =~ /River|Creek|Bridge|Sperrwerk|Elbe|Weser|Ems|Hafen|Bayou|Canal|Inlet/i ? 'yes' : 'no'
    yrs = rec['years']
    reclen = yrs.nil? ? 'n/a' : yrs < 1 ? '<1 y' : yrs < 5 ? '1-5 y' : yrs < 19 ? '5-19 y' : '>=19 y'
    kind = row['src'] == 'noaa' ? "noaa_#{row['kind']}" : row['src'] == 'chs' ? "chs_#{row['kind']}" : row['src']
    full = (row['src'] == 'noaa' && row['kind'] == 'R') || (row['src'] == 'chs' && row['kind'] == 'PERMANENT') ||
           %w[rws bsh kartverket imi].include?(row['src'])
    { 'name' => name, 'region' => region, 'regime' => regime, 'F' => f.round(3), 'shallow' => shallow, 'm4m2' => (m2.zero? ? nil : (m4 / m2).round(3)),
      'range' => rngb, 'range_m' => rng.round(2), 'estuary' => estuary, 'reclen' => reclen, 'rec_years' => yrs, 'rec_nobs' => rec['n_obs'],
      'rec_src' => rec['src'], 'siblings' => (RECS[id] || []).size, 'ref_kind' => kind, 'gated' => full, 'microtidal' => rng < 0.3 }
end

out = CSV.open("#{G}/data/stations_ab.csv", 'w')
cols = %w[window station gated ref_kind src ref_id name region regime F shallow range estuary reclen rec_years rec_src siblings
          r1_t_mae r2_t_mae d_t r1_h r2_h r1_rng r2_rng r1_p95 r2_p95 r1_miss r2_miss r1_bias r2_bias class improved_t meaningful reasons]
out << cols
res = {}
WINS.each do |w|
    a = load[r1_runs, w]; b = load[r2_runs, w]
    ids = (a.keys & b.keys).sort
    rows = ids.map do |id|
        ra, rb = a[id], b[id]
        abort "ref mismatch #{id}" unless ra['ref_id'] == rb['ref_id'] && ra['n_ref'] == rb['n_ref']
        c = Classify.classify(ra, rb)
        t = tags(id, ra)
        h = t.merge('station' => id, 'src' => ra['src'], 'ref_id' => ra['ref_id'], 'r1_t_mae' => fv[ra, 't_mae'], 'r2_t_mae' => fv[rb, 't_mae'],
                    'r1_h' => fv[ra, 'h_mae_al'], 'r2_h' => fv[rb, 'h_mae_al'], 'r1_rng' => fv[ra, 'range_mae'], 'r2_rng' => fv[rb, 'range_mae'],
                    'r1_p95' => fv[ra, 't_p95'], 'r2_p95' => fv[rb, 't_p95'], 'r1_miss' => fv[ra, 'missed_per100'], 'r2_miss' => fv[rb, 'missed_per100'],
                    'r1_bias' => fv[ra, 't_bias'], 'r2_bias' => fv[rb, 't_bias'],
                    'class' => c[:cls], 'improved_t' => c[:improved_t], 'meaningful' => c[:meaningful], 'reasons' => c[:reasons].join('; '),
                    'd_t' => c[:dt])
        out << cols.map { |k| k == 'window' ? w : (v = h[k]).is_a?(Float) ? format('%.3f', v) : v }
        h
    end
    summ = lambda do |rs0|
        rs = rs0.select { |r| r['r1_t_mae'] && r['r2_t_mae'] }
        t1 = rs.map { |r| r['r1_t_mae'] }; t2 = rs.map { |r| r['r2_t_mae'] }
        h1 = rs.filter_map { |r| r['r1_h'] }; h2 = rs.filter_map { |r| r['r2_h'] }
        g1 = rs.filter_map { |r| r['r1_rng'] }; g2 = rs.filter_map { |r| r['r2_rng'] }
        k = rs.map { |r| r['class'] }.tally
        { 'n' => rs.size, 'r1_mean' => mean[t1], 'r2_mean' => mean[t2], 'ratio' => mean[t1] && mean[t2] / mean[t1],
          'r1_median' => med[t1], 'r2_median' => med[t2], 'median_ratio' => med[rs.map { |r| r['r1_t_mae'] > 0 ? r['r2_t_mae'] / r['r1_t_mae'] : 1.0 }],
          'improved' => rs.count { |r| r['improved_t'] }, 'meaningful' => rs.count { |r| r['meaningful'] },
          'real' => k['real'].to_i, 'minor' => k['minor'].to_i, 'meaningless' => k['meaningless'].to_i, 'no_change' => k['no_change'].to_i,
          'h_r1' => mean[h1], 'h_r2' => mean[h2], 'rng_r1' => mean[g1], 'rng_r2' => mean[g2],
          'd_mean' => mean[t2] && mean[t2] - mean[t1] }
    end
    gated = rows.select { |r| r['gated'] }
    offs = rows.reject { |r| r['gated'] }
    strata = {}
    %w[region regime estuary reclen ref_kind shallow range microtidal].each do |ax|
        gated.group_by { |r| r[ax].to_s }.sort.each { |v, rs| strata["#{ax}=#{v}"] = summ[rs] }
    end
    off_strata = {}
    offs.group_by { |r| r['ref_kind'] }.sort.each { |v, rs| off_strata[v] = summ[rs] }
    res[w] = { 'gated' => summ[gated], 'offset' => summ[offs], 'all' => summ[rows], 'strata' => strata, 'offset_strata' => off_strata,
               'unpaired' => ((a.keys | b.keys) - ids).size, 'nil_t_mae' => rows.reject { |r| r['r1_t_mae'] && r['r2_t_mae'] }.map { |r| [r['station'], r['src'], r['r1_t_mae'], r['r2_t_mae']] }, 'gated_ids_expected' => FULLREF.size,
               'gated_missing' => FULLREF.keys - gated.map { |r| r['station'] },
               'real_list' => gated.select { |r| r['class'] == 'real' }.map { |r| r.slice('station', 'src', 'ref_id', 'name', 'r1_t_mae', 'r2_t_mae', 'reasons', 'regime', 'reclen', 'rec_years', 'region') },
               'minor_list' => gated.select { |r| r['class'] == 'minor' }.map { |r| r.slice('station', 'src', 'r1_t_mae', 'r2_t_mae', 'r1_h', 'r2_h') },
               'offset_real_list' => offs.select { |r| r['class'] == 'real' }.map { |r| r.slice('station', 'src', 'ref_id', 'ref_kind', 'name', 'r1_t_mae', 'r2_t_mae', 'reasons') } }
end
out.close
File.write("#{G}/data/gate_ab.json", JSON.pretty_generate(res))
WINS.each do |w|
    g = res[w]['gated']; o = res[w]['offset']
    puts "#{w} gated n=#{g['n']} R1=#{g['r1_mean'].round(3)} R2=#{g['r2_mean'].round(3)} ratio=#{g['ratio'].round(3)} improved=#{g['improved']} (#{(100.0 * g['improved'] / g['n']).round(1)}%) real=#{g['real']} (#{(100.0 * g['real'] / g['n']).round(2)}%) minor=#{g['minor']} (#{(100.0 * g['minor'] / g['n']).round(2)}%) h #{g['h_r1'].round(2)}->#{g['h_r2'].round(2)} rng #{g['rng_r1'].round(2)}->#{g['rng_r2'].round(2)} missing=#{res[w]['gated_missing'].size}"
    puts "#{w} offset n=#{o['n']} ratio=#{o['ratio'].round(3)} real=#{o['real']} minor=#{o['minor']}"
    bad = res[w]['strata'].select { |_, s| s['n'] >= 10 && s['d_mean'] > 1.0 }
    puts "#{w} strata >=10 worse >1 min: #{bad.map { |k, s| "#{k}(n=#{s['n']} #{s['r1_mean'].round(2)}->#{s['r2_mean'].round(2)})" }.join(', ')}"
end
