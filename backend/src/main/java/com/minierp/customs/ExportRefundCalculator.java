package com.minierp.customs;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 生产企业出口货物"免、抵、退"税（纯函数，财税〔2012〕39 号口径，未考虑免税购进原材料）：
 *   不得免征和抵扣税额 = 出口 FOB 人民币 × (征税率 − 退税率)（进项税额转出，计入主营业务成本）
 *   当期应纳税额 = 内销销项 − (进项 − 不得免征和抵扣税额) − 上期留抵
 *   免抵退税额 = 出口 FOB 人民币 × 退税率
 *   应纳税额 ≥ 0：应退税额 = 0，免抵税额 = 免抵退税额，期末留抵 = 0；
 *   应纳税额 < 0：期末留抵 = −应纳；应退税额 = min(期末留抵, 免抵退税额)，免抵税额 = 免抵退税额 − 应退税额，期末留抵 −= 应退税额。
 * 附加税（城建 7% + 教育费附加 3% + 地方教育附加 2%）以"应纳增值税 + 免抵税额"为计税依据。
 */
public final class ExportRefundCalculator {
    private ExportRefundCalculator() {
    }

    public static final BigDecimal SURTAX_RATE = new BigDecimal("0.12");

    public record Result(BigDecimal nonDeductible, BigDecimal payable, BigDecimal exemptRefund, BigDecimal refund, BigDecimal exemptOffset,
                         BigDecimal endCredit, BigDecimal surtax) {
    }

    public static Result compute(BigDecimal outputVat, BigDecimal inputVat, BigDecimal prevCredit, BigDecimal fobCny, BigDecimal levyRate, BigDecimal refundRate) {
        BigDecimal nonDeductible = r(fobCny.multiply(levyRate.subtract(refundRate)));
        BigDecimal payable = r(outputVat.subtract(inputVat.subtract(nonDeductible)).subtract(prevCredit));
        BigDecimal exemptRefund = r(fobCny.multiply(refundRate));
        BigDecimal refund, exemptOffset, endCredit;
        if (payable.signum() >= 0) {
            refund = BigDecimal.ZERO;
            exemptOffset = exemptRefund;
            endCredit = BigDecimal.ZERO;
        } else {
            BigDecimal credit = payable.negate();
            refund = credit.min(exemptRefund);
            exemptOffset = exemptRefund.subtract(refund);
            endCredit = credit.subtract(refund);
        }
        BigDecimal surtax = r(payable.max(BigDecimal.ZERO).add(exemptOffset).multiply(SURTAX_RATE));
        return new Result(nonDeductible, payable, exemptRefund, refund, exemptOffset, endCredit, surtax);
    }

    private static BigDecimal r(BigDecimal v) {
        return v.setScale(2, RoundingMode.HALF_UP);
    }
}
