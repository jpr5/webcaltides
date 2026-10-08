#!/usr/bin/env ruby
# Combined row (§6.3): score a variant on the active-with-reference set over W1,W2,W3,WY.
# Run from an engine worktree: TICON_FILE=<dataset> bundle exec ruby combined_runner.rb <run> <set.json>
# Also scores prod ICS (R0p) when <run> == 'R0p' (no engine needed). Same Score.score matching as the gate runs.
require 'bundler/setup'
require 'logger'
require 'active_support/all'
H = File.expand_path('~/.local/share/copilotkit/cr/webcaltides-harmonics-eval')
G = File.expand_path('~/.local/share/copilotkit/cr/webcaltides-harmonics-b/gate3')
require "#{H}/tools/lib"
require "#{H}/tools/score"
run, set_file = ARGV
WINS = Eval::WINDOWS.slice('W1', 'W2', 'W3').merge('WY' => Eval::WY)
BL = '/Users/jpr5/.local/share/copilotkit/cr/webcaltides-harmonics-baseline-2026-10-06'
jobs = JSON.parse(File.read(set_file))
model_for =
    if run == 'R0p'
        fl = JSON.parse(File.read("#{BL}/fetch_log.json")).select { |r| r['status'] == 'ok' && r['type'] == 'tides' }.to_h { |r| [r['id'], r] }
        lambda do |j, _w0, _w1|
            r = fl[j['id']] or next nil
            ics = File.read("#{BL}/#{r['file']}")
            ics.split('BEGIN:VEVENT').drop(1).filter_map do |v|
                s = v[/^SUMMARY:(.*)$/, 1].to_s.strip
                m = s.match(/\A(High|Low) Tide(?: (-?\d+(?:\.\d+)?) (ft|m))?/) or next
                t = v[/^DTSTART[^:]*:(\d{8}T\d{6})/, 1] or next
                abort "non-GMT DTSTART #{j['id']}" unless v =~ /^DTSTART;TZID=GMT:|^DTSTART:\d{8}T\d{6}Z/
                h = m[2] ? m[2].to_f * (m[3] == 'ft' ? 30.48 : 100.0) : 0.0
                { t: Time.strptime("#{t}Z", '%Y%m%dT%H%M%S%z').utc, ty: m[1], hr: h }
            end.sort_by { |e| e[:t] }
        end
    else
        require File.expand_path('lib/harmonics_engine', Dir.pwd)
        cache = "#{G}/cache/combined_#{run}"; FileUtils.rm_rf(cache); FileUtils.mkdir_p(cache)
        eng = Harmonics::Engine.new(Logger.new(nil), cache); eng.stations
        sc = eng.stations_cache
        lambda do |j, w0, w1|
            s = sc[j['id']] or next nil
            eng.generate_peaks_optimized(j['id'], w0 - 6.hours, w1 + 6.hours)
               .map { |p| { t: p['time'].utc, ty: p['type'], hr: (p['height'] - s['datum_offset'].to_f) * 100.0 } }
        end
    end
FileUtils.mkdir_p("#{G}/data/combined")
csv = CSV.open("#{G}/data/combined/#{run}.csv", 'w')
csv << %w[station src ref window n_ref matched missed_per100 t_mae t_p95 t_bias h_mae_al]
jobs.each do |j|
    _, all = Eval.read_events_json("#{Eval::REFS}/#{j['src']}/events/#{j['ref']}.json")
    next unless all
    WINS.each do |wn, (w0, w1)|
        ref = all.select { |e| e[:t] >= w0 && e[:t] < w1 }
        next if ref.empty?
        model = model_for.call(j, w0, w1)
        next if model.nil?
        m, = Score.score(ref, model, w0, w1)
        csv << [j['id'], j['src'], j['ref'], wn] + %i[n_ref matched missed_per100 t_mae t_p95 t_bias h_mae_al].map { |k| Score.fmt(m[k]) }
    end
end
csv.close
puts "#{run} done"
