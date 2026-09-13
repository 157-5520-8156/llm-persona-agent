# QQ 表达候选配置

适用隔离发布候选，当前仍为 `manual_only / qualification_incomplete`。配置接线验证与真实 QQ 回执是两项不同的验收；本批没有启动或改变生产进程。

```dotenv
WORLD_V2_VISIBLE_EXPRESSION_PROFILE=whole_v3_review_v6
WORLD_V2_EXPRESSION_EPISODE_MODE=off
WORLD_V2_INTERACTIVE_TURN_BUDGET_SECONDS=12
WORLD_V2_INTERACTIVE_HEDGE_ENABLED=false
```

第一项把 QQ/OneBot 正常启动入口接入完整 author v3 与 source reviewer v6。角色仍拥有措辞、节奏、主动联系和沉默的决定权；完整来源审核及一次受约束重选保留。`off` 指关闭表达 episode 流式模式，普通表达与持久化技术重试继续工作。未选择该配置时保持既有 compact 路径。

审核客户端从正常供应商配置创建，使用与角色相同的实例费用账本执行预留、结算和未知费用保留。客户端与后台模型实例分开，由现有关闭及静默等待机制释放。不会读取本文件中的凭据，因为此文件不包含任何凭据。

非原子表达模式、冲突的显式 wire 版本以及缺少必要模型会拒绝构造。限量体验仍应保持既有单私聊用户配置；此变更没有实现多租户共享服务。

验证入口：`tests/world_v2/test_qq_visible_release_profile.py`。测试构造真实 OneBot 应用组合，禁止所有 HTTP，检查配置传递、默认路径、计费观察器、独立客户端及关闭；不能据此宣称已向 QQ 用户交付。

实际供应商表达证据：`release-life-followup-validation-2026-09-13.json`。该试验使用相同 author/review 版本和捕获交付。下一步部署验收仍须检查实际账号、收件人、入站、授权、发送及终态回执，不能用健康接口代替。
