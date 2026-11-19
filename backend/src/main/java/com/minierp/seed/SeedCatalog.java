package com.minierp.seed;

import com.minierp.masterdata.*;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/**
 * 模拟主数据：物料及月度价格、汇率、供应商、产品及 BOM、客户。
 * 伺服电机采用三层 BOM：成品 → 定子组件 / 转子组件（L7）→ 转子铁芯（L8）→ 外购件；另配置两组替代料。
 * 客户与供应商均为虚构名称。
 */
@Component
@RequiredArgsConstructor
public class SeedCatalog {
    private final MaterialRepository materials;
    private final MaterialPriceRepository prices;
    private final FxRateRepository fxRates;
    private final SupplierRepository suppliers;
    private final ProductRepository products;
    private final BomItemRepository boms;
    private final CustomerRepository customers;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    // ---------- 模拟用的内存模型 ----------

    public record MatInfo(long id, String code, String category, double basePrice, double moq) {
    }

    public record SupInfo(long id, String code, String category, double baseDefect, int leadDays, double priceFactor, double share) {
    }

    public record ProdInfo(long id, String code, String series, int lot, double listPrice, Map<Long, Double> bom) {
    }

    /** 自制半成品：bom 为下层物料（可能仍是半成品）→ 用量，unitCost 为按基准价卷积的标准成本 */
    public record SemiInfo(long id, String code, String line, double conversion, int lot, Map<Long, Double> bom, double unitCost) {
    }

    public record CustInfo(long id, String code, String name, String industry, boolean domestic, String currency,
                           double weight, double discount, String payProfile, List<ProdInfo> affinity) {
    }

    public static class Model {
        public final Map<Long, MatInfo> materials = new LinkedHashMap<>();
        public final Map<String, List<SupInfo>> suppliersByCategory = new HashMap<>();
        public final List<ProdInfo> products = new ArrayList<>();
        public final List<CustInfo> customers = new ArrayList<>();
        public final Map<Long, SemiInfo> semis = new LinkedHashMap<>();
    }

    private static final String[] SERIES_ORDER = {"SHADED_POLE", "CROSSFLOW", "BLDC", "STEPPER", "SERVO", "GEAR"};

    @Transactional
    public Model build(LocalDate start, LocalDate end, Random rnd) {
        Model m = new Model();
        Map<String, Long> mat = createMaterials(m, start, end, rnd);
        createFx(start, end, rnd);
        createSuppliers(m);
        createProducts(m, mat, start);
        createSubstitutes(mat);
        createCustomers(m, rnd);
        return m;
    }

    // ---------- 物料与价格 ----------

    private Map<String, Long> createMaterials(Model model, LocalDate start, LocalDate end, Random rnd) {
        Object[][] defs = {
                // code, name, category, uom, base price, moq
                {"M-CU-025", "漆包铜线 QZ-2 Φ0.25", "COPPER", "kg", 86.0, 2000},
                {"M-CU-040", "漆包铜线 QZ-2 Φ0.40", "COPPER", "kg", 84.0, 2000},
                {"M-CU-063", "漆包铜线 QZ-2 Φ0.63", "COPPER", "kg", 82.0, 1000},
                {"M-SI-470", "冷轧硅钢片 50W470", "STEEL", "kg", 7.6, 8000},
                {"M-SI-800", "冷轧硅钢片 50W800", "STEEL", "kg", 6.4, 5000},
                {"M-MG-N35", "钕铁硼磁钢 N35", "MAGNET", "pcs", 3.2, 20000},
                {"M-MG-N42", "钕铁硼磁钢 N42SH", "MAGNET", "pcs", 5.8, 5000},
                {"M-MG-N35H", "钕铁硼磁钢 N35（加严磁通公差）", "MAGNET", "pcs", 3.6, 20000},
                {"M-MG-FE", "铁氧体磁瓦", "MAGNET", "pcs", 0.55, 20000},
                {"M-MG-N45", "钕铁硼磁钢 N45SH（N42SH 替代料）", "MAGNET", "pcs", 6.4, 3000},
                {"M-BR-OIL", "含油轴承", "BEARING", "pcs", 0.28, 50000},
                {"M-BR-608", "深沟球轴承 608ZZ", "BEARING", "pcs", 1.15, 20000},
                {"M-BR-6000", "深沟球轴承 6000ZZ", "BEARING", "pcs", 1.9, 5000},
                {"M-BR-6000D", "深沟球轴承 6000ZZ（国产替代）", "BEARING", "pcs", 1.55, 3000},
                {"M-BR-6204", "深沟球轴承 6204ZZ", "BEARING", "pcs", 4.2, 2000},
                {"M-SH-04", "转轴 Φ4", "SHAFT", "pcs", 0.45, 30000},
                {"M-SH-08", "转轴 Φ8", "SHAFT", "pcs", 1.2, 10000},
                {"M-SH-14", "转轴 Φ14", "SHAFT", "pcs", 3.6, 2000},
                {"M-PL-CAP", "PBT 端盖", "PLASTIC", "pcs", 0.75, 50000},
                {"M-PL-FAN", "轴流风叶", "PLASTIC", "pcs", 0.5, 30000},
                {"M-PL-CF", "贯流风叶", "PLASTIC", "pcs", 3.8, 5000},
                {"M-PL-HS", "贯流风机机壳", "PLASTIC", "pcs", 2.6, 5000},
                {"M-AL-HS", "伺服电机铝合金机壳", "PLASTIC", "pcs", 18.0, 1000},
                {"M-PCB-BL", "无刷电机驱动板", "PCB", "pcs", 11.5, 5000},
                {"M-PCB-HALL", "霍尔传感器板", "PCB", "pcs", 1.6, 10000},
                {"M-TERM", "编码器压接端子组件", "PCB", "pcs", 1.8, 2000},
                {"M-EN-17", "17位绝对值编码器", "ENCODER", "pcs", 62.0, 500},
                {"M-EN-2500", "2500线增量编码器", "ENCODER", "pcs", 24.0, 500},
                {"M-GR-SET", "减速齿轮组", "GEAR", "pcs", 5.5, 10000},
                {"M-GR-BOX", "减速箱壳体", "GEAR", "pcs", 4.8, 10000},
                {"M-PK-S", "包装材料(小)", "PACKING", "pcs", 0.35, 50000},
                {"M-PK-L", "包装材料(大)", "PACKING", "pcs", 2.2, 10000},
                {"M-LW", "引出线组件", "PACKING", "pcs", 0.4, 50000},
                // 设备维修备件（MRO）
                {"M-SP-NOZ", "绕线嘴（陶瓷）", "SPARE", "pcs", 60.0, 40}, {"M-SP-TENS", "绕线张力器", "SPARE", "pcs", 850.0, 6},
                {"M-SP-DIE", "冲模刃口镶件", "SPARE", "pcs", 2800.0, 4}, {"M-SP-CLUTCH", "冲床离合器片", "SPARE", "pcs", 1200.0, 4},
                {"M-SP-CYL", "气缸", "SPARE", "pcs", 320.0, 10}, {"M-SP-SENSOR", "光电传感器", "SPARE", "pcs", 180.0, 20},
                {"M-SP-PLC", "PLC I/O 模块", "SPARE", "pcs", 1500.0, 3}, {"M-SP-FIX", "测试夹具探针组", "SPARE", "pcs", 650.0, 8},
                {"M-SP-CAP", "充磁机储能电容", "SPARE", "pcs", 900.0, 4}, {"M-SP-PUMP", "真空泵修理包", "SPARE", "pcs", 1600.0, 2},
                {"M-SP-BELT", "同步带", "SPARE", "pcs", 95.0, 20}, {"M-SP-FILTER", "空压机滤芯", "SPARE", "pcs", 260.0, 8},
        };
        Map<String, Long> ids = new HashMap<>();
        for (Object[] d : defs) {
            Material mt = new Material();
            mt.setCode((String) d[0]);
            mt.setName((String) d[1]);
            mt.setCategory((String) d[2]);
            mt.setUom((String) d[3]);
            materials.save(mt);
            ids.put(mt.getCode(), mt.getId());
            double base = (Double) d[4];
            model.materials.put(mt.getId(), new MatInfo(mt.getId(), mt.getCode(), mt.getCategory(), base,
                    ((Number) d[5]).doubleValue()));
            for (YearMonth ym = YearMonth.from(start); !ym.isAfter(YearMonth.from(end)); ym = ym.plusMonths(1)) {
                double idx = priceIndex(mt.getCategory(), ym) * (1 + (rnd.nextDouble() - 0.5) * 0.02);
                MaterialPrice p = new MaterialPrice();
                p.setMaterialId(mt.getId());
                p.setPriceMonth(ym.atDay(1));
                p.setPrice(bd(base * idx, 4));
                prices.save(p);
            }
        }
        return ids;
    }

    /** 大宗价格指数：2026 年铜价大涨；2025 下半年稀土磁材上涨；电子料缓慢降价 */
    static double priceIndex(String category, YearMonth ym) {
        int t = (ym.getYear() - 2024) * 12 + ym.getMonthValue() - 10; // 2024-10 = 0
        return switch (category) {
            case "COPPER" -> {
                if (ym.getYear() < 2026) {
                    yield 1.0 + 0.03 * Math.sin(t / 3.0);
                }
                double[] y2026 = {1.06, 1.09, 1.13, 1.18, 1.24, 1.27, 1.26, 1.24, 1.22, 1.21, 1.20, 1.20};
                yield y2026[ym.getMonthValue() - 1];
            }
            case "MAGNET" -> {
                if (t < 8) {
                    yield 1.0;
                }
                yield t < 12 ? 1.0 + 0.0375 * (t - 7) : 1.13 + 0.02 * Math.sin(t / 2.0);
            }
            case "STEEL" -> 1.0 + 0.04 * Math.sin(t / 4.0);
            case "PCB", "ENCODER" -> 1.0 - 0.003 * t;
            default -> 1.0 + 0.001 * t;
        };
    }

    private void createFx(LocalDate start, LocalDate end, Random rnd) {
        for (YearMonth ym = YearMonth.from(start); !ym.isAfter(YearMonth.from(end)); ym = ym.plusMonths(1)) {
            int t = (ym.getYear() - 2024) * 12 + ym.getMonthValue() - 10;
            double usd = 7.12 + 0.12 * Math.sin(t / 4.0) - 0.004 * t + (rnd.nextDouble() - 0.5) * 0.03;
            double eur = 7.75 + 0.25 * Math.sin(t / 5.0 + 1) + 0.006 * t + (rnd.nextDouble() - 0.5) * 0.05;
            for (Object[] c : new Object[][]{{"USD", usd}, {"EUR", eur}}) {
                FxRate f = new FxRate();
                f.setCurrency((String) c[0]);
                f.setRateMonth(ym.atDay(1));
                f.setRate(bd((Double) c[1], 6));
                fxRates.save(f);
            }
        }
    }

    // ---------- 供应商 ----------

    private void createSuppliers(Model m) {
        Object[][] defs = {
                // code, name, category, region, terms, defect, lead, priceFactor, share
                {"S01", "江西铜源漆包线有限公司", "COPPER", "华中", 60, 0.004, 5, 1.00, 0.7},
                {"S02", "常州金线电工材料有限公司", "COPPER", "华东", 60, 0.006, 6, 0.98, 0.3},
                {"S03", "武汉华钢硅钢有限公司", "STEEL", "华中", 60, 0.003, 7, 1.00, 0.6},
                {"S04", "江阴新峰冲压材料有限公司", "STEEL", "华东", 90, 0.005, 6, 0.99, 0.4},
                {"S05", "宁波恒磁科技有限公司", "MAGNET", "华东", 60, 0.005, 10, 1.00, 0.7},
                {"S06", "包头稀宝磁材有限公司", "MAGNET", "华北", 30, 0.006, 12, 1.03, 0.3},
                {"S07", "浙江环宇轴承有限公司", "BEARING", "华东", 60, 0.003, 6, 1.00, 0.6},
                {"S08", "常山精密轴承有限公司", "BEARING", "华东", 60, 0.004, 7, 0.98, 0.4},
                {"S09", "东莞精锐五金制品有限公司", "SHAFT", "华南", 60, 0.005, 5, 1.00, 1.0},
                {"S10", "深圳宏塑精密注塑有限公司", "PLASTIC", "华南", 60, 0.008, 5, 1.00, 0.6},
                {"S11", "中山华美塑胶制品有限公司", "PLASTIC", "华南", 90, 0.009, 5, 0.97, 0.4},
                {"S12", "深圳汇芯电子有限公司", "PCB", "华南", 60, 0.006, 10, 1.00, 1.0},
                {"S13", "长春光启编码器有限公司", "ENCODER", "东北", 30, 0.005, 15, 1.00, 1.0},
                {"S14", "东莞精齿传动科技有限公司", "GEAR", "华南", 60, 0.007, 8, 1.00, 1.0},
                {"S15", "永州鑫达包装材料有限公司", "PACKING", "华中", 30, 0.005, 3, 1.00, 1.0},
                {"S16", "东莞恒力机电设备配件有限公司", "SPARE", "华南", 60, 0.004, 7, 1.00, 1.0},
        };
        for (Object[] d : defs) {
            Supplier s = new Supplier();
            s.setCode((String) d[0]);
            s.setName((String) d[1]);
            s.setCategory((String) d[2]);
            s.setRegion((String) d[3]);
            s.setPaymentTermsDays((Integer) d[4]);
            suppliers.save(s);
            m.suppliersByCategory.computeIfAbsent(s.getCategory(), k -> new ArrayList<>())
                    .add(new SupInfo(s.getId(), s.getCode(), s.getCategory(), (Double) d[5], (Integer) d[6], (Double) d[7], (Double) d[8]));
        }
    }

    // ---------- 产品与 BOM ----------

    private void createProducts(Model model, Map<String, Long> mat, LocalDate start) {
        // 罩极电机：功率 W，电压
        for (Object[] v : new Object[][]{{"YJ48", 5, "220V"}, {"YJ58", 8, "220V"}, {"YJ61", 12, "220V"}, {"YJ61", 12, "120V"},
                {"YJ61", 18, "220V"}, {"YJ68", 25, "220V"}, {"YJ68", 25, "120V"}, {"YJ72", 30, "220V"}}) {
            int w = (Integer) v[1];
            Map<String, Double> b = new LinkedHashMap<>();
            b.put("M-CU-025", 0.05 + 0.004 * w);
            b.put("M-SI-470", 0.18 + 0.012 * w);
            b.put("M-BR-OIL", 2.0);
            b.put("M-SH-04", 1.0);
            b.put("M-PL-CAP", 2.0);
            b.put("M-PL-FAN", 1.0);
            b.put("M-LW", 1.0);
            b.put("M-PK-S", 1.0);
            add(model, mat, "KL-SP-" + v[0] + "-" + w + "W-" + ((String) v[2]).replace("V", ""),
                    "罩极电机 " + v[0] + " " + w + "W " + v[2], "SHADED_POLE", "家电电机事业部", (String) v[2], w, 2600,
                    2.8, 5000, 0.24, b);
        }
        for (Object[] v : new Object[][]{{"60×360", 0.12, 0.35}, {"65×480", 0.15, 0.42}, {"80×600", 0.19, 0.52}}) {
            Map<String, Double> b = new LinkedHashMap<>();
            b.put("M-CU-025", (Double) v[1]);
            b.put("M-SI-470", (Double) v[2]);
            b.put("M-BR-OIL", 2.0);
            b.put("M-SH-08", 1.0);
            b.put("M-PL-CF", 1.0);
            b.put("M-PL-HS", 1.0);
            b.put("M-LW", 1.0);
            b.put("M-PK-L", 1.0);
            add(model, mat, "KL-CF-" + ((String) v[0]).replace("×", "X"), "贯流风机 Φ" + v[0], "CROSSFLOW", "家电电机事业部",
                    "220V", 20, 1300, 8.0, 2000, 0.22, b);
        }
        for (int w : new int[]{36, 60, 90, 120}) {
            Map<String, Double> b = new LinkedHashMap<>();
            b.put("M-CU-040", 0.08 + 0.0015 * w);
            b.put("M-SI-470", 0.25 + 0.004 * w);
            b.put("M-MG-N35", w <= 60 ? 4.0 : 6.0);
            b.put("M-BR-608", 2.0);
            b.put("M-SH-08", 1.0);
            b.put("M-PCB-BL", 1.0);
            b.put("M-PCB-HALL", 1.0);
            b.put("M-PL-CAP", 2.0);
            b.put("M-LW", 1.0);
            b.put("M-PK-S", 1.0);
            add(model, mat, "KL-BL-" + w + "W-310", "无刷直流电机 " + w + "W 310VDC", "BLDC", "智能驱控事业部", "310VDC", w, 1200,
                    9.0, 1000, 0.26, b);
        }
        for (Object[] v : new Object[][]{{"28", 32, 0.03, 0.10, "M-SH-04", "M-BR-608", 1.0}, {"42", 40, 0.06, 0.28, "M-SH-04", "M-BR-608", 1.0},
                {"42", 48, 0.08, 0.36, "M-SH-04", "M-BR-608", 1.0}, {"57", 56, 0.15, 0.62, "M-SH-08", "M-BR-608", 2.0},
                {"57", 76, 0.20, 0.85, "M-SH-08", "M-BR-608", 2.0}, {"86", 98, 0.45, 1.60, "M-SH-14", "M-BR-6000", 2.0}}) {
            Map<String, Double> b = new LinkedHashMap<>();
            b.put("M-CU-025", (Double) v[2]);
            b.put("M-SI-800", (Double) v[3]);
            b.put("M-MG-N35", (Double) v[6]);
            b.put((String) v[5], 2.0);
            b.put((String) v[4], 1.0);
            b.put("M-PL-CAP", 2.0);
            b.put("M-LW", 1.0);
            b.put("M-PK-S", 1.0);
            add(model, mat, "KL-ST-" + v[0] + "x" + v[1], "两相混合式步进电机 " + v[0] + "×" + v[1] + "mm", "STEPPER", "智能驱控事业部",
                    "24VDC", 0, 600, 7.0, 1000, 0.28, b);
        }
        for (Object[] v : new Object[][]{{"60", 200, 0.25, 0.8, 8.0, "M-EN-2500", "M-BR-6000"}, {"80", 400, 0.45, 1.4, 10.0, "M-EN-2500", "M-BR-6000"},
                {"80", 750, 0.65, 2.0, 12.0, "M-EN-17", "M-BR-6000"}, {"110", 1200, 0.95, 3.2, 14.0, "M-EN-17", "M-BR-6204"},
                {"130", 1500, 1.15, 3.9, 16.0, "M-EN-17", "M-BR-6204"}, {"130", 2000, 1.40, 4.5, 16.0, "M-EN-17", "M-BR-6204"}}) {
            int w = (Integer) v[1];
            String frame = (String) v[0];
            double steel = (Double) v[3];
            // 三层 BOM：转子铁芯(同机座共用) → 转子组件 / 定子组件 → 成品
            String core = "SA-RC-" + frame;
            if (!mat.containsKey(core)) {
                semi(model, mat, core, "伺服转子铁芯 " + frame + " 法兰", "L8", 0.6, 400, Map.of("M-SI-470", steel * 0.3));
            }
            Map<String, Double> rotor = new LinkedHashMap<>();
            rotor.put(core, 1.0);
            rotor.put("M-MG-N42", (Double) v[4]);
            rotor.put("M-SH-14", 1.0);
            semi(model, mat, "SA-RT-" + frame + "-" + w, "伺服转子组件 " + frame + "-" + w + "W（充磁）", "L7", 4.0, 200, rotor);
            Map<String, Double> stator = new LinkedHashMap<>();
            stator.put("M-CU-063", (Double) v[2]);
            stator.put("M-SI-470", steel * 0.7);
            semi(model, mat, "SA-ST-" + frame + "-" + w, "伺服定子组件 " + frame + "-" + w + "W（嵌线浸漆）", "L7", 10.0, 200, stator);
            Map<String, Double> b = new LinkedHashMap<>();
            b.put("SA-ST-" + frame + "-" + w, 1.0);
            b.put("SA-RT-" + frame + "-" + w, 1.0);
            add(model, mat, "KL-SV-" + v[0] + "-" + w + "W", "交流伺服电机 " + v[0] + "法兰 " + w + "W", "SERVO", "智能驱控事业部",
                    "220VAC", w, 3000, 30.4, 200, 0.36, b);
        }
        for (Object[] v : new Object[][]{{"TYD49", 4, 0.02, 0.06}, {"TYD50", 6, 0.03, 0.08}, {"GM37", 12, 0.04, 0.10}, {"GM25", 6, 0.02, 0.06}}) {
            Map<String, Double> b = new LinkedHashMap<>();
            b.put("M-CU-025", (Double) v[2]);
            b.put("M-SI-800", (Double) v[3]);
            b.put("M-MG-FE", 2.0);
            b.put("M-BR-OIL", 2.0);
            b.put("M-SH-04", 1.0);
            b.put("M-GR-SET", 1.0);
            b.put("M-GR-BOX", 1.0);
            b.put("M-LW", 1.0);
            b.put("M-PK-S", 1.0);
            add(model, mat, "KL-GR-" + v[0], "齿轮减速电机 " + v[0] + " " + v[1] + "W", "GEAR", "家电电机事业部",
                    ((String) v[0]).startsWith("GM") ? "12VDC" : "220V", (Integer) v[1], 30, 6.0, 2000, 0.25, b);
        }
        model.products.sort(Comparator.comparingInt(p -> Arrays.asList(SERIES_ORDER).indexOf(p.series())));
    }

    private void add(Model model, Map<String, Long> mat, String code, String name, String series, String bu, String voltage,
                     int powerW, int rpm, double conversion, int lot, double targetMargin, Map<String, Double> bomDef) {
        double materialCost = 0;
        Map<Long, Double> bom = new LinkedHashMap<>();
        for (var e : bomDef.entrySet()) {
            long mid = mat.get(e.getKey());
            bom.put(mid, e.getValue());
            materialCost += e.getValue() * unitCost(model, mid);
        }
        double listPrice = (materialCost + conversion) / (1 - targetMargin);
        Product p = new Product();
        p.setCode(code);
        p.setName(name);
        p.setSeries(series);
        p.setBusinessUnit(bu);
        p.setVoltage(voltage);
        p.setPowerW(powerW > 0 ? BigDecimal.valueOf(powerW) : null);
        p.setRatedSpeedRpm(rpm);
        p.setConversionCost(bd(conversion, 4));
        p.setListPrice(bd(listPrice, 2));
        p.setLotSize(lot);
        products.save(p);
        for (var e : bom.entrySet()) {
            BomItem b = new BomItem();
            b.setProductId(p.getId());
            b.setMaterialId(e.getKey());
            b.setQtyPer(bd(e.getValue(), 4));
            boms.save(b);
        }
        model.products.add(new ProdInfo(p.getId(), code, series, lot, p.getListPrice().doubleValue(), bom));
    }

    private static double unitCost(Model model, long mid) {
        SemiInfo si = model.semis.get(mid);
        return si != null ? si.unitCost() : model.materials.get(mid).basePrice();
    }

    /** 自制半成品：物料主数据(supply_type=MAKE) + 半成品 BOM */
    private void semi(Model model, Map<String, Long> mat, String code, String name, String line, double conversion, int lot,
                      Map<String, Double> bomDef) {
        Material mt = new Material();
        mt.setCode(code);
        mt.setName(name);
        mt.setCategory("SEMI");
        mt.setUom("pcs");
        mt.setSupplyType("MAKE");
        mt.setLineCode(line);
        mt.setConversionCost(bd(conversion, 4));
        mt.setLotSize(lot);
        materials.save(mt);
        mat.put(code, mt.getId());
        Map<Long, Double> bom = new LinkedHashMap<>();
        double cost = conversion;
        for (var e : bomDef.entrySet()) {
            long child = mat.get(e.getKey());
            bom.put(child, e.getValue());
            cost += e.getValue() * unitCost(model, child);
            jdbc.update("INSERT INTO md_material_bom (parent_material_id, child_material_id, qty_per) VALUES (?,?,?)", mt.getId(), child, bd(e.getValue(), 4));
        }
        model.semis.put(mt.getId(), new SemiInfo(mt.getId(), code, line, conversion, lot, bom, cost));
    }

    /** 替代料：伺服磁钢 N42SH 缺料时用 N45SH（性能更高、贵约 10%）；6000ZZ 进口轴承缺料时用国产替代 */
    private void createSubstitutes(Map<String, Long> mat) {
        jdbc.update("INSERT INTO md_material_substitute (material_id, substitute_id, ratio, priority, valid_from, reason) VALUES (?,?,?,?,?,?)",
                mat.get("M-MG-N42"), mat.get("M-MG-N45"), BigDecimal.ONE, 1, LocalDate.of(2024, 10, 1),
                "同尺寸更高牌号，磁通与耐温满足 N42SH 要求，单价高约 10%，仅在主料缺料时使用");
        jdbc.update("INSERT INTO md_material_substitute (material_id, substitute_id, ratio, priority, valid_from, reason) VALUES (?,?,?,?,?,?)",
                mat.get("M-BR-6000"), mat.get("M-BR-6000D"), BigDecimal.ONE, 1, LocalDate.of(2025, 3, 1),
                "国产轴承已通过 1000h 寿命与噪声验证（ECN 会签），可替代进口 6000ZZ");
    }

    // ---------- 客户 ----------

    private void createCustomers(Model m, Random rnd) {
        Object[][] defs = {
                // code, name, industry, region, country, domestic, currency, terms, weight, payProfile
                {"C001", "广东华澜电器集团有限公司", "家电", "华南", "中国", true, "CNY", 90, 3.0, "NORMAL"},
                {"C002", "浙江蓝鲸家电股份有限公司", "家电", "华东", "中国", true, "CNY", 90, 2.5, "ACCEPTANCE"},
                {"C003", "佛山顺德宏达电器有限公司", "家电", "华南", "中国", true, "CNY", 90, 2.0, "DETERIORATING"},
                {"C004", "中山美思小家电有限公司", "家电", "华南", "中国", true, "CNY", 60, 1.2, "NORMAL"},
                {"C005", "宁波瑞丰电器有限公司", "家电", "华东", "中国", true, "CNY", 90, 1.5, "NORMAL"},
                {"C006", "珠海格瑞环境电器有限公司", "家电", "华南", "中国", true, "CNY", 90, 1.8, "ACCEPTANCE"},
                {"C007", "青岛海岳电器有限公司", "家电", "华北", "中国", true, "CNY", 90, 1.4, "NORMAL"},
                {"C008", "安徽润扬制冷设备有限公司", "家电", "华东", "中国", true, "CNY", 60, 1.0, "NORMAL"},
                {"C009", "江门力拓电器有限公司", "家电", "华南", "中国", true, "CNY", 60, 0.8, "NORMAL"},
                {"C010", "苏州莱顿清洁电器有限公司", "家电", "华东", "中国", true, "CNY", 60, 0.9, "NORMAL"},
                {"C011", "Nordica Home Appliances AB", "家电", "欧洲", "瑞典", false, "EUR", 60, 1.3, "NORMAL"},
                {"C012", "Midwest Appliance Corp.", "家电", "北美", "美国", false, "USD", 60, 1.5, "NORMAL"},
                {"C013", "Sakura Kaden Co., Ltd.", "家电", "亚太", "日本", false, "USD", 60, 1.0, "NORMAL"},
                {"C014", "Hanbit Electronics Co.", "家电", "亚太", "韩国", false, "USD", 45, 0.9, "NORMAL"},
                {"C015", "Rheinland Haushaltsgeräte GmbH", "家电", "欧洲", "德国", false, "EUR", 60, 1.1, "NORMAL"},
                {"C016", "Istanbul Beyaz Esya A.S.", "家电", "欧洲", "土耳其", false, "USD", 90, 0.7, "LATE"},
                {"C017", "深圳灵犀机器人有限公司", "机器人", "华南", "中国", true, "CNY", 60, 1.2, "NORMAL"},
                {"C018", "苏州卓越自动化机器人有限公司", "机器人", "华东", "中国", true, "CNY", 60, 1.0, "NORMAL"},
                {"C019", "上海擎天协作机器人有限公司", "机器人", "华东", "中国", true, "CNY", 60, 0.9, "NORMAL"},
                {"C020", "杭州元启智能科技有限公司", "机器人", "华东", "中国", true, "CNY", 60, 0.6, "NORMAL"},
                {"C021", "东莞智臂科技有限公司", "机器人", "华南", "中国", true, "CNY", 60, 0.8, "NORMAL"},
                {"C022", "Nordwerk Robotics GmbH", "机器人", "欧洲", "德国", false, "EUR", 60, 0.6, "NORMAL"},
                {"C023", "杭州安视科技有限公司", "安防", "华东", "中国", true, "CNY", 90, 1.0, "NORMAL"},
                {"C024", "深圳守望者安防技术有限公司", "安防", "华南", "中国", true, "CNY", 60, 0.7, "LATE"},
                {"C025", "Vigilant Security Inc.", "安防", "北美", "美国", false, "USD", 60, 0.5, "NORMAL"},
                {"C026", "珠海联创打印科技有限公司", "办公自动化", "华南", "中国", true, "CNY", 60, 0.9, "NORMAL"},
                {"C027", "厦门印捷办公设备有限公司", "办公自动化", "华南", "中国", true, "CNY", 60, 0.6, "NORMAL"},
                {"C028", "Kyoei Office Systems Co.", "办公自动化", "亚太", "日本", false, "USD", 60, 0.7, "NORMAL"},
                {"C029", "深圳康宁医疗器械有限公司", "医疗器械", "华南", "中国", true, "CNY", 90, 0.6, "NORMAL"},
                {"C030", "迈诺医疗设备(苏州)有限公司", "医疗器械", "华东", "中国", true, "CNY", 60, 0.5, "NORMAL"},
                {"C031", "东莞力拓锂电装备有限公司", "工业自动化", "华南", "中国", true, "CNY", 90, 1.0, "NORMAL"},
                {"C032", "无锡光能光伏装备有限公司", "工业自动化", "华东", "中国", true, "CNY", 90, 0.9, "NORMAL"},
                {"C033", "佛山精工包装机械有限公司", "工业自动化", "华南", "中国", true, "CNY", 60, 0.6, "NORMAL"},
                {"C034", "常州纺机自动化有限公司", "工业自动化", "华东", "中国", true, "CNY", 60, 0.5, "NORMAL"},
                {"C035", "Tecnomatica S.r.l.", "工业自动化", "欧洲", "意大利", false, "EUR", 60, 0.5, "NORMAL"},
        };
        Map<String, List<String>> affinity = Map.of(
                "家电", List.of("SHADED_POLE", "CROSSFLOW", "BLDC", "GEAR"),
                "机器人", List.of("SERVO", "STEPPER", "BLDC"),
                "安防", List.of("STEPPER", "GEAR"),
                "办公自动化", List.of("STEPPER", "GEAR"),
                "医疗器械", List.of("STEPPER", "BLDC"),
                "工业自动化", List.of("SERVO", "STEPPER", "GEAR"));
        for (Object[] d : defs) {
            double weight = (Double) d[8];
            boolean domestic = (Boolean) d[5];
            Customer c = new Customer();
            c.setCode((String) d[0]);
            c.setName((String) d[1]);
            c.setIndustry((String) d[2]);
            c.setRegion((String) d[3]);
            c.setCountry((String) d[4]);
            c.setDomestic(domestic);
            c.setCurrency((String) d[6]);
            c.setPaymentTermsDays((Integer) d[7]);
            // C003 信用评级较低，额度收紧，回款恶化后会触发信用冻结
            double perWeight = "DETERIORATING".equals(d[9]) ? 7_000_000 : domestic ? 12_000_000 : 8_000_000;
            c.setCreditLimit(bd(weight * perWeight, 2));
            customers.save(c);

            List<ProdInfo> pool = m.products.stream().filter(p -> affinity.get(c.getIndustry()).contains(p.series())).toList();
            List<ProdInfo> shuffled = new ArrayList<>(pool);
            Collections.shuffle(shuffled, rnd);
            int k = Math.min(shuffled.size(), 3 + rnd.nextInt(4));
            // 外销客户只买 120V 罩极，内销只买 220V
            List<ProdInfo> picks = shuffled.stream()
                    .filter(p -> !p.series().equals("SHADED_POLE") || p.code().endsWith(domestic ? "220" : "120") || !domestic)
                    .limit(k).toList();
            m.customers.add(new CustInfo(c.getId(), c.getCode(), c.getName(), c.getIndustry(), domestic, c.getCurrency(),
                    weight, 0.90 + rnd.nextDouble() * 0.10, (String) d[9], picks));
        }
    }

    static BigDecimal bd(double v, int scale) {
        return BigDecimal.valueOf(v).setScale(scale, RoundingMode.HALF_UP);
    }
}
