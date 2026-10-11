#!/usr/bin/env ruby
# Harness runner shard (plan §4.1). Run from the engine worktree root via `bundle exec`.
# Usage: ruby runner.rb <run> <shard> <nshards> <mode:model|rn> <srcs csv|all> <windows csv>
# Writes results/<run>/<window>/parts/{events,stations,timing}_<shard>.csv
require 'bundler/setup'
require 'logger'
require 'active_support/all'
require_relative 'lib'
require_relative 'score'
require File.expand_path('lib/harmonics_engine', Dir.pwd)

run, shard, nsh, mode, srcs, wins = ARGV
shard = shard.to_i; nsh = nsh.to_i
srcs = srcs == 'all' ? nil : srcs.split(',')
wins = wins.split(',')
cache = "#{Eval::ROOT}/cache/#{run}/p#{shard}"
FileUtils.mkdir_p(cache)
eng = Harmonics::Engine.new(Logger.new(nil), cache)
eng.stations
SC = eng.stations_cache
CONV = { 'feet' => 30.48, 'ft' => 30.48, 'm' => 100.0, 'meters' => 100.0 }.freeze
NMAP = { 'LAM2' => 'LDA2', 'RHO' => 'RHO1', 'SIGMA1' => 'SIG1' }.freeze

jobs = srcs&.first&.start_with?('@') ? JSON.parse(File.read(srcs.first[1..])) : Eval.reference_set(srcs)
if mode == 'rn'
    require 'tcd'
    tcdc = TCD.open(File.expand_path('data/latest-xtide.tcd', Dir.pwd)).constituents.map(&:name)
    defs = eng.instance_variable_get(:@constituent_definitions) || {}
    jobs = jobs.select { |j| j['src'] == 'noaa' && j['kind'] == 'R' }.uniq { |j| j['ref'] }.filter_map do |j|
        p = "#{Eval::REFS}/noaa/harcon/harcon_#{j['ref']}.json"
        next unless File.exist?(p) && (h = (JSON.parse(File.read(p))['HarmonicConstituents'] rescue nil))
        cs = h.select { |c| c['amplitude'].to_f > 0 }.map { |c| { 'name' => NMAP[c['name']] || c['name'], 'amp' => c['amplitude'].to_f, 'phase' => c['phase_GMT'].to_f } }
        cs = cs.select { |c| (eng.speeds[c['name']] || defs[c['name']]) && tcdc.include?(c['name']) }
        next if cs.empty?
        sid = "N#{j['ref']}"
        SC[sid] = { 'name' => "NOAA #{j['ref']}", 'datum_offset' => 0.0, 'meridian' => '00:00:00', 'units' => 'm', 'type' => 'tide', 'constituents' => cs }
        j.merge('id' => sid, 'prov' => 'noaa_harcon', 'sub' => false, 'dist_km' => 0.0)
    end
end
jobs = jobs.each_with_index.select { |_, i| i % nsh == shard }.map(&:first)

# model height relative to the model's own mean level (cm): remove datum_offset (ref's × mult for subs)
def raw_cm(st, pk)
    c = CONV.fetch(pk['units'] || st['units'] || 'ft') { raise "units #{pk['units']}" }
    dz = st['datum_offset'].to_f
    if st['ref_key']
        ref = SC[st['ref_key']] || {}
        mult = pk['type'] == 'High' ? st['h_height_mult'] : st['l_height_mult']
        dz = ref['datum_offset'].to_f * (mult || 1.0)
    end
    (pk['height'] - dz) * c
end

ECOLS = %w[station prov sub src ref_id kind dist_km datum type t_ref t_model dt_min h_ref h_model dh_cm dh_al_cm]
SCOLS = %w[station prov sub src ref_id kind dist_km region datum] + Score::STATION_COLS.map(&:to_s)
refcache = {}
wins.each do |wn|
    w0, w1 = Eval::WINDOWS.fetch(wn)
    dir = "#{Eval::ROOT}/results/#{run}/#{wn}/parts"; FileUtils.mkdir_p(dir)
    ev = CSV.open("#{dir}/events_#{shard}.csv", 'w'); st = CSV.open("#{dir}/stations_#{shard}.csv", 'w')
    tm = CSV.open("#{dir}/timing_#{shard}.csv", 'w')
    ev << ECOLS if shard.zero?; st << SCOLS if shard.zero?; tm << %w[station secs] if shard.zero?
    jobs.each do |j|
        info, all = (refcache[[j['src'], j['ref']]] ||= Eval.read_events_json("#{Eval::REFS}/#{j['src']}/events/#{j['ref']}.json"))
        next unless all
        ref = all.select { |e| e[:t] >= w0 && e[:t] < w1 }
        next if ref.empty?
        s = SC[j['id']] or next
        t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
        pk = eng.generate_peaks_optimized(j['id'], w0 - 6.hours, w1 + 6.hours)
        secs = Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0
        model = pk.map { |p| { t: p['time'].utc, ty: p['type'], hr: raw_cm(s, p) } }
        m, rows = Score.score(ref, model, w0, w1)
        datum = info['datum'].to_s
        m[:h_bias_raw] = nil unless datum == 'MSL' && !s['ref_key']
        base = [j['id'], j['prov'], j['sub'], j['src'], j['ref'], j['kind'], j['dist_km']]
        rows.each do |r|
            ev << base + [datum, r[:type], r[:t_ref].iso8601, r[:t_model].iso8601] +
                  [r[:dt], r[:h_ref], r[:h_model], r[:dh], r[:dh_al]].map { |v| Score.fmt(v) }
        end
        region = Eval.stations_meta.dig(j['id'], 'region') || ''
        st << base + [region, datum] + Score::STATION_COLS.map { |k| Score.fmt(m[k]) }
        tm << [j['id'], format('%.4f', secs)]
    end
    [ev, st, tm].each(&:close)
end
puts "shard #{shard}/#{nsh} #{run} #{mode} jobs=#{jobs.size} done"
