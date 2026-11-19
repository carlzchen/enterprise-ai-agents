package com.minierp.kb;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class TextDiffTest {
    @Test
    void lineDiff() {
        var d = TextDiff.diff("标题\n步骤1\n步骤2\n备注", "标题\n步骤1\n步骤2（改）\n备注\n新增");
        assertThat(d).extracting(TextDiff.Line::op).containsExactly("SAME", "SAME", "DEL", "ADD", "SAME", "ADD");
        assertThat(TextDiff.diff("a", "a")).extracting(TextDiff.Line::op).containsExactly("SAME");
    }
}
