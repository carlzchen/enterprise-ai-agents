package com.minierp.workflow;

import java.math.BigDecimal;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 审批节点进入条件（纯函数，不用脚本引擎，避免表达式注入）。
 * 语法：子句用 && / || 连接（&& 优先），子句形如 `amount >= 5000`、`days > 3`、`leaveType == 'SICK'`。
 * 空条件恒为真；引用了表单里没有的字段时该子句为假；语法错误抛 IllegalArgumentException（保存流程定义时校验）。
 */
public final class ConditionEvaluator {
    private ConditionEvaluator() {
    }

    private static final Pattern CLAUSE = Pattern.compile("^\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*(>=|<=|==|!=|>|<)\\s*('([^']*)'|-?\\d+(\\.\\d+)?)\\s*$");

    public static boolean eval(String expr, Map<String, ?> vars) {
        if (expr == null || expr.isBlank()) {
            return true;
        }
        for (String or : expr.split("\\|\\|")) {
            boolean all = true;
            for (String and : or.split("&&")) {
                if (!clause(and, vars)) {
                    all = false;
                    break;
                }
            }
            if (all) {
                return true;
            }
        }
        return false;
    }

    public static void validate(String expr) {
        if (expr == null || expr.isBlank()) {
            return;
        }
        for (String or : expr.split("\\|\\|")) {
            for (String and : or.split("&&")) {
                if (!CLAUSE.matcher(and).matches()) {
                    throw new IllegalArgumentException("条件表达式语法错误：" + and.trim());
                }
            }
        }
    }

    private static boolean clause(String s, Map<String, ?> vars) {
        Matcher m = CLAUSE.matcher(s);
        if (!m.matches()) {
            throw new IllegalArgumentException("条件表达式语法错误：" + s.trim());
        }
        Object v = vars.get(m.group(1));
        if (v == null) {
            return false;
        }
        String op = m.group(2);
        if (m.group(4) != null) { // 字符串比较只支持 == / !=
            boolean eq = m.group(4).equals(String.valueOf(v));
            return switch (op) {
                case "==" -> eq;
                case "!=" -> !eq;
                default -> throw new IllegalArgumentException("字符串只支持 == 和 !=：" + s.trim());
            };
        }
        BigDecimal left;
        try {
            left = new BigDecimal(String.valueOf(v));
        } catch (NumberFormatException e) {
            return false;
        }
        int c = left.compareTo(new BigDecimal(m.group(3)));
        return switch (op) {
            case ">=" -> c >= 0;
            case "<=" -> c <= 0;
            case ">" -> c > 0;
            case "<" -> c < 0;
            case "==" -> c == 0;
            default -> c != 0;
        };
    }
}
