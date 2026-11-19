package com.minierp.org;

import com.minierp.common.BizException;
import com.minierp.common.NotFoundException;
import com.minierp.workflow.WorkflowRouter;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.util.*;

/**
 * 组织与统一身份：部门树、员工档案、入转调离，以及账号与员工的绑定。
 * 员工离职生效时同步停用其系统账号（JwtAuthFilter 会拒绝已停用账号的旧 token）。
 * 同时为流程引擎提供审批人解析（直属上级、部门负责人、指定部门负责人、角色、指定账号）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class OrgService {
    private final JdbcTemplate jdbc;
    private final PasswordEncoder encoder;
    private final UserStatusCache userStatus;

    public record HireReq(String name, String gender, LocalDate birthDate, Long deptId, Long positionId, Long managerId,
                          String lineCode, String team, String employmentType, String wageType, BigDecimal baseSalary,
                          BigDecimal socialBase, LocalDate hireDate) {
    }

    // ---------- 组织关系 ----------

    public record Chart(Map<Long, OrgChart.Emp> emps, Map<Long, OrgChart.Dept> depts) {
    }

    public Chart chart() {
        Map<Long, OrgChart.Dept> depts = new HashMap<>();
        jdbc.query("SELECT id, parent_id, manager_emp_id FROM org_dept WHERE status = 'ACTIVE'", rs -> {
            depts.put(rs.getLong(1), new OrgChart.Dept(rs.getLong(1), (Long) rs.getObject(2), (Long) rs.getObject(3)));
        });
        Map<Long, OrgChart.Emp> emps = new HashMap<>();
        jdbc.query("SELECT id, dept_id, manager_id FROM hr_employee WHERE status <> 'LEFT'", rs -> {
            emps.put(rs.getLong(1), new OrgChart.Emp(rs.getLong(1), rs.getLong(2), (Long) rs.getObject(3)));
        });
        return new Chart(emps, depts);
    }

    /** 流程引擎的审批人解析器（一次发起内复用同一份组织快照） */
    public WorkflowRouter.ApproverResolver resolver() {
        Chart c = chart();
        return new WorkflowRouter.ApproverResolver() {
            public List<Long> manager(long applicant, int level) {
                List<Long> chain = OrgChart.managerChain(applicant, c.emps(), c.depts());
                return chain.size() >= level ? List.of(chain.get(level - 1)) : List.of();
            }

            public List<Long> deptHead(long applicant) {
                Long h = OrgChart.deptHead(applicant, c.emps(), c.depts());
                return h == null ? List.of() : List.of(h);
            }

            public List<Long> headOf(String deptCode) {
                return jdbc.queryForList("SELECT manager_emp_id FROM org_dept WHERE code = ? AND manager_emp_id IS NOT NULL", Long.class, deptCode);
            }

            public List<Long> role(String role) {
                return jdbc.queryForList("""
                        SELECT e.id FROM hr_employee e JOIN sys_user u ON u.id = e.user_id
                        WHERE u.role = ? AND u.enabled = 1 AND e.status <> 'LEFT' ORDER BY e.id""", Long.class, role);
            }

            public List<Long> user(String username) {
                return jdbc.queryForList("SELECT u.employee_id FROM sys_user u WHERE u.username = ? AND u.enabled = 1 AND u.employee_id IS NOT NULL",
                        Long.class, username);
            }
        };
    }

    public Long employeeOfUser(String username) {
        return jdbc.queryForList("SELECT employee_id FROM sys_user WHERE username = ?", Long.class, username)
                .stream().filter(Objects::nonNull).findFirst()
                .orElseThrow(() -> new BizException("账号 " + username + " 未关联员工档案，请联系人事"));
    }

    public Map<String, Object> employee(long id) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM v_employee WHERE id = ?", id);
        if (r.isEmpty()) {
            throw new NotFoundException("员工", id);
        }
        Map<String, Object> e = new LinkedHashMap<>(r.get(0));
        Chart c = chart();
        e.put("reportingChain", OrgChart.managerChain(id, c.emps(), c.depts()).stream()
                .map(m -> jdbc.queryForObject("SELECT CONCAT(name, '（', (SELECT name FROM org_dept WHERE id = dept_id), '）') FROM hr_employee WHERE id = ?", String.class, m))
                .toList());
        e.put("changes", jdbc.queryForList("SELECT * FROM v_employee_change WHERE employee_id = ? ORDER BY change_date, id", id));
        return e;
    }

    /** 部门树（含在职人数，含下级部门合计） */
    public List<Map<String, Object>> tree() {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * FROM v_dept WHERE status = 'ACTIVE' ORDER BY sort_no, id");
        Map<Object, Map<String, Object>> byId = new LinkedHashMap<>();
        rows.forEach(r -> {
            Map<String, Object> m = new LinkedHashMap<>(r);
            m.put("children", new ArrayList<Map<String, Object>>());
            byId.put(r.get("id"), m);
        });
        List<Map<String, Object>> roots = new ArrayList<>();
        for (Map<String, Object> m : byId.values()) {
            Map<String, Object> parent = m.get("parent_id") == null ? null : byId.get(m.get("parent_id"));
            if (parent == null) {
                roots.add(m);
            } else {
                @SuppressWarnings("unchecked") List<Map<String, Object>> kids = (List<Map<String, Object>>) parent.get("children");
                kids.add(m);
            }
        }
        roots.forEach(this::sumHeadcount);
        return roots;
    }

    @SuppressWarnings("unchecked")
    private long sumHeadcount(Map<String, Object> node) {
        long total = ((Number) node.get("headcount")).longValue();
        for (Map<String, Object> k : (List<Map<String, Object>>) node.get("children")) {
            total += sumHeadcount(k);
        }
        node.put("total_headcount", total);
        return total;
    }

    // ---------- 入转调离 ----------

    @Transactional
    public long hire(HireReq r) {
        if (r.name() == null || r.name().isBlank() || r.deptId() == null || r.positionId() == null || r.hireDate() == null) {
            throw new BizException("姓名、部门、岗位、入职日期必填");
        }
        String type = r.employmentType() == null ? "FULL_TIME" : r.employmentType();
        String wage = r.wageType() == null ? "MONTHLY" : r.wageType();
        BigDecimal base = r.baseSalary() == null ? BigDecimal.ZERO : r.baseSalary();
        String empNo = nextEmpNo(r.hireDate());
        jdbc.update("""
                INSERT INTO hr_employee (emp_no, name, gender, birth_date, dept_id, position_id, manager_id, line_code, team, employment_type,
                  wage_type, base_salary, social_base, hire_date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                empNo, r.name(), r.gender() == null ? "M" : r.gender(), r.birthDate() == null ? null : Date.valueOf(r.birthDate()),
                r.deptId(), r.positionId(), r.managerId(), r.lineCode(), r.team(), type, wage, base,
                r.socialBase() == null ? base : r.socialBase(), Date.valueOf(r.hireDate()), "INTERN".equals(type) ? "ACTIVE" : "PROBATION");
        long id = jdbc.queryForObject("SELECT id FROM hr_employee WHERE emp_no = ?", Long.class, empNo);
        change(id, "HIRE", r.hireDate(), null, r.deptId(), null, base, "入职：" + typeName(type), null);
        return id;
    }

    /** 试用期满转正（入职满 3 个月） */
    @Transactional
    public int confirmProbation(LocalDate date) {
        List<Long> ids = jdbc.queryForList("SELECT id FROM hr_employee WHERE status = 'PROBATION' AND hire_date <= ?", Long.class,
                Date.valueOf(date.minusMonths(3)));
        for (Long id : ids) {
            jdbc.update("UPDATE hr_employee SET status = 'ACTIVE' WHERE id = ?", id);
            Long dept = jdbc.queryForObject("SELECT dept_id FROM hr_employee WHERE id = ?", Long.class, id);
            change(id, "REGULAR", date, dept, dept, null, null, "试用期满转正", null);
        }
        return ids.size();
    }

    @Transactional
    public void transfer(long empId, long deptId, Long positionId, Long managerId, LocalDate date, String remark) {
        Map<String, Object> e = raw(empId);
        jdbc.update("UPDATE hr_employee SET dept_id = ?, position_id = COALESCE(?, position_id), manager_id = ? WHERE id = ?",
                deptId, positionId, managerId, empId);
        change(empId, "TRANSFER", date, ((Number) e.get("dept_id")).longValue(), deptId, null, null, remark, null);
    }

    @Transactional
    public void adjustSalary(long empId, BigDecimal newBase, LocalDate date, String remark) {
        Map<String, Object> e = raw(empId);
        if (newBase == null || newBase.signum() <= 0) {
            throw new BizException("工资必须大于 0");
        }
        jdbc.update("UPDATE hr_employee SET base_salary = ?, social_base = GREATEST(social_base, ?) WHERE id = ?", newBase, newBase, empId);
        long dept = ((Number) e.get("dept_id")).longValue();
        change(empId, "SALARY", date, dept, dept, (BigDecimal) e.get("base_salary"), newBase, remark, null);
    }

    /** 离职申请审批通过：登记最后工作日，到期由 processDepartures 生效 */
    @Transactional
    public void scheduleLeave(long empId, LocalDate lastDay, String reason, Long wfInstanceId) {
        raw(empId);
        jdbc.update("UPDATE hr_employee SET leave_date = ?, leave_reason = ? WHERE id = ?", Date.valueOf(lastDay), reason, empId);
        if (wfInstanceId != null) {
            jdbc.update("UPDATE hr_employee_change SET wf_instance_id = ? WHERE employee_id = ? AND change_type = 'LEAVE' AND wf_instance_id IS NULL",
                    wfInstanceId, empId);
        }
    }

    /** 直接离职（自离、辞退）：当天生效 */
    @Transactional
    public void leaveNow(long empId, LocalDate date, String reason) {
        scheduleLeave(empId, date, reason, null);
        processDepartures(date);
    }

    /** 离职生效：状态改为离职、停用系统账号（统一身份），若是部门负责人则清空负责人 */
    @Transactional
    public int processDepartures(LocalDate date) {
        List<Map<String, Object>> due = jdbc.queryForList(
                "SELECT id, dept_id, user_id, leave_date, leave_reason FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NOT NULL AND leave_date <= ?",
                Date.valueOf(date));
        for (Map<String, Object> e : due) {
            long id = ((Number) e.get("id")).longValue();
            jdbc.update("UPDATE hr_employee SET status = 'LEFT' WHERE id = ?", id);
            if (e.get("user_id") != null) {
                jdbc.update("UPDATE sys_user SET enabled = 0 WHERE id = ?", e.get("user_id"));
                userStatus.evictAll();
                log.info("员工 {} 离职生效，已停用系统账号 #{}", id, e.get("user_id"));
            }
            jdbc.update("UPDATE org_dept SET manager_emp_id = NULL WHERE manager_emp_id = ?", id);
            jdbc.update("UPDATE hr_employee SET manager_id = NULL WHERE manager_id = ?", id);
            long dept = ((Number) e.get("dept_id")).longValue();
            change(id, "LEAVE", ((java.sql.Date) e.get("leave_date")).toLocalDate(), dept, null, null, null, (String) e.get("leave_reason"), null);
        }
        return due.size();
    }

    /** 为员工开通系统账号（统一身份：一个员工一个账号，角色决定功能权限） */
    @Transactional
    public long createAccount(long empId, String username, String role, String initialPassword) {
        Map<String, Object> e = raw(empId);
        if ("LEFT".equals(e.get("status"))) {
            throw new BizException("离职员工不能开通账号");
        }
        if (e.get("user_id") != null) {
            throw new BizException("该员工已有账号");
        }
        if (initialPassword == null || initialPassword.length() < 8) {
            throw new BizException("初始密码至少 8 位");
        }
        if (!jdbc.queryForList("SELECT id FROM sys_user WHERE username = ?", Long.class, username).isEmpty()) {
            throw new BizException("账号已存在：" + username);
        }
        String dept = jdbc.queryForObject("SELECT name FROM org_dept WHERE id = ?", String.class, e.get("dept_id"));
        jdbc.update("INSERT INTO sys_user (username, password_hash, display_name, role, dept, enabled, employee_id) VALUES (?,?,?,?,?,1,?)",
                username, encoder.encode(initialPassword), e.get("name"), role, dept, empId);
        long uid = jdbc.queryForObject("SELECT id FROM sys_user WHERE username = ?", Long.class, username);
        jdbc.update("UPDATE hr_employee SET user_id = ? WHERE id = ?", uid, empId);
        return uid;
    }

    /** 已有账号绑定员工（初始化 / 历史数据） */
    @Transactional
    public void linkAccount(long empId, String username) {
        Long uid = jdbc.queryForList("SELECT id FROM sys_user WHERE username = ?", Long.class, username).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("账号", username));
        jdbc.update("UPDATE sys_user SET employee_id = ? WHERE id = ?", empId, uid);
        jdbc.update("UPDATE hr_employee SET user_id = ? WHERE id = ?", uid, empId);
    }

    public void change(long empId, String type, LocalDate date, Long fromDept, Long toDept, BigDecimal fromSalary, BigDecimal toSalary,
                       String remark, Long wfId) {
        jdbc.update("""
                INSERT INTO hr_employee_change (employee_id, change_type, change_date, from_dept_id, to_dept_id, from_salary, to_salary, remark, wf_instance_id)
                VALUES (?,?,?,?,?,?,?,?,?)""", empId, type, Date.valueOf(date), fromDept, toDept, fromSalary, toSalary, remark, wfId);
    }

    private Map<String, Object> raw(long empId) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM hr_employee WHERE id = ?", empId);
        if (r.isEmpty()) {
            throw new NotFoundException("员工", empId);
        }
        return r.get(0);
    }

    private String nextEmpNo(LocalDate hireDate) {
        String prefix = "E" + hireDate.getYear() % 100;
        Integer max = jdbc.queryForObject("SELECT COALESCE(MAX(CAST(SUBSTRING(emp_no, 4) AS UNSIGNED)), 0) FROM hr_employee WHERE emp_no LIKE ?",
                Integer.class, prefix + "%");
        return String.format("%s%04d", prefix, (max == null ? 0 : max) + 1);
    }

    private static String typeName(String t) {
        return switch (t) {
            case "DISPATCH" -> "劳务派遣";
            case "INTERN" -> "实习";
            default -> "正式";
        };
    }
}
