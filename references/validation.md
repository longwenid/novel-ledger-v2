# 验证方法

## 自动化测试集

任选一种运行全量测试：
```bash
pytest
# 或
python3 -m unittest discover -s tests -v
```

工程测试使用临时 SQLite，不调用外部模型。

## 覆盖要点

1. **事务与账本**：阶段原子性、资源守恒、时钟、空间路径、伏笔到期、引用和审稿覆盖。
2. **返修**：局部补丁与整章重写、短稿保留、篇幅计数、目标不被默降、有限返工及修订传播。
3. **材料保真**：语义页还原冻结来源，坐标与哈希一致；归并继承全部证据，旧 pending 保留身份与输入。
4. **缓存与索引**：来源/用途/读依赖变化失效；负查询、历史分支、unknown/core 依赖不遗漏；完整 audit 全验。
5. **视图与导出**：人物场景视图、book.html 与 export 保真。
6. **篇幅与卷末**：substance/pacing/volume_development 必须正文举证；合理短章、短卷和持续高潮允许；无预算与旧 pending 保持兼容。

规模与结构测试：

```bash
python3 validation/context_endurance.py --out /tmp/results.json
python3 validation/context_endurance.py --out /tmp/knowledge.json --chapters 100 500 1000 --cases knowledge --unscoped-knowledge
python3 validation/structural_benchmark.py --out /tmp/structure.json
```

测试项目存临时目录。分别观察单包、累计输入、准备成本和本地耗时；协议字符与毫秒不换算模型 token 或费用。

文学质量按 [独立正文评测](literary-evaluation.md) 使用实际正文、定位变体和新 Worker 验证，再复审修稿。合成回执只验证工程流转；误报率需要独立裁决，跨卷质量需要真实卷界与兑现证据。
