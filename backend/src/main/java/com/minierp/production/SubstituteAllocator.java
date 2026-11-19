package com.minierp.production;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;

/**
 * 替代料分配（纯函数）：先用主料，主料不足的部分按优先级依次用替代料补足。
 * 替代比例 ratio：1 单位主料 = ratio 单位替代料（如铜线换铜包铝线按重量折算）。
 */
public final class SubstituteAllocator {
    private SubstituteAllocator() {
    }

    public record Option(long materialId, BigDecimal ratio, BigDecimal available) {
    }

    /** @param coversPrimary 这部分替代了多少主料 */
    public record Take(long materialId, BigDecimal qty, BigDecimal coversPrimary, boolean substitute) {
    }

    /** 主料 + 替代料都不够时抛出 IllegalStateException，说明缺多少 */
    public static List<Take> allocate(long primaryId, BigDecimal need, BigDecimal primaryAvailable, List<Option> options) {
        List<Take> takes = new ArrayList<>();
        BigDecimal fromPrimary = need.min(primaryAvailable.max(BigDecimal.ZERO));
        if (fromPrimary.signum() > 0) {
            takes.add(new Take(primaryId, fromPrimary, fromPrimary, false));
        }
        BigDecimal missing = need.subtract(fromPrimary);
        for (Option o : options) {
            if (missing.signum() <= 0) {
                break;
            }
            if (o.available().signum() <= 0 || o.ratio().signum() <= 0) {
                continue;
            }
            BigDecimal coverable = o.available().divide(o.ratio(), 3, RoundingMode.DOWN);
            BigDecimal cover = missing.min(coverable);
            if (cover.signum() <= 0) {
                continue;
            }
            BigDecimal qty = cover.multiply(o.ratio()).setScale(3, RoundingMode.UP).min(o.available());
            takes.add(new Take(o.materialId(), qty, cover, true));
            missing = missing.subtract(cover);
        }
        if (missing.signum() > 0) {
            throw new IllegalStateException("主料及替代料均不足，还缺 " + missing.stripTrailingZeros().toPlainString());
        }
        return takes;
    }
}
