package com.minierp.query;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ViewQueryServiceTest {
    private JdbcTemplate jdbc;
    private ViewQueryService service;

    @BeforeEach
    void setUp() {
        jdbc = mock(JdbcTemplate.class);
        service = new ViewQueryService(jdbc);
        when(jdbc.queryForObject(anyString(), eq(Long.class), any(Object[].class))).thenReturn(1L);
        Map<String, Object> row = new HashMap<>(Map.of("id", 1, "delivery_no", "DN1", "cost_cny", 100));
        when(jdbc.queryForList(anyString(), any(Object[].class))).thenReturn(new ArrayList<>(List.of(row)));
    }

    private String lastPageSql() {
        ArgumentCaptor<String> sql = ArgumentCaptor.forClass(String.class);
        verify(jdbc).queryForList(sql.capture(), any(Object[].class));
        return sql.getValue();
    }

    @Test
    void sortInjectionFallsBackToDefaultColumn() {
        service.page("deliveries", "ADMIN", Map.of("sort", "id; DROP TABLE md_product", "order", "asc"));
        assertThat(lastPageSql()).doesNotContain("DROP").contains("ORDER BY delivery_date ASC");
    }

    @Test
    void unknownFilterIsIgnoredAndValuesAreParameterized() {
        service.page("deliveries", "ADMIN", Map.of("status", "SHIPPED' OR '1'='1", "evil_col", "x"));
        String sql = lastPageSql();
        assertThat(sql).contains("status = ?").doesNotContain("evil_col").doesNotContain("'1'='1");
    }

    @Test
    void sensitiveColumnsAreRemovedForSales() {
        var result = service.page("deliveries", "SALES", Map.of());
        assertThat(result.records().get(0)).doesNotContainKey("cost_cny").containsKey("delivery_no");
    }

    @Test
    void financeCanSeeCost() {
        var result = service.page("deliveries", "FINANCE", Map.of());
        assertThat(result.records().get(0)).containsKey("cost_cny");
    }

    @Test
    void restrictedViewDeniedForOtherRoles() {
        assertThatThrownBy(() -> service.page("vouchers", "SALES", Map.of())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.page("no-such-view", "ADMIN", Map.of())).hasMessageContaining("未知查询");
    }

    @Test
    void pageSizeIsCapped() {
        service.page("deliveries", "ADMIN", Map.of("size", "100000"));
        ArgumentCaptor<Object[]> args = ArgumentCaptor.forClass(Object[].class);
        verify(jdbc).queryForList(anyString(), args.capture());
        Object[] a = args.getValue();
        assertThat(a[a.length - 2]).isEqualTo(500);
    }
}
