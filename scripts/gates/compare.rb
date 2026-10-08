#!/usr/bin/env ruby
# Usage: ruby tools/compare.rb <runA> <runB> [--rn <rn_run>] [--windows W1,W2,W3]
# Paired per-station comparison (§5.2, §6.2) and §6.3 row-A reference-set gates on runB, per window.
# Writes results/compare_<A>_<B>.csv and results/compare_<A>_<B>.md
require 'csv'
require 'json'
require_relative 'classify'
require_relative 'paths'
H = GatePaths::ROOT
a_run, b_run = ARGV[0], ARGV[1]
rn = (i = ARGV.index('--rn')) ? ARGV[i + 1] : nil
wins = ((i = ARGV.index('--windows')) ? ARGV[i + 1] : 'W1,W2,W3').split(',')
load_st = ->(run, w) { (p = "#{H}/results/#{run}/#{w}/stations.csv") && File.exist?(p) ? CSV.read(p, headers: true).to_h { |r| [r['station'], r] } : nil }
med = ->(a) { a.empty? ? nil : (s = a.sort; s.size.odd? ? s[s.size / 2] : (s[s.size / 2 - 1] + s[s.size / 2]) / 2.0) }
mean = ->(a) { a.empty? ? nil : a.sum / a.size }
fm = ->(v, d = 2) { v.nil? ? 'n/a' : format("%.#{d}f", v) }
stratum = lambda do |r|
    if r['prov'] == 'xtide'
        r['sub'] == 'true' ? (r['kind'] == 'S' ? 'xtide_S' : 'xtide_sub_other') : (r['kind'] == 'R' ? 'xtide_R' : 'xtide_ref_other')
    else
        "ticon_#{r['src']}#{r['src'] == 'noaa' ? "_#{r['kind']}" : ''}"
    end
end
out = CSV.open("#{H}/results/compare_#{a_run}_#{b_run}.csv", 'w')
out << %w[window station stratum src ref_id a_t_mae b_t_mae d_t_mae a_h_mae b_h_mae d_h_mae a_t_p95 b_t_p95 a_missed100 b_missed100 class improved_t meaningful reasons]
md = ["# compare #{a_run} -> #{b_run}#{rn ? " (engine control: #{rn})" : ''}", '']
gates = Hash.new { |h, k| h[k] = {} }
wins.each do |w|
    a = load_st[a_run, w] or abort "missing #{a_run}/#{w}"
    b = load_st[b_run, w] or abort "missing #{b_run}/#{w}"
    ids = (a.keys & b.keys).sort
    unpaired = (a.keys | b.keys) - ids
    cls = {}
    ids.each do |id|
        ra, rb = a[id], b[id]
        abort "ref mismatch #{id}" if ra['ref_id'] != rb['ref_id']
        c = Classify.classify(ra, rb); cls[id] = c
        out << [w, id, stratum[ra], ra['src'], ra['ref_id'], ra['t_mae'], rb['t_mae'], fm[c[:dt], 3], ra['h_mae_al'], rb['h_mae_al'], fm[c[:dh], 3],
                ra['t_p95'], rb['t_p95'], ra['missed_per100'], rb['missed_per100'], c[:cls], c[:improved_t], c[:meaningful], c[:reasons].join('; ')]
    end
    md << "## #{w}" << '' << "paired stations: #{ids.size}; unpaired: #{unpaired.size}" << ''
    md << '| stratum | n | A mean t_mae | B mean t_mae | B median t_mae | A mean h_mae | B mean h_mae | improved | meaningful | no change | meaningless | minor | real | sign-test p |'
    md << '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|'
    ids.group_by { |id| stratum[a[id]] }.sort.each do |s, sids|
        ta = sids.map { |id| a[id]['t_mae'].to_f }; tb = sids.map { |id| b[id]['t_mae'].to_f }
        ha = sids.filter_map { |id| a[id]['h_mae_al']&.then { |v| v.empty? ? nil : v.to_f } }
        hb = sids.filter_map { |id| b[id]['h_mae_al']&.then { |v| v.empty? ? nil : v.to_f } }
        k = sids.map { |id| cls[id][:cls] }.tally
        pos = sids.count { |id| (cls[id][:dt] || 0) < 0 }; neg = sids.count { |id| (cls[id][:dt] || 0) > 0 }
        md << "| #{s} | #{sids.size} | #{fm[mean[ta]]} | #{fm[mean[tb]]} | #{fm[med[tb]]} | #{fm[mean[ha]]} | #{fm[mean[hb]]} | #{sids.count { |id| cls[id][:improved_t] }} | " \
              "#{sids.count { |id| cls[id][:meaningful] }} | #{k['no_change'].to_i} | #{k['meaningless'].to_i} | #{k['minor'].to_i} | #{k['real'].to_i} | #{fm[Classify.sign_test(pos, neg), 4]} |"
    end
    md << ''
    # §6.3 row A, reference set, on runB
    g = gates[w]
    sel = ->(st) { ids.select { |id| stratum[a[id]] == st } }
    xr = sel['xtide_R'].map { |id| b[id]['t_mae'].to_f }
    g['xtide_R median t_mae <= 1 min'] = [med[xr], med[xr] && med[xr] <= 1.0, xr.size]
    share = xr.empty? ? nil : xr.count { |v| v <= 2.0 }.fdiv(xr.size)
    g['xtide_R share t_mae <= 2 min >= 95%'] = [share && 100 * share, share && share >= 0.95, xr.size]
    xs = sel['xtide_S'].map { |id| b[id]['t_mae'].to_f }
    g['xtide_S median t_mae <= 2 min'] = [med[xs], med[xs] && med[xs] <= 2.0, xs.size]
    tic = ids.select { |id| a[id]['prov'] == 'ticon' }
    tma, tmb = mean[tic.map { |id| a[id]['t_mae'].to_f }], mean[tic.map { |id| b[id]['t_mae'].to_f }]
    g["ticon mean t_mae not worse than #{a_run} (A=#{fm[tma]})"] = [tmb, tma && tmb && tmb <= tma, tic.size]
    real = tic.select { |id| cls[id][:cls] == 'real' }
    g['ticon real regressions <= 2%'] = [tic.empty? ? nil : 100.0 * real.size / tic.size, tic.empty? ? nil : real.size <= 0.02 * tic.size, tic.size]
    if rn && (r = load_st[rn, w])
        rt = r.values.map { |x| x['t_mae'].to_f }; rh = r.values.filter_map { |x| x['h_mae_al'].to_s.empty? ? nil : x['h_mae_al'].to_f }
        g['engine control mean t_mae <= 0.5 min'] = [mean[rt], mean[rt] && mean[rt] <= 0.5, rt.size]
        g['engine control mean h_mae <= 0.5 cm'] = [mean[rh], mean[rh] && mean[rh] <= 0.5, rh.size]
    end
    md << "### #{w} gates (row A, reference set, on #{b_run})" << '' << '| rule | value | n | verdict |' << '|---|---|---|---|'
    g.each { |k, (v, ok, n)| md << "| #{k} | #{fm[v]} | #{n} | #{ok.nil? ? 'n/a' : ok ? 'PASS' : 'FAIL'} |" }
    md << ''
    %w[real minor].each do |c|
        l = ids.select { |id| cls[id][:cls] == c }
        md << "#{c} regressions (#{l.size}): " + l.first(200).map { |id| "#{id}(#{stratum[a[id]]} #{a[id]['t_mae']}->#{b[id]['t_mae']}#{cls[id][:reasons].empty? ? '' : ' ' + cls[id][:reasons].join(',')})" }.join(', ')
        md << ''
    end
end
out.close
all_ok = gates.values.flat_map { |g| g.values.map { |_, ok, _| ok } }
md << "## Overall row-A reference-set verdict (each window must hold): #{all_ok.include?(false) ? 'FAIL' : all_ok.include?(nil) ? 'INCOMPLETE' : 'PASS'}"
File.write("#{H}/results/compare_#{a_run}_#{b_run}.md", md.join("\n") + "\n")
puts md.last
