package com.minierp.org;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class OrgChartTest {
    // 总经办(1, 负责人 100) → 生产部(2, 负责人 200) → 智能驱控车间(3, 负责人 300)；工人 400 的直属上级为班组长 350
    private static final Map<Long, OrgChart.Dept> DEPTS = Map.of(
            1L, new OrgChart.Dept(1, null, 100L), 2L, new OrgChart.Dept(2, 1L, 200L), 3L, new OrgChart.Dept(3, 2L, 300L),
            4L, new OrgChart.Dept(4, 2L, null));
    private static final Map<Long, OrgChart.Emp> EMPS = Map.of(
            100L, new OrgChart.Emp(100, 1, null), 200L, new OrgChart.Emp(200, 2, null), 300L, new OrgChart.Emp(300, 3, null),
            350L, new OrgChart.Emp(350, 3, null), 400L, new OrgChart.Emp(400, 3, 350L), 500L, new OrgChart.Emp(500, 4, null));

    @Test
    void explicitManagerThenDeptHead() {
        assertThat(OrgChart.directManager(400, EMPS, DEPTS)).isEqualTo(350L);
        assertThat(OrgChart.directManager(350, EMPS, DEPTS)).isEqualTo(300L);
    }

    @Test
    void headReportsToParentDeptHeadAndEmptyDeptWalksUp() {
        assertThat(OrgChart.directManager(300, EMPS, DEPTS)).isEqualTo(200L);
        assertThat(OrgChart.deptHead(300, EMPS, DEPTS)).isEqualTo(200L); // 本人是负责人 → 上级部门负责人
        assertThat(OrgChart.directManager(500, EMPS, DEPTS)).isEqualTo(200L); // 部门没设负责人
        assertThat(OrgChart.directManager(100, EMPS, DEPTS)).isNull(); // 总经理没有上级
    }

    @Test
    void managerChainStopsAtTop() {
        assertThat(OrgChart.managerChain(400, EMPS, DEPTS)).containsExactly(350L, 300L, 200L, 100L);
    }

    @Test
    void subtreeIncludesDescendants() {
        assertThat(OrgChart.subtree(2, DEPTS.values())).containsExactlyInAnyOrder(2L, 3L, 4L);
    }
}
