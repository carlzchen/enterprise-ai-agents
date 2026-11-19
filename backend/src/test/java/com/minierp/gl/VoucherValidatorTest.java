package com.minierp.gl;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Set;

import static com.minierp.gl.VoucherValidator.Line.cr;
import static com.minierp.gl.VoucherValidator.Line.dr;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class VoucherValidatorTest {
    private static final Set<String> ACCOUNTS = Set.of("1122", "6001", "222102");

    @Test
    void balancedSalesVoucherPasses() {
        assertThatCode(() -> VoucherValidator.validate(List.of(
                dr("1122", new BigDecimal("113.00"), null), cr("6001", new BigDecimal("100.00"), null),
                cr("222102", new BigDecimal("13.00"), null)), ACCOUNTS)).doesNotThrowAnyException();
    }

    @Test
    void unbalancedVoucherIsRejected() {
        assertThatThrownBy(() -> VoucherValidator.validate(List.of(
                dr("1122", new BigDecimal("113.00"), null), cr("6001", new BigDecimal("100.00"), null)), ACCOUNTS))
                .hasMessageContaining("借贷不平衡");
    }

    @Test
    void unknownAccountNegativeAmountAndDoubleSidedLineAreRejected() {
        assertThatThrownBy(() -> VoucherValidator.validate(List.of(dr("9999", BigDecimal.ONE, null), cr("6001", BigDecimal.ONE, null)), ACCOUNTS))
                .hasMessageContaining("科目不存在");
        assertThatThrownBy(() -> VoucherValidator.validate(List.of(dr("1122", BigDecimal.ONE.negate(), null), cr("6001", BigDecimal.ONE.negate(), null)), ACCOUNTS))
                .hasMessageContaining("负");
        assertThatThrownBy(() -> VoucherValidator.validate(List.of(
                new VoucherValidator.Line("1122", BigDecimal.ONE, BigDecimal.ONE, null), cr("6001", BigDecimal.ZERO, null)), ACCOUNTS))
                .hasMessageContaining("同时");
    }

    @Test
    void singleLineOrZeroVoucherIsRejected() {
        assertThatThrownBy(() -> VoucherValidator.validate(List.of(dr("1122", BigDecimal.ONE, null)), ACCOUNTS)).hasMessageContaining("两条");
        assertThatThrownBy(() -> VoucherValidator.validate(List.of(dr("1122", BigDecimal.ZERO, null), cr("6001", BigDecimal.ZERO, null)), ACCOUNTS))
                .hasMessageContaining("不能为 0");
    }

    @Test
    void expenseAccountFollowsDepartment() {
        org.assertj.core.api.Assertions.assertThat(GlService.expenseAccount("销售部")).isEqualTo(GlService.SELLING);
        org.assertj.core.api.Assertions.assertThat(GlService.expenseAccount("研发中心")).isEqualTo(GlService.RND);
        org.assertj.core.api.Assertions.assertThat(GlService.expenseAccount("行政部")).isEqualTo(GlService.ADMIN);
    }
}
