#!/usr/bin/env ruby
# Non-NOAA reference fetcher (plan §3, step 5) using the app's own clients from the engine worktree.
# Run from the worktree root: bundle exec ruby <H>/tools/fetch_others.rb <src> [<src> ...]
# srcs: bsh chs kartverket imi rws linz. HTTP is disk-cached under refs/<src>/raw/ (body + meta),
# events normalized to UTC/cm under refs/<src>/events/<ref>.json. No app code is changed: the
# harness prepends a caching module to the client classes in this process only.
require 'bundler/setup'
require 'logger'
require 'active_support/all'
require 'mechanize'
require_relative 'lib'
module WebCalTides; def self.remove_tide_station(*) = nil; end unless defined?(WebCalTides)
%w[models/tide_data models/station clients/base].each { |f| require File.expand_path(f, Dir.pwd) }
CLS = { 'bsh' => 'BshTides', 'chs' => 'ChsTides', 'kartverket' => 'KartverketTides', 'imi' => 'MarineInstituteTides',
        'rws' => 'RijkswaterstaatTides', 'linz' => 'LinzTides' }.freeze
FILE = { 'bsh' => 'bsh_tides', 'chs' => 'chs_tides', 'kartverket' => 'kartverket_tides', 'imi' => 'marine_institute_tides',
         'rws' => 'rijkswaterstaat_tides', 'linz' => 'linz_tides' }.freeze
AROUND = Time.utc(2026, 10, 15) # window: 2026-09-01 .. 2027-08/09 (W1–W3 inside)
SPACING = { 'chs' => 1.0, 'kartverket' => 0.2 }.freeze

module HttpDiskCache
    def self.src = Thread.current[:eval_src]
    def cached_http(url, body)
        key = Digest::SHA256.hexdigest("#{url}\n#{body}")
        path = "#{Eval::REFS}/#{HttpDiskCache.src}/raw/#{key}.body"
        return File.binread(path) if File.exist?(path) && File.exist?("#{path}.meta.json")
        res = yield
        FileUtils.mkdir_p(File.dirname(path))
        File.binwrite(path, res.to_s)
        File.write("#{path}.meta.json", JSON.generate('url' => url, 'post' => body, 'fetched_at' => Time.now.utc.iso8601,
                                                       'sha256' => Digest::SHA256.hexdigest(res.to_s)))
        sleep SPACING.fetch(HttpDiskCache.src, 0.5)
        res
    end
    def get_url(url) = cached_http(url, nil) { super }
    def post_json(url, body) = cached_http(url, body.to_json) { super }
end

def norm(tides)
    Array(tides).filter_map do |td|
        t = td.time.respond_to?(:to_time) ? td.time.to_time.utc : td.time
        next unless t && %w[High Low].include?(td.type.to_s)
        h = td.prediction.nil? ? nil : (td.prediction.to_f * (td.units.to_s.start_with?('f') ? 30.48 : 100.0)).round(1)
        { t: Time.at(t.to_i).utc, ty: td.type.to_s, h: h }
    end.sort_by { |e| e[:t] }.uniq { |e| [e[:t], e[:ty]] }
end

def linz_targets(client)
    ports = Clients::LinzTides::PORTS
    tj = JSON.parse(File.read(ENV['TICON_FILE'] || File.expand_path('data/ticon.json', Dir.pwd)))['stations']
    sid = ->(s) { 'T' + Digest::SHA256.hexdigest(format('%.8f_%.8f', s['lat'], s['lon']))[0...7] }
    km = ->(a1, o1, a2, o2) { 6371.0 * Math.acos([[Math.sin(a1 * Math::PI / 180) * Math.sin(a2 * Math::PI / 180) + Math.cos(a1 * Math::PI / 180) * Math.cos(a2 * Math::PI / 180) * Math.cos((o2 - o1) * Math::PI / 180), 1.0].min, -1.0].max) }
    active = %w[T9162534 T8791561 T42845f2 T311ad96]
    tj.filter_map do |s|
        p = ports.min_by { |q| km[s['lat'], s['lon'], q[3], q[4]] }
        d = km[s['lat'], s['lon'], p[3], p[4]]
        next unless d <= Eval::MAX_KM || active.include?(sid[s])
        { 'id' => sid[s], 'prov' => 'ticon', 'sub' => false, 'src' => 'linz', 'ref' => p[1], 'kind' => d <= Eval::MAX_KM ? 'PORT' : 'PORT_FAR', 'dist_km' => d.round(3) }
    end
end

ARGV.each do |src|
    Thread.current[:eval_src] = src
    require File.expand_path("clients/#{FILE.fetch(src)}", Dir.pwd)
    klass = Clients.const_get(CLS.fetch(src)); klass.prepend(HttpDiskCache)
    log = Logger.new("#{Eval::ROOT}/logs/fetch_#{src}.client.log"); log.level = Logger::INFO
    client = klass.new(log)
    stations = client.tide_stations.to_a
    by_pub = stations.group_by { |s| s.public_id.to_s }
    targets = src == 'linz' ? linz_targets(client) : Eval.reference_set([src])
    if src == 'linz'
        File.write("#{Eval::ROOT}/sets/linz.json", JSON.pretty_generate(targets))
        by_pub = stations.group_by { |s| s.id.to_s.delete_prefix('NZ__') }
    end
    refs = targets.map { |t| t['ref'] }.uniq.sort
    ok = 0; fail = []
    refs.each do |ref|
        out = "#{Eval::REFS}/#{src}/events/#{ref}.json"
        next ok += 1 if File.exist?(out)
        st = by_pub[ref]&.first
        next fail << "#{ref}:no-station" unless st
        ev = begin
            norm(client.tide_data_for(st, AROUND))
        rescue StandardError => e
            fail << "#{ref}:#{e.class}"; next
        end
        next fail << "#{ref}:empty" if ev.empty?
        Eval.write_events_json(out, ev, 'source' => src, 'ref' => ref, 'station_id' => st.id.to_s, 'units' => 'cm', 'tz' => 'UTC',
                                        'datum' => "#{src}-native", 'first' => ev.first[:t].iso8601, 'last' => ev.last[:t].iso8601)
        ok += 1
    end
    puts "#{src}: stations listed=#{stations.size} targets=#{targets.size} refs=#{refs.size} ok=#{ok} fail=#{fail.size} #{fail.first(30).join(' ')}"
    $stdout.flush
end
