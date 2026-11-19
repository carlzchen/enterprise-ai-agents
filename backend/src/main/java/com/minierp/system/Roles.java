package com.minierp.system;

/** 角色常量，与 sys_user.role 一致；@PreAuthorize 中使用 hasAnyRole(...) */
public final class Roles {
    private Roles() {
    }

    public static final String ADMIN = "ADMIN";
    public static final String SALES = "SALES";
    public static final String PURCHASE = "PURCHASE";
    public static final String WAREHOUSE = "WAREHOUSE";
    public static final String PRODUCTION = "PRODUCTION";
    public static final String QUALITY = "QUALITY";
    public static final String FINANCE = "FINANCE";
    /** 管理层：只读查看所有看板与报表 */
    public static final String MANAGEMENT = "MANAGEMENT";
    /** 研发工程师：BOM、ECN、技术文档、新产品项目 */
    public static final String ENGINEER = "ENGINEER";
    /** 人力资源：组织、员工、考勤、薪资 */
    public static final String HR = "HR";
}
