package com.minierp.gl;

import java.math.BigDecimal;
import java.util.List;
import java.util.Set;

/** 凭证校验（纯函数，便于单元测试）：至少两行、单行借贷只能一方且非负、借贷合计相等且大于 0、科目必须存在 */
public final class VoucherValidator {
    private VoucherValidator() {
    }

    public record Line(String account, BigDecimal debit, BigDecimal credit, String summary) {
        public static Line dr(String account, BigDecimal amount, String summary) {
            return new Line(account, amount, BigDecimal.ZERO, summary);
        }

        public static Line cr(String account, BigDecimal amount, String summary) {
            return new Line(account, BigDecimal.ZERO, amount, summary);
        }

        /** 正数记借方、负数记贷方（用于方向随金额变化的差异类分录） */
        public static Line signed(String account, BigDecimal amount, String summary) {
            return amount.signum() >= 0 ? dr(account, amount, summary) : cr(account, amount.negate(), summary);
        }
    }

    public static void validate(List<Line> lines, Set<String> accounts) {
        if (lines == null || lines.size() < 2) {
            throw new IllegalArgumentException("凭证至少需要两条分录");
        }
        BigDecimal dr = BigDecimal.ZERO, cr = BigDecimal.ZERO;
        for (Line l : lines) {
            if (!accounts.contains(l.account())) {
                throw new IllegalArgumentException("科目不存在：" + l.account());
            }
            if (l.debit().signum() < 0 || l.credit().signum() < 0) {
                throw new IllegalArgumentException("借贷金额不能为负：" + l.account());
            }
            if (l.debit().signum() > 0 && l.credit().signum() > 0) {
                throw new IllegalArgumentException("同一分录不能同时有借方和贷方：" + l.account());
            }
            dr = dr.add(l.debit());
            cr = cr.add(l.credit());
        }
        if (dr.signum() == 0) {
            throw new IllegalArgumentException("凭证金额不能为 0");
        }
        if (dr.compareTo(cr) != 0) {
            throw new IllegalArgumentException("借贷不平衡：借方 " + dr + "，贷方 " + cr);
        }
    }
}
