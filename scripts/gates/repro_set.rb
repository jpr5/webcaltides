#!/usr/bin/env ruby
# Build the §6.1 reproduction station set: the retest's 19 NOAA stations, each paired with the
# webcaltides TICON station nearest the NOAA gauge (retest.rb rule). Writes sets/repro19.json.
# Usage: ruby tools/repro_set.rb <worktree>
# Then: tools/run_variant.sh <wt> R0_repro model @<H>/sets/repro19.json RT1,W2 4
#       ruby tools/repro_check.rb R0_repro
require_relative 'lib'
wt = ARGV[0] or abort 'usage: repro_set.rb <worktree>'
IDS = %w[1612340 1617760 1820000 1630000 1619910 1770000 1890000 1619000 1840000 1841367
         8443970 8665530 9447130 9414290 8771450 9455920 9439040 8418150 8638610].freeze
noaa = JSON.parse(File.read("#{Eval::EVID}/constituents/preds_stations.json"))['stations'].to_h { |s| [s['id'], s] }
tj = JSON.parse(File.read(File.join(wt, 'data/ticon.json')))['stations']
set = IDS.map do |nid|
    ns = noaa.fetch(nid); la = ns['lat'].to_f; lo = ns['lng'].to_f
    t = tj.min_by { |s| (s['lat'] - la)**2 + (s['lon'] - lo)**2 }
    sid = 'T' + Digest::SHA256.hexdigest(format('%.8f_%.8f', t['lat'], t['lon']))[0...7]
    { 'id' => sid, 'prov' => 'ticon', 'sub' => false, 'src' => 'noaa', 'ref' => nid, 'kind' => 'R',
      'dist_km' => (111.0 * [(t['lat'] - la).abs, (t['lon'] - lo).abs].max).round(3) }
end
FileUtils.mkdir_p("#{Eval::ROOT}/sets")
File.write("#{Eval::ROOT}/sets/repro19.json", JSON.pretty_generate(set))
puts "wrote sets/repro19.json (#{set.size} stations, #{set.map { |s| s['id'] }.uniq.size} distinct TICON ids)"
