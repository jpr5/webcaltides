#!/usr/bin/env ruby
# Current events (max flood, max ebb, slack) from the webcaltides harmonics engine, for the
# A-NOAAC proof (manifest A-NOAAC). Run from the webcaltides root:
#
#   bundle exec ruby scripts/otc/tools/engine_current_events.rb IN.json OUT.json
#
# IN.json: {"start": ISO8601 UTC, "end": ISO8601 UTC, "datum_offset": mean flow,
#           "constituents": [{"name", "amp", "phase"}], "step_seconds": 60}
# amp/phase are a scalar series along the flood direction, Greenwich phases (meridian 0),
# the form an XTide current takes in the engine. The engine has no ellipse, azimuth or bin:
# the caller (otc_pipeline.current_proof) projects the ellipse onto the flood direction.
#
# The series comes from Engine#generate_predictions, peaks from Engine#detect_peaks and slacks
# from Clients::Harmonics#detect_zero_crossings, as Clients::Harmonics#current_data_for does for
# an XTide reference current.
require 'bundler/setup'
require 'json'
require 'logger'
require 'tmpdir'
require 'active_support/all'
root = File.expand_path('../../..', __dir__)
require File.join(root, 'lib/harmonics_engine')
require File.join(root, 'clients/harmonics')

inp, out = ARGV
abort "usage: #{$0} IN.json OUT.json" unless inp && out
req = JSON.parse(File.read(inp))
cache = ENV['ENGINE_CACHE'] || Dir.mktmpdir('otc-current-engine')
eng = Harmonics::Engine.new(Logger.new(nil), cache)
eng.stations   # loads the constituent speeds and nodal tables from the TCD
defs = eng.instance_variable_get(:@constituent_definitions) || {}
known, unknown = req['constituents'].partition { |c| eng.speeds[c['name']] || defs[c['name']] }
sid = 'OTC-CURRENT-PROOF'
eng.stations_cache[sid] = { 'name' => sid, 'datum_offset' => req.fetch('datum_offset', 0.0).to_f,
                            'meridian' => '00:00:00', 'units' => 'm/s', 'type' => 'current',
                            'constituents' => known.map { |c| { 'name' => c['name'], 'amp' => c['amp'].to_f,
                                                                'phase' => c['phase'].to_f } } }
t0 = Time.parse(req['start']).utc
t1 = Time.parse(req['end']).utc
step = req.fetch('step_seconds', 60)
pred = eng.generate_predictions(sid, t0, t1, type: 'current', step_seconds: step)
peaks = eng.detect_peaks(pred, step_seconds: step)
client = Clients::Harmonics.allocate   # no settings or network needed for these two methods
events = peaks.filter_map do |p|
    type = client.max_current_type(p)
    type && { 'type' => type, 'time' => p['time'].utc.iso8601, 'velocity' => p['height'] }
end
events += client.detect_zero_crossings(pred).map do |s|
    { 'type' => 'slack', 'time' => s['time'].utc.iso8601, 'velocity' => 0.0, 'begins' => s['begins'] }
end
File.write(out, JSON.pretty_generate({ 'nodal_mode' => eng.nodal_mode,
                                       'unknown_constituents' => unknown.map { |c| c['name'] },
                                       'events' => events.sort_by { |e| e['time'] } }))
