package com.minierp.energy;

import com.minierp.energy.TouTariff.Band;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

class TouTariffTest {
    @Test
    void bands() {
        assertThat(TouTariff.band(3, 1)).isEqualTo(Band.VALLEY);
        assertThat(TouTariff.band(9, 8)).isEqualTo(Band.FLAT);
        assertThat(TouTariff.band(11, 8)).isEqualTo(Band.SHARP);
        assertThat(TouTariff.band(11, 3)).isEqualTo(Band.PEAK);
        assertThat(TouTariff.band(18, 3)).isEqualTo(Band.PEAK);
        assertThat(TouTariff.band(19, 3)).isEqualTo(Band.FLAT);
    }

    @Test
    void splitAndCharge() {
        // 三班连续生产 24 小时：谷 8 小时、峰 7 小时、平 9 小时
        Map<Band, Double> allDay = TouTariff.split(240, 0, 0, 3);
        assertThat(allDay.get(Band.VALLEY)).isCloseTo(80, within(1e-9));
        assertThat(allDay.get(Band.PEAK)).isCloseTo(70, within(1e-9));
        assertThat(allDay.get(Band.FLAT)).isCloseTo(90, within(1e-9));
        // 两班 8:00–24:00：不用谷电
        Map<Band, Double> twoShift = TouTariff.split(160, 8, 0, 3);
        assertThat(twoShift.get(Band.VALLEY)).isZero();
        assertThat(twoShift.get(Band.PEAK)).isCloseTo(70, within(1e-9));
        assertThat(TouTariff.energyCharge(allDay, new BigDecimal("0.6"))).isEqualByComparingTo("143.64");
        assertThat(TouTariff.carbonTons(10_000)).isEqualByComparingTo("5.366");
    }
}
