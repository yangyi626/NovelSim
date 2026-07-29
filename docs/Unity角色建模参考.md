# Unity 角色建模参考

## 目的

本文件记录 Unity Phase 2.1 的公开参考、原创概念稿和程序化建模取舍。参考图只用于
提取古风角色的通用结构语言，不一比一复刻任何现有游戏角色。

## 公开调研

| 来源 | 可复用信息 | 使用边界 |
|---|---|---|
| [CadNav 古代持枪武士](https://www.cadnav.com/3d-models/model-39018.html) | 约 3K 顶点的实时角色复杂度、长枪轮廓、布衣与甲片分层 | 页面标注 Non-commercial；本项目未下载原模型，只参考品类结构 |
| [ZBrushCentral 生产级武士案例](https://www.zbrushcentral.com/t/zhi-zun-bao-honor-of-kings/437527) | Maya 基础形、ZBrush 衣褶、简单绑定、Substance Painter 材质的制作顺序 | 仅参考制作流程，不复制具体人物 |
| [Meshy Chinese 模型库](https://www.meshy.ai/tags/chinese) | FBX/OBJ/GLB、PBR、自动 UV、自动绑定的后续资产路线 | 本轮未下载社区模型；以后采用前仍需保存具体资产许可证 |
| [武侠女角色参考页](https://www.5883d.com/thread-6127-1-1.html) | 交领短袍、分片下摆、护腕、腰封与长发的通用侠客服装层级 | 页面声明仅供参考学习、切勿商用；不复制纹样和具体造型 |
| [《射雕》角色技术报道](https://k.sina.com.cn/article_2801599274_a6fd032a001014qxg.html) | 发丝、衣料摆动、年龄与时代妆造共同决定角色可信度 | 只采用“轮廓 + 动态 + 材质”方法，不模仿游戏角色 |

## 原创概念稿

- 女主三视图：
  [`docs/assets/unity-concepts/heroine-turnaround-v1.png`](./assets/unity-concepts/heroine-turnaround-v1.png)
- 守卫三视图：
  [`docs/assets/unity-concepts/guard-turnaround-v1.png`](./assets/unity-concepts/guard-turnaround-v1.png)

两张概念稿使用内置图像生成能力创建，提示词明确要求原创组合设计、三视图一致、
面向低多边形建模，并排除具体游戏、影视、艺术家或已有角色的复刻。

## 女主建模规格

- 约 7.5 头身，肩宽收敛，腿部适合第三人称跑步；
- 青绿色交领短袍，黑色内袖，红色腰封；
- 前、侧、后独立裙片，保留裤装和长靴轮廓；
- 金属护腕、靴口与腰饰提供雨夜高光；
- 高马尾拆成三段，并以低幅度程序摆动；
- 面部保留眉、眼、鼻、嘴和两侧鬓发，远景仍可辨识朝向。

## 守卫建模规格

- 约 7.25 头身，肩宽和手臂体积高于女主；
- 暗红交领布衣，前后各三排黑铁札甲；
- 前甲增加铜铆钉，腰部增加分片裙甲与铜扣；
- 铜色护肩、护腕和头盔箍形成身份色；
- 头盔包含冠顶与左右护片；
- 长枪独立于手臂步态，避免巡逻时武器大幅穿模。

## 当前实现与后续

当前实现继续使用运行时轻量网格，优点是无外部依赖、可自动测试、可以随小说世界包
换色。正式 FBX 阶段应保留同样的骨骼语义和碰撞体尺寸，并补充：

1. Blender/Maya 重拓扑与 UV；
2. 2K PBR 或手绘风格贴图；
3. Mecanim Humanoid 骨架；
4. Idle / Walk / Run / Talk / Interact 动画；
5. Addressables 角色资源映射；
6. 许可证文件和署名清单。
