package com.minierp.workflow;

import java.util.*;

/**
 * 审批路线解析（纯函数）：发起时按流程定义 + 表单变量 + 组织关系，算出每个节点的审批人。
 * 规则：
 *  1. 条件不满足的节点不进入；
 *  2. 审批人是申请人本人时自动跳过（职责分离：不能审批自己的单据）；
 *  3. 审批人去重：同一人在前序节点已出现，后续节点不再重复审批；
 *  4. 去掉以上审批人后节点为空，则该节点自动通过（保留在路线里并注明原因，便于审计）。
 */
public final class WorkflowRouter {
    private WorkflowRouter() {
    }

    /**
     * @param type  MANAGER（第 level 级上级）/ DEPT_HEAD（申请人部门负责人）/ DEPT_OF（指定部门 dept 的负责人）/ ROLE（角色 role 的全部人员）/ USER（指定账号 user）
     * @param mode  OR 或签（一人同意即可）/ AND 会签（全部同意）
     */
    public record NodeDef(String name, String type, Integer level, String dept, String role, String user, String mode, String condition) {
    }

    public record RouteNode(int index, String name, String mode, List<Long> approvers, String note) {
        public boolean autoPass() {
            return approvers.isEmpty();
        }
    }

    public interface ApproverResolver {
        List<Long> manager(long applicant, int level);

        List<Long> deptHead(long applicant);

        List<Long> headOf(String deptCode);

        List<Long> role(String role);

        List<Long> user(String username);
    }

    public static List<RouteNode> route(List<NodeDef> nodes, Map<String, ?> vars, long applicant, ApproverResolver r) {
        List<RouteNode> out = new ArrayList<>();
        Set<Long> already = new HashSet<>();
        for (NodeDef n : nodes) {
            if (!ConditionEvaluator.eval(n.condition(), vars)) {
                continue;
            }
            List<Long> raw = switch (n.type()) {
                case "MANAGER" -> r.manager(applicant, n.level() == null ? 1 : n.level());
                case "DEPT_HEAD" -> r.deptHead(applicant);
                case "DEPT_OF" -> r.headOf(n.dept());
                case "ROLE" -> r.role(n.role());
                case "USER" -> r.user(n.user());
                default -> throw new IllegalArgumentException("未知审批人类型：" + n.type());
            };
            List<Long> approvers = new ArrayList<>();
            String note = null;
            boolean self = false, dup = false;
            for (Long a : new LinkedHashSet<>(raw)) {
                if (a == null) {
                    continue;
                }
                if (a == applicant) {
                    self = true;
                } else if (already.contains(a)) {
                    dup = true;
                } else {
                    approvers.add(a);
                }
            }
            if (approvers.isEmpty()) {
                note = raw.isEmpty() ? "未找到审批人，自动通过" : self && !dup ? "审批人为申请人本人，自动跳过" : "审批人已在前序节点审批，自动通过";
            }
            already.addAll(approvers);
            out.add(new RouteNode(out.size(), n.name(), n.mode() == null ? "OR" : n.mode(), approvers, note));
        }
        return out;
    }
}
