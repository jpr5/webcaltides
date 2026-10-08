#!/usr/bin/env ruby
# R0 vs prod ICS: |dt| (min) of same-kind events matched within +-3 h, per source, before / from 2027-02.
require 'json'; require 'zlib'; require 'time'
require_relative '../paths'
ROOT = GatePaths::ROOT; RUN = ARGV[0]
B = GatePaths::BASELINE
def ev(s) = s.split('BEGIN:VEVENT').drop(1).filter_map { |v| k = v[/^SUMMARY:(High|Low|Flood|Ebb|Slack)/, 1] or next; [Time.strptime(v[/^DTSTART[^:]*:(\d{8}T\d{6})/, 1] + 'Z', '%Y%m%dT%H%M%S%z').to_i, k] }
pct = ->(a, p) { a.empty? ? nil : a.sort[((a.size - 1) * p).round].round(2) }
out = Hash.new { |h, k| h[k] = [] }; feb = Time.utc(2027, 2, 1).to_i; odd = []
JSON.parse(File.read("#{B}/fetch_log.json")).select { |r| r['status'] == 'ok' }.each do |r|
    pr = ev(File.read("#{B}/#{r['file']}"))
    lo = ev(Zlib::GzipReader.open("#{ROOT}/results/safety/#{RUN}/ics/#{r['type']}_#{r['id']}.ics.gz", &:read)).group_by(&:last).transform_values { |a| a.map(&:first).sort }
    pr.each do |t, k|
        c = (lo[k] || []).min_by { |x| (x - t).abs }
        d = c && (c - t).abs <= 10_800 ? (c - t).abs / 60.0 : nil
        out[[r['prov'], r['type'], t < feb ? 'before_2027-02' : 'from_2027-02']] << d
        odd << [r['id'], Time.at(t).utc.iso8601, k, d&.round(2)] if t >= feb && (d.nil? || d > 1)
    end
end
out.sort.each { |k, a| m = a.compact; puts "#{k.join(' ')}: events=#{a.size} unmatched=#{a.count(&:nil?)} median|dt|=#{pct.(m, 0.5)} p95=#{pct.(m, 0.95)} max=#{m.max&.round(2)} min" }
puts "from_2027-02 events off by >1 min or unmatched: #{odd.size}"; odd.first(10).each { |o| puts "  #{o.inspect}" }
