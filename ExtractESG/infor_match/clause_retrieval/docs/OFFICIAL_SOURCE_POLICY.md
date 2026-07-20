# 官方来源与防虚构规则

本工作包的正式条款只允许来自标准发布机构或监管机构的原始页面/文件。

## 当前来源

- GRI 302: Energy 2016: <https://www.globalreporting.org/publications/documents/english/gri-302-energy-2016/>
- GRI 303: Water and Effluents 2018: <https://www.globalreporting.org/publications/documents/english/gri-303-water-and-effluents-2018/>
- GRI 305: Emissions 2016: <https://www.globalreporting.org/publications/documents/english/gri-305-emissions-2016/>
- GRI 306: Waste 2020: <https://www.globalreporting.org/publications/documents/english/gri-306-waste-2020/>
- GRI 403: Occupational Health and Safety 2018: <https://www.globalreporting.org/publications/documents/english/gri-403-occupational-health-and-safety-2018/>
- GRI 404: Training and Education 2016: <https://www.globalreporting.org/publications/documents/english/gri-404-training-and-education-2016/>
- GRI 405: Diversity and Equal Opportunity 2016: <https://www.globalreporting.org/publications/documents/english/gri-405-diversity-and-equal-opportunity-2016/>
- GRI 201: Economic Performance 2016: <https://www.globalreporting.org/publications/documents/english/gri-201-economic-performance-2016/>
- GRI 3: Material Topics 2021: <https://www.globalreporting.org/publications/documents/english/gri-3-material-topics-2021/>
- HKEX Appendix C2 / ESG Reporting Code: <https://en-rules.hkex.com.hk/rulebook/appendix-c2-environmental-social-and-governance-reporting-code-0>
- IFRS S2 Climate-related Disclosures: <https://www.ifrs.org/issued-standards/ifrs-sustainability-standards-navigator/ifrs-s2-climate-related-disclosures/>

## 数据边界

1. `standard_clauses_sample.csv` 只保存官方条款编号和官方标题；`text_summary` 明确标为未保存，避免把人工改写误当作标准原文。
2. `synonyms_sample.csv` 的 alias 只收录官方标准/规则中出现的词；来自公司官方报告的表达单独放在 `official_report_aliases_sample.csv`，并记录报告页码，避免混淆标准术语与公司用语。
3. 测试夹具中的公司报告文本来自组内提供的 Document IR；本提交只保留页文本和印刷页码，不把组员电脑路径当作来源。
4. 检索脚本输出的是 candidate，不是合规结论，也不是最终 mapping。
5. 版本状态只记录本次引用的官方版本，不推断未来替代关系；正式使用时必须重新核对发布机构当前页面。

## 进入下一阶段的门槛

只有同时具备公司官方 ESG/可持续发展报告原件、页码/结构定位和官方条款来源，才可以把候选升级为正式 Evidence。不得把模拟句子、模型生成句子或估算数字放进正式数据。
