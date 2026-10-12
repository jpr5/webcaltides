# frozen_string_literal: true

require 'json'
require 'digest'
require 'fileutils'
require 'open3'
require 'optparse'

mode = ARGV.shift
options = {}
OptionParser.new do |parser|
    %w[inputs inputs-sha256 release sdk-root out python].each do |name|
        parser.on("--#{name} VALUE") { |value| options[name] = value }
    end
end.parse!
%w[inputs inputs-sha256 release sdk-root out python].each do |name|
    raise ArgumentError, "--#{name} required" unless options[name]
end
raise ArgumentError, 'H0 modes are inventory or legacy-policy' unless %w[inventory legacy-policy].include?(mode)
bundle = File.realpath(options.fetch('inputs'))
out = File.expand_path(options.fetch('out'))
raise "proof output already exists: #{out}" if File.exist?(out)
FileUtils.mkdir_p(out)
manifest_path = File.join(bundle, 'noaa-two-gap-followup-v1-manifest.json')
manifest_sha = Digest::SHA256.file(manifest_path).hexdigest
raise 'manifest pin mismatch' unless manifest_sha == options.fetch('inputs-sha256')
manifest = JSON.parse(File.read(manifest_path))
manifest.fetch('members').each do |member|
    path = File.realpath(File.join(bundle, member.fetch('path')))
    raise "member escapes bundle: #{path}" unless path.start_with?(bundle + '/')
    raise "member changed: #{path}" unless Digest::SHA256.file(path).hexdigest == member.fetch('sha256') && File.size(path) == member.fetch('bytes')
end
matrix_entry = manifest.fetch('station_matrix')
matrix_path = File.join(bundle, matrix_entry.fetch('path'))
raise 'matrix pin mismatch' unless Digest::SHA256.file(matrix_path).hexdigest == matrix_entry.fetch('sha256')
matrix = JSON.parse(File.read(matrix_path)).fetch('stations')
release_path = File.expand_path(options.fetch('release'))
jsonl = release_path.sub(/\.json\z/, '.jsonl')
raise 'R3 pin mismatch' unless Digest::SHA256.file(release_path).hexdigest == '22f581635fb99023595b00e9a9a4c0096bada9afef30be39606d7edb271bbb7c'
raise 'R3 JSONL pin mismatch' unless Digest::SHA256.file(jsonl).hexdigest == 'b98b5d40717bfcce0ff3c9b8cb43a299e7831d29b2164ef1557c1a635a65e0d2'
puts JSON.generate('stage' => 'verified', 'members' => manifest.fetch('members').length)
STDOUT.flush
reference_ids = {'8661070' => 'OTC-a6c00a3f', '9414290' => 'OTC-ee4593cd'}
control_ids = %w[8443970 9447130 9414290]
selected = {}
taken = []
findings = matrix.map do |row|
    {'legacy_id' => row.fetch('legacy_source_record_id'), 'name' => row.fetch('name'),
        'source_record_id' => "annual-tide-tables-2020/#{row.fetch('annual_table_row') == 2513 ? 'atlantic' : 'pacific'}/Table2/#{row.fetch('annual_table_row')}",
        'matching_published_profiles' => [], 'legacy_alias_stations' => []}
end
File.foreach(jsonl) do |line|
    station = JSON.parse(line)
    taken << station.fetch('station_id')
    aliases = station.fetch('aliases', {})
    noaa_ids = Array(aliases['noaa'])
    source_ids = station.fetch('constant_sets', []).select { |set| set['source'] == 'noaa' }.map { |set| set['source_record_id'] }
    selected[station.fetch('station_id')] = station if (noaa_ids + source_ids).any? { |id| reference_ids.key?(id) || control_ids.include?(id) }
    findings.each do |finding|
        finding['legacy_alias_stations'] << station.fetch('station_id') if noaa_ids.include?(finding.fetch('legacy_id'))
        offsets = station['subordinate_offsets'] || {}
        source_match = station.fetch('source_records', []).any? { |record| record['source'] == 'noaa' && record['source_version'] == 'annual-tide-tables-2020' && record['source_record_id'] == finding['source_record_id'] }
        finding['matching_published_profiles'] << station.fetch('station_id') if offsets['source_record_id'] == finding['source_record_id'] || source_match
    end
end
meta = JSON.parse(File.read(release_path.sub(/\.json\z/, '.meta.json')))
report = {'mode' => mode, 'manifest_sha256' => manifest_sha, 'verified_members' => manifest.fetch('members'),
    'matrix_sha256' => matrix_entry.fetch('sha256'), 'release_sha256' => Digest::SHA256.file(release_path).hexdigest,
    'jsonl_sha256' => Digest::SHA256.file(jsonl).hexdigest, 'source_rows' => matrix, 'findings' => findings,
    'references' => reference_ids, 'control_noaa_ids' => control_ids, 'argv' => [mode, options], 'cwd' => Dir.pwd,
    'source_status' => 'historical2020; inferred legacy associations; current validity unestablished; accuracy unverified'}
File.write(File.join(out, 'source-inputs.json'), JSON.pretty_generate(report) + "\n")
if mode == 'inventory'
    report['proof'] = findings.all? { |finding| finding['matching_published_profiles'].empty? } ? 'RED missing both actual annual profiles' : 'unexpected profile presence'
    File.write(File.join(out, 'inventory-RED.json'), JSON.pretty_generate(report) + "\n")
    puts JSON.generate('proof' => report['proof'], 'findings' => findings)
    exit 1
end
producer = File.expand_path('../../..', __dir__)
python = options.fetch('python')
python = '/Users/jpr5/.local/state/worktrees/webcaltides/otc-u1e-r1-continuity/scripts/otc/.venv/bin/python' if python == '3.12'
founders = matrix.map do |row|
    book = row.fetch('annual_table_row') == 2513 ? 'atlantic' : 'pacific'
    "noaa:annual-tide-tables-2020:#{book}:table2:#{row.fetch('annual_table_row')}"
end
id_input = File.join(out, 'identity-input.json')
File.write(id_input, JSON.generate('founders' => founders, 'taken' => taken))
identity_code = <<~PYTHON
    import json,sys
    from otc_pipeline.station_map import new_id
    data=json.load(open(sys.argv[1]))
    taken=set(data['taken'])
    ids=[]
    for founder in data['founders']:
        sid=new_id(founder,taken)
        taken.add(sid)
        ids.append(sid)
    print(json.dumps(ids))
PYTHON
ids_json, status = Open3.capture2({'PYTHONPATH' => File.join(producer, 'scripts/otc')}, python, '-c', identity_code, id_input)
raise 'production stable-ID mechanism failed' unless status.success?
ids = JSON.parse(ids_json)
shell = meta.merge('stations' => selected.values)
matrix.zip(ids).each do |row, sid|
    reference = reference_ids.fetch(row.fetch('candidate_reference_noaa_id'))
    raise "actual reference missing: #{reference}" unless selected.key?(reference)
    shell['stations'] << {'station_id' => sid, 'status' => 'active', 'name' => row.fetch('name'), 'country' => 'USA',
        'lat' => row.fetch('nominal_position_degrees').fetch('latitude'), 'lon' => row.fetch('nominal_position_degrees').fetch('longitude'),
        'timezone' => selected.fetch(reference).fetch('timezone'), 'type' => 'subordinate', 'recommended_set_id' => nil, 'aliases' => {}, 'constant_sets' => [],
        'subordinate_offsets' => row.fetch('contract_numeric_representation').merge('reference_station_id' => reference, 'licence_id' => 'noaa-public-domain',
            'datum' => {'zero' => 'chart_datum', 'chart_datum' => 'mllw', 'named' => {'mllw' => 0.0},
                'basis' => {'mllw' => {'kind' => 'published', 'method' => 'source', 'source_name' => 'NOAA2020 annual tide tables: historical subordinate MLLW zero'}}})}
end
shell_path = File.join(out, 'OTC_20261011.json')
File.write(shell_path, JSON.generate(shell) + "\n")
report['legacy_shell'] = {'path' => shell_path, 'sha256' => Digest::SHA256.file(shell_path).hexdigest, 'target_station_ids' => ids}
File.write(File.join(out, 'source-inputs.json'), JSON.pretty_generate(report) + "\n")
runner = File.join(options.fetch('sdk-root'), 'python/tests/test_noaa_historical.py')
command = [python, runner, '--native', '--release', release_path, '--shell', shell_path, '--source-inputs', File.join(out, 'source-inputs.json'), '--out', File.join(out, 'native-policy-RED.json')]
File.write(File.join(out, 'native-command.json'), JSON.pretty_generate(command) + "\n")
log = File.open(File.join(out, 'native-policy.log'), 'w')
pid = Process.spawn({'PYTHONPATH' => File.join(options.fetch('sdk-root'), 'python/src')}, *command, out: log, err: log, chdir: options.fetch('sdk-root'))
puts JSON.generate('stage' => 'native-policy', 'pid' => pid)
STDOUT.flush
Process.wait(pid)
File.write(File.join(out, 'native-exit.json'), JSON.generate('exit' => $?.exitstatus) + "\n")
exit $?.exitstatus
