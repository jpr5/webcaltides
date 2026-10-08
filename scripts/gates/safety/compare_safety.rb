#!/usr/bin/env ruby
# tools/safety/compare_safety.rb <R0 run> <Rx run> [confirmed_ids.json]
# New (station, day, rule) violations vs R0, old->new shift per station, plan 6.3 row A active-set verdict.
# confirmed_ids.json: optional ["T…", …] ids whose >30 min shift a reference confirms.
require 'json'; require 'fileutils'; require 'set'
require_relative 'shift'
ROOT = File.expand_path('../..', __dir__)
R0, RX, CONF = ARGV
abort 'usage: compare_safety.rb <R0> <Rx> [confirmed.json]' unless R0 && RX
D0 = "#{ROOT}/results/safety/#{R0}"; DX = "#{ROOT}/results/safety/#{RX}"
OUT = "#{ROOT}/results/safety/compare_#{R0}_vs_#{RX}"
FileUtils.mkdir_p(OUT)
confirmed = CONF ? JSON.parse(File.read(CONF)) : []
names = File.readlines("#{__dir__}/double_tide_names.txt").map(&:strip).reject { |l| l.empty? || l.start_with?('#') }

SHAPE = %w[count alternation yb_alternation yb_duplicate yb_gap yb_count].freeze
viols = ->(d) { File.readlines("#{d}/violations.tsv").drop(1).map { |l| l.chomp.split("\t", 6) } }
key = ->(r) { [r[0], r[1], r[3], r[4]] } # type, id, day, rule
v0 = viols.(D0); vx = viols.(DX)
base = v0.map(&key).to_set
shape_r0 = v0.select { |r| SHAPE.include?(r[4]) }.map { |r| [r[0], r[1]] }.to_set
new_v = vx.reject { |r| base.include?(key.(r)) }.uniq(&key)

set = JSON.parse(File.read("#{ROOT}/safety_set.json"))
rows = []; shifts = {}
set.each do |s|
    k = "#{s['type']}_#{s['id']}"
    j0 = JSON.parse(File.read("#{D0}/ids/#{k}.json")) rescue {}
    jx = JSON.parse(File.read("#{DX}/ids/#{k}.json")) rescue {}
    st = j0['events'] && jx['events'] ? Shift.stats(j0['events'], jx['events']) : {}
    f = jx['F'] || j0['F']
    why = []
    why << 'double/stand (name)' if names.any? { |n| (jx['name'] || j0['name']).to_s.downcase.include?(n.downcase) }
    why << "F=#{f}" if f && f.between?(1.0, 3.0)
    why << 'R0 shape violations' if shape_r0.include?([s['type'], s['id']])
    sh0 = (j0.dig('ics', 'summary_shapes') || {}).keys.map { |x| x.sub('-#', '#') }.uniq.sort
    shx = (jx.dig('ics', 'summary_shapes') || {}).keys.map { |x| x.sub('-#', '#') }.uniq.sort
    shifts[k] = st.merge(id: s['id'], type: s['type'], origin: s['origin'], name: jx['name'] || j0['name'],
                         F: f, review_ok: why, vevents_r0: j0.dig('ics', 'vevents'), vevents_rx: jx.dig('ics', 'vevents'),
                         summary_shapes_same: sh0 == shx, summary_shapes_rx: shx)
end

new_v.each do |r|
    sh = shifts["#{r[0]}_#{r[1]}"] || {}
    cls = if SHAPE.include?(r[4]) && !(sh[:review_ok] || []).empty? then 'manual_review'
          else 'fail' end
    rows << r + [cls, (sh[:review_ok] || []).join('; ')]
end
File.write("#{OUT}/new_violations.tsv", (["type\tid\torigin\tday\trule\tdetail\tclass\treview_basis"] +
    rows.map { |x| x.join("\t") }).join("\n") + "\n")
cols = %i[type id origin name F n_old n_new matched unmatched_old unmatched_new median_abs_min p95_abs_min max_abs_min
          median_signed_min median_abs_dh max_abs_dh vevents_r0 vevents_rx summary_shapes_same]
File.write("#{OUT}/shifts.tsv", ([cols.join("\t")] + shifts.values.map { |h| cols.map { |c| h[c] }.join("\t") }).join("\n") + "\n")

act = ->(r) { r[2] == 'active' }
gate = lambda do |pick|
    fails = rows.select { |r| pick.(r) && r[6] == 'fail' }
    review = rows.select { |r| pick.(r) && r[6] == 'manual_review' }
    st = shifts.values.select { |h| pick.([h[:type], h[:id], h[:origin]]) }
    big = st.select { |h| h[:median_abs_min].to_f > 30 && !confirmed.include?(h[:id]) }
    mid = st.select { |h| h[:median_abs_min].to_f > 15 }
    ics = st.reject { |h| h[:summary_shapes_same] }
    { stations: st.size, new_fail: fails.size, new_fail_stations: fails.map { |r| r[1] }.uniq,
      new_manual_review: review.size, manual_review_stations: review.map { |r| r[1] }.uniq,
      shift_gt30_unconfirmed: big.map { |h| [h[:id], h[:median_abs_min]] },
      shift_gt15_manual_review: mid.map { |h| [h[:id], h[:median_abs_min]] },
      ics_structure_changed: ics.map { |h| h[:id] },
      verdict: fails.empty? && big.empty? && ics.empty? ? (review.empty? ? 'GO' : 'GO pending manual review') : 'NO-GO' }
end
verdict = { r0: R0, rx: RX, new_violations: rows.size, by_rule: rows.map { |r| r[4] }.tally,
            active_set_row_A: gate.(act), all_254: gate.(->(_) { true }) }
File.write("#{OUT}/verdict.json", JSON.pretty_generate(verdict))
puts JSON.generate(verdict.slice(:new_violations, :by_rule).merge(
    active: verdict[:active_set_row_A].slice(:verdict, :new_fail, :new_manual_review),
    all: verdict[:all_254].slice(:verdict, :new_fail, :new_manual_review)))
