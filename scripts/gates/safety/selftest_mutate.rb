#!/usr/bin/env ruby
# Positive control for compare_safety.rb: copy run <src> to <dst>, mutate 3 stations, recompute
# violations with rules.rb. Expected: T9162534 shift +40 min (>30 gate), X878f4cf drops one event on
# 2027-01-01 (new count/alternation/yb), T0a5ca11 one NaN height (always fail).
require 'json'; require 'fileutils'; require 'time'
require_relative 'rules'
ROOT = File.expand_path('../..', __dir__)
SRC, DST = ARGV
S = "#{ROOT}/results/safety/#{SRC}"; D = "#{ROOT}/results/safety/#{DST}"
FileUtils.rm_rf(D); FileUtils.mkdir_p("#{D}/ids")
FileUtils.cp_r(Dir["#{S}/ids/*.json"], "#{D}/ids/")
mut = {
    'tides_T9162534' => ->(ev) { ev.map { |t, k, h| [(Time.iso8601(t) + 2400).utc.iso8601, k, h] } },
    'tides_X878f4cf' => ->(ev) { i = ev.index { |t, _, _| t.start_with?('2027-01-01') }; ev.dup.tap { |a| a.delete_at(i) } },
    'tides_T0a5ca11' => ->(ev) { ev.dup.tap { |a| a[100] = [a[100][0], a[100][1], 'NaN'] } }
}
rows = []
set = JSON.parse(File.read("#{ROOT}/safety_set.json"))
set.each do |s|
    k = "#{s['type']}_#{s['id']}"
    f = "#{D}/ids/#{k}.json"; j = JSON.parse(File.read(f))
    if mut[k]
        j['events'] = mut[k].(j['events'])
        evs = j['events'].map { |t, kk, h| { t: Time.iso8601(t).utc, k: kk, h: h.is_a?(String) ? Float::NAN : h } }
        j['violations'] = s['type'] == 'tides' ? SafetyRules.tide(evs, j['F'] || 0) : SafetyRules.current(evs)
        File.write(f, JSON.generate(j))
    end
    j['violations'].each { |d, r, det| rows << [s['type'], s['id'], s['origin'], d, r, det] }
end
File.write("#{D}/violations.tsv", (["type\tid\torigin\tday\trule\tdetail"] + rows.map { |x| x.join("\t") }).join("\n") + "\n")
puts "mutated #{mut.keys.join(', ')}; violations #{rows.size}"
