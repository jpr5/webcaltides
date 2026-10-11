# Event matching (plan §4.4) and per-station metrics (plan §5.1). Pure Ruby, no engine.
module Score
    MATCH_S = 3 * 3600

    module_function

    # One-to-one greedy-by-distance matching of same-type events within ±3 h.
    # ref, model: arrays of { t:, ty:, h: (cm, raw) , hr: (cm, model raw vs model MSL; model only) }
    # Returns [pairs [[ri, mi], ...] sorted by ri, unmatched model indices]
    def match(ref, model)
        cands = []
        mt = model.each_with_index.sort_by { |m, _| m[:t] }
        ref.each_with_index do |r, ri|
            mt.each do |m, mi|
                d = (m[:t] - r[:t]).abs
                next if d >= MATCH_S || m[:ty] != r[:ty]
                cands << [d, ri, mi]
            end
        end
        cands.sort!
        used_r = {}; used_m = {}; pairs = []
        cands.each do |_, ri, mi|
            next if used_r[ri] || used_m[mi]
            used_r[ri] = used_m[mi] = true
            pairs << [ri, mi]
        end
        [pairs.sort, (0...model.size).reject { |mi| used_m[mi] }]
    end

    def mean(a) = a.empty? ? nil : a.sum / a.size.to_f
    def p95(a)
        return nil if a.empty?
        s = a.sort
        s[[(0.95 * s.size).ceil - 1, 0].max]
    end

    # ref: reference events inside [w0, w1). model: model events over the padded window.
    # Returns [station_metrics_hash, event_rows]
    def score(ref, model, w0, w1)
        pairs, unmatched = match(ref, model)
        extra = unmatched.count { |mi| model[mi][:t] >= w0 && model[mi][:t] < w1 }
        rows = pairs.map do |ri, mi|
            r = ref[ri]; m = model[mi]
            { ri: ri, type: r[:ty], t_ref: r[:t], t_model: m[:t], dt: (m[:t] - r[:t]) / 60.0,
              h_ref: r[:h], h_model: m[:hr], dh: m[:hr] - r[:h] }
        end
        hb = mean(rows.map { |x| x[:dh] }) || 0.0
        rows.each { |x| x[:dh_al] = x[:dh] - hb }
        dts = rows.map { |x| x[:dt] }; adt = dts.map(&:abs)
        hw = rows.select { |x| x[:type] == 'High' }; lw = rows.select { |x| x[:type] == 'Low' }
        ahal = rows.map { |x| x[:dh_al].abs }
        # range error per tidal cycle: consecutive matched reference events of opposite type
        byri = rows.to_h { |x| [x[:ri], x] }
        rng = (0...(ref.size - 1)).filter_map do |i|
            a = byri[i]; b = byri[i + 1]
            next unless a && b && a[:type] != b[:type]
            (a[:h_model] - b[:h_model]).abs - (a[:h_ref] - b[:h_ref]).abs
        end
        n = ref.size; missed = n - pairs.size
        m = {
            n_ref: n, n_model: model.count { |x| x[:t] >= w0 && x[:t] < w1 }, matched: pairs.size,
            missed: missed, extra: extra, me_per100: n.zero? ? nil : 100.0 * (missed + extra) / n,
            missed_per100: n.zero? ? nil : 100.0 * missed / n,
            t_bias: mean(dts), t_mae: mean(adt), t_p95: p95(adt), t_max: adt.max,
            t_bias_hw: mean(hw.map { |x| x[:dt] }), t_mae_hw: mean(hw.map { |x| x[:dt].abs }),
            t_bias_lw: mean(lw.map { |x| x[:dt] }), t_mae_lw: mean(lw.map { |x| x[:dt].abs }),
            h_bias_raw: rows.empty? ? nil : hb, h_mae_al: mean(ahal), h_p95_al: p95(ahal),
            range_bias: mean(rng), range_mae: mean(rng.map(&:abs)), n_range: rng.size,
        }
        [m, rows]
    end

    STATION_COLS = %i[n_ref n_model matched missed extra me_per100 missed_per100 t_bias t_mae t_p95 t_max
                      t_bias_hw t_mae_hw t_bias_lw t_mae_lw h_bias_raw h_mae_al h_p95_al range_bias range_mae n_range].freeze

    def fmt(v)
        case v
        when nil then ''
        when Float then format('%.3f', v)
        else v.to_s
        end
    end
end
