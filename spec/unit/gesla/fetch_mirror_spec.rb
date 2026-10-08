# frozen_string_literal: true

require 'json'
require 'digest'
require 'fileutils'
require 'open3'
require 'tmpdir'
require 'zip'

# BP S1: fetch_mirror.rb takes the GESLA mirror from the bucket (here a local directory with the
# bucket's layout), reassembles the zip from its parts and checks every file and member against
# MANIFEST.json, which it checks against the committed pin.  Any mismatch stops it with a SHA error.
RSpec.describe 'scripts/gesla/fetch_mirror.rb' do
    root   = File.expand_path('../../..', __dir__)
    let(:script) { File.join(root, 'scripts', 'gesla', 'fetch_mirror.rb') }
    prefix = 'inputs/gesla/9.9'

    sha = ->(path) { Digest::SHA256.file(path).hexdigest }

    around do |ex|
        Dir.mktmpdir('fetch-mirror') do |tmp|
            @tmp = tmp
            ex.run
        end
    end

    let(:bucket) { File.join(@tmp, 'bucket') }
    let(:cache)  { File.join(@tmp, 'cache') }
    let(:pin)    { File.join(@tmp, 'inputs.lock.json') }
    let(:src)    { File.join(bucket, prefix) }

    # A small zip in the GESLA shape (deflated text records), stored as three parts, plus a metadata
    # CSV and one fixture, with a manifest and a pin, laid out like the bucket.
    before do
        FileUtils.mkdir_p(File.join(src, 'parts'))
        FileUtils.mkdir_p(File.join(src, 'metadata'))
        FileUtils.mkdir_p(File.join(src, 'fixtures', 'gesla-4.0-wsv'))
        zip_path = File.join(@tmp, 'GESLA9.9_ALL.zip')
        records = (1..4).to_h do |i|
            body = (0...2000).map { |t| format("2020/01/01 %05d %8.3f 1 1\n", t, Math.sin(t * 0.1 + i)) }.join
            ["station#{i}-s#{i}-xyz-src", body]
        end
        Zip::File.open(zip_path, create: true) { |z| records.each { |n, b| z.get_output_stream(n) { |o| o.write(b) } } }

        bytes = File.binread(zip_path)
        part_size = (bytes.bytesize / 3) + 1
        parts = bytes.bytes.each_slice(part_size).each_with_index.map do |chunk, i|
            rel = format('parts/GESLA9.9_ALL.zip.%03d', i)
            File.binwrite(File.join(src, rel), chunk.pack('C*'))
            rel
        end
        File.write(File.join(src, 'metadata', 'GESLA9-9_ALL.csv'), "FILE NAME,SITE NAME\nstation1-s1-xyz-src,One\n")
        File.write(File.join(src, 'fixtures', 'gesla-4.0-wsv', 'fixture-wsv'), "fixture\n")

        files = (parts.map { |p| [p, 'part'] } +
                 [['metadata/GESLA9-9_ALL.csv', 'metadata'], ['fixtures/gesla-4.0-wsv/fixture-wsv', 'fixture']])
                .map do |rel, role|
                    path = File.join(src, rel)
                    { 'path' => rel, 'sha256' => sha.call(path), 'size' => File.size(path), 'role' => role,
                      'source_url' => 'test', 'fetched_at' => '2026-10-08T00:00:00Z' }
                end
        members = records.map { |n, b| { 'name' => n, 'size' => b.bytesize, 'sha256' => Digest::SHA256.hexdigest(b) } }
        manifest = { 'version' => '9.9',
                     'archive' => { 'name' => 'GESLA9.9_ALL.zip', 'size' => bytes.bytesize,
                                    'sha256' => Digest::SHA256.hexdigest(bytes), 'parts' => parts },
                     'files' => files, 'members' => members }
        write_manifest(manifest)
    end

    def write_manifest(manifest)
        path = File.join(src, 'MANIFEST.json')
        File.write(path, JSON.pretty_generate(manifest))
        key = 'inputs/gesla/9.9/MANIFEST.json'
        File.write(pin, JSON.generate('gesla_version' => '9.9', 'bucket' => 'test',
                                      'objects' => { key => { 'uri' => "s3://test/#{key}",
                                                              'sha256' => Digest::SHA256.file(path).hexdigest,
                                                              'size' => File.size(path) } }))
    end

    def fetch(*extra)
        out, err, st = Open3.capture3('ruby', script, '--source', bucket, '--cache', cache, '--pin', pin, *extra)
        [st.exitstatus, out + err]
    end

    def manifest = JSON.parse(File.read(File.join(src, 'MANIFEST.json')))

    def flip_byte(path, at)
        bytes = File.binread(path)
        bytes.setbyte(at, bytes.getbyte(at) ^ 0x01)
        File.binwrite(path, bytes)
    end

    it 'fetches into a clean cache, reassembles the zip and verifies every member' do
        code, log = fetch
        expect(code).to eq(0), log
        expect(log).to include('MANIFEST.json matches the pin')
        expect(log).to include('5 files verified (5 downloaded, 0 cached): 3 parts, 1 metadata, 1 fixtures')
        expect(log).to include('4 members verified against MANIFEST.json')
        expect(log).to include('verified 5 entries (4 members + 1 metadata CSV)')
        zip = File.join(cache, prefix, 'GESLA9.9_ALL.zip')
        expect(sha.call(zip)).to eq(manifest['archive']['sha256'])
        expect(Dir.glob("#{cache}/**/*.part")).to be_empty
    end

    it 'keeps cached files with the right SHA-256 on a second run' do
        expect(fetch.first).to eq(0)
        code, log = fetch
        expect(code).to eq(0), log
        expect(log).to include('(0 downloaded, 5 cached)')
    end

    it 'stops with a SHA error naming the part when a part in the bucket is corrupted' do
        flip_byte(File.join(src, 'parts', 'GESLA9.9_ALL.zip.001'), 10)
        code, log = fetch
        expect(code).not_to eq(0)
        expect(log).to match(%r{SHA-256 mismatch for parts/GESLA9\.9_ALL\.zip\.001: got \h{64}, expected \h{64}})
        expect(File).not_to exist(File.join(cache, prefix, 'parts', 'GESLA9.9_ALL.zip.001'))
        expect(File).not_to exist(File.join(cache, prefix, 'GESLA9.9_ALL.zip'))
    end

    it 'stops with a SHA error naming the part when a cached part is corrupted' do
        expect(fetch.first).to eq(0)
        FileUtils.rm_f(File.join(cache, prefix, 'GESLA9.9_ALL.zip'))
        flip_byte(File.join(cache, prefix, 'parts', 'GESLA9.9_ALL.zip.002'), 0)
        code, log = fetch
        expect(code).not_to eq(0)
        expect(log).to match(%r{SHA-256 mismatch for parts/GESLA9\.9_ALL\.zip\.002 \(cached\)})
        expect(File).not_to exist(File.join(cache, prefix, 'GESLA9.9_ALL.zip'))

        code, log = fetch('--refresh')
        expect(code).to eq(0), log
    end

    it 'stops with a SHA error naming the member when a manifest member entry is wrong' do
        m = manifest
        m['members'][2]['sha256'] = '0' * 64
        write_manifest(m)
        code, log = fetch
        expect(code).not_to eq(0)
        expect(log).to include("SHA-256 mismatch for member #{m['members'][2]['name']}: got")
        expect(log).to include("manifest #{'0' * 64}")
    end

    it 'stops when MANIFEST.json does not match the pin' do
        File.write(File.join(src, 'MANIFEST.json'), "#{File.read(File.join(src, 'MANIFEST.json'))}\n")
        code, log = fetch
        expect(code).not_to eq(0)
        expect(log).to include('MANIFEST.json (against the pin in data/gesla/inputs.lock.json)')
        expect(log).to match(/size \d+, expected \d+|SHA-256 mismatch/)
    end

    it 'stops when the reassembled zip does not match the archive entry' do
        m = manifest
        m['archive']['sha256'] = 'f' * 64
        write_manifest(m)
        code, log = fetch
        expect(code).not_to eq(0)
        expect(log).to include('SHA-256 mismatch for GESLA9.9_ALL.zip (reassembled from 3 parts)')
        expect(File).not_to exist(File.join(cache, prefix, 'GESLA9.9_ALL.zip'))
    end
end
