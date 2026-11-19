package com.minierp.aps;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class KittingCalculatorTest {
    private static final LocalDate D = LocalDate.of(2026, 9, 28);

    @Test
    void earlierDueOrderTakesStockFirstLaterOrderWaitsForPo() {
        var kits = KittingCalculator.check(List.of(
                new KittingCalculator.Demand(2, D.plusDays(10), Map.of(7L, new BigDecimal("60"))),
                new KittingCalculator.Demand(1, D.plusDays(2), Map.of(7L, new BigDecimal("80")))),
                Map.of(7L, new BigDecimal("100")),
                List.of(new KittingCalculator.Supply(7L, D.plusDays(5), new BigDecimal("50"))), D, 15);
        assertThat(kits.get(1L).status()).isEqualTo("READY");
        assertThat(kits.get(2L).status()).isEqualTo("WAIT_PO");
        assertThat(kits.get(2L).earliestStart()).isEqualTo(D.plusDays(5));
    }

    @Test
    void noStockAndNoPoIsShortage() {
        var kits = KittingCalculator.check(List.of(new KittingCalculator.Demand(1, D, Map.of(9L, BigDecimal.TEN))),
                Map.of(), List.of(), D, 15);
        assertThat(kits.get(1L).status()).isEqualTo("SHORTAGE");
        assertThat(kits.get(1L).shortMaterials()).containsExactly(9L);
        assertThat(kits.get(1L).earliestStart()).isEqualTo(D.plusDays(15));
    }

    @Test
    void substituteOnHandUsedBeforeWaitingForPo() {
        var pool = new KittingCalculator.Pool(Map.of(7L, new BigDecimal("30"), 8L, new BigDecimal("100")),
                List.of(new KittingCalculator.Supply(7L, D.plusDays(6), new BigDecimal("500"))),
                Map.of(7L, List.of(new KittingCalculator.Sub(8L, BigDecimal.ONE))));
        var kits = pool.check(List.of(new KittingCalculator.Demand(1, D.plusDays(3), Map.of(7L, new BigDecimal("80")))), D, 15);
        assertThat(kits.get(1L).status()).isEqualTo("READY");
        assertThat(kits.get(1L).substitutions()).containsEntry(8L, new BigDecimal("50.000"));
    }

    @Test
    void parentWaitsForSubAssemblyPlannedCompletion() {
        // 第 1 层组件工单排完产后，把完工日作为上层的在途供应
        var pool = new KittingCalculator.Pool(Map.of(20L, new BigDecimal("10")), List.of(), Map.of());
        pool.addSupply(new KittingCalculator.Supply(20L, D.plusDays(4), new BigDecimal("200")));
        var kits = pool.check(List.of(new KittingCalculator.Demand(5, D.plusDays(8), Map.of(20L, new BigDecimal("150")))), D, 15);
        assertThat(kits.get(5L).status()).isEqualTo("WAIT_PO");
        assertThat(kits.get(5L).earliestStart()).isEqualTo(D.plusDays(4));
    }
}
