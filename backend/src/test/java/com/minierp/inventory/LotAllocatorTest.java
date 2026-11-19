package com.minierp.inventory;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class LotAllocatorTest {
    @Test
    void consumesOldestLotsFirst() {
        var takes = LotAllocator.fifo(List.of(new LotAllocator.Lot(1, new BigDecimal("30")), new LotAllocator.Lot(2, new BigDecimal("50"))),
                new BigDecimal("40"));
        assertThat(takes).containsExactly(new LotAllocator.Take(1, new BigDecimal("30")), new LotAllocator.Take(2, new BigDecimal("10")));
    }

    @Test
    void skipsEmptyLotsAndFlagsUncoveredQuantity() {
        var takes = LotAllocator.fifo(List.of(new LotAllocator.Lot(1, BigDecimal.ZERO), new LotAllocator.Lot(2, new BigDecimal("5"))),
                new BigDecimal("8"));
        assertThat(takes).extracting(LotAllocator.Take::lotId).containsExactly(2L, -1L);
        assertThat(takes.get(1).qty()).isEqualByComparingTo("3");
    }
}
