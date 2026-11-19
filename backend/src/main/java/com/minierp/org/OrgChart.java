package com.minierp.org;

import java.util.*;

/**
 * 组织汇报关系（纯函数）。
 * 直属上级：员工档案指定了上级就用它；否则取所在部门负责人；本人就是部门负责人时，向上找最近一个负责人不是本人的上级部门。
 * 部门负责人：同上，本人是负责人时取上级部门负责人（"部门负责人审批"不会落到申请人自己身上）。
 */
public final class OrgChart {
    private OrgChart() {
    }

    public record Dept(long id, Long parentId, Long managerEmpId) {
    }

    public record Emp(long id, long deptId, Long managerId) {
    }

    public static Long directManager(long empId, Map<Long, Emp> emps, Map<Long, Dept> depts) {
        Emp e = emps.get(empId);
        if (e == null) {
            return null;
        }
        if (e.managerId() != null && e.managerId() != empId) {
            return e.managerId();
        }
        return deptHeadFrom(e.deptId(), empId, depts);
    }

    public static Long deptHead(long empId, Map<Long, Emp> emps, Map<Long, Dept> depts) {
        Emp e = emps.get(empId);
        return e == null ? null : deptHeadFrom(e.deptId(), empId, depts);
    }

    /** 从某部门开始向上找第一个负责人不是 empId 的部门负责人 */
    static Long deptHeadFrom(long deptId, long empId, Map<Long, Dept> depts) {
        Set<Long> seen = new HashSet<>();
        Long cur = deptId;
        while (cur != null && seen.add(cur)) {
            Dept d = depts.get(cur);
            if (d == null) {
                return null;
            }
            if (d.managerEmpId() != null && d.managerEmpId() != empId) {
                return d.managerEmpId();
            }
            cur = d.parentId();
        }
        return null;
    }

    /** 汇报链：直属上级、上级的上级……（去环，最多 10 级） */
    public static List<Long> managerChain(long empId, Map<Long, Emp> emps, Map<Long, Dept> depts) {
        List<Long> chain = new ArrayList<>();
        Set<Long> seen = new HashSet<>(List.of(empId));
        Long cur = empId;
        for (int i = 0; i < 10; i++) {
            Long m = directManager(cur, emps, depts);
            if (m == null || !seen.add(m)) {
                break;
            }
            chain.add(m);
            cur = m;
        }
        return chain;
    }

    /** 部门及全部下级部门 id（部门树查询、数据权限） */
    public static Set<Long> subtree(long deptId, Collection<Dept> depts) {
        Map<Long, List<Long>> children = new HashMap<>();
        depts.forEach(d -> {
            if (d.parentId() != null) {
                children.computeIfAbsent(d.parentId(), k -> new ArrayList<>()).add(d.id());
            }
        });
        Set<Long> out = new LinkedHashSet<>();
        Deque<Long> stack = new ArrayDeque<>(List.of(deptId));
        while (!stack.isEmpty()) {
            Long d = stack.pop();
            if (out.add(d)) {
                children.getOrDefault(d, List.of()).forEach(stack::push);
            }
        }
        return out;
    }
}
