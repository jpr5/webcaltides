# frozen_string_literal: true

# The clean-room module's own oracle test (from its build; see
# docs/nodal-cleanroom/PROVENANCE.md), run here against the copied module and
# the committed oracle. spec/unit/harmonics/nodal_oracle_spec.rb checks the same
# oracle through the engine.
RSpec.describe NodalSchureman do
    oracle = JSON.parse(File.read(File.expand_path('../fixtures/harmonics/nodal_oracle.json', __dir__)))
    tol = 1e-9

    # Circular difference in degrees: ((a - b + 180) mod 360) - 180.
    def circ_diff(a, b)
        ((a - b + 180.0) % 360.0) - 180.0
    end

    it 'covers exactly the 13 specified constituents' do
        expect(described_class::CONSTITUENTS.sort).to eq(oracle.fetch('constituents').sort)
    end

    it 'has 780 oracle points' do
        expect(oracle.fetch('cases').size).to eq(780)
    end

    it 'returns no entry for any other constituent name' do
        r = described_class.compute(2026, month: 7, day: 2)
        %w[Mf Mm J1 M3 M4 MS4 MN4 2N2 MU2 SA SSA k1 m2 OO1].each do |name|
            expect(r).not_to have_key(name)
            expect(described_class.factor(name, 2026, month: 7, day: 2)).to be_nil
        end
        expect(r.keys.sort).to eq(described_class::CONSTITUENTS.sort)
    end

    it 'matches all 780 oracle points to 1e-9' do
        bad = oracle.fetch('cases').reject do |c|
            got = described_class.factor(c['constituent'], c['year'], month: c['month'], day: c['day'],
                                         hour: c['hour'], shift_hours: c['shift_hours'])
            got &&
                (got[:f] - c['f']).abs <= tol &&
                circ_diff(got[:v0_plus_u], c['V0_plus_u_mod360']).abs <= tol &&
                circ_diff(got[:u], c['u_mod_pm180']).abs <= tol
        end
        expect(bad.map { |c| c.values_at('constituent', 'year', 'month', 'day', 'hour', 'shift_hours') }).to eq([])
    end
end
