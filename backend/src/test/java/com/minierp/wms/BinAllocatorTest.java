package com.minierp.wms;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BinAllocatorTest {
    private static BinAllocator.Bin bin(long id, String item, String total) {
        return new BinAllocator.Bin(id, new BigDecimal(item), new BigDecimal(total));
    }

    @Test
    void putawayConsolidatesWithExistingStockOfSameItem() {
        assertThat(BinAllocator.putaway(List.of(bin(1, "0", "0"), bin(2, "30", "500"), bin(3, "80", "900")))).isEqualTo(3);
    }

    @Test
    void putawayUsesEmptiestLocationForNewItem() {
        assertThat(BinAllocator.putaway(List.of(bin(1, "0", "700"), bin(2, "0", "100"), bin(3, "0", "400")))).isEqualTo(2);
    }

    @Test
    void pickTakesSmallestBinsFirst() {
        var plan = BinAllocator.pick(List.of(bin(1, "100", "100"), bin(2, "20", "20"), bin(3, "50", "50")), new BigDecimal("60"));
        assertThat(plan).extracting(BinAllocator.Allocation::locationId).containsExactly(2L, 3L);
        assertThat(plan.get(1).qty()).isEqualByComparingTo("40");
    }

    @Test
    void pickFailsWhenInsufficient() {
        assertThatThrownBy(() -> BinAllocator.pick(List.of(bin(1, "5", "5")), new BigDecimal("6"))).hasMessageContaining("不足");
    }

    @Test
    void emptyZoneCannotPutaway() {
        assertThatThrownBy(() -> BinAllocator.putaway(List.of())).isInstanceOf(IllegalArgumentException.class);
    }
}
