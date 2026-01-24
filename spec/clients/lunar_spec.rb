# frozen_string_literal: true

RSpec.describe Clients::Lunar do
    let(:logger) { Logger.new('/dev/null') }
    let(:client) { described_class.new(logger) }

    describe '#phase' do
        context 'with known lunar phases' do
            it 'identifies new moon phase' do
                # Phase angle near 0 degrees
                allow(client).to receive(:phase_angle).and_return(10.0)
                expect(client.phase(Date.today)).to eq(:new)
            end

            it 'identifies waxing crescent phase' do
                allow(client).to receive(:phase_angle).and_return(45.0)
                expect(client.phase(Date.today)).to eq(:waxing_crescent)
            end

            it 'identifies first quarter phase' do
                allow(client).to receive(:phase_angle).and_return(90.0)
                expect(client.phase(Date.today)).to eq(:first_quarter)
            end

            it 'identifies waxing gibbous phase' do
                allow(client).to receive(:phase_angle).and_return(135.0)
                expect(client.phase(Date.today)).to eq(:waxing_gibbous)
            end

            it 'identifies full moon phase' do
                allow(client).to receive(:phase_angle).and_return(180.0)
                expect(client.phase(Date.today)).to eq(:full)
            end

            it 'identifies waning gibbous phase' do
                allow(client).to receive(:phase_angle).and_return(225.0)
                expect(client.phase(Date.today)).to eq(:waning_gibbous)
            end

            it 'identifies last quarter phase' do
                allow(client).to receive(:phase_angle).and_return(270.0)
                expect(client.phase(Date.today)).to eq(:last_quarter)
            end

            it 'identifies waning crescent phase' do
                allow(client).to receive(:phase_angle).and_return(315.0)
                expect(client.phase(Date.today)).to eq(:waning_crescent)
            end

            it 'identifies new moon at boundary (near 360)' do
                allow(client).to receive(:phase_angle).and_return(350.0)
                expect(client.phase(Date.today)).to eq(:new)
            end
        end
    end

    describe '#phase_angle' do
        it 'returns a value between 0 and 360' do
            angle = client.phase_angle(Date.today)
            expect(angle).to be >= 0
            expect(angle).to be < 360
        end

        it 'returns different angles for different dates' do
            angle1 = client.phase_angle(Date.new(2025, 6, 15))
            angle2 = client.phase_angle(Date.new(2025, 6, 22))

            expect(angle1).not_to eq(angle2)
        end

        it 'returns consistent results for the same date' do
            date = Date.new(2025, 1, 1)
            angle1 = client.phase_angle(date)
            angle2 = client.phase_angle(date)

            expect(angle1).to eq(angle2)
        end
    end

    describe '#percent_full' do
        it 'returns 0 percent for new moon' do
            allow(client).to receive(:phase_angle).and_return(0.0)
            expect(client.percent_full(Date.today)).to be_within(0.01).of(0.0)
        end

        it 'returns 50 percent for first quarter' do
            allow(client).to receive(:phase_angle).and_return(90.0)
            expect(client.percent_full(Date.today)).to be_within(0.01).of(0.5)
        end

        it 'returns 100 percent for full moon' do
            allow(client).to receive(:phase_angle).and_return(180.0)
            expect(client.percent_full(Date.today)).to be_within(0.01).of(1.0)
        end

        it 'returns 50 percent for last quarter' do
            allow(client).to receive(:phase_angle).and_return(270.0)
            expect(client.percent_full(Date.today)).to be_within(0.01).of(0.5)
        end
    end

    describe '#phases_for_year' do
        context 'with mocked API responses' do
            let(:usno_response) do
                {
                    'phasedata' => [
                        { 'year' => 2025, 'month' => 1, 'day' => 13, 'time' => '12:00', 'phase' => 'Full Moon' },
                        { 'year' => 2025, 'month' => 1, 'day' => 21, 'time' => '08:30', 'phase' => 'Last Quarter' },
                        { 'year' => 2025, 'month' => 1, 'day' => 29, 'time' => '04:15', 'phase' => 'New Moon' },
                        { 'year' => 2025, 'month' => 2, 'day' => 5, 'time' => '10:45', 'phase' => 'First Quarter' }
                    ]
                }.to_json
            end

            it 'fetches and processes lunar phases from moon-data API' do
                moondata_response = [
                    { 'Date' => '2025-01-13T12:00:00', 'Phase' => 2 },
                    { 'Date' => '2025-01-21T08:30:00', 'Phase' => 3 },
                    { 'Date' => '2025-01-29T04:15:00', 'Phase' => 0 },
                    { 'Date' => '2025-02-05T10:45:00', 'Phase' => 1 }
                ].to_json

                stub_request(:get, /craigchamberlain.github.io/)
                    .to_return(status: 200, body: moondata_response, headers: { 'Content-Type' => 'application/json' })

                phases = client.phases_for_year(2025)

                expect(phases).to be_an(Array)
                expect(phases.length).to eq(4)
                expect(phases.map { |p| p[:type] }).to include(:full_moon, :last_quarter, :new_moon, :first_quarter)
            end

            it 'falls back to USNO API when moon-data fails' do
                stub_request(:get, /craigchamberlain.github.io/)
                    .to_return(status: 502)

                stub_request(:get, /aa.usno.navy.mil/)
                    .to_return(status: 200, body: usno_response, headers: { 'Content-Type' => 'application/json' })

                phases = client.phases_for_year(2025)

                expect(phases).to be_an(Array)
                expect(phases.length).to eq(4)
            end

            it 'falls back to ephemeris when all APIs fail' do
                stub_request(:get, /craigchamberlain.github.io/).to_return(status: 502)
                stub_request(:get, /aa.usno.navy.mil/).to_return(status: 502)

                phases = client.phases_for_year(2025)

                expect(phases).to be_an(Array)
                expect(phases.length).to be_between(48, 52)
                expect(phases.first[:type]).to be_a(Symbol)
                expect(phases.first[:datetime]).to be_a(DateTime)
            end
        end
    end

    describe 'private helper methods' do
        describe '#normalize_angle' do
            it 'normalizes positive angles greater than 360' do
                expect(client.send(:normalize_angle, 370)).to eq(10)
                expect(client.send(:normalize_angle, 720)).to eq(0)
            end

            it 'normalizes negative angles' do
                expect(client.send(:normalize_angle, -10)).to eq(350)
                expect(client.send(:normalize_angle, -370)).to eq(350)
            end

            it 'leaves angles in range unchanged' do
                expect(client.send(:normalize_angle, 180)).to eq(180)
                expect(client.send(:normalize_angle, 0)).to eq(0)
                expect(client.send(:normalize_angle, 359)).to eq(359)
            end
        end

        describe '#degrees_to_radians' do
            it 'converts 0 degrees to 0 radians' do
                expect(client.send(:degrees_to_radians, 0)).to eq(0)
            end

            it 'converts 90 degrees to PI/2 radians' do
                expect(client.send(:degrees_to_radians, 90)).to be_within(0.0001).of(Math::PI / 2)
            end

            it 'converts 180 degrees to PI radians' do
                expect(client.send(:degrees_to_radians, 180)).to be_within(0.0001).of(Math::PI)
            end

            it 'converts 360 degrees to 2*PI radians' do
                expect(client.send(:degrees_to_radians, 360)).to be_within(0.0001).of(2 * Math::PI)
            end
        end
    end
end
