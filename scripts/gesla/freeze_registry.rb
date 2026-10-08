#!/usr/bin/env ruby
# frozen_string_literal: true

# BP S0: freeze the GESLA build's station registry and pin its inputs.
#
#   ruby scripts/gesla/freeze_registry.rb [--harness DIR] [--skip-lock]
#
# Writes:
#   data/gesla/station_registry.json  - the 2,706 served ids with their identity fields
#   $OTC_WORK/inputs/gesla/4.1/inputs.lock.json
#                                     - SHA-256 of the served ticon.json, the engine's TCD file,
#                                       every file in the harness refs/ and cache/, and safety_set.json.
#                                       This is data: it lives in the private work bucket
#                                       (s3://opentideconstants-work/inputs/gesla/4.1/inputs.lock.json),
#                                       never in git.  $OTC_WORK is only a local cache of the bucket.
#   data/gesla/inputs.lock.json       - the committed pin: GESLA version, bucket, and the SHA-256 and
#                                       size of each pinned bucket object.  Readers check an object
#                                       against this pin before they trust it.
#
# After a run, upload the lock to the bucket (the script prints the command).
#
# The served ticon.json is data/ticon.json.  When it is missing, it is fetched from the data-v1
# release.  Either way its SHA-256 must equal TICON_SHA256, or the script stops.

require 'json'
require 'digest'
require 'net/http'
require 'uri'
require 'fileutils'
require 'optparse'

ROOT          = File.expand_path('../..', __dir__)
TICON_PATH    = File.join(ROOT, 'data', 'ticon.json')
TCD_LINK      = File.join(ROOT, 'data', 'latest-xtide.tcd')
OUT_DIR       = File.join(ROOT, 'data', 'gesla')
REGISTRY_PATH = File.join(OUT_DIR, 'station_registry.json')
PIN_PATH      = File.join(OUT_DIR, 'inputs.lock.json')
GESLA_VERSION = '4.1'
BUCKET        = 'opentideconstants-work'
LOCK_KEY      = "inputs/gesla/#{GESLA_VERSION}/inputs.lock.json"
OTC_WORK      = File.expand_path(ENV.fetch('OTC_WORK', '~/.local/share/opentideconstants/work'))
LOCK_PATH     = File.join(OTC_WORK, LOCK_KEY)

TICON_URL     = 'https://github.com/jpr5/webcaltides/releases/download/data-v1/ticon.json'
TICON_SHA256  = 'f58c960a6acfd15677219c2a611af4be84b59c7dbe20bce3182d673e7b269ea6'
IDENTITY      = %w[id lat lon name region timezone units].freeze
HARNESS_HOME  = '~/.local/share/copilotkit/cr/webcaltides-harmonics-eval'

def download(url, path, limit = 5)
    raise 'too many redirects' if limit.zero?

    uri = URI(url)
    Net::HTTP.start(uri.host, uri.port, use_ssl: true) do |http|
        resp = http.get(uri.request_uri)
        next download(resp['location'], path, limit - 1) if resp.is_a?(Net::HTTPRedirection)
        raise "GET #{url}: HTTP #{resp.code}" unless resp.is_a?(Net::HTTPSuccess)

        tmp = "#{path}.part"
        File.binwrite(tmp, resp.body)
        File.rename(tmp, path)
        puts "Downloaded #{path} (#{resp.body.size} bytes)"
    end
end

def served_ticon
    download(TICON_URL, TICON_PATH) unless File.exist?(TICON_PATH)
    sha = Digest::SHA256.file(TICON_PATH).hexdigest
    abort "#{TICON_PATH}: SHA-256 #{sha}, expected #{TICON_SHA256}" unless sha == TICON_SHA256
    [JSON.parse(File.read(TICON_PATH)), sha]
end

# Float#to_s is the shortest round-trip form, so the registry keeps the served file's literals
# (JSON.generate can print 5.216974 as 5.2169739999999996).
def json_value(v)
    v.is_a?(Float) ? v.to_s : JSON.generate(v)
end

def json_object(h)
    "{#{h.map { |k, v| "#{JSON.generate(k)}:#{json_value(v)}" }.join(',')}}"
end

# One station per line keeps the committed file diffable.
def write_registry(ticon, sha)
    stations = ticon['stations'].map { |s| IDENTITY.to_h { |k| [k, s.fetch(k)] } }.sort_by { |s| s['id'] }
    ids = stations.map { |s| s['id'] }
    abort "duplicate ids in #{TICON_PATH}" unless ids.uniq.size == ids.size

    source = { 'file' => 'data/ticon.json', 'generated_at' => ticon['generated_at'],
               'sha256' => sha, 'source' => ticon['source'] }
    lines = stations.map { |s| "    #{json_object(s)}" }
    File.write(REGISTRY_PATH, <<~JSON)
        {
          "count": #{stations.size},
          "source": #{JSON.generate(source.sort.to_h)},
          "stations": [
        #{lines.join(",\n")}
          ]
        }
    JSON
    puts "Wrote #{REGISTRY_PATH} (#{stations.size} stations)"
end

def file_entry(path, rel)
    { 'file' => rel, 'sha256' => Digest::SHA256.file(path).hexdigest, 'size' => File.size(path) }
end

def tree_entry(dir)
    abort "missing harness directory #{dir}" unless File.directory?(dir)

    files = Dir.glob('**/*', File::FNM_DOTMATCH, base: dir).sort
               .select { |rel| File.file?(File.join(dir, rel)) }
               .to_h { |rel| [rel, Digest::SHA256.file(File.join(dir, rel)).hexdigest] }
    # tree_sha256 = SHA-256 over "<sha256>  <path>\n" lines, in path order (shasum output format)
    tree = Digest::SHA256.hexdigest(files.map { |rel, h| "#{h}  #{rel}\n" }.join)
    { 'count' => files.size, 'tree_sha256' => tree, 'files' => files }
end

def write_lock(sha, harness)
    tcd = File.realpath(TCD_LINK)
    lock = {
        'ticon'   => { 'file' => 'data/ticon.json', 'sha256' => sha, 'size' => File.size(TICON_PATH),
                       'url' => TICON_URL },
        'tcd'     => file_entry(tcd, File.basename(tcd)).merge('read_via' => 'data/latest-xtide.tcd'),
        'harness' => {
            'root'            => HARNESS_HOME,
            'safety_set.json' => file_entry(File.join(harness, 'safety_set.json'), 'safety_set.json'),
            'refs'            => tree_entry(File.join(harness, 'refs')),
            'cache'           => tree_entry(File.join(harness, 'cache')),
        },
    }
    FileUtils.mkdir_p(File.dirname(LOCK_PATH))
    File.write(LOCK_PATH, "#{JSON.pretty_generate(lock)}\n")
    puts "Wrote #{LOCK_PATH} (refs #{lock['harness']['refs']['count']} files, " \
         "cache #{lock['harness']['cache']['count']} files)"
    write_pin(LOCK_KEY, LOCK_PATH)
    puts "Upload it: aws s3 cp #{LOCK_PATH} s3://#{BUCKET}/#{LOCK_KEY}"
end

# The pin keeps every other object it already names (later steps add theirs).
def write_pin(key, path)
    pin = File.exist?(PIN_PATH) ? JSON.parse(File.read(PIN_PATH)) : {}
    pin['gesla_version'] = GESLA_VERSION
    pin['bucket']        = BUCKET
    pin['objects']     ||= {}
    pin['objects'][key]  = { 'uri' => "s3://#{BUCKET}/#{key}",
                             'sha256' => Digest::SHA256.file(path).hexdigest, 'size' => File.size(path) }
    pin['objects']       = pin['objects'].sort.to_h
    File.write(PIN_PATH, "#{JSON.pretty_generate(pin.slice('gesla_version', 'bucket', 'objects'))}\n")
    puts "Wrote #{PIN_PATH} (#{key} #{pin['objects'][key]['sha256']})"
end

if $PROGRAM_NAME == __FILE__
    opts = { harness: File.expand_path(HARNESS_HOME), lock: true }
    OptionParser.new do |o|
        o.on('--harness DIR', 'harness root (refs/, cache/, safety_set.json)') { |d| opts[:harness] = File.expand_path(d) }
        o.on('--skip-lock', 'write the registry only') { opts[:lock] = false }
    end.parse!

    FileUtils.mkdir_p(OUT_DIR)
    ticon, sha = served_ticon
    write_registry(ticon, sha)
    write_lock(sha, opts[:harness]) if opts[:lock]
end
