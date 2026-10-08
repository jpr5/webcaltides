# frozen_string_literal: true

require 'tmpdir'

# harmonics_checksum is in every harmonics cache name, so it runs on every harmonics ICS request
# (cache hits too).  The engine loads its dataset once per instance, so the checksum of those files
# is computed once per instance as well, not by hashing ~6.7 MB on each call.
RSpec.describe Harmonics::Engine, '#source_files_checksum' do
    let(:logger) { Logger.new('/dev/null') }

    def uncached_checksum(engine)
        xtide, ticon = %i[@xtide_file @ticon_file].map do |ivar|
            f = engine.instance_variable_get(ivar)
            File.exist?(f) ? Digest::MD5.file(f).hexdigest[0, 8] : '00000000'
        end
        types = engine.instance_variable_get(:@noaa_station_types_file)
        xtide = Digest::MD5.hexdigest(xtide + Digest::MD5.file(types).hexdigest)[0, 8] if File.exist?(types)
        [xtide, ticon].join('_')
    end

    it 'hashes the data files once per engine across repeated calls, with the same value' do
        Dir.mktmpdir do |dir|
            engine = described_class.new(logger, dir)
            expected = uncached_checksum(engine)

            allow(Digest::MD5).to receive(:file).and_call_original
            first  = engine.source_files_checksum
            second = engine.source_files_checksum

            expect([first, second]).to eq([expected, expected])
            expect(Digest::MD5).to have_received(:file).exactly(3).times # one per data file, first call only
        end
    end

    it 'does not rehash the data files on repeated WebCalTides.harmonics_checksum calls' do
        WebCalTides.harmonics_checksum # warm: the engine is a per-process singleton

        allow(Digest::MD5).to receive(:file).and_call_original
        2.times { WebCalTides.harmonics_checksum }

        expect(Digest::MD5).not_to have_received(:file)
    end
end
