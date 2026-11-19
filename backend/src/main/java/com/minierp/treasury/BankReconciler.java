package com.minierp.treasury;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;

/**
 * 银行对账（纯函数）：银行流水与总账银行存款分录一对一匹配，分两轮：
 *   1. 参考号匹配：金额相等且附言 / 摘要相同（付款附言带发票号），日期相差不超过 REF_WINDOW_DAYS（覆盖春节、国庆长假）；
 *   2. 金额 + 日期匹配：金额相等、日期相差不超过窗口，取日期最近者。
 * 匹配不上的形成未达账项，编制余额调节表：
 *   企业账面余额 + 银行已收企业未收 − 银行已付企业未付 = 银行对账单余额 + 企业已收银行未收 − 企业已付银行未付。
 * 金额一律带符号（收入为正、支出为负）。
 */
public final class BankReconciler {
    private BankReconciler() {
    }

    /** 参考号匹配允许的最大日期差 */
    public static final int REF_WINDOW_DAYS = 31;

    public record Item(long id, LocalDate date, BigDecimal amount, String ref) {
        public Item(long id, LocalDate date, BigDecimal amount) {
            this(id, date, amount, null);
        }
    }

    public record Match(long bookId, long bankId) {
    }

    public record Result(List<Match> matches, List<Item> bookOnly, List<Item> bankOnly, BigDecimal adjustedBook, BigDecimal adjustedBank, boolean balanced) {
    }

    public static Result reconcile(List<Item> book, List<Item> bank, int windowDays, BigDecimal bookBalance, BigDecimal bankBalance) {
        List<Item> bankSorted = new ArrayList<>(bank);
        bankSorted.sort(Comparator.comparing(Item::date).thenComparing(Item::id));
        Map<BigDecimal, List<Item>> openBook = new HashMap<>();
        book.forEach(b -> openBook.computeIfAbsent(b.amount().stripTrailingZeros(), k -> new ArrayList<>()).add(b));
        List<Match> matches = new ArrayList<>();
        List<Item> bankOnly = new ArrayList<>();
        List<Item> unmatched = new ArrayList<>();
        for (Item s : bankSorted) {
            if (!take(openBook, s, REF_WINDOW_DAYS, true, matches)) {
                unmatched.add(s);
            }
        }
        for (Item s : unmatched) {
            if (!take(openBook, s, windowDays, false, matches)) {
                bankOnly.add(s);
            }
        }
        List<Item> bookOnly = openBook.values().stream().flatMap(List::stream).sorted(Comparator.comparing(Item::date).thenComparing(Item::id)).toList();
        BigDecimal adjBook = bookBalance.add(bankOnly.stream().map(Item::amount).reduce(BigDecimal.ZERO, BigDecimal::add));
        BigDecimal adjBank = bankBalance.add(bookOnly.stream().map(Item::amount).reduce(BigDecimal.ZERO, BigDecimal::add));
        return new Result(matches, bookOnly, bankOnly, adjBook, adjBank, adjBook.compareTo(adjBank) == 0);
    }

    private static boolean take(Map<BigDecimal, List<Item>> openBook, Item s, int window, boolean byRef, List<Match> matches) {
        if (byRef && (s.ref() == null || s.ref().isBlank())) {
            return false;
        }
        List<Item> candidates = openBook.getOrDefault(s.amount().stripTrailingZeros(), List.of());
        Item best = null;
        long bestGap = Long.MAX_VALUE;
        for (Item b : candidates) {
            if (byRef && !s.ref().equals(b.ref())) {
                continue;
            }
            long gap = Math.abs(ChronoUnit.DAYS.between(b.date(), s.date()));
            if (gap <= window && (gap < bestGap || gap == bestGap && b.id() < best.id())) {
                best = b;
                bestGap = gap;
            }
        }
        if (best == null) {
            return false;
        }
        candidates.remove(best);
        matches.add(new Match(best.id(), s.id()));
        return true;
    }
}
