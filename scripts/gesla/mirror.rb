# frozen_string_literal: true

# Shared code for the GESLA mirror (BP S1): the private work bucket, SHA-256 checks, the CloudKit
# download of the GESLA release files, and the zip member listing.
#
# fetch_release.rb builds the mirror (once per GESLA release); fetch_mirror.rb reads it (every build).

require 'json'
require 'digest'
require 'fileutils'
require 'net/http'
require 'uri'
require 'open3'
require 'time'

module GeslaMirror
    ROOT     = File.expand_path('../..', __dir__)
    PIN_PATH = File.join(ROOT, 'data', 'gesla', 'inputs.lock.json')
    OTC_BIN  = File.join(ROOT, 'bin', 'otc-work')
    BUCKET   = 'opentideconstants-work'
    VERSION  = '4.1'

    # Any file whose SHA-256 or size differs from what it is pinned to.
    class ShaError < StandardError; end

    module_function

    def otc_work
        File.expand_path(ENV.fetch('OTC_WORK', '~/.local/share/opentideconstants/work'))
    end

    def prefix(version = VERSION)
        "inputs/gesla/#{version}"
    end

    def manifest_key(version = VERSION)
        "#{prefix(version)}/MANIFEST.json"
    end

    def sha256_file(path)
        Digest::SHA256.file(path).hexdigest
    end

    # Raises ShaError naming +what+ unless +path+ has the expected size and SHA-256.
    def check_file!(path, sha256:, size: nil, what: path)
        got_size = File.size(path)
        if size && got_size != size
            raise ShaError, "SHA-256 check failed for #{what}: size #{got_size}, expected #{size}"
        end

        got = sha256_file(path)
        raise ShaError, "SHA-256 mismatch for #{what}: got #{got}, expected #{sha256}" unless got == sha256

        got
    end

    def utc_now
        Time.now.utc.iso8601
    end

    # --- the committed pin -------------------------------------------------------------------------

    def read_pin(path = PIN_PATH)
        JSON.parse(File.read(path))
    end

    def pin_entry(pin, key)
        pin.fetch('objects').fetch(key) { raise ShaError, "#{key} is not pinned in data/gesla/inputs.lock.json" }
    end

    # Adds or replaces one bucket object in the pin, keeping the others.
    def write_pin_object(key, path, pin_path: PIN_PATH, bucket: BUCKET)
        pin = File.exist?(pin_path) ? JSON.parse(File.read(pin_path)) : {}
        pin['gesla_version'] ||= VERSION
        pin['bucket']        ||= bucket
        pin['objects']       ||= {}
        pin['objects'][key] = { 'uri' => "s3://#{pin['bucket']}/#{key}",
                                'sha256' => sha256_file(path), 'size' => File.size(path) }
        pin['objects'] = pin['objects'].sort.to_h
        File.write(pin_path, "#{JSON.pretty_generate(pin.slice('gesla_version', 'bucket', 'objects'))}\n")
        pin['objects'][key]
    end

    # --- sources: the work bucket (through bin/otc-work), or a local directory with the same layout --

    class BucketSource
        def describe = "s3://#{ENV.fetch('OTC_WORK_BUCKET', BUCKET)}"

        def get(key, dest)
            run!('get', key, dest)
        end

        def put(path, key)
            run!('put', path, key)
        end

        def head(key)
            Integer(run!('head', key).strip)
        end

        private

        def run!(*args)
            out, err, st = Open3.capture3(OTC_BIN, *args)
            raise "bin/otc-work #{args.first} #{args[1]} failed: #{err.strip}" unless st.success?

            out
        end
    end

    class DirSource
        def initialize(dir) = @dir = File.expand_path(dir)
        def describe = @dir

        def get(key, dest)
            src = File.join(@dir, key)
            raise "#{src}: no such file" unless File.file?(src)

            FileUtils.mkdir_p(File.dirname(dest))
            FileUtils.cp(src, dest)
        end

        def put(path, key)
            dest = File.join(@dir, key)
            FileUtils.mkdir_p(File.dirname(dest))
            FileUtils.cp(path, dest)
        end

        def head(key) = File.size(File.join(@dir, key))
    end

    # --- zip members ---------------------------------------------------------------------------------

    # Yields [name, size, sha256] for every file member of +zip_path+, in central-directory order.
    # rubyzip inflates each member and checks its CRC-32 and size while it reads.
    def each_member(zip_path)
        require 'zip'
        Zip.on_exists_proc = false
        Zip::File.open(zip_path) do |zip|
            zip.each do |entry|
                next unless entry.file?

                sha = Digest::SHA256.new
                n = 0
                entry.get_input_stream do |io|
                    while (chunk = io.read(1 << 20))
                        sha.update(chunk)
                        n += chunk.bytesize
                    end
                end
                raise ShaError, "#{entry.name}: read #{n} bytes, central directory says #{entry.size}" unless n == entry.size

                yield entry.name, n, sha.hexdigest
            end
        end
    end

    # --- CloudKit (the GESLA download links are public iCloud Drive shares) -------------------------

    RESOLVE_URL = 'https://ckdatabasews.icloud.com/database/1/com.apple.cloudkit/production/public/records/resolve'

    # The public, keyless records/resolve call returns a signed download URL that expires; callers
    # re-resolve on 403/410.  Returns [url, size].
    def resolve_share(short_guid, file_name)
        uri = URI(RESOLVE_URL)
        req = Net::HTTP::Post.new(uri, 'Content-Type' => 'text/plain', 'Origin' => 'https://www.icloud.com')
        req.body = JSON.generate(shortGUIDs: [{ value: short_guid }])
        resp = Net::HTTP.start(uri.host, uri.port, use_ssl: true) { |h| h.request(req) }
        raise "CloudKit resolve #{short_guid}: HTTP #{resp.code}" unless resp.is_a?(Net::HTTPSuccess)

        content = JSON.parse(resp.body).dig('results', 0, 'rootRecord', 'fields', 'fileContent', 'value')
        raise "CloudKit resolve #{short_guid}: no fileContent" unless content

        [content.fetch('downloadURL').gsub('${f}', file_name), content.fetch('size')]
    end

    # Downloads a share to +dest+ in ranged requests.  It resumes from +dest+.part, and re-resolves
    # the signed URL when it expires.  The finished file is renamed into place.
    def download_share(short_guid, file_name, dest, chunk: 256 << 20, tries: 8, log: $stderr)
        url, size = resolve_share(short_guid, file_name)
        part = "#{dest}.part"
        FileUtils.mkdir_p(File.dirname(dest))
        File.open(part, 'ab') do |f|
            failures = 0
            while (pos = f.size) < size
                last = [pos + chunk, size].min - 1
                begin
                    http_range(url, pos, last) { |data| f.write(data) }
                    f.flush
                    failures = 0
                    log.puts format('%s: %d / %d bytes (%.0f%%)', file_name, f.size, size, 100.0 * f.size / size)
                rescue StandardError => e
                    failures += 1
                    raise "#{file_name}: download failed #{failures} times in a row: #{e.message}" if failures >= tries

                    log.puts "#{file_name}: #{e.message}; re-resolving and resuming at #{f.size}"
                    sleep 2 * failures
                    url, = resolve_share(short_guid, file_name)
                end
            end
        end
        raise "#{file_name}: got #{File.size(part)} bytes, CloudKit says #{size}" unless File.size(part) == size

        File.rename(part, dest)
        size
    end

    class RangeMismatch < StandardError; end

    def http_range(url, first, last, &block)
        uri = URI(url)
        Net::HTTP.start(uri.host, uri.port, use_ssl: true, read_timeout: 120) do |http|
            req = Net::HTTP::Get.new(uri, 'Range' => "bytes=#{first}-#{last}")
            http.request(req) do |resp|
                # A range that covers a whole small file can come back as a plain 200 with the full body.
                if resp.code == '200' && first.zero? && resp['Content-Length'].to_i == last + 1
                    resp.read_body(&block)
                    next
                end
                raise "HTTP #{resp.code}" unless resp.code == '206'

                range = resp['Content-Range'].to_s
                raise RangeMismatch, "Content-Range #{range.inspect}, asked #{first}-#{last}" unless range.start_with?("bytes #{first}-#{last}/")

                resp.read_body(&block)
            end
        end
    end

    # A read-only, seekable IO over a CloudKit share, for reading a few members of a large zip
    # without downloading it.  rubyzip opens it with buffer: true.
    class RemoteIO
        BLOCK = 1 << 20

        attr_reader :size

        def initialize(short_guid, file_name)
            @guid = short_guid
            @name = file_name
            @url, @size = GeslaMirror.resolve_share(short_guid, file_name)
            @pos = 0
            @cache = {}
        end

        def initialize_copy(_other)
            super
            @pos = 0
        end

        def pos = @pos
        alias tell pos

        def seek(offset, whence = IO::SEEK_SET)
            @pos = case whence
                   when IO::SEEK_SET then offset
                   when IO::SEEK_CUR then @pos + offset
                   when IO::SEEK_END then @size + offset
                   end
            0
        end

        def eof? = @pos >= @size
        alias eof eof?

        def read(length = nil, outbuf = nil)
            length ||= @size - @pos
            return(length.zero? ? +'' : nil) if @pos >= @size && length.positive?

            out = +''.b
            while out.bytesize < length && @pos < @size
                idx = @pos / BLOCK
                blk = block(idx)
                off = @pos - (idx * BLOCK)
                take = [length - out.bytesize, blk.bytesize - off].min
                out << blk.byteslice(off, take)
                @pos += take
            end
            outbuf ? outbuf.replace(out) : out
        end

        def binmode = self
        def close = nil

        private

        def block(idx)
            @cache[idx] ||= begin
                @cache.shift while @cache.size > 64
                first = idx * BLOCK
                last = [first + BLOCK, @size].min - 1
                data = +''.b
                4.times do |k|
                    data = +''.b
                    GeslaMirror.http_range(@url, first, last) { |d| data << d }
                    break
                rescue StandardError
                    raise if k == 3

                    sleep 2
                    @url, = GeslaMirror.resolve_share(@guid, @name)
                end
                data
            end
        end
    end
end
