# 待审核候选条款审计与发布链路增强（2026-10-24）

对发布库当前全部候选做逐条检查（共 117 条，其中待审核 94 条、已发布 23 条）。

## 1. 阻塞原因分布

| 原因 | 条数 |
|---|---:|
| `reviewed_context_missing` | 91 |
| `fact_candidate` | 90 |
| `mapping_review_required` | 90 |
| `mapping_not_equivalent` | 90 |
| `standard_snapshot_mismatch` | 3 |
| `missing_required_dimension:statistical_time_basis` | 1 |

## 2. 逐条诊断


### 宁德时代新能源科技股份有限公司2025年环境、社会及公司治理（ESG）报告（22 条）

| 条款 | 原值 | 事实 | 映射 | 关系 | 口径 | 阻塞原因 | 诊断 |
|---|---|---|---|---|---|---|---|
| E1-5_05 · 清洁能源使用量 | 15,639,793.40 兆瓦时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_11 · 范围三温室气体排放总量³ | 111,151,357.75 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_09 · 范围二温室气体排放总量（基于位置） | 7,977,357.46 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_10 · 范围二温室气体排放总量（基于市场） | 3,020,476.35 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_30 · 范围一温室气体排放强度 | 3.82 吨二氧化碳当量/兆瓦时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_09 ·  | ³ VOCs排放总量基于物料衡算法统计，数据覆盖工艺生产过程，不含辅助工艺。 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 氮氧化物（NOx）² | 1,530.14 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 化学需氧量（COD） | 175.61 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 挥发性有机物（VOCs）³ | 1,161.80 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 氨氮（NH3 -N） | 20.36 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 二氧化硫（SO2）² | 5,856.94 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_03 · 循环使用⁵，⁷ | 1,339,058 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_01 · 耗水总量 | 32,075,664.77 立方米 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_03 · 其他回收（不含再使用、循环使用）⁸ | 244,888 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E5-5_15 · 危险废物生成总量 | 31,711 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-13_04 · 员工接受培训平均小时数 | 55.89 小时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_07 · 范围一温室气体排放总量² | 1,164,145.57 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_30 · 范围一温室气体排放强度 | 3.82 吨二氧化碳当量/兆瓦时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_30 · 温室气体排放强度⁴ | 2.34 吨二氧化碳当量/兆瓦时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_12 · 温室气体排放总量¹ | 1,751,599.73 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_03 · 循环使用⁷ | 1,679 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_03 · 其他回收（不含再使用、循环使用）⁸ | 356 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |

### 法拉电子2025年度环境、社会及公司治理报告（37 条）

| 条款 | 原值 | 事实 | 映射 | 关系 | 口径 | 阻塞原因 | 诊断 |
|---|---|---|---|---|---|---|---|
| E1-5_05 · 清洁能源使用量 | 567.42 万千瓦时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_12 · 温室气体排放总量 | 131,874 吨二氧化碳当量 | approved | approved | exact/supports | 无 | reviewed_context_missing | 事实与映射已批准，仅缺测量口径 |
| E1-6_09 · 间接温室气体排放量（范围二） | 131,504 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_10 · 间接温室气体排放量（范围二） | 131,504 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_30 · 温室气体排放强度（按照不变价计算） $ ^{{* | 25.34 吨二氧化碳当量 / 百万元营收 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_07 · 直接温室气体排放量（范围一） | 370 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 废水 › 化学需氧量（COD）排放量 | 1.91 千克 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 硫氧化物（SOx）排放量 | 0 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 氮氧化物（NOx）排放量 | 0 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 挥发性有机化合物（VOCs）排放量 | 1.64 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 颗粒物（PM）排放量 | 3.39 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_09 ·  | 法拉电子将污染物排放与废弃物管理作为环境治理的重要内容，针对废水、废气、噪声及各 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 废水 › 氨氮（ $ NH_{{3}}-N $） | 0.01 千克 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_11 · 用水总量 | 560,858 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E5-5_15 · 有害废弃物总量 | 1,342.46 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-6_12 · 员工流失率 | 12.22 % | approved | approved | exact/supports | 有 | standard_snapshot_mismatch | 旧标准包版本交付，需按当前版本重新交付 |
| S1-6_02 · 员工总数 | 5,895 人 | approved | approved | exact/supports | 有 | missing_required_dimension:statistical_time_basis, standard_snapshot_mismatch | 旧标准包版本交付，需按当前版本重新交付 |
| S1-9_02 · 女性高管人数占比 | 7.69 % | approved | approved | exact/supports | 有 | standard_snapshot_mismatch | 旧标准包版本交付，需按当前版本重新交付 |
| S1-14_05 · 每百人工伤率 | 0.289 % | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-5_05 · 清洁能源使用量 | 567.42 万千瓦时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-5_01 · 能源消耗总量 | 21,110.23 吨标准煤 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_09 · 间接温室气体排放量（范围二） | 131,504 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_10 · 间接温室气体排放量（范围二） | 131,504 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_30 · 温室气体排放强度（按照不变价计算） $ ^{{* | 25.34 吨二氧化碳当量 / 百万元营收 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_07 · 直接温室气体排放量（范围一） | 370 吨二氧化碳当量 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 氮氧化物（NOx）排放量 | 0 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 废水 › 化学需氧量（COD）排放量 | 1.91 千克 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 废水 › 氨氮（ $ NH_{{3}}-N $） | 0.01 千克 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_09 ·  | 法拉电子将污染物排放与废弃物管理作为环境治理的重要内容，针对废水、废气、噪声及各 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 挥发性有机化合物（VOCs）排放量 | 1.64 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 硫氧化物（SOx）排放量 | 0 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 废气 › 颗粒物（PM）排放量 | 3.39 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_11 · 用水总量 | 560,858 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E5-5_15 · 有害废弃物总量 | 1,342.46 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-6_12 · 员工流失率 | 12.22 % | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-14_05 · 每百人工伤率 | 0.289 % | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| GOV-1_06 · 女性董事占比 | 22.22 % | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |

### 紫金矿业集团股份有限公司2025年可持续发展报告（25 条）

| 条款 | 原值 | 事实 | 映射 | 关系 | 口径 | 阻塞原因 | 诊断 |
|---|---|---|---|---|---|---|---|
| E1-5_09 · 可再生能源占总用能比例 | 28.62 % | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_09 · -范围二GHG排放(SCOPE2) | 4.29 百万tCO2 e | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 氨氮 | 23.49 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 颗粒物（PM） | 507.81 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 汞及其化合物 | 0.05 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 总铜 | 2.47 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · COD | 240.15 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 硫化氢 | 0.04 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_09 ·  | 注：1.大气污染物总量根据废气检测报告污染物浓度和尾气流量估算得出。 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 硫酸雾 | 81.87 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 氮氧化物 | 586.30 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 氯化氢 | 1.91 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_09 ·  | 在采用多种手段减少废气排放的同时，我们建立了废气在线监测系统，实时监控主要排放口 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 二氧化硫 | 1,361.92 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 120a.1 › (1)一氧化碳 | 0 公吨(t) | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 铅及其化合物 | 0.78 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 砷及其化合物 | 0.42 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 氨气 | 6.53 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · 挥发性有机化合物（VOC） | 1.88 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_03 · 总锌 | 0.81 吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_01 · EM-MM- 140a.1 › (2)淡水总消耗 | 45.61 百万立方米(m3) | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_11 · 总取水量 | 138.98 百万吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E5-5_15 ·  | 251.80 千吨 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-13_04 ·  | 56.90 小时 | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-14_05 · 百万工时可记录事故率（TRIR） | 1.27 / | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |

### 香港中华煤气有限公司2024年可持续发展报告（10 条）

| 条款 | 原值 | 事实 | 映射 | 关系 | 口径 | 阻塞原因 | 诊断 |
|---|---|---|---|---|---|---|---|
| E1-5_01 · Total energy consumption | 7,999,444 GJ | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-5_01 · Total energy consumption | 6,442,175 GJ | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_09 · Scope 2 (location-based) | 518,000 Tonnes CO2 e | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_10 · Scope 2 (market-based) | 518,000 Tonnes CO2 e | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E1-6_30 · Average training spend ( | 1,327 HK$ | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · Sulphur dioxides (SOx ) | 105.1 Tonnes | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · Respirable suspended par | 15.1 Tonnes | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E2-4_02 · Nitrogen oxides (NOx ) | 157.4 Tonnes | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| E3-4_12 · Wastewater discharged –  | 119,381 m3 (m3 per | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |
| S1-14_05 · Recordable work-related  | 2 Number (per 1,000,000 work-hours) | candidate | review_required | partial/unknown | 无 | fact_candidate, mapping_review_required, mapping_not_equivalent, reviewed_context_missing | 历史交付未审核（映射与口径均待人工确认） |

## 3. 共性结论

1. **90 条是历史交付从未审核**：导入把映射一律写成 `partial/unknown`，且没有预填测量口径；事实和映射本身并未被判为错误，只是缺少审核动作与可用的口径建议。
2. **3 条是旧标准包版本**：交付时用的包版本（2.1.1）已经不是字典里的当前版本（2.2.0），批准旧事实不能改变这一身份，只能按当前版本重新交付。
3. **1 条只缺测量口径**：事实与映射均已批准且关系为 exact，补上口径即可发布。
4. **所有 117 条都没有来源期间**：历史导入没有把报告身份期间回填到事实，提案因此全部缺期间。

## 4. 本轮链路修复

- **导入期映射判定**：`import-bundle.mjs` 不再一律写 `partial/unknown`。由提取证据决定关系：守卫接受且未标注子范围 → `exact/supports`；原文写明子范围（part_of_group 等）→ `narrower/partially_supports`；其余保持 `partial/unknown`。审核仍是人的决定，但不再把明确对应的披露标成“不等价”。
- **报告期回填**：事实没有日期时使用报告身份的报告期，并在来源身份里记录 `period_source=report_identity`。
- **证据提案**：新增 `candidate-review.mjs`，为每个候选计算实体、期间、边界、方法、单位、维度与逐项检查；定义里唯一允许值或固定值自动填入，其余明确列为“仍需人工确认”。
- **机器核验**：新增 `machine-verification-v1`，只批准全部发布条件都被证据证明的候选（指标就绪、映射 exact/supports、有原文来源、数值可核、期间可证、主体已登记、边界可证、单位可换算、维度齐全），并把依据、规则版本与证据写进审核记录。发布动作仍由操作员触发。
- **接口与界面**：`GET /internal/v1/candidate-proposals`、`GET /internal/v1/candidates/{id}/proposal`、`POST /internal/v1/machine-verification`；发布中心新增“机器核验”面板（先展示可批准与仍缺证据的原因，再提交）；审核表单按提案预填口径。

## 5. 实测（新交付）

- 交付宁德时代 e1-6 的 9 条事实：6 条 `exact/supports`、3 条 `narrower/partially_supports`；报告期自动回填。
- 机器核验：3 条直接批准（范围一、范围二地点法、范围二市场法），6 条保留人工：3 条子范围需要确认边界、1 条范围三需要类别与聚合层级、1 条排放总量需要合并方法、1 条强度分母与平台定义不同且单位不可换算。
- 历史 117 条中 9 条本来就有完整口径可发布；其余需按新链路重新交付后才能进入机器核验（旧映射缺少证据判定，不能凭现有数据臆断为 exact）。
