#!/usr/bin/env ruby
# Plan 4.2.3 (small): R0 (local run) vs captured prod ICS, per id with prod ICS, per source.
# tools/safety/r0p_check.rb <R0 run>   -> results/safety/r0p_<run>/{per_id.tsv,summary.json}
# An event matches when the same kind is within 60 s; an id matches when every event matches
# both ways and the counts agree. Also gives per-month match rate (cache boundary at 2027-02).
require 'json'; require 'zlib'; require 'fileutils'; require 'time'
require_relative '../paths'
ROOT = GatePaths::ROOT
RUN = ARGV[0] or abort 'usage: r0p_check.rb <R0 run>'
B = GatePaths::BASELINE
OUT = "#{ROOT}/results/safety/r0p_#{RUN}"
FileUtils.mkdir_p(OUT)

def events(ics)
    ics.split("BEGIN:VEVENT").drop(1).filter_map do |v|
        s = v[/^SUMMARY:(.*)$/, 1].to_s.strip
        k = s[/\A(High|Low) Tide/, 1] || s[/\A(Flood|Ebb|Slack)/, 1] or next
        t = v[/^DTSTART[^:]*:(\d{8}T\d{6})/, 1] or next
        [Time.strptime(t + 'Z', '%Y%m%dT%H%M%S%z').to_i, k, s]
    end.sort
end

def match(a, b)
    by = b.group_by { |e| e[1] }.transform_values { |x| x.map(&:first).sort }
    a.map { |t, k, _| (by[k] || []).bsearch { |x| x >= t - 60 }.then { |x| x && (x - t).abs <= 60 } }
end

rows = []
JSON.parse(File.read("#{B}/fetch_log.json")).select { |r| r['status'] == 'ok' }.each do |r|
    prod = events(File.read("#{B}/#{r['file']}"))
    f = "#{ROOT}/results/safety/#{RUN}/ics/#{r['type']}_#{r['id']}.ics.gz"
    unless File.exist?(f)
        rows << { id: r['id'], type: r['type'], prov: r['prov'], error: 'no R0 ics' }
        next
    end
    loc = events(Zlib::GzipReader.open(f, &:read))
    mp = match(prod, loc); ml = match(loc, prod)
    same_summary = prod.map(&:last) == loc.map(&:last)
    months = prod.zip(mp).group_by { |(t, _, _), _| Time.at(t).utc.strftime('%Y-%m') }
                 .transform_values { |a| (a.count { |_, m| m } * 100.0 / a.size).round(1) }
    rows << { id: r['id'], type: r['type'], prov: r['prov'], requests: r['requests'], variant: r['variant'],
              n_prod: prod.size, n_r0: loc.size, prod_matched: mp.count(true), r0_matched: ml.count(true),
              exact_summaries: same_summary, match: mp.all? && ml.all? && prod.size == loc.size,
              first_mismatch_month: months.find { |_, p| p < 100 }&.first, pct_by_month: months }
end
File.write("#{OUT}/per_id.json", JSON.pretty_generate(rows))
cols = %i[prov type id requests variant n_prod n_r0 prod_matched r0_matched match exact_summaries first_mismatch_month]
File.write("#{OUT}/per_id.tsv", ([cols.join("\t")] + rows.map { |h| cols.map { |c| h[c] }.join("\t") }).join("\n") + "\n")
summary = rows.group_by { |h| [h[:prov], h[:type]] }.map do |(p, t), a|
    pre = a.flat_map { |h| (h[:pct_by_month] || {}).select { |m, _| m < '2027-02' }.values }
    post = a.flat_map { |h| (h[:pct_by_month] || {}).select { |m, _| m >= '2027-02' }.values }
    { prov: p, type: t, ids: a.size, ids_match: a.count { |h| h[:match] }, errors: a.count { |h| h[:error] },
      mean_month_match_pct_before_2027_02: pre.empty? ? nil : (pre.sum / pre.size).round(1),
      mean_month_match_pct_from_2027_02: post.empty? ? nil : (post.sum / post.size).round(1),
      r0_reproduces_prod_rule_4_2_3: a.count { |h| h[:match] } >= 10 && a.none? { |h| !h[:match] } }
end
File.write("#{OUT}/summary.json", JSON.pretty_generate(summary))
puts JSON.generate(summary)
