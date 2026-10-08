# Shared helpers for the webcaltides harmonics eval harness (plan §4).
require 'json'
require 'csv'
require 'time'
require 'digest'
require 'fileutils'
require_relative 'paths'
require 'net/http'
require 'uri'

module Eval
    ROOT = GatePaths::ROOT
    EVID = GatePaths::EVID
    REFS = "#{ROOT}/refs"
    WINDOWS = {
        'W1' => [Time.utc(2026, 11, 1), Time.utc(2027, 1, 1)],
        'W2' => [Time.utc(2027, 3, 1),  Time.utc(2027, 5, 1)],
        'W3' => [Time.utc(2027, 6, 1),  Time.utc(2027, 8, 1)],
        # retest windows for the §6.1 reproduction check
        'RT1' => [Time.utc(2026, 10, 1), Time.utc(2026, 12, 1)],
    }.freeze
    WY = [Time.utc(2026, 9, 1), Time.utc(2027, 10, 1)].freeze
    MAX_KM = 2.0

    module_function

    def stations_meta
        @stations_meta ||= JSON.parse(File.read("#{EVID}/testplan/harmonics_stations.json")).to_h { |s| [s['id'], s] }
    end

    def nearest_ref
        @nearest_ref ||= JSON.parse(File.read("#{EVID}/testplan/nearest_ref.json"))
    end

    # Reference-set entries (tide stations, reference within MAX_KM), optionally filtered by source.
    def reference_set(srcs = nil)
        nearest_ref.filter_map do |id, r|
            m = stations_meta[id] or next
            next unless m['type'] == 'tide' && r['dist_km'].to_f <= MAX_KM
            next if srcs && !srcs.include?(r['src'])
            { 'id' => id, 'prov' => m['prov'], 'sub' => m['sub'] ? true : false, 'src' => r['src'],
              'ref' => r['ref'], 'kind' => r['kind'].to_s, 'dist_km' => r['dist_km'].to_f }
        end.sort_by { |e| e['id'] }
    end

    # On-disk cached HTTP fetch. Writes body + .meta.json (url, fetched_at, sha256, status).
    # Returns body string (or nil when the cached/actual response was an error).
    def cached_get(path, url, headers: {}, post_body: nil)
        meta_path = "#{path}.meta.json"
        if File.exist?(path) && File.exist?(meta_path)
            meta = JSON.parse(File.read(meta_path))
            return meta['ok'] ? File.read(path) : nil
        end
        FileUtils.mkdir_p(File.dirname(path))
        uri = URI(url)
        body = nil; status = nil
        3.times do |attempt|
            begin
                http = Net::HTTP.new(uri.host, uri.port)
                http.use_ssl = uri.scheme == 'https'
                http.open_timeout = 20; http.read_timeout = 120
                req = post_body ? Net::HTTP::Post.new(uri) : Net::HTTP::Get.new(uri)
                req['User-Agent'] = 'webcaltides-harmonics-eval/1 (jpr5)'
                headers.each { |k, v| req[k] = v }
                req.body = post_body if post_body
                res = http.request(req)
                status = res.code.to_i; body = res.body.to_s
                break if status < 500
            rescue StandardError => e
                status = -1; body = "#{e.class}: #{e.message}"
            end
            sleep(2 * (attempt + 1))
        end
        ok = status == 200
        File.write(path, body)
        File.write(meta_path, JSON.generate({ 'url' => url, 'post' => post_body, 'fetched_at' => Time.now.utc.iso8601,
                                              'status' => status, 'ok' => ok, 'sha256' => Digest::SHA256.hexdigest(body) }))
        ok ? body : nil
    end

    # Event = { t: Time(UTC), ty: 'High'|'Low', h: Float(cm) }
    def write_events_json(path, events, info = {})
        FileUtils.mkdir_p(File.dirname(path))
        File.write(path, JSON.generate(info.merge('events' => events.map { |e| [e[:t].utc.iso8601, e[:ty], e[:h]] })))
    end

    def read_events_json(path)
        return nil unless File.exist?(path)
        j = JSON.parse(File.read(path))
        [j, j['events'].map { |t, ty, h| { t: Time.iso8601(t), ty: ty, h: h } }]
    end
end
