package com.minierp.seed;

import com.minierp.masterdata.MasterDataService;
import com.minierp.masterdata.Product;
import com.minierp.plm.*;
import com.minierp.seed.SeedCatalog.MatInfo;
import com.minierp.seed.SeedCatalog.Model;
import com.minierp.seed.SeedCatalog.ProdInfo;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.*;
import java.util.function.Supplier;

/**
 * PLM 模拟：初始 BOM 版本、技术文档（规格书 / SOP / 检验标准，Markdown，可作为后续 RAG 语料）、
 * 按时间线执行的 ECN 与 NPI 项目。ECN 与业务故事呼应：
 * 伺服编码器压接端子（解决 L5 编码器问题）、罩极线径优化（应对铜价）、无刷磁钢规格加严（应对 S05 来料问题）。
 */
@Component
@RequiredArgsConstructor
public class PlmSimulation {
    private static final Map<String, String> SERIES_CN = Map.of("SHADED_POLE", "罩极电机", "CROSSFLOW", "贯流风机", "BLDC", "无刷直流电机",
            "STEPPER", "步进电机", "SERVO", "交流伺服电机", "GEAR", "齿轮减速电机");

    private final EcnService ecns;
    private final DocumentService documents;
    private final ProjectService projects;
    private final MasterDataService md;

    private final TreeMap<LocalDate, List<Runnable>> events = new TreeMap<>();
    private final Map<String, Long> projectIds = new HashMap<>();
    private final Map<String, String> datasheetDocNo = new HashMap<>();
    private Model model;
    private Random rnd;

    public void init(Model model, LocalDate start, LocalDate end, Random rnd) {
        this.model = model;
        this.rnd = rnd;
        LocalDate d0 = start.minusDays(1);
        for (ProdInfo p : model.products) {
            ecns.ensureInitialRevision(p.id(), d0);
            PlmDocument doc = upload(null, md.product(p.id()).getCode() + " 产品规格书", "DATASHEET", p.id(), p.series(),
                    md.product(p.id()).getCode() + "_datasheet.md", datasheet(md.product(p.id()), null), d0);
            datasheetDocNo.put(md.product(p.id()).getCode(), doc.getDocNo());
        }
        for (var e : Map.of("SHADED_POLE", "L1", "CROSSFLOW", "L2", "BLDC", "L3", "STEPPER", "L4", "SERVO", "L5", "GEAR", "L6").entrySet()) {
            upload(null, e.getValue() + " " + SERIES_CN.get(e.getKey()) + "装配作业指导书", "SOP", null, e.getKey(),
                    e.getValue() + "_SOP.md", sop(e.getKey(), e.getValue(), null), d0);
        }
        model.materials.values().stream().map(MatInfo::category).distinct().sorted().forEach(cat ->
                upload(null, cat + " 类物料来料检验标准", "INSPECTION", null, null, "IQC_" + cat + ".md", inspection(cat), d0));
        scheduleProjects();
        scheduleEcns();
    }

    public void onDay(LocalDate d) {
        while (!events.isEmpty() && !events.firstKey().isAfter(d)) {
            events.pollFirstEntry().getValue().forEach(Runnable::run);
        }
        // 研发项目按月计费用
        if (d.getDayOfMonth() <= 3 && d.getDayOfWeek().getValue() == 1) {
            for (Long id : projectIds.values()) {
                spend(id);
            }
        }
    }

    // ================= ECN =================

    private void scheduleEcns() {
        // 1. 贯流风机包材减量（降本）——已发布
        ecn("2025-06-10", "贯流风机共用包装箱设计，包材用量降低 20%", "COST_DOWN",
                "两台共用一个外箱并取消内衬泡沫，经跌落测试验证。", () -> lines("CROSSFLOW", "CHANGE", "M-PK-L", q -> q.multiply(new BigDecimal("0.8"))),
                null, "2025-07-01");
        // 2. 步进电机轴承降级（降本）——质量否决
        ecn("2025-09-15", "步进电机 608ZZ 轴承改用低价国产型号", "COST_DOWN",
                "单台可降本约 0.4 元。替代料寿命测试 1500h 出现异响。", () -> lines("STEPPER", "CHANGE", "M-BR-608", q -> q),
                new String[]{"QUALITY", "替代轴承 1500h 寿命测试异响比例 3%，不满足 3000h 要求，否决"}, null);
        // 3. 伺服编码器压接端子（质量改进）——已发布，呼应 L5 编码器问题
        ecn("2026-01-12", "伺服电机编码器连接改为压接端子", "QUALITY",
                "2025-11 起伺服线 L5 编码器虚焊导致良率下滑及客诉。编码器插接由手工焊接改为压接端子组件，并增加振动老化测试。",
                () -> lines("SERVO", "ADD", "M-TERM", q -> BigDecimal.ONE), null, "2026-02-02");
        // 4. 罩极电机绕组线径优化（应对铜价）——已发布
        ecn("2026-04-08", "罩极电机绕组线径优化，漆包线用量降低 8%", "COST_DOWN",
                "2026 年铜价上涨超过 20%。通过优化槽满率与绕组匝数，在温升不超标前提下降低铜线用量 8%，效率下降约 0.5 个百分点。",
                () -> lines("SHADED_POLE", "CHANGE", "M-CU-025", q -> q.multiply(new BigDecimal("0.92")).setScale(4, RoundingMode.HALF_UP)),
                null, "2026-06-15");
        // 5. 无刷电机磁钢规格加严（供应问题）——已发布，呼应 S05 来料问题
        ecn("2026-07-06", "无刷电机磁钢切换为加严磁通公差规格 N35H", "SUPPLY",
                "2026-04 起磁钢来料磁通一致性差，导致无刷电机异响客诉。改用加严公差规格（磁通 ±3%），单价上升约 12%。",
                () -> {
                    List<EcnService.LineReq> l = new ArrayList<>(lines("BLDC", "REMOVE", "M-MG-N35", q -> null));
                    l.addAll(lines("BLDC", "ADD", "M-MG-N35H", q -> null));
                    return l;
                }, null, "2026-09-01");
        // 6. 86 步进转轴材质变更——会签中
        ecn("2026-09-10", "86 步进电机转轴材质由 45# 钢改为 40Cr", "DESIGN",
                "客户反馈大负载工况下转轴磨损，提高转轴强度。", () -> linesFor("KL-ST-86x98", "CHANGE", "M-SH-14", q -> q),
                new String[]{"PENDING", "PRODUCTION"}, null);
        // 7. 伺服 130 法兰加厚机壳——草稿
        ecn("2026-09-22", "伺服电机 130 法兰机壳加厚以改善散热", "CUSTOMER",
                "应机器人客户要求，2kW 型号连续运行温升偏高。", () -> linesFor("KL-SV-130-2000W", "CHANGE", "M-AL-HS", q -> q),
                new String[]{"DRAFT"}, null);
    }

    /**
     * @param outcome null=全部同意；{"QUALITY","意见"}=该角色否决；{"PENDING", role}=该角色尚未会签；{"DRAFT"}=仅草稿
     */
    private void ecn(String createDate, String title, String reason, String desc, Supplier<List<EcnService.LineReq>> lines,
                     String[] outcome, String releaseDate) {
        LocalDate d = LocalDate.parse(createDate);
        at(d, () -> {
            Ecn e = ecns.create(new EcnService.CreateReq(title, reason, desc, lines.get()), "eng01", d);
            if (outcome != null && "DRAFT".equals(outcome[0])) {
                return;
            }
            ecns.submit(e.getId());
            String[] reviewers = {"eng02", "prod01", "qa01", "buyer01"}; // 研发会签人不能是申请人 eng01
            for (int i = 0; i < EcnService.REVIEW_ROLES.size(); i++) {
                String role = EcnService.REVIEW_ROLES.get(i);
                String who = reviewers[i];
                LocalDate rd = d.plusDays(2L * (i + 1) + rnd.nextInt(2)); // 会签日期严格递增
                if (outcome != null && "PENDING".equals(outcome[0]) && role.equals(outcome[1])) {
                    continue;
                }
                boolean reject = outcome != null && role.equals(outcome[0]);
                at(rd, () -> ecns.review(e.getId(), role, who, !reject, reject ? outcome[1] : "同意", rd));
                if (reject) {
                    break;
                }
            }
            if (releaseDate != null) {
                LocalDate rel = LocalDate.parse(releaseDate);
                at(rel, () -> {
                    ecns.release(e.getId(), rel);
                    reviseDatasheets(e.getId(), title, rel);
                });
            }
        });
    }

    /** ECN 发布后，受影响产品的规格书升版 */
    private void reviseDatasheets(Long ecnId, String title, LocalDate date) {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> lines = (List<Map<String, Object>>) ecns.detail(ecnId).get("lines");
        lines.stream().map(l -> (String) l.get("product_code")).distinct().forEach(code -> {
            Product p = model.products.stream().filter(x -> md.product(x.id()).getCode().equals(code))
                    .findFirst().map(x -> md.product(x.id())).orElseThrow();
            upload(datasheetDocNo.get(code), null, "DATASHEET", null, null, code + "_datasheet.md",
                    datasheet(p, ecnsNo(ecnId) + "：" + title), date);
        });
    }

    private String ecnsNo(Long ecnId) {
        return (String) ecns.detail(ecnId).get("ecn_no");
    }

    private List<EcnService.LineReq> lines(String series, String action, String materialCode,
                                           java.util.function.Function<BigDecimal, BigDecimal> qty) {
        List<EcnService.LineReq> out = new ArrayList<>();
        for (ProdInfo p : model.products) {
            if (p.series().equals(series)) {
                out.addAll(linesFor(md.product(p.id()).getCode(), action, materialCode, qty));
            }
        }
        return out;
    }

    private List<EcnService.LineReq> linesFor(String productCode, String action, String materialCode,
                                              java.util.function.Function<BigDecimal, BigDecimal> qty) {
        ProdInfo p = model.products.stream().filter(x -> md.product(x.id()).getCode().equals(productCode)).findFirst().orElseThrow();
        long mid = materialId(materialCode);
        BigDecimal current = md.bomOf(p.id()).stream().filter(b -> b.getMaterialId() == mid)
                .map(b -> b.getQtyPer()).findFirst().orElse(null);
        if ("CHANGE".equals(action) || "REMOVE".equals(action)) {
            if (current == null) {
                return List.of();
            }
        }
        BigDecimal newQty = switch (action) {
            case "REMOVE" -> null;
            case "ADD" -> {
                if (current != null) {
                    yield null;
                }
                // 磁钢规格替换沿用原 N35 用量
                BigDecimal replaced = md.bomOf(p.id()).stream().filter(b -> b.getMaterialId() == materialId("M-MG-N35"))
                        .map(b -> b.getQtyPer()).findFirst().orElse(BigDecimal.ONE);
                yield "M-MG-N35H".equals(materialCode) ? replaced : qty.apply(null);
            }
            default -> qty.apply(current);
        };
        if ("ADD".equals(action) && newQty == null) {
            return List.of();
        }
        return List.of(new EcnService.LineReq(p.id(), action, mid, newQty));
    }

    private long materialId(String code) {
        return model.materials.values().stream().filter(m -> m.code().equals(code)).findFirst().orElseThrow().id();
    }

    // ================= NPI =================

    private void scheduleProjects() {
        project("变频空调 BLDC 150W 新平台", "BLDC", "林志远", "广东华澜电器集团有限公司", "2024-11-04", "2025-12-31", 2_600_000,
                new String[][]{{"2025-01-20", "GO", "概念评审通过，目标成本 118 元"}, {"2025-04-15", "GO", "设计评审通过"},
                        {"2025-08-12", "GO", "样机 500h 耐久通过，进入试产"}, {"2025-11-18", "GO", "试产良率 98.6%，批准量产"},
                        {"2026-03-02", "GO", "量产稳定，项目结项"}});
        project("一体化关节模组（伺服+谐波减速+双编码器）", "SERVO", "林志远", "深圳灵犀机器人有限公司", "2025-06-02", "2026-10-31", 5_200_000,
                new String[][]{{"2025-08-18", "GO", "客户需求冻结：峰值扭矩 120Nm，重量 < 1.6kg"}, {"2025-12-08", "GO", "设计评审通过"},
                        {"2026-06-15", "GO", "样机通过客户 DV 测试，进入试产"}});
        project("灵巧手空心杯电机平台", "BLDC", "陈家豪", "杭州元启智能科技有限公司", "2025-09-01", "2026-12-31", 3_800_000,
                new String[][]{{"2025-11-10", "GO", "立项：Φ16 空心杯，目标效率 80%"}, {"2026-04-20", "GO", "设计评审通过，开模"}});
        project("86 闭环步进电机", "STEPPER", "陈家豪", "工业自动化市场", "2025-03-03", "2026-03-31", 1_500_000,
                new String[][]{{"2025-05-12", "GO", "概念评审通过"}, {"2025-10-20", "HOLD", "市场需求不及预期，研发资源优先保障关节模组项目"}});
        project("罩极电机铝线替代方案", "SHADED_POLE", "林志远", "家电客户", "2026-04-15", "2027-03-31", 1_200_000,
                new String[][]{{"2026-07-01", "GO", "铜价高位，评估铝线方案：效率降 3%，需客户认可"}});
    }

    private void project(String name, String series, String owner, String customer, String start, String mp, double budget,
                         String[][] gates) {
        LocalDate s = LocalDate.parse(start);
        at(s, () -> {
            PlmProject p = new PlmProject();
            p.setName(name);
            p.setSeries(series);
            p.setOwner(owner);
            p.setTargetCustomer(customer);
            p.setStartDate(s);
            p.setTargetMpDate(LocalDate.parse(mp));
            p.setBudget(BigDecimal.valueOf(budget));
            PlmProject saved = projects.create(p, s);
            projectIds.put(name, saved.getId());
            for (String[] g : gates) {
                LocalDate gd = LocalDate.parse(g[0]);
                at(gd, () -> {
                    PlmProject after = projects.gate(saved.getId(), g[1], g[2], "cfo", gd);
                    if (!"ACTIVE".equals(after.getStatus())) {
                        projectIds.remove(name);
                    }
                });
            }
        });
    }

    private void spend(Long projectId) {
        projects.addSpent(projectId, BigDecimal.valueOf(60_000 + rnd.nextDouble() * 180_000).setScale(2, RoundingMode.HALF_UP));
    }

    // ================= 文档内容 =================

    private PlmDocument upload(String docNo, String title, String type, Long productId, String series, String fileName,
                               String content, LocalDate date) {
        return documents.store(new DocumentService.Meta(docNo, title, type, productId, series), fileName,
                content.getBytes(StandardCharsets.UTF_8), "text/markdown; charset=utf-8", "eng01", date);
    }

    private String datasheet(Product p, String changeNote) {
        double power = p.getPowerW() == null ? 0 : p.getPowerW().doubleValue();
        int rpm = p.getRatedSpeedRpm() == null ? 0 : p.getRatedSpeedRpm();
        String torque = power > 0 && rpm > 0 ? String.format("%.3f N·m", 9550 * power / 1000 / rpm) : "见型号表";
        String efficiency = switch (p.getSeries()) {
            case "SHADED_POLE" -> "18%～28%";
            case "CROSSFLOW" -> "25%～35%";
            case "BLDC" -> "80%～88%";
            case "STEPPER" -> "—（保持转矩见下表）";
            case "SERVO" -> "≥ 88%";
            default -> "35%～55%（含减速箱）";
        };
        String apps = switch (p.getSeries()) {
            case "SHADED_POLE" -> "微波炉转盘、冰箱风扇、电暖器、小型排气扇";
            case "CROSSFLOW" -> "空调室内机、暖风机、空气幕";
            case "BLDC" -> "变频空调风机、空气净化器、洗碗机、吸尘器";
            case "STEPPER" -> "3D 打印机、安防云台、办公设备、医疗输液泵";
            case "SERVO" -> "工业机器人关节、协作机器人、锂电与光伏设备、包装机械";
            default -> "自动窗帘、智能马桶、阀门执行器、自动售货机";
        };
        StringBuilder sb = new StringBuilder();
        sb.append("# ").append(p.getName()).append(" 产品规格书\n\n");
        sb.append("- 型号：").append(p.getCode()).append("\n- 产品系列：").append(SERIES_CN.get(p.getSeries()))
                .append("\n- 所属事业部：").append(p.getBusinessUnit()).append("\n- 当前 BOM 版本：").append(p.getRevision()).append("\n\n");
        sb.append("## 1. 主要技术参数\n\n| 参数 | 数值 |\n|---|---|\n");
        sb.append("| 额定电压 | ").append(p.getVoltage()).append(" |\n");
        if (power > 0) {
            sb.append("| 额定功率 | ").append((int) power).append(" W |\n");
        }
        sb.append("| 额定转速 | ").append(rpm).append(" rpm |\n");
        sb.append("| 额定转矩 | ").append(torque).append(" |\n");
        sb.append("| 效率 | ").append(efficiency).append(" |\n");
        sb.append("| 绝缘等级 | ").append("SERVO".equals(p.getSeries()) ? "F 级" : "B 级").append(" |\n");
        sb.append("| 防护等级 | ").append("SERVO".equals(p.getSeries()) ? "IP65" : "IP40").append(" |\n");
        sb.append("| 工作环境温度 | -10℃ ～ +40℃ |\n| 温升 | ≤ 75K（电阻法） |\n\n");
        sb.append("## 2. 典型应用\n\n").append(apps).append("\n\n");
        sb.append("## 3. 使用注意事项\n\n1. 安装时请勿敲击转轴，避免轴承与磁钢损伤。\n2. 请在额定电压 ±10% 范围内使用，长期过压会导致温升超标。\n");
        if ("SERVO".equals(p.getSeries())) {
            sb.append("3. 须与匹配的伺服驱动器配套使用，编码器线缆长度不超过 20m，并做好屏蔽接地。\n");
        } else if ("STEPPER".equals(p.getSeries())) {
            sb.append("3. 建议驱动器细分设置 ≥ 8，可显著降低低速振动与噪声。\n");
        }
        sb.append("\n## 4. 可靠性\n\n- 寿命：").append("SHADED_POLE".equals(p.getSeries()) ? "≥ 10000h" : "≥ 20000h")
                .append("\n- 噪声：≤ ").append("SERVO".equals(p.getSeries()) ? "60" : "45").append(" dB(A)（1m）\n");
        if (changeNote != null) {
            sb.append("\n## 变更记录\n\n- ").append(changeNote).append("（BOM 升级至 ").append(p.getRevision()).append(" 版）\n");
        }
        return sb.toString();
    }

    private static String sop(String series, String line, String note) {
        String steps = switch (series) {
            case "SERVO" -> "定子冲片叠压 → 嵌线绕组 → 浸漆烘干 → 转子磁钢粘接与充磁 → 编码器安装与零位校准 → 总装 → 耐压/绝缘测试 → 空载与负载测试 → 振动老化 → 包装";
            case "STEPPER" -> "定子叠压 → 自动绕线 → 转子压装 → 总装 → 保持转矩测试 → 噪声测试 → 包装";
            case "BLDC" -> "定子叠压 → 自动绕线 → 转子磁钢装配 → 驱动板焊接 → 总装 → 反电势与噪声测试 → 包装";
            default -> "铁芯叠压 → 绕线 → 嵌线整形 → 浸漆 → 总装 → 耐压测试 → 转速/电流测试 → 包装";
        };
        return "# " + line + " " + SERIES_CN.get(series) + "装配作业指导书\n\n## 工艺流程\n\n" + steps
                + "\n\n## 关键控制点\n\n1. 绕线匝数与线径按 BOM 当前版本执行，首件须经工艺确认。\n2. 耐压测试：AC 1500V/1s，漏电流 ≤ 5mA。\n"
                + "3. 每 2 小时抽检 5 台，记录于报工系统，不良按不良代码登记。\n4. 异常停线：连续 3 台同类不良立即通知质量部。\n"
                + (note == null ? "" : "\n## 变更说明\n\n" + note + "\n");
    }

    private static String inspection(String category) {
        String items = switch (category) {
            case "COPPER" -> "线径（千分尺）、漆膜针孔（盐水针孔试验）、耐压击穿、伸长率";
            case "STEEL" -> "厚度、毛刺高度 ≤ 0.03mm、铁损（爱泼斯坦方圈）";
            case "MAGNET" -> "尺寸、表面磁通（高斯计）、磁通一致性 ±5%（加严规格 ±3%）、镀层盐雾";
            case "BEARING" -> "游隙、旋转噪声、外观锈蚀";
            case "ENCODER" -> "分辨率与零位、信号波形、线缆导通";
            case "PCB" -> "外观焊点、ICT 在线测试、功能测试";
            default -> "外观、关键尺寸、材质证明";
        };
        return "# " + category + " 类物料来料检验标准\n\n## 抽样方案\n\nGB/T 2828.1 一般检验水平 II，AQL = 2.0%（抽样不良率超过 2% 整批判退）。\n\n"
                + "## 检验项目\n\n" + items + "\n\n## 不合格处理\n\n判退批次由采购通知供应商退货，同一供应商连续 3 批判退须提交 8D 报告并启动供应商审核。\n";
    }

    private void at(LocalDate d, Runnable r) {
        events.computeIfAbsent(d, k -> new ArrayList<>()).add(r);
    }
}
