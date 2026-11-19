package com.minierp.query;

import java.util.Set;

/**
 * 列表视图白名单定义。前端只能按这里声明的列过滤/排序，杜绝 SQL 注入。
 *
 * @param view          视图或表名
 * @param roles         允许访问的角色（空 = 所有登录用户）
 * @param filters       允许精确过滤的列
 * @param keywordCols   关键字模糊搜索的列
 * @param dateCol       日期范围过滤的列（可空）
 * @param defaultSort   默认排序列
 * @param sensitiveCols 成本/毛利等敏感列，仅 FINANCE/MANAGEMENT/ADMIN 可见
 */
public record ViewDef(String view, Set<String> roles, Set<String> filters, Set<String> keywordCols,
                      String dateCol, String defaultSort, Set<String> sensitiveCols) {
}
