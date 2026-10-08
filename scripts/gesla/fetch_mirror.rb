#!/usr/bin/env ruby
# frozen_string_literal: true

# BP S1: fetch the GESLA mirror from the private work bucket into the local cache, and verify it.
# Every build runs this before it reads GESLA.
#
#   ruby scripts/gesla/fetch_mirror.rb [--version V] [--cache DIR] [--source DIR] [--pin FILE]
#                                      [--no-members] [--refresh]
#
# 1. MANIFEST.json: downloaded (or taken from the cache) and checked against the SHA-256 and size
#    pinned in data/gesla/inputs.lock.json.
# 2. Every file in the manifest (zip parts, metadata CSV, 4.0 fixtures): a cached file with the
#    right SHA-256 is kept; a missing one is downloaded to <file>.part, checked, and renamed.
# 3. The parts are concatenated, in order, to <zip>.part; its size and SHA-256 are checked against
#    the manifest's archive entry before it is renamed into place.
# 4. Every member of the zip is read (inflated, CRC-checked) and its size and SHA-256 compared with
#    the manifest; the member names must match exactly.  --no-members skips this step.
#
# Any mismatch stops with a message naming the file or member, and a non-zero exit.  A cached file
# with a wrong SHA-256 also stops the fetch (--refresh downloads it again instead).
#
# The cache is $OTC_WORK/inputs/gesla/<version>/ (default ~/.local/share/opentideconstants/work);
# it may be deleted at any time.  --source DIR reads a directory with the bucket's layout instead
# of the bucket (used by the spec).

require 'optparse'
require_relative 'mirror'

module FetchMirror
    module_function

    def log(msg) = warn("fetch_mirror: #{msg}")

    # Fetches +key+ into +dest+ unless a cached copy matches; checks it against sha256/size.
    def ensure_file(source, key, dest, sha256:, size:, what:, refresh:)
        if File.exist?(dest)
            begin
                GeslaMirror.check_file!(dest, sha256: sha256, size: size, what: "#{what} (cached)")
                return :cached
            rescue GeslaMirror::ShaError
                raise unless refresh

                log "#{what}: cached copy does not match; downloading it again"
            end
        end
        part = "#{dest}.part"
        FileUtils.rm_f(part)
        source.get(key, part)
        begin
            GeslaMirror.check_file!(part, sha256: sha256, size: size, what: what)
        rescue GeslaMirror::ShaError
            FileUtils.rm_f(part)
            raise
        end
        File.rename(part, dest)
        :fetched
    end

    def reassemble(dir, archive)
        zip = File.join(dir, archive.fetch('name'))
        if File.exist?(zip) && File.size(zip) == archive['size']
            begin
                GeslaMirror.check_file!(zip, sha256: archive['sha256'], size: archive['size'], what: archive['name'])
                return zip
            rescue GeslaMirror::ShaError
                log "#{archive['name']}: cached copy does not match; reassembling it"
            end
        end
        part = "#{zip}.part"
        File.open(part, 'wb') do |out|
            archive.fetch('parts').each { |rel| File.open(File.join(dir, rel), 'rb') { |f| IO.copy_stream(f, out) } }
        end
        begin
            GeslaMirror.check_file!(part, sha256: archive['sha256'], size: archive['size'],
                                          what: "#{archive['name']} (reassembled from #{archive['parts'].size} parts)")
        rescue GeslaMirror::ShaError
            FileUtils.rm_f(part)
            raise
        end
        File.rename(part, zip)
        zip
    end

    def verify_members(zip, members)
        want = members.to_h { |m| [m['name'], m] }
        seen = 0
        GeslaMirror.each_member(zip) do |name, size, sha|
            m = want.delete(name) or raise GeslaMirror::ShaError, "member #{name}: in the zip but not in MANIFEST.json"
            unless m['size'] == size && m['sha256'] == sha
                raise GeslaMirror::ShaError, "SHA-256 mismatch for member #{name}: got #{sha} (#{size} bytes), " \
                                             "manifest #{m['sha256']} (#{m['size']} bytes)"
            end
            seen += 1
            log "#{seen} members verified" if (seen % 1000).zero?
        end
        raise GeslaMirror::ShaError, "#{want.size} manifest members missing from the zip, e.g. #{want.keys.first}" unless want.empty?

        seen
    end

    def run(opts)
        pin = GeslaMirror.read_pin(opts[:pin])
        version = opts[:version] || pin.fetch('gesla_version')
        prefix = GeslaMirror.prefix(version)
        dir = File.join(opts[:cache], prefix)
        FileUtils.mkdir_p(dir)
        source = opts[:source] ? GeslaMirror::DirSource.new(opts[:source]) : GeslaMirror::BucketSource.new
        log "GESLA #{version}: #{source.describe}/#{prefix} -> #{dir}"

        mkey = GeslaMirror.manifest_key(version)
        mpin = GeslaMirror.pin_entry(pin, mkey)
        mpath = File.join(dir, 'MANIFEST.json')
        ensure_file(source, mkey, mpath, sha256: mpin['sha256'], size: mpin['size'],
                                         what: 'MANIFEST.json (against the pin in data/gesla/inputs.lock.json)',
                                         refresh: opts[:refresh])
        manifest = JSON.parse(File.read(mpath))
        log "MANIFEST.json matches the pin (#{mpin['sha256']})"

        counts = Hash.new(0)
        manifest.fetch('files').each do |f|
            state = ensure_file(source, "#{prefix}/#{f['path']}", File.join(dir, f['path']),
                                sha256: f['sha256'], size: f['size'], what: f['path'], refresh: opts[:refresh])
            counts[state] += 1
            counts[f['role']] += 1
        end
        log "#{manifest['files'].size} files verified (#{counts[:fetched]} downloaded, #{counts[:cached]} cached): " \
            "#{counts['part']} parts, #{counts['metadata']} metadata, #{counts['fixture']} fixtures"

        archive = manifest.fetch('archive')
        zip = reassemble(dir, archive)
        log "#{archive['name']} verified: #{archive['size']} bytes, sha256 #{archive['sha256']}"

        if opts[:members]
            n = verify_members(zip, manifest.fetch('members'))
            log "#{n} members verified against MANIFEST.json"
            log "verified #{n + counts['metadata']} entries (#{n} members + #{counts['metadata']} metadata CSV), " \
                "#{counts['part']} parts, #{counts['fixture']} fixtures"
        end
        zip
    end
end

if $PROGRAM_NAME == __FILE__
    opts = { cache: GeslaMirror.otc_work, pin: GeslaMirror::PIN_PATH, members: true, refresh: false }
    OptionParser.new do |o|
        o.on('--version V', 'GESLA version (default: the pin)') { |v| opts[:version] = v }
        o.on('--cache DIR', 'local cache root (default $OTC_WORK)') { |d| opts[:cache] = File.expand_path(d) }
        o.on('--source DIR', 'read a local directory with the bucket layout') { |d| opts[:source] = d }
        o.on('--pin FILE', 'pin file (default data/gesla/inputs.lock.json)') { |f| opts[:pin] = f }
        o.on('--no-members', 'skip the member check') { opts[:members] = false }
        o.on('--refresh', 'download cached files that do not match again') { opts[:refresh] = true }
    end.parse!

    begin
        FetchMirror.run(opts)
    rescue GeslaMirror::ShaError => e
        abort "fetch_mirror: #{e.message}"
    end
end
