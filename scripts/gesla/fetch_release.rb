#!/usr/bin/env ruby
# frozen_string_literal: true

# BP S1: mirror one GESLA release into the private work bucket (run once per GESLA release).
#
#   ruby scripts/gesla/fetch_release.rb [--file ZIP] [--stage DIR] [--part-size BYTES] [--upload | --upload-only]
#
# 1. Gets the release zip: --file uses a local copy; otherwise it downloads the public iCloud Drive
#    share through the keyless CloudKit records/resolve call (re-resolved on expiry, resumable).
#    It also downloads the metadata CSV, which GESLA publishes beside the zip.
# 2. Hashes the zip and every member (SHA-256 and size).
# 3. Reads the three GESLA-4.0 WSV control fixtures out of the 4.0 zip with ranged reads (BP §1.2)
#    and checks them against their pinned SHA-256s.
# 4. Splits the zip into parts and writes MANIFEST.json, all under the stage directory
#    ($OTC_WORK/inputs/gesla/<version>/ by default, the bucket cache layout).
# 5. --upload puts the parts, the CSV, the fixtures and then MANIFEST.json into the work bucket,
#    checks each object's size, and pins MANIFEST.json's SHA-256 and size in
#    data/gesla/inputs.lock.json.  Nothing else is committed.  --upload-only skips steps 1-4 and
#    uploads a stage directory built before.

require 'optparse'
require_relative 'mirror'

module FetchRelease
    include GeslaMirror

    RELEASE = {
        version: '4.1',
        dataset: 'GESLA-4.1 (Global Extreme Sea Level Analysis), all records',
        landing_page: 'https://gesla787883612.wordpress.com/downloads/',
        licence: 'GESLA licence (per-contributor terms; see licence_url). Raw series are not redistributed.',
        licence_url: 'https://gesla787883612.wordpress.com/license/',
        citation: 'Haigh, I.D., et al. (2023), GESLA Version 3: A major update to the global higher-frequency ' \
                  'sea-level dataset, Geoscience Data Journal, https://doi.org/10.1002/gdj3.174; Woodworth, P.L., ' \
                  'et al. (2017), Towards a global higher-frequency sea level dataset, Geoscience Data Journal, ' \
                  'https://doi.org/10.1002/gdj3.42; Caldwell, P.C., et al. (2015), JASL/UHSLC (as the GESLA ' \
                  'downloads page asks)',
        zip: { guid: '092OldNDh1CEOTcC1atuThsww', name: 'GESLA4.1_ALL.zip' },
        metadata: { guid: '02fFab01rq4LdVcwRCf5QCyCQ', name: 'GESLA4-1_ALL.csv' },
    }.freeze

    # BP §1.2: the 4.0 WSV control fixtures stay pinned across GESLA releases.
    FIXTURE_ZIP = { guid: '08e3IrYfVsHqjk-9eOuO9XdJg', name: 'GESLA4_ALL.zip', size: 6_797_781_209 }.freeze
    FIXTURES = {
        'helgolandbinnenhafen-9510070-deu-wsv' => 'ab97305948013d63274689c06db73dcefcb66104447d86e24444bb3d4b1362f6',
        'bhvalterleuchtturm-4990010-deu-wsv' => '45696cd45caadad15009290cd8afc6b827c34aaccc00b084e9c87bbb5333f6a2',
        'cuxhavensteubenhft-5990020-deu-wsv' => '414040047c25dc81ec29d71d78147501931504dedb06c77efdb74b620a2f6c64',
    }.freeze
    FIXTURE_DIR = 'fixtures/gesla-4.0-wsv'

    PART_SIZE = 512 << 20

    module_function

    def share_url(share) = "https://www.icloud.com/iclouddrive/#{share[:guid]}##{File.basename(share[:name], '.*')}"

    def get_share(share, dest)
        if File.exist?(dest)
            warn "#{dest}: present, not downloaded again"
        else
            GeslaMirror.download_share(share[:guid], share[:name], dest)
        end
        { 'source_url' => share_url(share), 'fetched_at' => File.mtime(dest).utc.iso8601 }
    end

    def fetch_fixtures(stage)
        dir = File.join(stage, FIXTURE_DIR)
        FileUtils.mkdir_p(dir)
        todo = FIXTURES.reject { |n, sha| File.exist?(File.join(dir, n)) && GeslaMirror.sha256_file(File.join(dir, n)) == sha }
        unless todo.empty?
            require 'zip'
            io = GeslaMirror::RemoteIO.new(FIXTURE_ZIP[:guid], FIXTURE_ZIP[:name])
            raise "4.0 zip is #{io.size} bytes, expected #{FIXTURE_ZIP[:size]}" unless io.size == FIXTURE_ZIP[:size]

            zip = Zip::File.new(io, buffer: true)
            todo.each_key do |name|
                entry = zip.find_entry(name) or raise "#{name}: not in #{FIXTURE_ZIP[:name]}"
                tmp = File.join(dir, "#{name}.part")
                File.open(tmp, 'wb') { |f| entry.get_input_stream { |s| IO.copy_stream(s, f) } }
                File.rename(tmp, File.join(dir, name))
                warn "#{FIXTURE_DIR}/#{name}: #{entry.size} bytes from #{FIXTURE_ZIP[:name]}"
            end
        end
        FIXTURES.map do |name, sha|
            path = File.join(dir, name)
            GeslaMirror.check_file!(path, sha256: sha, what: "#{FIXTURE_DIR}/#{name}")
            { 'path' => "#{FIXTURE_DIR}/#{name}", 'sha256' => sha, 'size' => File.size(path),
              'source_url' => share_url(FIXTURE_ZIP), 'source_member' => "#{FIXTURE_ZIP[:name]}!#{name}",
              'fetched_at' => File.mtime(path).utc.iso8601, 'role' => 'fixture' }
        end
    end

    # Splits +zip+ into PART_SIZE parts named <zip>.000, .001, ... under parts/.
    def split(zip, stage, part_size)
        dir = File.join(stage, 'parts')
        FileUtils.rm_rf(dir)
        FileUtils.mkdir_p(dir)
        base = File.basename(zip)
        parts = []
        File.open(zip, 'rb') do |src|
            until src.eof?
                rel = format('parts/%s.%03d', base, parts.size)
                tmp = File.join(stage, "#{rel}.tmp")
                File.open(tmp, 'wb') { |f| IO.copy_stream(src, f, part_size) }
                File.rename(tmp, File.join(stage, rel))
                parts << rel
            end
        end
        parts
    end

    def build(opts)
        stage = opts[:stage]
        FileUtils.mkdir_p(stage)
        zip_name = RELEASE[:zip][:name]
        zip = File.join(stage, zip_name)
        if opts[:file]
            FileUtils.ln_sf(File.expand_path(opts[:file]), zip) unless File.exist?(zip)
            zip_src = { 'source_url' => share_url(RELEASE[:zip]), 'fetched_at' => File.mtime(opts[:file]).utc.iso8601 }
        else
            zip_src = get_share(RELEASE[:zip], zip)
        end
        csv = File.join(stage, 'metadata', RELEASE[:metadata][:name])
        csv_src = get_share(RELEASE[:metadata], csv)

        warn "hashing #{zip_name} ..."
        zip_sha = GeslaMirror.sha256_file(zip)
        members = []
        GeslaMirror.each_member(zip) do |name, size, sha|
            members << { 'name' => name, 'size' => size, 'sha256' => sha }
            warn "  #{members.size} members hashed" if (members.size % 500).zero?
        end
        warn "#{members.size} members"

        fixtures = fetch_fixtures(stage)
        parts = split(zip, stage, opts[:part_size])
        part_files = parts.map do |rel|
            path = File.join(stage, rel)
            { 'path' => rel, 'sha256' => GeslaMirror.sha256_file(path), 'size' => File.size(path),
              'role' => 'part' }.merge(zip_src)
        end
        csv_file = { 'path' => "metadata/#{RELEASE[:metadata][:name]}", 'sha256' => GeslaMirror.sha256_file(csv),
                     'size' => File.size(csv), 'role' => 'metadata' }.merge(csv_src)

        manifest = {
            'dataset' => RELEASE[:dataset], 'version' => RELEASE[:version],
            'landing_page' => RELEASE[:landing_page], 'licence' => RELEASE[:licence],
            'licence_url' => RELEASE[:licence_url], 'citation' => RELEASE[:citation],
            'note' => 'Private mirror: never publish these files. Parts concatenate, in order, to the archive. ' \
                      'members lists every record in the archive; the metadata CSV is published beside it.',
            'retrieved_utc' => zip_src['fetched_at'],
            'archive' => { 'name' => zip_name, 'size' => File.size(zip), 'sha256' => zip_sha,
                           'parts' => parts, 'part_size' => opts[:part_size] }.merge(zip_src),
            'files' => part_files + [csv_file] + fixtures,
            'members' => members,
        }
        path = File.join(stage, 'MANIFEST.json')
        File.write("#{path}.tmp", "#{JSON.pretty_generate(manifest)}\n")
        File.rename("#{path}.tmp", path)
        warn "wrote #{path}: archive #{zip_sha}, #{parts.size} parts, #{members.size} members + metadata CSV, " \
             "#{fixtures.size} fixtures"
        path
    end

    def upload(stage, version, source: GeslaMirror::BucketSource.new)
        manifest_path = File.join(stage, 'MANIFEST.json')
        manifest = JSON.parse(File.read(manifest_path))
        pre = GeslaMirror.prefix(version)
        manifest['files'].each do |f|
            path = File.join(stage, f['path'])
            GeslaMirror.check_file!(path, sha256: f['sha256'], size: f['size'], what: f['path'])
            key = "#{pre}/#{f['path']}"
            if (begin; source.head(key); rescue StandardError; nil; end) == f['size']
                warn "#{key}: already in the bucket with the right size"
                next
            end
            source.put(path, key)
            warn "put #{key} (#{f['size']} bytes)"
        end
        key = GeslaMirror.manifest_key(version)
        source.put(manifest_path, key)
        got = source.head(key)
        raise "#{key}: bucket has #{got} bytes, local #{File.size(manifest_path)}" unless got == File.size(manifest_path)

        pinned = GeslaMirror.write_pin_object(key, manifest_path)
        warn "pinned #{key}: sha256 #{pinned['sha256']}, #{pinned['size']} bytes"
    end
end

if $PROGRAM_NAME == __FILE__
    opts = { stage: File.join(GeslaMirror.otc_work, GeslaMirror.prefix), part_size: FetchRelease::PART_SIZE }
    OptionParser.new do |o|
        o.on('--file ZIP', 'use a local copy of the release zip') { |f| opts[:file] = f }
        o.on('--stage DIR', 'where to build the mirror (default: the bucket cache)') { |d| opts[:stage] = File.expand_path(d) }
        o.on('--part-size BYTES', Integer) { |n| opts[:part_size] = n }
        o.on('--upload', 'upload to the work bucket and pin MANIFEST.json') { opts[:upload] = true }
        o.on('--upload-only', 'upload an already built stage directory') { opts[:upload] = opts[:skip_build] = true }
    end.parse!

    begin
        FetchRelease.build(opts) unless opts[:skip_build]
        FetchRelease.upload(opts[:stage], FetchRelease::RELEASE[:version]) if opts[:upload]
    rescue GeslaMirror::ShaError => e
        abort "fetch_release: #{e.message}"
    end
end
