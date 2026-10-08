# usage: ruby proof.rb <port> <run R2c|R1> <label>
# Fetches ICS feeds from the local app and compares High/Low event times with the harness run's
# t_model (TICON) or t_ref (NOAA) for the same station, W1 window (Nov-Dec 2026).
require 'net/http'
require 'icalendar'
require 'time'
require 'json'

port, run, label = ARGV
require_relative '../paths'
H = GatePaths::ROOT
OUT = "#{H}/ship-proof/#{label}"
Dir.mkdir(File.dirname(OUT)) rescue nil
Dir.mkdir(OUT) rescue nil
TICON = %w[T8929e6c Tfbbf38a T189a67a]
NOAA = '8443970'
DATES = %w[20261115 20261215]

def harness(run, id, col)
    rows = {}
    File.foreach("#{H}/results/#{run}/W1/events.csv") do |l|
        f = l.split(',')
        next unless (col == :model ? f[0] == id : (f[3] == 'noaa' && f[4] == id && f[5] == 'R'))
        t = col == :model ? f[10] : f[9]
        next if t.nil? || t.empty?
        rows[Time.iso8601(t)] = f[8]
    end
    rows
end

def ics(port, path, label, name)
    res = Net::HTTP.get_response(URI("http://127.0.0.1:#{port}#{path}"))
    File.write("#{OUT}/#{name}.ics", res.body)
    evs = res.code == '200' ? Icalendar::Calendar.parse(res.body).first.events : []
    tide = evs.filter_map do |e|
        s = e.summary.to_s
        type = s =~ /\bHigh\b/i ? 'High' : (s =~ /\bLow\b/i ? 'Low' : nil)
        next unless type
        [e.dtstart.to_time.utc, type]
    end
    [res.code, tide]
end

report = {}
(TICON.map { |id| [id, :model] } + [[NOAA, :ref]]).each do |id, col|
    codes = []; ics_set = {}
    DATES.each do |d|
        code, evs = ics(port, "/tides/#{id}.ics?date=#{d}&solar=0", label, "#{id}_#{d}")
        codes << code
        evs.each { |t, type| ics_set[[t, type]] = true }
    end
    r = { http: codes, ics_unique_events: ics_set.size }
    %w[R1 R2c].each do |rn|
        h = harness(rn, id, col)
        exact = h.count { |t, type| ics_set[[t, type]] }
        r["#{rn}_events"] = h.size
        r["#{rn}_found_exact_in_ics"] = exact
        r["#{rn}_missing_sample"] = h.reject { |t, type| ics_set[[t, type]] }.first(3).map { |t, ty| [t.iso8601, ty] }
    end
    report[id] = r
end
puts JSON.pretty_generate(report)
File.write("#{OUT}/report.json", JSON.pretty_generate(report))
