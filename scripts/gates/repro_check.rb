#!/usr/bin/env ruby
# §6.1 reproduction check: mean per-station time MAE (and height MAE) of a run over the
# 19 retest stations vs the plan's R0 numbers (time within 0.2 min).
require 'csv'
H = File.expand_path('..', __dir__)
run = ARGV[0] || 'R0_repro'
PLAN = { 'RT1' => [10.5, 7.9], 'W2' => [9.25, 6.5] }.freeze
ok = true
PLAN.each do |w, (pt, ph)|
    rows = CSV.read("#{H}/results/#{run}/#{w}/stations.csv", headers: true)
    t = rows.map { |r| r['t_mae'].to_f }.sum / rows.size
    ev = CSV.read("#{H}/results/#{run}/#{w}/events.csv", headers: true).group_by { |r| r['station'] }
    hraw = ev.values.map { |es| es.sum { |r| r['dh_cm'].to_f.abs } / es.size }.then { |a| a.sum / a.size }
    h = rows.map { |r| r['h_mae_al'].to_f }.sum / rows.size
    pass = (t - pt).abs <= 0.2
    ok &&= pass
    printf("%s %s: n=%d time MAE mean %.2f (plan %.2f, |d|=%.2f) %s | raw height MAE mean %.2f cm (plan %.1f) | bias-removed %.2f cm\n",
           run, w, rows.size, t, pt, (t - pt).abs, pass ? 'PASS' : 'FAIL', hraw, ph, h)
end
puts "reproduction: #{ok ? 'PASS' : 'FAIL'}"
