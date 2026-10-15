# frozen_string_literal: true

require 'rspec'
require 'json'
require 'open3'
require 'tmpdir'
require 'rbconfig'

RSpec.describe 'the native prediction vectors' do
    let(:script) { File.expand_path('../../../scripts/otc/vectors/generate.rb', __dir__) }
    let(:inputs) { ENV.fetch('OTC_VECTOR_INPUTS') }
    let(:release) { ENV.fetch('OTC_VECTOR_RELEASE') }

    # The real release and its copied inputs are an external proof profile.
    before(:context) do
        profile = %w[OTC_VECTOR_INPUTS OTC_VECTOR_RELEASE]
        if profile.none? { |name| ENV.key?(name) }
            skip "Prediction vectors require external pinned fixtures: #{profile.join(', ')}; see scripts/otc/vectors/README.md"
        end
    end

    def generate(path, *extra)
        Open3.capture2e(RbConfig.ruby, script, '--release', release,
                       '--run-dir', File.join(inputs, 'congen'), '--output', path,
                       '--only', '8443970', *extra)
    end

    it 'regenerates actual Boston heights and three 31-day windows byte for byte' do
        Dir.mktmpdir do |dir|
            first, second = %w[first.json second.json].map { |f| File.join(dir, f) }
            log, status = generate(first)
            expect(status.success?).to be(true), log
            doc = JSON.parse(File.read(first))
            vectors = doc.fetch('vectors')
            heights = vectors.find { |v| v['op'] == 'heights_at' }
            expect(heights.fetch('args').fetch('times').size).to be >= 200
            expect(vectors.count { |v| v['op'] == 'extremes' }).to eq(3)
            expect(doc.fetch('checks').fetch('problems')).to eq([])
            log, status = generate(second)
            expect(status.success?).to be(true), log
            expect(File.binread(second)).to eq(File.binread(first))
        end
    end

    it 'refuses a fractional instant before it writes vectors' do
        Dir.mktmpdir do |dir|
            path = File.join(dir, 'vectors.json')
            log, status = generate(path, '--instant', '2010-03-01T12:00:00.7Z')
            expect(status.success?).to be(false)
            expect(log).to include('whole second')
            expect(File.exist?(path)).to be(false)
        end
    end
end
