package com.minierp.workflow;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.CommandLineRunner;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * 内置流程定义（首次启动写入 v1；之后在页面上修改会生成新版本）。
 * 审批人类型：MANAGER 直属上级（level 级）/ DEPT_HEAD 申请人部门负责人 / DEPT_OF 指定部门负责人 / ROLE 角色 / USER 指定账号。
 */
@Slf4j
@Component
@Order(0)
@RequiredArgsConstructor
public class WorkflowDefinitionSeeder implements CommandLineRunner {
    private final JdbcTemplate jdbc;

    private static final String[][] DEFS = {
            {"LEAVE", "请假", """
                    {"nodes":[
                      {"name":"直属上级","type":"MANAGER","level":1},
                      {"name":"部门负责人","type":"DEPT_HEAD","condition":"days >= 3"},
                      {"name":"人力资源备案","type":"ROLE","role":"HR","condition":"days >= 5 || leaveType == 'SICK'"}]}""",
                    """
                    [{"prop":"leaveType","label":"假别","type":"select","options":[["ANNUAL","年假"],["SICK","病假"],["PERSONAL","事假"]],"required":true},
                     {"prop":"startDate","label":"开始日期","type":"date","required":true},{"prop":"endDate","label":"结束日期","type":"date","required":true},
                     {"prop":"reason","label":"事由","required":true}]"""},
            {"EXPENSE", "费用报销", """
                    {"nodes":[
                      {"name":"直属上级","type":"MANAGER","level":1},
                      {"name":"部门负责人","type":"DEPT_HEAD","condition":"amount >= 5000"},
                      {"name":"财务审核","type":"ROLE","role":"FINANCE"},
                      {"name":"总经理","type":"USER","user":"gm","condition":"amount >= 50000"},
                      {"name":"超预算审批（总经理）","type":"USER","user":"gm","condition":"overBudget == 'Y'"}]}""", null},
            {"CAPEX", "资本性支出", """
                    {"nodes":[
                      {"name":"部门负责人","type":"DEPT_HEAD"},
                      {"name":"设备动力部","type":"DEPT_OF","dept":"EQP","condition":"categoryCode == 'MACHINE' || categoryCode == 'MOLD'"},
                      {"name":"财务负责人","type":"DEPT_OF","dept":"FIN"},
                      {"name":"总经理","type":"USER","user":"gm","condition":"amount >= 200000"}]}""", null},
            {"CONTRACT", "合同审批", """
                    {"nodes":[
                      {"name":"部门负责人","type":"DEPT_HEAD"},
                      {"name":"财务审核","type":"ROLE","role":"FINANCE","condition":"amount >= 500000"},
                      {"name":"总经理","type":"USER","user":"gm","condition":"amount >= 2000000"}]}""", null},
            {"IT_CHANGE", "IT 变更", """
                    {"nodes":[
                      {"name":"信息部负责人","type":"DEPT_OF","dept":"IT"},
                      {"name":"总经理","type":"USER","user":"gm","condition":"risk == 'HIGH'"}]}""", null},
            {"EHS_PERMIT", "危险作业许可", """
                    {"nodes":[
                      {"name":"部门负责人","type":"DEPT_HEAD"},
                      {"name":"安环审核","type":"DEPT_OF","dept":"EHS"},
                      {"name":"总经理","type":"USER","user":"gm","condition":"permitType == 'CONFINED' || level == '1'"}]}""", null},
            {"TRAVEL", "出差申请", """
                    {"nodes":[
                      {"name":"直属上级","type":"MANAGER","level":1},
                      {"name":"部门负责人","type":"DEPT_HEAD","condition":"days > 3 || amount >= 10000"}]}""",
                    """
                    [{"prop":"destination","label":"目的地","required":true},{"prop":"startDate","label":"出发日期","type":"date","required":true},
                     {"prop":"endDate","label":"返回日期","type":"date","required":true},{"prop":"budget","label":"预算（元）","type":"number"},
                     {"prop":"reason","label":"事由","required":true}]"""},
            {"SEAL", "用印申请", """
                    {"nodes":[
                      {"name":"部门负责人","type":"DEPT_HEAD"},
                      {"name":"行政审核","type":"DEPT_OF","dept":"ADM"},
                      {"name":"总经理","type":"USER","user":"gm","condition":"sealType == 'CONTRACT'"}]}""",
                    """
                    [{"prop":"sealType","label":"印章","type":"select","options":[["OFFICIAL","公章"],["CONTRACT","合同章"],["FINANCE","财务章"]],"required":true},
                     {"prop":"document","label":"用印文件","required":true},{"prop":"copies","label":"份数","type":"number"},{"prop":"reason","label":"用途","required":true}]"""},
            {"RESIGN", "离职申请", """
                    {"nodes":[
                      {"name":"直属上级","type":"MANAGER","level":1},
                      {"name":"部门负责人","type":"DEPT_HEAD"},
                      {"name":"人力资源","type":"ROLE","role":"HR"}]}""",
                    """
                    [{"prop":"lastDay","label":"最后工作日","type":"date","required":true},{"prop":"reason","label":"离职原因","required":true}]"""},
            {"PAYROLL", "工资发放审批", """
                    {"nodes":[
                      {"name":"人力资源负责人","type":"DEPT_OF","dept":"HR"},
                      {"name":"财务负责人","type":"DEPT_OF","dept":"FIN"},
                      {"name":"总经理","type":"USER","user":"gm"}]}""", null},
            {"GENERAL", "通用审批", """
                    {"nodes":[
                      {"name":"直属上级","type":"MANAGER","level":1},
                      {"name":"部门负责人","type":"DEPT_HEAD"}]}""",
                    """
                    [{"prop":"subject","label":"事项","required":true},{"prop":"detail","label":"说明","type":"textarea","required":true}]"""},
    };

    @Override
    public void run(String... args) {
        int n = 0;
        for (String[] d : DEFS) {
            Integer exists = jdbc.queryForObject("SELECT COUNT(*) FROM wf_definition WHERE code = ?", Integer.class, d[0]);
            if (exists == null || exists == 0) {
                jdbc.update("INSERT INTO wf_definition (code, name, version, definition, form_schema, enabled) VALUES (?,?,1,?,?,1)",
                        d[0], d[1], d[2], d[3]);
                n++;
            }
        }
        if (n > 0) {
            log.info("Created {} workflow definitions", n);
        }
    }
}
