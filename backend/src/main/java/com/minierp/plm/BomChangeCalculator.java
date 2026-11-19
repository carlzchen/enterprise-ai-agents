package com.minierp.plm;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** BOM 变更计算（纯函数）：在现有 BOM 上应用 ADD / REMOVE / CHANGE，返回新 BOM；非法变更抛 IllegalArgumentException */
public final class BomChangeCalculator {
    private BomChangeCalculator() {
    }

    public record Change(String action, long materialId, BigDecimal newQty) {
    }

    public static Map<Long, BigDecimal> apply(Map<Long, BigDecimal> bom, List<Change> changes) {
        Map<Long, BigDecimal> result = new LinkedHashMap<>(bom);
        for (Change c : changes) {
            switch (c.action()) {
                case "ADD" -> {
                    if (result.containsKey(c.materialId())) {
                        throw new IllegalArgumentException("BOM 已包含该物料，请使用 CHANGE");
                    }
                    result.put(c.materialId(), positive(c.newQty()));
                }
                case "REMOVE" -> {
                    if (result.remove(c.materialId()) == null) {
                        throw new IllegalArgumentException("BOM 不包含要删除的物料");
                    }
                }
                case "CHANGE" -> {
                    if (!result.containsKey(c.materialId())) {
                        throw new IllegalArgumentException("BOM 不包含要修改的物料");
                    }
                    result.put(c.materialId(), positive(c.newQty()));
                }
                default -> throw new IllegalArgumentException("未知变更动作：" + c.action());
            }
        }
        if (result.isEmpty()) {
            throw new IllegalArgumentException("变更后 BOM 不能为空");
        }
        return result;
    }

    /** A → B → … → Z，超过 Z 不允许 */
    public static String nextRevision(String rev) {
        if (rev == null || rev.length() != 1 || rev.charAt(0) < 'A' || rev.charAt(0) >= 'Z') {
            throw new IllegalArgumentException("版本号无法继续升级：" + rev);
        }
        return String.valueOf((char) (rev.charAt(0) + 1));
    }

    private static BigDecimal positive(BigDecimal q) {
        if (q == null || q.signum() <= 0) {
            throw new IllegalArgumentException("新用量必须大于 0");
        }
        return q;
    }
}
