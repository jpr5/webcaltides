#!/usr/bin/env ruby
# Plan 5.4 safety run: tools/safety/run_safety.rb <worktree> <run> [jobs=8]
# Predicts WY (2026-09-01..2027-09-30) for all 254 safety-set ids through
# WebCalTides.tide_calendar_for / current_calendar_for with per-run, per-worker empty cache dirs.
# Output: results/safety/<run>/{manifest.json,ids/,ics/,violations.tsv,summary.json}
require 'json'; require 'fileutils'; require 'digest'
require_relative '../paths'
ROOT = GatePaths::ROOT
WT, RUN, JOBS = ARGV[0], ARGV[1], (ARGV[2] || 8).to_i
abort "usage: run_safety.rb <worktree> <run> [jobs]" unless WT && RUN && File.exist?("#{WT}/webcaltides.rb")
OUT = "#{ROOT}/results/safety/#{RUN}"
FileUtils.mkdir_p(OUT)
FETCH = "#{GatePaths::BASELINE}/fetch_log.json"

git = ->(*a) { IO.popen(['git', '-C', WT, *a], &:read).strip }
pre_status = git.('status', '--porcelain')
data_sha = { 'ticon' => ENV['TICON_FILE'] || "#{WT}/data/latest-ticon.json",
             'xtide' => ENV['XTIDE_FILE'] || "#{WT}/data/latest-xtide.tcd" }.to_h do |k, f|
    p = File.realpath(f) rescue nil
    [k, { path: p, sha256: p && Digest::SHA256.file(p).hexdigest }]
end
manifest = { run: RUN, worktree: WT, head: git.('rev-parse', 'HEAD'), status_clean_before: pre_status.empty?,
             status_before: pre_status, ticon_file_env: ENV['TICON_FILE'], data_sha256: data_sha,
             around: '2026-10-06T12:00Z', window: '2026-09-01..2027-09-30', jobs: JOBS, started_at: Time.now.utc }
File.write("#{OUT}/manifest.json", JSON.pretty_generate(manifest))

units = JSON.parse(File.read(FETCH)).select { |r| r['status'] == 'ok' }
            .to_h { |r| [[r['type'], r['id']], r['variant'].split('_').first] }
set = JSON.parse(File.read("#{ROOT}/safety_set.json"))
ids = set.map { |r| r.merge('units' => units[[r['type'], r['id']]] || 'imperial') }
slices = ids.each_slice((ids.size / JOBS.to_f).ceil).to_a
pids = slices.each_with_index.map do |sl, k|
    f = "#{OUT}/w#{k}.json"; File.write(f, JSON.generate(sl))
    spawn({ 'BUNDLE_GEMFILE' => "#{WT}/Gemfile" }, 'bundle', 'exec', 'ruby', "#{__dir__}/worker.rb",
          OUT, "#{OUT}/cache/w#{k}", f, chdir: WT, out: "#{OUT}/w#{k}.out", err: "#{OUT}/w#{k}.err")
end
pids.each { |p| Process.wait(p) }
FileUtils.rm_rf("#{OUT}/cache") unless ENV['KEEP_CACHE'] # per-run temp cache dirs

# aggregate
rows = []; per = {}
ids.each do |r|
    key = "#{r['type']}_#{r['id']}"
    f = "#{OUT}/ids/#{key}.json"
    j = File.exist?(f) ? JSON.parse(File.read(f)) : { 'ok' => false, 'error' => 'no output' }
    (j['violations'] || []).each { |d, rule, det| rows << [r['type'], r['id'], r['origin'], d, rule, det] }
    rows << [r['type'], r['id'], r['origin'], '-', 'error', j['error']] unless j['ok']
    rows << [r['type'], r['id'], r['origin'], '-', 'unresolved', 'station not found'] if j['ok'] && !j['resolved']
    per[key] = { origin: r['origin'], ok: j['ok'], resolved: j['resolved'], n: j['n_events'], F: j['F'],
                 provider: j['provider'], vevents: j.dig('ics', 'vevents'),
                 rules: (j['violations'] || []).map { |v| v[1] }.tally }
end
File.write("#{OUT}/violations.tsv", (["type\tid\torigin\tday\trule\tdetail"] + rows.map { |x| x.join("\t") }).join("\n") + "\n")
post_status = git.('status', '--porcelain')
summary = { ids: ids.size, ok: per.count { |_, v| v[:ok] }, resolved: per.count { |_, v| v[:resolved] },
            violations: rows.size, by_rule: rows.map { |x| x[4] }.tally,
            stations_with_violations: rows.map { |x| x[1] }.uniq.size,
            status_clean_after: post_status.empty?, finished_at: Time.now.utc, per_id: per }
File.write("#{OUT}/summary.json", JSON.pretty_generate(summary))
puts summary.except(:per_id).to_json
