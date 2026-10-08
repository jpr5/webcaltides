# Plan §6.2 per-station classes for a paired (A = baseline, B = candidate) station-window.
# Inputs are CSV::Row-like hashes with t_mae, t_p95, h_mae_al, missed_per100.
module Classify
    NOISE_T = 1.0  # min
    NOISE_H = 1.0  # cm

    module_function

    def f(row, k)
        v = row[k]
        v.nil? || v == '' ? nil : v.to_f
    end

    # Returns { cls:, improved_t:, improved_h:, meaningful:, reasons: [] }
    # cls: 'real' | 'minor' | 'meaningless' | 'improved' | 'no_change'
    # Worsening for the meaningless/minor split is judged on time MAE and height MAE (the two metrics
    # §6.2 gives meaningless bounds for); P95 |dt| and missed events only feed the real criteria.
    def classify(a, b)
        ta, tb = f(a, 't_mae'), f(b, 't_mae')
        ha, hb = f(a, 'h_mae_al'), f(b, 'h_mae_al')
        pa, pb = f(a, 't_p95'), f(b, 't_p95')
        ma, mb = f(a, 'missed_per100') || 0.0, f(b, 'missed_per100') || 0.0
        dt = ta && tb ? tb - ta : nil
        dh = ha && hb ? hb - ha : nil
        reasons = []
        reasons << format('t_mae +%.2f (%.0f%%)', dt, 100 * dt / ta) if dt && dt >= 2.0 && ta.positive? && dt >= 0.2 * ta
        reasons << format('t_p95 +%.1f', pb - pa) if pa && pb && pb - pa >= 10.0
        reasons << format('h_mae +%.2f (%.0f%%)', dh, 100 * dh / ha) if dh && dh >= 3.0 && ha.positive? && dh >= 0.3 * ha
        reasons << format('missed/100 +%.2f', mb - ma) if mb - ma > 2.0
        improved_t = dt && dt <= -NOISE_T
        improved_h = dh && dh <= -NOISE_H
        meaningful = (dt && ta.positive? && dt <= -2.0 && -dt >= 0.2 * ta) || (dh && ha.positive? && dh <= -2.0 && -dh >= 0.2 * ha)
        cls =
            if !reasons.empty? then 'real'
            elsif (dt && dt.positive?) || (dh && dh.positive?)
                t_ok = !(dt && dt.positive?) || dt < 1.0 || (ta >= 10.0 && dt < 0.1 * ta)
                h_ok = !(dh && dh.positive?) || dh < 1.0
                if t_ok && h_ok
                    improved_t ? 'improved' : 'meaningless'
                else
                    'minor'
                end
            elsif improved_t then 'improved'
            else 'no_change'
            end
        { cls: cls, improved_t: improved_t ? true : false, improved_h: improved_h ? true : false,
          meaningful: meaningful ? true : false, dt: dt, dh: dh, reasons: reasons }
    end

    # Two-sided sign test p-value for n_pos vs n_neg (ties dropped).
    def sign_test(n_pos, n_neg)
        n = n_pos + n_neg
        return nil if n.zero?
        k = [n_pos, n_neg].min
        lg = ->(x) { Math.lgamma(x + 1).first }
        cdf = (0..k).sum { |i| Math.exp(lg[n] - lg[i] - lg[n - i] - n * Math.log(2)) }
        [2 * cdf, 1.0].min
    end
end
