# frozen_string_literal: true

RSpec.describe WebCalTides do
    describe '.atomic_write' do
        it 'writes file content atomically' do
            with_test_cache_dir do |dir|
                path = "#{dir}/test_file.json"
                described_class.atomic_write(path, '{"key": "value"}')

                expect(File.exist?(path)).to be true
                expect(File.read(path)).to eq('{"key": "value"}')
            end
        end

        it 'does not leave temp files on success' do
            with_test_cache_dir do |dir|
                path = "#{dir}/test_file.json"
                described_class.atomic_write(path, 'content')

                temp_files = Dir.glob("#{dir}/*.tmp.*")
                expect(temp_files).to be_empty
            end
        end

        it 'cleans up temp file on write failure' do
            with_test_cache_dir do |dir|
                path = "#{dir}/test_file.json"

                # Stub File.rename to raise after File.binwrite succeeds
                allow(File).to receive(:rename).and_raise(Errno::ENOSPC.new("No space left"))

                expect {
                    described_class.atomic_write(path, 'content')
                }.to raise_error(Errno::ENOSPC)

                temp_files = Dir.glob("#{dir}/*.tmp.*")
                expect(temp_files).to be_empty
                expect(File.exist?(path)).to be false
            end
        end

        it 'overwrites existing file atomically' do
            with_test_cache_dir do |dir|
                path = "#{dir}/test_file.json"
                File.write(path, 'old content')

                described_class.atomic_write(path, 'new content')
                expect(File.read(path)).to eq('new content')
            end
        end

        it 'concurrent writes do not produce corrupted content' do
            Dir.mktmpdir('webcaltides_test_cache') do |dir|
                original_cache = Server.settings.cache_dir
                Server.set :cache_dir, dir

                path = "#{dir}/test_file.json"
                content_a = "A" * 10000
                content_b = "B" * 10000

                threads = 10.times.map do |i|
                    Thread.new do
                        content = i.even? ? content_a : content_b
                        described_class.atomic_write(path, content)
                    end
                end
                threads.each(&:join)

                # File should contain one complete content, never mixed
                result = File.read(path)
                expect(result == content_a || result == content_b).to be true

                Server.set :cache_dir, original_cache
            end
        end
    end

    describe '.retire_old_cache_files' do
        it 'deletes older monthly files for the same station/type' do
            with_test_cache_dir do |dir|
                current = "#{dir}/tides_v1_9410170_202602.json"
                old1    = "#{dir}/tides_v1_9410170_202601.json"
                old2    = "#{dir}/tides_v1_9410170_202512.json"

                [current, old1, old2].each { |f| File.write(f, 'data') }

                described_class.retire_old_cache_files(current)

                expect(File.exist?(current)).to be true
                expect(File.exist?(old1)).to be false
                expect(File.exist?(old2)).to be false
            end
        end

        it 'does not delete files for different stations' do
            with_test_cache_dir do |dir|
                current     = "#{dir}/tides_v1_9410170_202602.json"
                other_station = "#{dir}/tides_v1_8461490_202601.json"

                [current, other_station].each { |f| File.write(f, 'data') }

                described_class.retire_old_cache_files(current)

                expect(File.exist?(current)).to be true
                expect(File.exist?(other_station)).to be true
            end
        end

        it 'does not delete files with different suffixes' do
            with_test_cache_dir do |dir|
                current  = "#{dir}/tides_v1_9410170_202602.json"
                ics_file = "#{dir}/tides_v1_9410170_202601_imperial_1_0.ics"

                [current, ics_file].each { |f| File.write(f, 'data') }

                described_class.retire_old_cache_files(current)

                expect(File.exist?(current)).to be true
                expect(File.exist?(ics_file)).to be true
            end
        end

        it 'handles iCal files with option suffixes' do
            with_test_cache_dir do |dir|
                current = "#{dir}/tides_v1_9410170_202602_imperial_1_0.ics"
                old     = "#{dir}/tides_v1_9410170_202601_imperial_1_0.ics"

                [current, old].each { |f| File.write(f, 'data') }

                described_class.retire_old_cache_files(current)

                expect(File.exist?(current)).to be true
                expect(File.exist?(old)).to be false
            end
        end

        it 'no-ops gracefully when no old files exist' do
            with_test_cache_dir do |dir|
                current = "#{dir}/tides_v1_9410170_202602.json"
                File.write(current, 'data')

                expect { described_class.retire_old_cache_files(current) }.not_to raise_error
                expect(File.exist?(current)).to be true
            end
        end

        it 'no-ops gracefully when filename does not match pattern' do
            with_test_cache_dir do |dir|
                current = "#{dir}/tzs.json"
                File.write(current, 'data')

                expect { described_class.retire_old_cache_files(current) }.not_to raise_error
                expect(File.exist?(current)).to be true
            end
        end

        it 'handles current station files' do
            with_test_cache_dir do |dir|
                current = "#{dir}/currents_v1_PUG1515_202602.json"
                old     = "#{dir}/currents_v1_PUG1515_202601.json"

                [current, old].each { |f| File.write(f, 'data') }

                described_class.retire_old_cache_files(current)

                expect(File.exist?(current)).to be true
                expect(File.exist?(old)).to be false
            end
        end
    end

    describe '.cleanup_old_cache_files' do
        it 'deletes monthly data files older than current month' do
            Timecop.freeze(Time.utc(2026, 2, 15)) do
                with_test_cache_dir do |dir|
                    current = "#{dir}/tides_v1_9410170_202602.json"
                    old     = "#{dir}/tides_v1_9410170_202601.json"
                    older   = "#{dir}/tides_v1_9410170_202512.json"

                    [current, old, older].each { |f| File.write(f, 'data') }

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(current)).to be true
                    expect(File.exist?(old)).to be false
                    expect(File.exist?(older)).to be false
                end
            end
        end

        it 'deletes iCal files older than current month' do
            Timecop.freeze(Time.utc(2026, 2, 15)) do
                with_test_cache_dir do |dir|
                    current = "#{dir}/tides_v1_9410170_202602_imperial_1_0.ics"
                    old     = "#{dir}/tides_v1_9410170_202601_imperial_1_0.ics"

                    [current, old].each { |f| File.write(f, 'data') }

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(current)).to be true
                    expect(File.exist?(old)).to be false
                end
            end
        end

        it 'deletes quarterly station list files older than current quarter' do
            Timecop.freeze(Time.utc(2026, 4, 15)) do  # Q2 2026
                with_test_cache_dir do |dir|
                    current = "#{dir}/tide_stations_v1_2026Q2_noaa.json"
                    old_q1  = "#{dir}/tide_stations_v1_2026Q1_noaa.json"
                    old_q4  = "#{dir}/tide_stations_v1_2025Q4_noaa.json"

                    [current, old_q1, old_q4].each { |f| File.write(f, 'data') }

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(current)).to be true
                    expect(File.exist?(old_q1)).to be false
                    expect(File.exist?(old_q4)).to be false
                end
            end
        end

        it 'keeps current month data and iCal files' do
            Timecop.freeze(Time.utc(2026, 3, 10)) do
                with_test_cache_dir do |dir|
                    files = [
                        "#{dir}/tides_v1_9410170_202603.json",
                        "#{dir}/currents_v1_PUG1515_202603.json",
                        "#{dir}/tides_v1_9410170_202603_imperial_1_0.ics",
                    ]

                    files.each { |f| File.write(f, 'data') }

                    described_class.cleanup_old_cache_files

                    files.each { |f| expect(File.exist?(f)).to be(true), "Expected #{File.basename(f)} to be kept" }
                end
            end
        end

        it 'keeps current quarter station files' do
            Timecop.freeze(Time.utc(2026, 5, 1)) do  # Q2
                with_test_cache_dir do |dir|
                    current_q = "#{dir}/tide_stations_v1_2026Q2_noaa.json"
                    File.write(current_q, 'data')

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(current_q)).to be true
                end
            end
        end

        it 'keeps tzs.json untouched' do
            Timecop.freeze(Time.utc(2026, 2, 15)) do
                with_test_cache_dir do |dir|
                    tzs = "#{dir}/tzs.json"
                    File.write(tzs, '{"42.0 -71.0": "America/New_York"}')

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(tzs)).to be true
                end
            end
        end

        it 'keeps lunar phases for current and prior year, deletes older' do
            Timecop.freeze(Time.utc(2026, 6, 15)) do
                with_test_cache_dir do |dir|
                    current_year = "#{dir}/lunar_phases_2026.json"
                    prior_year   = "#{dir}/lunar_phases_2025.json"
                    old_year     = "#{dir}/lunar_phases_2024.json"
                    ancient      = "#{dir}/lunar_phases_2023.json"

                    [current_year, prior_year, old_year, ancient].each { |f| File.write(f, '[]') }

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(current_year)).to be true
                    expect(File.exist?(prior_year)).to be true
                    expect(File.exist?(old_year)).to be false
                    expect(File.exist?(ancient)).to be false
                end
            end
        end

        it 'reports correct count and freed bytes in log' do
            Timecop.freeze(Time.utc(2026, 2, 15)) do
                with_test_cache_dir do |dir|
                    old1 = "#{dir}/tides_v1_9410170_202601.json"
                    old2 = "#{dir}/tides_v1_8461490_202512.json"
                    File.write(old1, 'x' * 1024)  # 1KB
                    File.write(old2, 'y' * 2048)  # 2KB

                    expect($LOG).to receive(:info).with(/removed 2 files, freed 0MB/)
                    described_class.cleanup_old_cache_files
                end
            end
        end

        it 'handles empty cache directory gracefully' do
            with_test_cache_dir do |dir|
                expect { described_class.cleanup_old_cache_files }.not_to raise_error
            end
        end

        it 'skips directories in the cache dir' do
            Timecop.freeze(Time.utc(2026, 2, 15)) do
                with_test_cache_dir do |dir|
                    subdir = "#{dir}/subdir_202501"
                    FileUtils.mkdir_p(subdir)

                    expect { described_class.cleanup_old_cache_files }.not_to raise_error
                    expect(Dir.exist?(subdir)).to be true
                end
            end
        end

        it 'deletes NOAA current region files from old quarters' do
            Timecop.freeze(Time.utc(2026, 7, 1)) do  # Q3
                with_test_cache_dir do |dir|
                    current = "#{dir}/noaa_current_regions_2026Q3.json"
                    old     = "#{dir}/noaa_current_regions_2026Q2.json"

                    [current, old].each { |f| File.write(f, '{}') }

                    described_class.cleanup_old_cache_files

                    expect(File.exist?(current)).to be true
                    expect(File.exist?(old)).to be false
                end
            end
        end

        it 'handles files from multiple stations and types' do
            Timecop.freeze(Time.utc(2026, 3, 1)) do
                with_test_cache_dir do |dir|
                    keep = [
                        "#{dir}/tides_v1_9410170_202603.json",
                        "#{dir}/currents_v1_PUG1515_202603.json",
                        "#{dir}/tzs.json",
                        "#{dir}/lunar_phases_2026.json",
                    ]
                    delete = [
                        "#{dir}/tides_v1_9410170_202602.json",
                        "#{dir}/tides_v1_9410170_202601.json",
                        "#{dir}/currents_v1_PUG1515_202512.json",
                        "#{dir}/tides_v1_9410170_202601_imperial_1_0.ics",
                    ]

                    (keep + delete).each { |f| File.write(f, 'data') }

                    described_class.cleanup_old_cache_files

                    keep.each   { |f| expect(File.exist?(f)).to be(true),  "Expected #{File.basename(f)} to be kept" }
                    delete.each { |f| expect(File.exist?(f)).to be(false), "Expected #{File.basename(f)} to be deleted" }
                end
            end
        end
    end
end
