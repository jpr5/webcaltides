#!/usr/bin/env ruby
# NOAA CO-OPS reference fetcher (plan §3, §4.4): hilo predictions over WY (one call per station,
# time_zone=gmt, datum=MSL with MLLW fallback, metric) and mdapi harcon.json for NOAA R stations (Rn).
# Raw responses cached under refs/noaa/raw/; normalized events under refs/noaa/events/<id>.json.
# Usage: ruby tools/fetch_noaa.rb [--harcon-only|--hilo-only] [--limit N]
require_relative 'lib'

DG = 'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter'
MD = 'https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations'
RAW = "#{Eval::REFS}/noaa/raw"
EVT = "#{Eval::REFS}/noaa/events"
HAR = "#{Eval::REFS}/noaa/harcon"

def hilo_url(id, datum)
    b, e = Eval::WY
    "#{DG}?product=predictions&interval=hilo&datum=#{datum}&time_zone=gmt&units=metric&format=json&application=webcaltides_eval" \
        "&begin_date=#{b.strftime('%Y%m%d')}&end_date=#{(e - 86_400).strftime('%Y%m%d')}&station=#{id}"
end

# returns [fetched_from_network?, status-string]
def fetch_hilo(id)
    out = "#{EVT}/#{id}.json"
    return [false, 'cached'] if File.exist?(out)
    net = false
    # NOAA subordinate (S) stations never serve datum=MSL (measured: 243/243 errors), so skip the MSL call
    (KIND[id] == 'S' ? %w[MLLW] : %w[MSL MLLW]).each do |datum|
        path = "#{RAW}/hilo_#{id}_#{datum}.json"
        net ||= !File.exist?(path)
        body = Eval.cached_get(path, hilo_url(id, datum))
        next unless body
        j = JSON.parse(body) rescue nil
        preds = j && j['predictions']
        next unless preds.is_a?(Array) && !preds.empty?
        ev = preds.map { |p| { t: Time.parse("#{p['t']} UTC"), ty: p['type'] == 'H' ? 'High' : 'Low', h: (p['v'].to_f * 100).round(1) } }
        Eval.write_events_json(out, ev, 'source' => 'noaa', 'ref' => id, 'datum' => datum, 'units' => 'cm', 'tz' => 'UTC',
                                        'raw' => File.basename(path), 'raw_sha256' => Digest::SHA256.file(path).hexdigest)
        return [net, "ok #{datum} n=#{ev.size}"]
    end
    [net, 'FAIL']
end

def fetch_harcon(id)
    path = "#{HAR}/harcon_#{id}.json"
    net = !File.exist?(path)
    body = Eval.cached_get(path, "#{MD}/#{id}/harcon.json?units=metric")
    n = body ? (JSON.parse(body)['HarmonicConstituents'] || []).size : 0
    [net, body ? "ok n=#{n}" : 'FAIL']
end

mode = ARGV.include?('--harcon-only') ? :harcon : ARGV.include?('--hilo-only') ? :hilo : :both
limit = (i = ARGV.index('--limit')) ? ARGV[i + 1].to_i : nil
refs = Eval.reference_set(['noaa'])
KIND = refs.to_h { |r| [r['ref'], r['kind']] }
hilo_ids = refs.map { |r| r['ref'] }.uniq.sort
har_ids = refs.select { |r| r['kind'] == 'R' }.map { |r| r['ref'] }.uniq.sort
jobs = []
jobs += hilo_ids.map { |id| [:hilo, id] } if mode != :harcon
jobs += har_ids.map { |id| [:harcon, id] } if mode != :hilo
jobs = jobs.first(limit) if limit
if (i = ARGV.index('--ids'))
    ids = ARGV[i + 1].split(',')
    jobs = ids.map { |id| [:hilo, id] } + ids.map { |id| [:harcon, id] }
end
puts "NOAA jobs: #{jobs.size} (hilo #{hilo_ids.size}, harcon #{har_ids.size}) mode=#{mode}"
q = Queue.new; jobs.each { |j| q << j }
stats = Hash.new(0); mu = Mutex.new; done = 0; t0 = Time.now
workers = 2.times.map do
    Thread.new do
        while (j = (q.pop(true) rescue nil))
            kind, id = j
            net, st = kind == :hilo ? fetch_hilo(id) : fetch_harcon(id)
            mu.synchronize do
                done += 1; stats["#{kind} #{st.split.first(2).join(' ')}"] += 1
                puts "#{kind} #{id} #{st}" if st.start_with?('FAIL')
                puts "progress #{done}/#{jobs.size} #{(Time.now - t0).round}s #{stats.to_a.inspect}" if (done % 200).zero?
                $stdout.flush
            end
            sleep 1.0 if net # politeness: ≤2 concurrent, 1 s spacing per worker
        end
    end
end
workers.each(&:join)
puts "DONE #{done} in #{(Time.now - t0).round}s #{stats.to_a.inspect}"
