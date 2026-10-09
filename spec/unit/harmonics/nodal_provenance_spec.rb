# frozen_string_literal: true

# The engine's nodal corrections must come from lib/nodal_schureman.rb, the
# clean-room implementation written from SP98 via a written spec. No code or
# comment derived from libcongen (GPL) may remain in the application source.
RSpec.describe 'nodal correction provenance' do
    root = File.expand_path('../../..', __dir__)
    sources = Dir.glob(%w[*.rb lib/**/*.rb clients/**/*.rb models/**/*.rb scripts/**/*.rb], base: root).sort

    def offending(root, files, pattern)
        files.flat_map do |f|
            File.foreach(File.join(root, f)).each_with_index.select { |line, _| line.match?(pattern) }
                .map { |line, i| "#{f}:#{i + 1}: #{line.strip}" }
        end
    end

    it 'scans the application source' do
        expect(sources).to include('lib/harmonics_engine.rb', 'webcaltides.rb')
    end

    it 'has no libcongen reference' do
        expect(offending(root, sources, /congen/i)).to eq([])
    end

    it 'has no BASES table' do
        expect(offending(root, sources, /\bBASES(_ORDER)?\b/)).to eq([])
    end

    it 'takes the engine-computed corrections from NodalSchureman' do
        engine = File.read(File.join(root, 'lib/harmonics_engine.rb'))
        expect(engine.include?('NodalSchureman.compute')).to be(true), 'lib/harmonics_engine.rb does not call NodalSchureman.compute'
    end
end
