package com.minierp.hr;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 单人月度工资计算（纯函数）。
 * 月薪：基本工资按在职天数折算，扣事假 / 旷工（日薪 = 月薪 / 21.75）；加班按小时工资 1.5 倍。
 * 计件：计件工资 = 分摊到个人的良品数 × 单价；不足保底工资（按出勤折算）的部分补足；
 *       加班产量已计件，另付 0.5 倍加班溢价（按保底工资折算小时工资）。
 * 社保公积金按缴费基数（上下限内）计算；个税用累计预扣法。
 */
public final class PayrollCalculator {
    private PayrollCalculator() {
    }

    public static final BigDecimal MONTH_DAYS = new BigDecimal("21.75");
    public static final BigDecimal SOCIAL_BASE_MIN = new BigDecimal("2360"), SOCIAL_BASE_MAX = new BigDecimal("27501");
    /** 个人：养老 8% + 医疗 2% + 失业 0.3% + 公积金 5% */
    public static final BigDecimal EMPLOYEE_RATE = new BigDecimal("0.153");
    /** 单位：养老 15% + 医疗 5% + 失业 0.7% + 工伤 0.4% + 公积金 5% */
    public static final BigDecimal EMPLOYER_RATE = new BigDecimal("0.261");

    /**
     * @param monthWorkdays  当月工作日数
     * @param scheduledDays  在职期间的工作日数（月中入职 / 离职时小于 monthWorkdays）
     * @param attendDays     实际出勤天数（含迟到）
     * @param paidLeaveDays  带薪假（年假、病假）
     * @param unpaidDays     事假 + 旷工
     */
    public record Input(String wageType, BigDecimal baseSalary, BigDecimal socialBase, int monthWorkdays, int scheduledDays,
                        BigDecimal attendDays, BigDecimal paidLeaveDays, BigDecimal unpaidDays, BigDecimal overtimeHours,
                        BigDecimal piecePay, BigDecimal prevYtdTaxable, BigDecimal prevYtdTax) {
    }

    public record Result(BigDecimal basePay, BigDecimal piecePay, BigDecimal topup, BigDecimal overtimePay, BigDecimal deduction,
                         BigDecimal gross, BigDecimal socialEmployee, BigDecimal socialEmployer, BigDecimal ytdTaxable,
                         BigDecimal tax, BigDecimal ytdTax, BigDecimal net) {
    }

    public static Result compute(Input in) {
        BigDecimal daily = in.baseSalary().divide(MONTH_DAYS, 6, RoundingMode.HALF_UP);
        BigDecimal hourly = daily.divide(BigDecimal.valueOf(8), 6, RoundingMode.HALF_UP);
        BigDecimal basePay = BigDecimal.ZERO, piece = BigDecimal.ZERO, topup = BigDecimal.ZERO, deduction = BigDecimal.ZERO, ot;
        if ("PIECE".equals(in.wageType())) {
            piece = in.piecePay();
            BigDecimal guarantee = in.baseSalary().multiply(in.attendDays().add(in.paidLeaveDays()))
                    .divide(BigDecimal.valueOf(in.monthWorkdays()), 6, RoundingMode.HALF_UP);
            topup = guarantee.subtract(piece).max(BigDecimal.ZERO);
            ot = hourly.multiply(new BigDecimal("0.5")).multiply(in.overtimeHours());
        } else {
            basePay = in.baseSalary().multiply(BigDecimal.valueOf(in.scheduledDays()))
                    .divide(BigDecimal.valueOf(in.monthWorkdays()), 6, RoundingMode.HALF_UP);
            deduction = daily.multiply(in.unpaidDays()).min(basePay);
            ot = hourly.multiply(new BigDecimal("1.5")).multiply(in.overtimeHours());
        }
        basePay = r(basePay);
        piece = r(piece);
        topup = r(topup);
        ot = r(ot);
        deduction = r(deduction);
        BigDecimal gross = basePay.add(piece).add(topup).add(ot).subtract(deduction);
        BigDecimal base = in.socialBase().max(SOCIAL_BASE_MIN).min(SOCIAL_BASE_MAX);
        BigDecimal socialEmp = in.scheduledDays() > 0 ? r(base.multiply(EMPLOYEE_RATE)) : BigDecimal.ZERO;
        BigDecimal socialEr = in.scheduledDays() > 0 ? r(base.multiply(EMPLOYER_RATE)) : BigDecimal.ZERO;
        BigDecimal ytdTaxable = in.prevYtdTaxable().add(gross).subtract(socialEmp).subtract(IncomeTaxCalculator.MONTHLY_DEDUCTION);
        BigDecimal tax = IncomeTaxCalculator.monthlyWithholding(ytdTaxable, in.prevYtdTax());
        BigDecimal net = gross.subtract(socialEmp).subtract(tax);
        return new Result(basePay, piece, topup, ot, deduction, gross, socialEmp, socialEr, ytdTaxable, tax, in.prevYtdTax().add(tax), net);
    }

    private static BigDecimal r(BigDecimal v) {
        return v.setScale(2, RoundingMode.HALF_UP);
    }
}
