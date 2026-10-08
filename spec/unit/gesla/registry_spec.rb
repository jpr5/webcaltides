# frozen_string_literal: true

require 'json'
require 'digest'
require 'fileutils'

# The GESLA build (BP S0) freezes the station registry from the served ticon.json.  Every later step
# keeps these 2,706 ids and their identity fields; this spec pins the registry to the served file.
RSpec.describe 'GESLA station registry' do
    root          = File.expand_path('../../..', __dir__)
    ticon_path    = File.join(root, 'data', 'ticon.json')
    registry_path = File.join(root, 'data', 'gesla', 'station_registry.json')
    pin_path      = File.join(root, 'data', 'gesla', 'inputs.lock.json')
    lock_key      = 'inputs/gesla/4.1/inputs.lock.json'
    otc_work      = File.expand_path(ENV.fetch('OTC_WORK', '~/.local/share/opentideconstants/work'))
    lock_path     = File.join(otc_work, lock_key)
    env_file      = File.expand_path('~/.config/opentideconstants/r2-work.env')

    identity = %w[id lat lon name region timezone units]

    let(:ticon_sha) { Digest::SHA256.file(ticon_path).hexdigest }
    let(:served)    { JSON.parse(File.read(ticon_path))['stations'] }
    let(:registry)  { JSON.parse(File.read(registry_path)) }
    let(:pin)       { JSON.parse(File.read(pin_path)) }
    let(:lock_pin)  { pin.fetch('objects').fetch(lock_key) }

    # The full lock is data: it lives in the private work bucket, and $OTC_WORK is its local cache.
    # Fetch it when the cache lacks it and bucket credentials exist; otherwise skip (CI has no copy).
    let(:lock) do
        unless File.exist?(lock_path)
            skip "#{lock_key} is not cached and #{env_file} is missing" unless File.exist?(env_file)
            FileUtils.mkdir_p(File.dirname(lock_path))
            cmd = "set -a; . '#{env_file}'; set +a; unset AWS_PROFILE; " \
                  "aws s3 cp --only-show-errors \"s3://$OTC_WORK_BUCKET/#{lock_key}\" '#{lock_path}.part'"
            raise "fetch of #{lock_key} failed" unless system('bash', '-c', cmd)
            File.rename("#{lock_path}.part", lock_path)
        end
        sha = Digest::SHA256.file(lock_path).hexdigest
        raise "#{lock_path}: SHA-256 #{sha}, pin says #{lock_pin['sha256']}" unless sha == lock_pin['sha256']

        JSON.parse(File.read(lock_path))
    end

    it 'is frozen from the served ticon.json (SHA-256 f58c960a...)' do
        expect(ticon_sha).to start_with('f58c960a')
        expect(registry['source']['sha256']).to eq(ticon_sha)
    end

    it 'holds 2,706 unique ids, sorted' do
        ids = registry['stations'].map { |s| s['id'] }
        expect(ids.size).to eq(2706)
        expect(ids.uniq.size).to eq(2706)
        expect(ids).to eq(ids.sort)
        expect(registry['count']).to eq(2706)
    end

    it 'has exactly the identity fields, equal to the served file for every id' do
        by_id = served.to_h { |s| [s['id'], s.slice(*identity)] }
        expect(registry['stations'].map { |s| s['id'] }).to match_array(by_id.keys)

        diffs = registry['stations'].reject { |s| s == by_id[s['id']] }
        expect(diffs).to be_empty
        expect(registry['stations'].map(&:keys).uniq).to eq([identity])
    end

    it 'commits only a small pin naming the bucket lock by SHA-256 and size' do
        expect(File.size(pin_path)).to be < 4096
        expect(pin['gesla_version']).to eq('4.1')
        expect(pin['bucket']).to eq('opentideconstants-work')
        expect(lock_pin['uri']).to eq("s3://opentideconstants-work/#{lock_key}")
        expect(lock_pin['sha256']).to match(/\A[0-9a-f]{64}\z/)
        expect(lock_pin['size']).to be_a(Integer).and be_positive
    end

    it 'pins the served ticon.json and the engine TCD in the bucket lock' do
        tcd = File.realpath(File.join(root, 'data', 'latest-xtide.tcd'))
        expect(lock['ticon']['sha256']).to eq(ticon_sha)
        expect(lock['tcd']['file']).to eq(File.basename(tcd))
        expect(lock['tcd']['sha256']).to eq(Digest::SHA256.file(tcd).hexdigest)
    end

    it 'pins the harness reference caches and the safety set' do
        %w[refs cache].each do |dir|
            expect(lock['harness'][dir]['files']).not_to be_empty
            expect(lock['harness'][dir]['files'].values).to all(match(/\A[0-9a-f]{64}\z/))
            expect(lock['harness'][dir]['count']).to eq(lock['harness'][dir]['files'].size)
        end
        expect(lock['harness']['safety_set.json']['sha256']).to match(/\A[0-9a-f]{64}\z/)
    end
end
