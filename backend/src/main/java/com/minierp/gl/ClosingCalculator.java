package com.minierp.gl;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 期末结转（纯函数）：
 *  1. 制造费用差异：5101 余额（实际 − 分配）结转主营业务成本
 *  2. 损益类科目本期净额全部结转本年利润 4103
 * 输入为各科目本期"借方 − 贷方"净额。
 */
public final class ClosingCalculator {
    private ClosingCalculator() {
    }

    /** 损益类科目：收入、资产处置收益、成本、税金及附加、销售 / 管理 / 财务 / 研发费用、营业外支出 */
    public static final List<String> PNL = List.of("6001", "6051", "6115", "6401", "6402", "6403", "6601", "6602", "6603", "6604", "6711");

    public record Result(List<VoucherValidator.Line> overheadLines, List<VoucherValidator.Line> profitLines, BigDecimal profit) {
    }

    public static Result close(Map<String, BigDecimal> netDr) {
        List<VoucherValidator.Line> overhead = new ArrayList<>();
        BigDecimal variance = netDr.getOrDefault("5101", BigDecimal.ZERO);
        BigDecimal cogs = netDr.getOrDefault("6401", BigDecimal.ZERO);
        if (variance.signum() > 0) {         // 实际大于分配：少分配，补计成本
            overhead.add(VoucherValidator.Line.dr("6401", variance, "制造费用少分配结转"));
            overhead.add(VoucherValidator.Line.cr("5101", variance, "制造费用少分配结转"));
        } else if (variance.signum() < 0) {  // 多分配：冲减成本
            overhead.add(VoucherValidator.Line.dr("5101", variance.negate(), "制造费用多分配结转"));
            overhead.add(VoucherValidator.Line.cr("6401", variance.negate(), "制造费用多分配结转"));
        }
        cogs = cogs.add(variance);

        List<VoucherValidator.Line> profit = new ArrayList<>();
        BigDecimal total = BigDecimal.ZERO; // 利润 = −Σ(借−贷)
        for (String acc : PNL) {
            BigDecimal net = "6401".equals(acc) ? cogs : netDr.getOrDefault(acc, BigDecimal.ZERO);
            if (net.signum() == 0) {
                continue;
            }
            total = total.subtract(net);
            if (net.signum() < 0) {      // 贷方余额（收入）：借记该科目
                profit.add(VoucherValidator.Line.dr(acc, net.negate(), "结转损益"));
            } else {                     // 借方余额（成本费用）：贷记该科目
                profit.add(VoucherValidator.Line.cr(acc, net, "结转损益"));
            }
        }
        if (total.signum() > 0) {
            profit.add(VoucherValidator.Line.cr("4103", total, "本期利润"));
        } else if (total.signum() < 0) {
            profit.add(VoucherValidator.Line.dr("4103", total.negate(), "本期亏损"));
        }
        return new Result(overhead, profit, total);
    }
}
