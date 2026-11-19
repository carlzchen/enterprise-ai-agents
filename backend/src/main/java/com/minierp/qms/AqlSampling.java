package com.minierp.qms;

/**
 * GB/T 2828.1（ISO 2859-1）一般检验水平 II、正常检验一次抽样方案（纯函数）。
 * 1. 按批量查样本量字码；2. 按字码与 AQL 查主表，遇箭头则改用箭头指向的第一个方案（样本量随之改变）；
 * 样本量 ≥ 批量时全检。
 */
public final class AqlSampling {
    private AqlSampling() {
    }

    public record Plan(char codeLetter, int sampleSize, int ac, int re) {
    }

    private static final int[] LOT_UPPER = {8, 15, 25, 50, 90, 150, 280, 500, 1200, 3200, 10000, 35000, 150000, 500000, Integer.MAX_VALUE};
    private static final char[] LETTERS = {'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'J', 'K', 'L', 'M', 'N', 'P', 'Q', 'R'};
    private static final int[] SAMPLE = {2, 3, 5, 8, 13, 20, 32, 50, 80, 125, 200, 315, 500, 800, 1250, 2000};
    /** 各 AQL 的 "0 1" 方案所在行（主表对角线规律：AQL 每降一档，下移一行） */
    private static final double[] AQLS = {0.4, 0.65, 1.0, 1.5, 2.5, 4.0};
    private static final int[] ZERO_ROW = {6, 5, 4, 3, 2, 1};
    /** 从 "0 1" 行起的偏移 → 接收数：0→0，+1 ↑（用上一方案），+2 ↓（用下一方案），+3→1，+4→2，… */
    private static final int[] AC_BY_OFFSET = {0, -1, -1, 1, 2, 3, 5, 7, 10, 14, 21};

    public static Plan plan(int lotSize, double aql) {
        if (lotSize < 2) {
            throw new IllegalArgumentException("批量至少为 2");
        }
        int col = -1;
        for (int i = 0; i < AQLS.length; i++) {
            if (Math.abs(AQLS[i] - aql) < 1e-9) {
                col = i;
            }
        }
        if (col < 0) {
            throw new IllegalArgumentException("不支持的 AQL：" + aql);
        }
        int row = 0;
        while (lotSize > LOT_UPPER[row]) {
            row++;
        }
        int k = ZERO_ROW[col];
        int off = row - k;
        if (off < 0) {
            off = 0; // ↓ 到 "0 1"
        } else if (off == 1) {
            off = 0; // ↑ 用 "0 1"
        } else if (off == 2) {
            off = 3; // ↓ 用 "1 2"
        } else if (off > 10) {
            off = 10;
        }
        int useRow = k + off;
        int ac = AC_BY_OFFSET[off];
        int n = Math.min(SAMPLE[useRow], lotSize);
        return new Plan(LETTERS[row], n, ac, ac + 1);
    }
}
