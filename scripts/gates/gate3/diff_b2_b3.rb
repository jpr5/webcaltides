# Semantic diff B2 -> B3, and B3 fallback ids vs master's shipped data/ticon.json (f58c96..., = ticon-prev.json).
require 'json'; require 'digest'; require 'yaml'
require_relative '../paths'
H = GatePaths::ROOT
WT = File.expand_path(ENV.fetch('WT')) # B worktree (was harmonics-b2-fixes)
master_path = "#{WT}/data/ticon-prev.json"
puts "master sha256=#{Digest::SHA256.file(master_path).hexdigest}"
b2 = JSON.parse(File.read("#{H}/builds/B2/ticon.json"))['stations']
b3 = JSON.parse(File.read("#{H}/builds/B3/ticon.json"))['stations']
m  = JSON.parse(File.read(master_path))['stations']
fb = YAML.safe_load_file("#{WT}/scripts/ticon_fallback_stations.yml")['stations'].map { _1['id'] }
sw = YAML.safe_load_file("#{WT}/scripts/ticon_skipped_records.yml")['records'].map { _1['id'] }
puts "stations b2=#{b2.size} b3=#{b3.size} master=#{m.size} fallback=#{fb.size} switched=#{sw.size} overlap=#{(fb & sw).size}"
puts "order b2==b3: #{b2.map { _1['id'] } == b3.map { _1['id'] }}; order master==b3: #{m.map { _1['id'] } == b3.map { _1['id'] }}"
diff = b2.zip(b3).reject { |x, y| x == y }.map { |x, _| x['id'] }
puts "B2 vs B3 differing stations=#{diff.size}; == fallback+switched: #{diff.sort == (fb + sw).sort}"
puts "  differ but not expected: #{(diff - fb - sw).inspect}; expected but equal: #{((fb + sw) - diff).inspect}"
keys_changed = b2.zip(b3).flat_map { |x, y| x.keys.select { |k| x[k] != y[k] } }.tally
puts "  changed keys: #{keys_changed.inspect}"
mi = m.to_h { [_1['id'], _1] }
meta = %w[id lat lon]
idc = b3.count { |s| !mi[s['id']] || s.slice(*meta) != mi[s['id']].slice(*meta) }
puts "ids/coords B3 vs master: #{b3.size - idc}/#{b3.size} identical; set equal: #{b3.map { _1['id'] }.sort == m.map { _1['id'] }.sort}"
md = b3.count { |s| s.slice('name', 'timezone', 'region', 'units') != mi[s['id']].slice('name', 'timezone', 'region', 'units') }
puts "name/timezone/region/units differences vs master: #{md}"
bad = fb.reject { |id| (s = b3.find { _1['id'] == id }) && s['constituents'] == mi[id]['constituents'] && s['datum_offset'] == mi[id]['datum_offset'] }
puts "fallback ids whose constituents+datum_offset != master exactly: #{bad.size} #{bad.inspect}"
puts "fallback full-station equal to master (all keys): #{fb.count { |id| b3.find { _1['id'] == id } == mi[id] }}/#{fb.size}"
b2i = b2.to_h { [_1['id'], _1] }
sw.each do |id|
    x = b2i[id]; y = b3.find { _1['id'] == id }
    m2 = ->(s) { c = s['constituents'].find { _1['name'] == 'M2' }; "M2 #{c['amp'].round(4)} m #{c['phase'].round(2)} deg" }
    puts "switched #{id} #{y['name']}: n #{x['constituents'].size}->#{y['constituents'].size}, datum #{x['datum_offset'].round(4)}->#{y['datum_offset'].round(4)}, #{m2[x]} -> #{m2[y]}"
end
