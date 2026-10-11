#!/usr/bin/env ruby
# Recompute the 5.4 safety set from plan 2.2 sources and diff vs safety_set.json.
require 'json'; require 'set'
require_relative '../paths'
EV = "#{GatePaths::EVID}/testplan"
SS = "#{GatePaths::ROOT}/safety_set.json"
H = /\A[TX][0-9a-f]{7}(_\d+)?\z/
active = JSON.parse(File.read("#{EV}/active_harmonics.json")).map { |r| [r['type'], r['id']] }.to_set
nxt = Set.new; seen = Set.new
Dir["#{EV}/http/*.jsonl"].sort.each do |f|
  File.foreach(f) do |l|
    next unless l.include?('/next')
    r = JSON.parse(l) rescue next
    next if seen.include?(r['requestId']); seen << r['requestId']
    m = r['path'].to_s.match(%r{\A/api/stations/(tides|currents)/([^/]+)/next}) or next
    nxt << [m[1], m[2]] if r['httpStatus'] == 200 && m[2] =~ H
  end
end
cache = Set.new
File.foreach("#{EV}/prod_cache_ls.txt") do |l|
  m = l.match(/(tides|currents)_v\d_([TX][0-9a-f]{7}(?:_\d+)?)_20\d{4}\.json/) and cache << [m[1], m[2]]
end
others = (nxt | cache) - active
exp = active | others
got = JSON.parse(File.read(SS)).map { |r| [r['type'], r['id']] }.to_set
puts "active=#{active.size} next200=#{nxt.size} next_not_active=#{(nxt - active).size} cache_json=#{cache.size} cache_not_active=#{(cache - active).size} cache_only_not_next=#{(cache - active - nxt).size}"
puts "expected=#{exp.size} (active #{active.size} + others #{others.size}) got=#{got.size}"
puts "missing=#{(exp - got).to_a.inspect}"; puts "extra=#{(got - exp).to_a.inspect}"
