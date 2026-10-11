#!/usr/bin/env ruby
# Record a run in manifest.json (plan §4.5): base SHA, dataset SHAs, reference-file digest, windows, command.
require 'json'
require 'digest'
require 'time'
require_relative 'paths'
H = GatePaths::ROOT
run, wt, sha, tf, mode, srcs, wins, np, wall = ARGV
path = "#{H}/manifest.json"
man = File.exist?(path) ? JSON.parse(File.read(path)) : { 'harness_version' => 1, 'runs' => {} }
man['runs'] ||= {}
tcd = File.realpath(File.join(wt, 'data/latest-xtide.tcd'))
srcs_l = srcs == 'all' ? Dir["#{H}/refs/*/events"].map { |d| File.basename(File.dirname(d)) }.sort : srcs.split(',')
ref_digest = Digest::SHA256.new
srcs_l.each { |s| Dir["#{H}/refs/#{s}/events/*.json"].sort.each { |f| ref_digest << File.basename(f) << Digest::SHA256.file(f).hexdigest } }
man['runs'][run] = {
    'worktree' => wt, 'sha' => sha, 'mode' => mode, 'srcs' => srcs, 'windows' => wins.split(','), 'nprocs' => np.to_i,
    'ticon_file' => tf, 'ticon_sha256' => Digest::SHA256.file(tf).hexdigest,
    'tcd_file' => tcd, 'tcd_sha256' => Digest::SHA256.file(tcd).hexdigest,
    'ref_events_digest' => ref_digest.hexdigest, 'wall_secs' => wall.to_i, 'finished_at' => Time.now.utc.iso8601,
    'command' => "tools/run_variant.sh #{wt} #{run} #{mode} #{srcs} #{wins} #{np}",
    'results_sha256' => wins.split(',').to_h do |w|
        [w, %w[events stations].to_h { |k| [k, (f = "#{H}/results/#{run}/#{w}/#{k}.csv") && File.exist?(f) ? Digest::SHA256.file(f).hexdigest : nil] }]
    end,
}
File.write(path, JSON.pretty_generate(man))
