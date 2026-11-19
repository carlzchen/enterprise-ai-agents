package com.minierp.masterdata;

import java.math.BigDecimal;
import java.util.*;

/**
 * 多级 BOM 展开（纯函数）。节点键：成品 "P:id"，物料 "M:id"；structure 为 父节点 → (子物料 id → 单位用量)。
 * 低阶码（low-level code）= 物料在所有 BOM 中出现的最深层级，MRP/APS 按低阶码从高到低逐层计算，保证上层需求先汇总完。
 */
public final class BomExploder {
    private BomExploder() {
    }

    public record Node(int level, String key, String parentKey, BigDecimal qtyPer, BigDecimal totalQty, boolean leaf) {
    }

    public static String product(long id) {
        return "P:" + id;
    }

    public static String material(long id) {
        return "M:" + id;
    }

    /** 逐层展开：深度优先，totalQty = 上层数量 × 单位用量 */
    public static List<Node> explode(String root, BigDecimal qty, Map<String, Map<Long, BigDecimal>> structure) {
        List<Node> out = new ArrayList<>();
        walk(root, qty, 1, structure, out, new ArrayDeque<>(List.of(root)));
        return out;
    }

    private static void walk(String parent, BigDecimal qty, int level, Map<String, Map<Long, BigDecimal>> structure,
                             List<Node> out, Deque<String> path) {
        for (var e : structure.getOrDefault(parent, Map.of()).entrySet()) {
            String key = material(e.getKey());
            if (path.contains(key)) {
                throw new IllegalArgumentException("BOM 存在循环引用：" + String.join(" → ", path) + " → " + key);
            }
            BigDecimal total = qty.multiply(e.getValue());
            boolean leaf = !structure.containsKey(key);
            out.add(new Node(level, key, parent, e.getValue(), total, leaf));
            path.addLast(key);
            walk(key, total, level + 1, structure, out, path);
            path.removeLast();
        }
    }

    /** 展开到最底层外购件的总需求（同一物料在不同分支的需求合并） */
    public static Map<Long, BigDecimal> flatten(String root, BigDecimal qty, Map<String, Map<Long, BigDecimal>> structure) {
        Map<Long, BigDecimal> out = new TreeMap<>();
        for (Node n : explode(root, qty, structure)) {
            if (n.leaf()) {
                out.merge(Long.parseLong(n.key().substring(2)), n.totalQty(), BigDecimal::add);
            }
        }
        return out;
    }

    /** 每个节点的低阶码：成品为 0，物料取其在所有 BOM 路径中出现的最大层级 */
    public static Map<String, Integer> lowLevelCodes(Map<String, Map<Long, BigDecimal>> structure) {
        Map<String, Integer> llc = new HashMap<>();
        for (String root : structure.keySet()) {
            llc.putIfAbsent(root, 0);
            for (Node n : explode(root, BigDecimal.ONE, structure)) {
                llc.merge(n.key(), n.level(), Math::max);
            }
        }
        // 半成品自身作为根时被记为 0，这里以它在上层 BOM 中的层级为准（上面 merge 取最大值已覆盖）
        return llc;
    }
}
