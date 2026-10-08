# Run inside the worktree: bundle exec ruby worker.rb <outdir> <cache_dir> <ids.json>
# ids.json: [{id,type,units}]. Writes <outdir>/ids/<type>_<id>.json and <outdir>/ics/<type>_<id>.ics.gz.
# Skips ids already done (resume).
require 'json'; require 'zlib'; require 'logger'; require 'fileutils'
OUT, CACHE, IDS = ARGV
FileUtils.mkdir_p([CACHE, "#{OUT}/ids", "#{OUT}/ics", "#{OUT}/logs"])
$LOG = Logger.new("#{OUT}/logs/#{File.basename(IDS, '.json')}.log").tap { |l| l.level = Logger::WARN }
require File.join(Dir.pwd, 'webcaltides.rb')
require_relative 'rules'

CACHE_STRUCT = Struct.new(:cache_dir).new(CACHE)
WebCalTides.define_singleton_method(:settings) { CACHE_STRUCT }
AROUND = Time.utc(2026, 10, 6, 12) # served window = 2026-09-01 .. 2027-09-30 (WY)

h = WebCalTides.get_harmonics_client
t0 = Time.now
WebCalTides.instance_variable_set(:@tide_stations, h.tide_stations)
WebCalTides.instance_variable_set(:@current_stations, h.current_stations)
$stderr.puts "stations loaded in #{(Time.now - t0).round(1)}s"
SC = h.engine.stations_cache

def consts_for(id)
    e = SC[id] or return []
    e['ref_key'] && SC[e['ref_key']] ? SC[e['ref_key']]['constituents'] : e['constituents']
end

SUMMARY_RE = {
    'tides'    => /\A(High|Low) Tide( -?\d+(\.\d+)? (ft|m))?\z/,
    'currents' => /\A(Flood -?\d+(\.\d+)?kts \S* ?T? ?\S*ft|Ebb \d+(\.\d+)?kts \S* ?T? ?\S*ft|Slack)\z/
}.freeze

JSON.parse(File.read(IDS)).each do |r|
    id, type, units = r.values_at('id', 'type', 'units')
    key = "#{type}_#{id}"
    out = "#{OUT}/ids/#{key}.json"
    next if File.exist?(out)
    res = { id: id, type: type, units: units, ok: false }
    t1 = Time.now
    begin
        if type == 'tides'
            st = WebCalTides.tide_station_for(id)
            cal = WebCalTides.tide_calendar_for(id, around: AROUND, units: units)
            data = st && WebCalTides.tide_data_for(st, around: AROUND)
            evs = (data || []).map { |d| { t: d.time.to_time.utc, k: d.type, h: d.prediction&.to_f } }
        else
            st = WebCalTides.current_station_for(id)
            cal = WebCalTides.current_calendar_for(id, around: AROUND)
            data = st && WebCalTides.current_data_for(st, around: AROUND)
            evs = (data || []).map { |d| { t: d.time.to_time.utc, k: d.type, h: d.velocity_major&.to_f } }
        end
        evs.sort_by! { |e| e[:t] }
        res[:resolved] = !st.nil?
        res[:provider] = st&.provider
        res[:name] = st&.name
        sid = st && (st.bid || st.id)
        res[:ref_key] = SC.dig(sid, 'ref_key')
        res[:native_units] = SC.dig(sid, 'units')
        f = consts_for(sid)
        res[:F] = f.empty? ? nil : SafetyRules.form_factor(f).round(3)
        res[:n_events] = evs.size
        viol = type == 'tides' ? SafetyRules.tide(evs, res[:F] || 0) : SafetyRules.current(evs)
        if cal
            s = cal.to_ical
            Zlib::GzipWriter.open("#{OUT}/ics/#{key}.ics.gz") { |gz| gz.write(s) }
            parsed = Icalendar::Calendar.parse(s)
            ves = parsed.flat_map(&:events)
            res[:ics] = { calendars: parsed.size, vevents: ves.size, bytes: s.bytesize }
            res[:ics][:summary_shapes] = ves.map { |e| e.summary.to_s.gsub(/\d+(\.\d+)?/, '#') }.tally
            viol << ['-', 'ics_parse', "#{parsed.size} calendars"] unless parsed.size == 1
            viol << ['-', 'ics_count', "#{ves.size} vevents != #{evs.size} events"] unless ves.size == evs.size
            bad = ves.reject { |e| e.dtstart && e.dtend && SUMMARY_RE[type].match?(e.summary.to_s) }
            viol << ['-', 'ics_vevent', "#{bad.size} bad VEVENTs e.g. #{bad.first&.summary.inspect}"] unless bad.empty?
        else
            viol << ['-', 'ics_missing', 'calendar_for returned nil']
        end
        res[:violations] = viol
        res[:events] = evs.map { |e| [e[:t].strftime('%Y-%m-%dT%H:%M:%SZ'), e[:k], (e[:h]&.finite? ? e[:h].round(4) : e[:h].to_s)] }
        res[:ok] = true
    rescue => e
        res[:error] = "#{e.class}: #{e.message} @ #{e.backtrace&.first}"
    end
    res[:secs] = (Time.now - t1).round(2)
    File.write("#{out}.tmp", JSON.generate(res)); File.rename("#{out}.tmp", out)
    $stderr.puts "#{key} ok=#{res[:ok]} n=#{res[:n_events]} v=#{res[:violations]&.size} #{res[:secs]}s #{res[:error]}"
end
