package com.minierp.kb;

import java.util.ArrayList;
import java.util.List;

/** 按行比较两个版本（最长公共子序列，纯函数），用于知识页面版本对比 */
public final class TextDiff {
    private TextDiff() {
    }

    public record Line(String op, String text) {
    } // op: SAME / ADD / DEL

    public static List<Line> diff(String oldText, String newText) {
        String[] a = oldText == null ? new String[0] : oldText.split("\n", -1);
        String[] b = newText == null ? new String[0] : newText.split("\n", -1);
        int[][] lcs = new int[a.length + 1][b.length + 1];
        for (int i = a.length - 1; i >= 0; i--) {
            for (int j = b.length - 1; j >= 0; j--) {
                lcs[i][j] = a[i].equals(b[j]) ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
            }
        }
        List<Line> out = new ArrayList<>();
        int i = 0, j = 0;
        while (i < a.length && j < b.length) {
            if (a[i].equals(b[j])) {
                out.add(new Line("SAME", a[i]));
                i++;
                j++;
            } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
                out.add(new Line("DEL", a[i++]));
            } else {
                out.add(new Line("ADD", b[j++]));
            }
        }
        while (i < a.length) {
            out.add(new Line("DEL", a[i++]));
        }
        while (j < b.length) {
            out.add(new Line("ADD", b[j++]));
        }
        return out;
    }
}
