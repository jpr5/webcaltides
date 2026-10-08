#!/usr/bin/env ruby
# Merge shard parts into results/<run>/<window>/{events,stations,timing}.csv (sorted, deterministic).
require 'csv'
require_relative 'paths'
H = GatePaths::ROOT
run, wins = ARGV
wins.split(',').each do |w|
    dir = "#{H}/results/#{run}/#{w}"
    %w[events stations timing].each do |kind|
        parts = Dir["#{dir}/parts/#{kind}_*.csv"].sort_by { |p| p[/_(\d+)\.csv\z/, 1].to_i }
        rows = parts.flat_map { |p| CSV.read(p) }
        header = rows.shift
        key = kind == 'events' ? ->(r) { [r[0], r[9]] } : ->(r) { [r[0]] }
        rows.sort_by!(&key)
        CSV.open("#{dir}/#{kind}.csv", 'w') { |c| c << header; rows.each { |r| c << r } }
    end
    st = CSV.read("#{dir}/stations.csv", headers: true)
    tm = CSV.read("#{dir}/timing.csv", headers: true).map { |r| r['secs'].to_f }
    printf("%s %s: stations=%d events=%d model-secs total=%.1f mean=%.3f max=%.3f\n", run, w, st.size,
           CSV.read("#{dir}/events.csv").size - 1, tm.sum, tm.empty? ? 0 : tm.sum / tm.size, tm.max || 0)
end
